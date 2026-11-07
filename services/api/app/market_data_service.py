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
import time as _time
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


def _utc_now_epoch() -> int:
    """Current UTC epoch seconds (seam for deterministic tests)."""
    return int(_time.time())


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
            stats.update({
                "status": status,
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
            stats["mt5_fetch_required"] = bool(
                stats["row_count"] and last_epoch is not None
                and int(last_epoch) + md.TIMEFRAME_STEPS[tf] <= _utc_now_epoch())
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
        "last_successful_update": max(updates) if updates else None,
    }
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
    _op_event(operation_id, "latest_stored_candle_identified", instrument=instrument,
              timeframe=timeframe, latest_stored_ts=res["latest_stored_ts"])
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
    rates = mt5.copy_rates_range(instrument, tf_const, from_dt, to_dt)
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
    _op_event(operation_id, "mt5_history_response_received", instrument=instrument,
              timeframe=timeframe, received=res["received"],
              requested_from=res["requested_from"], requested_to=res["requested_to"])
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
    _op_event(operation_id, "candle_validation_complete", instrument=instrument,
              timeframe=timeframe, valid_records=res["valid_records"],
              invalid_rejected=res["invalid_rejected"],
              incomplete_dropped=res["incomplete_dropped"])
    batch_fp = md.canonical_fingerprint(candles)
    dataset_id = hashlib.sha256(
        f"{instrument}|{timeframe}|mt5-fetch|{batch_fp}".encode()).hexdigest()[:16]
    with conn:  # one transaction per timeframe: inserts + audit record commit together
        merged = md.merge_candles(conn, dataset_id=dataset_id, instrument=instrument,
                                  timeframe=timeframe, rows=candles, policy="fetch")
        res["duplicates_skipped"] += merged["duplicates_skipped"]
        res["representation_diffs"] = int(merged.get("representation_diffs") or 0)
        res["conflicts"] = len(merged["conflicts"])
        res["conflict_details"] = merged.get("conflict_details") or []
        res["appended"] = merged["inserted"]
        if merged["inserted"] or merged["conflicts"]:
            inserted_rows = [c for c in candles
                             if c["ts"] not in _stored_ts_set(conn, instrument, timeframe, dataset_id)]
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
    _op_event(operation_id, "timeframe_fetch_complete", instrument=instrument,
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
    _op_event(operation_id, "fetch_request_received", instrument=sym,
              timeframes=list(timeframes), store=str(store_db_path()))
    mt5 = mt5_connection.mt5_module()
    if mt5 is None:
        raise FetchError("MT5_UNAVAILABLE",
                         f"MetaTrader5 package not importable: {mt5_connection.import_error()}. "
                         f"FETCH DATA needs the Windows machine with the terminal (see TROUBLESHOOTING.txt).")
    result = mt5_connection.connect(None)
    if result.get("state") == mt5_connection.STATE_CONNECTED:
        raise FetchError("MT5_LOGIN_REQUIRED",
                         "terminal is running but no account is logged in; log in inside "
                         "MT5, reconnect, and only then FETCH DATA")
    if result.get("state") != mt5_connection.STATE_ACCOUNT_LOADED:
        raise FetchError(result.get("error_code") or "MT5_CONNECT_FAILED",
                         result.get("error") or "MT5 connection failed")
    account = result.get("account") or {}
    terminal = result.get("terminal") or {}
    broker_server = account.get("server") or terminal.get("company") or "unknown-server"
    _op_event(operation_id, "mt5_connection_verified", instrument=sym,
              broker_server=broker_server)

    # MT5 rate timestamps and the canonical store are both UTC epoch seconds.
    # No broker offset is measured, rounded or applied anywhere in this path.
    utc_now = _utc_now_epoch()

    db = store_db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = md.connect_store(db)
    results: List[Dict[str, Any]] = []
    try:
        for tf in timeframes:
            try:
                results.append(_fetch_one(mt5, conn, instrument=sym,
                                          timeframe=tf, utc_now=utc_now,
                                          broker_server=broker_server,
                                          operation_id=operation_id))
            except Exception as exc:  # noqa: BLE001 - one TF must not sink the rest
                _op_event(operation_id, "timeframe_fetch_failed", instrument=sym,
                          timeframe=tf, severity="ERROR",
                          error=f"{type(exc).__name__}: {exc}")
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
    _op_event(operation_id, "fetch_operation_completed", instrument=sym,
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
