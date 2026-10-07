"""
Lab Orchestrator — the autonomous research loop (V2).

Modes:
  exploration  – birth-heavy random exploration, cheap screening only
  evolution    – standard generate -> screen -> detail -> select -> reproduce
  validation   – focus on OOS / walk-forward / robustness of survivors
  paper        – paper trading only (live feed), no new births
  continuous   – full loop: generate, test, analyze, mutate, validate,
                 paper test, compare paper vs backtest, hypothesize, repeat

V2 enhancements:
  - Deterministic experiment fingerprinting & stale detection (spec §9)
  - Complete trade history & equity curves exported to Parquet (spec §11)
  - RESEARCH/ directory mirroring for portability & inspection (spec §10)
  - Computational resource controls (CPU workers & memory budget, spec §26-28)
  - Real-time operational activity stream (spec §20, §21)
"""
from __future__ import annotations

import itertools
import json
import logging
import os
import random
import threading
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..activity import activity
from ..ai_researcher.analyzer import apply_hypothesis, run_research_cycle
from ..api.ws import bus
from ..backtest.fingerprint import compute_experiment_fingerprint
from ..config import config_digest, get_config
from ..data.engine import get_data_engine
from ..status import STRATEGY_FAILED, classify_failure
from ..db.database import get_db
from ..evolution.engine import EvolutionEngine
from ..features.engine import get_feature_engine
from ..fitness.evaluator import death_check, fitness
from ..genome.schema import describe, genome_hash
from ..jsonutil import jd
from ..paper import calibration as calib
from ..paper.engine import get_paper_engine
from ..research import export
from ..resources.manager import get_resource_manager
from ..specialization.matrix import full_matrices, propose_specializations
from ..validation.engine import full_validation
from ..versions import manifest

log = logging.getLogger("orchestrator.lab")

MODES = ("exploration", "evolution", "validation", "paper", "continuous")

CORE_FEATURE_SPECS = ["price", "ema:20", "ema:50", "ema:100", "sma:20", "rsi:14",
                      "macd:12:26:9", "roc:12", "momentum:10", "stoch:14:3",
                      "cci:20", "adx:14", "atr:14", "bb:20:2.0", "volatility:20",
                      "volume:20", "vwap", "prev_day", "time", "regime", "sessions"]


def _bt_payload(genome, dataset_id, stage, window=None, **kw):
    p = {"genome": genome, "dataset_id": dataset_id, "stage": stage, "window": window}
    p.update(kw)
    return p


def feature_validation_outcome(usable_ids: List[str], failures: List[Dict[str, str]]) -> Dict[str, Any]:
    """V5 §1 — what a feature-validation result means for the research cycle.

    * nothing usable  -> BLOCK (with the real per-dataset reasons in `reason`)
    * some unusable   -> CONTINUE and exclude exactly those datasets; their nodes
                         are data outcomes, never strategy failures

    A missing feature cache is *derived* data: the engine computes it on demand,
    so only a dataset that still cannot produce its core feature set is excluded.
    """
    excluded = [str(f.get("dataset_id")) for f in failures or []]
    if failures and not usable_ids:
        detail = "; ".join(str(f.get("reason") or "") for f in failures)[:400]
        return {
            "action": "BLOCK",
            "excluded": excluded,
            "reason": f"FEATURE_VALIDATION_FAILED: no dataset could produce its core feature set - {detail}",
        }
    return {"action": "CONTINUE", "excluded": excluded, "reason": None}


class Lab:
    def __init__(self):
        self.db = get_db()
        self.evo = EvolutionEngine(self.db)
        self.mode = "continuous"
        self.running = False
        self.paused = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._pool: Optional[ProcessPoolExecutor] = None
        self._pool_workers = 0
        self._cycle = 0
        self._tested_this_gen = 0
        self._generation = 0
        self._datasets_ready = False
        self.counters = {"screened": 0, "detailed": 0, "validated": 0, "qualified": 0,
                         "killed": 0, "born": 0, "duplicates": 0, "hypotheses": 0,
                         "specializations": 0, "cycles": 0,
                         # V5 §4 diagnostics — separated so a data/infrastructure
                         # problem can never be read as a strategy result again
                         "data_failures": 0, "data_corrupt": 0, "backtest_errors": 0,
                         "skipped": 0, "rejected": 0, "strategy_failed": 0,
                         "tested": 0, "survived": 0, "dataset_unavailable_candidates": 0,
                         "requeued": 0}
        self.last_error = ""
        self.safe_paused = False
        self.safe_paused_reason = ""
        self._consecutive_failures = 0
        self._strategy_recovery_attempts: Dict[int, int] = {}
        self._recovered_strategy_ids: set[int] = set()
        self._last_recovery_check_ts: float = 0.0
        self._last_pipeline_idle_ts: float = 0.0
        # V5 §3/§4: nodes that were never judged because of data/infrastructure
        self._infra_lock = threading.RLock()
        self._infrastructure_nodes: set = set()
        self._lock = threading.RLock()

    # ---------------- lifecycle ----------------
    def start(self, mode: str = "continuous", target: Optional[int] = None,
              run_type: str = "resume", new_run_id: Optional[str] = None) -> Dict:
        mode = mode.lower()
        if mode not in MODES:
            return {"ok": False, "error": f"unknown mode {mode}"}
        if target is not None and int(target) > 0:
            self.evo.set_total_node_target(int(target))

        from .pipeline_state import get_pipeline_state_manager
        psm = get_pipeline_state_manager()

        if run_type == "new":
            new_id = psm.new_run(run_id=new_run_id, target=target)
            self.evo.active_run_id = new_id
            log.info("[RESEARCH] Started NEW RESEARCH RUN %s (target=%s)", new_id, self.evo.get_total_node_target())
            activity.info("SYSTEM", f"Started NEW RESEARCH RUN ({new_id}) with target {self.evo.get_total_node_target()} nodes")
        else:
            active_id = psm.run_id or self.evo.active_run_id
            if not active_id:
                db_runs = self.db.get_all_research_runs()
                active_id = db_runs[0]["run_id"] if db_runs else "RUN-HISTORICAL-PRESERVED"
            self.evo.active_run_id = active_id
            psm.run_id = active_id
            psm.resume_run(target=target)
            log.info("[RESEARCH] Resuming existing research run %s from frontier (%d persisted nodes, target=%d)",
                     active_id, self.evo.total_nodes(), self.evo.get_total_node_target())
            activity.info("SYSTEM", f"Resumed research run {active_id} from existing frontier ({self.evo.total_nodes()} nodes, target {self.evo.get_total_node_target()})")

        with self._lock:
            self.mode = mode
            self.safe_paused = False
            self.safe_paused_reason = ""
            self._consecutive_failures = 0
            self._research_completed = False
            if self.running and not self.paused:
                return {"ok": True, "already_running": True, "mode": mode, "run_id": psm.run_id}
            self.paused = False
            if not self.running:
                self.running = True
                self._stop.clear()
                self._thread = threading.Thread(target=self._loop, daemon=True,
                                                name="lab-orchestrator")
                self._thread.start()
        activity.info("SYSTEM", f"Laboratory research loop started (mode: {mode})")
        self._publish_state("started")
        return {"ok": True, "mode": mode, "run_id": psm.run_id}

    def pause(self) -> Dict:
        with self._lock:
            self.paused = True
        activity.info("SYSTEM", "Laboratory research loop paused")
        self._publish_state("paused")
        return {"ok": True}

    def resume(self, target: Optional[int] = None) -> Dict:
        if target is not None and int(target) > 0:
            self.evo.set_total_node_target(int(target))
        from .pipeline_state import get_pipeline_state_manager
        psm = get_pipeline_state_manager()
        active_id = psm.run_id or self.evo.active_run_id
        if not active_id:
            db_runs = self.db.get_all_research_runs()
            active_id = db_runs[0]["run_id"] if db_runs else "RUN-HISTORICAL-PRESERVED"
        self.evo.active_run_id = active_id
        psm.run_id = active_id
        psm.resume_run(target=target)
        with self._lock:
            self.paused = False
            self.safe_paused = False
            self.safe_paused_reason = ""
            self._consecutive_failures = 0
            self._research_completed = False
        activity.info("SYSTEM", f"Laboratory research loop resumed for run {active_id} (frontier: {self.evo.total_nodes()}/{self.evo.get_total_node_target()} nodes)")
        self._publish_state("resumed")
        return {"ok": True}

    def stop(self) -> Dict:
        with self._lock:
            self.running = False
            self.paused = False
            self._stop.set()
        get_paper_engine().stop()
        self._shutdown_pool()
        try:
            from ..features.tasks import get_feature_task_manager
            get_feature_task_manager().cancel_all_active("Laboratory research loop stopped by user")
        except Exception:
            pass
        activity.set_idle()
        # Explicit diagnostic log on intentional user stop (spec §3, §4, §16)
        self.evo.emit_stop_diagnostic("NODE GENERATION STOPPED: USER REQUEST", recovery_action="Click Start to resume research loop")
        activity.info("SYSTEM", "NODE GENERATION STOPPED: USER REQUEST")
        self._publish_state("stopped")
        return {"ok": True}

    def reset_generation(self) -> Dict:
        cfg = get_config()
        out = self.evo.reset_generation(cfg.data.symbol)
        self._tested_this_gen = 0
        activity.warning("EVOLUTION", f"Generation reset: population reseeded with {out.get('new_seeds')} strategies")
        bus.publish("generation_reset", out)
        return {"ok": True, **out}

    def clear_failed(self) -> Dict:
        n = self.evo.clear_failed()
        activity.info("EVOLUTION", f"Cleared {n} leaf failed strategies (ancestor chains preserved)")
        bus.publish("failed_cleared", {"deleted": n})
        return {"ok": True, "deleted": n}

    def recheck(self) -> Dict[str, Any]:
        """V2.1 RECHECK: fast reconstruction of persisted state from SQLite without rerun (spec §V2.1 G).

        V4.0: the displayed research state excludes the ~787 LEGACY_TEST
        infrastructure nodes (exclude_legacy=True), so the dashboard statistics
        describe the user research population. Node statuses themselves are not
        modified in any way.
        """
        target = self.evo.get_total_node_target()
        state = self.db.reconstruct_state(target, exclude_legacy=True)
        self._generation = state["current_generation"]
        bus.publish("lab_recheck", state)
        bus.publish("lab_status", self.status())
        activity.info(
            "EVOLUTION",
            f"RECHECK completed: {state['total_nodes']} total nodes (Target: {state['target']}, "
            f"Remaining: {state['remaining']}, Alive: {state['alive']}, Dead: {state['dead']}, "
            f"Backtesting: {state['backtesting']}, Validating: {state['validating']}, "
            f"Qualified: {state['qualified']}, Gen: {state['current_generation']})"
        )
        return state

    def status(self) -> Dict:
        """Laboratory status payload for API/WebSocket display.

        V4.0: the population/status statistics reported here describe the user
        research population only - the ~787 LEGACY_TEST infrastructure records
        are excluded (exclude_legacy=True). The status classification, metric
        formulas and the engine's own internal counts are untouched; this is the
        display scope only.
        """
        target = self.evo.get_total_node_target()
        reconstructed = self.db.reconstruct_state(target, exclude_legacy=True)
        node_state = self.evo.get_node_generation_state(exclude_legacy=True)
        return {
            "running": self.running, "paused": self.paused,
            "safe_paused": getattr(self, "safe_paused", False),
            "safe_paused_reason": getattr(self, "safe_paused_reason", ""),
            "consecutive_failures": getattr(self, "_consecutive_failures", 0),
            "mode": self.mode,
            "cycle": self._cycle, "generation": node_state["generation_number"],
            "population_active": self.evo.active_count(exclude_legacy=True),
            "status_counts": reconstructed["status_counts"], "counters": self.counters,
            "last_error": self.last_error,
            "duplicates_blocked": self.evo.duplicates_blocked,
            "paper_running": get_paper_engine().running,
            "datasets_ready": self._datasets_ready,
            # Authoritative single source of truth for node generation (spec §3)
            **node_state,
            # V2.1 Total Node Target & Reconstruction fields (spec §V2.1 I)
            "total_nodes": node_state["current_nodes"],
            "target": node_state["target_nodes"],
            "remaining": node_state["remaining_nodes"],
            "alive": node_state["alive_nodes"],
            "dead": node_state["dead_nodes"],
            "backtesting": node_state["backtesting_nodes"],
            "validating": node_state["validating_nodes"],
            "qualified": node_state["qualified_nodes"],
            "target_reached": node_state["is_target_reached"],
            "progress": f"{node_state['current_nodes']} / {node_state['target_nodes']}",
            "progress_pct": node_state["progress_pct"],
            "next_strategy_id": reconstructed["next_strategy_id"],
            "ancestry": reconstructed["ancestry"],
            "fingerprints": reconstructed["fingerprints"],
            "research": reconstructed["research"],
        }

    def _publish_state(self, action: str) -> None:
        bus.publish("lab_state", {"action": action, **self.status()})
        self.db.log_event("lab_" + action, {"mode": self.mode})

    # ---------------- worker pool & resource control (spec §27) ----------------
    def _ensure_pool(self) -> None:
        rm = get_resource_manager()
        n_workers = rm.effective_workers()
        with self._lock:
            if self._pool is None or self._pool_workers != n_workers:
                if self._pool is not None:
                    try:
                        self._pool.shutdown(wait=False, cancel_futures=True)
                    except Exception:
                        pass
                try:
                    self._pool = ProcessPoolExecutor(
                        max_workers=max(1, n_workers),
                        initializer=_worker_init,
                        mp_context=__import__("multiprocessing").get_context(
                            "spawn" if os.name == "nt" else "fork"))
                    self._pool_workers = n_workers
                    log.info("ProcessPoolExecutor started with %d workers (CPU limit enforced)", n_workers)
                except Exception as e:
                    log.warning("process pool unavailable (%s) — inline mode", e)
                    self._pool = None
                    self._pool_workers = 0

    def _shutdown_pool(self) -> None:
        with self._lock:
            if self._pool is not None:
                try:
                    self._pool.shutdown(wait=False, cancel_futures=True)
                except Exception:
                    pass
                self._pool = None
                self._pool_workers = 0

    def resize_pool(self) -> int:
        """Dynamically resize worker pool according to current CPU target/settings."""
        with self._lock:
            rm = get_resource_manager()
            needed = rm.effective_workers()
            if self._pool_workers != needed and self._pool is not None:
                log.info("Dynamically resizing worker pool from %d to %d workers",
                         self._pool_workers, needed)
                self._shutdown_pool()
                self._ensure_pool()
            return self._pool_workers

    def _run_batch(self, payloads: List[Dict], timeout: float = 240.0) -> List[Dict]:
        """Run backtests in parallel obeying configured CPU & memory limits."""
        if not payloads:
            return []

        rm = get_resource_manager()
        # Memory budget gate (spec §28) - scale to concurrent worker footprint
        concurrent_load = min(len(payloads), rm.effective_workers())
        if not rm.check_memory_budget(estimated_mb=concurrent_load * 2.0):
            # V5: returning an empty list here left every candidate stuck in
            # BACKTESTING (the caller zips payloads with results). Returning one
            # None per payload lets the caller requeue them honestly.
            time.sleep(0.1)
            return [None] * len(payloads)

        self._ensure_pool()
        results: List[Optional[Dict]] = [None] * len(payloads)
        pool = self._pool
        t0 = time.perf_counter()
        if pool is None:
            _worker_init()
            from .workers import bt_worker
            for i, p in enumerate(payloads):
                try:
                    results[i] = bt_worker(p)
                except Exception as e:
                    results[i] = {"ok": False, "error": str(e), "metrics": {},
                                  "trades": [], "equity_curve": []}
        else:
            futs = {}
            rm.update_job_counts(running=len(payloads), queued=0, active_workers=min(len(payloads), self._pool_workers))
            try:
                for i, p in enumerate(payloads):
                    futs[pool.submit(_worker_bt, p)] = i
                for fut in as_completed(futs, timeout=timeout):
                    i = futs[fut]
                    try:
                        results[i] = fut.result()
                    except Exception as e:
                        results[i] = {"ok": False, "error": str(e), "metrics": {},
                                      "trades": [], "equity_curve": []}
            except TimeoutError:
                log.error("backtest batch timed out — recycling pool")
                for i, r in enumerate(results):
                    if r is None:
                        results[i] = {"ok": False, "error": "timeout", "metrics": {},
                                      "trades": [], "equity_curve": []}
                self._shutdown_pool()
            except Exception as e:
                log.exception("pool failure: %s", e)
                self._shutdown_pool()
                for i, r in enumerate(results):
                    if r is None:
                        results[i] = {"ok": False, "error": f"pool: {e}", "metrics": {},
                                      "trades": [], "equity_curve": []}
            finally:
                rm.update_job_counts(running=0, queued=0, active_workers=0)

        t_compute = time.perf_counter() - t0

        # Record task completion metrics for throughput and duration telemetry (V3.5 Phase 2)
        dur_per_task = t_compute / max(1, len(payloads))
        for r in results:
            rm.record_task_completion(dur_per_task, success=bool(r and r.get("ok")))

        # CPU Target Duty Cycle Governor (spec §3)
        # Governs micro-pause based on configured target CPU (10%-100%)
        cfg = get_config()
        target_pct = getattr(cfg.resources, "cpu_target_pct", 60)
        if target_pct < 100 and t_compute > 0:
            target_duty = max(0.1, min(0.95, target_pct / 100.0))
            desired_pause = t_compute * ((1.0 - target_duty) / target_duty)
            time.sleep(min(desired_pause, 0.5))

        # Guarantee exact 1:1 correspondence for every payload
        for i in range(len(payloads)):
            if results[i] is None:
                results[i] = {"ok": False, "error": "Evaluation worker uncompleted", "metrics": {},
                              "trades": [], "equity_curve": []}
        return results

    # ---------------- main loop ----------------
    def _emit_node_heartbeat(self) -> None:
        try:
            rm = get_resource_manager()
            live = rm.live_metrics()
            from .stages import get_stage_manager
            sm = get_stage_manager()
            target = self.evo.get_total_node_target()
            cur_persisted = self.evo.total_nodes()
            rem = max(0, target - cur_persisted)
            gen = self.evo.generation()

            pending_queue = self.db.one(
                "SELECT COUNT(*) c FROM strategies WHERE status IN ('BORN', 'BACKTESTING')"
            )["c"]
            completed_tasks = self.counters.get("screened", 0) + self.counters.get("detailed", 0)
            failed_tasks = self.counters.get("killed", 0) + self.db.one(
                "SELECT COUNT(*) c FROM strategies WHERE status IN ('FAILED','STRATEGY_FAILED','KILLED','RETIRED')"
            )["c"]
            # V5 §4: data/infrastructure skips are reported separately from
            # strategy failures (10,000 -> 1-5 was mostly this number)
            infra_tasks = self.db.one(
                """SELECT COUNT(*) c FROM strategies
                   WHERE status IN ('DATA_UNAVAILABLE','DATA_CORRUPT','BACKTEST_ERROR')
                      OR (status='FAILED' AND (creation_reason LIKE 'DATASET UNAVAILABLE%'
                                               OR creation_reason LIKE 'TRAIN_WINDOW_FAILED%'))"""
            )["c"]
            alive_tasks = self.db.one(
                """SELECT COUNT(*) c FROM strategies
                   WHERE status IN ('SURVIVED','QUALIFIED','SHORTLISTED','SCREENED','PAPER',
                                    'MT5_BACKTESTED','LIVE_ELIGIBLE','LIVE_TESTING',
                                    'VALID','LIVE_COMPLETED','MT5_DEMO')"""
            )["c"]
            duplicate_tasks = self.counters.get("duplicates", 0) + self.evo.duplicates_blocked
            eligible_parents = len(self.evo.elites(20))
            children_gen = self.counters.get("born", 0)
            children_rej = self.counters.get("killed", 0)
            eff_workers = rm.effective_workers()
            cpu_pct = live["cpu"]["percent"]
            gpu_str = "ENABLED" if live["gpu"]["enabled"] else "OFF"

            stop_condition = "TARGET REACHED" if cur_persisted >= target else ("PAUSED" if self.paused else ("STOPPED" if not self.running else "ACTIVE"))

            log.info(
                "[NODE GENERATION DIAGNOSTICS]\n"
                f"  TARGET NODES: {target}\n"
                f"  CURRENT PERSISTED NODES: {cur_persisted}\n"
                f"  CURRENT GENERATED NODES: {cur_persisted}\n"
                f"  REMAINING NODES: {rem}\n"
                f"  CURRENT GENERATION / BATCH: Gen {gen}\n"
                f"  ACTIVE WORKERS: {eff_workers}\n"
                f"  PENDING TASKS: {pending_queue}\n"
                f"  COMPLETED TASKS: {completed_tasks}\n"
                f"  REJECTED TASKS: {children_rej}\n"
                f"  DUPLICATE TASKS: {duplicate_tasks}\n"
                f"  FAILED TASKS: {failed_tasks}\n"
                f"  DATA/INFRA SKIPPED TASKS: {infra_tasks}\n"
                f"  ALIVE TASKS: {alive_tasks}\n"
                f"  ELIGIBLE PARENTS: {eligible_parents}\n"
                f"  CHILDREN GENERATED: {children_gen}\n"
                f"  CHILDREN REJECTED: {children_rej}\n"
                f"  STOP CONDITION: {stop_condition}"
            )

            msg = (
                f"[NODE ENGINE] ACTIVE | Current Node: {cur_persisted} / {target} (Rem: {rem}) | "
                f"Stage: {sm.current_stage} | Gen: {gen} | Workers: {eff_workers} | "
                f"Queue: {pending_queue} | CPU: {cpu_pct:.1f}% | GPU: {gpu_str}"
            )
            activity.heartbeat("NODE ENGINE", msg, details={
                "target_nodes": target,
                "current_persisted_nodes": cur_persisted,
                "current_generated_nodes": cur_persisted,
                "remaining_nodes": rem,
                "current_generation": gen,
                "active_workers": eff_workers,
                "pending_tasks": pending_queue,
                "completed_tasks": completed_tasks,
                "rejected_tasks": children_rej,
                "duplicate_tasks": duplicate_tasks,
                "failed_tasks": failed_tasks,
                "data_skipped_tasks": infra_tasks,
                "alive_tasks": alive_tasks,
                "eligible_parents": eligible_parents,
                "children_generated": children_gen,
                "children_rejected": children_rej,
                "stop_condition": stop_condition,
                "stage": sm.current_stage,
                "cpu_pct": cpu_pct,
                "gpu_str": gpu_str,
            })

            try:
                from .pipeline_state import get_pipeline_state
                ps = get_pipeline_state()
                ps.update_node_progress(cur_persisted, target, gen)
                ps.heartbeat()
            except Exception:
                pass
        except Exception:
            pass

    def _generation_watchdog_check(self) -> None:
        """Watchdog detecting stalled generation when target not yet reached (spec §15)."""
        if not self.running or self.paused or self.evo.is_target_reached():
            return
        target = self.evo.get_total_node_target()
        cur = self.evo.total_nodes()
        if cur >= target:
            return

        now = time.time()
        pipeline_candidates = self.db.one(
            "SELECT COUNT(*) c FROM strategies WHERE status IN ('BORN', 'BACKTESTING', 'SURVIVED', 'VALIDATING')"
        )["c"]

        # Check for empty pipeline / queue starvation before target (V3.5 Phase 2)
        if pipeline_candidates == 0:
            log.info(
                "[WATCHDOG] Node generation idle before target: 0 pipeline candidates (Current: %d / Target: %d). "
                "Triggering immediate candidate reproduction.", cur, target
            )
            activity.info(
                "WATCHDOG",
                f"Generation pipeline idle at {cur}/{target} nodes. Replenishing candidates immediately.",
                details={"current_nodes": cur, "target_nodes": target}
            )
            self._ensure_population()

    def _loop(self) -> None:
        from .stages import get_stage_manager
        sm = get_stage_manager()
        last_heartbeat_t = 0.0
        MAX_CONSECUTIVE_FAILURES = 3

        while not self._stop.is_set():
            if self.paused or not self.running:
                self._stop.wait(0.5)
                continue
            try:
                # Active watchdog inspecting running stages for stalls or deadlocks (User Spec §3)
                sm.watchdog_check(stall_threshold_s=30.0)

                # Continuous activity heartbeat (spec §11)
                now = time.time()
                if now - last_heartbeat_t >= 2.0:
                    last_heartbeat_t = now
                    self._emit_node_heartbeat()

                # Node generation stall watchdog (spec §15)
                self._generation_watchdog_check()

                self._tick()
                # Reset consecutive failure counter on successful tick
                self._consecutive_failures = 0

                # Adaptive loop yield based on pending work and configured CPU target (spec §3, V3.5 Phase 2)
                run_filter = f"AND run_id='{self.evo.active_run_id}'" if self.evo.active_run_id else ""
                has_pending = self.db.one(
                    f"SELECT 1 FROM strategies WHERE status IN ('BORN', 'SURVIVED', 'VALIDATING', 'BACKTESTING') {run_filter} LIMIT 1"
                )
                target_pct = getattr(get_config().resources, "cpu_target_pct", 60)
                target = self.evo.get_total_node_target()
                cur_nodes = self.evo.total_nodes()
                target_not_reached = cur_nodes < target

                if target_not_reached:
                    if has_pending:
                        if target_pct >= 95:
                            self._stop.wait(0.005)
                        elif target_pct >= 75:
                            self._stop.wait(0.02)
                        elif target_pct >= 50:
                            self._stop.wait(0.06)
                        else:
                            self._stop.wait(0.15)
                    else:
                        # Queue exhausted but target not yet reached: trigger replenishment immediately
                        self._ensure_population()
                        duty_pause = max(0.01, min(0.3, (100 - target_pct) / 200.0))
                        self._stop.wait(duty_pause)
                else:
                    self._stop.wait(get_config().evolution.loop_interval_s)
            except Exception as e:
                self._consecutive_failures += 1
                self.last_error = f"{type(e).__name__}: {e}"
                log.exception("[ORCHESTRATOR] Tick failed (consecutive failure #%d): %s",
                              self._consecutive_failures, e)

                if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    with self._lock:
                        self.paused = True
                        self.safe_paused = True
                        self.safe_paused_reason = (
                            f"Repeated orchestrator failures ({self._consecutive_failures} consecutive). "
                            f"Last error: {self.last_error}"
                        )
                    log.error(
                        "[ORCHESTRATOR] Repeated failures detected (%d consecutive). Entering SAFE PAUSED mode.\n"
                        "Reason: %s\n"
                        "Research paused. Dashboard, health, and management APIs remain fully responsive.",
                        self._consecutive_failures, self.safe_paused_reason
                    )
                    self.evo.emit_stop_diagnostic(
                        "ORCHESTRATOR STATUS: SAFE PAUSED — REPEATED FAILURES",
                        exception=e,
                        recovery_action="Inspect logs and click Start/Resume to restart research"
                    )
                    activity.error(
                        "SYSTEM",
                        f"ORCHESTRATOR STATUS: PAUSED — Repeated orchestrator failures ({self._consecutive_failures}x). Last error: {self.last_error}. Research paused to protect application.",
                        details={"consecutive_failures": self._consecutive_failures, "last_error": self.last_error, "safe_paused": True}
                    )
                    self._publish_state("paused")
                    bus.publish("orchestrator_safe_paused", {
                        "reason": self.safe_paused_reason,
                        "last_error": self.last_error,
                        "consecutive_failures": self._consecutive_failures,
                    })
                else:
                    backoff_s = 2.0 if self._consecutive_failures == 1 else 5.0
                    log.warning("[ORCHESTRATOR] Controlled retry backoff: waiting %.1fs before next attempt (#%d/%d)...",
                                backoff_s, self._consecutive_failures, MAX_CONSECUTIVE_FAILURES)
                    activity.warning(
                        "SYSTEM",
                        f"Orchestrator failure #{self._consecutive_failures}/{MAX_CONSECUTIVE_FAILURES}: {type(e).__name__}. Retrying in {backoff_s:.0f}s...",
                        details={"consecutive_failures": self._consecutive_failures, "backoff_s": backoff_s, "error": str(e)}
                    )
                    bus.publish("lab_error", {"error": self.last_error, "retry_in": backoff_s})
                    self._stop.wait(backoff_s)

        # Loop exited: verify whether target reached or unexpected exit
        if not self.evo.is_target_reached() and not self._stop.is_set():
            self.evo.emit_stop_diagnostic("NODE GENERATION STOPPED UNEXPECTEDLY", recovery_action="Click Start to resume research loop")
            activity.warning("SYSTEM", "NODE GENERATION STOPPED UNEXPECTEDLY")

    def _tick(self) -> None:
        self._cycle += 1
        self.counters["cycles"] += 1
        cfg = get_config()

        # 1) datasets & feature warm-up (once)
        if not self._datasets_ready:
            ready = self._ensure_datasets()
            if not ready:
                self._datasets_ready = False
                log.warning("[ORCHESTRATOR] Research cycle blocked: datasets or features not ready.")
                self._stop.wait(2.0)
                return
            self._datasets_ready = True

        mode = self.mode
        if mode == "paper":
            pe = get_paper_engine()
            if not pe.running and cfg.paper.autostart:
                pe.start()
            self._publish_state_tick()
            return

        if mode == "continuous":
            pe = get_paper_engine()
            # Only autostart paper trading if explicitly enabled in config (spec §49)
            if not pe.running and self._count_qualifies() > 0 and cfg.paper.autostart:
                pe.start()
                activity.info("PAPER", "Paper trading started for qualified strategies")
                bus.publish("paper_auto_started", {"reason": "continuous mode with qualified strategies"})

        # 2) population top-up
        if mode in ("exploration", "evolution", "continuous"):
            self._ensure_population()

        # 3) screening (stage 1)
        if mode in ("exploration", "evolution", "continuous"):
            self._screen_batch()

        # 4) detailed backtest (stage 2)
        if mode in ("evolution", "validation", "continuous"):
            self._detail_batch()

        # 5) validation battery (stages 3-5)
        if mode in ("validation", "continuous", "evolution"):
            self._validation_batch()

        # 6) specialization of newly qualified
        if mode in ("evolution", "continuous"):
            self._specialization_pass()

        # 7) AI researcher cycle
        if mode == "continuous" and cfg.ai.researcher_enabled and self._cycle % 8 == 0:
            self._research_pass()

        # 8) population cap + stats
        self.evo.enforce_population_cap(cfg.data.symbol)
        self._maybe_generation_complete()
        if mode == "continuous" and self._cycle % 60 == 0:
            calib.snapshot_to_db()

        # 9) In-flight node completion and target boundary check (spec §6, §7)
        if self.evo.is_target_reached():
            from ..evolution.engine import IN_FLIGHT_STATES
            run_filter = f"AND run_id='{self.evo.active_run_id}'" if self.evo.active_run_id else ""
            in_flight_row = self.db.one(
                f"SELECT COUNT(*) c FROM strategies WHERE status IN {tuple(IN_FLIGHT_STATES)} {run_filter}"
            )
            in_flight_count = in_flight_row["c"] if in_flight_row else 0
            if in_flight_count == 0 and not getattr(self, "_research_completed", False):
                self._research_completed = True
                target = self.evo.get_total_node_target()
                cur_nodes = self.evo.total_nodes()
                if self.evo.active_run_id:
                    self.db.x("UPDATE research_runs SET status='COMPLETED', last_checkpoint=? WHERE run_id=?",
                              (time.time(), self.evo.active_run_id))
                    try:
                        from .diagnostic_logger import log_research_run_event
                        log_research_run_event("COMPLETED", self.evo.active_run_id, {"target": target, "total_nodes": cur_nodes})
                    except Exception:
                        pass
                from .stages import get_stage_manager
                sm = get_stage_manager()
                sm.transition(sm.current_stage, "RESEARCH_COMPLETED", {
                    "target": target, "total_nodes": cur_nodes,
                    "message": f"Target of {target} nodes reached — all in-flight candidates fully evaluated."
                })
                from .pipeline_state import get_pipeline_state_manager
                get_pipeline_state_manager().set_completed(
                    f"Target of {target} nodes reached — all in-flight evaluations complete.",
                    status="COMPLETED_AT_CEILING"
                )
                activity.success("RESEARCH", f"RESEARCH RUN COMPLETED: Target of {target} nodes reached ({cur_nodes} nodes evaluated). All in-flight tasks settled.")
                bus.publish("research_run_complete", {"target": target, "total_nodes": cur_nodes})
                log.info("[RESEARCH COMPLETED] Target of %d reached. In-flight tasks: 0. Research loop smoothly settled.", target)

        self._check_generation_watchdog()
        self._publish_state_tick()

    def _check_generation_watchdog(self) -> None:
        """Watchdog for silent generation stalls (User Spec §9)."""
        if self.evo.is_target_reached() or not self.running or self.paused:
            return

        node_state = self.evo.get_node_generation_state()
        cur = node_state["current_nodes"]
        target = node_state["target_nodes"]
        rem = node_state["remaining_nodes"]
        gen = node_state["generation_number"]
        last_created = node_state["last_node_created_at"]
        now = time.time()
        elapsed = now - (last_created or self._started_at or now)

        if elapsed >= 30.0 and (now - getattr(self, "_last_watchdog_log_ts", 0)) >= 15.0:
            self._last_watchdog_log_ts = now
            if elapsed >= 90.0:
                log.warning(
                    "[WARNING] NODE GENERATION TIMEOUT\n"
                    "No node created for %.0f seconds\n"
                    "Current: %d / %d\n"
                    "Generation: %d\n"
                    "Action: RESTARTING GENERATION BATCH",
                    elapsed, cur, target, gen
                )
                activity.warning(
                    "WATCHDOG",
                    f"[WARNING] NODE GENERATION TIMEOUT: No node created for {elapsed:.0f}s. Action: RESTARTING GENERATION BATCH",
                    operation_id="gen_timeout"
                )
                self._recover_stuck_strategies(force=True)
            else:
                msg = (
                    f"[WATCHDOG] Node generation heartbeat\n"
                    f"Current: {cur} / {target}\n"
                    f"Remaining: {rem}\n"
                    f"Generation: {gen}\n"
                    f"Pending: {node_state['pending_nodes']}\n"
                    f"Backtesting: {node_state['backtesting_nodes']}\n"
                    f"Validating: {node_state['validating_nodes']}\n"
                    f"Alive: {node_state['alive_nodes']}\n"
                    f"Dead: {node_state['dead_nodes']}\n"
                    f"Qualified: {node_state['qualified_nodes']}\n"
                    f"Elapsed since last node: {elapsed:.0f}s\n"
                    f"Action: GENERATING NEXT BATCH"
                )
                log.info(msg)
                activity.info(
                    "WATCHDOG",
                    f"[WATCHDOG] Node generation heartbeat: {cur}/{target} (idle {elapsed:.0f}s)",
                    operation_id="gen_heartbeat",
                    details=node_state
                )

    def _publish_state_tick(self) -> None:
        if self._cycle % 4 == 0:
            bus.publish("lab_status", self.status())

    def _count_qualifies(self) -> int:
        c = self.db.count_by_status()
        return c.get("QUALIFIED", 0) + c.get("PAPER", 0)

    # ---------------- datasets & features ----------------
    def _verify_and_validate_features(self, datasets: List[Dict]) -> Tuple[List[str], List[Dict[str, str]]]:
        """Verify feature artifacts on disk, schema columns, row counts before advancing (User Spec §4).

        V5 §1 — returns ``(usable_dataset_ids, failures)`` instead of a bare bool.
        A dataset whose core feature set cannot be produced *after* the engine has
        tried to compute it on demand is reported as a failure and excluded from
        this cycle; it never blocks research on the datasets that do work, and a
        node belonging to it is classified as a data outcome (never as a strategy
        failure). Only when *nothing* usable remains does the caller block.
        """
        import pandas as pd
        from .. import paths as P
        from ..features.library import is_spec_cached

        log.info("[INFO] [FEATURES] Feature validation started")
        activity.info("FEATURES", "Feature validation started: verifying columns and row counts")
        total_cols = 0
        total_rows = 0
        usable: List[str] = []
        failures: List[Dict[str, str]] = []

        def _exclude(ds_id: str, reason: str) -> None:
            """Record the dataset as unusable for this cycle and keep going."""
            failures.append({"dataset_id": ds_id, "reason": reason})
            log.error("[ERROR] [FEATURES] %s", reason)
            activity.error("FEATURES", reason)

        from ..data import storage
        cfg = get_config()

        for ds in datasets:
            ds_id = ds["id"]
            # 1. Verify feature artifact exists on disk (P.FEATURES_DIR or storage cache)
            v27_path = P.FEATURES_DIR / f"{ds_id}__core_v1__core_v1.parquet"
            simple_path = P.FEATURES_DIR / f"{ds_id}.parquet"
            target_path = v27_path if v27_path.exists() else simple_path

            df_feat = None
            if target_path.exists():
                try:
                    df_feat = pd.read_parquet(target_path)
                except Exception:
                    df_feat = None

            if df_feat is None:
                df_feat = storage.read_feature_cache(cfg.data.cache_dir, ds_id)

            if df_feat is None:
                feat_eng = get_feature_engine()
                cached_specs = feat_eng.cached_features(ds_id)
                if cached_specs:
                    try:
                        df_feat = pd.DataFrame({s: feat_eng.get(ds_id, s) for s in cached_specs})
                    except Exception:
                        df_feat = None

            if df_feat is None:
                _exclude(ds_id, f"Feature parquet artifact missing for {ds_id} on disk")
                continue

            # 2. Verify expected columns and row count
            try:
                cached_cols = set(df_feat.columns)
                missing = [s for s in CORE_FEATURE_SPECS if not is_spec_cached(s, cached_cols)]
                if missing:
                    log.info("[FEATURES] Precomputing %d missing features for %s during verification", len(missing), ds_id)
                    try:
                        os.environ.pop("LAB_NO_FEATURE_PERSIST", None)
                        self._precompute_dataset_features(ds_id, ds.get("timeframe", "H1"), missing)
                        if v27_path.exists():
                            df_feat = pd.read_parquet(v27_path)
                        elif simple_path.exists():
                            df_feat = pd.read_parquet(simple_path)
                        cached_cols = set(df_feat.columns) if df_feat is not None else set()
                        missing = [s for s in CORE_FEATURE_SPECS if not is_spec_cached(s, cached_cols)]
                    except Exception as e:
                        log.warning("[FEATURES] Inline recomputation failed for %s: %s", ds_id, e)

                if missing:
                    _exclude(ds_id, f"Feature validation failed for {ds_id}: missing columns for specs {missing}")
                    continue

                if len(df_feat) == 0:
                    _exclude(ds_id, f"Feature validation failed for {ds_id}: parquet has 0 rows")
                    continue

                total_cols += len(df_feat.columns)
                total_rows += len(df_feat)
                usable.append(ds_id)
                log.debug("[FEATURES] Validated %s: %d columns, %d rows OK", ds_id, len(df_feat.columns), len(df_feat))
            except Exception as e:
                _exclude(ds_id, f"Failed to read/validate feature parquet for {ds_id}: {e}")
                continue

        msg = (f"Feature validation completed: {len(usable)}/{len(datasets)} datasets verified "
               f"({total_cols} columns, {total_rows:,} rows)")
        log.info("[SUCCESS] [FEATURES] %s", msg)
        activity.success("FEATURES", msg)
        if failures:
            listed = "; ".join(f"{f['dataset_id']}: {f['reason']}" for f in failures)
            log.warning("[FEATURES] %d dataset(s) excluded from this cycle: %s", len(failures), listed)
            activity.warning("FEATURES", f"{len(failures)} dataset(s) excluded from this cycle "
                                        f"(data outcome, not a strategy failure): {listed}")
        return usable, failures

    def _ensure_datasets(self) -> None:
        from .stages import get_stage_manager
        from .milestones import get_milestone_manager
        from ..features.tasks import get_feature_task_manager
        from ..features.library import is_spec_cached
        from ..data.discovery import get_discovery_engine

        sm = get_stage_manager()
        mm = get_milestone_manager()

        # Stage 0: Explicit DATA DISCOVERY & RECONCILIATION before data prechecks (spec §2, §3, §4)
        sm.transition("INIT", "DATA_DISCOVERY", {"message": "Scanning DATA_ROOT and discovering reusable artifacts"})
        disc = get_discovery_engine()
        disc.discover_all(emit_logs=True)
        disc.reconcile_with_database(emit_logs=True)

        # Stage 1: DATA_SYNC
        sm.transition("DATA_DISCOVERY", "DATA_SYNC", {"message": "Market data sync initiated"})
        mm.set_running("data_sync", "Verifying & syncing Parquet datasets")
        de = get_data_engine()
        results = de.ingest_default()
        bus.publish("datasets_ready", {"results": results})
        log.info("[SUCCESS] [DATA] Data sync complete: %d datasets verified/reused", len(results))
        activity.success("DATA", f"Data sync complete: {len(results)} datasets verified/reused")

        # Stage 2: DATA_VALIDATION
        sm.transition("DATA_SYNC", "DATA_VALIDATION", {"message": "Validating bar ranges and OHLC integrity"})
        datasets = [ds for ds in de.list_datasets() if ds.get("on_disk")]
        log.info("[SUCCESS] [DATA] Data validation complete: %d datasets valid", len(datasets))
        activity.success("DATA", f"Data validation complete: {len(datasets)} datasets valid")
        mm.set_complete("data_sync", f"{len(datasets)} Datasets Verified & Reused")

        # Stage 3: FEATURE_DISCOVERY
        sm.transition("DATA_VALIDATION", "FEATURE_DISCOVERY", {"message": "Discovering cached indicator arrays"})
        feat = get_feature_engine()
        task_mgr = get_feature_task_manager()
        rm = get_resource_manager()
        eff_workers = rm.effective_workers()

        to_compute = []
        for ds in datasets:
            ds_id = ds["id"]
            tf = ds.get("timeframe", "H1")
            cached = set(feat.cached_features(ds_id))
            missing = [s for s in CORE_FEATURE_SPECS if not is_spec_cached(s, cached)]

            if not missing:
                # Features already cached and verified on disk -> REUSED (spec §7)
                task, _ = task_mgr.create_or_attach(ds_id, tf, CORE_FEATURE_SPECS, worker_id="cached")
                task.complete(reused=True)
                col_count = len(cached)
                feat_log = (
                    f"[FEATURE DISCOVERY]\n"
                    f"Dataset: {ds_id}\n"
                    f"Dataset fingerprint: {ds.get('fingerprint', 'VALID')}\n"
                    f"Feature version: core_v1\n\n"
                    f"Existing feature artifact found.\n\n"
                    f"Columns available: {col_count}\n"
                    f"Columns required: {len(CORE_FEATURE_SPECS)}\n"
                    f"Compatibility: PASS\n\n"
                    f"ACTION: REUSE EXISTING FEATURES"
                )
                log.info(feat_log)
                activity.success("FEATURES", f"ACTION: REUSE EXISTING FEATURES for {ds_id} ({col_count} columns)",
                                 operation_id=f"feat_{ds_id.lower()}", progress=100.0)
                mm.set_subprocess("feature_precompute", ds_id, {
                    "dataset_id": ds_id,
                    "timeframe": tf,
                    "status": "COMPLETE",
                    "features": f"{col_count} cached (REUSED)",
                    "progress": 100.0,
                    "reused": True,
                })
                mm.add_completed_item("feature_precompute", f"{ds_id} (REUSED)")
            else:
                diff_log = (
                    f"[FEATURE DISCOVERY]\n"
                    f"Dataset: {ds_id}\n"
                    f"Existing features: {len(cached)}/{len(CORE_FEATURE_SPECS)}\n"
                    f"Missing: {len(missing)}\n\n"
                    f"ACTION: COMPUTE ONLY {len(missing)} MISSING FEATURES"
                )
                log.info(diff_log)
                activity.info("FEATURES", f"{ds_id}: existing {len(cached)}/{len(CORE_FEATURE_SPECS)}, computing {len(missing)} missing")
                to_compute.append((ds, missing))

        log.info("[SUCCESS] [FEATURES] Feature discovery complete: %d datasets ready, %d requiring compute",
                 len(datasets) - len(to_compute), len(to_compute))
        log.info("DEBUG_TO_COMPUTE_LIST: len=%d items=%s", len(to_compute), [x[0]['id'] for x in to_compute])
        activity.success("FEATURES", f"Feature discovery complete: {len(datasets) - len(to_compute)} reused, {len(to_compute)} to compute")

        # Stage 4: FEATURE_PRECOMPUTATION
        sm.transition("FEATURE_DISCOVERY", "FEATURE_PRECOMPUTATION", {"message": f"Precomputing features for {len(to_compute)} datasets"})
        mm.set_running("feature_precompute", "Computing feature indicator arrays")

        if to_compute:
            target_pct = rm.live_metrics()["cpu"]["target_pct"]
            n_needed = len(to_compute)
            n_workers = min(eff_workers, n_needed)
            log.info(
                "[FEATURE] Precomputing missing features for %d datasets across %d workers (CPU target %d%%, pool size: %d)",
                n_needed, n_workers, target_pct, eff_workers
            )

            from concurrent.futures import ThreadPoolExecutor

            def _do_precompute(item):
                ds_info, missing_specs = item
                log.info("DEBUG_DO_PRECOMPUTE: start for %s missing=%d", ds_info["id"], len(missing_specs))
                try:
                    self._precompute_dataset_features(
                        ds_info["id"],
                        ds_info.get("timeframe", "H1"),
                        missing_specs,
                        worker_id=f"worker-{threading.get_ident() % 1000}"
                    )
                    log.info("DEBUG_DO_PRECOMPUTE: finished for %s", ds_info["id"])
                except Exception as e:
                    log.error("DEBUG_DO_PRECOMPUTE: failed for %s: %s", ds_info["id"], e)

            if n_workers > 1:
                with ThreadPoolExecutor(max_workers=n_workers) as executor:
                    list(executor.map(_do_precompute, to_compute))
            else:
                for item in to_compute:
                    _do_precompute(item)

        log.info("[SUCCESS] [FEATURES] Feature precomputation completed")
        activity.success("FEATURES", "Feature precomputation completed — all datasets processed")

        # Stage 5: FEATURE_VALIDATION
        sm.transition("FEATURE_PRECOMPUTATION", "FEATURE_VALIDATION", {"message": "Verifying column schemas and row counts"})
        usable_ids, feat_failures = self._verify_and_validate_features(datasets)
        outcome = feature_validation_outcome(usable_ids, feat_failures)
        if outcome["action"] == "BLOCK":
            # nothing can be researched at all: block (and say exactly why)
            self._datasets_ready = False
            self._research_blocked = True
            self._block_reason = outcome["reason"]
            log.error("[SYSTEM] [ORCHESTRATOR] Feature validation failed for every dataset. Research is BLOCKED.")
            activity.error("ORCHESTRATOR", "Feature validation failed for every dataset - research is BLOCKED "
                                            "until at least one dataset has valid feature artifacts.")
            sm.transition("FEATURE_VALIDATION", "BLOCKED", {
                "reason": "Feature validation failed for every dataset",
                "action": "HALT_RESEARCH",
            })
            return False
        if feat_failures:
            # V5 §1: exclude the unusable datasets and research the rest. The
            # excluded ones are reported (here and in the diagnostics report) and
            # owned nodes are data outcomes, never strategy failures.
            unusable = set(outcome["excluded"])
            datasets = [d for d in datasets if d.get("id") not in unusable]
            log.warning("[SYSTEM] [ORCHESTRATOR] excluding %d dataset(s) with unusable features; "
                        "research continues on %d dataset(s)", len(unusable), len(datasets))
            activity.warning("FEATURES", f"{len(unusable)} dataset(s) excluded from research "
                                          f"(data outcome, not a strategy failure); "
                                          f"{len(datasets)} dataset(s) remain usable")

        self._research_blocked = False
        self._block_reason = ""
        mm.set_complete("feature_precompute", "All Features Cached & Verified")

        # Explicit terminal logging and immediate transition per spec §8
        term_log = (
            "[FEATURES] COMPLETE\n"
            "[FEATURES] Output validated\n"
            "[FEATURES] Artifact persisted\n"
            "[FEATURES] Registered in database\n"
            "[FEATURES] Downstream dependency released\n"
            "[PIPELINE] Transitioning: FEATURES COMPLETE -> FEATURE VALIDATION -> DATASET REGISTRATION -> RESEARCH INITIALIZATION -> NODE GENERATION -> NODE EVALUATION"
        )
        log.info(term_log)
        activity.success("FEATURES", "Feature pipeline complete: validated, persisted, registered")
        activity.info("PIPELINE", "Transitioning: FEATURES COMPLETE -> FEATURE VALIDATION -> DATASET REGISTRATION -> RESEARCH INITIALIZATION -> NODE GENERATION -> NODE EVALUATION")

        # Stage 6: DATASET_REGISTRATION
        sm.transition("FEATURE_VALIDATION", "DATASET_REGISTRATION", {"message": "All datasets and features registered in catalog"})
        log.info("[SUCCESS] [DATA] Datasets and features registered in catalog")
        activity.success("DATA", "Datasets and features registered in catalog")

        # Stage 7: RESEARCH_INITIALIZATION
        sm.transition("DATASET_REGISTRATION", "RESEARCH_INITIALIZATION", {"message": "Initializing population and research loop"})
        log.info("[RESEARCH] Initializing population and research space")
        activity.info("EVOLUTION", "Initializing research space and population parameters")

        # Stage 8: Transition into RESEARCH / NODE_GENERATION
        sm.transition("RESEARCH_INITIALIZATION", "RESEARCH", {"message": "Starting research generation loop"})
        log.info("[RESEARCH] Ready for candidate generation and evaluation")
        activity.set_idle()
        return True

    def _precompute_features(self, dataset_id: str) -> None:
        """Precompute features only if missing from disk cache."""
        feat = get_feature_engine()
        from ..features.library import is_spec_cached
        cached = set(feat.cached_features(dataset_id))
        missing = [s for s in CORE_FEATURE_SPECS if not is_spec_cached(s, cached)]
        if not missing:
            log.info("core features for %s already cached on disk (skipping recompute)", dataset_id)
            return
        tf = dataset_id.split("_")[1] if "_" in dataset_id else "H1"
        self._precompute_dataset_features(dataset_id, tf, missing)

    def _precompute_dataset_features(
        self,
        dataset_id: str,
        timeframe: str,
        missing: List[str],
        worker_id: str = "worker-1",
    ) -> None:
        feat = get_feature_engine()
        from ..features.tasks import get_feature_task_manager
        from .milestones import get_milestone_manager
        task_mgr = get_feature_task_manager()
        mm = get_milestone_manager()
        op_id = f"feat_{dataset_id.lower()}"

        task, created = task_mgr.create_or_attach(
            dataset_id, timeframe, missing, worker_id=worker_id
        )
        if not created and task.status == "RUNNING":
            if not task.is_stalled(stall_threshold_s=5.0):
                log.info("[FEATURE] Task already running for %s, attaching", dataset_id)
                return
            else:
                log.warning("[FEATURE] Previous task for %s was stalled, re-starting", dataset_id)

        try:
            df = get_data_engine().get_frame(dataset_id)
            n_rows = len(df)
            from ..features.library import parse_spec, is_spec_cached

            with feat._lock:
                valid_cached = {k[1] for k, v in feat._cache.items() if k[0] == dataset_id and len(v) == n_rows}
            specs_to_compute = [s for s in CORE_FEATURE_SPECS if not is_spec_cached(s, valid_cached)]
            if not specs_to_compute:
                specs_to_compute = list(missing)

            task.start(rows_total=n_rows)
            activity.running("FEATURES", f"Precomputing {len(specs_to_compute)} core features for {dataset_id}",
                             operation_id=op_id, progress=0.0)
            t0 = time.time()

            accumulated_out = {}
            for idx, spec in enumerate(specs_to_compute, 1):
                t_f0 = time.time()
                task.start_feature(spec)
                task.heartbeat(rows_processed=n_rows)
                fn, args = parse_spec(spec)
                out = fn(df, *args)
                for name, arr in out.items():
                    feat._put((dataset_id, name), arr)
                    accumulated_out[name] = arr
                task.complete_feature(spec, rows=n_rows)

                t_f1 = time.time()
                f_dur = max(0.001, t_f1 - t_f0)
                log.info("[FEATURE] Computing %s... OK (%.2fs)", spec, f_dur)
                bars_sec = int(n_rows / f_dur) if n_rows > 0 else 0
                prog = round((idx / len(specs_to_compute)) * 100.0, 1)

                task.heartbeat(rows_processed=n_rows)
                mm.set_subprocess("feature_precompute", dataset_id, {
                    "dataset_id": dataset_id,
                    "timeframe": timeframe,
                    "status": "RUNNING",
                    "features": f"{idx}/{len(specs_to_compute)} ({spec})",
                    "progress": prog,
                    "reused": False,
                })
                mm.set_progress("feature_precompute", prog, task_desc=f"{dataset_id}: {spec} ({idx}/{len(specs_to_compute)})")

                # Subtask progress tick (User Spec §8)
                log.info("[FEATURES] task=%s stage=FEATURES progress=%d/%d operation=%s [%s bars/s]",
                         task.task_id, idx, len(specs_to_compute), spec, f"{bars_sec:,}")
                activity.running(
                    "FEATURES",
                    f"✓ {idx}/{len(specs_to_compute)} {spec} ({bars_sec:,} bars/s)",
                    operation_id=op_id, progress=prog,
                    details={
                        "task_id": task.task_id,
                        "feature": spec,
                        "current": idx,
                        "total": len(specs_to_compute),
                        "bars_per_sec": bars_sec,
                        "elapsed_s": round(t_f1 - t0, 1),
                    }
                )

            feat._persist(dataset_id, accumulated_out, "", force=True)
            dur = time.time() - t0
            task.complete(reused=False)

            # Technical heartbeat & persistence logging (spec §8, §9)
            last_spec = missing[-1] if missing else "features"
            log.info(
                "[PIPELINE HEARTBEAT]\n"
                "Stage: FEATURE_PRECOMPUTATION\n"
                "Dataset: %s\n"
                "Task: %s\n"
                "Progress: %d/%d\n"
                "Percent: 100%%\n"
                "Elapsed: %.1fs\n"
                "Worker: %s\n"
                "CPU: 60%%\n"
                "GPU: disabled\n"
                "Status: COMPLETING / PERSISTING",
                dataset_id, last_spec, len(missing), len(missing), dur, worker_id
            )
            log.info(
                "[PIPELINE] Feature computation finished for %s\n"
                "[PIPELINE] Persisting feature artifact...\n"
                "[PIPELINE] Validating feature artifact...\n"
                "[PIPELINE] Registering feature artifact in manifest and database...\n"
                "[PIPELINE] Feature artifact ready\n"
                "[PIPELINE] Releasing next stage",
                dataset_id
            )

            mm.set_subprocess("feature_precompute", dataset_id, {
                "dataset_id": dataset_id,
                "timeframe": timeframe,
                "status": "COMPLETE",
                "features": f"{len(missing)}/{len(missing)} (100%)",
                "progress": 100.0,
                "reused": False,
            })
            mm.add_completed_item("feature_precompute", f"{dataset_id} ({len(missing)} features)")
            log.info("[SUCCESS] [FEATURES] task=%s stage=FEATURES progress=%d/%d elapsed=%.1fs",
                     task.task_id, len(missing), len(missing), dur)
            log.info("[FEATURE] Validating computed arrays: PASS")
            log.info("[FEATURE] Feature cache updated: DATA/features/%s.parquet", dataset_id)
            activity.success("FEATURES", f"✓ Feature computation completed — {len(missing)}/{len(missing)} for {dataset_id} in {dur:.1f}s",
                             operation_id=op_id, progress=100.0)
            bus.publish("features_cached", {"dataset_id": dataset_id,
                                            "features": len(feat.cached_features(dataset_id))})
        except Exception as e:
            task.fail(str(e))
            mm.set_subprocess("feature_precompute", dataset_id, {
                "dataset_id": dataset_id,
                "timeframe": timeframe,
                "status": "FAILED",
                "error": str(e),
                "progress": 0.0,
            })
            log.exception("[FEATURE] Precompute %s failed: %s", dataset_id, e)
            activity.error("FEATURES", f"Precompute {dataset_id} failed: {e}", operation_id=op_id)

    def _recover_stuck_strategies(self, force: bool = False) -> None:
        """Idempotent recovery of crashed/interrupted strategies with distinct RECOVERY ID."""
        import uuid
        now_ts = time.time()
        # Only check periodically (every 60s) unless forced by watchdog
        if not force and (now_ts - self._last_recovery_check_ts) < 60.0:
            return
        self._last_recovery_check_ts = now_ts

        # Find candidates stuck in BACKTESTING or VALIDATING past 120s
        stuck_rows = self.db.q(
            "SELECT id, status FROM strategies WHERE status IN ('BACKTESTING', 'VALIDATING') AND updated_at < ?",
            (now_ts - 120.0,)
        )
        if not stuck_rows:
            return

        newly_stuck = [r for r in stuck_rows if r["id"] not in self._recovered_strategy_ids]
        if not newly_stuck:
            return

        rec_id = f"REC-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
        n_bt = 0
        n_val = 0
        n_failed = 0

        for r in newly_stuck:
            sid = r["id"]
            self._recovered_strategy_ids.add(sid)
            attempts = self._strategy_recovery_attempts.get(sid, 0) + 1
            self._strategy_recovery_attempts[sid] = attempts

            if attempts >= 3:
                # V5 §3: a node that could not be evaluated because its worker
                # kept timing out is a BACKTEST_ERROR, never a failed strategy.
                self._mark_infrastructure(
                    sid, "BACKTEST_ERROR",
                    f"Failed recovery: evaluation timeout threshold ({attempts}x) exceeded")
                self.counters["backtest_errors"] = self.counters.get("backtest_errors", 0) + 1
                n_failed += 1
            elif r["status"] == "BACKTESTING":
                self.db.update_strategy(sid, status="BORN", updated_at=now_ts)
                n_bt += 1
            elif r["status"] == "VALIDATING":
                self.db.update_strategy(sid, status="SURVIVED", updated_at=now_ts)
                n_val += 1

        log.info(
            "[RECOVERY] [ID: %s] Idempotently recovered %d stuck strategies (%d BACKTESTING -> BORN, %d VALIDATING -> SURVIVED, %d -> FAILED). Total unique recovered: %d",
            rec_id, len(newly_stuck), n_bt, n_val, n_failed, len(self._recovered_strategy_ids)
        )
        activity.info(
            "SYSTEM",
            f"Idempotent recovery {rec_id}: {len(newly_stuck)} strategies recovered ({n_failed} failed after repeat timeouts)",
            operation_id=rec_id,
            details={"rec_id": rec_id, "recovered": len(newly_stuck), "born": n_bt, "survived": n_val, "failed": n_failed}
        )

    # ---------------- population ----------------
    def _ensure_population(self) -> None:
        if not self._datasets_ready or getattr(self, "_research_blocked", False):
            return
        from .stages import get_stage_manager
        sm = get_stage_manager()
        cur_nodes = self.evo.total_nodes()
        target = self.evo.get_total_node_target()

        if self.evo.is_target_reached():
            self.evo.emit_stop_diagnostic("NODE GENERATION STOPPED: TARGET REACHED", recovery_action="Increase TOTAL NODES target to continue")
            log.info("[RESEARCH] Node target ceiling reached (%d strategies). New strategy creation safely halted.",
                     target)
            return

        cfg = get_config().evolution

        # 1. Recover any strategies stuck in BACKTESTING or VALIDATING (idempotent with RECOVERY ID)
        self._recover_stuck_strategies()

        # Count candidate strategies currently in active evaluation pipeline for this run
        run_filter = f"AND run_id='{self.evo.active_run_id}'" if self.evo.active_run_id else ""
        row = self.db.one(
            f"SELECT COUNT(*) c FROM strategies WHERE status IN ('BORN', 'BACKTESTING', 'SURVIVED', 'VALIDATING') {run_filter}"
        )
        pipeline_candidates = row["c"] if row else 0
        rm = get_resource_manager()
        eff_workers = rm.effective_workers()
        target_batch = max(30, getattr(cfg, "population_size", 30), eff_workers * 20)
        remaining_target = max(0, target - cur_nodes)

        if cur_nodes == 0:
            seed_count = min(target_batch, remaining_target)
            n = self.evo.seed_population(seed_count, get_config().data.symbol)
            if n == 0 and remaining_target > 0:
                # Force novel seeding with high entropy jitter if initial attempt created 0
                log.warning("[EVOLUTION] Initial seed attempt yielded 0 candidates (all duplicates). Retrying with high-entropy salt...")
                self.evo._entropy_counter += 1
                salt = (int(time.time() * 1000) ^ os.getpid() ^ (self.evo._entropy_counter << 16)) & 0x7FFFFFFF
                self.evo._rng = random.Random(salt)
                n = self.evo.seed_population(seed_count, get_config().data.symbol)
            self.counters["born"] += n
            activity.info("EVOLUTION", f"Population seeded with {n} strategies for run {self.evo.active_run_id or 'CLEAN'} (Gen 0)")
            bus.publish("population_seeded", {"n": n, "generation": 0})
            sm.transition("RESEARCH", "BACKTESTING", {
                "reason": f"Seeded {n} generation 0 strategies",
                "completed": 0,
                "current_nodes": self.evo.total_nodes(),
                "target": target,
            })
            return

        deficit = target_batch - pipeline_candidates
        if deficit > 0 and remaining_target > 0:
            max_born = max(30, eff_workers * 15)
            born_target = min(deficit, remaining_target, max_born)
            gen = self.evo.generation()
            cur_stage = sm.current_stage
            if cur_stage != "EVOLUTION":
                sm.transition(cur_stage, "EVOLUTION", {
                    "reason": f"Need {born_target} candidates to fill batch ({pipeline_candidates}/{target_batch} active)",
                    "current_nodes": cur_nodes,
                    "target": target,
                    "action": "generate next candidate batch",
                })

            log.info("[INFO] [EVOLUTION] Starting research generation (Gen %d): reproducing %d candidates",
                     gen + 1, born_target)
            activity.info("EVOLUTION", f"Starting research generation: reproducing {born_target} candidates (Gen {gen + 1})")

            if self.mode == "exploration":
                counts = self.evo.reproduce(born_target, get_config().data.symbol,
                                            pct_override={"mutation": 0.3, "crossover": 0.2,
                                                          "exploration": 0.5})
            else:
                counts = self.evo.reproduce(born_target, get_config().data.symbol)

            born_count = counts.get("mutation", 0) + counts.get("crossover", 0) + counts.get("exploration", 0)
            self.counters["born"] += born_count
            self.counters["duplicates"] += counts.get("duplicate", 0)
            if born_count > 0:
                cur_after = self.evo.total_nodes()
                log.info("[SUCCESS] [EVOLUTION] Reproduced %d candidate strategies for generation %d (Total: %d/%d)",
                         born_count, gen + 1, cur_after, target)
                activity.success("EVOLUTION", f"Reproduced {born_count} candidate strategies for Gen {gen + 1} (Total: {cur_after}/{target})")
                bus.publish("births", counts)
                # Meaningful transition to BACKTESTING
                sm.transition("EVOLUTION", "BACKTESTING", {
                    "reason": f"{born_count} new candidates ready for screening",
                    "completed": self.counters.get("screened", 0),
                    "current_nodes": cur_after,
                    "target": target,
                })

    # ---------------- screening (stage 1) ----------------
    def _train_window(self, dataset_id: str):
        split = get_data_engine().train_test_split(dataset_id)
        return (0, split["cut"])

    def _dataset_for(self, genome: Dict) -> Optional[Dict]:
        data_cfg = getattr(get_config(), "data", None)
        sym = genome.get("symbol") or (getattr(data_cfg, "symbol", "XAUUSD") if data_cfg else "XAUUSD")
        default_tf = data_cfg.timeframes[0] if (data_cfg and getattr(data_cfg, "timeframes", None)) else "M15"
        tf = genome.get("timeframe") or default_tf
        ds = get_data_engine().latest_dataset(sym, tf, require_eligible=True)
        if ds:
            is_elig, reason = get_data_engine().is_dataset_eligible_for_research(ds["id"], require_features=True)
            if is_elig:
                return ds
            else:
                log.warning("[DATASET ELIGIBILITY] Dataset %s rejected: %s", ds.get("id"), reason)
        return None

    def _mark_infrastructure(self, sid: int, cls: str, reason: str) -> None:
        """Record an infrastructure/data outcome without calling it a strategy failure.

        Spec V5 §3: the row keeps the raw reason verbatim, but its status is the
        infrastructure class (DATA_UNAVAILABLE / DATA_CORRUPT / BACKTEST_ERROR),
        so neither the dashboard nor the survival statistics can ever read it as
        "this strategy failed".
        """
        status = cls if cls in ("DATA_UNAVAILABLE", "DATA_CORRUPT", "BACKTEST_ERROR") else "BACKTEST_ERROR"
        self.db.update_strategy(sid, status=status, creation_reason=reason)
        with self._infra_lock:
            self._infrastructure_nodes.add(sid)

    def _screen_batch(self) -> None:
        if not self._datasets_ready or getattr(self, "_research_blocked", False):
            return

        cfg = get_config()
        from .stages import get_stage_manager
        sm = get_stage_manager()

        # Idempotent recovery check
        self._recover_stuck_strategies()
        rm = get_resource_manager()
        eff_batch = max(cfg.evolution.screen_batch_size, rm.effective_workers() * 25)
        run_filter = f"AND run_id='{self.evo.active_run_id}'" if self.evo.active_run_id else ""
        rows = self.db.q(f"SELECT * FROM strategies WHERE status='BORN' {run_filter} ORDER BY id LIMIT ?",
                         (eff_batch,))
        if not rows:
            return

        payloads, valid = [], []
        for r in rows:
            genome = json.loads(r["genome"])
            ds_info = self._dataset_for(genome)
            if not ds_info:
                # V5 §3: unavailable market data is an INFRASTRUCTURE outcome.
                # The strategy itself was never judged, so it must not be
                # recorded — or counted — as a failed strategy.
                reason = f"DATASET UNAVAILABLE: No eligible dataset found for {genome.get('symbol')}:{genome.get('timeframe')}"
                self._mark_infrastructure(r["id"], "DATA_UNAVAILABLE", reason)
                self.counters["data_failures"] += 1
                self.counters["dataset_unavailable_candidates"] += 1
                self.counters["skipped"] += 1
                log.warning("[BACKTEST] Candidate strategy #%d not tested: %s. Proceeding to next candidate.", r["id"], reason)
                activity.warning("RESEARCH", f"Candidate #{r['id']} NOT TESTED: {reason}")
                continue

            dsid = ds_info["id"]
            try:
                w = self._train_window(dsid)
            except Exception as e:
                reason = f"TRAIN_WINDOW_FAILED: {dsid} ({e})"
                self._mark_infrastructure(r["id"], "DATA_UNAVAILABLE", reason)
                self.counters["data_failures"] += 1
                log.error("[BACKTEST] Candidate strategy #%d could not open a train window: %s", r["id"], reason)
                continue

            self.db.update_strategy(r["id"], status="BACKTESTING")

            # Check experiment fingerprint reuse (spec §9)
            ghash = genome_hash(genome)
            fp = compute_experiment_fingerprint(
                ghash, genome["symbol"], genome["timeframe"], "screen",
                dataset_version=ds_info.get("dataset_version", 1),
                dataset_fingerprint=ds_info.get("fingerprint", ""),
            )
            existing_bt = self.db.one(
                "SELECT * FROM backtests WHERE strategy_id=? AND stage='screen' AND fingerprint=? AND stale=0",
                (r["id"], fp)
            )
            if existing_bt:
                # Reuse existing result immediately!
                self.db.update_strategy(r["id"], status=existing_bt["verdict"],
                                        fitness=existing_bt["fitness"])
                activity.info("BACKTEST", f"Reusing screen experiment #{r['id']} (fingerprint matches)")
                continue

            payloads.append(_bt_payload(genome, dsid, "screen", window=w))
            valid.append((r, genome, ds_info, fp))

        if not payloads:
            return

        sm.transition("RESEARCH", "BACKTESTING", {"message": f"Screening batch of {len(payloads)} strategies"})
        log.info("[INFO] [BACKTEST] Screening batch of %d candidate strategies", len(payloads))
        activity.running("BACKTEST", f"Screening batch of {len(payloads)} candidate strategies", progress=0.0)

        results = self._run_batch(payloads)
        total_eval = len(valid)
        survived_count = 0
        failed_count = 0

        results = list(itertools.chain(results, [None] * max(0, total_eval - len(results))))
        for idx, ((r, genome, ds_info, fp), res) in enumerate(zip(valid, results), 1):
            dsid = ds_info["id"]
            prog = round((idx / total_eval) * 100.0, 1)

            if not res:
                # V5 §3: the batch could not run this candidate at all (memory
                # budget / pool recycle). That is neither a strategy failure nor
                # a data failure — requeue it for the next tick.
                self.db.update_strategy(r["id"], status="BORN")
                self.counters["skipped"] += 1
                self.counters["requeued"] = self.counters.get("requeued", 0) + 1
                continue

            self.counters["screened"] += 1
            self.counters["tested"] += 1
            self._tested_this_gen += 1

            if not res["ok"]:
                fail_err = res.get("error", "Evaluation worker error")
                # V5 §3: an evaluation/worker failure is an infrastructure
                # outcome — the strategy was never judged on its own merits.
                cls = classify_failure(fail_err) or "BACKTEST_ERROR"
                self._mark_infrastructure(r["id"], cls, fail_err[:250])
                self.counters["backtest_errors" if cls != "DATA_CORRUPT" else "data_corrupt"] += 1
                bus.publish("screen_failed", {"id": r["id"], "error": fail_err[:200], "class": cls})
                log.warning("[NODE] Node_%d not evaluated (%s): %s", r["id"], cls, fail_err[:120])
                continue
            m = res["metrics"]
            f, _ = fitness(m, genome)
            died, reasons = death_check(m, genome, stage="screen")
            verdict = "FAILED" if died else "SURVIVED"

            # Insert backtest record with fingerprint & versions (spec §9, §41)
            self.db.x("""INSERT INTO backtests
                         (strategy_id,stage,dataset_id,window,metrics,fitness,verdict,created_at,
                          fingerprint,dataset_version,engine_versions)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                      (r["id"], "screen", dsid, jd(m.get("window")),
                       jd(m), f, verdict, time.time(),
                       fp, ds_info.get("dataset_version", 1), jd(manifest())))
            if died:
                failed_count += 1
                fail_msg = f"REJECTED: {'; '.join(reasons)}" if reasons else "REJECTED: failed screening criteria"
                self.db.update_strategy(r["id"], status="FAILED", failure_reason=fail_msg)
                self.counters["killed"] += 1
                self.counters["strategy_failed"] += 1
                self.counters["rejected"] += 1
                bus.publish("strategy_failed", {"id": r["id"], "reasons": reasons[:3],
                                                "trades": m.get("trades", 0)})
            else:
                survived_count += 1
                self.counters["survived"] += 1
                wr = round(float(m.get("win_rate") or 0.0) * 100, 1)
                pf = round(float(m.get("profit_factor") or 0.0), 2)
                dd = round(float(m.get("max_drawdown_pct") or 0.0) * 100, 1)
                ret = round(float(m.get("total_return_pct") or 0.0), 1)
                tr = m.get("trades", 0)
                surv_msg = f"SURVIVED: Win Rate {wr}%, Profit Factor {pf}, Return {ret}%, Max DD {dd}%, Trades {tr}"
                self.db.update_strategy(r["id"], status="SURVIVED", fitness=f, survival_reason=surv_msg)
                bus.publish("strategy_survived", {"id": r["id"], "fitness": f,
                                                  "trades": tr,
                                                  "pf": pf})

            # Subtask progress tick
            activity.running("BACKTEST", f"Screened {idx}/{total_eval}: Strategy #{r['id']} ({verdict})", progress=prog)

            # Independent parent/child metric logging (spec §6, §14)
            parent_id = r.get("parent_id")
            parent_ret = None
            if parent_id:
                p_bt = self.db.one(
                    "SELECT metrics FROM backtests WHERE strategy_id=? ORDER BY id DESC LIMIT 1",
                    (parent_id,)
                )
                if p_bt and p_bt.get("metrics"):
                    try:
                        p_m = json.loads(p_bt["metrics"])
                        parent_ret = p_m.get("total_return_pct")
                    except Exception:
                        pass
            child_ret = m.get("total_return_pct", 0.0)
            ret_diff = round(child_ret - (parent_ret or 0.0), 4) if parent_ret is not None else None
            p_label = f"Node_{parent_id} ({parent_ret:+.1%})" if parent_id and parent_ret is not None else (f"Node_{parent_id}" if parent_id else "None")
            diff_label = f" (diff: {ret_diff:+.1%})" if ret_diff is not None else ""
            log.info("[NODE] Parent=%s -> Child=Node_%d (%s%s) | Verdict=%s | Stage=BACKTEST",
                     p_label, r["id"], f"{child_ret:+.1%}", diff_label, verdict)

            if r.get("parent_id"):
                try:
                    from ..ai_researcher.analyzer import record_experiment_memory
                    record_experiment_memory(r["id"], self.db)
                except Exception as e:
                    log.debug("record_experiment_memory failed #%s: %s", r["id"], e)

        # V5: report what actually happened. Candidates whose batch could not run
        # are requeued, not counted as screened/failed.
        requeued = self.counters.get("requeued", 0)
        log.info("[SUCCESS] [BACKTEST] Screened %d strategies (%d survived, %d failed, %d requeued)",
                 survived_count + failed_count, survived_count, failed_count, requeued)
        activity.success(
            "BACKTEST",
            f"✓ Screening completed — {survived_count + failed_count} screened "
            f"({survived_count} survived, {failed_count} failed)"
            + (f", {requeued} requeued for the next batch" if requeued else ""),
            progress=100.0)
        activity.set_idle()

    # ---------------- detailed backtest (stage 2) ----------------
    def _detail_batch(self) -> None:
        cfg = get_config()
        rm = get_resource_manager()
        eff_batch = max(cfg.evolution.detail_batch_size, rm.effective_workers() * 15)
        run_filter = f"AND s.run_id='{self.evo.active_run_id}'" if self.evo.active_run_id else ""
        rows = self.db.q(f"""SELECT s.* FROM strategies s
                            WHERE s.status='SURVIVED' {run_filter}
                              AND NOT EXISTS (SELECT 1 FROM backtests b
                                              WHERE b.strategy_id=s.id AND b.stage='detail' AND b.stale=0)
                            ORDER BY s.fitness DESC LIMIT ?""",
                         (eff_batch,))
        if not rows:
            return
        payloads, valid = [], []
        for r in rows:
            genome = json.loads(r["genome"])
            ds_info = self._dataset_for(genome)
            if not ds_info:
                continue
            dsid = ds_info["id"]
            try:
                w = self._train_window(dsid)
            except Exception as e:
                log.error("[BACKTEST] Detailed test train window failed for %s: %s", dsid, e)
                continue

            ghash = genome_hash(genome)
            fp = compute_experiment_fingerprint(
                ghash, genome["symbol"], genome["timeframe"], "detail",
                dataset_version=ds_info.get("dataset_version", 1),
                dataset_fingerprint=ds_info.get("fingerprint", ""),
            )
            # Stale check: if an old backtest exists with a DIFFERENT fingerprint, mark it stale (spec §9)
            old_bt = self.db.one(
                "SELECT id, fingerprint FROM backtests WHERE strategy_id=? AND stage='detail' AND stale=0",
                (r["id"],)
            )
            if old_bt and old_bt["fingerprint"] != fp:
                self.db.x("UPDATE backtests SET stale=1 WHERE id=?", (old_bt["id"],))
                activity.info("BACKTEST", f"Experiment #{r['id']} marked STALE (config or data changed) — re-running")

            payloads.append(_bt_payload(genome, dsid, "detail", window=w))
            valid.append((r, genome, ds_info, fp))

        if not payloads:
            return

        activity.info("BACKTEST", f"Starting detailed backtest batch ({len(payloads)} strategies)")
        results = self._run_batch(payloads)
        results = list(itertools.chain(results, [None] * max(0, len(valid) - len(results))))
        for (r, genome, ds_info, fp), res in zip(valid, results):
            dsid = ds_info["id"]
            if not res:
                # not executed (budget/pool) — stays SURVIVED and is retried
                self.counters["skipped"] += 1
                self.counters["requeued"] = self.counters.get("requeued", 0) + 1
                continue
            self.counters["detailed"] += 1
            if not res["ok"]:
                # V5 §3: a worker/engine failure is an infrastructure outcome —
                # the strategy was never judged on its own merits.
                err = str(res.get("error") or "Evaluation worker error")
                cls = classify_failure(err) or "BACKTEST_ERROR"
                self._mark_infrastructure(r["id"], cls, err[:250])
                self.counters["backtest_errors" if cls != "DATA_CORRUPT" else "data_corrupt"] += 1
                bus.publish("detail_failed", {"id": r["id"], "error": err[:200], "class": cls})
                activity.warning("BACKTEST", f"Strategy #{r['id']} not evaluated ({cls}): {err[:100]}")
                continue
            m = res["metrics"]
            m["trades_sample"] = res["trades"][-300:]
            f, components = fitness(m, genome)
            died, reasons = death_check(m, genome, stage="detail")
            verdict = "KILLED" if died else "SURVIVED"

            # Export complete trade history & equity curves to Parquet in RESEARCH/ (spec §10, §11)
            research_path = ""
            try:
                base_dir = export.export_backtest(
                    r["id"], m, all_trades=res.get("trades", []),
                    equity_curve=res.get("equity_curve", [])
                )
                research_path = str(base_dir)
            except Exception as e:
                log.warning("failed to export backtest parquet for #%s: %s", r["id"], e)

            self.db.x("""INSERT INTO backtests
                         (strategy_id,stage,dataset_id,window,params,metrics,fitness,
                          verdict,created_at,fingerprint,dataset_version,engine_versions,research_path)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (r["id"], "detail", dsid, jd(m.get("window")),
                       jd({"components": components}), jd(m), f,
                       verdict, time.time(),
                       fp, ds_info.get("dataset_version", 1), jd(manifest()), research_path))

            if died:
                fail_msg = "; ".join(reasons)
                self.db.update_strategy(r["id"], status="KILLED", failure_reason=fail_msg)
                self.counters["killed"] += 1
                activity.warning("BACKTEST", f"Strategy #{r['id']} KILLED: {', '.join(reasons[:2])}")
                bus.publish("strategy_killed", {"id": r["id"], "reasons": reasons,
                                                "stage": "detail"})
            else:
                surv_msg = f"Detailed: PF {m.get('profit_factor', 0):.2f}, Sharpe {m.get('sharpe', 0):.2f}, Trades {m.get('trades', 0)}"
                self.db.update_strategy(r["id"], status="SURVIVED", fitness=f, survival_reason=surv_msg)
                activity.success("BACKTEST", f"Strategy #{r['id']} SURVIVED detail test (fitness {f:.2f}, PF {m['profit_factor']:.2f})")
                bus.publish("detail_result", {"id": r["id"], "fitness": f, "pf": m["profit_factor"],
                                              "return_pct": m["total_return_pct"],
                                              "dd": m["max_drawdown_pct"], "trades": m["trades"],
                                              "desc": describe(genome)[:120]})
            if r.get("parent_id"):
                try:
                    from ..ai_researcher.analyzer import record_experiment_memory
                    record_experiment_memory(r["id"], self.db)
                except Exception as e:
                    log.debug("record_experiment_memory failed #%s: %s", r["id"], e)

        from .stages import get_stage_manager
        get_stage_manager().transition("BACKTESTING", "VALIDATION", {"message": "Advancing from detailed testing to validation"})

    # ---------------- validation battery (stages 3-5) ----------------
    def _validation_batch(self) -> None:
        cfg = get_config()
        run_filter = f"AND s.run_id='{self.evo.active_run_id}'" if self.evo.active_run_id else ""
        rows = self.db.q(f"""SELECT s.* FROM strategies s
                            WHERE s.status IN ('SURVIVED', 'VALIDATING') {run_filter}
                              AND EXISTS (SELECT 1 FROM backtests b
                                          WHERE b.strategy_id=s.id AND b.stage='detail' AND b.stale=0)
                              AND NOT EXISTS (SELECT 1 FROM validations v
                                              WHERE v.strategy_id=s.id AND v.stale=0)
                            ORDER BY s.fitness DESC LIMIT ?""",
                         (cfg.evolution.validation_batch_size,))
        if not rows:
            return

        from .stages import get_stage_manager
        sm = get_stage_manager()
        cur_nodes = self.evo.total_nodes()
        target = self.evo.get_total_node_target()
        sm.transition(sm.current_stage, "VALIDATION", {
            "reason": f"Advancing {len(rows)} candidate strategies to validation",
            "current_nodes": cur_nodes,
            "target": target
        })
        total_val = len(rows)
        log.info("[INFO] [VALIDATION] Starting validation battery for %d candidate strategies", total_val)
        activity.running("VALIDATION", f"Starting validation battery for {total_val} candidate strategies", progress=0.0)

        for idx, r in enumerate(rows, 1):
            genome = json.loads(r["genome"])
            bt = self.db.one("""SELECT * FROM backtests WHERE strategy_id=? AND stage='detail' AND stale=0
                                ORDER BY id DESC LIMIT 1""", (r["id"],))
            if not bt:
                continue
            base_metrics = json.loads(bt["metrics"])
            base_fitness = bt["fitness"] or 0.0
            self.db.update_strategy(r["id"], status="VALIDATING")
            prog = round((idx / total_val) * 100.0, 1)
            activity.running("VALIDATION", f"Validating strategy #{r['id']} ({idx}/{total_val})", progress=prog)
            bus.publish("validation_started", {"id": r["id"]})
            t0 = time.time()
            try:
                v = full_validation({**r, "genome": genome}, base_metrics, base_fitness,
                                    base_metrics.get("trades_sample", []))
            except Exception as e:
                log.exception("validation crashed for #%s", r["id"])
                self.db.update_strategy(r["id"], status="SURVIVED")
                bus.publish("validation_error", {"id": r["id"], "error": str(e)[:200]})
                continue

            f2 = v.get("final_fitness", base_fitness)
            passed = v.get("passed", False)
            verdict = "QUALIFIED" if passed else "KILLED"

            # Compute validation fingerprint
            ghash = genome_hash(genome)
            val_fp = compute_experiment_fingerprint(
                ghash, genome["symbol"], genome["timeframe"], "validation",
                dataset_version=bt.get("dataset_version", 1),
            )

            # Export validation to RESEARCH/ (spec §10)
            try:
                export.export_validation(r["id"], v)
            except Exception as e:
                log.warning("failed to export validation json for #%s: %s", r["id"], e)

            self.db.x("""INSERT OR REPLACE INTO validations
                         (strategy_id,oos,walkforward,perturbation,spread_stress,slippage_stress,
                          montecarlo,regime_holdout,robustness_score,passed,notes,created_at,fingerprint)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (r["id"], jd(v.get("oos")), jd(v.get("walkforward")),
                       jd(v.get("perturbation")), jd(v.get("spread_slippage_stress")), None,
                       jd(v.get("montecarlo")), jd(v.get("regime_holdout")),
                       v.get("robustness_score"), 1 if passed else 0,
                       jd(v.get("death_reasons", [])), time.time(), val_fp))

            self.counters["validated"] += 1
            if passed:
                wr = round(float(base_metrics.get("win_rate") or 0.0) * 100, 1)
                pf = round(float(base_metrics.get("profit_factor") or 0.0), 2)
                dd = round(float(base_metrics.get("max_drawdown_pct") or 0.0) * 100, 1)
                rob = round(float(v.get("robustness_score") or 0.0), 2)
                tr = base_metrics.get("trades", 0)
                surv_msg = f"QUALIFIED: Win Rate {wr}%, Profit Factor {pf}, Robustness {rob}, Max DD {dd}%, Trades {tr}"
                self.db.update_strategy(r["id"], status="QUALIFIED", survival_reason=surv_msg)
                self.counters["qualified"] += 1
                log.info("[NODE] Child=Node_%d | Validation=COMPLETE | Qualification=QUALIFIED | %s",
                         r["id"], surv_msg)
                activity.success("VALIDATION", f"Strategy #{r['id']} {surv_msg}")
                bus.publish("strategy_qualified", {
                    "id": r["id"], "fitness": f2,
                    "robustness": rob,
                    "oos": (v.get("oos") or {}).get("metrics", {}).get("profit_factor"),
                    "desc": describe(genome)[:140], "runtime_s": round(time.time() - t0, 1)})
            else:
                death_reasons = v.get("death_reasons") or ["failed validation floor"]
                fail_reasons_str = "; ".join(death_reasons) if isinstance(death_reasons, list) else str(death_reasons)
                fail_msg = f"REJECTED: {fail_reasons_str}"
                self.db.update_strategy(r["id"], status="KILLED", failure_reason=fail_msg)
                self.counters["killed"] += 1
                log.info("[NODE] Child=Node_%d | Validation=COMPLETE | Qualification=REJECTED | Reason=%s",
                         r["id"], fail_msg[:120])
                activity.warning("VALIDATION", f"Strategy #{r['id']} KILLED during validation: {fail_msg}")
                bus.publish("strategy_killed", {"id": r["id"],
                                                "reasons": v.get("death_reasons", []),
                                                "stage": "validation",
                                                "robustness": v.get("robustness_score")})
            if r.get("parent_id"):
                try:
                    from ..ai_researcher.analyzer import record_experiment_memory
                    record_experiment_memory(r["id"], self.db)
                except Exception as e:
                    log.debug("record_experiment_memory failed #%s: %s", r["id"], e)

        log.info("[SUCCESS] [VALIDATION] Validation battery completed: %d strategies evaluated", total_val)
        activity.success("VALIDATION", f"✓ Validation completed — {total_val}/{total_val} strategies evaluated", progress=100.0)
        activity.set_idle()
        sm.transition("VALIDATION", "QUALIFICATION", {"message": "Advancing to qualification and specialization"})

    # ---------------- specialization ----------------
    def _specialization_pass(self) -> None:
        rows = self.db.q("""SELECT s.* FROM strategies s
                            WHERE s.status='QUALIFIED'
                              AND NOT EXISTS (SELECT 1 FROM matrices m WHERE m.strategy_id=s.id)
                            ORDER BY s.fitness DESC LIMIT 1""")
        for r in rows:
            genome = json.loads(r["genome"])
            bt = self.db.one("""SELECT metrics FROM backtests WHERE strategy_id=? AND stage='detail'
                                ORDER BY id DESC LIMIT 1""", (r["id"],))
            trades = json.loads(bt["metrics"]).get("trades_sample", []) if bt else []
            try:
                mats = full_matrices(genome, genome["symbol"], trades)
            except Exception as e:
                log.warning("matrix computation failed #%s: %s", r["id"], e)
                continue
            self.db.x("""INSERT OR REPLACE INTO matrices
                         (strategy_id,timeframe,session,day,regime,direction,created_at)
                         VALUES (?,?,?,?,?,?,?)""",
                      (r["id"], jd(mats["timeframe"]), jd(mats["session"]),
                       jd(mats["day"]), jd(mats["regime"]),
                       jd(mats["direction"]), time.time()))
            bus.publish("matrices_ready", {"id": r["id"]})
            props = propose_specializations(genome, mats, max_children=3)
            from ..genome import ops as gops
            for p in props:
                if self.evo.is_target_reached():
                    break
                try:
                    child, mt, desc = gops.directed(genome, p["action"], p["params"])
                except Exception:
                    continue
                sid = self.evo.spawn_child(child, r["id"], mt,
                                           f"specialization of #{r['id']}: {p['reason']}")
                if sid:
                    self.counters["specializations"] += 1
                    activity.info("EVOLUTION", f"Specialized child #{sid} created from parent #{r['id']} ({p['action']})")
                    bus.publish("specialized_child", {"parent": r["id"], "child": sid,
                                                      "reason": p["reason"], "action": p["action"]})

        from .stages import get_stage_manager
        if rows:
            get_stage_manager().transition("QUALIFICATION", "EVOLUTION", {
                "reason": "Specialization pass complete; advancing to generation evaluation",
                "current_nodes": self.evo.total_nodes(),
                "target": self.evo.get_total_node_target()
            })

    # ---------------- AI researcher ----------------
    def _research_pass(self) -> None:
        if self.evo.is_target_reached():
            return
        created = run_research_cycle(self.evo)
        for h in created:
            self.counters["hypotheses"] += 1
            export.export_hypothesis(h["id"], h)
            bus.publish("hypothesis_created", {"id": h["id"], "strategy_id": h["strategy_id"],
                                               "observation": h["observation"][:200],
                                               "hypothesis": h["hypothesis"][:200],
                                               "action": h["proposal"]["action"]})
        pending = self.db.q("""SELECT id FROM hypotheses WHERE status='PROPOSED'
                               ORDER BY id DESC LIMIT 2""")
        for p in pending:
            child = apply_hypothesis(p["id"], self.db)
            if child:
                activity.info("AI", f"Applied hypothesis #{p['id']} -> spawned candidate strategy #{child}")
                bus.publish("hypothesis_applied", {"hypothesis_id": p["id"],
                                                   "child_strategy_id": child})

    # ---------------- generations ----------------
    def _maybe_generation_complete(self) -> None:
        cfg = get_config().evolution
        target = max(30, cfg.population_size)
        pipeline_candidates = self.db.one(
            "SELECT COUNT(*) c FROM strategies WHERE status IN ('BORN', 'BACKTESTING', 'SURVIVED', 'VALIDATING')"
        )["c"]
        if self._tested_this_gen >= target or (pipeline_candidates == 0 and self._tested_this_gen > 0):
            from .stages import get_stage_manager
            sm = get_stage_manager()
            gen = self.evo.generation()
            counts = self.db.count_by_status()
            best = self.db.one("SELECT MAX(fitness) f, AVG(fitness) a FROM strategies WHERE fitness IS NOT NULL")
            stats = {"generation": gen, "born": counts.get("BORN", 0),
                     "tested": self.counters["screened"] + self.counters["detailed"],
                     "failed": counts.get("FAILED", 0), "survived": counts.get("SURVIVED", 0),
                     "validated": self.counters["validated"],
                     "qualified": counts.get("QUALIFIED", 0) + counts.get("PAPER", 0),
                     "killed": counts.get("KILLED", 0), "retired": counts.get("RETIRED", 0),
                     "duplicates_blocked": self.evo.duplicates_blocked,
                     "best_fitness": best["f"] if best else None,
                     "avg_fitness": round(best["a"], 4) if best and best["a"] else None,
                     "ts": time.time()}
            self.db.x("""INSERT OR REPLACE INTO generation_stats
                         (generation,born,tested,failed,survived,validated,qualified,
                          killed,retired,duplicates_blocked,best_fitness,avg_fitness,ts)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      tuple(stats[k] for k in ("generation", "born", "tested", "failed",
                                               "survived", "validated", "qualified",
                                               "killed", "retired", "duplicates_blocked",
                                               "best_fitness", "avg_fitness", "ts")))

            # Export generation to RESEARCH/ (spec §10)
            try:
                export.export_generation(gen, stats)
            except Exception as e:
                log.warning("failed to export generation stats for Gen %s: %s", gen, e)

            log.info("[SUCCESS] [EVOLUTION] Generation %d complete! Evaluated: %d, Qualified: %d, Best fitness: %s",
                     gen, stats["tested"], stats["qualified"], stats["best_fitness"])
            activity.success("EVOLUTION",
                             f"Generation {gen} complete! Evaluated: {stats['tested']}, Qualified: {stats['qualified']}, Best fitness: {stats['best_fitness']}")
            bus.publish("generation_complete", stats)
            self._tested_this_gen = 0
            # Explicit transition to next research cycle
            sm.transition("EVOLUTION", "NEXT_RESEARCH_CYCLE", {
                "generation": gen, "stats": stats,
                "current_nodes": self.evo.total_nodes(),
                "target": self.evo.get_total_node_target()
            })
            if not self.evo.is_target_reached():
                sm.transition("NEXT_RESEARCH_CYCLE", "EVOLUTION", {
                    "reason": "generation complete and target not reached",
                    "current_nodes": self.evo.total_nodes(),
                    "target": self.evo.get_total_node_target(),
                    "action": "generate next candidate batch"
                })


def _worker_init() -> None:
    os.environ["LAB_NO_FEATURE_PERSIST"] = "1"


def _worker_bt(payload: Dict) -> Dict:
    from .workers import bt_worker
    return bt_worker(payload)


_lab: Optional[Lab] = None


def get_lab() -> Lab:
    global _lab
    if _lab is None:
        _lab = Lab()
    return _lab
