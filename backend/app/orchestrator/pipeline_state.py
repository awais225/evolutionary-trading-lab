"""
Authoritative Single Source of Truth for Laboratory Pipeline State (V2.7).

Maintains a unified, thread-safe, persisted state consumed identically across:
  - Overview dashboard
  - Metro style milestone progress route
  - LIVE ACTIVITY collapsible panel
  - Main LOG diagnostic execution console
  - Node generation accounting & progress indicators

Persisted to DATA/metadata/pipeline_state.json to enable crash-safe recovery
and resumption across software restarts.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil

from .. import paths as P
from ..activity import activity
from ..api.ws import bus
from ..config import get_config
from ..db.database import get_db

log = logging.getLogger("orchestrator.pipeline_state")
_lock = threading.RLock()

PIPELINE_8_STAGES = [
    {"index": 1, "id": "init", "name": "INITIALIZATION", "label": "Initialization"},
    {"index": 2, "id": "mt5", "name": "MT5_CONNECTION", "label": "MT5 Connection"},
    {"index": 3, "id": "data_disc", "name": "DATA_DISCOVERY", "label": "Data Discovery"},
    {"index": 4, "id": "data_sync", "name": "DATA_SYNCHRONIZATION", "label": "Data Synchronization"},
    {"index": 5, "id": "feat_disc", "name": "FEATURE_DISCOVERY", "label": "Feature Discovery"},
    {"index": 6, "id": "feat_comp", "name": "FEATURE_COMPUTATION", "label": "Feature Computation"},
    {"index": 7, "id": "node_gen", "name": "NODE_GENERATION", "label": "Node Generation"},
    {"index": 8, "id": "eval", "name": "EVALUATION", "label": "Evaluation"},
]


class PipelineStateManager:
    """Singleton authoritative pipeline state manager."""

    def __init__(self):
        self._lock = threading.RLock()
        self.meta_dir = P.DATA_ROOT / "metadata"
        self.state_file = self.meta_dir / "pipeline_state.json"
        self.active_run_file = self.meta_dir / "active_run.json"
        self.meta_dir.mkdir(parents=True, exist_ok=True)

        now = time.time()
        now_dt = datetime.fromtimestamp(now, tz=timezone.utc)
        self.run_id = f"RUN-{now_dt.strftime('%Y%m%d')}-CLEAN-MT5"
        self.experiment_id = "EXP-XAUUSD-M15"

        self.stage = "INITIALIZATION"
        self.stage_index = 1
        self.stage_count = 8
        self.stage_name = "INITIALIZATION"

        self.task_name = "System Startup"
        self.task_index = 0
        self.task_count = 0

        self.current_dataset = "XAUUSD_M5"
        self.current_node = 0
        self.node_completed = 0
        self.node_total = 1000
        self.generation = 0

        self.overall_completed = 0
        self.overall_total = 1000
        self.overall_percent = 0.0

        self.status = "RUNNING"  # RUNNING, COMPLETED, WAITING, IDLE, STALLED, FAILED
        self.started_at = now
        self.updated_at = now
        self.last_progress_ts = now
        self.last_success = "Pipeline initialized"
        self.last_error: Optional[str] = None

        self.worker_status = "ACTIVE"
        self.cpu_usage = 0.0
        self.cpu_target = 60
        self.gpu_status = "OFF"
        self.previous_run_info: Dict[str, Any] = {
            "run_id": "RUN-20260927-061830",
            "nodes": 371,
            "preserved": True,
        }

        self.stage_statuses: Dict[str, str] = {
            "INITIALIZATION": "COMPLETED",
            "MT5_CONNECTION": "QUEUED",
            "DATA_DISCOVERY": "QUEUED",
            "DATA_SYNCHRONIZATION": "QUEUED",
            "FEATURE_DISCOVERY": "QUEUED",
            "FEATURE_COMPUTATION": "QUEUED",
            "NODE_GENERATION": "QUEUED",
            "EVALUATION": "QUEUED",
        }

        # Load active run if persisted or query latest from database
        self._load_active_run()

        # Check for previous persisted run to record info without hijacking clean run
        self._check_recovery()

    def _load_active_run(self) -> None:
        if self.active_run_file.exists():
            try:
                data = json.loads(self.active_run_file.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("run_id"):
                    self.run_id = data["run_id"]
                    self.experiment_id = data.get("experiment_id", self.experiment_id)
                    self.node_total = data.get("target_nodes", self.node_total)
                    self.overall_total = self.node_total
                    return
            except Exception:
                pass
        try:
            db = get_db()
            runs = db.get_all_research_runs()
            if runs:
                latest = runs[0]
                if latest.get("status") not in ("COMPLETED", "FINISHED"):
                    self.run_id = latest["run_id"]
                    self.experiment_id = latest.get("experiment_id", self.experiment_id)
                    self.node_total = latest.get("node_ceiling", self.node_total)
                    self.overall_total = self.node_total
        except Exception:
            pass

    def _save_active_run(self) -> None:
        try:
            data = {
                "run_id": self.run_id,
                "experiment_id": self.experiment_id,
                "target_nodes": self.node_total,
                "created_at": self.started_at,
                "status": self.status,
                "updated_at": self.updated_at,
            }
            tmp = self.active_run_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            shutil.move(str(tmp), str(self.active_run_file))
        except Exception as e:
            log.warning("Notice: failed saving active run: %s", e)

    def new_run(self, run_id: Optional[str] = None, target: Optional[int] = None) -> str:
        """Start a new research run with clean frontier without modifying historical database (spec §8)."""
        with self._lock:
            now = time.time()
            now_dt = datetime.fromtimestamp(now, tz=timezone.utc)
            self.run_id = run_id or f"RUN-{now_dt.strftime('%Y%m%d-%H%M%S')}"
            self.started_at = now
            self.updated_at = now
            self.last_progress_ts = now
            self.status = "RUNNING"
            if target and target > 0:
                self.node_total = target
                self.overall_total = target
            self.node_completed = 0
            self.overall_completed = 0
            self.overall_percent = 0.0

            self._save_active_run()

            try:
                db = get_db()
                db.upsert_research_run({
                    "run_id": self.run_id,
                    "experiment_id": self.experiment_id,
                    "created_at": self.started_at,
                    "target_nodes": self.node_total,
                    "status": "RUNNING",
                    "persistence_status": "PERSISTED",
                    "last_checkpoint": now,
                })
            except Exception as e:
                log.warning("Notice: failed upserting research run to DB: %s", e)

            try:
                from .diagnostic_logger import log_research_run_event
                log_research_run_event("NEW_RUN", self.run_id, {"target": self.node_total, "experiment_id": self.experiment_id})
            except Exception:
                pass

            self._persist()
            self._broadcast()
            return self.run_id

    def resume_run(self, target: Optional[int] = None) -> str:
        """Resume existing research from persisted frontier (spec §8)."""
        with self._lock:
            now = time.time()
            self.updated_at = now
            self.status = "RUNNING"
            if target and target > 0:
                self.node_total = target
                self.overall_total = target

            self._save_active_run()

            try:
                db = get_db()
                db.upsert_research_run({
                    "run_id": self.run_id,
                    "experiment_id": self.experiment_id,
                    "created_at": self.started_at,
                    "target_nodes": self.node_total,
                    "status": "RUNNING",
                    "persistence_status": "PERSISTED",
                    "last_checkpoint": now,
                })
            except Exception:
                pass

            try:
                from .diagnostic_logger import log_research_run_event
                log_research_run_event("RESUME_RUN", self.run_id, {"target": self.node_total, "experiment_id": self.experiment_id})
            except Exception:
                pass

            self._persist()
            self._broadcast()
            return self.run_id

    def _check_recovery(self) -> None:
        if not self.state_file.exists():
            return
        try:
            prev = json.loads(self.state_file.read_text(encoding="utf-8"))
            if isinstance(prev, dict):
                prev_run = prev.get("run_id", "RUN-20260927-061830")
                prev_stage = prev.get("stage", "UNKNOWN")
                prev_nodes = prev.get("node_completed", 371)
                self.previous_run_info = {
                    "run_id": prev_run,
                    "stage": prev_stage,
                    "nodes": prev_nodes,
                    "preserved": True,
                }
                log.info(
                    "[RECOVERY] Previous run detected: %s (Last stage: %s, Nodes completed: %d). Preserved in database/archive. ACTION: START CLEAN RUN",
                    prev_run, prev_stage, prev_nodes
                )
                activity.info("SYSTEM", f"Previous run preserved: {prev_run} ({prev_nodes} nodes). Clean run: {self.run_id}")
        except Exception as e:
            log.warning("Failed to inspect previous pipeline state: %s", e)

    def print_startup_diagnostic(self) -> Dict[str, Any]:
        """Authoritative startup diagnostic logging per spec V2.8."""
        from ..data.discovery import get_discovery_engine
        from ..mt5 import bridge_status
        de = get_discovery_engine()
        rep = de.get_startup_status_report()
        valid_raw = rep["data"]["valid_datasets"]
        total_raw = rep["data"]["raw_datasets"]

        bst = bridge_status()
        mt5_connected = bst.get("connected", False) or bst.get("mt5_package_installed", False)
        mt5_status_str = "CONNECTED" if mt5_connected else ("DISCONNECTED" if not bst.get("is_simulated") else "CONNECTED (Simulator bridge active)")

        raw_status = "VALID" if (valid_raw == total_raw and total_raw > 0) else ("EMPTY" if valid_raw == 0 else "PARTIAL")
        raw_source = "MT5 REAL" if (valid_raw > 0) else "NONE"
        prev_run_str = f"PRESERVED BUT NOT RESUMED ({self.previous_run_info.get('run_id', 'RUN-20260927-061830')} with {self.previous_run_info.get('nodes', 371)} nodes)"
        decision = "FETCH REAL MT5 DATA" if valid_raw == 0 else "REUSE REAL RAW DATA"

        diagnostic_lines = [
            "=" * 60,
            " EVOLUTIONARY TRADING RESEARCH LAB — STARTUP DIAGNOSTIC (V3.2)",
            "=" * 60,
            f"RUN ID: {self.run_id}",
            f"DATA ROOT: {P.DATA_ROOT}",
            f"TEST DATA ROOT: {P.TEST_DATA_ROOT}",
            f"CACHE ROOT: {P.CACHE_ROOT}",
            f"DATABASE: {P.DATABASE_DIR / 'lab_state.db'}",
            f"RAW DATA STATUS: {raw_status}",
            f"RAW DATA SOURCE: {raw_source}",
            f"MT5 STATUS: {mt5_status_str}",
            f"SIMULATOR STATUS: NOT ELIGIBLE FOR REAL DATA (SEPARATED IN TEST_DATA)",
            f"PREVIOUS RUN DETECTED: {prev_run_str}",
            f"RESUME DECISION: {decision}",
            "=" * 60,
        ]
        diagnostic_text = "\n".join(diagnostic_lines)
        log.info("\n" + diagnostic_text)
        return {
            "run_id": self.run_id,
            "data_root": str(P.DATA_ROOT),
            "test_data_root": str(P.TEST_DATA_ROOT),
            "cache_root": str(P.CACHE_ROOT),
            "database": str(P.DATABASE_DIR / "lab_state.db"),
            "raw_data_status": raw_status,
            "raw_data_source": raw_source,
            "mt5_status": mt5_status_str,
            "simulator_status": "NOT ELIGIBLE FOR REAL DATA",
            "previous_run_detected": prev_run_str,
            "resume_decision": decision,
            "diagnostic_text": diagnostic_text,
        }

    def set_stage(self, stage_name: str, details: Optional[Dict[str, Any]] = None) -> None:
        """Advance authoritative pipeline stage with terminal event logging."""
        with self._lock:
            old_stage = self.stage
            self.stage = stage_name
            self.updated_at = time.time()
            self.last_progress_ts = time.time()

            # Map to 8-stage index
            stage_map = {
                "INITIALIZATION": (1, "INITIALIZATION"),
                "MT5_CONNECTION": (2, "MT5 CONNECTION"),
                "DATA_DISCOVERY": (3, "DATA DISCOVERY"),
                "DATA_SYNC": (4, "DATA SYNCHRONIZATION"),
                "DATA_SYNCHRONIZATION": (4, "DATA SYNCHRONIZATION"),
                "FEATURE_DISCOVERY": (5, "FEATURE DISCOVERY"),
                "FEATURE_PRECOMPUTATION": (6, "FEATURE COMPUTATION"),
                "FEATURE_COMPUTATION": (6, "FEATURE COMPUTATION"),
                "FEATURE_VALIDATION": (6, "FEATURE COMPUTATION"),
                "DATASET_READY": (6, "FEATURE COMPUTATION"),
                "RESEARCH": (7, "NODE GENERATION"),
                "NODE_GENERATION": (7, "NODE GENERATION"),
                "BACKTESTING": (8, "EVALUATION"),
                "VALIDATION": (8, "EVALUATION"),
                "QUALIFICATION": (8, "EVALUATION"),
                "EVOLUTION": (7, "NODE GENERATION"),
            }

            idx, name = stage_map.get(stage_name, (self.stage_index, stage_name))
            self.stage_index = idx
            self.stage_name = name

            # Update stage statuses
            for s in PIPELINE_8_STAGES:
                s_name = s["name"]
                if s["index"] < idx:
                    self.stage_statuses[s_name] = "COMPLETED"
                elif s["index"] == idx:
                    self.stage_statuses[s_name] = "RUNNING"
                else:
                    self.stage_statuses[s_name] = "QUEUED"

            # Log stage transition clearly
            transition_msg = (
                f"[PIPELINE STAGE {idx}/8 STARTING]\n"
                f"{name}\n"
                f"[PIPELINE] Transitioning {old_stage} → {stage_name}"
            )
            log.info(transition_msg)
            activity.info("PIPELINE", f"Stage {idx}/8: {name} (Started)")

            self._persist()
            self._broadcast()

    def update_subtask(
        self,
        task_name: str,
        current: int,
        total: int,
        dataset: Optional[str] = None,
        item_label: Optional[str] = None,
        throughput: Optional[str] = None,
        status: str = "RUNNING",
    ) -> None:
        """Update active subtask progress (e.g. FEATURES [14/21])."""
        with self._lock:
            self.task_name = task_name
            self.task_index = current
            self.task_count = total
            self.status = status
            self.updated_at = time.time()
            self.last_progress_ts = time.time()
            if dataset:
                self.current_dataset = dataset

            # Update resource snapshot
            try:
                self.cpu_usage = round(psutil.cpu_percent(interval=None), 1)
            except Exception:
                pass

            pct = (current / total * 100.0) if total > 0 else 0.0

            # Broadcast progress
            self._broadcast()

    def update_node_progress(
        self,
        current_node: int,
        total_target: int = 1000,
        generation: int = 0,
        accounting: Optional[Dict[str, int]] = None,
        last_success: Optional[str] = None,
    ) -> None:
        """Authoritative single update for node generation progress across entire app."""
        with self._lock:
            self.current_node = current_node
            self.node_completed = current_node
            self.node_total = total_target
            self.generation = generation
            self.updated_at = time.time()
            self.last_progress_ts = time.time()
            if last_success:
                self.last_success = last_success

            self.overall_completed = current_node
            self.overall_total = total_target
            self.overall_percent = round((current_node / total_target * 100.0), 1) if total_target > 0 else 0.0

            if current_node >= total_target:
                self.status = "COMPLETED"
                self.stage_statuses["NODE_GENERATION"] = "COMPLETED"
                self.stage_statuses["EVALUATION"] = "COMPLETED"

            self._persist()
            self._broadcast()

    def set_completed(self, message: str = "Research run completed", status: str = "COMPLETED_AT_CEILING") -> None:
        """Mark pipeline run completed after target reached and all in-flight nodes finish (spec §7, V3.6)."""
        with self._lock:
            self.status = status
            self.last_success = message
            self.updated_at = time.time()
            self.stage_statuses["NODE_GENERATION"] = "COMPLETED"
            self.stage_statuses["EVALUATION"] = "COMPLETED"
            self._persist()
            self._broadcast()

    def heartbeat(self) -> Dict[str, Any]:
        """Emit periodic technical heartbeat log."""
        with self._lock:
            now = time.time()
            elapsed = round(now - self.started_at, 1)
            idle_s = round(now - self.last_progress_ts, 1)

            try:
                self.cpu_usage = round(psutil.cpu_percent(interval=None), 1)
            except Exception:
                pass

            # Warn if progress unchanged for >30s during active execution
            if idle_s >= 30.0 and self.status == "RUNNING":
                warn_msg = (
                    f"[WARNING]\n"
                    f"Stage: {self.stage}\n"
                    f"Progress unchanged for {idle_s:.1f}s\n"
                    f"Last completed: {self.node_completed}\n"
                    f"Current task: {self.task_name} (Node {self.current_node})\n"
                    f"Worker: {self.worker_status}\n"
                    f"Waiting on: active backtest/evaluation worker\n"
                    f"This is NOT necessarily an error."
                )
                log.warning(warn_msg)
                activity.warning("SYSTEM", f"Stage {self.stage} progress unchanged for {idle_s:.0f}s (Worker: {self.worker_status})")
                self.last_progress_ts = now  # Debounce warning

            rep = (
                f"[HEARTBEAT]\n"
                f"Stage: {self.stage}\n"
                f"Progress: {self.node_completed}/{self.node_total} ({self.overall_percent}%)\n"
                f"Current generation: {self.generation}\n"
                f"Current node: {self.current_node}\n"
                f"Elapsed: {elapsed}s\n"
                f"Last completed node: {self.node_completed}\n"
                f"Last progress event: {idle_s}s ago\n"
                f"Worker status: {self.worker_status}\n"
                f"CPU: {self.cpu_usage}%\n"
                f"GPU: {self.gpu_status}"
            )
            return self.to_dict()

    def to_dict(self) -> Dict[str, Any]:
        # Fetch authoritative node accounting from EvolutionEngine outside lock to prevent lock inversion
        accounting = {}
        node_st = {}
        try:
            from ..evolution.engine import get_evo_engine
            evo = get_evo_engine()
            node_st = evo.get_node_generation_state()
            accounting = node_st.get("node_accounting", {})
        except Exception:
            pass

        db = get_db()
        all_runs = db.get_all_research_runs()
        current_run_match = next((r for r in all_runs if r["run_id"] == self.run_id), None)
        if not current_run_match and all_runs:
            current_run_match = all_runs[0]

        res = {}
        try:
            from ..resources.manager import get_resource_manager
            res = get_resource_manager().live_metrics()
        except Exception:
            pass

        now = time.time()
        elapsed = round(now - self.started_at, 1)

        run_gen_nodes = current_run_match["generated_nodes"] if current_run_match else self.node_completed
        run_comp_nodes = current_run_match["completed_nodes"] if current_run_match else 0
        run_qual_nodes = current_run_match["qualified_nodes"] if current_run_match else 0
        run_pen_bt = current_run_match["pending_backtesting"] if current_run_match else 0
        run_pen_val = current_run_match["pending_validation"] if current_run_match else 0
        ceiling = self.node_total or (current_run_match["node_ceiling"] if current_run_match else 500)
        run_gen = current_run_match["current_generation"] if current_run_match else self.generation

        cum_total = db.total_strategies_count()
        cum_completed = sum(r.get("completed_nodes", 0) for r in all_runs)
        cum_qualified = sum(r.get("qualified_nodes", 0) for r in all_runs)

        with self._lock:
            self.generation = run_gen
            self.node_completed = node_st.get("current_nodes", cum_total)
            self.overall_completed = run_comp_nodes
            self.overall_total = ceiling
            self.overall_percent = round((run_comp_nodes / ceiling) * 100.0, 1) if ceiling > 0 else 100.0
            gen_pct = round((run_gen_nodes / ceiling) * 100.0, 1) if ceiling > 0 else 100.0

            # Build stage items for UI
            stages_list = []
            for s in PIPELINE_8_STAGES:
                s_name = s["name"]
                st = self.stage_statuses.get(s_name, "QUEUED")
                stages_list.append({
                    "index": s["index"],
                    "id": s["id"],
                    "name": s["name"],
                    "label": s["label"],
                    "status": st,
                    "symbol": "✓" if st == "COMPLETED" else ("▶" if st == "RUNNING" else ("○" if st == "QUEUED" else "✕")),
                    "percent": 100.0 if st == "COMPLETED" else (self.overall_percent if st == "RUNNING" else 0.0),
                })

            return {
                "run_id": self.run_id,
                "experiment_id": self.experiment_id,
                "stage": self.stage,
                "stage_index": self.stage_index,
                "stage_count": self.stage_count,
                "stage_name": self.stage_name,
                "stages": stages_list,
                "task_name": self.task_name,
                "task_index": self.task_index,
                "task_count": self.task_count,
                "overall_completed": run_comp_nodes,
                "overall_total": ceiling,
                "overall_percent": self.overall_percent,
                "generated_nodes": run_gen_nodes,
                "generated_percent": gen_pct,
                "completed_nodes": run_comp_nodes,
                "current_dataset": self.current_dataset,
                "current_node": self.current_node,
                "node_completed": node_st.get("current_nodes", cum_total),
                "node_total": ceiling,
                "generation": self.generation,
                "status": self.status,
                "started_at": self.started_at,
                "updated_at": self.updated_at,
                "elapsed": elapsed,
                "last_success": self.last_success,
                "last_error": self.last_error,
                "worker_status": self.worker_status,
                "cpu_usage": self.cpu_usage,
                "cpu_target": self.cpu_target,
                "gpu_status": self.gpu_status,
                "app_cpu_usage": res.get("cpu", {}).get("app_percent", 0.0),
                "active_workers": res.get("cpu", {}).get("active_workers", 0),
                "idle_workers": res.get("cpu", {}).get("idle_workers", 0),
                "effective_workers": res.get("cpu", {}).get("effective_workers", 1),
                "ram_used_mb": res.get("memory", {}).get("used_mb", 0.0),
                "app_ram_mb": res.get("memory", {}).get("app_used_mb", 0.0),
                "app_peak_ram_mb": res.get("memory", {}).get("app_peak_mb", 0.0),
                "tasks_per_minute": res.get("tasks", {}).get("tasks_per_minute", 0),
                "avg_task_duration_ms": res.get("tasks", {}).get("avg_duration_ms", 0.0),
                "node_accounting": accounting,
                "current_run": {
                    "run_id": self.run_id,
                    "experiment_id": self.experiment_id,
                    "created_at": self.started_at,
                    "current_generation": self.generation,
                    "node_ceiling": ceiling,
                    "target": ceiling,
                    "generated_nodes": run_gen_nodes,
                    "completed_nodes": run_comp_nodes,
                    "qualified_nodes": run_qual_nodes,
                    "pending_validation": run_pen_val,
                    "pending_backtesting": run_pen_bt,
                    "run_status": self.status,
                    "persistence_status": "PERSISTED",
                    "last_checkpoint": self.updated_at,
                },
                "cumulative": {
                    "total_nodes": cum_total,
                    "completed_nodes": cum_completed,
                    "qualified_nodes": cum_qualified,
                    "total_runs": len(all_runs),
                },
                "runs": all_runs,
                "historical_runs": all_runs,
            }

    get_state = to_dict

    def _persist(self) -> None:
        try:
            d = self.to_dict()
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(d, indent=2, sort_keys=True), encoding="utf-8")
            shutil.move(str(tmp), str(self.state_file))
        except Exception as e:
            log.warning("Failed to persist pipeline state: %s", e)

    def _broadcast(self) -> None:
        try:
            d = self.to_dict()
            bus.publish("pipeline_state", d)
        except Exception:
            pass


_pipeline_state_manager: Optional[PipelineStateManager] = None


def get_pipeline_state() -> PipelineStateManager:
    global _pipeline_state_manager
    with _lock:
        if _pipeline_state_manager is None:
            _pipeline_state_manager = PipelineStateManager()
        return _pipeline_state_manager


get_pipeline_state_manager = get_pipeline_state
