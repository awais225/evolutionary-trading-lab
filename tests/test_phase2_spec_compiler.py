"""Phase 2 regression: spec surface, compiler determinism, fingerprint identity."""
import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages/strategy_spec/src"))

from hero1_strategy_spec import (  # noqa: E402
    StrategySpec, compile_spec, ema_sma_stepped_trailing, validate_spec)
from hero1_strategy_spec.examples import bullish_engulfing  # noqa: E402

META = {"XAUUSD": {"pip_size": 0.10, "point_size": 0.01, "tick_size": 0.01,
                   "contract_size": 100, "volume_min": 0.01}}


def codes(report):
    return {(i.code, i.path) for i in report.issues}


def base_spec(**over):
    spec = {
        "schema_version": "1.1.0",
        "identity": {"name": "T", "instrument": "XAUUSD", "timeframe": "H1",
                     "direction": "LONG", "origin": "CUSTOM"},
        "entry": {"type": "compare", "op": "cross_above",
                  "left": {"kind": "indicator", "name": "EMA", "params": {"period": 9},
                           "field": None, "offset": 0},
                  "right": {"kind": "indicator", "name": "SMA", "params": {"period": 20},
                            "field": None, "offset": 0}},
        "exits": {"stop_loss": {"anchor": "entry_price", "distance": {"value": 25, "unit": "pips"}}},
        "risk": {"sizing": {"mode": "fixed_risk", "risk_pct": 1.0}},
        "execution": {"entry_timing": "next_bar_open"},
    }
    spec.update(over)
    return spec


# --- conditions (1-8) compile & round-trip through the deterministic compiler
@pytest.mark.parametrize("entry", [
    # 1 EMA cross above SMA (base), 2 cross below
    {"type": "compare", "op": "cross_below",
     "left": {"kind": "indicator", "name": "EMA", "params": {"period": 9}, "field": None, "offset": 0},
     "right": {"kind": "indicator", "name": "SMA", "params": {"period": 20}, "field": None, "offset": 0}},
    # 3 indicator > numeric value
    {"type": "compare", "op": "gt",
     "left": {"kind": "indicator", "name": "RSI", "params": {"period": 14}, "field": None, "offset": 0},
     "right": {"kind": "constant", "value": 70}},
    # 4 price above indicator (candle vs indicator)
    {"type": "compare", "op": "gt",
     "left": {"kind": "candle", "field": "close", "offset": 0},
     "right": {"kind": "indicator", "name": "EMA", "params": {"period": 50}, "field": None, "offset": 0}},
    # 5 AND, 6 OR, 7 nested groups
    {"type": "and", "children": [
        {"type": "compare", "op": "gt", "left": {"kind": "candle", "field": "close", "offset": 0},
         "right": {"kind": "constant", "value": 1}},
        {"type": "or", "children": [
            {"type": "compare", "op": "lt", "left": {"kind": "candle", "field": "close", "offset": 0},
             "right": {"kind": "constant", "value": 99999}},
            {"type": "not", "child": {"type": "compare", "op": "eq",
             "left": {"kind": "candle", "field": "close", "offset": 0},
             "right": {"kind": "constant", "value": 0}}}]}]},
])
def test_condition_shapes_validate_and_compile(entry):
    spec = base_spec(entry=entry)
    report = validate_spec(spec, broker_meta=META)
    assert report.status == "VALID", report.issues
    ir1, ir2 = compile_spec(spec, broker_meta=META), compile_spec(spec, broker_meta=META)
    assert ir1.ir == ir2.ir                      # 18 deterministic compilation
    assert ir1.fingerprint == ir2.fingerprint      # 19 stable fingerprint


def test_direction_variants():
    # 8 LONG/SHORT/BOTH parse
    for d in ("LONG", "SHORT", "BOTH"):
        s = base_spec(); s["identity"]["direction"] = d
        assert StrategySpec.model_validate(s).identity.direction.value == d


# --- trade management validation (17 conflicting/invalid, 16 conversion via meta)
def stepped(**kw):
    rule = {"start": "after_activation", "activation": {"value": 30, "unit": "pips"},
            "initial_locked_profit": {"value": 0, "unit": "pips"},
            "step": {"value": 30, "unit": "pips"},
            "advance_per_step": {"value": 30, "unit": "pips"},
            "first_step_to_breakeven": True, "applies_to": "BOTH"}
    rule.update(kw)
    return {"management": {"stepped_trailing": rule}}


def test_management_valid_with_metadata():
    report = validate_spec(base_spec(**stepped()), broker_meta=META)
    assert report.status == "VALID", report.issues


def test_management_pips_need_metadata():
    report = validate_spec(base_spec(**stepped()))
    assert report.status == "NEEDS_METADATA"
    assert any(p.startswith("management.stepped_trailing") for _, p in codes(report))


def test_management_requires_initial_stop():
    spec = base_spec(**stepped()); spec["exits"] = {"take_profit": {"ratio": 2.0}}
    report = validate_spec(spec, broker_meta=META)
    assert report.status == "INVALID"
    assert ("E_MANAGEMENT_REQUIRES_SL", "management") in codes(report)


def test_management_locked_beyond_activation_invalid():
    report = validate_spec(base_spec(**stepped(
        initial_locked_profit={"value": 35, "unit": "pips"},
        first_step_to_breakeven=False)), broker_meta=META)
    assert ("E_STEPPED_LOCKED_BEYOND_ACTIVATION",
            "management.stepped_trailing.initial_locked_profit") in codes(report)


def test_management_direction_mismatch():
    report = validate_spec(base_spec(**stepped(applies_to="SHORT")), broker_meta=META)
    assert ("E_MANAGEMENT_DIRECTION", "management.stepped_trailing.applies_to") in codes(report)


def test_breakeven_agreement_is_strict():
    with pytest.raises(Exception) as exc:  # structural rejection, never silent precedence
        StrategySpec.model_validate(base_spec(**stepped(first_step_to_breakeven=False)))
    assert "initial_locked_profit" in str(exc.value)


def test_immediate_start_rejects_activation():
    with pytest.raises(Exception) as exc:
        StrategySpec.model_validate(base_spec(**stepped(start="immediate")))
    assert "immediate" in str(exc.value)


# --- prioritized exit rules
def test_duplicate_exit_priority_rejected():
    cond = {"type": "compare", "op": "gt", "left": {"kind": "candle", "field": "close", "offset": 0},
            "right": {"kind": "constant", "value": 1}}
    spec = base_spec(exits={"stop_loss": {"anchor": "entry_price", "distance": {"value": 25, "unit": "pips"}},
                            "exit_rules": [{"name": "a", "priority": 1, "condition": cond},
                                           {"name": "b", "priority": 1, "condition": cond}]})
    report = validate_spec(spec, broker_meta=META)
    assert ("E_EXIT_RULE_PRIORITY_DUP", "exits.exit_rules[1].priority") in codes(report)


def test_ir_orders_exit_rules_with_legacy_first():
    cond = {"type": "compare", "op": "cross_below",
            "left": {"kind": "indicator", "name": "EMA", "params": {"period": 9}, "field": None, "offset": 0},
            "right": {"kind": "indicator", "name": "SMA", "params": {"period": 20}, "field": None, "offset": 0}}
    spec = base_spec(exits={"stop_loss": {"anchor": "entry_price", "distance": {"value": 25, "unit": "pips"}},
                            "explicit_exit": cond,
                            "exit_rules": [{"name": "late", "priority": 5, "condition": cond},
                                           {"name": "early", "priority": 2, "condition": cond}]})
    ir = compile_spec(spec, broker_meta=META)
    assert [r["name"] for r in ir.ir["ordered_exit_rules"]] == ["explicit_exit", "early", "late"]
    assert [r["priority"] for r in ir.ir["ordered_exit_rules"]] == [0, 2, 5]


# --- identity (19-21) and fingerprint backward compatibility
def test_fingerprint_ignores_rename():
    # 20: renaming a node never forks its semantic identity
    a = compile_spec(base_spec(), broker_meta=META)
    renamed = base_spec(); renamed["identity"]["name"] = "Something Else Entirely"
    assert compile_spec(renamed, broker_meta=META).fingerprint == a.fingerprint


def test_semantic_edit_changes_fingerprint():
    # 21 (edit -> new identity/version material): a step-size edit changes semantics
    a = compile_spec(base_spec(**stepped()), broker_meta=META)
    assert a.ir["management"]["stepped_trailing"]["step"]["value"] == 30
    changed = compile_spec(base_spec(**stepped(step={"value": 45, "unit": "pips"})), broker_meta=META)
    assert changed.fingerprint != a.fingerprint


def test_old_specs_keep_their_fingerprints():
    # 1.0.0-era spec (no management / exit_rules keys) compiled twice: equal;
    # and an explicit model round-trip (which materializes the new sections
    # as absent) must NOT drift the fingerprint.
    old = bullish_engulfing("XAUUSD", "H1")
    old["schema_version"] = "1.0.0"  # a genuine 1.0.0-era stored spec
    ir1 = compile_spec(old, broker_meta=META)
    parsed = StrategySpec.model_validate(old)
    dumped = parsed.model_dump(mode="json")          # adds management: null, exit_rules: []
    assert dumped.get("management") is None and dumped["exits"].get("exit_rules") == []
    ir2 = compile_spec(dumped, broker_meta=META)
    ir3 = compile_spec(parsed, broker_meta=META)
    assert ir1.fingerprint == ir2.fingerprint == ir3.fingerprint
    assert ir1.schema_version == "1.0.0"             # identity preserved verbatim


def test_schema_100_still_accepted():
    spec = base_spec(schema_version="1.0.0", **stepped())
    assert validate_spec(spec, broker_meta=META).status == "VALID"
    ir = compile_spec(spec, broker_meta=META)
    assert ir.schema_version == "1.0.0"


def test_generated_and_manual_share_the_contract():
    # 23: origin only records provenance; both origins validate/compile identically
    for origin in ("CUSTOM", "INTERACTIVE", "FINE_TUNED"):
        spec = base_spec(); spec["identity"]["origin"] = origin
        assert validate_spec(spec, broker_meta=META).status == "VALID"
        assert compile_spec(spec, broker_meta=META).fingerprint


def test_reference_stepped_example_compiles():
    # The brief's example: EMA9 x SMA20 + stepped trailing, nothing hardcoded
    spec = ema_sma_stepped_trailing("XAUUSD", "H1")
    report = validate_spec(spec, broker_meta=META)
    assert report.status == "VALID", report.issues
    ir = compile_spec(spec, broker_meta=META)
    assert ir.ir["management"]["stepped_trailing"]["activation"] == {"value": 30.0, "unit": "pips"}
    assert ir.ir["management"]["stepped_trailing"]["first_step_to_breakeven"] is True
    ir_other = compile_spec(ema_sma_stepped_trailing("XAUUSD", "H1", activation_pips=45), broker_meta=META)
    assert ir_other.fingerprint != ir.fingerprint  # configurable, not hardcoded


def test_invalid_rule_error_names_exact_rule():
    # 22: duplicate ATR param period 0 rejected with the offending rule path
    bad = base_spec(entry={"type": "compare", "op": "gt",
                           "left": {"kind": "indicator", "name": "ATR", "params": {"period": 0},
                                    "field": None, "offset": 0},
                           "right": {"kind": "constant", "value": 1}})
    report = validate_spec(bad, broker_meta=META)
    assert report.status == "INVALID"
    assert any(code == "E_INDICATOR_PARAMS" and path.startswith("entry")
               and "period" in msg for code, path, msg in
               ((i.code, i.path, i.message) for i in report.issues))
