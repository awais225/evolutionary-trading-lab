"""
Orchestration Stage Machine & Task Watchdog (V2.7 Repair).

Maintains the explicit research pipeline lifecycle:
  DATA_SYNC
    ↓
  DATA_VALIDATION
    ↓
  FEATURE_DISCOVERY
    ↓
  FEATURE_PRECOMPUTATION
    ↓
  FEATURE_VALIDATION
    ↓
  DATASET_READY
    ↓
  RESEARCH / NODE_GENERATION
    ↓
  BACKTESTING
    ↓
  VALIDATION
    ↓
  QUALIFICATION
    ↓
  EVOLUTION
    ↓
  NEXT_RESEARCH_CYCLE

Enforces:
  1. Authoritative backend transitions with dual logging (Python log + Activity event).
  2. Zero silent stalls or hanging after feature precompute.
  3. Real task watchdog tracking progress, items, throughput, workers, and resource snapshots.
  4. Automatic stage hand-off and state synchronization with frontend.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import psutil

from ..activity import activity
from ..api.ws import bus

log = logging.getLogger("orchestrator.stages")

PIPELINE_STAGES = [
    "INIT",
    "DATA_DISCOVERY",
    "DATA_SYNC",
    "DATA_VALIDATION",
    "FEATURE_DISCOVERY",
    "FEATURE_PRECOMPUTATION",
    "FEATURE_VALIDATION",
    "DATASET_READY",
    "DATASET_REGISTRATION",
    "RESEARCH_INITIALIZATION",
    "RESEARCH",
    "NODE_GENERATION",
    "NODE_EVALUATION",
    "BACKTESTING",
    "VALIDATION",
    "QUALIFICATION",
    "EVOLUTION",
    "RESEARCH_COMPLETED",
    "BLOCKED",
]


@dataclass
class StageTaskState:
    task_id: str
    stage: str
    substage: str = ""
    status: str = "ACTIVE"  # ACTIVE, COMPLETE, STALLED, FAILED, CANCELLED
    current: int = 0
    total: int = 0
    percentage: float = 0.0
    current_item: str = ""
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    elapsed_seconds: float = 0.0
    message: str = ""
    last_op: str = ""
    throughput: Optional[str] = None
    worker_id: str = "main"
    worker_state: str = "WORKING"  # WORKING, IDLE, TERMINATED
    error: Optional[str] = None
    cpu_pct: float = 0.0
    gpu_status: str = "IDLE"
    ram_gb: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        now = time.time()
        elapsed = round(now - self.started_at, 1) if not self.completed_at else round(self.completed_at - self.started_at, 1)
        idle_s = round(max(0.0, now - self.updated_at), 1)
        d = asdict(self)
        d["elapsed_seconds"] = elapsed
        d["idle_seconds"] = idle_s
        d["updated_ago_str"] = f"{idle_s:.1f}s ago"
        return d


class CallableString(str):
    """String subclass that allows callable invocation `s()` returning str(s)."""
    def __call__(self, *args, **kwargs) -> str:
        return str(self)


class StageManager:
    """Singleton authoritative stage coordinator and watchdog."""

    def __init__(self):
        self._lock = threading.RLock()
        self._current_stage: str = "DATA_SYNC"
        self._active_tasks: Dict[str, StageTaskState] = {}
        self._completed_tasks: List[StageTaskState] = []
        self._last_transition_ts = time.time()
        self._stage_history: List[Dict[str, Any]] = []

    @property
    def current_stage(self) -> CallableString:
        with self._lock:
            return CallableString(self._current_stage)

    def get_current_stage(self) -> str:
        with self._lock:
            return str(self._current_stage)

    def transition(self, from_stage: str, to_stage: str, details: Optional[Dict[str, Any]] = None) -> None:
        """Explicitly hand off control between stages with dual logging and broadcast."""
        with self._lock:
            now = time.time()
            prev_stage = self._current_stage
            self._current_stage = to_stage
            self._last_transition_ts = now

            transition_record = {
                "from_stage": from_stage,
                "to_stage": to_stage,
                "timestamp": now,
                "details": details or {},
            }
            self._stage_history.append(transition_record)
            if len(self._stage_history) > 100:
                self._stage_history = self._stage_history[-100:]

            # Dual logging (Python standard log + Activity event stream)
            log.info("[SYSTEM] [ORCHESTRATOR] Transitioning %s → %s", from_stage, to_stage)
            activity.info(
                "SYSTEM",
                f"[ORCHESTRATOR] Transitioning {from_stage} → {to_stage}",
                details={"from_stage": from_stage, "to_stage": to_stage, **(details or {})},
                operation_id=f"trans_{int(now)}"
            )

            # Update milestone progress line if relevant
            from .milestones import get_milestone_manager
            mm = get_milestone_manager()
            self._sync_milestone_for_stage(to_stage, mm)

            # Update authoritative pipeline state (single source of truth)
            try:
                from .pipeline_state import get_pipeline_state
                get_pipeline_state().set_stage(to_stage, details)
            except Exception:
                pass

            try:
                from .diagnostic_logger import log_stage_transition
                log_stage_transition(from_stage, to_stage, details)
            except Exception:
                pass

            bus.publish("stage_transition", {
                "from_stage": from_stage,
                "to_stage": to_stage,
                "timestamp": now,
                "details": details or {},
            })

    def _sync_milestone_for_stage(self, stage: str, mm) -> None:
        """Map fine-grained orchestrator stages to milestone route stops."""
        mapping = {
            "DATA_DISCOVERY": ("data_sync", "RUNNING", "Scanning physical DATA hierarchy & reconciling index"),
            "DATA_SYNC": ("data_sync", "RUNNING", "Ingesting and verifying market datasets"),
            "DATA_VALIDATION": ("data_sync", "RUNNING", "Validating bar ranges and OHLC integrity"),
            "FEATURE_DISCOVERY": ("feature_precompute", "RUNNING", "Discovering cached indicator arrays"),
            "FEATURE_PRECOMPUTATION": ("feature_precompute", "RUNNING", "Computing missing feature schemas"),
            "FEATURE_VALIDATION": ("feature_precompute", "RUNNING", "Verifying feature column schema integrity"),
            "DATASET_READY": ("feature_precompute", "COMPLETE", "Datasets and features fully cached on disk"),
            "DATASET_REGISTRATION": ("feature_precompute", "COMPLETE", "Datasets and features registered in database"),
            "RESEARCH_INITIALIZATION": ("evolution", "RUNNING", "Initializing research population parameters"),
            "RESEARCH": ("evolution", "RUNNING", "Generating new strategy genomes and mutations"),
            "NODE_GENERATION": ("evolution", "RUNNING", "Generating candidate nodes for population"),
            "NODE_EVALUATION": ("validation", "RUNNING", "Screening and evaluating candidate nodes"),
            "BACKTESTING": ("validation", "RUNNING", "Running screening and backtest matrix"),
            "VALIDATION": ("validation", "RUNNING", "Executing validation battery (Walk-forward/Monte Carlo)"),
            "QUALIFICATION": ("evolution", "RUNNING", "Qualifying robust strategies for paper trading"),
            "EVOLUTION": ("evolution", "RUNNING", "Evaluating generation fitness and breeding pool"),
            "RESEARCH_COMPLETED": ("evolution", "COMPLETE", "Target reached — all candidate nodes evaluated"),
        }
        if stage in mapping:
            ms_id, status, desc = mapping[stage]
            if status == "COMPLETE":
                mm.set_complete(ms_id, desc)
            else:
                mm.set_running(ms_id, desc)

    def start_task(
        self,
        stage: str,
        task_name: str,
        total: int,
        substage: str = "",
        worker_id: str = "main",
    ) -> StageTaskState:
        with self._lock:
            tid = f"TSK-{uuid.uuid4().hex[:6].upper()}"
            now = time.time()
            task = StageTaskState(
                task_id=tid,
                stage=stage,
                substage=substage,
                status="ACTIVE",
                current=0,
                total=total,
                percentage=0.0,
                message=task_name,
                started_at=now,
                updated_at=now,
                worker_id=worker_id,
                worker_state="WORKING",
            )
            self._active_tasks[tid] = task

            activity.running(
                self._map_stage_category(stage),
                task_name,
                operation_id=tid,
                progress=0.0,
                details={"stage": stage, "total": total, "task_id": tid}
            )
            return task

    def update_progress(
        self,
        task_id: str,
        current: int,
        item_name: str,
        throughput: Optional[str] = None,
        custom_message: Optional[str] = None,
    ) -> Optional[StageTaskState]:
        with self._lock:
            task = self._active_tasks.get(task_id)
            if not task:
                return None
            now = time.time()
            task.current = current
            task.current_item = item_name
            task.last_op = item_name
            task.updated_at = now
            task.throughput = throughput
            task.worker_state = "WORKING"
            if task.total > 0:
                task.percentage = min(100.0, round((current / task.total) * 100.0, 1))

            msg = custom_message or f"{task.message}: ({current}/{task.total} {item_name})"
            if throughput:
                msg += f" [{throughput}]"

            # Emit subtask tick in activity feed
            cat = self._map_stage_category(task.stage)
            activity.running(
                cat,
                msg,
                operation_id=task_id,
                progress=task.percentage,
                details={
                    "stage": task.stage,
                    "task_id": task_id,
                    "current": current,
                    "total": task.total,
                    "item": item_name,
                    "throughput": throughput,
                    "elapsed_s": round(now - task.started_at, 1),
                }
            )
            return task

    def complete_task(
        self,
        task_id: str,
        result_message: Optional[str] = None,
        reused: bool = False,
    ) -> Optional[StageTaskState]:
        with self._lock:
            task = self._active_tasks.pop(task_id, None)
            if not task:
                return None
            now = time.time()
            task.status = "COMPLETE"
            task.completed_at = now
            task.updated_at = now
            task.percentage = 100.0
            task.current = task.total
            task.worker_state = "IDLE"
            elapsed = round(now - task.started_at, 1)

            final_msg = result_message or f"Completed {task.message} in {elapsed}s"
            if reused:
                final_msg = f"ACTION: REUSED {task.message}"

            cat = self._map_stage_category(task.stage)
            activity.success(
                cat,
                final_msg,
                operation_id=task_id,
                progress=100.0,
                details={
                    "stage": task.stage,
                    "task_id": task_id,
                    "elapsed_s": elapsed,
                    "reused": reused,
                    "completed_at": now,
                }
            )

            self._completed_tasks.append(task)
            if len(self._completed_tasks) > 200:
                self._completed_tasks = self._completed_tasks[-200:]
            return task

    def fail_task(self, task_id: str, error_message: str) -> Optional[StageTaskState]:
        with self._lock:
            task = self._active_tasks.pop(task_id, None)
            if not task:
                return None
            now = time.time()
            task.status = "FAILED"
            task.completed_at = now
            task.updated_at = now
            task.error = error_message
            task.worker_state = "TERMINATED"

            cat = self._map_stage_category(task.stage)
            activity.error(
                cat,
                f"Task failed [{task.stage}]: {error_message}",
                operation_id=task_id,
                details={"stage": task.stage, "task_id": task_id, "error": error_message}
            )
            log.error("[SYSTEM] Task %s failed in stage %s: %s", task_id, task.stage, error_message)

            self._completed_tasks.append(task)
            return task

    def emit_heartbeat(
        self,
        task_id: str,
        custom_message: Optional[str] = None,
        rows_processed: Optional[int] = None,
        rows_total: Optional[int] = None,
    ) -> None:
        """Periodic heartbeat for long-running operations per spec §9."""
        with self._lock:
            task = self._active_tasks.get(task_id)
            if not task:
                return
            now = time.time()
            task.updated_at = now
            if rows_processed is not None:
                task.current = rows_processed
            if rows_total is not None:
                task.total = rows_total
                if rows_total > 0:
                    task.percentage = round(min(100.0, (task.current / rows_total) * 100.0), 1)

            elapsed = round(now - task.started_at, 1)
            vmem = psutil.virtual_memory()
            mem_gb = round(vmem.used / (1024 ** 3), 1)

            hb_msg = custom_message or (
                f"[HEARTBEAT] Task: {task.message} | {task.current:,}/{task.total:,} rows | "
                f"Elapsed: {elapsed:.1f}s | Memory: {mem_gb}GB"
            )
            log.info(hb_msg)

    def watchdog_check(self, stall_threshold_s: float = 30.0) -> List[Dict[str, Any]]:
        """Active watchdog inspecting running tasks for stalls or deadlocks per spec §9."""
        with self._lock:
            now = time.time()
            stalled_alerts = []

            for tid, t in list(self._active_tasks.items()):
                idle_s = now - t.updated_at
                if idle_s > stall_threshold_s:
                    t.status = "STALLED"
                    # Capture live resource metrics
                    vmem = psutil.virtual_memory()
                    cpu_pct = psutil.cpu_percent(interval=None)
                    ram_gb = round(vmem.used / (1024 ** 3), 1)

                    # Distinguish worker alive vs worker dead
                    is_alive = True
                    try:
                        if t.worker_id.startswith("pid-"):
                            pid = int(t.worker_id.split("-")[1])
                            is_alive = psutil.pid_exists(pid)
                    except Exception:
                        pass

                    worker_state_desc = "WORKER ALIVE (WORKING - stalled/blocked)" if is_alive else "WORKER DEAD (abruptly terminated)"
                    t.worker_state = worker_state_desc

                    alert = {
                        "task_id": tid,
                        "stage": t.stage,
                        "substage": t.substage,
                        "current_item": t.current_item,
                        "percentage": t.percentage,
                        "last_op": t.last_op,
                        "idle_seconds": round(idle_s, 1),
                        "elapsed_seconds": round(now - t.started_at, 1),
                        "worker_id": t.worker_id,
                        "worker_state": worker_state_desc,
                        "is_alive": is_alive,
                        "cpu_pct": cpu_pct,
                        "ram_gb": ram_gb,
                    }
                    stalled_alerts.append(alert)

                    warn_msg = (
                        f"[WATCHDOG] No progress detected for {idle_s:.1f}s | "
                        f"Stage: {t.stage} | Task: {t.message} | "
                        f"Item: {t.current_item or 'init'} ({t.current}/{t.total} {t.percentage:.0f}%) | "
                        f"Worker: {t.worker_id} ({worker_state_desc}) | CPU: {cpu_pct:.1f}% | RAM: {ram_gb}GB"
                    )
                    log.warning(warn_msg)
                    log.info(
                        "[WATCHDOG] Stalled task diagnostic: task=%s stage=%s worker=%s alive=%s state=%s",
                        tid, t.stage, t.worker_id, is_alive, worker_state_desc
                    )
                    activity.warning(
                        "WATCHDOG",
                        warn_msg,
                        operation_id=f"wd_{tid}",
                        details=alert
                    )

            return stalled_alerts

    def list_active_tasks(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [t.to_dict() for t in self._active_tasks.values()]

    def get_unified_task_state(self) -> Dict[str, Any]:
        """Unified backend task state consumed by Activity, Milestones, and Dashboard."""
        with self._lock:
            active_list = list(self._active_tasks.values())
            latest_active = active_list[-1] if active_list else None
            out = {
                "current_stage": self._current_stage,
                "active_tasks_count": len(active_list),
                "active_task": latest_active.to_dict() if latest_active else None,
                "all_active_tasks": [t.to_dict() for t in active_list],
                "last_transition_ts": self._last_transition_ts,
            }
        try:
            from ..evolution.engine import get_evo_engine
            evo = get_evo_engine()
            node_state = evo.get_node_generation_state()
            out.update(node_state)
            # Backwards-compatible aliases
            out["node_target"] = node_state["target_nodes"]
            out["current_nodes"] = node_state["current_nodes"]
            out["node_completed"] = node_state["current_nodes"]
            out["generation"] = node_state["generation_number"]
            out["remaining_nodes"] = node_state["remaining_nodes"]
            # V5.4 §1 follow-up — this payload feeds the Live Activity panel,
            # which prints its own DEAD / QUALIFIED counters. The engine's raw
            # formula drifted from the authority (9991 / 5 vs 9996 / 41), so the
            # same aliases as `Lab.status()` are resolved from the ONE snapshot;
            # the raw engine values stay under their `engine_*` names.
            try:
                from ..research.populations import node_state_snapshot, authoritative_aliases
                snap_state = (node_state_snapshot(db=None, engine=evo).get("state") or {})
                for key, value in node_state.items():
                    if key in ("alive_nodes", "dead_nodes", "qualified_nodes",
                               "backtesting_nodes", "validating_nodes"):
                        out[f"engine_{key}"] = value
                out.update(authoritative_aliases(snap_state))
            except Exception as e:                                # pragma: no cover
                log.warning("unified task state: snapshot unavailable: %s", e)
        except Exception as e:
            log.warning("get_unified_task_state node_state fetch warning: %s", e)

        try:
            from .pipeline_state import get_pipeline_state
            ps = get_pipeline_state()
            out["pipeline_state"] = ps.to_dict()
        except Exception as e:
            log.warning("get_unified_task_state pipeline_state fetch warning: %s", e)

        return out

    def _map_stage_category(self, stage: str) -> str:
        mapping = {
            "DATA_SYNC": "DATA",
            "DATA_VALIDATION": "DATA",
            "FEATURE_DISCOVERY": "FEATURES",
            "FEATURE_PRECOMPUTATION": "FEATURES",
            "FEATURE_VALIDATION": "FEATURES",
            "DATASET_READY": "DATA",
            "RESEARCH": "EVOLUTION",
            "BACKTESTING": "BACKTEST",
            "VALIDATION": "VALIDATION",
            "QUALIFICATION": "QUALIFICATION",
            "EVOLUTION": "EVOLUTION",
        }
        return mapping.get(stage, "SYSTEM")


_stage_manager: Optional[StageManager] = None


def get_stage_manager() -> StageManager:
    global _stage_manager
    if _stage_manager is None:
        _stage_manager = StageManager()
    return _stage_manager
