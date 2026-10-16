"""
Data Engine — incremental ingestion, normalization, integrity, and caching (V2).

Pipeline:
  1. Master sync: requests ONLY missing range from MT5/Simulator (spec §4)
  2. Data integrity validation (spec §7): flag, log, quarantine invalid rows
  3. Deduplication & conflict detection (spec §5)
  4. DST-aware session labeling (spec §43)
  5. Persistent master store per (symbol, timeframe, broker, server) (spec §6)
  6. Materialized dataset views for reproducible experiment runs

V1 datasets are recognized and reused without re-download (spec §3, §45).
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .. import paths as P
from ..activity import activity
from ..config import get_config
from ..db.database import get_db
from ..jobs import get_job_manager
from ..mt5 import get_bridge
from ..versions import DATA_SCHEMA_VERSION
from . import integrity, manifest, master, sessions, storage

log = logging.getLogger("data.engine")

TF_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}


def make_dataset_id(symbol: str, timeframe: str, start_ts: float, end_ts: float, source: str) -> str:
    s = datetime.fromtimestamp(start_ts, tz=timezone.utc).strftime("%Y%m%d")
    e = datetime.fromtimestamp(end_ts, tz=timezone.utc).strftime("%Y%m%d")
    return f"{symbol}_{timeframe}_{s}_{e}_{source}"


def make_master_dataset_id(symbol: str, timeframe: str, profile_key: str, version: int) -> str:
    safe_profile = profile_key.replace("|", "_").replace("/", "-")
    return f"{symbol}_{timeframe}_{safe_profile}_v{version}"


# V6.7 §5 — cross-call concurrency control and cancellation for history sync.
# One in-flight sync per (symbol, timeframe, profile): a second caller waits on
# the lock and then reuses the already-synced store (no duplicate requests, no
# duplicated work); every sync can be cancelled and always reaches a terminal
# state.
_SYNC_GUARD = threading.Lock()
_SYNC_LOCKS: Dict[tuple, threading.Lock] = {}
_SYNC_CANCELS: Dict[tuple, threading.Event] = {}


def _sync_key(symbol: str, timeframe: str, profile_key: str) -> tuple:
    return (str(symbol).upper(), str(timeframe).upper(), str(profile_key))


def _sync_lock(key: tuple) -> threading.Lock:
    with _SYNC_GUARD:
        return _SYNC_LOCKS.setdefault(key, threading.Lock())


def cancel_sync(symbol: str, timeframe: str, profile_key: str = "*") -> int:
    """Request cancellation of any in-flight sync for this pair. Returns the
    number of in-flight operations signalled."""
    n = 0
    with _SYNC_GUARD:
        for (sym, tf, prof), ev in _SYNC_CANCELS.items():
            if sym == str(symbol).upper() and tf == str(timeframe).upper() and \
                    (profile_key == "*" or prof == str(profile_key)):
                ev.set()
                n += 1
    return n


class DataEngine:
    def __init__(self):
        self._lock = threading.RLock()
        self._frames: Dict[str, pd.DataFrame] = {}   # in-memory dataset cache (dataset_id -> DataFrame)
        # V5: feature-artifact validation results keyed by (path, mtime, size) so
        # the eligibility contract stays cheap on the screening hot path.
        self._feat_status_lock = threading.RLock()
        self._feat_status_cache: Dict[tuple, tuple] = {}

    # ---------- master incremental sync (spec §4, §5, §6, §7) ----------
    def sync_master(self, symbol: str, timeframe: str, force_full: bool = False,
                    progress_cb=None, request_start: float | None = None,
                    request_end: float | None = None) -> Dict:
        """Sync the master dataset from the active bridge toward a GOAL range.

        V7.0 §2 — ``request_start``/``request_end`` (explicit UTC bounds, e.g.
        the operator's selected 1D..3Y duration) define the goal window: the
        sync fetches the genuinely-missing portions of that window (older head
        AND newer tail) instead of a fixed recent subset. Without a request the
        legacy config window (``data.history_months``) applies unchanged.
        Reads last stored timestamp, requests ONLY missing ranges, deduplicates
        by timestamp, discards identical duplicates, quarantines conflicts.
        """
        symbol, timeframe = str(symbol).upper(), str(timeframe).upper()
        if timeframe not in TF_MINUTES:
            return {"symbol": symbol, "timeframe": timeframe, "status": "invalid_range",
                    "error": "unsupported candle timeframe", "new_bars": 0, "total_bars": 0}
        cfg = get_config()
        bridge = get_bridge()
        profile = bridge.profile()
        mstore = master.get_master(symbol, timeframe, profile)

        tf_min = TF_MINUTES.get(timeframe, 15)
        step_s = tf_min * 60
        now = time.time()
        months = cfg.data.history_months.get(timeframe, 6)
        default_start_ts = now - months * 30 * 86400

        last_stored = mstore.last_ts
        first_stored = mstore.first_ts

        # Check if existing data was discovered in DATA_ROOT (spec §2, §3, §5)
        if (last_stored is None or mstore.rows == 0) and not force_full:
            from .discovery import get_discovery_engine
            disc = get_discovery_engine()
            reuse_dec = disc.check_dataset_reuse(symbol, timeframe, emit_logs=True)
            if reuse_dec.get("action") == "REUSE":
                ds_path = reuse_dec["dataset"]["path"]
                try:
                    df_existing = pd.read_parquet(ds_path)
                    if mstore.rows == 0:
                        mstore.append(df_existing)
                    # V7.0 §2.5 — a discovered dataset only closes the request
                    # when it covers the requested goal; otherwise fall through
                    # and fetch the genuinely missing portions.
                    if request_start is not None:
                        raise RuntimeError("discovered dataset does not cover the requested range")
                    did = self._materialize_dataset_view(mstore)
                    return {
                        "symbol": symbol, "timeframe": timeframe, "fetch_mode": "reuse_existing",
                        "new_bars": 0, "total_bars": len(df_existing), "version": mstore.version,
                        "dataset_id": did, "action": "REUSING EXISTING DATA",
                    }
                except Exception as e:
                    log.warning("failed to reuse discovered dataset %s: %s", ds_path, e)
            elif reuse_dec.get("action") == "EXTEND":
                ds_path = reuse_dec["dataset"]["path"]
                try:
                    df_existing = pd.read_parquet(ds_path)
                    if mstore.rows == 0:
                        mstore.append(df_existing)
                    last_stored = mstore.last_ts
                except Exception as e:
                    log.warning("failed to seed mstore from %s: %s", ds_path, e)

        is_initial = (last_stored is None or force_full or mstore.rows == 0)

        # V7.0 §2.5 — a cache hit only counts when the STORE COVERS THE GOAL:
        # tail freshness alone must never satisfy a longer-range request, and a
        # short cached history must never masquerade as a 2-year cache.
        goal_start_ts = float(request_start) if request_start is not None else float(default_start_ts)
        goal_end_ts = float(request_end) if request_end is not None else float(now)
        store_covers_goal = (
            first_stored is not None
            and float(first_stored) <= goal_start_ts + step_s * 2
            and float(last_stored) >= goal_end_ts - step_s * 2
        )

        # Check if data can be reused without network request
        if not is_initial and not force_full and (now - last_stored) < step_s \
                and request_start is None:
            reuse_msg = f"ACTION: REUSING EXISTING DATA for {symbol} {timeframe} ({mstore.rows:,} bars up to date)"
            log.info(reuse_msg)
            activity.info("DATA", reuse_msg)
            did = self._materialize_dataset_view(mstore)
            return {
                "symbol": symbol, "timeframe": timeframe, "fetch_mode": "reuse_existing",
                "new_bars": 0, "total_bars": mstore.rows, "version": mstore.version,
                "dataset_id": did, "action": "REUSING EXISTING DATA",
            }

        # V6.7 §5 — one sync per (symbol, timeframe, profile): concurrent
        # callers dedupe on the keyed lock and reuse the finished store.
        lock_key = _sync_key(symbol, timeframe, mstore.profile_key)
        lock = _sync_lock(lock_key)
        # Serialize writes per store, then evaluate THIS caller's goal again.
        # A shorter in-flight request cannot satisfy a different/longer range.
        waited = not lock.acquire(blocking=False)
        if waited:
            lock.acquire()
            mstore = master.get_master(symbol, timeframe, profile)
            if request_start is None:
                try:
                    did = self._materialize_dataset_view(mstore)
                    return {"symbol": symbol, "timeframe": timeframe,
                            "fetch_mode": "reuse_concurrent_sync", "new_bars": 0,
                            "total_bars": mstore.rows, "version": mstore.version,
                            "dataset_id": did, "action": "REUSING CONCURRENT SYNC"}
                finally:
                    lock.release()
            # Explicit callers re-evaluate their OWN goal against the union.
            force_full = False
        mstore = master.get_master(symbol, timeframe, profile)
        cancel_ev = threading.Event()
        with _SYNC_GUARD:
            _SYNC_CANCELS[lock_key] = cancel_ev
        try:
            return self._sync_master_locked(symbol, timeframe, force_full, mstore,
                                            cancel_ev, lock_key, progress_cb,
                                            request_start=request_start,
                                            request_end=request_end)
        finally:
            lock.release()
            with _SYNC_GUARD:
                _SYNC_CANCELS.pop(lock_key, None)

    def _sync_master_locked(self, symbol: str, timeframe: str, force_full: bool,
                            mstore, cancel_ev: threading.Event, lock_key: tuple,
                            progress_cb=None, request_start: float | None = None,
                            request_end: float | None = None) -> Dict:
        cfg = get_config()
        bridge = get_bridge()
        profile = bridge.profile()
        tf_min = TF_MINUTES.get(timeframe, 15)
        step_s = tf_min * 60
        now = time.time()
        months = cfg.data.history_months.get(timeframe, 6)
        default_start_ts = now - months * 30 * 86400
        last_stored = mstore.last_ts
        is_initial = (last_stored is None or force_full or mstore.rows == 0)

        # Multi-level job tracking (V2.7)
        jm = get_job_manager()
        job = jm.create_job(name=f"Sync {symbol} {timeframe}", job_type="ingest")
        job.start()
        job.add_stage("fetch", "Fetch Bars")
        job.add_stage("validate", "Validate Integrity")
        job.add_stage("normalize", "Normalize & Sessionize")
        job.add_stage("persist", "Persist & Index")

        # V7.0 §2 — the GOAL window is the REQUESTED range (explicit UTC bounds
        # from the duration selector / preflight), falling back to the legacy
        # config window only when no request was made. Missing HEAD history
        # (requested start before the store) and missing TAIL history are both
        # fetched; nothing is silently shrunken to a fixed recent subset.
        goal_start = float(request_start) if request_start is not None else float(default_start_ts)
        goal_end = float(request_end) if request_end is not None else float(now)
        if not (goal_end > goal_start):
            msg = f"requested range is empty for {symbol} {timeframe}: start must precede end"
            job.fail(msg)
            return {"symbol": symbol, "timeframe": timeframe, "status": "invalid_range",
                    "error": msg, "new_bars": 0, "total_bars": mstore.rows,
                    "version": mstore.version}

        fetch_intervals: list = []            # [(start_ts, end_ts), ...] genuinely missing
        first_stored = mstore.first_ts
        if is_initial or first_stored is None:
            fetch_intervals.append((goal_start, goal_end))
            fetch_mode = "requested_full_range" if request_start is not None else "initial_bootstrap"
        else:
            fetch_mode = "requested_range" if request_start is not None else "incremental_delta"
            # head gap: REQUESTED history older than anything stored. Only an
            # explicit request extends backward — the legacy no-request call
            # stays strictly incremental (V6.7 contract).
            if request_start is not None and goal_start < float(first_stored) - step_s:
                fetch_intervals.append((goal_start, float(first_stored) + step_s * 2))
            # tail gap: new bars after the store's last bar (2-bar overlap keeps
            # closed-bar continuity)
            if goal_end > float(last_stored) + step_s:
                fetch_intervals.append((max(float(last_stored) - step_s * 2, goal_start), goal_end))
            if fetch_intervals:
                log.info("ACTION: RANGE FETCH for %s %s: %d missing interval(s) of the requested range",
                         symbol, timeframe, len(fetch_intervals))

        from .history_range import coverage as range_coverage, missing_intervals
        cached_coverage = range_coverage(mstore, goal_start, goal_end, step_s)
        if request_start is not None and not force_full:
            fetch_intervals = missing_intervals(cached_coverage, goal_start, goal_end, step_s)
        if not fetch_intervals:
            did = self._materialize_dataset_view(mstore)
            job.complete({"dataset_id": did, "total_bars": mstore.rows})
            rep = {**cached_coverage, "requested_start": goal_start, "requested_end": goal_end,
                   "chunks": 0, "chunk_results": [], "bars_received": 0,
                   "persisted_rows": mstore.rows, "cache_source": "persisted_master",
                   "persisted_first_ts": mstore.first_ts, "persisted_last_ts": mstore.last_ts}
            return {"symbol": symbol, "timeframe": timeframe, "fetch_mode": "requested_range" if request_start is not None else "reuse_existing",
                    "new_bars": 0, "total_bars": mstore.rows, "dataset_id": did,
                    "status": cached_coverage["status"].lower(), "sync_status": cached_coverage["status"],
                    "fetch": rep, "partial_reason": cached_coverage["uncovered"][0]["reason"] if cached_coverage["uncovered"] else None}

        n_bars_needed = max(10, int((goal_end - goal_start) // step_s) + 5)
        job.add_task("fetch", "request_bars", "Request bars from bridge", total_units=n_bars_needed, unit_name="bars")

        # Fetch from bridge
        op_id = f"sync_{symbol.lower()}_{timeframe.lower()}"
        activity.started("DATA", f"Starting {symbol} {timeframe} synchronization ({fetch_mode})",
                         operation_id=op_id, progress=10.0)

        db = get_db()
        if not is_initial:
            last_dt_str = datetime.fromtimestamp(last_stored, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            activity.info("DATA", f"Checking {symbol} {timeframe}: last stored bar {last_dt_str}",
                          operation_id=op_id, progress=20.0)
        else:
            activity.info("DATA", f"Initial bootstrap for {symbol} {timeframe}",
                          operation_id=op_id, progress=20.0)

        # V6.7 §5 — chunked history fetch. ONE mega-request (the old
        # "requesting recent 259205 bars") blocks the bridge and freezes the
        # UI at a fake percentage; instead the range is walked in bounded
        # chunks with per-chunk bounded retries, REAL progress, cancellation,
        # and a wall-clock deadline. Every sync reaches a terminal state and
        # reports requested vs actually-received coverage — partial data is
        # labeled partial, never presented as complete.
        CHUNK_BARS = 15000                 # bars per bridge request (bounded)
        MAX_FETCH_SECONDS = 900            # overall deadline for the fetch stage
        chunk_span = max(step_s * 10, step_s * CHUNK_BARS)
        activity.running("DATA", f"Requesting historical data from {bridge.source} for {symbol} {timeframe}",
                         operation_id=op_id, progress=35.0)

        collected: list = []
        fetch_errors: list = []
        t_fetch0 = time.time()
        cancelled = False
        timed_out = False
        received_first_ts = None
        received_last_ts = None
        chunks_walked = 0
        chunk_results = []
        scheduled = set(fetch_intervals)
        MAX_CHUNKS = 512
        for (int_start, int_end) in fetch_intervals:
            cursor = float(int_start)
            consecutive_empty = 0
            while cursor < float(int_end):
                if cancel_ev.is_set():
                    cancelled = True
                    break
                if time.time() - t_fetch0 > MAX_FETCH_SECONDS or chunks_walked >= MAX_CHUNKS:
                    timed_out = True
                    break
                chunk_end = min(cursor + chunk_span, float(int_end))
                chunks_walked += 1
                chunk = None
                for attempt in range(3):            # bounded retries per chunk
                    try:
                        chunk = bridge.copy_rates_range(symbol, timeframe, cursor, chunk_end)
                        terminal = getattr(bridge, "last_history_result", {}) or {}
                        if terminal.get("status") == "error":
                            raise RuntimeError(str(terminal.get("error") or "terminal history error"))
                        break
                    except Exception as e:
                        fetch_errors.append(f"{type(e).__name__}: {e}"[:160])
                        if attempt == 2:
                            chunk = None
                        else:
                            time.sleep(min(2.0 * (attempt + 1), 5.0))
                raw_chunk = list(chunk or [])
                accepted_chunk, out_of_range = [], 0
                for bar in raw_chunk:
                    ts = float(bar.ts)
                    if cursor <= ts <= chunk_end and goal_start <= ts <= goal_end:
                        accepted_chunk.append(bar)
                    else:
                        out_of_range += 1
                chunk_results.append({"start": cursor, "end": chunk_end,
                    "raw_bars": len(raw_chunk), "accepted_bars": len(accepted_chunk),
                    "unique_bars": len({float(b.ts) for b in accepted_chunk}),
                    "out_of_range_bars": out_of_range, "attempts": attempt + 1,
                    "error": fetch_errors[-1] if chunk is None and fetch_errors else None,
                    "status": "received" if accepted_chunk else "error" if chunk is None else "empty",
                    "first_ts": min((b.ts for b in accepted_chunk), default=None),
                    "last_ts": max((b.ts for b in accepted_chunk), default=None)})
                collected.extend(accepted_chunk)
                if accepted_chunk:
                    b_first = min(float(b.ts) for b in accepted_chunk)
                    b_last = max(float(b.ts) for b in accepted_chunk)
                    received_first_ts = b_first if received_first_ts is None else min(received_first_ts, b_first)
                    received_last_ts = b_last if received_last_ts is None else max(received_last_ts, b_last)
                # A terminal may return a capped suffix/prefix for a large range.
                # Walk the unreturned portions too, rather than mistaking a
                # successful-but-truncated response for full chunk coverage.
                # Empty subranges terminate locally; global deadline/chunk cap
                # bounds this adaptive walk. Internal <=96h closures stay warnings.
                if accepted_chunk:
                    ts = sorted({float(b.ts) for b in accepted_chunk})
                    pending = []
                    if ts[0] > cursor + 2 * step_s:
                        pending.append((cursor, ts[0] - step_s))
                    if ts[-1] < chunk_end - 2 * step_s:
                        pending.append((ts[-1] + step_s, chunk_end))
                    pending.extend((a + step_s, b - step_s) for a,b in zip(ts,ts[1:]) if b-a > 96*3600)
                    for a,b in pending:
                        if b > a and (a,b) not in scheduled:
                            scheduled.add((a,b))
                            fetch_intervals.append((a,b))
                # Empty early history is not proof later chunks are unavailable.
                done_frac = min(1.0, (cursor - goal_start) / max(1.0, goal_end - goal_start))
                pct = 35.0 + 25.0 * done_frac
                activity.running("DATA",
                                 f"Fetching {symbol} {timeframe}: {len(collected):,} bars "
                                 f"({done_frac * 100:.0f}% of requested range)",
                                 operation_id=op_id, progress=pct)
                job.update_task_progress("fetch", "request_bars",
                                         int(done_frac * n_bars_needed), n_bars_needed)
                job.heartbeat(f"{len(collected):,} bars fetched in bounded chunks")
                if progress_cb is not None:
                    try:
                        progress_cb(pct, f"{len(collected):,} bars fetched in bounded chunks")
                    except Exception:
                        pass
                cursor = chunk_end
            if cancelled or timed_out:
                break

        bars = collected
        # Bounded legacy fallback: only when the range API produced NOTHING at
        # all, and never more than 20k recent bars (the old unbounded
        # copy_rates(n_bars_needed) call was the stall the user saw).
        if request_start is None and not bars and not cancelled and not timed_out:
            n_recent = min(n_bars_needed, 20000)
            activity.info("DATA", f"No range bars returned for {symbol} {timeframe}, requesting recent {n_recent} bars",
                          operation_id=op_id, progress=45.0)
            try:
                bars = list(bridge.copy_rates(symbol, timeframe, n_recent) or [])
            except Exception as e:
                fetch_errors.append(f"fallback copy_rates: {type(e).__name__}: {e}"[:160])
                bars = []
            if bars:
                received_first_ts = float(getattr(bars[0], "ts", None)
                                          or bars[0].to_dict().get("ts"))
                received_last_ts = float(getattr(bars[-1], "ts", None)
                                         or bars[-1].to_dict().get("ts"))

        # V7.0 §2.4 — requested vs actually-received coverage, per interval and
        # overall: a partially available terminal history is reported as PARTIAL
        # with the uncovered portion named, never silently shrunken or faked.
        uncovered = []
        if bars and not cancelled:
            for (int_start, int_end) in fetch_intervals:
                if received_first_ts is None:
                    uncovered.append({"start": float(int_start), "end": float(int_end),
                                      "reason": "bridge returned no bars for this interval"})
                    continue
                # uncovered = the parts of the interval outside the received span
                if float(int_start) < received_first_ts - step_s:
                    uncovered.append({"start": float(int_start),
                                      "end": float(received_first_ts),
                                      "reason": "no bars received for this portion"})
                if float(int_end) > received_last_ts + step_s:
                    uncovered.append({"start": float(received_last_ts),
                                      "end": float(int_end),
                                      "reason": "no bars received for this portion"})
        unique_ts = set()
        for b in bars:
            try:
                unique_ts.add(float(getattr(b, "ts", None) or b.to_dict().get("ts")))
            except Exception:
                pass
        fetch_report = {
            "requested_start": float(goal_start),
            "requested_end": float(goal_end),
            "received_start": received_first_ts,
            "received_end": received_last_ts,
            "bars_expected_estimate": int(n_bars_needed),
            # unique bars actually received — the count that persists; the raw
            # collected count (with boundary overlaps) is reported separately.
            "bars_received": len(unique_ts) if unique_ts else len(bars),
            "bars_collected_raw": len(bars),
            "chunks": int(chunks_walked),
            "chunk_results": chunk_results,
            "cache_source": "bridge_range + persisted_master",
            "intervals": [{"start": float(a), "end": float(b)} for (a, b) in fetch_intervals],
            "uncovered": uncovered,
            "errors": fetch_errors[-5:],
        }

        if cancelled:
            msg = f"Sync cancelled for {symbol} {timeframe} ({len(bars):,} bars fetched before cancel)"
            activity.warning("DATA", msg, operation_id=op_id)
            job.fail(msg)
            return {
                "symbol": symbol, "timeframe": timeframe, "fetch_mode": fetch_mode,
                "new_bars": 0, "total_bars": mstore.rows, "version": mstore.version,
                "status": "cancelled", "fetch": fetch_report,
            }

        if bars is None or len(bars) == 0:
            msg = f"No in-range bars returned for {symbol} {timeframe} from {bridge.source}"
            activity.warning("DATA", msg, operation_id=op_id)
            job.fail(msg)
            fetch_report.update(cached_coverage)
            fetch_report.update({"persisted_rows": mstore.rows,
                                 "partial_reason": "bridge error" if fetch_errors else "empty/unavailable requested range"})
            return {"symbol": symbol, "timeframe": timeframe, "fetch_mode": fetch_mode,
                    "new_bars": 0, "total_bars": mstore.rows, "version": mstore.version,
                    "status": "no_data_from_bridge", "sync_status": "PARTIAL", "fetch": fetch_report,
                    "partial_reason": fetch_report["partial_reason"]}

        job.update_task_progress("fetch", "request_bars", len(bars), max(len(bars), n_bars_needed))
        job.complete_stage("fetch")
        job.heartbeat(f"Received {len(bars):,} bars from {bridge.source}")

        activity.success("DATA", f"Received {len(bars):,} {timeframe} bars from {bridge.source}",
                         operation_id=op_id, progress=60.0)
        activity.running("DATA", f"Validating timestamps & data integrity for {symbol} {timeframe}",
                         operation_id=op_id, progress=75.0)

        df_raw = pd.DataFrame([b.to_dict() for b in bars]).sort_values("ts", kind="stable").reset_index(drop=True)

        # Filter simulator weekend bars if needed
        if bridge.source == "SIMULATOR":
            dt = pd.to_datetime(df_raw["ts"], unit="s", utc=True)
            df_raw = df_raw[dt.dt.dayofweek < 5].reset_index(drop=True)

        # 1. Data Integrity Validation & Quarantine (spec §7)
        job.add_task("validate", "integrity_check", "Check monotonicity and OHLC consistency", total_units=len(df_raw), unit_name="bars")
        quarantine_dir = P.symbol_dirs(symbol)["quarantine"]
        df_accepted, integrity_rep = integrity.validate_batch(
            df_raw, timeframe=timeframe, quarantine_dir=quarantine_dir,
            tag=f"{symbol}_{timeframe}_{bridge.source}"
        )
        job.update_task_progress("validate", "integrity_check", len(df_accepted), len(df_raw))
        job.complete_stage("validate")

        # Record integrity report in SQLite
        db.x("""INSERT INTO integrity_reports
                (ts, symbol, timeframe, profile, stage, report, quarantined, quarantine_path)
                VALUES (?,?,?,?,?,?,?,?)""",
             (time.time(), symbol, timeframe, mstore.profile_key, fetch_mode,
              str(integrity_rep.as_dict()), integrity_rep.rows_quarantined,
              integrity_rep.quarantine_path))

        if integrity_rep.rows_quarantined > 0:
            activity.warning("DATA",
                             f"Integrity check quarantined {integrity_rep.rows_quarantined} bars for {symbol} {timeframe}",
                             operation_id=op_id)
        else:
            activity.info("DATA", f"Data integrity check passed for {symbol} {timeframe}",
                          operation_id=op_id)

        if df_accepted.empty:
            did = self._materialize_dataset_view(mstore) if mstore.rows > 0 else ""
            activity.warning("DATA", f"All fetched bars quarantined/rejected for {symbol} {timeframe}",
                             operation_id=op_id)
            return {
                "symbol": symbol, "timeframe": timeframe, "fetch_mode": fetch_mode,
                "new_bars": 0, "total_bars": mstore.rows, "version": mstore.version,
                "dataset_id": did,
                "integrity": integrity_rep.as_dict(),
            }

        # 2. Normalize (DST sessions, numeric types, spread)
        activity.running("DATA", f"Normalizing & updating master cache for {symbol} {timeframe}",
                         operation_id=op_id, progress=85.0)
        job.add_task("normalize", "apply_sessions", "Label trading sessions with zoneinfo", total_units=len(df_accepted), unit_name="bars")
        df_norm = self._normalize(df_accepted)
        job.update_task_progress("normalize", "apply_sessions", len(df_norm), len(df_accepted))
        job.complete_stage("normalize")

        # 3. Master append with deduplication & conflict detection (spec §5)
        job.add_task("persist", "append_partitions", "Append and update master store", total_units=len(df_norm), unit_name="bars")
        append_rep = mstore.append(df_norm, source_info=profile)
        job.update_task_progress("persist", "append_partitions", append_rep["accepted"], len(df_norm))

        # Materialize / update latest dataset view in registry so pipeline can query it
        did = self._materialize_dataset_view(mstore)
        job.complete_stage("persist")
        job.complete({
            "symbol": symbol, "timeframe": timeframe, "new_bars": append_rep["accepted"],
            "total_bars": mstore.rows, "dataset_id": did,
        })

        activity.success("DATA",
                         f"{symbol} {timeframe} synchronization complete: "
                         f"{append_rep['accepted']} new bars appended (total: {mstore.rows:,})",
                         operation_id=op_id, progress=100.0)

        # V6.7 §5.5 + V7.0 §2.4 — requested vs actual coverage decides the
        # terminal label: COMPLETE only when the PERSISTED bars cover the goal
        # window; otherwise PARTIAL with the uncovered portion named, so the UI
        # never presents short data as full.
        persisted = range_coverage(mstore, goal_start, goal_end, step_s)
        fetch_report.update(persisted)
        sync_status = "PARTIAL" if timed_out else persisted["status"]
        partial_reason = ("fetch deadline or bounded chunk limit reached — remaining range not downloaded" if timed_out
                          else persisted["uncovered"][0]["reason"] if persisted["uncovered"] else None)
        fetch_report.update({
            "status": sync_status, "partial_reason": partial_reason,
            "persisted_first_ts": mstore.first_ts,
            "persisted_last_ts": mstore.last_ts,
            "persisted_rows": mstore.rows,
        })
        return {
            "symbol": symbol, "timeframe": timeframe, "fetch_mode": fetch_mode,
            "new_bars": append_rep["accepted"],
            "duplicates_identical": append_rep["duplicates_identical"],
            "conflicts": append_rep["conflicts"],
            "total_bars": mstore.rows,
            "master_version": mstore.version,
            "dataset_id": did,
            "integrity": integrity_rep.as_dict(),
            "status": "complete" if sync_status == "COMPLETE" else "partial",
            "sync_status": sync_status,
            "partial_reason": partial_reason,
            "fetch": fetch_report,
        }

    def _materialize_dataset_view(self, mstore: master.MasterStore) -> str:
        """Create/update a view dataset in `datasets` table pointing to the master store."""
        if mstore.rows == 0:
            return ""

        cfg = get_config()
        did = make_master_dataset_id(mstore.symbol, mstore.timeframe,
                                     mstore.profile_key, mstore.version)
        db = get_db()
        existing = db.one("SELECT id, bars FROM datasets WHERE id=?", (did,))
        if existing:
            return did

        # Write dataset snapshot to cache_dir so existing fast paths / worker pools
        # can read single parquet files with zero architectural break
        df = mstore.read_range()
        p = storage.write_bars(cfg.data.cache_dir, did, df)

        source_name = mstore.profile.get("source", "UNKNOWN")

        # Spec V2.8 Isolation: Simulator data must NEVER enter DATA/MT5/XAUUSD
        if source_name == "SIMULATOR" or "SIMULATOR" in str(did).upper():
            try:
                sim_sym_dir = P.SIMULATOR_DATA_DIR / mstore.symbol
                sim_sym_dir.mkdir(parents=True, exist_ok=True)
                sim_path = sim_sym_dir / f"{mstore.timeframe}.parquet"
                df.to_parquet(sim_path, index=False)
                log.info("[DATA ISOLATION] Simulator dataset saved to TEST_DATA: %s", sim_path)
            except Exception as e:
                log.warning("Failed to write simulator dataset to TEST_DATA: %s", e)
        else:
            # V2.8 Authoritative Real Data: DATA/MT5/{symbol}/{timeframe}.parquet
            try:
                sym_dir = P.MT5_DIR / mstore.symbol
                sym_dir.mkdir(parents=True, exist_ok=True)
                sym_tf_path = sym_dir / f"{mstore.timeframe}.parquet"
                df.to_parquet(sym_tf_path, index=False)

                raw_path = P.MT5_RAW_DIR / f"{mstore.symbol}_{mstore.timeframe}.parquet"
                norm_path = P.MT5_NORMALIZED_DIR / f"{mstore.symbol}_{mstore.timeframe}.parquet"
                df.to_parquet(raw_path, index=False)
                df.to_parquet(norm_path, index=False)
                manifest.update_dataset_entry(f"{mstore.symbol}_{mstore.timeframe}", {
                    "symbol": mstore.symbol,
                    "timeframe": mstore.timeframe,
                    "dataset_id": did,
                    "first_ts": float(mstore.first_ts or 0),
                    "last_ts": float(mstore.last_ts or 0),
                    "start_time": datetime.fromtimestamp(float(mstore.first_ts or 0), tz=timezone.utc).isoformat(),
                    "end_time": datetime.fromtimestamp(float(mstore.last_ts or 0), tz=timezone.utc).isoformat(),
                    "rows": len(df),
                    "source": source_name,
                    "broker": mstore.profile.get("broker", "UNKNOWN"),
                    "server": mstore.profile.get("server", "UNKNOWN"),
                    "raw_path": P.data_relative_path(sym_tf_path),
                    "normalized_path": P.data_relative_path(norm_path),
                    "file_size_bytes": sym_tf_path.stat().st_size if sym_tf_path.exists() else 0,
                })
            except Exception as e:
                log.warning("Failed to write V2.8 MT5 authoritative parquet: %s", e)

        db.x("""INSERT OR REPLACE INTO datasets
                (id, symbol, timeframe, start_ts, end_ts, bars, source, path,
                 feature_cache, created_at, kind, master_profile, dataset_version, fingerprint)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
             (did, mstore.symbol, mstore.timeframe, float(mstore.first_ts or 0),
              float(mstore.last_ts or 0), len(df), source_name,
              str(p), "[]", time.time(), "master_view", mstore.profile_key,
              mstore.version, mstore.fingerprint))

        with self._lock:
            self._frames[did] = df

        return did

    # ---------- legacy & backward-compatible ingest ----------
    def ingest(self, symbol: str, timeframe: str, months: Optional[float] = None,
               force: bool = False) -> Dict:
        """Unified ingest method: delegates to incremental master sync."""
        rep = self.sync_master(symbol, timeframe, force_full=force)
        did = rep.get("dataset_id")
        bridge = get_bridge()
        if not did:
            # Fallback ONLY to existing dataset matching active bridge mode (NEVER silently substitute SIMULATOR when real)
            latest = self.latest_dataset(symbol, timeframe, require_real=(not bridge.is_simulated))
            if latest:
                did = latest["id"]
        return {
            "dataset_id": did,
            "cached": rep.get("new_bars", 0) == 0,
            "bars": rep.get("total_bars", 0),
            "source": bridge.source,
            "new_bars": rep.get("new_bars", 0),
            "master_version": rep.get("master_version", 0),
            "error": rep.get("error"),
        }

    def ingest_default(self) -> List[Dict]:
        """Incremental synchronization for all configured symbols & timeframes."""
        cfg = get_config()
        bridge = get_bridge()
        from ..activity import activity
        out = []
        activity.started("DATA", f"Starting data synchronization for {cfg.data.symbol} ({bridge.source})",
                         operation_id=f"sync_{cfg.data.symbol.lower()}_all")

        for tf in cfg.data.timeframes:
            op_id = f"sync_{cfg.data.symbol.lower()}_{tf.lower()}"
            try:
                rep = self.sync_master(cfg.data.symbol, tf)
                out.append(rep)
            except Exception as e:
                log.error("sync %s %s failed: %s", cfg.data.symbol, tf, e, exc_info=True)
                activity.failed(
                    "DATA",
                    f"{cfg.data.symbol} {tf} synchronization failed",
                    operation_id=op_id,
                    reason=str(e),
                    platform=f"MT5 REAL ({bridge.source})" if not bridge.is_simulated else "SIMULATOR",
                    next_action="Synchronization stopped for this timeframe.",
                )
                out.append({"dataset_id": None, "symbol": cfg.data.symbol, "timeframe": tf,
                            "error": str(e)})

        succeeded = sum(1 for r in out if r.get("dataset_id"))
        failed = len(out) - succeeded
        if failed == 0:
            activity.success("DATA", f"Market data synchronization complete: all {len(out)} timeframes synchronized ({bridge.source})",
                             operation_id=f"sync_{cfg.data.symbol.lower()}_all", progress=100.0)
        else:
            activity.warning("DATA", f"Market data synchronization finished with {failed} failure(s) out of {len(out)} timeframes ({bridge.source})",
                             operation_id=f"sync_{cfg.data.symbol.lower()}_all")
        return out

    # ---------- normalization (DST sessions, numeric types, spread) ----------
    def _normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        work = df.copy()
        ts_arr = work["ts"].to_numpy(dtype=np.float64)
        dt = pd.to_datetime(ts_arr, unit="s", utc=True)
        work["hour"] = dt.hour.astype(np.int8)
        work["minute"] = dt.minute.astype(np.int8)
        work["dow"] = dt.dayofweek.astype(np.int8)      # 0=Mon
        work["dom"] = dt.day.astype(np.int8)

        # DST-aware session labeling (spec §43)
        work["session"] = sessions.session_labels(ts_arr).astype(str)

        for c in ("open", "high", "low", "close", "bid", "ask"):
            if c in work.columns:
                work[c] = pd.to_numeric(work[c], errors="coerce").astype(np.float64)
            else:
                work[c] = np.nan

        # Bid/Ask synthesis if missing
        if work["bid"].isna().all() and not work["close"].isna().all():
            work["bid"] = work["close"]
        if work["ask"].isna().all() and not work["close"].isna().all():
            work["ask"] = work["close"]

        if "spread" not in work or work["spread"].isna().any():
            work["spread"] = ((work["ask"] - work["bid"]) / 0.01).fillna(18.0)

        if "tick_volume" not in work:
            work["tick_volume"] = 0
        work["tick_volume"] = work["tick_volume"].fillna(0).astype(np.int32)

        return work[storage.COLUMNS]

    # ---------- access & retrieval ----------
    def get_frame(self, dataset_id: str) -> pd.DataFrame:
        """Retrieve dataset DataFrame, checking memory -> disk -> master store -> legacy."""
        with self._lock:
            if dataset_id in self._frames:
                df = self._frames[dataset_id]
                df["session"] = sessions.session_labels(df["ts"].to_numpy())
                return df

        cfg = get_config()
        # 1. Try V2.8 XAUUSD directory, then V2.7 raw / datasets directory
        df = None
        parts = dataset_id.split("_")
        if len(parts) >= 2:
            sym, tf = parts[0], parts[1]
            if sym.upper() == "XAUUSD":
                xau_path = P.MT5_XAUUSD_DIR / f"{tf}.parquet"
                if xau_path.exists() and xau_path.stat().st_size > 0:
                    try:
                        df = pd.read_parquet(xau_path)
                    except Exception:
                        df = None
            if df is None:
                mt5_raw = P.MT5_RAW_DIR / f"{sym}_{tf}.parquet"
                if mt5_raw.exists():
                    try:
                        df = pd.read_parquet(mt5_raw)
                    except Exception:
                        df = None

        # 2. Try storage (checks cache_dir + DATA/.../legacy/)
        if df is None:
            df = storage.read_dataset(cfg.data.cache_dir, dataset_id)

        # 3. If not found as a snapshot file, check database registry and fallback artifact paths
        if df is None:
            artifact_path = self.find_dataset_artifact_path(dataset_id)
            if artifact_path and artifact_path.exists() and artifact_path.stat().st_size > 0:
                try:
                    df = pd.read_parquet(artifact_path)
                except Exception:
                    df = None

        if df is None:
            db = get_db()
            row = db.one("SELECT * FROM datasets WHERE id=?", (dataset_id,))
            if row and row["path"] and Path(row["path"]).exists():
                df = pd.read_parquet(row["path"])

        # 4. If still not found, check master datasets
        if df is None:
            parts = dataset_id.split("_")
            if len(parts) >= 2:
                sym, tf = parts[0], parts[1]
                bridge = get_bridge()
                mstore = master.get_master(sym, tf, bridge.profile())
                if mstore.rows > 0:
                    df = mstore.read_range()

        if df is None:
            raise KeyError(f"dataset not found on disk or master store: {dataset_id}")

        msg = f"ACTION: REUSING EXISTING DATA for {dataset_id} ({len(df):,} bars)"
        log.info(msg)
        activity.info("DATA", msg)

        # Derived session columns are recomputed at load time (spec §43)
        df["session"] = sessions.session_labels(df["ts"].to_numpy())
        with self._lock:
            if len(self._frames) > 12:
                self._frames.clear()
            self._frames[dataset_id] = df
        return df

    def find_dataset_artifact_path(self, dataset_id: str) -> Optional[Path]:
        """Locate physical parquet artifact on disk for a dataset ID without loading it."""
        cfg = get_config()
        parts = dataset_id.split("_")
        if len(parts) >= 2:
            sym, tf = parts[0], parts[1]
            if sym.upper() == "XAUUSD":
                xau_path = P.MT5_XAUUSD_DIR / f"{tf}.parquet"
                if xau_path.exists() and xau_path.stat().st_size > 0:
                    return xau_path
            mt5_raw = P.MT5_RAW_DIR / f"{sym}_{tf}.parquet"
            if mt5_raw.exists() and mt5_raw.stat().st_size > 0:
                return mt5_raw

        storage_path = storage.dataset_path(cfg.data.cache_dir, dataset_id)
        if storage_path.exists() and storage_path.stat().st_size > 0:
            return storage_path

        db = get_db()
        row = db.one("SELECT path FROM datasets WHERE id=?", (dataset_id,))
        if row and row["path"]:
            p = Path(row["path"])
            if p.exists() and p.stat().st_size > 0:
                return p
            # Check relative to DATA_ROOT or DATA_CACHE_DIR only if filename matches dataset_id
            if p.name == f"{dataset_id}.parquet":
                p_alt = P.DATA_CACHE_DIR / p.name
                if p_alt.exists() and p_alt.stat().st_size > 0:
                    return p_alt
                if len(parts) >= 1:
                    p_sim = P.SIMULATOR_DATA_DIR / parts[0] / p.name
                    if p_sim.exists() and p_sim.stat().st_size > 0:
                        return p_sim
                    p_test = P.TEST_DATA_DIR / "SIMULATOR_DATA" / parts[0] / p.name
                    if p_test.exists() and p_test.stat().st_size > 0:
                        return p_test
            return None

        # Check DATA_CACHE_DIR fallback
        cache_fallback = P.DATA_CACHE_DIR / f"{dataset_id}.parquet"
        if cache_fallback.exists() and cache_fallback.stat().st_size > 0:
            return cache_fallback

        # Check SIMULATOR_DATA_DIR / TEST_DATA fallback
        if len(parts) >= 1:
            sym_clean = parts[0]
            sim_path = P.SIMULATOR_DATA_DIR / sym_clean / f"{dataset_id}.parquet"
            if sim_path.exists() and sim_path.stat().st_size > 0:
                return sim_path
            test_path = P.TEST_DATA_DIR / "SIMULATOR_DATA" / sym_clean / f"{dataset_id}.parquet"
            if test_path.exists() and test_path.stat().st_size > 0:
                return test_path

        return None

    def is_dataset_eligible_for_research(
        self,
        dataset_id: str,
        require_features: bool = True
    ) -> Tuple[bool, str]:
        """Dataset Eligibility Contract (Spec V2.71):
        A dataset may enter research only when the actual runtime can prove:
        1. Physical dataset artifact exists in DATA_ROOT and has size > 0.
        2. Can be loaded into DataFrame without error.
        3. Schema has valid OHLC price and time series.
        4. Row count > 0 (meets minimum bar threshold).
        5. Source/type compatible with current research run (no simulator in MT5 REAL mode).
        6. Feature artifact exists and is valid on disk when require_features is True.
        """
        path = self.find_dataset_artifact_path(dataset_id)
        if not path:
            return False, f"Physical dataset artifact not found on disk: {dataset_id}"

        bridge = get_bridge()
        if not bridge.is_simulated and "SIMULATOR" in dataset_id.upper():
            return False, f"Dataset source SIMULATOR incompatible with active MT5 REAL bridge"

        try:
            df = pd.read_parquet(path)
            if len(df) == 0:
                return False, f"Dataset parquet on disk has 0 rows: {dataset_id}"
            cols = set(df.columns)
            has_price = bool({"close", "price", "bid", "ask"} & cols)
            has_time = bool({"ts", "time", "date"} & cols)
            if not has_price or not has_time:
                return False, f"Dataset schema invalid (missing price or time columns): {dataset_id}"
        except Exception as e:
            return False, f"Failed to load dataset artifact from disk: {dataset_id} ({e})"

        if require_features:
            # V5: the feature cache is a DERIVED accelerator. The engine computes
            # any missing indicator family on demand from this very dataset
            # frame and caches the result, so an absent artifact is not a reason
            # to exclude real market data from research. A present-but-corrupt
            # artifact is likewise rebuilt rather than allowed to block the run
            # (its state is reported through dataset_diagnostics()).
            feat_state, feat_reason, _feat_path = self.feature_artifact_status(
                dataset_id, dataset_rows=len(df))
            if feat_state == "corrupt":
                log.warning("[DATASET ELIGIBILITY] %s: %s - the feature cache will be rebuilt", dataset_id, feat_reason)

        return True, "ELIGIBLE"

    def feature_artifact_status(self, dataset_id: str, dataset_rows: Optional[int] = None
                                ) -> Tuple[str, str, Optional[Path]]:
        """V5: validate a *derived* feature artifact against its own contract.

        The feature cache is an accelerator: it holds whichever engineered
        feature columns were computed for a dataset (``momentum:10``,
        ``prev_day_high``, ``regime:trending`` ...). The canonical OHLC lives in
        the dataset artifact, which the caller validates separately.

        The previous contract demanded a ``price``/``close`` column *inside the
        feature cache*. The feature engine itself rewrites that file with only
        the computed columns, so the cache invalidated the dataset it belonged
        to: the first candidates of a run screened fine and every candidate after
        the first cache write was rejected as "no eligible dataset" — which the
        pipeline then recorded as a failed *strategy*.

        The contract is now three-way, because the artifact is *derived*:
          * ``present`` — an artifact exists, parses, has columns/rows and, when it
            carries price/close, is row-aligned with the dataset;
          * ``absent``  — no artifact; the feature engine computes and caches the
            indicators on demand, so research may proceed;
          * ``corrupt`` — an artifact exists but cannot be used; it is rebuilt on
            demand and reported as a data-corruption diagnostic.

        Only ``corrupt`` is an infrastructure *condition*; none of the three ever
        turns a strategy into a failed strategy.
        """
        cfg = get_config()
        # V5: resolve the artifact under the SAME name the writer uses. The writer
        # (FeatureEngine._persist) builds "<id>__<feature_set>__<schema_version>"
        # from its own constants; the gate used to hard-code a different schema
        # version, so a freshly written artifact could never be found again. The
        # canonical name is imported lazily (function scope) to avoid an import
        # cycle between the data and feature engines; legacy names stay as fallbacks.
        try:
            from ..features.engine import DEFAULT_FEATURE_SET_ID as _FS
            from ..features.engine import DEFAULT_SCHEMA_VERSION as _SV
            canonical = P.FEATURES_DIR / f"{dataset_id}__{_FS}__{_SV}.parquet"
        except Exception:  # pragma: no cover - defensive
            canonical = P.FEATURES_DIR / f"{dataset_id}__core_v1__core_v1.parquet"
        candidates = (
            canonical,
            P.FEATURES_DIR / f"{dataset_id}__core_v1__core_v1.parquet",
            P.FEATURES_DIR / f"{dataset_id}__core_v1__schema_v2.parquet",
            P.FEATURES_DIR / f"{dataset_id}.parquet",
            Path(cfg.data.cache_dir) / "features" / f"{dataset_id}.parquet",
        )
        target_feat = None
        for fp in candidates:
            try:
                if fp.exists() and fp.stat().st_size > 0:
                    target_feat = fp
                    break
            except OSError:
                continue

        if not target_feat:
            return "absent", f"Feature artifact not cached for dataset: {dataset_id}", None

        try:
            stat = target_feat.stat()
            key = (str(target_feat), int(stat.st_mtime), int(stat.st_size))
        except OSError:
            return "corrupt", f"Feature artifact is not readable from disk: {dataset_id}", target_feat

        with self._feat_status_lock:
            cached = self._feat_status_cache.get(key)
        if cached is not None:
            state, reason, _rows = cached
            return state, reason, target_feat

        try:
            df_f = pd.read_parquet(target_feat)
        except Exception as e:
            reason = f"Feature artifact unreadable for {dataset_id}: {e}"
            self._remember_feature_status(key, "corrupt", reason, 0)
            return "corrupt", reason, target_feat

        if len(df_f) == 0:
            reason = f"Feature artifact has 0 rows: {dataset_id}"
            self._remember_feature_status(key, "corrupt", reason, 0)
            return "corrupt", reason, target_feat
        if len(df_f.columns) == 0:
            reason = f"Feature artifact has no columns: {dataset_id}"
            self._remember_feature_status(key, "corrupt", reason, len(df_f))
            return "corrupt", reason, target_feat

        f_cols = set(df_f.columns)
        if ({"price", "close"} & f_cols) and dataset_rows and len(df_f) != dataset_rows:
            reason = (f"Feature artifact misaligned with dataset {dataset_id} "
                      f"({len(df_f)} rows vs {dataset_rows} dataset rows)")
            self._remember_feature_status(key, "corrupt", reason, len(df_f))
            return "corrupt", reason, target_feat

        self._remember_feature_status(key, "present", "cached feature artifact is readable", len(df_f))
        return "present", f"cached feature artifact is readable ({len(df_f.columns)} columns, {len(df_f)} rows)", target_feat

    def _remember_feature_status(self, key, state: str, reason: str, rows: int) -> None:
        with self._feat_status_lock:
            if len(self._feat_status_cache) > 512:
                self._feat_status_cache.clear()
            self._feat_status_cache[key] = (state, reason, rows)

    def get_available_timeframes(self, symbol: str = "XAUUSD") -> List[str]:
        """Return list of timeframes that physically exist and are research-eligible."""
        eligible_tfs = []
        for tf in ("M1", "M5", "M15", "M30", "H1"):
            ds = self.latest_dataset(symbol, tf, require_eligible=True)
            if ds is not None:
                eligible_tfs.append(tf)
        tfs = getattr(get_config().data, "timeframes", ["M15"])
        return eligible_tfs or (tfs if isinstance(tfs, list) else [tfs]) or ["M15"]

    def latest_dataset(
        self,
        symbol: str,
        timeframe: str,
        source: Optional[str] = None,
        require_real: bool = False,
        require_eligible: bool = True
    ) -> Optional[Dict]:
        """Find the latest dataset (master view or legacy) in the database.
        When require_eligible is True (default), enforces the V2.71 Dataset Eligibility Contract:
        verifies that the physical artifact actually exists on disk and is valid.
        Stale database references missing on disk are rejected and ignored.
        """
        db = get_db()
        bridge = get_bridge()
        filter_real = require_real or (not bridge.is_simulated)

        # 1. Authoritative check: MT5_XAUUSD_DIR for XAUUSD
        if symbol.upper() == "XAUUSD":
            xau_path = P.MT5_XAUUSD_DIR / f"{timeframe}.parquet"
            if xau_path.exists() and xau_path.stat().st_size > 0:
                ds_id = f"XAUUSD_{timeframe}"
                is_elig, _ = self.is_dataset_eligible_for_research(ds_id, require_features=False)
                if is_elig:
                    return {
                        "id": ds_id,
                        "symbol": "XAUUSD",
                        "timeframe": timeframe,
                        "source": "MT5" if filter_real else "SIMULATOR",
                        "kind": "master_view",
                        "path": str(xau_path),
                        "created_at": xau_path.stat().st_mtime,
                        "fingerprint": "VALID",
                        "dataset_version": 1,
                    }

        # 2. Database queries
        candidates = []
        if source:
            rows = db.q(
                """SELECT * FROM datasets
                   WHERE symbol=? AND timeframe=? AND source=?
                   ORDER BY created_at DESC""",
                (symbol, timeframe, source))
            candidates.extend(rows)

        if filter_real:
            rows = db.q(
                """SELECT * FROM datasets
                   WHERE symbol=? AND timeframe=? AND source!='SIMULATOR'
                   ORDER BY created_at DESC""",
                (symbol, timeframe))
            candidates.extend(rows)
            if not require_real and not candidates:
                rows = db.q(
                    """SELECT * FROM datasets
                       WHERE symbol=? AND timeframe=?
                       ORDER BY created_at DESC""",
                    (symbol, timeframe))
                candidates.extend(rows)
        else:
            rows = db.q(
                """SELECT * FROM datasets
                   WHERE symbol=? AND timeframe=?
                   ORDER BY created_at DESC""",
                (symbol, timeframe))
            candidates.extend(rows)

        # 3. Filter candidates through Dataset Eligibility Contract
        seen = set()
        for c in candidates:
            cid = c["id"]
            if cid in seen:
                continue
            seen.add(cid)
            if require_eligible:
                is_elig, reason = self.is_dataset_eligible_for_research(cid, require_features=False)
                if not is_elig:
                    log.debug("[DATASET ELIGIBILITY] Stale/missing DB dataset %s rejected: %s", cid, reason)
                    continue
            return c

        return None

    def list_datasets(self) -> List[Dict]:
        db = get_db()
        rows = db.q("SELECT * FROM datasets ORDER BY symbol, timeframe, created_at DESC")
        if not rows:
            from .discovery import get_discovery_engine
            disc = get_discovery_engine()
            disc.reconcile_with_database(db=db, emit_logs=False)
            rows = db.q("SELECT * FROM datasets ORDER BY symbol, timeframe, created_at DESC")
        for r in rows:
            p = storage.dataset_path(get_config().data.cache_dir, r["id"])
            r["on_disk"] = p.exists() or (r.get("path") and Path(r["path"]).exists())
        return rows

    def train_test_split(self, dataset_id: str) -> Dict[str, slice]:
        df = self.get_frame(dataset_id)
        n = len(df)
        cut = int(n * get_config().backtest.train_fraction)
        return {"train": slice(0, cut), "test": slice(cut, n), "cut": cut, "n": n}


_engine: Optional[DataEngine] = None


def get_data_engine() -> DataEngine:
    global _engine
    if _engine is None:
        _engine = DataEngine()
    return _engine
