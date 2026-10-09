"""V6 — real per-node live-testing workers.

The Live Testing node table's START/STOP controls drive THESE workers:

* START creates the node's worker thread and only reports success after the
  worker is confirmed actually running (state RUNNING, thread alive).
* STOP signals the worker, joins it, and only reports success after the worker
  is confirmed stopped. STOP is idempotent.
* One node can have at most ONE worker. A duplicate START never creates a
  second worker, a second schedule, or a second order path — it returns the
  existing worker's state.
* The worker's state is the BACKEND's state. The frontend reconstructs the
  buttons from it; a page refresh can never fake a running worker.

Each worker evaluates its node through the existing LiveTestingEngine logic
(``_evaluate_node``), so schedules, risk limits, sizing and the order path are
the ones the research system already enforces — nothing is rewritten here.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional

log = logging.getLogger("live_testing.workers")

#: seconds between one worker's evaluation cycles
DEFAULT_CYCLE_INTERVAL_S = 30.0


class NodeLiveWorker:
    """One node's live-testing worker: a real thread with a real lifecycle."""

    def __init__(self, node_id: int, *, interval_s: float = DEFAULT_CYCLE_INTERVAL_S,
                 engine: Any = None):
        self.node_id = int(node_id)
        self._interval = float(interval_s)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._engine = engine
        self._state = "STOPPED"
        self._state_lock = threading.Lock()
        self.started_at: Optional[float] = None
        self.stopped_at: Optional[float] = None
        self.last_cycle_at: Optional[float] = None
        self.cycles = 0
        self.last_error = ""

    # ------------------------------------------------------------------ state --
    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def state_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "state": self._state,
            "running": self._state == "RUNNING" and self.alive,
            "alive": self.alive,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "last_cycle_at": self.last_cycle_at,
            "cycles": self.cycles,
            "last_error": self.last_error,
            "interval_s": self._interval,
        }

    # -------------------------------------------------------------- lifecycle --
    def start(self) -> bool:
        """Start the thread and CONFIRM it is actually running.

        Returns True only when the worker reached RUNNING with its thread
        alive — the caller (API) must not report STARTED before that.
        """
        with self._state_lock:
            if self._thread is not None and self._thread.is_alive():
                return False                      # duplicate start — no second thread
            self._stop.clear()
            self.last_error = ""
            self._state = "STARTING"
            self._thread = threading.Thread(
                target=self._run, name=f"live-test-node-{self.node_id}", daemon=True)
            self._thread.start()
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if self._state == "RUNNING" and self.alive:
                return True
            if self._state in ("STOPPED", "FAILED"):
                return False
            time.sleep(0.01)
        return self._state == "RUNNING" and self.alive

    def request_stop(self) -> None:
        self._stop.set()

    def join(self, timeout: float = 10.0) -> None:
        t = self._thread
        if t is not None:
            t.join(timeout=timeout)

    def confirm_stopped(self) -> bool:
        """True when the worker is verifiably not running any more."""
        return (not self.alive) and self._state in ("STOPPED", "FAILED")

    # ------------------------------------------------------------------- work --
    def _run(self) -> None:
        with self._state_lock:
            self._state = "RUNNING"
            self.started_at = time.time()
        log.info("live-testing worker for node %s started", self.node_id)
        try:
            while not self._stop.is_set():
                try:
                    self._cycle()
                except Exception as e:            # one bad cycle must not kill the worker
                    self.last_error = f"{type(e).__name__}: {e}"
                    log.exception("live-testing worker node %s cycle failed: %s",
                                  self.node_id, e)
                self._stop.wait(self._interval)
        finally:
            with self._state_lock:
                self._state = "STOPPED"
                self.stopped_at = time.time()
            log.info("live-testing worker for node %s stopped (cycles=%d)",
                     self.node_id, self.cycles)

    def _cycle(self) -> None:
        """Run ONE evaluation for this node through the existing engine logic."""
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
            self.last_error = "node is not enrolled (is_active) - nothing evaluated"
            return
        from ..mt5.factory import get_bridge
        bridge = get_bridge()
        if not eng._bridge_connected(bridge):
            self.last_error = "MT5 not connected - cycle skipped (no orders possible)"
            return
        frames: Dict = {}
        eng._evaluate_node(node, bridge, frames)
        self.cycles += 1
        self.last_cycle_at = time.time()
        self.last_error = ""


class LiveTestWorkerManager:
    """Process-wide registry: at most ONE live-testing worker per node."""

    def __init__(self):
        self._lock = threading.RLock()
        self._workers: Dict[int, NodeLiveWorker] = {}

    # ---- START: create the worker; confirm it runs; never duplicate ----
    def start_worker(self, node_id: int, *, interval_s: float = DEFAULT_CYCLE_INTERVAL_S) -> Dict[str, Any]:
        node_id = int(node_id)
        with self._lock:
            existing = self._workers.get(node_id)
            if existing is not None and existing.alive:
                return {"ok": True, "already_running": True,
                        "worker": existing.state_dict()}   # the ONE worker's state
            if existing is not None:
                self._workers.pop(node_id, None)           # stale entry
            worker = NodeLiveWorker(node_id, interval_s=interval_s)
            self._workers[node_id] = worker
        ok = worker.start()
        if not ok:
            with self._lock:
                self._workers.pop(node_id, None)
            return {"ok": False, "already_running": False,
                    "worker": worker.state_dict(),
                    "error": worker.last_error or "worker did not reach RUNNING"}
        return {"ok": True, "already_running": False, "worker": worker.state_dict()}

    # ---- STOP: cancel the worker; confirm it stopped; idempotent ----
    def stop_worker(self, node_id: int, *, timeout: float = 10.0) -> Dict[str, Any]:
        node_id = int(node_id)
        with self._lock:
            worker = self._workers.get(node_id)
            if worker is None or not worker.alive:
                self._workers.pop(node_id, None)
                probe = NodeLiveWorker(node_id)
                return {"ok": True, "already_stopped": True,
                        "worker": probe.state_dict()}
            worker.request_stop()
        worker.join(timeout=timeout)
        with self._lock:
            self._workers.pop(node_id, None)
        state = worker.state_dict()
        if worker.alive:                                    # pragma: no cover - defensive
            state["state"] = "STOPPING"
            return {"ok": False, "already_stopped": False, "worker": state,
                    "error": "worker did not stop within the timeout"}
        return {"ok": True, "already_stopped": False, "worker": state}

    # ---- state (backend is authoritative) ----
    def is_running(self, node_id: int) -> bool:
        with self._lock:
            w = self._workers.get(int(node_id))
            return bool(w is not None and w.alive and w._state == "RUNNING")

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

    def stop_all(self, *, timeout: float = 10.0) -> None:
        with self._lock:
            ids = list(self._workers.keys())
        for nid in ids:
            try:
                self.stop_worker(nid, timeout=timeout)
            except Exception:                                # pragma: no cover
                log.exception("stopping live-testing worker %s failed", nid)


_manager: Optional[LiveTestWorkerManager] = None
_manager_lock = threading.Lock()


def get_worker_manager() -> LiveTestWorkerManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = LiveTestWorkerManager()
        return _manager
