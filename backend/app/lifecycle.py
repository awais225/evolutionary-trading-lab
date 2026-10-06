"""
Startup lifecycle: lazy bootstrap, readiness and bounded startup steps (V4.7).

Problem this solves
-------------------
The V4.x app performed *all* of its heavy startup work (dataset discovery, DATA
restoration, database reconciliation, diagnostics, monitors) inside the ASGI
``startup`` hook, i.e. **before** uvicorn accepted a single connection. An
operator could not even reach ``/health`` while the backend was scanning DATA,
and one slow subsystem delayed the whole application.

Behaviour now
-------------
* Safety-critical, cheap work stays on the startup path and is still done before
  readiness is announced: single-instance lease, directory layout migrations,
  configuration + logging, event-bus loop attachment, database open/schema,
  MT5 bridge detection, connection monitor, Live-Testing engine (always
  INACTIVE).
* Expensive work (data discovery, DATA restoration, database reconciliation,
  startup diagnostics) runs in a background bootstrap thread. ``/health`` and
  ``/api/health`` answer immediately and report the bootstrap state honestly;
  ``/api/ready`` answers 503 (with the running/failed step names) until the
  backend is genuinely ready and 200 afterwards.
* Every background step is bounded: if a subsystem hangs, the step is marked
  ``TIMEOUT`` with its limit, the remaining steps still run, and the condition is
  reported in ``/health``, ``/api/ready`` and the activity log instead of
  freezing startup forever.
* ``EVOLUTIONARY_LAB_STARTUP_BLOCKING=1`` runs everything synchronously (used by
  the test suite and by anyone who wants the old deterministic behaviour).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger("lifecycle")

# step states
PENDING = "PENDING"
RUNNING = "RUNNING"
DONE = "DONE"
FAILED = "FAILED"
TIMEOUT = "TIMEOUT"
SKIPPED = "SKIPPED"

# overall states
NOT_STARTED = "not_started"
STARTING = "starting"
READY = "ready"
DEGRADED = "degraded"
FAILED_STATE = "failed"

DEFAULT_STEP_TIMEOUT_S = 120.0
BACKGROUND_FLAG = "EVOLUTIONARY_LAB_STARTUP_BLOCKING"


class StartupStep:
    def __init__(self, name: str, label: str, fn: Callable[[], Any],
                 timeout_s: float, critical: bool = True):
        self.name = name
        self.label = label
        self.fn = fn
        self.timeout_s = timeout_s
        self.critical = critical
        self.status = PENDING
        self.error: Optional[str] = None
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None
        self.duration_ms: Optional[float] = None
        self.result: Any = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "status": self.status,
            "critical": self.critical,
            "timeout_s": self.timeout_s,
            "duration_ms": round(self.duration_ms, 1) if self.duration_ms is not None else None,
            "error": self.error,
        }


class StartupLifecycle:
    """Tracks startup progress; safe to query from any thread at any time."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._steps: List[StartupStep] = []
        self._state = NOT_STARTED
        self._thread: Optional[threading.Thread] = None
        self._began: Optional[float] = None
        self._finished: Optional[float] = None
        self._background = False
        self._notes: List[str] = []

    # ---------------- registration / execution ----------------
    def register(self, name: str, label: str, fn: Callable[[], Any],
                 timeout_s: float = DEFAULT_STEP_TIMEOUT_S, critical: bool = True) -> None:
        with self._lock:
            self._steps.append(StartupStep(name, label, fn, timeout_s, critical))

    @property
    def blocking_requested(self) -> bool:
        return os.environ.get(BACKGROUND_FLAG, "").lower() in ("1", "true", "yes")

    def run(self, background: Optional[bool] = None) -> None:
        """Execute the registered steps (in this thread or in a worker thread)."""
        with self._lock:
            if self._state != NOT_STARTED:
                return
            use_background = (not self.blocking_requested) if background is None else background
            self._background = use_background
            self._began = time.time()
            self._state = STARTING
        if use_background:
            self._thread = threading.Thread(target=self._execute_all, name="startup-bootstrap", daemon=True)
            self._thread.start()
        else:
            self._execute_all()

    def _execute_all(self) -> None:
        for step in self._steps:
            self._execute_step(step)
            if step.status in (FAILED, TIMEOUT) and step.critical:
                # a critical failure makes readiness impossible; still run the
                # remaining steps so the rest of the app is as complete as it can be
                self._note(f"critical step '{step.name}' {step.status}: {step.error}")
        with self._lock:
            self._finished = time.time()
            self._state = READY if self._compute_ready() else (
                FAILED_STATE if self._critical_failure() else DEGRADED)
        snap = self.snapshot()
        log.info("startup lifecycle finished in %.0f ms: state=%s ready=%s failed=%s",
                 (snap["elapsed_ms"] or 0), snap["state"], snap["ready"],
                 [s["name"] for s in snap["steps"] if s["status"] in (FAILED, TIMEOUT)])

    def _execute_step(self, step: StartupStep) -> None:
        step.started_at = time.time()
        with self._lock:
            step.status = RUNNING
        done = threading.Event()
        box: Dict[str, Any] = {}

        def _target() -> None:
            try:
                box["result"] = step.fn()
            except Exception as e:  # the step reports, it never kills startup
                box["error"] = f"{type(e).__name__}: {e}"
                log.exception("startup step '%s' failed", step.name)
            finally:
                done.set()

        worker = threading.Thread(target=_target, name=f"startup-{step.name}", daemon=True)
        worker.start()
        finished = done.wait(timeout=step.timeout_s)
        step.finished_at = time.time()
        step.duration_ms = (step.finished_at - step.started_at) * 1000.0
        if not finished:
            step.status = TIMEOUT
            step.error = f"step exceeded its {step.timeout_s:.0f}s startup budget; continuing without it"
            log.error("startup step '%s' TIMED OUT after %.0fs", step.name, step.timeout_s)
            return
        if "error" in box:
            step.status = FAILED
            step.error = box["error"]
            return
        step.result = box.get("result")
        step.status = DONE

    def _note(self, msg: str) -> None:
        with self._lock:
            self._notes.append(msg)

    # ---------------- queries ----------------
    def _compute_ready(self) -> bool:
        return all(s.status == DONE for s in self._steps) if self._steps else True

    def _critical_failure(self) -> bool:
        return any(s.critical and s.status in (FAILED, TIMEOUT) for s in self._steps)

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def is_ready(self) -> bool:
        with self._lock:
            return self._state == READY

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            steps = [s.to_dict() for s in self._steps]
            state = self._state
            began, finished = self._began, self._finished
            notes = list(self._notes)
            background = self._background
        running = [s["name"] for s in steps if s["status"] == RUNNING]
        failed = [s for s in steps if s["status"] in (FAILED, TIMEOUT)]
        return {
            "state": state,
            "ready": state == READY,
            "mode": "background" if background else "blocking",
            "elapsed_ms": round(((finished or time.time()) - began) * 1000.0, 1) if began else None,
            "pending": [s["name"] for s in steps if s["status"] == PENDING],
            "running": running,
            "failed_steps": failed,
            "warnings": notes,
            "steps": steps,
        }

    def wait_ready(self, timeout: float = 60.0) -> bool:
        """Block until the backend is ready (or the timeout expires)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._state in (READY, DEGRADED):     # degraded is usable, with warnings
                return True
            if self._state == FAILED_STATE:
                return False
            time.sleep(0.05)
        return self._state in (READY, DEGRADED)

    def readiness_response(self) -> Dict[str, Any]:
        """Body for /api/ready — never hides a failure."""
        snap = self.snapshot()
        if snap["state"] == READY:
            code = "ready"
            detail = "backend initialised; all startup steps completed"
        elif snap["state"] == NOT_STARTED:
            code = "not_started"
            detail = "startup hook has not run in this process yet"
        elif snap["state"] == FAILED_STATE:
            code = "failed"
            detail = "startup failed: " + ", ".join(
                f"{s['name']} ({s['status']}: {s['error']})" for s in snap["failed_steps"]) or "unknown startup failure"
        elif snap["state"] == DEGRADED:
            code = "degraded"
            detail = "backend running with non-critical startup problems: " + ", ".join(
                s["name"] for s in snap["failed_steps"])
        else:
            code = "starting"
            detail = "startup in progress"
        return {
            "ok": snap["state"] in (READY, DEGRADED),
            "code": code,
            "detail": detail,
            "startup": snap,
        }


_LIFECYCLE: Optional[StartupLifecycle] = None


def get_lifecycle() -> StartupLifecycle:
    global _LIFECYCLE
    if _LIFECYCLE is None:
        _LIFECYCLE = StartupLifecycle()
    return _LIFECYCLE


def reset_lifecycle() -> StartupLifecycle:
    """Test helper: drop the singleton so a fresh registration can be built."""
    global _LIFECYCLE
    _LIFECYCLE = StartupLifecycle()
    return _LIFECYCLE
