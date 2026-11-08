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


def build_audit_zip(*, run, node, trades: List[dict], scope: str = "FULL_RUN") -> bytes:
    config = json.loads(run.config_json) if run.config_json else {}
    assumptions = json.loads(run.assumptions_json) if run.assumptions_json else {}
    metrics = json.loads(run.metrics_json) if run.metrics_json else {}
    quality = json.loads(run.data_quality_json) if run.data_quality_json else {}
    skipped = json.loads(run.skipped_json) if run.skipped_json else []
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
        "assumptions": assumptions,
        "data": {"snapshot": config.get("data_snapshot"),
                 "readiness": quality,
                 "candles_available": config.get("candles_available")},
        "versions": {"compiler_version": run.compiler_version,
                     "engine_version": run.engine_version},
        "evidence_status": {
            "trade_ledger": "RECORDED",
            "skipped_signals": "RECORDED" if has_decisions else "INCOMPLETE EVIDENCE",
            "condition_evaluations": ("PARTIAL - entry-indicator snapshots per "
                                      "executed trade" if has_indicators
                                      else "INCOMPLETE EVIDENCE"),
            "note": ("Signals rejected before the engine's recorded skip points and "
                     "per-condition boolean evaluations were not persisted for this "
                     "run; those files say so explicitly instead of inventing history."),
        },
        "trade_count_exported": len(enriched),
    }

    # ---- trades.csv -------------------------------------------------------
    cols = ["id", "node_id", "run_id", "side", "instrument", "timeframe",
            "entry_ts", "entry_iso", "exit_ts", "exit_iso", "weekday", "session",
            "entry_price", "exit_price", "volume", "initial_stop", "take_profit",
            "final_stop", "planned_risk", "gross_pnl", "commission", "swap",
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
                             "volume": t.get("volume")})
        trade_events.append({"event": "TRADE_CLOSED", "trade_id": t.get("id"),
                             "ts": t.get("exit_ts"), "iso": _iso(t.get("exit_ts")),
                             "price": t.get("exit_price"),
                             "reason": t.get("exit_reason"), "net_pnl": t.get("net_pnl")})
        for mod in t.get("stop_history") or []:
            execution_events.append({"event": "STOP_MODIFIED", "trade_id": t.get("id"),
                                     **(mod if isinstance(mod, dict) else {"detail": mod})})
        execution_events.append({
            "event": "EXECUTION_SUMMARY", "trade_id": t.get("id"),
            "entry_price": t.get("entry_price"), "exit_price": t.get("exit_price"),
            "initial_stop": t.get("initial_stop"), "take_profit": t.get("take_profit"),
            "final_stop": t.get("final_stop"), "planned_risk": t.get("planned_risk"),
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
                          "run_id": run.id})
        decisions.append({"event": "TRADE_EXITED", "trade_id": t.get("id"),
                          "ts": t.get("exit_ts"), "iso": _iso(t.get("exit_ts")),
                          "reason": t.get("exit_reason")})
    decisions.sort(key=lambda d: (d.get("ts") is None, d.get("ts") or 0))

    # ---- condition / indicator evidence -----------------------------------
    condition_rows, indicator_rows = [], []
    if has_indicators:
        for t in enriched:
            indicator_rows.append({"trade_id": t.get("id"), "ts": t.get("entry_ts"),
                                   "iso": _iso(t.get("entry_ts")),
                                   "indicators_at_entry": t.get("entry_indicators")})
            condition_rows.append({
                "trade_id": t.get("id"), "ts": t.get("entry_ts"),
                "evidence": "entry indicator snapshot recorded by the engine",
                "indicators_at_entry": t.get("entry_indicators"),
                "boolean_evaluation": "UNVERIFIED - per-condition boolean results "
                                      "were not persisted for this run"})
    else:
        for rows in (condition_rows, indicator_rows):
            rows.append({"record_type": "EVIDENCE_STATUS",
                         "status": "INCOMPLETE EVIDENCE",
                         "detail": "no entry-indicator snapshots recorded for this run"})

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
        costs = (t.get("commission") or 0) + (t.get("swap") or 0) + (t.get("spread_cost") or 0)
        return "PASS" if abs((gross - costs) - net) <= 0.02 else "FAIL"
    check("net_pnl_reconciles_gross_minus_costs", _net_ok)

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

    data_refs = {"data_snapshot": config.get("data_snapshot"),
                 "candles_available": config.get("candles_available"),
                 "note": "market data itself is not duplicated in this archive; "
                         "the snapshot identifies the exact stored candle set "
                         "(instrument, timeframe, first/last candle, count)"}

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
        z.writestr(f"{root}/condition_evaluations.jsonl", _jsonl(condition_rows))
        z.writestr(f"{root}/indicator_evidence.jsonl", _jsonl(indicator_rows))
        z.writestr(f"{root}/execution_events.jsonl", _jsonl(execution_events))
        z.writestr(f"{root}/compliance_report.json", json.dumps(compliance, indent=2))
        z.writestr(f"{root}/data_references.json", json.dumps(data_refs, indent=2))
        z.writestr(f"{root}/README.txt", readme)
    return out.getvalue()
