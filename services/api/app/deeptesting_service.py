"""DeepTesting execution service (Phase 3).

Orchestrates: node spec -> Phase 2 compile -> ISSUE-060 readiness gate ->
historical candles from the canonical store -> the shared execution
engine -> analytics -> immutable persistence. One engine for every mode;
modes only change how the requested range is windowed. Nothing here
fabricates candles, sizes or results: gaps in metadata or data refuse
loudly with the exact reason, recorded on the run row.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import logging
import traceback
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from hero1_exec_semantics import (
    ENGINE_VERSION, EngineError, InstrumentSpec, RunConfig, run_backtest)
from hero1_exec_semantics.analytics import (
    apply_trade_filters, compute_metrics, rank_runs, time_buckets)
from hero1_exec_semantics.stop_management import PriceContext
from hero1_strategy_spec import TradeManagement
from hero1_storage import instrument_models as im

from . import market_data_service, metadata_service, spec_service

MAX_CANDLES_PER_RUN = 250_000
_LOG = logging.getLogger("hero1.api.deeptesting")
_CANCEL_EVENTS: dict[str, Any] = {}
_CANCEL_LOCK = __import__('threading').Lock()


def request_task_cancel(task_id: str) -> bool:
    """Cooperative cancellation for the exact in-process DeepTesting task."""
    with _CANCEL_LOCK:
        event = _CANCEL_EVENTS.get(task_id)
        if event is None:
            return False
        event.set()
        return True
BLOCKED_ERROR_CODES = {"DATA_BLOCKED", "HISTORICAL_SPREAD_UNAVAILABLE", "MISSING_TICK_SIZE",
                       "MISSING_TICK_VALUE", "UNSUPPORTED_CHART_PRICE_MODE",
                       "UNSUPPORTED_SWAP_MODE", "INVALID_SPREAD_MODE"}


def _execution_log(stage: str, status: str, *, task_id=None, run_id=None, node_id=None,
                   instrument=None, timeframe=None, started=None, details=None, exc=None):
    frame = inspect.currentframe().f_back
    now = time.time()
    record = {"ts": datetime.now(timezone.utc).isoformat(), "task_id": task_id,
              "run_id": run_id, "node_id": node_id, "instrument": instrument,
              "timeframe": timeframe, "stage": stage, "status": status,
              "module": frame.f_globals.get("__name__"), "function": frame.f_code.co_name,
              "file": frame.f_code.co_filename, "line": frame.f_lineno,
              "elapsed_ms": round((now - started) * 1000, 3) if started else None,
              "details": details or {}}
    if exc is not None:
        tb = traceback.TracebackException.from_exception(exc)
        frames = traceback.extract_tb(exc.__traceback__)
        leaf = frames[-1] if frames else None
        record["exception_type"] = type(exc).__name__
        record["exception_message"] = str(exc)
        record["exception_file"] = leaf.filename if leaf else None
        record["exception_function"] = leaf.name if leaf else None
        record["exception_line"] = leaf.lineno if leaf else None
        record["traceback"] = "".join(tb.format())
    line = "DEEPUTEST_STAGE %s"
    payload = json.dumps(record, default=str)
    if status == "FAILED":
        _LOG.error(line, payload)
    elif status in ("BLOCKED", "WARNING", "REJECTED", "NO_SIGNAL"):
        _LOG.warning(line, payload)
    else:
        _LOG.info(line, payload)
    return record


class DeepTestError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def load_candles(instrument: str, timeframe: str, start_ts: Optional[int],
                 end_ts: Optional[int]) -> List[dict]:
    """Candles from the CANONICAL market-data store, chronological.
    Synthetic/test databases can be injected by pointing the store path
    via market_data_service.store_db_path's environment (tests do)."""
    from hero1_market_data import store as md_store
    db = market_data_service.store_db_path()
    if not db.exists():
        return []
    conn = md_store.connect_store(db)
    try:
        sql = ("SELECT ts, open, high, low, close, volume, spread FROM candles "
               "WHERE instrument=? AND timeframe=?")
        args: list = [instrument.upper(), timeframe.upper()]
        if start_ts is not None:
            sql += " AND ts >= ?"
            args.append(int(start_ts))
        if end_ts is not None:
            sql += " AND ts <= ?"
            args.append(int(end_ts))
        sql += " ORDER BY ts ASC"
        rows = conn.execute(sql, args).fetchall()
        return [{"ts": int(r[0]), "open": float(r[1]), "high": float(r[2]),
                 "low": float(r[3]), "close": float(r[4]),
                 "volume": float(r[5]) if r[5] is not None else 0.0,
                 "spread": int(r[6]) if r[6] is not None else None} for r in rows]
    finally:
        conn.close()


def readiness(instrument: str, timeframe: str, start_ts: Optional[int],
              end_ts: Optional[int]) -> Dict[str, Any]:
    return market_data_service.data_quality(instrument, timeframe,
                                            from_epoch=start_ts, to_epoch=end_ts)


def _instrument_spec(instrument: str, cfg: dict) -> InstrumentSpec:
    snap = metadata_service.best_snapshot(instrument)
    fields = (snap or {}).get("fields") or {}
    meta = metadata_service.snapshot_broker_meta(instrument)
    m0 = (meta or {}).get(instrument.upper()) or {}
    tick_size = cfg.get("tick_size") or m0.get("tick_size") or fields.get("tick_size")
    if not tick_size:
        raise DeepTestError("MISSING_TICK_SIZE",
                            f"no tick_size for {instrument} in broker metadata; sizing refused")
    return InstrumentSpec(
        tick_size=float(tick_size),
        tick_value_account=(cfg.get("tick_value_account") or fields.get("trade_tick_value_loss")
                            or fields.get("trade_tick_value") or fields.get("trade_tick_value_profit")),
        tick_value_quote=cfg.get("tick_value_quote") or fields.get("tick_value"),
        fx_to_account=cfg.get("fx_to_account"),
        contract_size=cfg.get("contract_size") or m0.get("contract_size") or fields.get("contract_size"),
        volume_min=float(cfg.get("volume_min") or fields.get("volume_min") or 0.01),
        volume_max=float(cfg.get("volume_max") or fields.get("volume_max") or 100.0),
        volume_step=float(cfg.get("volume_step") or fields.get("volume_step") or 0.01),
    )


def build_windows(mode: str, start_ts: int, end_ts: int, cfg: dict) -> List[dict]:
    """Window plan per testing mode. The ENGINE is identical for every
    window; modes only slice the requested range (stated honestly:
    walk-forward here evaluates sequential test windows - no parameter
    optimization exists in this phase, so nothing is 'trained')."""
    if mode in ("initial", "deeptest", "robustness"):
        return [{"label": mode, "start": start_ts, "end": end_ts}]
    if mode == "oos":
        split = cfg.get("in_sample_end") or (start_ts + (end_ts - start_ts) // 2)
        return [{"label": "in_sample", "start": start_ts, "end": int(split)},
                {"label": "out_of_sample", "start": int(split), "end": end_ts}]
    if mode == "walkforward":
        train_days = int(cfg.get("wf_train_days") or 90)
        test_days = int(cfg.get("wf_test_days") or 30)
        step_days = int(cfg.get("wf_step_days") or test_days)
        max_windows = int(cfg.get("wf_max_windows") or 12)
        out = []
        cursor = start_ts + train_days * 86400
        idx = 1
        while cursor + test_days * 86400 <= end_ts and len(out) < max_windows:
            out.append({"label": f"wf_{idx}: train {train_days}d -> test {test_days}d",
                        "start": cursor, "end": cursor + test_days * 86400,
                        "train_start": cursor - train_days * 86400, "train_end": cursor})
            cursor += step_days * 86400
            idx += 1
        if not out:
            raise DeepTestError("WF_NO_WINDOWS",
                                "walk-forward needs at least train+test days inside the range")
        return out
    raise DeepTestError("BAD_MODE", f"unknown testing mode {mode!r}")


def resolve_offsets(config: dict, node_id: int, spec: dict,
                    pip_size: Optional[float]) -> dict:
    """TP/SL offset precedence with FOUR pips controls (SL+/SL-, TP+/TP-):
    1. The strategy's own TP/SL logic is never replaced - offsets only move
       the declared initial distances, never trailing/break-even logic.
    2. Individual node offsets (its +/- pair) override the global pair.
    3. Global offsets apply only when the node pair is unset.
    Within a pair the signed offset is (+away) - (-closer) in pips,
    converted to PRICE with the instrument's own pip_size from broker
    metadata. Without a pip size the offset is reported NOT applied -
    pip definitions are never assumed."""
    indiv = (config.get("node_offsets") or {}).get(str(node_id)) or {}
    out: Dict[str, Any] = {"warnings": []}
    exits = spec.get("exits") or {}
    # The strategy's own declared SL/TP, recorded verbatim for the Inspect
    # 'original strategy' display (distance descriptor or ratio; null when
    # the strategy declares none - offsets are then reported NOT applied).
    out["strategy"] = {
        kind: (exits.get(key) or None) for kind, key in
        (("sl", "stop_loss"), ("tp", "take_profit"))}
    declared = {"tp": bool((exits.get("take_profit") or {})),
                "sl": bool((exits.get("stop_loss") or {}))}
    for kind in ("tp", "sl"):
        def pair(src: dict, prefix: str):
            plus, minus = src.get(f"{prefix}_plus_pips"), src.get(f"{prefix}_minus_pips")
            if plus is None and minus is None:
                return None
            return float(plus or 0.0) - float(minus or 0.0)

        signed = pair(indiv, kind)
        source = "individual_node"
        if signed is None:
            signed = pair(config, f"global_{kind}")
            source = "global_deeptesting"
        if signed is None or signed == 0.0:
            out[kind] = {"offset_pips": 0.0, "offset_price": 0.0, "source": "strategy"}
            continue
        if not declared[kind]:
            out["warnings"].append(
                f"{kind.upper()} offset {signed:+g} pips (source: {source}) configured but the "
                f"strategy declares no {'take-profit' if kind == 'tp' else 'stop-loss'}; "
                "offset NOT applied - strategy logic is never invented or replaced")
            out[kind] = {"offset_pips": 0.0, "offset_price": 0.0, "source": "strategy"}
            continue
        if not pip_size:
            out["warnings"].append(
                f"{kind.upper()} offset {signed:+g} pips (source: {source}) cannot be converted: "
                "no pip_size in broker metadata for this instrument; offset NOT applied - "
                "pip definitions are instrument-specific and never assumed")
            out[kind] = {"offset_pips": 0.0, "offset_price": 0.0, "source": "strategy"}
            continue
        out[kind] = {"offset_pips": signed, "offset_price": signed * float(pip_size),
                     "source": source}
    return out


def _dataset_provenance(instrument: str, timeframe: str, bounds: dict) -> dict:
    """Snapshot source-file provenance from the active market-data import manifest.

    A missing or ambiguous match is kept explicit; no source is inferred from
    dates alone and no historical run record is retroactively rewritten.
    """
    try:
        manifest = json.loads(market_data_service.manifest_path().read_text(encoding="utf-8"))
        first = datetime.fromtimestamp(bounds["first_ts"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        last = datetime.fromtimestamp(bounds["last_ts"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        candidates = [d for d in manifest.get("datasets", [])
                      if str(d.get("instrument", "")).upper() == instrument.upper()
                      and str(d.get("timeframe", "")).upper() == timeframe.upper()
                      and int(d.get("row_count") or -1) == int(bounds["candles"])
                      and d.get("first_ts") == first and d.get("last_ts") == last
                      and d.get("status") == "AVAILABLE"]
        if not candidates:
            return {"status": "UNVERIFIED_NO_MATCHING_IMPORT_MANIFEST_ROW"}
        return {"status": "MATCHED_IMPORT_MANIFEST_ROW" if len(candidates) == 1 else "MULTIPLE_MATCHING_MANIFEST_ROWS",
                "candidates": [{key: d.get(key) for key in
                                ("dataset_id", "source_file", "source_sha256", "source_bytes",
                                 "row_count", "fingerprint", "first_ts", "last_ts",
                                 "validation_result", "status", "imported_at")}
                               for d in candidates]}
    except Exception as exc:  # noqa: BLE001 - provenance absence is not a test failure
        return {"status": "UNVERIFIED_MANIFEST_READ_ERROR", "detail": str(exc)}


def available_bounds(instrument: str, timeframe: str) -> Optional[dict]:
    """First/last eligible candle + count for one instrument/timeframe."""
    from hero1_market_data import store as md_store
    db = market_data_service.store_db_path()
    if not db.exists():
        return None
    conn = md_store.connect_store(db)
    try:
        row = conn.execute(
            "SELECT MIN(ts), MAX(ts), COUNT(*) FROM candles WHERE instrument=? AND timeframe=?",
            (instrument.upper(), timeframe.upper())).fetchone()
        if not row or row[2] == 0:
            return None
        return {"first_ts": int(row[0]), "last_ts": int(row[1]), "candles": int(row[2])}
    finally:
        conn.close()


def execute_run(session: Session, *, instrument: str, node: im.Node,
                config: dict, scenario_id: Optional[int] = None,
                progress_cb=None, task_id: Optional[str] = None,
                cancel_check=None) -> im.DeepTestRun:
    """Execute one node under one config snapshot and persist the result.
    `config` carries: range {start,end}, mode + mode params, RunConfig
    fields and engine assumptions. Readiness BLOCKED => FAILED run row
    with the blocking reasons (never silently tested)."""
    started = time.time()
    stage = "RUN_INITIALIZATION"
    run = im.DeepTestRun(instrument_id=instrument.upper(), node_id=node.id,
                         node_version=node.version, scenario_id=scenario_id,
                         mode=str(config.get("mode") or "initial"),
                         status="RUNNING")
    session.add(run)
    session.commit()
    session.refresh(run)
    _execution_log(stage, "STARTED", task_id=task_id, run_id=run.id, node_id=node.id, instrument=instrument, timeframe=node.timeframe, started=started)
    try:
        stage = "STRATEGY_LOADING"
        _execution_log("NODE_RETRIEVAL", "COMPLETED", task_id=task_id, run_id=run.id, node_id=node.id, instrument=instrument, started=started, details={"node_version": node.version})
        spec = json.loads(node.spec_json) if node.spec_json else None
        _execution_log(stage, "COMPLETED", task_id=task_id, run_id=run.id, node_id=node.id, instrument=instrument, timeframe=(spec or {}).get("identity", {}).get("timeframe"), started=started, details={"spec_loaded": spec is not None})
        stage = "DEEPTESTING_CONFIGURATION_LOADING"
        _execution_log(stage, "COMPLETED", task_id=task_id, run_id=run.id, node_id=node.id, instrument=instrument, started=started, details={"config_keys": sorted(config.keys()), "mode": config.get("mode"),
                        "range": config.get("range"), "spread_mode": config.get("spread_mode"),
                        "risk_mode": config.get("risk_mode"), "risk_value": config.get("risk_value"),
                        "max_active_trades": config.get("max_active_trades"),
                        "account_currency": config.get("account_currency")})
        stage = "INSTRUMENT_SPECIFICATION_LOADING"
        broker_meta_pre = spec_service._resolve_broker_meta(instrument, config.get("broker_meta"))
        _execution_log(stage, "COMPLETED", task_id=task_id, run_id=run.id, node_id=node.id, instrument=instrument, started=started, details={"metadata_available": bool(broker_meta_pre),
                        "instrument_metadata": {key: value for key, value in
                            ((broker_meta_pre or {}).get(instrument.upper()) or {}).items()
                            if key in {"point", "digits", "tick_size", "tick_value", "tick_value_loss",
                                       "tick_value_profit", "contract_size", "volume_min", "volume_max",
                                       "volume_step", "currency_base", "currency_profit", "swap_mode"}},
                        "broker_server": (metadata_service.best_snapshot(instrument) or {}).get("broker_server")})
        pip_size = ((broker_meta_pre or {}).get(instrument.upper()) or {}).get("pip_size")
        if pip_size is None and config.get("pip_size"):
            pip_size = float(config["pip_size"])
        offsets = resolve_offsets(config, node.id, spec, pip_size) if spec else None
        if offsets is not None:
            config = {**config,
                      "tp_offset_price": offsets["tp"]["offset_price"],
                      "sl_offset_price": offsets["sl"]["offset_price"],
                      "offset_resolution": offsets}
        if spec is None:
            raise DeepTestError("NODE_HAS_NO_SPEC", f"node {node.id} has no strategy specification")
        tf = (spec.get("identity") or {}).get("timeframe")
        if not tf:
            raise DeepTestError("NODE_HAS_NO_TIMEFRAME", "node spec declares no timeframe")
        run.timeframe = tf
        rng = config.get("range") or {}
        req_start, req_end = rng.get("start"), rng.get("end")
        stage = "HISTORICAL_DATA_RETRIEVAL"
        bounds = available_bounds(instrument, tf)
        _execution_log(stage, "COMPLETED" if bounds else "BLOCKED", task_id=task_id, run_id=run.id, node_id=node.id, instrument=instrument, timeframe=tf, started=started, details=bounds or {"reason": "no stored candle bounds"})
        if bounds is None:
            raise DeepTestError("DATA_BLOCKED",
                                f"no eligible candles stored for {instrument} {tf}; nothing to test")
        # DATE WINDOW (Part 10): the requested end anchors to the LATEST
        # eligible candle of this node's own instrument/timeframe - never
        # to wall-clock 'now'. When the caller asked beyond the data end,
        # the whole window re-anchors (start = latest - requested duration)
        # so a duration button always means 'that much history ending at
        # the last candle'. PARTIAL TESTING: if the (anchored) requested
        # start precedes the first stored candle, ALL available eligible
        # data is tested and the run is PARTIAL_TESTED - never labelled
        # TESTED, never blocked merely for short history.
        end_ts = min(int(req_end), bounds["last_ts"]) if req_end else bounds["last_ts"]
        if req_end and int(req_end) > bounds["last_ts"] and req_start:
            anchored_start = end_ts - (int(req_end) - int(req_start))
        else:
            anchored_start = int(req_start) if req_start else bounds["first_ts"]
        start_ts = max(anchored_start, bounds["first_ts"])
        partial = anchored_start < bounds["first_ts"]
        config = {**config, "requested_range": {"start": anchored_start, "end": end_ts},
                  "actual_range": {"start": start_ts, "end": end_ts},
                  "coverage_status": "PARTIAL_TESTED" if partial else "TESTED",
                  "candles_available": bounds["candles"],
                  "data_snapshot": {"instrument": instrument.upper(), "timeframe": tf,
                                    "first_ts": bounds["first_ts"],
                                    "last_ts": bounds["last_ts"],
                                    "candles_available": bounds["candles"],
                                    "source_provenance": _dataset_provenance(instrument, tf, bounds)}}
        stage = "CANDLE_VALIDATION"
        dq = readiness(instrument, tf, start_ts, end_ts)
        _execution_log(stage, "COMPLETED" if dq.get("readiness") != "BLOCKED" else "BLOCKED", task_id=task_id, run_id=run.id, node_id=node.id, instrument=instrument, timeframe=tf, started=started, details={"readiness": dq.get("readiness"), "candles": bounds["candles"]})
        run.data_quality_json = json.dumps(dq)
        if dq.get("readiness") == "BLOCKED":
            raise DeepTestError("DATA_BLOCKED",
                                "historical data is BLOCKED for this range: "
                                + "; ".join(dq.get("blocked_reasons") or dq.get("reasons") or []))
        broker_meta = spec_service._resolve_broker_meta(instrument, config.get("broker_meta"))
        broker_snapshot = metadata_service.best_snapshot(instrument)
        # A metadata row without a valid integrity hash is not authoritative
        # run provenance. Continue only from explicit run inputs in that case.
        if broker_snapshot and not broker_snapshot.get("integrity_verified"):
            broker_snapshot = None
        if broker_snapshot:
            config = {**config, "specification_snapshot": {
                "snapshot_id": broker_snapshot.get("snapshot_id"),
                "integrity_sha256": broker_snapshot.get("integrity_sha256"),
                "provenance_class": ("CURRENT_SPECIFICATION_USED_FOR_HISTORICAL_TEST"
                    if broker_snapshot.get("verified_source_class") == "MT5_SAVED_SNAPSHOT" or broker_snapshot.get("provenance_class") == "MT5_LIVE_CAPTURE"
                    else "RUN_CONFIG_ONLY"),
                "broker_server": broker_snapshot.get("broker_server"),
                "retrieved_at": broker_snapshot.get("retrieved_at"),
                "fingerprint": broker_snapshot.get("fingerprint"),
                "source": broker_snapshot.get("source"),
                "symbol_name": broker_snapshot.get("symbol_name") or instrument.upper(),
                "fields": broker_snapshot.get("fields") or {},
                "raw": broker_snapshot.get("raw") or {},
                "tick": broker_snapshot.get("tick") or {},
                "derived": broker_snapshot.get("derived") or {},
                "terminal": broker_snapshot.get("terminal") or {},
                "field_provenance": broker_snapshot.get("field_provenance") or {},
                "integrity_verified": bool(broker_snapshot.get("integrity_verified")),
                "source": broker_snapshot.get("source"),
                "snapshot_payload": {k: v for k, v in broker_snapshot.items()
                                     if not str(k).startswith("_") and k not in ("integrity_verified", "verified_source_class")},
                "verified_source_class": broker_snapshot.get("verified_source_class"),
                "run_use_classification": ("CURRENT_SPECIFICATION_USED_FOR_HISTORICAL_TEST"
                    if broker_snapshot.get("provenance_class") == "MT5_LIVE_CAPTURE"
                    else broker_snapshot.get("provenance_class")),
                "retrieval_timestamp": broker_snapshot.get("retrieved_at"),
                "account_currency": (broker_snapshot.get("account") or {}).get("currency"),
            }, "specification_assumption": "current MT5 snapshot used; historical snapshot unavailable"}
        else:
            config = {**config, "specification_snapshot": None,
                      "specification_assumption": "no MT5 snapshot available; run cannot claim current or historical broker specifications"}
        stage = "STRATEGY_COMPILATION_VALIDATION"
        _report, compiled = spec_service.compile_or_raise(spec, instrument, broker_meta)
        _execution_log(stage, "COMPLETED", task_id=task_id, run_id=run.id, node_id=node.id, instrument=instrument, timeframe=tf, started=started, details={"fingerprint": compiled.fingerprint, "compiler_version": compiled.compiler_version})
        run.fingerprint = compiled.fingerprint
        run.compiler_version = compiled.compiler_version
        run.engine_version = ENGINE_VERSION

        spec_fields = (broker_snapshot or {}).get("fields") or {}
        chart_mode = config.get("chart_price_mode", spec_fields.get("chart_mode"))
        if chart_mode is not None and str(chart_mode).upper() not in ("0", "BID", "BY_BID"):
            raise DeepTestError("UNSUPPORTED_CHART_PRICE_MODE",
                                f"chart mode {chart_mode!r} is not Bid OHLC; current execution semantics cannot derive bid/ask OHLC without fabricating prices")
        config = {**config, "chart_price_mode": chart_mode,
                  "ohlc_price_basis": "BID" if chart_mode is not None else "UNVERIFIED; engine expects BID OHLC"}
        mt5_swap_mode = spec_fields.get("swap_mode")
        if ((mt5_swap_mode is None or str(mt5_swap_mode) not in ("0", "1"))
                and config.get("swap_type") is None
                and (spec_fields.get("swap_long") or spec_fields.get("swap_short"))):
            raise DeepTestError("UNSUPPORTED_SWAP_MODE",
                                f"MT5 swap mode {mt5_swap_mode} needs an explicit supported conversion; no silent approximation")
        inferred_swap_type = "points" if str(mt5_swap_mode) == "1" else "money_per_lot"
        mt5_triple_day = spec_fields.get("swap_rollover3days")
        inferred_triple_weekday = ((int(mt5_triple_day) - 1) % 7
                                   if mt5_triple_day is not None else 2)
        selected_spread_mode = str(config.get("spread_mode") or ("historical_points" if ((broker_meta or {}).get(instrument.upper()) or {}).get("point_size") else "fixed"))
        if selected_spread_mode not in ("historical_points", "no_spread", "fixed"):
            raise DeepTestError("INVALID_SPREAD_MODE", f"unsupported spread mode {selected_spread_mode!r}")
        rcfg = RunConfig(
            initial_capital=float(config.get("initial_capital") or 10000.0),
            account_currency=str(config.get("account_currency") or "USD"),
            risk_mode=str(config.get("risk_mode") or "percent"),
            risk_value=float(config.get("risk_value") if config.get("risk_value") is not None else 1.0),
            risk_basis=str(config.get("risk_basis") or "current_equity"),
            max_active_trades=int(config.get("max_active_trades") or 1),
            spread_price=float(config.get("spread_price") or 0.0),
            spread_mode=selected_spread_mode,
            point_size=float((((broker_meta or {}).get(instrument.upper()) or {}).get("point_size") or 0.0)) or None,
            missing_spread_policy=str(config.get("missing_spread_policy") or "block"),
            slippage_price=float(config.get("slippage_price") or 0.0),
            commission_per_lot_round_turn=float(config.get("commission_per_lot_round_turn") or 0.0),
            commission_per_lot_per_side=float(config.get("commission_per_lot_per_side") or 0.0),
            commission_mode=str(config.get("commission_mode") or "round_turn"),
            swap_long_per_lot_night=float(config.get("swap_long_per_lot_night") if config.get("swap_long_per_lot_night") is not None else (spec_fields.get("swap_long") or 0.0)),
            swap_short_per_lot_night=float(config.get("swap_short_per_lot_night") if config.get("swap_short_per_lot_night") is not None else (spec_fields.get("swap_short") or 0.0)),
            swap_type=str(config.get("swap_type") or inferred_swap_type),
            triple_swap_weekday=int(config.get("triple_swap_weekday", inferred_triple_weekday)),
            ambiguity_policy=str(config.get("ambiguity_policy") or "sl_first"),
            sessions=config.get("sessions"), weekdays=config.get("weekdays"),
            months=config.get("months"),
            tp_offset_price=float(config.get("tp_offset_price") or 0.0),
            sl_offset_price=float(config.get("sl_offset_price") or 0.0),
        )
        config = {**config, "spread_mode": selected_spread_mode,
                  "missing_spread_policy": rcfg.missing_spread_policy,
                  "spread_price": rcfg.spread_price,
                  "slippage_price": rcfg.slippage_price,
                  "commission_mode": rcfg.commission_mode,
                  "commission_per_lot_round_turn": rcfg.commission_per_lot_round_turn,
                  "commission_per_lot_per_side": rcfg.commission_per_lot_per_side,
                  "swap_type": rcfg.swap_type}
        stage = "INSTRUMENT_SPECIFICATION_LOADING"
        spec_meta = _instrument_spec(instrument, config)
        m0 = ((broker_meta or {}).get(instrument.upper())) or {}
        _execution_log(stage, "COMPLETED", task_id=task_id, run_id=run.id,
                       node_id=node.id, instrument=instrument, timeframe=tf, started=started,
                       details={"tick_size": spec_meta.tick_size,
                                "tick_value_account": spec_meta.tick_value_account,
                                "tick_value_quote": spec_meta.tick_value_quote,
                                "contract_size": spec_meta.contract_size,
                                "volume_min": spec_meta.volume_min,
                                "volume_max": spec_meta.volume_max,
                                "volume_step": spec_meta.volume_step})
        config = {**config, "execution_specification_inputs": {
            "verification_status": ("CURRENT_MT5_SNAPSHOT_NOT_HISTORICALLY_VERIFIED" if broker_snapshot
                                     else "UNVERIFIED_NO_MT5_SNAPSHOT; explicit run inputs used"),
            "tick_size": spec_meta.tick_size,
            "tick_size_source": ("RUN_CONFIG" if config.get("tick_size") is not None or m0.get("tick_size") is not None else
                                 "MT5" if spec_fields.get("tick_size") is not None else "UNAVAILABLE"),
            "tick_value_account": spec_meta.tick_value_account,
            "tick_value_account_source": ("RUN_CONFIG" if config.get("tick_value_account") is not None else
                                           "MT5" if any(spec_fields.get(k) is not None for k in
                                                        ("trade_tick_value_loss", "trade_tick_value", "trade_tick_value_profit"))
                                           else "DERIVED" if spec_meta.tick_value_quote is not None and spec_meta.fx_to_account is not None
                                           else "UNAVAILABLE"),
            "tick_value_quote_source": ("RUN_CONFIG" if config.get("tick_value_quote") is not None else
                                        "MT5" if spec_fields.get("tick_value") is not None else "UNAVAILABLE"),
            "tick_value_quote": spec_meta.tick_value_quote,
            "contract_size": spec_meta.contract_size,
            "contract_size_source": ("RUN_CONFIG" if config.get("contract_size") is not None or m0.get("contract_size") is not None else
                                      "MT5" if spec_fields.get("contract_size") is not None else "UNAVAILABLE"),
            "point_size": m0.get("point_size"), "pip_size": m0.get("pip_size"),
            "point_pip_size_source": ("RUN_CONFIG" if m0 or config.get("point_size") is not None or config.get("pip_size") is not None
                                      else "MT5" if broker_snapshot else "UNAVAILABLE"),
            "volume_min": spec_meta.volume_min, "volume_max": spec_meta.volume_max,
            "volume_step": spec_meta.volume_step,
            "volume_limits_source": ("RUN_CONFIG" if any(config.get(k) is not None for k in
                                     ("volume_min", "volume_max", "volume_step")) else
                                     "MT5" if any(spec_fields.get(k) is not None for k in
                                                  ("volume_min", "volume_max", "volume_step")) else
                                     "UNAVAILABLE"),
            "broker_snapshot_present": bool(broker_snapshot),
            "digits": config.get("digits"),
            "currency_profit": config.get("currency_profit"),
            "currency_margin": config.get("currency_margin"),
            "chart_price_mode": config.get("chart_price_mode"),
            "spread_mode": rcfg.spread_mode,
            "swap_type": rcfg.swap_type,
            "swap_long_per_lot_night": rcfg.swap_long_per_lot_night,
            "swap_short_per_lot_night": rcfg.swap_short_per_lot_night,
            "triple_swap_weekday": rcfg.triple_swap_weekday,
            "commission_per_lot_round_turn": rcfg.commission_per_lot_round_turn,
            "commission_per_lot_per_side": rcfg.commission_per_lot_per_side,
            "slippage_price": rcfg.slippage_price}}
        price_ctx = PriceContext(pip_size=m0.get("pip_size"), point_size=m0.get("point_size"),
                                 tick_size=m0.get("tick_size"))
        windows = build_windows(run.mode, int(start_ts), int(end_ts), config) \
            if start_ts and end_ts else [{"label": run.mode, "start": start_ts, "end": end_ts}]
        all_trades: List[dict] = []
        all_equity: List[dict] = []
        all_skipped: List[dict] = []
        run.config_json = json.dumps(config)
        run.assumptions_json = json.dumps({
            "spread_price": rcfg.spread_price,
            "spread_source": ("historical candle spread points x instrument point size"
                              if rcfg.spread_mode == "historical_points" else
                              "NO SPREAD: zero-spread assumption; no spread adjustment applied" if rcfg.spread_mode == "no_spread" else
                              "configured constant spread assumption" if rcfg.spread_price else
                              "zero spread assumption; historical spread not enabled/point size unavailable"),
            "spread_mode": rcfg.spread_mode,
            "missing_spread_policy": rcfg.missing_spread_policy,
            "commission_status": ("CONFIGURED" if (rcfg.commission_per_lot_per_side or rcfg.commission_per_lot_round_turn)
                                  else "COMMISSION NOT CONFIGURED — incomplete-cost testing"),
            "slippage_status": ("configured" if rcfg.slippage_price else "ASSUMPTION: zero slippage"),
            "slippage_price": rcfg.slippage_price,
            "commission_per_lot_round_turn": rcfg.commission_per_lot_round_turn,
            "swap_long_per_lot_night": rcfg.swap_long_per_lot_night,
            "swap_short_per_lot_night": rcfg.swap_short_per_lot_night,
            "ambiguity_policy": rcfg.ambiguity_policy,
            "tick_value_basis": ("account-currency tick value supplied in run config; not independently verified"
                                 if config.get("tick_value_account") is not None
                                 else "account-currency tick value from stored MT5 snapshot"
                                 if broker_snapshot and any(spec_fields.get(k) is not None for k in
                                                            ("trade_tick_value_loss", "trade_tick_value",
                                                             "trade_tick_value_profit"))
                                 else "quote tick value x configured fx_to_account"),
            "engine_version": ENGINE_VERSION, "windows": windows,
            "risk_basis": rcfg.risk_basis, "max_active_trades": rcfg.max_active_trades,
            "effective_offsets": offsets,
            "requested_range": config.get("requested_range"),
            "actual_range": config.get("actual_range"),
            "coverage_status": config.get("coverage_status"),
        })
        session.commit()

        # Multi-window modes execute per window and persist per-window runs;
        # this row represents the first window; siblings are created below.
        first = True
        reqs = (compiled.ir or {}).get("data_requirements") or {}
        aux_candles = None
        if reqs.get("reference_levels") or reqs.get("m1_momentum"):
            # Finest series (M1) for timeframe-independent reference levels
            # and M1 momentum; widened backwards so previous-day/session
            # levels exist from the first tested bar. Absence is recorded,
            # never silently substituted (momentum stays unevaluable).
            aux_candles = load_candles(instrument, "M1",
                                       int(start_ts) - 4 * 86400, int(end_ts)) or None
            _assump = json.loads(run.assumptions_json or "{}")
            _assump["reference_data_basis"] = (
                "M1 candles (finest series) for reference levels and M1 momentum"
                if aux_candles else
                "M1 unavailable: reference levels fall back to the node timeframe; "
                "M1 momentum is NOT substituted and stays unevaluable (no entries)")
            _assump["data_requirements"] = reqs
            run.assumptions_json = json.dumps(_assump)
            session.commit()
        for window in windows:
            stage = "HISTORICAL_DATA_RETRIEVAL"
            candles = load_candles(instrument, tf, window["start"], window["end"])
            _execution_log(stage, "COMPLETED", task_id=task_id, run_id=run.id, node_id=node.id, instrument=instrument, timeframe=tf, started=started, details={"candle_count": len(candles), "window": window})
            stage = "SPREAD_RETRIEVAL_VALIDATION"
            spread_missing = sum(c.get("spread") is None for c in candles)
            _execution_log(stage, "COMPLETED" if not spread_missing or rcfg.spread_mode != "historical_points" else "WARNING", task_id=task_id, run_id=run.id, node_id=node.id, instrument=instrument, timeframe=tf, started=started, details={"mode": rcfg.spread_mode, "missing_count": spread_missing, "point_size": rcfg.point_size, "policy": rcfg.missing_spread_policy})
            if len(candles) > MAX_CANDLES_PER_RUN:
                raise DeepTestError("RANGE_TOO_LARGE",
                                    f"{len(candles)} candles exceed the {MAX_CANDLES_PER_RUN} per-run cap")
            mgmt = None
            if spec.get("management"):
                mgmt = TradeManagement.model_validate(spec["management"])
            stage = "ENGINE_EXECUTION"
            _execution_log("INDICATOR_INITIALIZATION", "STARTED", task_id=task_id, run_id=run.id, node_id=node.id, instrument=instrument, timeframe=tf, started=started)
            def _stage_cb(engine_stage, engine_status, engine_details):
                _execution_log(engine_stage, engine_status, task_id=task_id,
                               run_id=run.id, node_id=node.id, instrument=instrument,
                               timeframe=tf, started=started, details=engine_details)

            def _persist_decision_batch(batch):
                # Bounded, append-only persistence. Stable event IDs make retries idempotent.
                from app.decision_ledger import persist_event_batch
                persist_event_batch(session, batch, run_id=run.id, node_id=node.id,
                    node_version=node.version, fingerprint=compiled.fingerprint,
                    instrument=instrument, timeframe=tf)
            result = run_backtest(ir=compiled.ir, candles=candles, cfg=rcfg,
                                  instrument=spec_meta, price_ctx=price_ctx, management=mgmt,
                                  aux_candles=aux_candles, progress_cb=progress_cb,
                                  stage_cb=_stage_cb,
                                  detailed_logging=bool(config.get("detailed_logging")),
                                  cancel_check=cancel_check,
                                  evidence_context={"run_id": run.id, "node_id": node.id,
                                      "node_version": node.version, "fingerprint": compiled.fingerprint,
                                      "compiler_version": compiled.compiler_version, "instrument": instrument.upper(),
                                      "timeframe": tf,
                                      "specification_snapshot_id": (config.get("specification_snapshot") or {}).get("snapshot_id"),
                                      "specification_provenance": (config.get("specification_snapshot") or {}).get("provenance_class", "RUN_CONFIG_ONLY"),
                                      "profit_currency": config.get("currency_profit")}, ledger_cb=_persist_decision_batch)
            _execution_log("ENGINE_EXECUTION", "COMPLETED", task_id=task_id, run_id=run.id, node_id=node.id, instrument=instrument, timeframe=tf, started=started, details={"signals_skipped": len(result.get("skipped_signals", [])), "trades": len(result.get("trades", [])), "warnings": len(result.get("notices", []))})
            assumptions = json.loads(run.assumptions_json or "{}")
            execution_warnings = list(result.get("notices", []))
            if assumptions.get("commission_status", "").startswith("COMMISSION NOT CONFIGURED"):
                execution_warnings.append({"code": "COMMISSION_NOT_CONFIGURED", "status": "INCOMPLETE COST TESTING"})
            if rcfg.spread_mode == "no_spread":
                execution_warnings.append({"code": "NO_SPREAD_ASSUMPTION", "status": "ZERO SPREAD; NOT REALISTIC BROKER EXECUTION"})
            if rcfg.slippage_price == 0:
                execution_warnings.append({"code": "ZERO_SLIPPAGE_ASSUMPTION", "status": "ASSUMPTION"})
            assumptions["execution_warnings"] = execution_warnings
            assumptions["completion_status"] = "COMPLETED_WITH_WARNINGS" if execution_warnings else "COMPLETED"
            run.assumptions_json = json.dumps(assumptions)
            stage = "METRICS_CALCULATION"
            metrics = compute_metrics(result["trades"], result["equity_curve"], rcfg.initial_capital)
            _execution_log(stage, "COMPLETED", task_id=task_id, run_id=run.id, node_id=node.id, instrument=instrument, timeframe=tf, started=started, details={"trade_count": len(result["trades"])})
            metrics["candles_tested"] = len(candles)
            metrics["coverage_status"] = config.get("coverage_status")
            metrics["requested_range"] = config.get("requested_range")
            metrics["actual_range"] = config.get("actual_range")
            metrics["time_buckets"] = time_buckets(result["trades"])
            target = run if first else im.DeepTestRun(
                instrument_id=instrument.upper(), node_id=node.id, node_version=node.version,
                scenario_id=scenario_id, mode=run.mode, status="RUNNING",
                timeframe=tf, fingerprint=compiled.fingerprint,
                compiler_version=compiled.compiler_version, engine_version=ENGINE_VERSION,
                config_json=run.config_json, data_quality_json=run.data_quality_json,
                assumptions_json=run.assumptions_json)
            if not first:
                session.add(target)
                session.flush()
            target.window_label = window["label"]
            target.metrics_json = json.dumps(metrics)
            target.equity_json = json.dumps(result["equity_curve"])
            target.skipped_json = json.dumps(result["skipped_signals"])
            target.decision_ledger_json = json.dumps(result.get("decision_ledger", []), separators=(",", ":"))
            target.status = "DONE"
            target.finished_at = time.time()
            for t in result["trades"]:
                session.add(im.DeepTestRunTrade(
                    instrument_id=instrument.upper(), run_id=target.id, node_id=node.id,
                    side=t["side"], volume=t["volume"], entry_ts=t["entry_ts"],
                    entry_price=t["entry_price"], exit_ts=t["exit_ts"],
                    exit_price=t["exit_price"], exit_reason=t["exit_reason"],
                    net_pnl=t["net_pnl"], detail_json=json.dumps(t)))
            stage = "RESULT_PERSISTENCE"
            session.commit()
            _execution_log(stage, "COMPLETED", task_id=task_id, run_id=target.id, node_id=node.id, instrument=instrument, timeframe=tf, started=started, details={"trade_count": len(result["trades"])})
            first = False
        _execution_log("FINAL_TASK_COMPLETION", "COMPLETED", task_id=task_id, run_id=run.id, node_id=node.id, instrument=instrument, timeframe=run.timeframe, started=started, details={"status": "DONE"})
        return run
    except (DeepTestError, EngineError) as exc:
        code = getattr(exc, "code", "ERROR")
        blocked_codes = {"DATA_BLOCKED", "HISTORICAL_SPREAD_UNAVAILABLE", "MISSING_TICK_SIZE",
                         "MISSING_TICK_VALUE", "UNSUPPORTED_CHART_PRICE_MODE", "UNSUPPORTED_SWAP_MODE",
                         "INVALID_SPREAD_MODE"}
        failure_stage = ("SPREAD_RETRIEVAL_VALIDATION" if code == "HISTORICAL_SPREAD_UNAVAILABLE" else
                         "POSITION_SIZING" if code == "MISSING_TICK_VALUE" else
                         "INSTRUMENT_SPECIFICATION_LOADING" if code in ("MISSING_TICK_SIZE", "UNSUPPORTED_SWAP_MODE") else
                         "CANDLE_VALIDATION" if code == "DATA_BLOCKED" else stage)
        detail = _execution_log(failure_stage, "BLOCKED" if code in blocked_codes else "FAILED",
            task_id=task_id, run_id=run.id, node_id=node.id, instrument=instrument,
            timeframe=run.timeframe, started=started, exc=exc)
        run.status = "CANCELLED" if code == "CANCELLED" else "FAILED"
        run.error = (f"{code}: {exc}\n"
                     f"Stage: {failure_stage}; Exception: {detail.get('exception_type')}\n"
                     f"Location: {detail.get('exception_file')}:{detail.get('exception_line')} "
                     f"in {detail.get('exception_function')}\n{detail.get('traceback')}")
        run.finished_at = time.time()
        session.commit()
        return run
    except Exception as exc:  # noqa: BLE001 - recorded, never hidden
        detail = _execution_log(stage, "FAILED", task_id=task_id, run_id=run.id, node_id=node.id, instrument=instrument, timeframe=run.timeframe, started=started, exc=exc)
        leaf_function = detail.get("exception_function")
        failure_stage = {"size_position": "POSITION_SIZING",
                         "evaluate_tree": "ENTRY_CONDITION_EVALUATION",
                         "_entry_side": "ENTRY_CONDITION_EVALUATION",
                         "_check_sl_tp": "TRADE_MANAGEMENT",
                         "close_position": "EXIT_EXECUTION",
                         "load_candles": "HISTORICAL_DATA_RETRIEVAL"}.get(leaf_function, stage)
        run.status = "FAILED"
        run.error = (f"UNEXPECTED {detail.get('exception_type')} at {detail.get('exception_file')}:{detail.get('exception_line')} "
                     f"({leaf_function}): {detail.get('exception_message')}\n"
                     f"Stage: {failure_stage}\n{detail.get('traceback')}")
        run.finished_at = time.time()
        session.commit()
        return run


def run_summary(run: im.DeepTestRun) -> dict:
    metrics = json.loads(run.metrics_json) if run.metrics_json else None
    assumptions = json.loads(run.assumptions_json) if run.assumptions_json else {}
    return {
        "id": run.id, "node_id": run.node_id, "node_version": run.node_version,
        "scenario_id": run.scenario_id, "mode": run.mode, "window_label": run.window_label,
        "status": run.status,
        "completion_status": assumptions.get("completion_status", run.status),
        "instrument": run.instrument_id, "timeframe": run.timeframe,
        "fingerprint": run.fingerprint, "compiler_version": run.compiler_version,
        "engine_version": run.engine_version, "error": run.error,
        "started_at": run.started_at, "finished_at": run.finished_at,
        "config": json.loads(run.config_json) if run.config_json else None,
        "metrics": metrics,
        "requested_range": (json.loads(run.config_json).get("requested_range")
                            if run.config_json else None),
        "coverage_status": metrics.get("coverage_status") if metrics else None,
    }


INSPECT_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


def inspect_cell(entry_ts: float) -> tuple:
    """The Inspect grid's classification (single source shared with the
    dashboard): weekday Mon-Fri by UTC entry time, weekend entries are
    UNASSIGNED; weekend session classification is also UNASSIGNED. Weekday
    sessions are contiguous non-overlapping UTC windows ASIA 22-08,
    ASIA-LONDON 08-09, LONDON 09-13, LONDON-NY 13-17, NY 17-22. Every trade
    lands in exactly one weekday/session cell."""
    dt = datetime.fromtimestamp(entry_ts, tz=timezone.utc)
    weekday = INSPECT_WEEKDAYS[dt.weekday()] if dt.weekday() < 5 else "UNASSIGNED"
    if weekday == "UNASSIGNED":
        return "UNASSIGNED", "UNASSIGNED"
    h = dt.hour
    if h >= 22 or h < 8:
        session = "ASIA"
    elif h < 9:
        session = "ASIA-LONDON"
    elif h < 13:
        session = "LONDON"
    elif h < 17:
        session = "LONDON-NY"
    else:
        session = "NY"
    return weekday, session


_TRADE_SORTS = {
    "id": lambda t: t.get("id"), "entry_ts": lambda t: t.get("entry_ts"),
    "exit_ts": lambda t: t.get("exit_ts"), "weekday": lambda t: t.get("weekday"),
    "session": lambda t: t.get("session"), "side": lambda t: t.get("side"),
    "entry_price": lambda t: t.get("entry_price"),
    "exit_price": lambda t: t.get("exit_price"),
    "initial_stop": lambda t: t.get("initial_stop"),
    "take_profit": lambda t: t.get("take_profit"),
    "volume": lambda t: t.get("volume"), "gross_pnl": lambda t: t.get("gross_pnl"),
    "net_pnl": lambda t: t.get("net_pnl"), "result": lambda t: t.get("result"),
    "holding_seconds": lambda t: t.get("holding_seconds"),
    "exit_reason": lambda t: t.get("exit_reason"),
}


def trade_matches_filters(t: dict, f: Optional[dict]) -> bool:
    """Structured trade-table filters, AND logic (Part 8). Session and
    weekday come from inspect_cell - the same single classification used
    by the weekday/session statistics, so a trade is in exactly one
    session bucket everywhere."""
    if not f:
        return True
    if f.get("side") and t.get("side") != f["side"]:
        return False
    net = t.get("net_pnl")
    if f.get("outcome"):
        res = "WIN" if (net or 0) > 1e-9 else "LOSS" if (net or 0) < -1e-9 else "BREAKEVEN"
        if res != f["outcome"]:
            return False
    if f.get("session") or f.get("weekday"):
        wd, sess = inspect_cell(float(t.get("entry_ts") or 0))
        if f.get("session") == "UNASSIGNED":
            if wd != "UNASSIGNED" or sess != "UNASSIGNED":
                return False
        elif f.get("session") and sess != f["session"]:
            return False
        if f.get("weekday") and wd != f["weekday"]:
            return False
    if f.get("entry_from") is not None and float(t.get("entry_ts") or 0) < float(f["entry_from"]):
        return False
    if f.get("entry_to") is not None and float(t.get("entry_ts") or 0) > float(f["entry_to"]):
        return False
    if f.get("exit_reason") and t.get("exit_reason") != f["exit_reason"]:
        return False
    if f.get("min_net") is not None and (net is None or net < float(f["min_net"])):
        return False
    if f.get("max_net") is not None and (net is None or net > float(f["max_net"])):
        return False
    held = t.get("holding_seconds")
    if f.get("min_hold") is not None and (held is None or held < float(f["min_hold"])):
        return False
    if f.get("max_hold") is not None and (held is None or held > float(f["max_hold"])):
        return False
    return True


def paginate_trades(trades: List[dict], *, page: int, page_size,
                    sort: str = "entry_ts", direction: str = "asc",
                    cells: Optional[List[str]] = None,
                    filters: Optional[dict] = None) -> dict:
    """Server-side filter + sort + pagination over the FULL filtered set.
    Stable: Trade ID is the final tie-break; nulls sort last in both
    directions. Page size is capped at 25 unless the caller explicitly
    requests the complete filtered set with page_size='all'."""
    want_all = page_size == "all"
    if not want_all:
        page_size = max(1, min(int(page_size), 25))
    page = max(0, int(page))
    rows = []
    for t in trades:
        weekday, session = inspect_cell(float(t.get("entry_ts") or 0))
        rows.append({**t, "weekday": weekday, "session": session})
    if cells is not None:
        wanted = set(cells)
        rows = [t for t in rows if f"{t['weekday']}|{t['session']}" in wanted]
    if filters:
        rows = [t for t in rows if trade_matches_filters(t, filters)]
    total = len(rows)
    key_fn = _TRADE_SORTS.get(sort, _TRADE_SORTS["entry_ts"])
    present = [t for t in rows if key_fn(t) is not None]
    missing = [t for t in rows if key_fn(t) is None]
    present.sort(key=lambda t: t.get("id") or 0)
    present.sort(key=key_fn, reverse=(direction == "desc"))
    missing.sort(key=lambda t: t.get("id") or 0)
    ordered = present + missing
    if want_all:
        return {"trades": ordered, "count": total, "page": 0,
                "page_size": "all", "pages": 1,
                "sort": sort if sort in _TRADE_SORTS else "entry_ts",
                "dir": direction}
    pages = max(1, (total + page_size - 1) // page_size)
    start = page * page_size
    return {"trades": ordered[start:start + page_size], "count": total,
            "page": page, "page_size": page_size, "pages": pages,
            "sort": sort if sort in _TRADE_SORTS else "entry_ts",
            "dir": direction}


def scenario_hash(config: dict) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def generate_scenarios(dimensions: dict, base_config: dict, max_count: int = 200) -> List[dict]:
    """Cartesian scenario preview/generation with an explosion guard.
    dimensions: {sessions?: [[...]], weekdays?: [[...]], tp_offset_pct?: [..],
    sl_offset_pct?: [..], risk_value?: [..], max_active_trades?: [..]}"""
    import itertools
    keys = [k for k, v in dimensions.items() if v]
    combos = 1
    for k in keys:
        combos *= len(dimensions[k])
    if combos > max_count:
        raise DeepTestError("SCENARIO_EXPLOSION",
                            f"dimensions produce {combos} scenarios, above the {max_count} cap; "
                            "narrow the ranges or raise the cap explicitly")
    out = []
    for values in itertools.product(*(dimensions[k] for k in keys)):
        cfg = dict(base_config)
        label = []
        for k, v in zip(keys, values):
            cfg[k] = v
            label.append(f"{k}={v}")
        out.append({"name": " · ".join(label) or "base", "config": cfg})
    return out


def fine_tune_suggestions(session: Session, run: im.DeepTestRun) -> List[dict]:
    """Evidence-based suggestions from a run's REAL trade ledger. Each
    suggestion is a concrete, explainable spec/config patch; nothing is
    applied autonomously."""
    trades = session.query(im.DeepTestRunTrade).filter_by(run_id=run.id).all()
    details = [json.loads(t.detail_json) for t in trades if t.detail_json]
    out: List[dict] = []
    if len(details) < 5:
        return [{"kind": "insufficient_data",
                 "rationale": f"only {len(details)} trades - too few to suggest changes honestly",
                 "patch": None}]
    by_session: Dict[str, list] = {}
    for t in details:
        by_session.setdefault(t.get("session"), []).append(t["net_pnl"])
    worst = min(by_session.items(), key=lambda kv: sum(kv[1]))
    if len(by_session) > 1 and sum(worst[1]) < 0:
        out.append({"kind": "exclude_session", "session": worst[0],
                    "rationale": f"session {worst[0]} lost {sum(worst[1]):.2f} over {len(worst[1])} trades "
                                 f"while other sessions performed better; test excluding it as a scenario filter",
                    "patch": {"config": {"sessions_exclude": [worst[0]]}}})
    losses = [t for t in details if t["net_pnl"] < 0]
    stopped = [t for t in losses if t["exit_reason"] == "STOP_LOSS"]
    if losses and len(stopped) / len(losses) > 0.8:
        out.append({"kind": "widen_sl", "multiplier": 1.25,
                    "rationale": f"{len(stopped)}/{len(losses)} losses are stop-outs; test a 25% wider "
                                 "initial stop as a fine-tuned child (risk sizing re-derives volume)",
                    "patch": {"spec": {"sl_multiplier": 1.25}}})
    wins = [t for t in details if t["net_pnl"] > 0]
    trailed = [t for t in wins if t.get("stop_history")]
    if wins and len(trailed) / len(wins) < 0.3:
        out.append({"kind": "add_stepped_trailing",
                    "rationale": f"only {len(trailed)}/{len(wins)} winners used trailing management; "
                                 "test a stepped trailing child to lock in favourable excursion",
                    "patch": {"spec": {"add_stepped_trailing": {
                        "activation_pips": 30, "step_pips": 30, "advance_pips": 30}}}})
    return out


__all__ = ["DeepTestError", "execute_run", "run_summary", "load_candles",
           "readiness", "generate_scenarios", "fine_tune_suggestions",
           "scenario_hash", "apply_trade_filters", "rank_runs"]


# --------------------------------------------------------------------------
# Background batch execution with shared task progress (Sections 1/3/13).
# The synchronous POST /runs path is unchanged; async mode runs the SAME
# execute_run per node on a daemon thread and reports through the shared
# dashboard task record - no parallel engine, no invented progress.
# --------------------------------------------------------------------------

def run_batch_async(*, instrument: str, node_ids: List[int], config: dict,
                    task_id: str) -> None:
    import threading

    cancel_event = __import__('threading').Event()
    with _CANCEL_LOCK:
        _CANCEL_EVENTS[task_id] = cancel_event

    def _work() -> None:
        from hero1_eventbus import get_event_publisher
        from hero1_storage.engine import instrument_session, registry_session
        from hero1_storage.registry_models import DashboardTask
        from app import task_service

        worker_started = time.time()
        _execution_log("TASK_INITIALIZATION", "STARTED", task_id=task_id, instrument=instrument,
                       started=worker_started, details={"node_ids": node_ids, "mode": config.get("mode")})
        reg = None
        sess = None
        try:
            publisher = get_event_publisher()
            reg = registry_session()
            sess = instrument_session(instrument)
            task = reg.get(DashboardTask, task_id)
            if task is None:
                raise DeepTestError("TASK_NOT_FOUND", f"dashboard task {task_id} does not exist")
            payload = json.loads(task.payload_json or "{}")
            entries = payload.get("nodes") or []
            by_id = {e["node_id"]: e for e in entries}
            done_units = 0
            failures = 0
            blocked_count = 0
            for node_id in node_ids:
                entry = by_id.get(node_id)
                _execution_log("NODE_RETRIEVAL", "STARTED", task_id=task_id, node_id=node_id,
                               instrument=instrument, started=worker_started)
                node = sess.query(im.Node).filter_by(id=node_id).first()
                _execution_log("NODE_RETRIEVAL", "COMPLETED" if node else "BLOCKED",
                               task_id=task_id, node_id=node_id, instrument=instrument,
                               started=worker_started, details={"found": node is not None})
                if node is None:
                    if entry is not None:
                        entry.update({"status": "BLOCKED", "stage": "NODE_RETRIEVAL",
                                      "error_code": "NODE_NOT_FOUND",
                                      "error": f"NODE_NOT_FOUND: node {node_id}"})
                    failures += 1
                    blocked_count += 1
                    done_units += 1
                    task_service.update_task(reg, task_id, completed_units=done_units,
                                             payload=payload)
                    continue
                if entry is not None:
                    entry.update({"status": "RUNNING", "trades": 0,
                                  "progress_pct": 0.0, "processed": 0,
                                  "total_candles": None})
                task_service.update_task(
                    reg, task_id, current_unit=f"Node {node_id} — {node.name}",
                    current_unit_progress=0.0, payload=payload)
                publisher.publish(category="DEEPTEST", severity="INFO",
                                  subsystem="deeptesting",
                                  message=f"deep test started [{instrument}] node #{node_id} {node.name}",
                                  instrument=instrument, node_id=node_id,
                                  payload={"task_id": task_id, "node_id": node_id})
                state = {"last": 0.0}

                def _cb(processed, total, trades_closed, _entry=entry,
                        _node_id=node_id):
                    import time as _time
                    now = _time.time()
                    if now - state["last"] < 0.4 and processed < total:
                        return
                    state["last"] = now
                    pct = min(99.0, round(100.0 * processed / total, 1)) if total else None
                    if _entry is not None:
                        _entry.update({"progress_pct": pct, "processed": processed,
                                       "total_candles": total, "trades": trades_closed})
                    task_service.update_task(reg, task_id,
                                             current_unit_progress=pct,
                                             payload=payload)

                run = execute_run(sess, instrument=instrument, node=node,
                                  config=dict(config),
                                  scenario_id=config.get("scenario_id"),
                                  progress_cb=_cb, task_id=task_id,
                                  cancel_check=cancel_event.is_set)
                trades_n = 0
                if run.metrics_json:
                    trades_n = int((json.loads(run.metrics_json) or {}).get("total_trades") or 0)
                if entry is not None:
                    if run.status == "CANCELLED":
                        entry.update({"status": "CANCELLED", "trades": trades_n,
                                      "run_id": run.id, "error": run.error,
                                      "progress_pct": entry.get("progress_pct", 0.0)})
                    elif run.status == "DONE":
                        run_warnings = (json.loads(run.assumptions_json or "{}").get("execution_warnings") or [])
                        entry.update({"status": "COMPLETED_WITH_WARNINGS" if run_warnings else "COMPLETED",
                                      "trades": trades_n, "progress_pct": 100.0,
                                      "run_id": run.id,
                                      "warnings": run_warnings or None})
                    else:
                        error_code = (run.error or "").split(":", 1)[0].split()[0]
                        blocked = error_code in BLOCKED_ERROR_CODES
                        entry.update({"status": "BLOCKED" if blocked else "FAILED",
                                      "trades": trades_n, "run_id": run.id,
                                      "stage": (run.error or "").split("Stage: ", 1)[-1].split(";", 1)[0] if "Stage: " in (run.error or "") else "ENGINE_EXECUTION",
                                      "error_code": error_code,
                                      "error": run.error, "technical_details": run.error})
                        failures += 1
                        if blocked:
                            blocked_count += 1
                if run.status != "CANCELLED":
                    done_units += 1
                task_service.update_task(reg, task_id, completed_units=done_units,
                                         current_unit_progress=(entry.get("progress_pct", 0.0) if run.status == "CANCELLED" and entry else None),
                                         payload=payload)
                _execution_log("NODE_EXECUTION_RESULT", "COMPLETED" if run.status == "DONE" else ("BLOCKED" if entry and entry.get("status") == "BLOCKED" else "FAILED"),
                               task_id=task_id, run_id=run.id, node_id=node_id, instrument=instrument,
                               timeframe=run.timeframe, started=worker_started,
                               details={"status": run.status, "trades": trades_n, "error": run.error})
                publisher.publish(
                    category="DEEPTEST",
                    severity="INFO" if run.status == "DONE" else "ERROR",
                    subsystem="deeptesting",
                    message=f"deep test {'completed' if run.status == 'DONE' else 'failed'} "
                            f"[{instrument}] node #{node_id} ({trades_n} trades)",
                    instrument=instrument, node_id=node_id,
                    payload={"task_id": task_id, "node_id": node_id, "run_id": run.id,
                             "trades": trades_n, "status": entry.get("status") if entry else run.status,
                             "error_code": entry.get("error_code") if entry else None,
                             "stage": entry.get("stage") if entry else None,
                             "error": run.error})
                if cancel_event.is_set():
                    break
            final = task_service.finish_task(
                reg, task_id, "CANCELLED" if cancel_event.is_set() else ("COMPLETED" if failures == 0 else ("BLOCKED" if blocked_count == failures else "FAILED")),
                error=(f"{failures} of {len(node_ids)} node(s) failed or blocked"
                       if failures else None),
                summary=f"{len(node_ids) - failures}/{len(node_ids)} nodes tested")
            if final is not None:
                payload["nodes"] = entries
                task_service.update_task(reg, task_id, payload=payload)
        except Exception as exc:  # noqa: BLE001 - recorded on the task, never hidden
            detail = _execution_log("TASK_WORKER", "FAILED", task_id=task_id,
                                    instrument=instrument, started=worker_started, exc=exc)
            try:
                if reg is not None:
                    task_service.finish_task(reg, task_id, "FAILED",
                                         error=(f"{detail.get('exception_type')} at "
                                                f"{detail.get('exception_file')}:{detail.get('exception_line')} "
                                                f"{detail.get('exception_function')}: {detail.get('exception_message')}\n"
                                                f"{detail.get('traceback')}"))
            except Exception:  # noqa: BLE001
                pass
        finally:
            with _CANCEL_LOCK:
                _CANCEL_EVENTS.pop(task_id, None)
            if sess is not None:
                sess.close()
            if reg is not None:
                reg.close()

    threading.Thread(target=_work, name=f"hero1-deeptest-{task_id[:8]}",
                     daemon=True).start()
