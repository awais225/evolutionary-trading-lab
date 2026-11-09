"""Independent checks for persisted DeepTesting evidence.

These routines deliberately report UNVERIFIED when the underlying source
inputs are absent; exported engine assertions are not themselves proof.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

VERIFIER_VERSION = "hero1-evidence-verifier/1"


def verify_entry_conditions(strategy: dict | None, events: list[dict], source_candles: list[dict],
                            expected_fingerprint: str | None = None) -> dict:
    if not strategy or not events or not source_candles:
        return {"verdict": "UNVERIFIED", "detail": "strategy, decision ledger, or exact source candles missing"}
    entry = strategy.get("entry") or {}
    fingerprint_mismatches = [e.get("event_id") for e in events
        if e.get("strategy_fingerprint") is not None and expected_fingerprint is not None
        and e.get("strategy_fingerprint") != expected_fingerprint]
    strategy_fingerprint = None
    if expected_fingerprint is not None:
        try:
            from hero1_strategy_spec import StrategySpec, semantic_fingerprint
            strategy_fingerprint, _ = semantic_fingerprint(StrategySpec.model_validate(strategy))
        except Exception as exc:  # unsupported historical schema remains incomplete
            return {"verdict": "UNVERIFIED", "detail": f"strategy fingerprint could not be recomputed: {exc}"}
        if strategy_fingerprint != expected_fingerprint:
            fingerprint_mismatches.append("strategy_definition")
    if fingerprint_mismatches:
        return {"verdict": "FAIL", "fingerprint_mismatches": fingerprint_mismatches,
                "recomputed_strategy_fingerprint": strategy_fingerprint,
                "detail": "ledger or strategy fingerprint differs from run fingerprint"}
    by_ts = {int(c["ts"]): c for c in source_candles}
    evaluations = [e for e in events if e.get("event") == "CONDITION_EVALUATED"]
    mismatches, unresolved = [], []

    ordered = sorted(source_candles, key=lambda c: int(c["ts"]))
    index_by_ts = {int(c["ts"]): i for i, c in enumerate(ordered)}

    def operand(op, candle_i):
        kind = op.get("kind")
        if kind == "constant":
            return op.get("value")
        if kind in ("candle", "forming_candle"):
            if kind == "forming_candle":
                raise ValueError("forming-candle operand requires explicit evidence policy")
            source_i = candle_i - int(op.get("offset", 0))
            if source_i < 0:
                return None
            return ordered[source_i].get(op.get("field"))
        raise ValueError(f"unsupported independent operand kind {kind!r}")

    def evaluate(node, candle_i, path="entry", leaves=None):
        typ = node.get("type")
        if typ in ("and", "or"):
            vals = [evaluate(c, candle_i, f"{path}.{typ}[{j}]", leaves)
                    for j, c in enumerate(node.get("children", []))]
            if typ == "and":
                result = False if any(v is False for v in vals) else True if all(v is True for v in vals) else None
            else:
                result = True if any(v is True for v in vals) else False if all(v is False for v in vals) else None
            return result
        if typ == "not":
            value = evaluate(node.get("child") or {}, candle_i, f"{path}.not", leaves)
            return None if value is None else not value
        if typ == "compare":
            op = node.get("op")
            left, right = node.get("left", {}), node.get("right", {})
            a, b = operand(left, candle_i), operand(right, candle_i)
            if op in ("cross_above", "cross_below"):
                if candle_i <= 0:
                    result = None
                else:
                    pa, pb = operand(left, candle_i - 1), operand(right, candle_i - 1)
                    if None in (a, b, pa, pb):
                        result = None
                    elif op == "cross_above":
                        result = pa <= pb and a > b
                    else:
                        result = pa >= pb and a < b
            elif a is None or b is None:
                result = None
            else:
                result = {"gt": lambda: a > b, "gte": lambda: a >= b,
                          "lt": lambda: a < b, "lte": lambda: a <= b,
                          "eq": lambda: a == b, "neq": lambda: a != b}.get(op, lambda: None)()
            if leaves is not None:
                leaves[path] = result
            return result
        raise ValueError(f"unsupported independent condition type {typ!r}")

    for event in evaluations:
        try:
            candle_i = index_by_ts.get(int(event.get("candle_ts", -1)))
            if candle_i is None:
                raise ValueError("evaluation candle timestamp is absent from the source dataset")
            leaves = {}
            result = evaluate(entry, candle_i, leaves=leaves)
        except (ValueError, TypeError, KeyError) as exc:
            unresolved.append({"event_id": event.get("event_id"), "reason": str(exc)})
            continue
        if result is None:
            unresolved.append({"event_id": event.get("event_id"), "reason": "warmup or input unavailable"})
        elif bool(result) != bool(event.get("result")):
            mismatches.append({"event_id": event.get("event_id"), "expected": result,
                               "recorded": event.get("result")})
        recorded_leaves = {str(v.get("path")): v.get("result")
                           for v in (event.get("conditions") or []) if isinstance(v, dict)}
        for path, expected in leaves.items():
            if path not in recorded_leaves:
                mismatches.append({"event_id": event.get("event_id"), "condition_path": path,
                                   "reason": "missing recorded leaf"})
            elif recorded_leaves[path] != expected:
                mismatches.append({"event_id": event.get("event_id"), "condition_path": path,
                                   "expected": expected, "recorded": recorded_leaves[path]})
    verdict = "FAIL" if mismatches else "UNVERIFIED" if unresolved else "PASS"
    return {"verdict": verdict, "evaluations_checked": len(evaluations),
            "mismatches": mismatches, "unresolved": unresolved,
            "verifier_version": VERIFIER_VERSION}


def verify_decision_outcomes(events: list[dict], trades: list[dict]) -> dict:
    signals = [e for e in events if e.get("event") == "SIGNAL_DETECTED"]
    outcomes = [e for e in events if e.get("event") in {
        "TRADE_EXECUTED", "SIGNAL_REJECTED_CONDITION", "SIGNAL_REJECTED_DIRECTION",
        "SIGNAL_REJECTED_ACTIVE_TRADE_LIMIT", "SIGNAL_REJECTED_RISK_LIMIT",
        "SIGNAL_REJECTED_INVALID_DATA", "SIGNAL_REJECTED_SESSION_FILTER",
        "SIGNAL_REJECTED_INVALID_METADATA", "EXECUTION_FAILED"}]
    grouped = defaultdict(list)
    for e in outcomes:
        if e.get("signal_id"):
            grouped[e["signal_id"]].append(e)
    duplicate = [k for k, v in grouped.items() if len(v) > 1]
    missing = [s.get("signal_id") for s in signals if len(grouped.get(s.get("signal_id"), [])) != 1]
    trade_ids = {str(t.get("id")) for t in trades}
    bad_links = []
    for e in outcomes:
        if e.get("event") != "TRADE_EXECUTED":
            continue
        linked = (str(e.get("trade_id")) in trade_ids if e.get("trade_id") is not None else
                  any((t.get("trade_ref") == e.get("trade_ref") or
                       (int(t.get("signal_ts") or -1) == int(e.get("signal_ts") or e.get("candle_ts") or -2)
                        and t.get("side") == e.get("side"))) for t in trades))
        if not linked:
            bad_links.append(e.get("trade_id") or e.get("signal_id"))
    verdict = "PASS" if events and not missing and not duplicate and not bad_links else "FAIL" if events else "UNVERIFIED"
    return {"verdict": verdict, "evaluations_checked": sum(e.get("event") == "CONDITION_EVALUATED" for e in events),
            "valid_signals_detected": len(signals),
            "executed_signals": sum(e.get("event") == "TRADE_EXECUTED" for e in outcomes),
            "rejected_signals_by_reason": dict(Counter(str(e.get("reason") or e.get("event")) for e in outcomes if e.get("event") != "TRADE_EXECUTED")),
            "missing_outcomes": missing, "duplicate_outcomes": duplicate,
            "bad_trade_links": bad_links, "detail": "requires persisted signal/outcome relationships"}


def verify_no_lookahead(events: list[dict]) -> dict:
    missing = []
    future = []
    for e in events:
        if e.get("event") != "CONDITION_EVALUATED":
            continue
        if e.get("evaluation_ts") is None or e.get("max_permitted_input_ts") is None or e.get("actual_max_input_ts") is None:
            missing.append(e.get("event_id"))
        elif int(e["actual_max_input_ts"]) > int(e["max_permitted_input_ts"]):
            future.append(e.get("event_id"))
    if future:
        verdict = "FAIL"
    elif not events or missing:
        verdict = "UNVERIFIED"
    else:
        # Metadata alone cannot prove causality; dynamic prefix and mutation
        # tests are recorded separately and required for a PASS.
        verdict = "UNVERIFIED"
    return {"verdict": verdict, "future_input_events": future,
            "missing_input_references": missing,
            "causal_test_status": "NOT_RUN_FROM_AUDIT_PACKAGE",
            "detail": "timestamps alone are insufficient; verify referenced inputs and causal replay"}


def verify_position_sizing(trades: list[dict]) -> dict:
    """Recalculate sizing without calling hero1_exec_semantics.size_position."""
    failures, missing = [], []
    checked = 0
    for trade in trades:
        calc = trade.get("sizing_calculation") or {}
        required = ("equity_before_entry", "initial_capital", "risk_mode", "risk_value", "risk_basis", "tick_size",
                    "tick_value_used", "volume_step", "volume_min", "volume_max", "stop_distance",
                    "volume", "planned_risk", "budget", "loss_per_lot")
        absent = [k for k in required if calc.get(k) is None]
        if absent:
            missing.append({"trade_id": trade.get("id"), "fields": absent})
            continue
        checked += 1
        equity = float(calc["equity_before_entry"])
        risk_base = equity if calc["risk_basis"] == "current_equity" else float(calc["initial_capital"])
        budget = (risk_base * float(calc["risk_value"]) / 100.0
                  if calc["risk_mode"] == "percent" else float(calc["risk_value"]))
        per_lot = float(calc["stop_distance"]) / float(calc["tick_size"]) * float(calc["tick_value_used"])
        raw = budget / per_lot if per_lot > 0 else -1
        vol_step = float(calc["volume_step"])
        rounded = int(raw / vol_step + 1e-12) * vol_step if raw > 0 else -1
        if rounded > float(calc["volume_max"]):
            rounded = float(calc["volume_max"])
        risk = rounded * per_lot
        observed = float(trade.get("volume") or 0)
        if abs(rounded - observed) > max(1e-8, vol_step * 1e-7) or abs(risk - float(calc["planned_risk"])) > 0.02 or risk > budget + 0.02:
            failures.append({"trade_id": trade.get("id"), "expected_volume": rounded,
                             "actual_volume": observed, "expected_risk": risk,
                             "recorded_risk": calc.get("planned_risk"), "budget": budget})
    verdict = "FAIL" if failures else "PASS" if trades and checked == len(trades) else "UNVERIFIED"
    return {"verdict": verdict, "trades_checked": checked, "missing_inputs": missing,
            "mismatches": failures, "verifier_version": VERIFIER_VERSION}


def verify_snapshot_provenance(snapshot: dict | None) -> dict:
    import hashlib
    import json
    if not snapshot:
        return {"verdict": "UNVERIFIED", "detail": "run has no linked specification snapshot"}
    payload = dict(snapshot.get("snapshot_payload") or {})
    claimed = payload.pop("integrity_sha256", None)
    if not claimed:
        return {"verdict": "UNVERIFIED", "detail": "snapshot integrity hash absent"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=True, allow_nan=False, default=str)
    valid_hash = hashlib.sha256(canonical.encode()).hexdigest() == claimed
    cls = snapshot.get("provenance_class")
    if not valid_hash:
        return {"verdict": "FAIL", "detail": "snapshot integrity hash mismatch"}
    if cls in ("RUN_CONFIG_ONLY", "DERIVED", "UNAVAILABLE"):
        return {"verdict": "UNVERIFIED", "detail": f"provenance is {cls}, not MT5 evidence"}
    return {"verdict": "WARNING" if cls == "CURRENT_SPECIFICATION_USED_FOR_HISTORICAL_TEST" else "PASS",
            "source_classification": cls, "integrity_hash_verified": True,
            "historical_specification_verified": False}


def verify_reference_levels(records: list[dict], source_candles: list[dict], strategy_uses_levels: bool) -> dict:
    if not strategy_uses_levels:
        return {"verdict": "NOT_APPLICABLE" if records == [] else "FAIL",
                "detail": "strategy and execution evidence establish no reference-level use"}
    if not records or not source_candles:
        return {"verdict": "UNVERIFIED", "missing": "reference records or source candles"}
    # Recalculation requires exact session/timezone and source membership in
    # each record; do not infer them from current defaults.
    required = ("name", "value", "source_candle_ts", "timezone", "source_period_start",
                "source_period_end", "calculation_version", "completed")
    missing = [{"record": i, "fields": [k for k in required if r.get(k) is None]}
               for i, r in enumerate(records) if any(r.get(k) is None for k in required)]
    if missing:
        return {"verdict": "UNVERIFIED", "missing": missing}
    return {"verdict": "UNVERIFIED", "detail": "reference source selection requires strategy-specific period/session verifier"}
