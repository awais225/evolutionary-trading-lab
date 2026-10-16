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
        self.request_period = period
        from .periods import resolve_period, explicit_period
        if period and period.get("start_ts") is not None:
            self.period = explicit_period(float(period["start_ts"]), float(period["end_ts"]))
            if self.period["end_ts"] <= self.period["start_ts"]:
                raise ValueError("range start must precede end")
        elif period and period.get("duration_key"):
            self.period = resolve_period(str(period["duration_key"]))
        else:
            self.period = period
        self.cancel = threading.Event()
        self.lock = threading.Lock()
        self.state: Dict[str, Any] = {
            "status": "QUEUED", "stage": "QUEUED",
            "symbol": symbol, "timeframe": timeframe,
            "period": self.period,
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
            if (job.request_period or {}) != (period or {}):
                return {"ok": False, "error": "different history range already fetching for this pair; wait or cancel before retrying", "job": job.state}
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

        # V7.0 §2 — the selected duration/requested range travels with the job
        # and defines the fetch goal (explicit UTC bounds via the documented
        # period semantics). No period = the legacy config window.
        req_start = req_end = None
        period = job.period or {}
        try:
            from .periods import explicit_period, resolve_period
            if period.get("start_ts") is not None and period.get("end_ts") is not None:
                pr = explicit_period(float(period["start_ts"]), float(period["end_ts"]))
            elif period.get("duration_key"):
                pr = resolve_period(str(period["duration_key"]))
            else:
                pr = None
            if pr:
                req_start, req_end = pr["start_ts"], pr["end_ts"]
                job.set(operation=f"bridge: chunked copy_rates_range for the requested "
                                  f"range {pr.get('start_iso')} → {pr.get('end_iso')}")
        except Exception as e:
            job.set(operation=f"requested range rejected: {type(e).__name__}: {e}")
            raise
        result = engine.sync_master(job.symbol, job.timeframe, progress_cb=_prog,
                                    request_start=req_start, request_end=req_end)
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
        if (result or {}).get("status") in ("no_data_from_bridge", "invalid_range"):
            job.set(status="FAILED", stage="FAILED", finished_at=time.time(),
                    error=(result or {}).get("partial_reason") or (result or {}).get("error") or "no history returned",
                    result={"sync": result, "coverage": cov, "requested_vs_actual": (result or {}).get("fetch")})
            return
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
