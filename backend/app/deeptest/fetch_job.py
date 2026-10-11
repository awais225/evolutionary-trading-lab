"""V6.6 §8 — GET MT5 DATA: fetch only the missing history for one (symbol, tf)
cache key through the lab's existing ``DataEngine.sync_master`` (bridge
``copy_rates_range``), with real progress, concurrent-key dedupe, safe cancel,
and NO implicit backtest.

Concurrent requests for the same (symbol, timeframe) are coalesced onto one
job. Cancel marks the job and lets the in-flight MT5 request finish or stop at
the next range boundary — a cancelled fetch never corrupts the cache (the store
append is atomic per partition).
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

_STAGES = ("QUEUED", "FETCHING", "VALIDATING", "DONE")


class _FetchJob:
    def __init__(self, symbol: str, timeframe: str, period: Optional[Dict[str, Any]]):
        self.symbol = symbol
        self.timeframe = timeframe
        self.period = period
        self.cancel = threading.Event()
        self.lock = threading.Lock()
        self.state: Dict[str, Any] = {
            "status": "QUEUED", "stage": "QUEUED",
            "symbol": symbol, "timeframe": timeframe,
            "period": period,
            "started_at": time.time(), "finished_at": None,
            "progress_pct": 0.0,
            "operation": "waiting for the fetch worker",
            "result": None, "error": None,
        }

    def set(self, **kw: Any) -> None:
        with self.lock:
            self.state.update(kw)


_JOBS: Dict[str, _FetchJob] = {}
_JOBS_LOCK = threading.Lock()
_EXEC = threading.Thread


def _key(symbol: str, timeframe: str) -> str:
    return f"{str(symbol).upper()}|{str(timeframe).upper()}"


def start_fetch(symbol: str, timeframe: str,
                period: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Start (or join) a GET MT5 DATA job for one cache key. Never backs up."""
    key = _key(symbol, timeframe)
    with _JOBS_LOCK:
        job = _JOBS.get(key)
        if job and job.state["status"] not in ("DONE", "FAILED", "CANCELLED"):
            return {"ok": True, "joined_existing": True, "job": job.state}
        job = _FetchJob(str(symbol).upper(), str(timeframe).upper(), period)
        _JOBS[key] = job
    t = threading.Thread(target=_run, args=(job,), name=f"deeptest-fetch-{key}", daemon=True)
    t.start()
    return {"ok": True, "joined_existing": False, "job": job.state}


def cancel_fetch(symbol: str, timeframe: str) -> Dict[str, Any]:
    key = _key(symbol, timeframe)
    with _JOBS_LOCK:
        job = _JOBS.get(key)
    if not job or job.state["status"] in ("DONE", "FAILED", "CANCELLED"):
        return {"ok": False, "error": "no fetch is running for this cache key"}
    job.cancel.set()
    # V6.7 §5 — cancellation reaches the chunked fetch loop itself (between
    # bounded chunks), so every fetch reaches its terminal state promptly.
    try:
        from ..data.engine import cancel_sync
        cancel_sync(symbol, timeframe)
    except Exception:
        pass
    job.set(operation="cancelling — stopping after the current chunk")
    return {"ok": True, "job": job.state}


def fetch_status(symbol: str, timeframe: str) -> Dict[str, Any]:
    key = _key(symbol, timeframe)
    with _JOBS_LOCK:
        job = _JOBS.get(key)
    return {"ok": True, "job": job.state if job else None}


def _run(job: _FetchJob) -> None:
    try:
        job.set(status="FETCHING", stage="FETCHING", progress_pct=5.0,
                operation=f"incremental fetch of missing {job.symbol}/{job.timeframe} history")
        if job.cancel.is_set():
            job.set(status="CANCELLED", stage="CANCELLED", finished_at=time.time(),
                    progress_pct=0.0)
            return
        from ..data.engine import get_data_engine
        engine = get_data_engine()
        # sync_master reads the last stored timestamp and requests ONLY the
        # missing range — the requested period extends the goal but the fetch
        # itself is strictly incremental
        job.set(progress_pct=15.0, operation="bridge: chunked copy_rates_range (missing range only)")

        def _prog(pct, msg):
            job.set(progress_pct=15.0 + (pct - 35.0) * 0.75, operation=msg)

        result = engine.sync_master(job.symbol, job.timeframe, progress_cb=_prog)
        sync_status = str((result or {}).get("sync_status") or "").upper()
        if job.cancel.is_set() or (result or {}).get("status") == "cancelled":
            job.set(status="CANCELLED", stage="CANCELLED", finished_at=time.time())
            return
        job.set(progress_pct=85.0, stage="VALIDATING",
                operation="validating and persisting the merged store")
        from .coverage import pair_coverage
        cov = pair_coverage(job.symbol, job.timeframe)
        # V6.7 §5.5 — terminal label reflects the REAL outcome: COMPLETE only
        # when the received range covers the request; a short range is PARTIAL.
        stage = "PARTIAL" if sync_status == "PARTIAL" else "COMPLETE"
        note = (result or {}).get("partial_reason") or "history up to date"
        job.set(status="DONE", stage=stage, finished_at=time.time(),
                progress_pct=100.0,
                operation=note,
                result={"sync": result, "coverage": cov,
                        "requested_vs_actual": (result or {}).get("fetch")})
    except Exception as e:  # noqa: BLE001 — a fetch failure must stay visible
        job.set(status="FAILED", stage="FAILED", finished_at=time.time(),
                error=f"{type(e).__name__}: {e}"[:400])
