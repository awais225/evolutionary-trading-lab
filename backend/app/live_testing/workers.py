"""V6 — one live-testing worker per node: backend state is authoritative.

V6.4 lifecycle contract (operator-facing states, explicit and complete):

    IDLE       never started for this node (or a fresh probe after restart)
    STARTING   start accepted; the thread is not yet confirmed running
    RUNNING    the worker is confirmed running (thread alive, cycles executing)
    STOPPING   stop requested; the in-flight cycle finishes first, no new cycle
               starts, and the MT5 state is reconciled (read-only) before the
               final state is reported
    STOPPED    verifiably not running (operator STOP, or stopped at startup)
    COMPLETED  the worker finished on its own (its schedule was disabled /
               the node left the active set) — not an error
    ERROR      the worker failed and stopped (its last error is recorded)

Guarantees:

* at most ONE worker per node — a second START returns the existing worker
  (``already_running``) and never duplicates it; stale entries are replaced,
  never stacked. Every start carries a fresh ``task_id``; a state reply always
  names the task_id that produced it, so a stale response can never be mistaken
  for a live one.
* STOP is idempotent and cancels the node's task, then CONFIRMS the final state.
  STOP ALL does exactly that for every worker, one node at a time.
* a STOPPING/STOPPED worker places NO new trades: the stop flag is checked
  before every evaluation, and the engine only evaluates enrolled (is_active)
  nodes — the stop path un-enrols first.
* stopping NEVER touches broker positions. The post-stop ``reconcile`` block
  reports what is still open at the broker for this node (read-only) so the
  operator knows stopping is not closing.
* START/STOP races: state transitions run under the worker's own lock; the
  manager registry is guarded by an RLock; a stop of a not-yet-started worker
  is a no-op success.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

DEFAULT_CYCLE_INTERVAL_S = 15.0

#: the complete, explicit lifecycle vocabulary
STATE_IDLE = "IDLE"
STATE_STARTING = "STARTING"
STATE_RUNNING = "RUNNING"
STATE_STOPPING = "STOPPING"
STATE_STOPPED = "STOPPED"
STATE_COMPLETED = "COMPLETED"
STATE_ERROR = "ERROR"
WORKER_STATES = (STATE_IDLE, STATE_STARTING, STATE_RUNNING, STATE_STOPPING,
                 STATE_STOPPED, STATE_COMPLETED, STATE_ERROR)
#: states in which the worker is (or may be) executing work
ACTIVE_STATES = (STATE_STARTING, STATE_RUNNING, STATE_STOPPING)
#: states that mean "verifiably not running any more"
FINAL_STATES = (STATE_STOPPED, STATE_COMPLETED, STATE_ERROR)


class NodeLiveWorker:
    """One node's live-testing loop. State is the BACKEND's state."""

    def __init__(self, node_id: int, *, interval_s: float = DEFAULT_CYCLE_INTERVAL_S,
                 schedule: Optional[Dict[str, Any]] = None,
                 task_id: Optional[str] = None):
        self.node_id = int(node_id)
        self._interval = float(interval_s)
        self.schedule: Dict[str, Any] = dict(schedule or {})
        self.task_id = task_id or f"ltw-{self.node_id}-{uuid.uuid4().hex[:12]}"
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._state = STATE_IDLE
        self._state_lock = threading.Lock()
        self._cycle_lock = threading.Lock()      # the in-flight cycle boundary
        self.cycles = 0
        self.started_at: Optional[float] = None
        self.stopped_at: Optional[float] = None
        self.last_error = ""
        self.last_cycle_at: Optional[float] = None
        self.stop_reason: Optional[str] = None
        self.reconcile: Dict[str, Any] = {}
        self._engine = None

    # ------------------------------------------------------------------ state --
    @property
    def alive(self) -> bool:
        t = self._thread
        return bool(t is not None and t.is_alive())

    def state_dict(self) -> Dict[str, Any]:
        with self._state_lock:
            st = self._state
        return {
            "task_id": self.task_id,
            "node_id": self.node_id,
            "state": st,
            "running": st == STATE_RUNNING and self.alive,
            "alive": bool(self.alive),
            "in_flight": self._cycle_lock.locked(),
            "cycles": self.cycles,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "last_cycle_at": self.last_cycle_at,
            "last_error": self.last_error,
            "stop_reason": self.stop_reason,
            "schedule": dict(self.schedule),
            "reconcile": dict(self.reconcile),
        }

    def start(self) -> bool:
        """Start the loop thread and confirm it reached RUNNING."""
        with self._state_lock:
            if self._state in ACTIVE_STATES and self.alive:
                return True
            if self._state == STATE_RUNNING and self.alive:      # pragma: no cover
                return True
            self._stop = threading.Event()
            self._state = STATE_STARTING
        self._thread = threading.Thread(
            target=self._run, name=f"LiveTestWorker-{self.node_id}-{self.task_id[-6:]}",
            daemon=True)
        self._thread.start()
        deadline = time.time() + 5.0
        while time.time() < deadline:
            with self._state_lock:
                st, alive = self._state, self.alive
            if st == STATE_RUNNING and alive:
                return True
            if st in FINAL_STATES:
                # a worker that finished cleanly during startup (its node left
                # the active set / schedule disabled) DID start — COMPLETED is a
                # success, not a failed start; ERROR is a failed start.
                return st == STATE_COMPLETED
            time.sleep(0.05)
        with self._state_lock:
            return self._state == STATE_RUNNING and self.alive

    def request_stop(self, reason: str = "operator STOP") -> None:
        """Ask the loop to stop. The in-flight cycle finishes; no new cycle starts."""
        with self._state_lock:
            if self._state in FINAL_STATES:
                return
            self._state = STATE_STOPPING
            self.stop_reason = reason
        self._stop.set()

    def join(self, timeout: float = 10.0) -> None:
        t = self._thread
        if t is not None:
            t.join(timeout=timeout)

    def confirm_stopped(self) -> bool:
        """True when the worker is verifiably not running any more."""
        with self._state_lock:
            st = self._state
        return (not self.alive) and st in FINAL_STATES

    # ------------------------------------------------------------------- work --
    def _run(self) -> None:
        with self._state_lock:
            self._state = STATE_RUNNING
            self.started_at = time.time()
        log.info("live-testing worker for node %s started (task %s, schedule=%s)",
                 self.node_id, self.task_id, self.schedule or "product default")
        final = STATE_STOPPED
        try:
            while not self._stop.is_set():
                # the stop flag gates EVERY evaluation: a stopped node never
                # starts a new cycle (and the engine only trades enrolled nodes)
                try:
                    with self._cycle_lock:
                        if self._stop.is_set():
                            break
                        done = self._cycle()
                    if done == "completed":
                        final = STATE_COMPLETED
                        break
                except Exception as e:            # one bad cycle must not kill the worker
                    self.last_error = f"{type(e).__name__}: {e}"
                    log.exception("live-testing worker node %s cycle failed: %s",
                                  self.node_id, e)
                self._stop.wait(self._interval)
        except Exception as e:                    # pragma: no cover - thread-level failure
            final = STATE_ERROR
            self.last_error = f"{type(e).__name__}: {e}"
            log.exception("live-testing worker node %s crashed: %s", self.node_id, e)
        finally:
            # V6.4 — before the final state is reported, reconcile with MT5
            # (READ-ONLY): what this node still has open at the broker. Stopping
            # NEVER closes a position; the reconcile block proves it.
            self.reconcile = self._reconcile()
            with self._state_lock:
                self._state = final
                self.stopped_at = time.time()
            log.info("live-testing worker for node %s stopped (state=%s cycles=%d task=%s)",
                     self.node_id, final, self.cycles, self.task_id)

    def _reconcile(self) -> Dict[str, Any]:
        """Read-only MT5 reconcile for this node — never places or closes anything."""
        out: Dict[str, Any] = {"checked": False, "positions": [], "orders": [],
                               "note": "stopping does not close broker positions"}
        try:
            from ..mt5.execution import live_test_magic
            from ..mt5.factory import get_bridge
            bridge = get_bridge()
            if getattr(bridge, "is_simulated", True):
                out["note"] = ("no real terminal attached - nothing to reconcile at a broker; "
                                "stopping does not close broker positions")
                return out
            magic = live_test_magic(self.node_id)
            positions = bridge.positions_get() or []
            mine = [p for p in positions if int(p.get("magic") or 0) == int(magic)]
            out["checked"] = True
            out["magic"] = magic
            out["positions"] = [{"ticket": p.get("ticket"), "symbol": p.get("symbol"),
                                 "volume": p.get("volume"), "profit": p.get("profit")}
                                for p in mine]
            out["positions_open"] = len(mine)
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {e}"
        return out

    def _cycle(self) -> Optional[str]:
        """Run ONE evaluation for this node through the existing engine logic.

        Returns ``"completed"`` when the worker should finish on its own (the
        node's schedule was disabled / it left the active set), else ``None``.
        """
        from .engine import get_live_testing_engine
        eng = self._engine if self._engine is not None else get_live_testing_engine()
        node = None
        for n in eng.eligible_nodes():
            try:
                if int(n.get("id")) == self.node_id:
                    node = n
                    break
            except (TypeError, ValueError):
                continue
        if node is None:
            # un-enrolled (STOP landed / schedule disabled elsewhere): finish
            # cleanly instead of spinning — COMPLETED, not an error.
            if self.cycles == 0:
                self.last_error = "node is not enrolled (is_active) - nothing evaluated"
            return "completed"
        cfg = node.get("config") or {}
        if cfg.get("enabled") is False:
            self.last_error = "the node's schedule is disabled - worker completes"
            return "completed"
        from ..mt5.factory import get_bridge
        bridge = get_bridge()
        if not eng._bridge_connected(bridge):
            self.last_error = "MT5 not connected - cycle skipped (no orders possible)"
            return None
        frames: Dict = {}
        eng._evaluate_node(node, bridge, frames)
        self.cycles += 1
        self.last_cycle_at = time.time()
        self.last_error = ""
        return None


class LiveTestWorkerManager:
    """Process-wide registry: at most ONE live-testing worker per node."""

    def __init__(self):
        self._lock = threading.RLock()
        self._workers: Dict[int, NodeLiveWorker] = {}

    # ---- START: create the worker; confirm it runs; never duplicate ----
    def start_worker(self, node_id: int, *, interval_s: float = DEFAULT_CYCLE_INTERVAL_S,
                     schedule: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        node_id = int(node_id)
        with self._lock:
            existing = self._workers.get(node_id)
            if existing is not None and existing.alive:
                # V6.4 — a second START is refused with the RUNNING worker's
                # identity (task_id), never a duplicate worker.
                return {"ok": True, "already_running": True, "task_id": existing.task_id,
                        "worker": existing.state_dict()}
            if existing is not None:
                self._workers.pop(node_id, None)           # stale entry
            worker = NodeLiveWorker(node_id, interval_s=interval_s, schedule=schedule)
            self._workers[node_id] = worker
        ok = worker.start()
        final = worker.state_dict()
        if not ok and final.get("state") != STATE_COMPLETED:
            with self._lock:
                self._workers.pop(node_id, None)
            return {"ok": False, "already_running": False, "task_id": worker.task_id,
                    "worker": worker.state_dict(),
                    "error": worker.last_error or "worker did not reach RUNNING"}
        # a COMPLETED entry is kept so the backend reports the true final state
        # (a page refresh must never fall back to an anonymous IDLE probe)
        return {"ok": True, "already_running": False, "task_id": worker.task_id,
                "worker": worker.state_dict()}

    # ---- STOP: cancel the worker's task; confirm final state; idempotent ----
    def stop_worker(self, node_id: int, *, timeout: float = 10.0,
                    reason: str = "operator STOP") -> Dict[str, Any]:
        node_id = int(node_id)
        with self._lock:
            worker = self._workers.get(node_id)
            if worker is None:
                probe = NodeLiveWorker(node_id)
                return {"ok": True, "already_stopped": True, "task_id": probe.task_id,
                        "worker": probe.state_dict()}
            if not worker.alive:
                # already final (STOPPED/COMPLETED/ERROR) — the entry STAYS so a
                # refresh keeps reporting the true final state; it is replaced
                # only by the next START (fresh task_id)
                return {"ok": True, "already_stopped": True, "task_id": worker.task_id,
                        "worker": worker.state_dict()}
            worker.request_stop(reason)
        worker.join(timeout=timeout)
        state = worker.state_dict()
        if worker.alive:                                    # pragma: no cover - defensive
            state["state"] = STATE_STOPPING
            return {"ok": False, "already_stopped": False, "task_id": worker.task_id,
                    "worker": state,
                    "error": "worker did not stop within the timeout"}
        # the final-state entry is KEPT: stopping loses no lifecycle state
        return {"ok": True, "already_stopped": False, "task_id": worker.task_id,
                "worker": state}

    # ---- STOP ALL: every RUNNING worker, one node at a time ----
    def stop_all(self, *, timeout: float = 10.0,
                 reason: str = "operator STOP ALL") -> Dict[str, Any]:
        with self._lock:
            # only ALIVE workers are cancellable; retained final-state entries
            # (STOPPED/COMPLETED/ERROR) are history, not work for STOP ALL —
            # a repeat STOP ALL is a clean no-op (stopped == 0)
            ids = [nid for nid, w in self._workers.items() if w.alive]
        results = []
        for nid in ids:
            try:
                results.append({"node_id": nid, **self.stop_worker(nid, timeout=timeout,
                                                                   reason=reason)})
            except Exception as e:                          # pragma: no cover
                log.exception("stopping live-testing worker %s failed", nid)
                results.append({"node_id": nid, "ok": False, "error": f"{type(e).__name__}: {e}"})
        return {"ok": all(r.get("ok") for r in results) if results else True,
                "stopped": len([r for r in results if r.get("ok")]),
                "results": results,
                "remaining_running": len([1 for _nid, w in self._all() if w.alive])}

    # ---- state (backend is authoritative) ----
    def is_running(self, node_id: int) -> bool:
        with self._lock:
            w = self._workers.get(int(node_id))
            return bool(w is not None and w.alive and w._state == STATE_RUNNING)

    def state(self, node_id: int) -> Dict[str, Any]:
        with self._lock:
            w = self._workers.get(int(node_id))
            if w is None:
                probe = NodeLiveWorker(int(node_id))
                return probe.state_dict()
            return w.state_dict()

    def states(self) -> Dict[str, Any]:
        with self._lock:
            return {str(nid): w.state_dict() for nid, w in self._workers.items()}

    def _all(self):
        with self._lock:
            return list(self._workers.items())


_manager: Optional[LiveTestWorkerManager] = None
_manager_lock = threading.Lock()


def get_worker_manager() -> LiveTestWorkerManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = LiveTestWorkerManager()
        return _manager
