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
import json
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
        sql = ("SELECT ts, open, high, low, close, volume FROM candles "
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
                 "volume": float(r[5]) if r[5] is not None else 0.0} for r in rows]
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
        tick_value_account=cfg.get("tick_value_account") or fields.get("trade_tick_value"),
        tick_value_quote=cfg.get("tick_value_quote") or fields.get("tick_value"),
        fx_to_account=cfg.get("fx_to_account"),
        contract_size=m0.get("contract_size") or fields.get("contract_size"),
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
                config: dict, scenario_id: Optional[int] = None) -> im.DeepTestRun:
    """Execute one node under one config snapshot and persist the result.
    `config` carries: range {start,end}, mode + mode params, RunConfig
    fields and engine assumptions. Readiness BLOCKED => FAILED run row
    with the blocking reasons (never silently tested)."""
    run = im.DeepTestRun(instrument_id=instrument.upper(), node_id=node.id,
                         node_version=node.version, scenario_id=scenario_id,
                         mode=str(config.get("mode") or "initial"),
                         status="RUNNING")
    session.add(run)
    session.commit()
    session.refresh(run)
    try:
        spec = json.loads(node.spec_json) if node.spec_json else None
        broker_meta_pre = spec_service._resolve_broker_meta(instrument, config.get("broker_meta"))
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
        bounds = available_bounds(instrument, tf)
        if bounds is None:
            raise DeepTestError("DATA_BLOCKED",
                                f"no eligible candles stored for {instrument} {tf}; nothing to test")
        # PARTIAL TESTING: clamp the requested window to actually available
        # data. Partial == the requested start precedes the first stored
        # candle (requested duration exceeds history). The end anchors to
        # the latest available candle and is never 'missing'.
        start_ts = max(int(req_start), bounds["first_ts"]) if req_start else bounds["first_ts"]
        end_ts = min(int(req_end), bounds["last_ts"]) if req_end else bounds["last_ts"]
        partial = bool(req_start) and int(req_start) < bounds["first_ts"]
        config = {**config, "requested_range": {"start": req_start, "end": req_end},
                  "actual_range": {"start": start_ts, "end": end_ts},
                  "coverage_status": "PARTIAL_TESTED" if partial else "TESTED",
                  "candles_available": bounds["candles"]}
        dq = readiness(instrument, tf, start_ts, end_ts)
        run.data_quality_json = json.dumps(dq)
        if dq.get("readiness") == "BLOCKED":
            raise DeepTestError("DATA_BLOCKED",
                                "historical data is BLOCKED for this range: "
                                + "; ".join(dq.get("blocked_reasons") or dq.get("reasons") or []))
        broker_meta = spec_service._resolve_broker_meta(instrument, config.get("broker_meta"))
        _report, compiled = spec_service.compile_or_raise(spec, instrument, broker_meta)
        run.fingerprint = compiled.fingerprint
        run.compiler_version = compiled.compiler_version
        run.engine_version = ENGINE_VERSION

        rcfg = RunConfig(
            initial_capital=float(config.get("initial_capital") or 10000.0),
            account_currency=str(config.get("account_currency") or "USD"),
            risk_mode=str(config.get("risk_mode") or "percent"),
            risk_value=float(config.get("risk_value") if config.get("risk_value") is not None else 1.0),
            risk_basis=str(config.get("risk_basis") or "current_equity"),
            max_active_trades=int(config.get("max_active_trades") or 1),
            spread_price=float(config.get("spread_price") or 0.0),
            slippage_price=float(config.get("slippage_price") or 0.0),
            commission_per_lot_round_turn=float(config.get("commission_per_lot_round_turn") or 0.0),
            swap_long_per_lot_night=float(config.get("swap_long_per_lot_night") or 0.0),
            swap_short_per_lot_night=float(config.get("swap_short_per_lot_night") or 0.0),
            ambiguity_policy=str(config.get("ambiguity_policy") or "sl_first"),
            sessions=config.get("sessions"), weekdays=config.get("weekdays"),
            months=config.get("months"),
            tp_offset_price=float(config.get("tp_offset_price") or 0.0),
            sl_offset_price=float(config.get("sl_offset_price") or 0.0),
        )
        spec_meta = _instrument_spec(instrument, config)
        m0 = ((broker_meta or {}).get(instrument.upper())) or {}
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
            "spread_source": "configured assumption (historical bid/ask not stored)"
            if rcfg.spread_price else "zero - candles are bid; no spread assumption configured",
            "slippage_price": rcfg.slippage_price,
            "commission_per_lot_round_turn": rcfg.commission_per_lot_round_turn,
            "swap_long_per_lot_night": rcfg.swap_long_per_lot_night,
            "swap_short_per_lot_night": rcfg.swap_short_per_lot_night,
            "ambiguity_policy": rcfg.ambiguity_policy,
            "tick_value_basis": ("account-currency tick value from broker snapshot"
                                 if spec_meta.tick_value_account else
                                 "quote tick value x configured fx_to_account"),
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
        for window in windows:
            candles = load_candles(instrument, tf, window["start"], window["end"])
            if len(candles) > MAX_CANDLES_PER_RUN:
                raise DeepTestError("RANGE_TOO_LARGE",
                                    f"{len(candles)} candles exceed the {MAX_CANDLES_PER_RUN} per-run cap")
            mgmt = None
            if spec.get("management"):
                mgmt = TradeManagement.model_validate(spec["management"])
            result = run_backtest(ir=compiled.ir, candles=candles, cfg=rcfg,
                                  instrument=spec_meta, price_ctx=price_ctx, management=mgmt)
            metrics = compute_metrics(result["trades"], result["equity_curve"], rcfg.initial_capital)
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
            target.status = "DONE"
            target.finished_at = time.time()
            for t in result["trades"]:
                session.add(im.DeepTestRunTrade(
                    instrument_id=instrument.upper(), run_id=target.id, node_id=node.id,
                    side=t["side"], volume=t["volume"], entry_ts=t["entry_ts"],
                    entry_price=t["entry_price"], exit_ts=t["exit_ts"],
                    exit_price=t["exit_price"], exit_reason=t["exit_reason"],
                    net_pnl=t["net_pnl"], detail_json=json.dumps(t)))
            session.commit()
            first = False
        return run
    except (DeepTestError, EngineError) as exc:
        run.status = "FAILED"
        run.error = f"{getattr(exc, 'code', 'ERROR')}: {exc}"
        run.finished_at = time.time()
        session.commit()
        return run
    except Exception as exc:  # noqa: BLE001 - recorded, never hidden
        run.status = "FAILED"
        run.error = f"UNEXPECTED: {type(exc).__name__}: {exc}"
        run.finished_at = time.time()
        session.commit()
        return run


def run_summary(run: im.DeepTestRun) -> dict:
    metrics = json.loads(run.metrics_json) if run.metrics_json else None
    return {
        "id": run.id, "node_id": run.node_id, "node_version": run.node_version,
        "scenario_id": run.scenario_id, "mode": run.mode, "window_label": run.window_label,
        "status": run.status, "instrument": run.instrument_id, "timeframe": run.timeframe,
        "fingerprint": run.fingerprint, "compiler_version": run.compiler_version,
        "engine_version": run.engine_version, "error": run.error,
        "started_at": run.started_at, "finished_at": run.finished_at,
        "config": json.loads(run.config_json) if run.config_json else None,
        "metrics": metrics,
        "requested_range": (json.loads(run.config_json).get("requested_range")
                            if run.config_json else None),
        "coverage_status": metrics.get("coverage_status") if metrics else None,
    }


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
