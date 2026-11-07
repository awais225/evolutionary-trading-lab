"""Historical market data: availability view + incremental MT5 fetch.

Reads/writes the canonical store (hero1_market_data) under
<DATA_ROOT>/MARKET_DATA/ - the same database and manifest the CSV importer
produces. Fetch rules (binding):
  * ranges are requested from the terminal with timezone-aware UTC datetimes
    (hero1_mt5_adapter.server_time converts the stored naive server epochs);
  * only COMPLETED candles are appended (ts + step <= server now);
  * identical overlaps are duplicates; conflicting overlaps abort that
    timeframe untouched - stored history is never silently overwritten;
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
from hero1_mt5_adapter import server_time
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


def availability(instrument: str) -> Dict[str, Any]:
    sym = instrument.strip().upper()
    db = store_db_path()
    out: Dict[str, Any] = {
        "instrument": sym, "available": False, "store": str(db),
        "timeframes": {tf: None for tf in md.TIMEFRAME_ORDER},
        "datasets": [],
    }
    if not db.exists():
        out["reason"] = ("no market-data store yet - import the MT CSV exports "
                          "(scripts/import_market_data.py) or FETCH DATA from a connected terminal")
        return out
    conn = md.connect_store(db)
    try:
        for tf in md.TIMEFRAME_ORDER:
            stats = md.series_stats(conn, sym, tf)
            if stats["row_count"]:
                out["timeframes"][tf] = stats
        out["datasets"] = md.list_datasets(conn, sym)
    finally:
        conn.close()
    out["available"] = any(v is not None for v in out["timeframes"].values())
    if not out["available"]:
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


def _fetch_one(mt5, conn, *, instrument: str, timeframe: str, server_now: int,
               broker_server: str = "unknown-server") -> Dict[str, Any]:
    step = md.TIMEFRAME_STEPS[timeframe]
    res: Dict[str, Any] = {
        "timeframe": timeframe, "status": "FAILED",
        "requested_from": None, "requested_to": md.ts_iso_of(server_now),
        "received": 0, "appended": 0, "duplicates_skipped": 0,
        "incomplete_dropped": 0, "invalid_rejected": 0, "conflicts": 0,
        "latest_stored_ts": None, "error": None,
    }
    latest = md.latest_ts(conn, instrument, timeframe)
    if latest is not None:
        res["latest_stored_ts"] = md.ts_iso_of(latest)
        from_server = latest + step
    else:
        from_server = max(0, server_now - step * MAX_BACKFILL_CANDLES)
        from_server -= from_server % step
    res["requested_from"] = md.ts_iso_of(from_server)
    if from_server > server_now:
        res["status"] = "UP_TO_DATE"
        return res

    tf_const = getattr(mt5, f"TIMEFRAME_{timeframe}", None)
    if tf_const is None:
        res["error"] = f"terminal module exposes no TIMEFRAME_{timeframe} constant"
        return res
    rates = mt5.copy_rates_range(instrument, tf_const,
                                 server_time.server_to_utc_dt(from_server),
                                 server_time.server_to_utc_dt(server_now))
    if rates is None:
        err = None
        try:
            err = mt5.last_error()
        except Exception:  # noqa: BLE001
            pass
        res["error"] = f"copy_rates_range returned None (last_error={err})"
        return res

    raw_rows = list(rates)
    res["received"] = len(raw_rows)
    candles: List[Dict[str, Any]] = []
    batch_conflicts: List[int] = []
    seen: Dict[int, Dict[str, Any]] = {}
    for rate in raw_rows:
        c = _rate_to_candle(rate)
        if c is None:
            res["invalid_rejected"] += 1
            continue
        if c["ts"] + step > server_now:
            res["incomplete_dropped"] += 1  # live, unfinished bar - never stored
            continue
        if md.validate_candle(c) is not None:
            res["invalid_rejected"] += 1
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

    batch_fp = md.canonical_fingerprint(candles)
    dataset_id = hashlib.sha256(
        f"{instrument}|{timeframe}|mt5-fetch|{batch_fp}".encode()).hexdigest()[:16]
    with conn:  # one transaction per timeframe - all-or-nothing for this TF
        merged = md.merge_candles(conn, dataset_id=dataset_id, instrument=instrument,
                                  timeframe=timeframe, rows=candles, policy="fetch")
        if merged["conflicts"]:
            raise _Conflict(merged["conflicts"])
        res["duplicates_skipped"] += merged["duplicates_skipped"]
        res["appended"] = merged["inserted"]
        if merged["inserted"]:
            inserted_rows = [c for c in candles
                             if c["ts"] not in _stored_ts_set(conn, instrument, timeframe, dataset_id)]
            gaps, largest = md.gap_stats(inserted_rows or candles, step)
            md.record_dataset(
                conn, dataset_id=dataset_id, instrument=instrument, timeframe=timeframe,
                source_file=f"mt5-fetch", source_sha256=batch_fp, source_bytes=0,
                row_count=merged["inserted"], duplicates_collapsed=merged["duplicates_skipped"],
                gaps_found=gaps, largest_gap_seconds=largest,
                first_ts=inserted_rows[0]["ts_iso"] if inserted_rows else None,
                last_ts=inserted_rows[-1]["ts_iso"] if inserted_rows else None,
                content_fingerprint=batch_fp, location=str(store_db_path()),
                imported_at=md.iso_now(), validation_result="PASS",
                validation_report={"kind": "mt5-fetch",
                                   "requested_from": res["requested_from"],
                                   "requested_to": res["requested_to"],
                                   "received": res["received"],
                                   "incomplete_dropped": res["incomplete_dropped"],
                                   "invalid_rejected": res["invalid_rejected"]},
                status="AVAILABLE")
    res["status"] = "UPDATED" if res["appended"] else "UP_TO_DATE"
    latest_after = md.latest_ts(conn, instrument, timeframe)
    res["latest_stored_ts"] = md.ts_iso_of(latest_after) if latest_after else None
    return res


class _Conflict(Exception):
    def __init__(self, ts_list: List[int]):
        super().__init__(f"conflicting stored candles at {ts_list[:5]}")
        self.ts_list = ts_list


def _stored_ts_set(conn, instrument: str, timeframe: str, exclude_dataset: str) -> set:
    return {int(r[0]) for r in conn.execute(
        "SELECT ts FROM candles WHERE instrument = ? AND timeframe = ? AND dataset_id != ?",
        (instrument, timeframe, exclude_dataset))}


def fetch(instrument: str, timeframes: List[str]) -> Dict[str, Any]:
    """Incrementally fetch completed candles for the given timeframes."""
    sym = instrument.strip().upper()
    mt5 = mt5_connection.mt5_module()
    if mt5 is None:
        raise FetchError("MT5_UNAVAILABLE",
                         f"MetaTrader5 package not importable: {mt5_connection.import_error()}. "
                         f"FETCH DATA needs the Windows machine with the terminal (see TROUBLESHOOTING.txt).")
    result = mt5_connection.connect(None)
    if result.get("state") not in (mt5_connection.STATE_ACCOUNT_LOADED, mt5_connection.STATE_CONNECTED):
        raise FetchError(result.get("error_code") or "MT5_CONNECT_FAILED",
                         result.get("error") or "MT5 connection failed")
    account = result.get("account") or {}
    terminal = result.get("terminal") or {}
    broker_server = account.get("server") or terminal.get("company") or "unknown-server"

    with mt5_connection.SESSION_LOCK:
        tick = mt5_connection.as_dict(mt5.symbol_info_tick(sym))
    utc_now = int(_time.time())
    tick_time = tick.get("time") if isinstance(tick.get("time"), (int, float)) else None
    server_now = server_time.server_now_epoch(int(tick_time) if tick_time else None, utc_now)
    offset, offset_source = server_time.estimate_offset_from_tick(
        int(tick_time) if tick_time else None, utc_now)

    db = store_db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = md.connect_store(db)
    results: List[Dict[str, Any]] = []
    try:
        for tf in timeframes:
            try:
                results.append(_fetch_one(mt5, conn, instrument=sym,
                                          timeframe=tf, server_now=server_now,
                                          broker_server=broker_server))
            except _Conflict as exc:
                results.append({
                    "timeframe": tf, "status": "FAILED", "requested_from": None,
                    "requested_to": md.ts_iso_of(server_now), "received": 0, "appended": 0,
                    "duplicates_skipped": 0, "incomplete_dropped": 0, "invalid_rejected": 0,
                    "conflicts": len(exc.ts_list), "latest_stored_ts": None,
                    "error": (f"terminal history conflicts with stored candles at "
                              f"{[md.ts_iso_of(t) for t in exc.ts_list[:5]]} - stored history "
                              f"was NOT overwritten; timeframe left untouched"),
                })
            except Exception as exc:  # noqa: BLE001 - one TF must not sink the rest
                results.append({
                    "timeframe": tf, "status": "FAILED", "requested_from": None,
                    "requested_to": md.ts_iso_of(server_now), "received": 0, "appended": 0,
                    "duplicates_skipped": 0, "incomplete_dropped": 0, "invalid_rejected": 0,
                    "conflicts": 0, "latest_stored_ts": None,
                    "error": f"{type(exc).__name__}: {exc}",
                })
        if any(r["status"] == "UPDATED" for r in results):
            md.rebuild_manifest(conn, db)
    finally:
        conn.close()

    return {
        "instrument": sym,
        "broker_server": broker_server,
        "server_time": {
            "rule": server_time.RULE_NAME,
            "server_now": md.ts_iso_of(server_now),
            "offset_seconds": offset,
            "offset_source": offset_source,
        },
        "ok": all(r["status"] != "FAILED" for r in results),
        "results": results,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


__all__ = ["FetchError", "store_db_path", "manifest_path", "availability", "fetch"]
