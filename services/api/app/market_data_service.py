"""Historical market data: availability view + incremental MT5 fetch.

Reads/writes the canonical store (hero1_market_data) under
<DATA_ROOT>/MARKET_DATA/ - the same database and manifest the CSV importer
produces. Fetch rules (binding):
  * the store's canonical timestamp is UTC epoch seconds; ranges are
    requested from the terminal with timezone-aware UTC datetimes built
    directly from those epochs - NO broker/server offset is applied, and no
    offset is ever derived from a live tick;
  * only COMPLETED candles are appended (ts + step <= current UTC time);
  * identical overlaps are duplicates; overlaps differing only in
    volume/spread are representation differences (also duplicates);
    conflicting overlaps (price/tick-volume differ) are ISOLATED - recorded
    with field-level diffs, the stored candle is preserved, and genuinely
    new candles still append (timeframe reports PARTIAL, never a false
    UPDATED and never a whole-timeframe abort);
  * invalid candles are rejected and counted, never repaired;
  * one timeframe's failure never discards another timeframe's success;
  * nothing is reported SUCCESS unless the terminal actually answered.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time as _time
import traceback
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from hero1_market_data import store as md
from hero1_mt5_adapter import connection as mt5_connection
from hero1_storage.engine import data_root

MAX_BACKFILL_CANDLES = 100_000  # mirrors the MT export cap for a first fetch


class FetchError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 503):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def store_db_path() -> Path:
    return data_root() / "MARKET_DATA" / "market_data.db"


def manifest_path() -> Path:
    return store_db_path().parent / "import_manifest.json"


def imports_dir() -> Path:
    """Permanent baseline CSV location inside the active DATA root."""
    return store_db_path().parent / "IMPORTS"


def ensure_baseline(symbols=("XAUUSD",)) -> Dict[str, Any]:
    """Import the permanent baseline CSVs into the canonical store.

    The supplied historical CSVs live at
    <DATA_ROOT>/MARKET_DATA/IMPORTS/<SYMBOL>/*.csv inside the project (the
    same files tracked in git), so EVERY installation - LMS and Windows -
    builds the same baseline in its own canonical database on startup.
    Idempotent (identical sources skip by SHA-256), append-preserving, and
    never fatal: failures are reported, existing data is untouched.
    """
    db = store_db_path()
    report: Dict[str, Any] = {"store": str(db), "imported": 0, "skipped": 0,
                              "rejected": 0, "files": []}
    try:
        wanted = {str(s).strip().upper() for s in symbols}
        csvs = [p for p in sorted(imports_dir().glob("*/*.csv"))
                if p.parent.name.upper() in wanted] if imports_dir().exists() else []
        if not csvs:
            report["reason"] = f"no permanent baseline CSVs under {imports_dir()}"
            return report
        import importlib.util
        script = Path(__file__).resolve().parents[3] / "scripts" / "import_market_data.py"
        spec = importlib.util.spec_from_file_location("hero1_import_market_data", script)
        if spec is None or spec.loader is None:
            report["error"] = f"cannot load importer: {script}"
            return report
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for csv_path in csvs:
            instrument, timeframe = mod._resolve(csv_path, None, None)
            res = mod.import_file(csv_path, instrument=instrument, timeframe=timeframe,
                                  db_path=db, force=False, dry_run=False)
            action = str(res.get("action", ""))
            report["files"].append({"file": csv_path.name, "instrument": instrument,
                                    "timeframe": timeframe, "action": action,
                                    "rows": res.get("row_count")})
            if action.startswith("SKIPPED"):
                report["skipped"] += 1
            elif res.get("status") == "AVAILABLE":
                report["imported"] += 1
            else:
                report["rejected"] += 1
    except Exception as exc:  # startup must survive a baseline problem
        report["error"] = f"{type(exc).__name__}: {exc}"
    return report


def new_operation_id() -> str:
    """Unique, sortable fetch operation id, e.g. FETCH-20261107-084512-9F3AC1."""
    import secrets
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"FETCH-{stamp}-{secrets.token_hex(3).upper()}"


def _op_event(operation_id: str, stage: str, *, severity: str = "INFO",
              instrument: str | None = None, timeframe: str | None = None, **ctx: Any) -> None:
    """Best-effort persistent lifecycle event (registry event store).

    Never raises and never logs secrets: a logging failure must not break a
    fetch, and a fetch failure must still be visible in the LOG tab.
    """
    _op_touch(operation_id, stage=stage)
    try:
        from hero1_eventbus import get_event_publisher
        get_event_publisher().publish(
            category="SYSTEM", severity=severity, subsystem="market-data",
            message=(f"[{operation_id}] {stage}" + (f" ({timeframe})" if timeframe else "")),
            instrument=instrument,
            payload={"operation_id": operation_id, "stage": stage,
                     "timeframe": timeframe, **ctx})
    except Exception:  # noqa: BLE001 - logging must never sink the operation
        pass


def _op_exception(operation_id: str, stage: str, exc: BaseException, *,
                  instrument: str | None = None, timeframe: str | None = None,
                  **ctx: Any) -> None:
    """Record a failure with its FULL stack trace in the persistent log.

    The LOG tab must expose the actual backend exception - a generic
    "Internal Server Error" is never the only evidence (ISSUE-052).
    """
    _op_event(operation_id, stage, severity="ERROR", instrument=instrument,
              timeframe=timeframe, exception_type=type(exc).__name__,
              exception_message=str(exc), traceback=traceback.format_exc()[-6000:], **ctx)


# --------------------------------------------------------------------------
# Fetch operation registry: one RUNNING operation per instrument, executed
# on a background thread so a slow/stuck terminal call can never wedge the
# HTTP request (or the UI) again. State is queryable so reopening the DATA
# tab resumes the SAME operation instead of starting a duplicate.
# --------------------------------------------------------------------------
_OPS: Dict[str, Dict[str, Any]] = {}
_OPS_LOCK = threading.Lock()


def _op_touch(operation_id: str, **kw: Any) -> None:
    with _OPS_LOCK:
        rec = _OPS.get(operation_id)
        if rec is not None:
            rec.update(kw)


def _tf_timeout() -> float:
    """Per-timeframe MT5 history-call timeout (seconds)."""
    try:
        return float(os.environ.get("HERO1_FETCH_TF_TIMEOUT_S", "180"))
    except ValueError:
        return 180.0


def start_fetch(instrument: str, timeframes: List[str],
                operation_id: Optional[str] = None) -> Dict[str, Any]:
    """Start (or return the already-running) fetch operation for an instrument."""
    sym = instrument.strip().upper()
    with _OPS_LOCK:
        for rec in _OPS.values():
            if rec["instrument"] == sym and rec["state"] in ("QUEUED", "RUNNING"):
                return {"operation_id": rec["operation_id"], "state": rec["state"],
                        "instrument": sym, "timeframes": list(rec["timeframes"]),
                        "existing": True, "started_at": rec["started_at"]}
        op_id = (operation_id or "").strip() or new_operation_id()
        rec = {"operation_id": op_id, "instrument": sym,
               "timeframes": list(timeframes), "state": "QUEUED",
               "stage": "OPERATION_CREATED", "started_at": _utc_now_epoch(),
               "finished_at": None, "report": None, "error": None}
        _OPS[op_id] = rec
    _op_event(op_id, "OPERATION_CREATED", instrument=sym,
              timeframes=list(timeframes), store=str(store_db_path()))

    def _run() -> None:
        _op_touch(op_id, state="RUNNING")
        try:
            report = fetch(sym, list(timeframes), operation_id=op_id)
            state = {"COMPLETE": "COMPLETED", "PARTIAL": "PARTIAL"}.get(
                report.get("status"), "FAILED")
            _op_touch(op_id, state=state, report=report,
                      finished_at=_utc_now_epoch(), stage="OPERATION_COMPLETED")
        except FetchError as exc:
            _op_exception(op_id, "OPERATION_FAILED", exc, instrument=sym,
                          error_code=exc.code)
            _op_touch(op_id, state="FAILED", finished_at=_utc_now_epoch(),
                      stage="OPERATION_FAILED",
                      error={"code": exc.code, "message": exc.message})
        except Exception as exc:  # noqa: BLE001 - the operation must always land
            _op_exception(op_id, "OPERATION_FAILED", exc, instrument=sym)
            _op_touch(op_id, state="FAILED", finished_at=_utc_now_epoch(),
                      stage="OPERATION_FAILED",
                      error={"code": type(exc).__name__, "message": str(exc)})

    threading.Thread(target=_run, name=f"hero1-{op_id}", daemon=True).start()
    return {"operation_id": op_id, "state": rec["state"], "instrument": sym,
            "timeframes": list(timeframes), "existing": False,
            "started_at": rec["started_at"]}


def operation_status(operation_id: str) -> Optional[Dict[str, Any]]:
    with _OPS_LOCK:
        rec = _OPS.get(operation_id)
        return dict(rec) if rec is not None else None


def latest_operation(instrument: str) -> Optional[Dict[str, Any]]:
    sym = instrument.strip().upper()
    with _OPS_LOCK:
        recs = [r for r in _OPS.values() if r["instrument"] == sym]
        if not recs:
            return None
        return dict(max(recs, key=lambda r: r["started_at"]))


def _utc_now_epoch() -> int:
    """Current UTC epoch seconds (seam for deterministic tests)."""
    return int(_time.time())


# --- session model for freshness (ISSUE-056) -------------------------------
# Measured from the canonical stored feed, not assumed: the nightly session
# break's last bar opens 20:59 UTC (58/59 observed breaks), the feed closes
# Friday evening UTC, and reopens Sunday 23:00 UTC. There is NO broker
# holiday calendar in evidence, so holidays stay "unverified", never
# "confirmed closed". The model only ever decides which *completed* candles
# are expected, and it is compared against STORED history, so it can never
# fabricate a fetch requirement for a closure.
def _expected_closed(moment: datetime) -> bool:
    wd = moment.weekday()  # Mon=0 .. Sun=6
    if wd == 5:
        return True                      # Saturday: closed all day
    if wd == 6:
        return moment.hour < 23          # Sunday: opens 23:00 UTC
    if wd == 4:
        return moment.hour >= 21         # Friday: closes after the 20:5x bar
    return moment.hour == 21             # Mon-Thu nightly session break


def expected_latest_completed(now_epoch: int, step_s: int) -> int:
    """Open-epoch of the newest candle that SHOULD be complete by now under
    the measured session model. Pure UTC; walks back over closures."""
    candidate = (int(now_epoch) // step_s) * step_s - step_s
    for _ in range(200_000):
        if not _expected_closed(datetime.fromtimestamp(candidate, tz=timezone.utc)):
            return candidate
        candidate -= step_s
    return candidate


def availability(instrument: str) -> Dict[str, Any]:
    """Canonical-store availability, calculated from actual stored records.

    Every timeframe carries its own status (AVAILABLE only when validated
    candles exist), first/last candle, count, source, last successful import,
    last successful MT5 update, gap/validation warnings and the UTC timezone
    convention. Nothing here is hardcoded.
    """
    sym = instrument.strip().upper()
    db = store_db_path()
    out: Dict[str, Any] = {
        "instrument": sym, "available": False, "store": str(db),
        "timezone": "UTC (epoch seconds)",
        "timeframes": {tf: None for tf in md.TIMEFRAME_ORDER},
        "datasets": [],
        "summary": {"available_timeframes": 0, "total_candles": 0,
                    "first_ts": None, "last_ts": None, "last_successful_update": None},
    }
    if not db.exists():
        out["reason"] = ("no market-data store yet - import the supplied MT CSV exports "
                          "(scripts/import_market_data.py) before using FETCH DATA")
        for tf in md.TIMEFRAME_ORDER:
            out["timeframes"][tf] = {"row_count": 0, "first_ts": None, "last_ts": None,
                                     "status": "EMPTY", "timezone": out["timezone"],
                                     "source": None, "last_successful_import": None,
                                     "last_successful_update": None, "last_mt5_update": None,
                                     "gaps_found": 0, "largest_gap_seconds": None,
                                     "mt5_fetch_required": False, "baseline_missing": True,
                                     "invalid_timestamp_count": 0, "warnings": []}
        return out
    conn = md.connect_store(db)
    try:
        datasets = md.list_datasets(conn, sym)
        out["datasets"] = datasets
        for tf in md.TIMEFRAME_ORDER:
            stats = md.series_stats(conn, sym, tf)
            tf_datasets = [d for d in datasets if d.get("timeframe") == tf]
            successful = [d for d in tf_datasets if d.get("status") == "AVAILABLE"
                          and d.get("validation_result") == "PASS"]
            csv_imports = [d for d in successful if not str(d.get("source_file", "")).startswith("mt5-fetch")]
            mt5_updates = [d for d in successful if str(d.get("source_file", "")).startswith("mt5-fetch")]
            invalid_ts = 0
            first_epoch = last_epoch = None
            if stats["row_count"]:
                invalid_ts = int(conn.execute(
                    "SELECT COUNT(*) FROM candles WHERE instrument = ? AND timeframe = ?"
                    " AND (ts < ? OR ts >= ?)",
                    (sym, tf, md.MIN_TS, md.MAX_TS)).fetchone()[0])
                first_epoch, last_epoch = conn.execute(
                    "SELECT MIN(ts), MAX(ts) FROM candles WHERE instrument = ? AND timeframe = ?",
                    (sym, tf)).fetchone()
            warnings: List[str] = []
            gaps = sum(int(d.get("gaps_found") or 0) for d in successful)
            largest = max((int(d["largest_gap_seconds"]) for d in successful
                           if d.get("largest_gap_seconds") is not None), default=None)
            if gaps:
                warnings.append(f"{gaps} recorded gap interval(s); gaps are reported, never filled")
            if invalid_ts:
                warnings.append(f"{invalid_ts} stored candle(s) have invalid timestamps and need correction")
            status = "EMPTY" if not stats["row_count"] else ("ERROR" if invalid_ts else "AVAILABLE")
            # DB-derived gap classification (ISSUE-055): weekend closures and
            # nightly session breaks are EXPECTED market rhythm, not missing
            # data; only UNVERIFIED weekday intervals count candles that
            # would be missing IF the market was open (no broker calendar is
            # available, so they are never labelled "confirmed missing").
            gap_analysis = None
            if stats["row_count"]:
                ts_list = [int(r[0]) for r in conn.execute(
                    "SELECT ts FROM candles WHERE instrument = ? AND timeframe = ?"
                    " ORDER BY ts", (sym, tf))]
                gap_analysis = md.classify_gaps(ts_list, md.TIMEFRAME_STEPS[tf])
                if gap_analysis["unverified_intervals"]:
                    warnings.append(
                        f"{gap_analysis['unverified_intervals']} unverified gap interval(s) "
                        f"(~{gap_analysis['unverified_missing_candles_est']} candles if the market "
                        "was open; may include holidays) - reported, never filled")
            stats.update({
                "status": status,
                "gap_analysis": gap_analysis,
                "first_epoch": int(first_epoch) if first_epoch is not None else None,
                "last_epoch": int(last_epoch) if last_epoch is not None else None,
                "timezone": out["timezone"],
                "source": (tf_datasets[-1].get("source_file") if tf_datasets else None),
                "sources": sorted({str(d.get("source_file")) for d in successful if d.get("source_file")}),
                "last_successful_import": max((d["imported_at"] for d in csv_imports), default=None),
                "last_mt5_update": max((d["imported_at"] for d in mt5_updates), default=None),
                "gaps_found": gaps, "largest_gap_seconds": largest,
                "invalid_timestamp_count": invalid_ts, "warnings": warnings,
            })
            # Freshness semantics (ISSUE-055): availability (history exists),
            # MT5 recency (last_mt5_update) and "a fetch would append newer
            # COMPLETED candles" are three different facts. fetch_required is
            # true only when at least one completed bar after the latest
            # stored candle has closed - market closures cannot be inferred
            # from the clock, so the UI phrases it neutrally.
            _step = md.TIMEFRAME_STEPS[tf]
            _now = _utc_now_epoch()
            # ISSUE-056: the expected newest completed candle comes from the
            # measured session model (weekend closure + nightly break), not
            # the raw clock - a Saturday fetch ending Friday evening is
            # CURRENT, while genuinely missing completed candles still count.
            _last_completed = expected_latest_completed(_now, _step)
            _missing = 0
            if last_epoch is not None and int(last_epoch) < _last_completed:
                _missing = (_last_completed - int(last_epoch)) // _step
            stats["missing_completed_candles"] = int(_missing)
            stats["expected_latest_candle"] = md.ts_iso_of(_last_completed)
            stats["market_open"] = not _expected_closed(
                datetime.fromtimestamp(_now, tz=timezone.utc))
            stats["up_to_date"] = bool(stats["row_count"]) and _missing == 0
            stats["mt5_fetch_required"] = bool(stats["row_count"]) and _missing > 0
            stats["baseline_missing"] = not bool(stats["row_count"])
            stats["last_successful_update"] = max(
                (v for v in (stats["last_successful_import"], stats["last_mt5_update"]) if v),
                default=None)
            out["timeframes"][tf] = stats
    finally:
        conn.close()
    present = [v for v in out["timeframes"].values() if v and v["row_count"]]
    out["available"] = any(v["status"] == "AVAILABLE" for v in present)
    firsts = [v["first_ts"] for v in present if v.get("first_ts")]
    lasts = [v["last_ts"] for v in present if v.get("last_ts")]
    updates = [v["last_successful_update"] for v in present if v.get("last_successful_update")]
    imports = [v["last_successful_import"] for v in present if v.get("last_successful_import")]
    mt5s = [v["last_mt5_update"] for v in present if v.get("last_mt5_update")]
    out["summary"] = {
        "available_timeframes": sum(1 for v in present if v["status"] == "AVAILABLE"),
        "total_candles": sum(int(v["row_count"]) for v in present),
        "first_ts": min(firsts) if firsts else None,
        "last_ts": max(lasts) if lasts else None,
        "last_successful_import": max(imports) if imports else None,
        "last_mt5_update": max(mt5s) if mt5s else None,
        "mt5_fetch_required_timeframes": [v_tf for v_tf, v in
                                          ((tf, out["timeframes"][tf]) for tf in md.TIMEFRAME_ORDER)
                                          if v and v.get("mt5_fetch_required")],
        "up_to_date_timeframes": [v_tf for v_tf, v in
                                  ((tf, out["timeframes"][tf]) for tf in md.TIMEFRAME_ORDER)
                                  if v and v.get("up_to_date")],
        "unverified_gap_intervals": sum(int((v.get("gap_analysis") or {}).get("unverified_intervals") or 0)
                                        for v in out["timeframes"].values() if v),
        "unverified_missing_candles_est": sum(int((v.get("gap_analysis") or {}).get("unverified_missing_candles_est") or 0)
                                              for v in out["timeframes"].values() if v),
        "last_successful_update": max(updates) if updates else None,
        "market_open": not _expected_closed(
            datetime.fromtimestamp(_utc_now_epoch(), tz=timezone.utc)),
    }
    try:
        _c2 = md.connect_store(db)
        try:
            _unresolved = _c2.execute(
                "SELECT COUNT(*) FROM fetch_conflicts WHERE instrument = ? AND resolution = 'UNRESOLVED'",
                (sym,)).fetchone()[0]
        finally:
            _c2.close()
    except Exception:  # noqa: BLE001 - pre-ISSUE-054 store without the table
        _unresolved = 0
    out["summary"]["unresolved_conflicts"] = int(_unresolved)
    if not present:
        out["reason"] = f"store exists but holds no candles for {sym} yet"
    return out


def _rate_to_candle(rate: Any) -> Optional[Dict[str, Any]]:
    """Normalize one copy_rates_range record (dict / namedtuple / MT5 row)."""
    if rate is None:
        return None
    if isinstance(rate, dict):
        d = rate
    elif hasattr(rate, "_asdict"):
        d = dict(rate._asdict())  # type: ignore[attr-defined]
    else:
        try:
            d = {"time": rate[0], "open": rate[1], "high": rate[2], "low": rate[3],
                 "close": rate[4], "tick_volume": rate[5], "volume_real": rate[6],
                 "spread": rate[7]}
        except Exception:  # noqa: BLE001
            return None

    def _f(key: str):
        v = d.get(key)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    def _i(key: str):
        v = _f(key)
        return int(v) if v is not None and float(v).is_integer() else None

    ts = _i("time")
    if ts is None:
        return None
    return {"ts": ts, "ts_iso": md.ts_iso_of(ts),
            "open": _f("open"), "high": _f("high"), "low": _f("low"), "close": _f("close"),
            "tick_volume": _i("tick_volume"),
            "volume": _f("volume_real") if d.get("volume_real") is not None else _f("volume"),
            "spread": _i("spread")}


def _raw_rate_time(rate: Any) -> Any:
    """The untouched `time` value of one MT5 rate record (for error reports)."""
    if isinstance(rate, dict):
        return rate.get("time")
    if hasattr(rate, "_asdict"):
        try:
            return dict(rate._asdict()).get("time")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return None
    try:
        return rate[0]
    except Exception:  # noqa: BLE001
        return None


def _fetch_one(mt5, conn, *, instrument: str, timeframe: str, utc_now: int,
               broker_server: str = "unknown-server",
               operation_id: str = "-") -> Dict[str, Any]:
    """Incrementally fetch ONE timeframe from its latest valid stored candle.

    Boundaries are UTC datetimes built directly from stored UTC epoch
    seconds. A small overlap (two candles) is requested so boundary candles
    are deduplicated instead of skipped. Without a stored baseline this
    refuses - the supplied CSV history must be imported first; FETCH DATA is
    an updater, not a history substitute.
    """
    step = md.TIMEFRAME_STEPS[timeframe]
    res: Dict[str, Any] = {
        "timeframe": timeframe, "status": "FAILED",
        "requested_from": None, "requested_to": None,
        "received": 0, "appended": 0, "duplicates_skipped": 0,
        "incomplete_dropped": 0, "invalid_rejected": 0,
        "invalid_timestamps_rejected": 0, "invalid_examples": [],
        "conflicts": 0, "latest_stored_ts": None, "error": None,
        "valid_records": 0, "representation_diffs": 0, "conflict_details": [],
    }
    latest = md.latest_ts(conn, instrument, timeframe)
    if latest is None:
        res["error"] = ("no stored baseline for this timeframe - import the supplied "
                        "CSV history first; FETCH DATA only appends newer candles")
        return res
    ts_error = md.validate_timestamp(latest)
    if ts_error is not None:
        res["error"] = (f"latest stored candle is unusable: {ts_error}; "
                        "refusing to build a request from it (no data modified)")
        return res
    res["latest_stored_ts"] = md.ts_iso_of(latest)
    _op_event(operation_id, "STORED_BASELINE_READ", instrument=instrument,
              timeframe=timeframe, latest_stored_ts=res["latest_stored_ts"])
    _op_event(operation_id, "TIMEFRAME_FETCH_STARTED", instrument=instrument,
              timeframe=timeframe)
    from_ts = max(md.MIN_TS, latest - 2 * step)  # small overlap for boundary safety
    to_ts = (int(utc_now) // step) * step
    if to_ts <= from_ts:
        res["status"] = "UP_TO_DATE"
        res["requested_from"] = md.ts_iso_of(from_ts)
        res["requested_to"] = md.ts_iso_of(to_ts)
        return res
    from_dt = datetime.fromtimestamp(from_ts, tz=timezone.utc)
    to_dt = datetime.fromtimestamp(to_ts, tz=timezone.utc)
    res["requested_from"] = md.ts_iso_of(from_ts)
    res["requested_to"] = md.ts_iso_of(to_ts)

    tf_const = getattr(mt5, f"TIMEFRAME_{timeframe}", None)
    if tf_const is None:
        res["error"] = f"terminal module exposes no TIMEFRAME_{timeframe} constant"
        return res
    _op_event(operation_id, "MT5_HISTORY_REQUEST", instrument=instrument,
              timeframe=timeframe, requested_from=res["requested_from"],
              requested_to=res["requested_to"], timeout_s=_tf_timeout())
    _call_started = _time.monotonic()
    _executor = ThreadPoolExecutor(max_workers=1)
    try:
        rates = _executor.submit(
            mt5.copy_rates_range, instrument, tf_const, from_dt, to_dt,
        ).result(timeout=_tf_timeout())
    except FuturesTimeout:
        # The native terminal call cannot be killed safely; it is abandoned
        # on a daemon worker while this timeframe fails loudly with its stage.
        res["error"] = (f"MT5 history request timed out after {_tf_timeout():.0f}s "
                        f"at stage MT5_HISTORY_REQUEST for UTC range "
                        f"{res['requested_from']}..{res['requested_to']} - the terminal "
                        f"did not answer; no data was modified for this timeframe")
        _op_event(operation_id, "MT5_HISTORY_REQUEST", instrument=instrument,
                  timeframe=timeframe, severity="ERROR", timed_out=True,
                  timeout_s=_tf_timeout(), requested_from=res["requested_from"],
                  requested_to=res["requested_to"])
        return res
    finally:
        _executor.shutdown(wait=False)
    _op_event(operation_id, "MT5_HISTORY_RESPONSE", instrument=instrument,
              timeframe=timeframe, received=(len(rates) if rates is not None else 0),
              elapsed_ms=int((_time.monotonic() - _call_started) * 1000))
    if rates is None:
        err = None
        try:
            err = mt5.last_error()
        except Exception:  # noqa: BLE001
            pass
        res["error"] = (f"copy_rates_range returned None (last_error={err}) "
                        f"for UTC range {res['requested_from']}..{res['requested_to']}")
        return res

    raw_rows = list(rates)
    res["received"] = len(raw_rows)
    candles: List[Dict[str, Any]] = []
    batch_conflicts: List[int] = []
    seen: Dict[int, Dict[str, Any]] = {}
    for rate in raw_rows:
        raw_time = _raw_rate_time(rate)
        time_error = md.validate_timestamp(raw_time)
        if time_error is not None:
            res["invalid_rejected"] += 1
            res["invalid_timestamps_rejected"] += 1
            if len(res["invalid_examples"]) < 3:
                res["invalid_examples"].append(f"time={raw_time!r}: {time_error}")
            continue
        c = _rate_to_candle(rate)
        if c is None:
            res["invalid_rejected"] += 1
            if len(res["invalid_examples"]) < 3:
                res["invalid_examples"].append(f"time={raw_time!r}: malformed rate record")
            continue
        if c["ts"] + step > int(utc_now):
            res["incomplete_dropped"] += 1  # live, unfinished bar - never stored
            continue
        validation_error = md.validate_candle(c)
        if validation_error is not None:
            res["invalid_rejected"] += 1
            if validation_error.startswith("invalid timestamp"):
                res["invalid_timestamps_rejected"] += 1
            if len(res["invalid_examples"]) < 3:
                res["invalid_examples"].append(f"time={raw_time!r}: {validation_error}")
            continue
        prior = seen.get(c["ts"])
        if prior is not None:
            if md.candle_equal(prior, c):
                res["duplicates_skipped"] += 1
            else:
                batch_conflicts.append(c["ts"])
            continue
        seen[c["ts"]] = c
        candles.append(c)
    candles.sort(key=lambda r: r["ts"])

    if batch_conflicts:
        res["conflicts"] = len(batch_conflicts)
        res["error"] = (f"terminal returned conflicting candles for {len(batch_conflicts)} "
                        f"timestamp(s) inside one response - timeframe left untouched")
        return res

    res["valid_records"] = len(candles)
    _op_event(operation_id, "TIMESTAMP_VALIDATION", instrument=instrument,
              timeframe=timeframe, invalid_timestamps_rejected=res["invalid_timestamps_rejected"],
              invalid_examples=res["invalid_examples"][:3])
    _op_event(operation_id, "CANDLE_VALIDATION", instrument=instrument,
              timeframe=timeframe, valid_records=res["valid_records"],
              invalid_rejected=res["invalid_rejected"],
              incomplete_dropped=res["incomplete_dropped"])
    batch_fp = md.canonical_fingerprint(candles)
    dataset_id = hashlib.sha256(
        f"{instrument}|{timeframe}|mt5-fetch|{batch_fp}".encode()).hexdigest()[:16]
    # ONE pre-transaction lookup of stored timestamps. (ISSUE-053 root
    # cause: this set was previously rebuilt by a full-table SELECT inside
    # the list comprehension - once per incoming candle - which made a
    # 21,337-candle M1 fetch perform ~2.5 billion row reads inside the
    # write transaction and effectively never commit.)
    _lookup_started = _time.monotonic()
    prior_ts = _stored_ts_set(conn, instrument, timeframe, dataset_id)
    _count_before = md.series_stats(conn, instrument, timeframe)["row_count"]
    _op_event(operation_id, "EXISTING_CANDLE_LOOKUP_COMPLETED", instrument=instrument,
              timeframe=timeframe, existing_records=len(prior_ts),
              elapsed_ms=int((_time.monotonic() - _lookup_started) * 1000),
              store=str(store_db_path()))
    _tx_started = _time.monotonic()
    _op_event(operation_id, "DATABASE_TRANSACTION_STARTED", instrument=instrument,
              timeframe=timeframe, store=str(store_db_path()))

    def _db_progress(stage: str, **ctx: Any) -> None:
        _op_event(operation_id, stage, instrument=instrument, timeframe=timeframe,
                  elapsed_ms=int((_time.monotonic() - _tx_started) * 1000), **ctx)

    with conn:  # one transaction per timeframe: inserts + audit record commit together
        merged = md.merge_candles(conn, dataset_id=dataset_id, instrument=instrument,
                                  timeframe=timeframe, rows=candles, policy="fetch",
                                  progress=_db_progress)
        res["duplicates_skipped"] += merged["duplicates_skipped"]
        res["representation_diffs"] = int(merged.get("representation_diffs") or 0)
        res["conflicts"] = len(merged["conflicts"])
        res["conflict_details"] = merged.get("conflict_details") or []
        res["appended"] = merged["inserted"]
        inserted_rows = [c for c in candles if c["ts"] not in prior_ts]
        if merged["inserted"] or merged["conflicts"]:
            gaps, largest = md.gap_stats(inserted_rows or candles, step)
            if not conn.execute("SELECT 1 FROM datasets WHERE dataset_id = ?",
                                (dataset_id,)).fetchone():
                md.record_dataset(
                    conn, dataset_id=dataset_id, instrument=instrument, timeframe=timeframe,
                    source_file=f"mt5-fetch:{broker_server}", source_sha256=batch_fp, source_bytes=0,
                    row_count=merged["inserted"], duplicates_collapsed=merged["duplicates_skipped"],
                    gaps_found=gaps, largest_gap_seconds=largest,
                    first_ts=inserted_rows[0]["ts_iso"] if inserted_rows else None,
                    last_ts=inserted_rows[-1]["ts_iso"] if inserted_rows else None,
                    content_fingerprint=batch_fp, location=str(store_db_path()),
                    imported_at=md.iso_now(), validation_result="PASS",
                    validation_report={"kind": "mt5-fetch", "timezone": "UTC epoch seconds",
                                       "operation_id": operation_id,
                                       "requested_from": res["requested_from"],
                                       "requested_to": res["requested_to"],
                                       "received": res["received"],
                                       "incomplete_dropped": res["incomplete_dropped"],
                                       "invalid_rejected": res["invalid_rejected"],
                                       "representation_diffs": res["representation_diffs"],
                                       "conflicts": res["conflicts"],
                                       "conflict_details": res["conflict_details"],
                                       "invalid_timestamps_rejected": res["invalid_timestamps_rejected"]},
                    status="AVAILABLE" if merged["inserted"] else "CONFLICT_RECORDED")
    _op_event(operation_id, "DUPLICATE_CHECK", instrument=instrument, timeframe=timeframe,
              duplicates_skipped=res["duplicates_skipped"],
              representation_diffs=res["representation_diffs"])
    _op_event(operation_id, "CONFLICT_CHECK", instrument=instrument, timeframe=timeframe,
              conflicts=res["conflicts"],
              severity="WARNING" if res["conflicts"] else "INFO")
    _op_event(operation_id, "DATABASE_COMMIT", instrument=instrument, timeframe=timeframe,
              appended=res["appended"],
              transaction_elapsed_ms=int((_time.monotonic() - _tx_started) * 1000))
    # Post-commit READ-BACK verification (ISSUE-056): an appended counter is
    # not proof of persistence. Re-read from a FRESH connection: the row
    # count must have grown by exactly `appended`, and the newest inserted
    # candle must read back with identical OHLC. Any mismatch fails the
    # timeframe honestly instead of reporting a false append.
    _verify_started = _time.monotonic()
    try:
        _fresh = md.connect_store(store_db_path())
        try:
            _after_count = md.series_stats(_fresh, instrument, timeframe)["row_count"]
            _readback_ok = (_after_count - _count_before) == res["appended"]
            _spot = None
            _conflict_ts = {int(d.get("ts")) for d in (res.get("conflict_details") or [])
                            if isinstance(d, dict) and d.get("ts") is not None}
            _inserted_actual = [c for c in inserted_rows
                                if int(c["ts"]) not in _conflict_ts]
            if _readback_ok and _inserted_actual:
                _newest = max(_inserted_actual, key=lambda c: c["ts"])
                _row = _fresh.execute(
                    "SELECT open, high, low, close FROM candles WHERE instrument = ?"
                    " AND timeframe = ? AND ts = ?",
                    (instrument, timeframe, int(_newest["ts"]))).fetchone()
                _spot = _row is not None and tuple(_row) == (
                    _newest["open"], _newest["high"], _newest["low"], _newest["close"])
                _readback_ok = bool(_spot)
        finally:
            _fresh.close()
    except Exception as exc:  # noqa: BLE001
        _op_exception(operation_id, "POST_COMMIT_VERIFICATION", exc,
                      instrument=instrument, timeframe=timeframe)
        res["status"] = "FAILED"
        res["error"] = f"post-commit read-back verification raised: {exc}"
        res["appended"] = 0
        return res
    _op_event(operation_id, "POST_COMMIT_VERIFICATION", instrument=instrument,
              timeframe=timeframe, before_count=_count_before,
              after_count=_after_count, appended=res["appended"],
              readback_ok=_readback_ok,
              elapsed_ms=int((_time.monotonic() - _verify_started) * 1000),
              severity="INFO" if _readback_ok else "ERROR")
    if not _readback_ok:
        res["status"] = "FAILED"
        res["error"] = (f"post-commit verification failed: expected {_count_before} + "
                        f"{res['appended']} rows, database holds {_after_count} "
                        "(counts/read-back mismatch - append NOT trusted)")
        res["appended"] = 0
        return res
    # Durable source-aware conflict audit (ISSUE-054): stored values,
    # incoming values, both provenances and the field diff are persisted so
    # conflicts can be inspected and resolved deliberately later - the
    # stored candles themselves remain untouched (UNRESOLVED by default).
    if res.get("conflict_details"):
        try:
            with conn:
                recorded = md.record_fetch_conflicts(
                    conn, operation_id=operation_id, instrument=instrument,
                    timeframe=timeframe, conflict_details=res["conflict_details"],
                    incoming_source=f"MT5 copy_rates_range ({broker_server})")
            _op_event(operation_id, "CONFLICT_AUDIT_PERSISTED", instrument=instrument,
                      timeframe=timeframe, conflicts_recorded=recorded)
        except Exception as exc:  # noqa: BLE001 - audit failure must be visible, not fatal
            _op_exception(operation_id, "CONFLICT_AUDIT_PERSISTED", exc,
                          instrument=instrument, timeframe=timeframe)
    if res["conflicts"]:
        res["status"] = "PARTIAL"
        res["error"] = (f"{res['conflicts']} terminal candle(s) conflict with stored history "
                        f"and were isolated (stored values preserved, see conflict_details); "
                        f"{res['appended']} new candle(s) appended, "
                        f"{res['duplicates_skipped']} duplicate(s) skipped")
    else:
        res["status"] = "UPDATED" if res["appended"] else "UP_TO_DATE"
    latest_after = md.latest_ts(conn, instrument, timeframe)
    res["latest_stored_ts"] = md.ts_iso_of(latest_after) if latest_after else None
    _op_event(operation_id, "POST_COMMIT_VERIFICATION", instrument=instrument,
              timeframe=timeframe, latest_stored_ts=res["latest_stored_ts"],
              stored_count=md.series_stats(conn, instrument, timeframe)["row_count"])
    _op_event(operation_id, "TIMEFRAME_COMPLETED", instrument=instrument,
              timeframe=timeframe, status=res["status"], appended=res["appended"],
              duplicates_skipped=res["duplicates_skipped"], conflicts=res["conflicts"],
              representation_diffs=res["representation_diffs"],
              latest_stored_ts=res["latest_stored_ts"],
              severity="WARNING" if res["status"] == "PARTIAL" else "INFO")
    return res


def _stored_ts_set(conn, instrument: str, timeframe: str, exclude_dataset: str) -> set:
    return {int(r[0]) for r in conn.execute(
        "SELECT ts FROM candles WHERE instrument = ? AND timeframe = ? AND dataset_id != ?",
        (instrument, timeframe, exclude_dataset))}


def fetch(instrument: str, timeframes: List[str],
          operation_id: Optional[str] = None) -> Dict[str, Any]:
    """Incrementally fetch completed candles for the given timeframes.

    Every stage is recorded as a persistent event carrying `operation_id`
    (generated when the caller did not supply one), so the whole operation
    can be reconstructed in the LOG tab.
    """
    sym = instrument.strip().upper()
    operation_id = (operation_id or "").strip() or new_operation_id()
    _op_event(operation_id, "BACKEND_REQUEST_RECEIVED", instrument=sym,
              timeframes=list(timeframes))
    _op_event(operation_id, "DATABASE_PATH_RESOLVED", instrument=sym,
              store=str(store_db_path()))
    mt5 = mt5_connection.mt5_module()
    if mt5 is None:
        _op_event(operation_id, "MT5_CONNECTION_CHECK", instrument=sym, severity="ERROR",
                  error_code="MT5_UNAVAILABLE", stage_failed="MT5_CONNECTION_CHECK")
        raise FetchError("MT5_UNAVAILABLE",
                         f"MetaTrader5 package not importable: {mt5_connection.import_error()}. "
                         f"FETCH DATA needs the Windows machine with the terminal (see TROUBLESHOOTING.txt).")
    result = mt5_connection.connect(None)
    if result.get("state") == mt5_connection.STATE_CONNECTED:
        _op_event(operation_id, "MT5_CONNECTION_CHECK", instrument=sym, severity="ERROR",
                  error_code="MT5_LOGIN_REQUIRED", state=result.get("state"))
        raise FetchError("MT5_LOGIN_REQUIRED",
                         "terminal is running but no account is logged in; log in inside "
                         "MT5, reconnect, and only then FETCH DATA")
    if result.get("state") != mt5_connection.STATE_ACCOUNT_LOADED:
        _op_event(operation_id, "MT5_CONNECTION_CHECK", instrument=sym, severity="ERROR",
                  error_code=result.get("error_code") or "MT5_CONNECT_FAILED",
                  state=result.get("state"))
        raise FetchError(result.get("error_code") or "MT5_CONNECT_FAILED",
                         result.get("error") or "MT5 connection failed")
    account = result.get("account") or {}
    terminal = result.get("terminal") or {}
    broker_server = account.get("server") or terminal.get("company") or "unknown-server"
    _op_event(operation_id, "MT5_CONNECTION_CHECK", instrument=sym,
              state=result.get("state"), broker_server=broker_server)

    # MT5 rate timestamps and the canonical store are both UTC epoch seconds.
    # No broker offset is measured, rounded or applied anywhere in this path.
    utc_now = _utc_now_epoch()

    db = store_db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = md.connect_store(db)
    _op_event(operation_id, "DATABASE_CONNECTION_ACQUIRED", instrument=sym,
              store=str(db), journal_mode="WAL", busy_timeout_ms=30000)
    results: List[Dict[str, Any]] = []
    try:
        for tf in timeframes:
            try:
                results.append(_fetch_one(mt5, conn, instrument=sym,
                                          timeframe=tf, utc_now=utc_now,
                                          broker_server=broker_server,
                                          operation_id=operation_id))
            except Exception as exc:  # noqa: BLE001 - one TF must not sink the rest
                _op_exception(operation_id, "TIMEFRAME_FETCH_FAILED", exc,
                              instrument=sym, timeframe=tf,
                              transaction_rolled_back=True)
                results.append({
                    "timeframe": tf, "status": "FAILED", "requested_from": None,
                    "requested_to": md.ts_iso_of(utc_now), "received": 0, "appended": 0,
                    "duplicates_skipped": 0, "incomplete_dropped": 0, "invalid_rejected": 0,
                    "invalid_timestamps_rejected": 0, "invalid_examples": [],
                    "conflicts": 0, "latest_stored_ts": None,
                    "valid_records": 0, "representation_diffs": 0, "conflict_details": [],
                    "error": f"{type(exc).__name__}: {exc}",
                })
        if any(r["status"] == "UPDATED" for r in results):
            md.rebuild_manifest(conn, db)
    finally:
        conn.close()

    clean = sum(1 for r in results if r["status"] in ("UPDATED", "UP_TO_DATE"))
    processed = sum(1 for r in results if r["status"] != "FAILED")
    overall = ("COMPLETE" if results and clean == len(results)
               else "PARTIAL" if processed else "FAILED")
    _op_event(operation_id, "OPERATION_COMPLETED", instrument=sym,
              status=overall, broker_server=broker_server,
              severity="INFO" if overall == "COMPLETE" else "WARNING" if overall == "PARTIAL" else "ERROR",
              results=[{"timeframe": r["timeframe"], "status": r["status"],
                        "appended": r["appended"], "conflicts": r["conflicts"]} for r in results])
    time_block = {
        "rule": "UTC epoch seconds; MT5 rate time is stored verbatim after validation; "
                "no broker/server offset is applied",
        "utc_now": md.ts_iso_of(utc_now),
        "offset_seconds": 0,
        "offset_source": "not applied (UTC canonical store)",
    }
    return {
        "instrument": sym,
        "operation_id": operation_id,
        "broker_server": broker_server,
        "status": overall,
        "time": time_block,
        "server_time": time_block,  # backward-compatible alias; values are UTC, not server time
        "ok": overall == "COMPLETE",
        "results": results,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


__all__ = ["FetchError", "store_db_path", "manifest_path", "availability", "fetch"]


# ----------------------------------------------------- conflict audit API
def list_conflicts(instrument: str, *, operation_id: Optional[str] = None,
                   resolution: Optional[str] = None, limit: int = 200) -> dict:
    db = store_db_path()
    if not db.exists():
        return {"instrument": instrument.upper(), "conflicts": [], "total": 0}
    conn = md.connect_store(db)
    try:
        rows = md.list_fetch_conflicts(conn, instrument=instrument.upper(),
                                       operation_id=operation_id,
                                       resolution=resolution, limit=limit)
    finally:
        conn.close()
    return {"instrument": instrument.upper(), "conflicts": rows, "total": len(rows)}


def resolve_conflict(instrument: str, conflict_id: int, action: str,
                     note: Optional[str] = None) -> dict:
    """Controlled correction path (ISSUE-054): one conflict at a time.

    The audit row retains the pre-resolution stored values (the backup);
    a full audit event is published; candle totals are verified unchanged.
    """
    db = store_db_path()
    conn = md.connect_store(db)
    try:
        before_total = conn.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
        result = md.resolve_fetch_conflict(conn, conflict_id, action=action, note=note)
        after_total = conn.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
    finally:
        conn.close()
    result["candle_total_before"] = before_total
    result["candle_total_after"] = after_total
    _op_event("CONFLICT-RESOLUTION", "CONFLICT_RESOLVED", instrument=instrument.upper(),
              timeframe=result["conflict"]["timeframe"], severity="WARNING",
              conflict_id=conflict_id, action=action,
              ts=result["conflict"]["ts_iso"], before=result["before"],
              after=result["after"], note=note)
    return result


# ------------------------------------------------- data-quality report (B7)
def data_quality(instrument: str, timeframe: str, *,
                 from_epoch: Optional[int] = None,
                 to_epoch: Optional[int] = None) -> Dict[str, Any]:
    """Backtesting-readiness report for one instrument/timeframe.

    Answers, from the canonical store only: how much history exists, does
    the requested range contain UNVERIFIED gaps (never silently "confirmed
    missing" - no broker calendar is available), do unresolved CSV-vs-MT5
    conflicts exist, which sources the data came from, and whether the
    range is suitable for a trustworthy backtest without synthetic data.
    """
    sym = instrument.strip().upper()
    tf = timeframe.strip().upper()
    if tf not in md.TIMEFRAME_STEPS:
        raise ValueError(f"unsupported timeframe {tf!r}")
    db = store_db_path()
    report: Dict[str, Any] = {
        "instrument": sym, "timeframe": tf, "store": str(db),
        "timezone": "UTC (epoch seconds)",
        "range": {"from": md.ts_iso_of(from_epoch) if from_epoch else None,
                  "to": md.ts_iso_of(to_epoch) if to_epoch else None},
        "synthetic_data_used": False,
    }
    if not db.exists():
        report.update({"candles_total": 0, "candles_in_range": 0,
                       "readiness": "NOT_READY", "reasons": ["no market-data store"]})
        return report
    conn = md.connect_store(db)
    try:
        step = md.TIMEFRAME_STEPS[tf]
        all_ts = [int(r[0]) for r in conn.execute(
            "SELECT ts FROM candles WHERE instrument = ? AND timeframe = ? ORDER BY ts",
            (sym, tf))]
        in_range = [t for t in all_ts
                    if (from_epoch is None or t >= from_epoch)
                    and (to_epoch is None or t <= to_epoch)]
        gaps = md.classify_gaps(in_range, step)
        datasets = [d for d in md.list_datasets(conn, sym) if d.get("timeframe") == tf]
        conflicts = {"unresolved": 0, "resolved": 0}
        for row in conn.execute(
                "SELECT resolution, COUNT(*) FROM fetch_conflicts"
                " WHERE instrument = ? AND timeframe = ? GROUP BY resolution", (sym, tf)):
            if row[0] == "UNRESOLVED":
                conflicts["unresolved"] = int(row[1])
            else:
                conflicts["resolved"] += int(row[1])
    finally:
        conn.close()
    reasons: List[str] = []
    if not all_ts:
        readiness = "NOT_READY"
        reasons.append("no stored history for this timeframe")
    else:
        if from_epoch is not None and all_ts[0] > from_epoch:
            reasons.append("requested range starts before stored history")
        if to_epoch is not None and all_ts[-1] < to_epoch:
            reasons.append("requested range ends after stored history")
        if gaps["unverified_intervals"]:
            reasons.append(
                f"{gaps['unverified_intervals']} unverified gap interval(s) in range "
                f"(~{gaps['unverified_missing_candles_est']} candles if the market was open)")
        if conflicts["unresolved"]:
            reasons.append(f"{conflicts['unresolved']} unresolved CSV-vs-MT5 conflict(s) for this timeframe")
        readiness = "CAUTION" if reasons else "READY"
        if (from_epoch is not None and all_ts[0] > from_epoch) or \
                (to_epoch is not None and all_ts[-1] < to_epoch) or not in_range:
            # the requested range is not fully covered by stored history -
            # a backtest over it would silently test less than asked
            readiness = "NOT_READY"
    report.update({
        "candles_total": len(all_ts), "candles_in_range": len(in_range),
        "first_ts": md.ts_iso_of(all_ts[0]) if all_ts else None,
        "last_ts": md.ts_iso_of(all_ts[-1]) if all_ts else None,
        "gaps": gaps, "conflicts": conflicts,
        "sources": sorted({str(d.get("source_file")) for d in datasets if d.get("source_file")}),
        "readiness": readiness, "reasons": reasons,
    })
    return report

