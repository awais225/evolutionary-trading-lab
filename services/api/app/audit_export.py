"""EXPORT TRADING DATA — independent execution audit package (Section 7).

Builds a ZIP of structured evidence from the ORIGINAL DeepTesting run
records. Nothing is reconstructed: decision/condition evidence that the
engine never recorded is labelled INCOMPLETE EVIDENCE / UNVERIFIED in the
export itself, never fabricated. Compliance checks that cannot be decided
from the stored ledger are UNVERIFIED — a missing log is not a PASS.
"""
from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import datetime, timezone
from typing import List, Optional

from . import deeptesting_service as dts


def _iso(ts) -> Optional[str]:
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()


def _jsonl(rows) -> str:
    return "".join(json.dumps(r, default=str) + "\n" for r in rows)


def build_audit_zip(*, run, node, trades: List[dict], scope: str = "FULL_RUN",
                    decision_events: Optional[List[dict]] = None) -> bytes:
    config = json.loads(run.config_json) if run.config_json else {}
    assumptions = json.loads(run.assumptions_json) if run.assumptions_json else {}
    metrics = json.loads(run.metrics_json) if run.metrics_json else {}
    quality = json.loads(run.data_quality_json) if run.data_quality_json else {}
    skipped = json.loads(run.skipped_json) if run.skipped_json else []
    # Only contemporaneously persisted ledger rows are evidence. Never derive
    # a historical ledger from current candles or trades during export.
    decision_ledger = (decision_events if decision_events is not None else
                       json.loads(run.decision_ledger_json) if getattr(run, "decision_ledger_json", None) else [])
    spec = None
    if node is not None and node.spec_json:
        try:
            spec = json.loads(node.spec_json)
        except Exception:  # noqa: BLE001
            spec = None

    enriched = []
    for t in trades:
        wd, sess = dts.inspect_cell(float(t.get("entry_ts") or 0))
        enriched.append({**t, "weekday": wd, "session": sess})

    has_indicators = any(t.get("entry_indicators") for t in enriched)
    has_decisions = bool(skipped)

    manifest = {
        "export_format": "HERO1_TRADING_AUDIT/1",
        "scope": scope,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "node": {"id": run.node_id, "name": node.name if node else None,
                 "version": run.node_version,
                 "fingerprint": run.fingerprint,
                 "strategy_snapshot": spec,
                 "strategy_snapshot_note": ("exact stored strategy JSON of the node's "
                                            "current record; the run's fingerprint pins "
                                            "the semantics it executed")},
        "run": {"id": run.id, "instrument": run.instrument_id,
                "timeframe": run.timeframe, "mode": run.mode,
                "window_label": run.window_label, "status": run.status,
                "scenario_id": run.scenario_id},
        "ranges": {"requested": config.get("requested_range"),
                   "actual": config.get("actual_range"),
                   "coverage_status": config.get("coverage_status")},
        "capital": {"initial_capital": config.get("initial_capital"),
                    "currency": config.get("account_currency")},
        "risk": {"risk_mode": config.get("risk_mode"),
                 "risk_value": config.get("risk_value"),
                 "risk_basis": config.get("risk_basis"),
                 "max_active_trades": config.get("max_active_trades"),
                 "position_sizing": "risk-based position sizing from stop distance "
                                    "(see assumptions + per-trade planned_risk)"},
        "offsets": config.get("offset_resolution"),
        "specification_snapshot": config.get("specification_snapshot"),
        "specification_assumption": config.get("specification_assumption"),
        "execution_specification_inputs": config.get("execution_specification_inputs"),
        "assumptions": assumptions,
        "data": {"snapshot": config.get("data_snapshot"),
                 "source_provenance": (config.get("data_snapshot") or {}).get("source_provenance"),
                 "source_provenance_status": ((config.get("data_snapshot") or {}).get("source_provenance") or {}).get("status", "UNVERIFIED_NOT_CAPTURED"),
                 "readiness": quality,
                 "candles_available": config.get("candles_available")},
        "instrument_specification_status": ((config.get("specification_snapshot") or {}).get("provenance_class")
            if (config.get("specification_snapshot") or {}).get("provenance_class") in ("MT5_LIVE_CAPTURE", "MT5_SAVED_SNAPSHOT", "CURRENT_SPECIFICATION_USED_FOR_HISTORICAL_TEST")
            else "RUN_CONFIG_ONLY" if config.get("execution_specification_inputs") else "UNAVAILABLE"),
        "versions": {"compiler_version": run.compiler_version,
                     "engine_version": run.engine_version},
        "evidence_status": {
            "decision_ledger": "RECORDED" if decision_ledger else "INCOMPLETE HISTORICAL EVIDENCE",
            "decision_ledger_event_count": len(decision_ledger),
            "trade_ledger": "RECORDED",
            "skipped_signals": "RECORDED" if has_decisions else "INCOMPLETE EVIDENCE",
            "condition_evaluations": ("RECORDED FOR EXECUTED SIGNALS; REJECTIONS/OTHER BARS UNVERIFIED"
                                      if any(t.get("condition_evaluations") for t in enriched)
                                      else "INCOMPLETE EVIDENCE"),
            "note": ("Condition leaves are captured at executed signal bars. Historical "
                     "condition decisions for non-signals and rejections before recorded "
                     "skip points are not reconstructed."),
        },
        "trade_count_exported": len(enriched),
    }

    # ---- trades.csv -------------------------------------------------------
    cols = ["id", "node_id", "run_id", "side", "instrument", "timeframe",
            "entry_ts", "entry_iso", "exit_ts", "exit_iso", "weekday", "session",
            "entry_price", "exit_price", "entry_bid_quote", "entry_ask_quote",
            "exit_bid_quote", "exit_ask_quote", "spread_entry_price", "spread_exit_price",
            "volume", "initial_stop", "take_profit", "final_stop", "planned_risk",
            "risk_amount", "realized_risk", "stop_distance", "tick_value_used",
            "equity_before_entry", "gross_pnl", "commission", "swap", "slippage",
            "spread_cost", "net_pnl", "result", "exit_reason", "holding_seconds",
            "mfe_price", "mae_price"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for t in enriched:
        w.writerow([run.node_id if c == "node_id" else run.id if c == "run_id"
                    else run.instrument_id if c == "instrument"
                    else run.timeframe if c == "timeframe"
                    else _iso(t.get("entry_ts")) if c == "entry_iso"
                    else _iso(t.get("exit_ts")) if c == "exit_iso"
                    else t.get(c) for c in cols])
    trades_csv = buf.getvalue()

    # ---- trade / execution events ----------------------------------------
    trade_events, execution_events = [], []
    for t in enriched:
        trade_events.append({"event": "TRADE_OPENED", "trade_id": t.get("id"),
                             "ts": t.get("entry_ts"), "iso": _iso(t.get("entry_ts")),
                             "side": t.get("side"), "price": t.get("entry_price"),
                             "bid": t.get("entry_bid_quote"), "ask": t.get("entry_ask_quote"),
                             "historical_spread_price": t.get("spread_entry_price"),
                             "volume": t.get("volume")})
        trade_events.append({"event": "TRADE_CLOSED", "trade_id": t.get("id"),
                             "ts": t.get("exit_ts"), "iso": _iso(t.get("exit_ts")),
                             "price": t.get("exit_price"),
                             "bid": t.get("exit_bid_quote"), "ask": t.get("exit_ask_quote"),
                             "historical_spread_price": t.get("spread_exit_price"),
                             "reason": t.get("exit_reason"), "net_pnl": t.get("net_pnl")})
        for mod in t.get("stop_history") or []:
            execution_events.append({"event": "STOP_MODIFIED", "trade_id": t.get("id"),
                                     **(mod if isinstance(mod, dict) else {"detail": mod})})
        execution_events.append({
            "event": "EXECUTION_SUMMARY", "trade_id": t.get("id"),
            "entry_price": t.get("entry_price"), "exit_price": t.get("exit_price"),
            "entry_bid_quote": t.get("entry_bid_quote"), "entry_ask_quote": t.get("entry_ask_quote"),
            "exit_bid_quote": t.get("exit_bid_quote"), "exit_ask_quote": t.get("exit_ask_quote"),
            "spread_entry_price": t.get("spread_entry_price"),
            "spread_exit_price": t.get("spread_exit_price"),
            "commission": t.get("commission"), "swap": t.get("swap"),
            "slippage": t.get("slippage"),
            "equity_before_entry": t.get("equity_before_entry"),
            "condition_evaluations": t.get("condition_evaluations"),
            "signal_ts": t.get("signal_ts"),
            "initial_stop": t.get("initial_stop"), "take_profit": t.get("take_profit"),
            "final_stop": t.get("final_stop"), "planned_risk": t.get("planned_risk"),
            "risk_amount": t.get("risk_amount"), "realized_risk": t.get("realized_risk"),
            "stop_distance": t.get("stop_distance"), "tick_value_used": t.get("tick_value_used"),
            "sizing_calculation": t.get("sizing_calculation"),
            "spread_cost": t.get("spread_cost"), "commission": t.get("commission"),
            "swap": t.get("swap"),
            "slippage_note": "fills at next bar open (OHLC semantics); no tick-level "
                             "slippage record exists for this run"})

    # ---- strategy decisions ----------------------------------------------
    decisions = []
    if has_decisions:
        for sk in skipped:
            reason = str(sk.get("reason") or "")
            etype = ("SIGNAL_REJECTED_ACTIVE_TRADE_LIMIT"
                     if "MAX_ACTIVE" in reason else "SIGNAL_REJECTED_INVALID_DATA"
                     if "INVALID" in reason or "OFFSET" in reason
                     else "SIGNAL_REJECTED_CONDITION")
            decisions.append({"event": etype, "ts": sk.get("ts"),
                              "iso": _iso(sk.get("ts")), "side": sk.get("side"),
                              "reason": reason, "node_id": run.node_id,
                              "run_id": run.id, "instrument": run.instrument_id,
                              "timeframe": run.timeframe})
    else:
        decisions.append({"record_type": "EVIDENCE_STATUS",
                          "status": "INCOMPLETE EVIDENCE",
                          "detail": "this run recorded no skipped/rejected signals"})
    for t in enriched:
        decisions.append({"event": "TRADE_EXECUTED", "trade_id": t.get("id"),
                          "ts": t.get("entry_ts"), "iso": _iso(t.get("entry_ts")),
                          "side": t.get("side"), "node_id": run.node_id,
                          "run_id": run.id, "signal_ts": t.get("signal_ts"),
                          "condition_evaluations": t.get("condition_evaluations")})
        decisions.append({"event": "TRADE_EXITED", "trade_id": t.get("id"),
                          "ts": t.get("exit_ts"), "iso": _iso(t.get("exit_ts")),
                          "reason": t.get("exit_reason")})
    decisions.sort(key=lambda d: (d.get("ts") is None, d.get("ts") or 0))

    # ---- condition / indicator evidence -----------------------------------
    condition_rows, indicator_rows = [], []
    for t in enriched:
        if t.get("entry_indicators"):
            indicator_rows.append({"trade_id": t.get("id"), "ts": t.get("entry_ts"),
                                   "iso": _iso(t.get("entry_ts")),
                                   "indicators_at_entry": t.get("entry_indicators")})
        evaluations = t.get("condition_evaluations") or []
        condition_rows.append({
            "trade_id": t.get("id"), "ts": t.get("signal_ts"),
            "iso": _iso(t.get("signal_ts")),
            "candle_index": t.get("signal_candle_index"),
            "condition_evaluations": evaluations,
            "evidence": ("recorded at executed signal candle" if evaluations
                         else "INCOMPLETE EVIDENCE - no condition results persisted"),
            "non_signal_and_rejection_bars": "UNVERIFIED - not reconstructed"})
    if not condition_rows:
        condition_rows.append({"record_type": "EVIDENCE_STATUS", "status": "INCOMPLETE EVIDENCE",
                               "detail": "no executed signal condition evaluations recorded"})
    if not indicator_rows:
        indicator_rows.append({"record_type": "EVIDENCE_STATUS", "status": "INCOMPLETE EVIDENCE",
                              "detail": "no entry-indicator snapshots recorded for this run"})

    # ---- independent evidence verifiers -----------------------------------
    from .execution_evidence_verifiers import (
        verify_decision_outcomes, verify_entry_conditions, verify_no_lookahead,
        verify_position_sizing, verify_reference_levels, verify_snapshot_provenance,
    )
    decision_verification = verify_decision_outcomes(decision_ledger, enriched)
    no_lookahead_verification = verify_no_lookahead(decision_ledger)
    sizing_verification = verify_position_sizing(enriched)
    source_candles = []
    try:
        actual = config.get("actual_range") or {}
        if actual.get("start") is not None and actual.get("end") is not None:
            source_candles = dts.load_candles(run.instrument_id, run.timeframe,
                                               int(actual["start"]), int(actual["end"]))
    except Exception:  # keep evidence absence explicit in the verification report
        source_candles = []
    entry_verification = verify_entry_conditions(spec, decision_ledger, source_candles)
    def _contains_reference(value):
        if isinstance(value, dict):
            return value.get("kind") == "reference" or any(_contains_reference(v) for v in value.values())
        if isinstance(value, list):
            return any(_contains_reference(v) for v in value)
        return False
    reference_verification = verify_reference_levels([], [], _contains_reference(spec))
    snapshot_verification = verify_snapshot_provenance(config.get("specification_snapshot"))
    verification_results = {
        "verifier_version": "hero1-evidence-verifier/1",
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "entry_conditions_followed": entry_verification,
        "signal_outcome_reconciliation": decision_verification,
        "no_look_ahead": no_lookahead_verification,
        "reference_level_calculations": reference_verification,
        "position_sizing_and_risk_limit": sizing_verification,
        "instrument_specification_provenance": snapshot_verification,
    }

    # ---- compliance --------------------------------------------------------
    checks = []

    def check(name, decided):
        counts = {"PASS": 0, "WARNING": 0, "FAIL": 0, "UNVERIFIED": 0}
        fails = []
        for t in enriched:
            verdict = decided(t)
            counts[verdict] += 1
            if verdict == "FAIL" and len(fails) < 10:
                fails.append(t.get("id"))
        verdict = ("FAIL" if counts["FAIL"] else "WARNING" if counts["WARNING"]
                   else "UNVERIFIED" if counts["UNVERIFIED"] and not counts["PASS"]
                   else "PASS")
        checks.append({"check": name, "verdict": verdict, "counts": counts,
                       "example_failing_trade_ids": fails})

    check("entry_before_exit_chronology",
          lambda t: "PASS" if (t.get("entry_ts") is not None and t.get("exit_ts") is not None
                               and t["exit_ts"] >= t["entry_ts"]) else "FAIL")
    check("direction_valid",
          lambda t: "PASS" if t.get("side") in ("LONG", "SHORT") else "FAIL")
    check("exit_reason_recorded",
          lambda t: "PASS" if t.get("exit_reason") else "FAIL")
    check("stop_and_target_recorded",
          lambda t: "PASS" if t.get("initial_stop") is not None else "WARNING")
    check("holding_duration_non_negative",
          lambda t: "PASS" if (t.get("holding_seconds") or 0) >= 0 else "FAIL")

    def _net_ok(t):
        gross, net = t.get("gross_pnl"), t.get("net_pnl")
        if gross is None or net is None:
            return "UNVERIFIED"
        commission = float(t.get("commission") or 0)
        swap = float(t.get("swap") or 0)  # signed amount: negative reduces net
        # gross_pnl is calculated from the actual execution fills (Ask entry /
        # Bid exit for longs), so spread and configured slippage are already
        # reflected. spread_cost is a diagnostic estimate, not a second debit.
        expected_net = gross - commission + swap
        return "PASS" if abs(expected_net - net) <= 0.02 else "FAIL"
    check("net_pnl_reconciles_gross_commission_signed_swap", _net_ok)
    checks[-1]["formula"] = ("gross_pnl - commission + signed swap; spread/slippage are reflected in fills; "
                              "spread_cost is informational and is not deducted again")

    max_active = int(config.get("max_active_trades") or 0)
    if max_active > 0 and enriched:
        events = []
        for t in enriched:
            events.append((float(t.get("entry_ts") or 0), 1))
            events.append((float(t.get("exit_ts") or 0), -1))
        events.sort(key=lambda e: (e[0], e[1]))
        cur = peak = 0
        for _, d in events:
            cur += d
            peak = max(peak, cur)
        checks.append({"check": "max_active_trades_respected",
                       "verdict": "PASS" if peak <= max_active else "FAIL",
                       "counts": {"PASS": len(enriched) if peak <= max_active else 0,
                                  "WARNING": 0,
                                  "FAIL": 0 if peak <= max_active else len(enriched),
                                  "UNVERIFIED": 0},
                       "observed_peak_concurrent": peak, "configured_limit": max_active})
    else:
        checks.append({"check": "max_active_trades_respected", "verdict": "UNVERIFIED",
                       "counts": {"PASS": 0, "WARNING": 0, "FAIL": 0,
                                  "UNVERIFIED": len(enriched)},
                       "detail": "no configured limit recorded"})

    for name, why in (
            ("entry_conditions_followed",
             "per-condition boolean evaluations were not persisted for this run"),
            ("no_look_ahead",
             "cannot be decided from the ledger alone; the engine is causal by "
             "construction but this export does not fabricate a proof"),
            ("reference_level_calculations",
             "reference levels are recomputed by the engine from stored candles; "
             "per-signal level values were not persisted for this run"),
            ("position_sizing_and_risk_limit",
             "tick-value inputs at each historical entry were not persisted; "
             "planned_risk per trade is exported for manual review")):
        checks.append({"check": name, "verdict": "UNVERIFIED",
                       "counts": {"PASS": 0, "WARNING": 0, "FAIL": 0,
                                  "UNVERIFIED": len(enriched)}, "detail": why})

    summary = {"PASS": 0, "WARNING": 0, "FAIL": 0, "UNVERIFIED": 0}
    for c in checks:
        summary[c["verdict"]] += 1
    compliance = {"run_id": run.id, "scope": scope, "summary_by_verdict": summary,
                  "checks": checks,
                  "rule": "a missing log is UNVERIFIED, never a PASS; warnings never "
                          "hide a confirmed FAIL"}

    data_snapshot = config.get("data_snapshot") or {}
    data_refs = {"data_snapshot": data_snapshot,
                 "source_provenance": data_snapshot.get("source_provenance"),
                 "candles_available": config.get("candles_available"),
                 "note": "market data itself is not duplicated in this archive; the snapshot "
                         "records the candle range/count and, when available, the matching "
                         "import-manifest source file SHA256 and dataset fingerprint. "
                         "Missing or ambiguous provenance remains UNVERIFIED."}

    readme = (
        "HERO1 TRADING AUDIT EXPORT\n"
        f"Run {run.id} · node {run.node_id} · {run.instrument_id} {run.timeframe} · scope {scope}\n\n"
        "Files:\n"
        "  manifest.json               run configuration, strategy snapshot, versions, evidence status\n"
        "  trades.csv                  complete exported trade ledger\n"
        "  trade_events.jsonl          open/close events per trade\n"
        "  strategy_decisions.jsonl    executed trades + recorded rejected signals\n"
        "  condition_evaluations.jsonl per-trade entry evidence (or INCOMPLETE EVIDENCE)\n"
        "  indicator_evidence.jsonl    indicator values recorded at entry\n"
        "  execution_events.jsonl      stop modifications + execution summaries\n"
        "  compliance_report.json      PASS/WARNING/FAIL/UNVERIFIED checks (missing log != PASS)\n"
        "  data_references.json        market-data snapshot reference (data not duplicated)\n\n"
        "Evidence honesty: sections the original run did not record are labelled\n"
        "INCOMPLETE EVIDENCE / UNVERIFIED. Nothing in this package is reconstructed\n"
        "or fabricated.\n")

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        root = "HERO1_TRADING_AUDIT"
        z.writestr(f"{root}/manifest.json", json.dumps(manifest, indent=2, default=str))
        z.writestr(f"{root}/trades.csv", trades_csv)
        z.writestr(f"{root}/trade_events.jsonl", _jsonl(trade_events))
        z.writestr(f"{root}/strategy_decisions.jsonl", _jsonl(decisions))
        z.writestr(f"{root}/decision_ledger.jsonl", _jsonl(decision_ledger))
        z.writestr(f"{root}/independent_verification.json", json.dumps(verification_results, indent=2, default=str))
        z.writestr(f"{root}/condition_evaluations.jsonl", _jsonl(condition_rows))
        z.writestr(f"{root}/indicator_evidence.jsonl", _jsonl(indicator_rows))
        z.writestr(f"{root}/execution_events.jsonl", _jsonl(execution_events))
        z.writestr(f"{root}/compliance_report.json", json.dumps(compliance, indent=2))
        z.writestr(f"{root}/data_references.json", json.dumps(data_refs, indent=2))
        z.writestr(f"{root}/README.txt", readme)
    return out.getvalue()
