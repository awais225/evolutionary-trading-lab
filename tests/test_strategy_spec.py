"""Strategy specification: model, validation engine, candle semantics, examples."""
from __future__ import annotations

import copy

import pytest

from hero1_strategy_spec import (
    ValidationReport, ReportStatus, bullish_engulfing, example_specs,
    validate_spec, SCHEMA_VERSION,
)

XAU_META = {"XAUUSD": {"pip_size": 0.1, "point_size": 0.01, "tick_size": 0.01, "tick_value": 1.0}}
EUR_META = {"EURUSD": {"pip_size": 0.0001, "point_size": 0.00001, "tick_size": 0.00001, "tick_value": 1.0}}
BTC_META = {"BTCUSD": {"pip_size": 1.0, "point_size": 0.1, "tick_size": 0.1, "tick_value": 0.1}}


def _validate(spec, **kw):
    return validate_spec(spec, **kw)


# ------------------------------------------------------------- acceptance
@pytest.mark.parametrize("timeframe", ["M1", "M5", "M15"])
def test_bullish_engulfing_valid_with_broker_meta(timeframe):
    report = _validate(bullish_engulfing("XAUUSD", timeframe),
                       expected_instrument="XAUUSD", broker_meta=XAU_META)
    assert report.status is ReportStatus.VALID
    assert report.issues == []
    assert report.summary == {"invalid": 0, "warning": 0, "needs_metadata": 0}


def test_example_specs_are_three_timeframes_same_logic():
    specs = example_specs()
    assert [s["identity"]["timeframe"] for s in specs] == ["M1", "M5", "M15"]
    logic = [s["entry"] for s in specs]
    assert logic[0] == logic[1] == logic[2]                      # identical logic
    assert all(s["identity"]["instrument"] == "XAUUSD" for s in specs)


def test_bullish_engulfing_without_broker_meta_needs_metadata():
    report = _validate(bullish_engulfing("XAUUSD", "M15"), expected_instrument="XAUUSD")
    assert report.status is ReportStatus.NEEDS_METADATA
    assert any(i.code == "RISK_METADATA_REQUIRED" and i.path == "exits.stop_loss.distance"
               for i in report.issues)
    # 10-pip buffer is preserved, not guessed away:
    spec = bullish_engulfing("XAUUSD", "M15")
    assert spec["exits"]["stop_loss"]["distance"] == {"unit": "pips", "value": 10}


def test_pip_size_not_hardcoded_anywhere_in_package():
    import pathlib, re
    pkg = pathlib.Path("/home/user/hero1-trading-platform/packages/strategy_spec/src/hero1_strategy_spec")
    suspicious = re.compile(r"pip_size\s*[:=]\s*0\.0?1\b")
    for f in pkg.glob("*.py"):
        assert not suspicious.search(f.read_text()), f"hardcoded pip size in {f.name}"


# ------------------------------------------------------- structural layer
def test_missing_required_field_is_structural():
    spec = bullish_engulfing("XAUUSD", "M5")
    del spec["identity"]["name"]
    report = _validate(spec, expected_instrument="XAUUSD", broker_meta=XAU_META)
    assert report.status is ReportStatus.INVALID
    assert any(i.code == "E_STRUCTURAL" and "name" in i.path for i in report.issues)


def test_missing_exits_rejected():
    spec = bullish_engulfing("XAUUSD", "M5")
    spec["exits"] = {}
    report = _validate(spec, expected_instrument="XAUUSD", broker_meta=XAU_META)
    assert report.status is ReportStatus.INVALID
    assert any(i.code == "EXIT_REQUIRED" for i in report.issues)


def test_invalid_schema_version():
    spec = bullish_engulfing("XAUUSD", "M5")
    spec["schema_version"] = "99.0.0"
    report = _validate(spec, expected_instrument="XAUUSD", broker_meta=XAU_META)
    assert report.status is ReportStatus.INVALID
    assert any(i.code == "SCHEMA_VERSION_UNSUPPORTED" for i in report.issues)


def test_extra_fields_rejected():
    spec = bullish_engulfing("XAUUSD", "M5")
    spec["identity"]["evil_field"] = "rm -rf /"
    report = _validate(spec, expected_instrument="XAUUSD", broker_meta=XAU_META)
    assert report.status is ReportStatus.INVALID
    assert any(i.code == "E_STRUCTURAL" for i in report.issues)


# -------------------------------------------------------- semantic layer
def test_unsupported_timeframe():
    spec = bullish_engulfing("XAUUSD", "M5")
    spec["identity"]["timeframe"] = "M3"
    report = _validate(spec, expected_instrument="XAUUSD", broker_meta=XAU_META)
    assert report.status is ReportStatus.INVALID
    assert any(i.code == "TIMEFRAME_INVALID" for i in report.issues)


def test_instrument_mismatch_rejected():
    spec = bullish_engulfing("EURUSD", "M5")
    report = _validate(spec, expected_instrument="XAUUSD", broker_meta=EUR_META)
    assert report.status is ReportStatus.INVALID
    assert any(i.code == "INSTRUMENT_MISMATCH" for i in report.issues)


def test_nonsense_expression_tree_rejected():
    spec = bullish_engulfing("XAUUSD", "M5")
    spec["entry"] = {"type": "xor", "children": []}   # no such combinator
    report = _validate(spec, expected_instrument="XAUUSD", broker_meta=XAU_META)
    assert report.status is ReportStatus.INVALID
    assert any(i.code == "E_STRUCTURAL" for i in report.issues)


def test_malformed_operand_rejected():
    spec = bullish_engulfing("XAUUSD", "M5")
    spec["entry"] = {"type": "compare", "op": "gt",
                     "left": {"kind": "candle", "field": "close", "offset": 0},
                     "right": {"kind": "constant", "value": "not-a-number"}}
    report = _validate(spec, expected_instrument="XAUUSD", broker_meta=XAU_META)
    assert report.status is ReportStatus.INVALID


def test_candle_offset_out_of_bounds_rejected():
    spec = bullish_engulfing("XAUUSD", "M5")
    spec["entry"]["children"][0]["left"]["offset"] = 9999
    report = _validate(spec, expected_instrument="XAUUSD", broker_meta=XAU_META)
    assert report.status is ReportStatus.INVALID
    assert any(i.code == "E_STRUCTURAL" for i in report.issues)   # pydantic le=500


def test_unsupported_indicator_rejected():
    spec = bullish_engulfing("XAUUSD", "M5")
    spec["entry"] = {"type": "compare", "op": "cross_above",
                     "left": {"kind": "indicator", "name": "ICHIMOKU", "params": {}, "offset": 0},
                     "right": {"kind": "constant", "value": 0.0}}
    report = _validate(spec, expected_instrument="XAUUSD", broker_meta=XAU_META)
    assert report.status is ReportStatus.INVALID


def test_invalid_indicator_param_range():
    spec = bullish_engulfing("XAUUSD", "M5")
    spec["entry"] = {"type": "compare", "op": "gt",
                     "left": {"kind": "indicator", "name": "RSI", "params": {"period": 0}, "offset": 0},
                     "right": {"kind": "constant", "value": 50.0}}
    report = _validate(spec, expected_instrument="XAUUSD", broker_meta=XAU_META)
    assert report.status is ReportStatus.INVALID
    assert any(i.code == "E_INDICATOR_PARAMS" for i in report.issues)


def test_macd_fast_must_be_below_slow():
    spec = bullish_engulfing("XAUUSD", "M5")
    spec["entry"] = {"type": "compare", "op": "cross_above",
                     "left": {"kind": "indicator", "name": "MACD",
                              "params": {"fast": 26, "slow": 12}, "field": "histogram", "offset": 0},
                     "right": {"kind": "constant", "value": 0.0}}
    report = _validate(spec, expected_instrument="XAUUSD", broker_meta=XAU_META)
    assert report.status is ReportStatus.INVALID


def test_bbands_and_stoch_field_selectors():
    ok = bullish_engulfing("XAUUSD", "M5")
    ok["entry"] = {"type": "compare", "op": "cross_above",
                   "left": {"kind": "indicator", "name": "BBANDS", "params": {},
                            "field": "upper", "offset": 0},
                   "right": {"kind": "candle", "field": "close", "offset": 0}}
    assert _validate(ok, expected_instrument="XAUUSD", broker_meta=XAU_META).status is ReportStatus.VALID
    bad = copy.deepcopy(ok)
    bad["entry"]["left"]["field"] = "middle"
    r = _validate(bad, expected_instrument="XAUUSD", broker_meta=XAU_META)
    assert r.status is ReportStatus.INVALID and any(i.code == "E_INDICATOR_PARAMS" for i in r.issues)


def test_rsi_valid_reference_passes():
    spec = bullish_engulfing("XAUUSD", "M5")
    spec["entry"] = {"type": "and", "children": [
        spec["entry"],
        {"type": "compare", "op": "lt",
         "left": {"kind": "indicator", "name": "RSI", "params": {"period": 14}, "offset": 0},
         "right": {"kind": "constant", "value": 80.0}},
    ]}
    report = _validate(spec, expected_instrument="XAUUSD", broker_meta=XAU_META)
    assert report.status is ReportStatus.VALID
    assert report.required_warmup_candles >= 16


# ---------------------------------------------------------- risk layer
def test_invalid_risk_definition_rejected():
    spec = bullish_engulfing("XAUUSD", "M5")
    spec["risk"]["sizing"] = {"mode": "fixed_volume"}             # missing volume
    report = _validate(spec, expected_instrument="XAUUSD", broker_meta=XAU_META)
    assert report.status is ReportStatus.INVALID


def test_take_profit_exactly_one_of_distance_ratio():
    spec = bullish_engulfing("XAUUSD", "M5")
    spec["exits"]["take_profit"] = {"ratio": 2.0, "distance": {"unit": "pips", "value": 20}}
    report = _validate(spec, expected_instrument="XAUUSD", broker_meta=XAU_META)
    assert report.status is ReportStatus.INVALID


def test_atr_multiple_distance_requires_atr_period():
    spec = bullish_engulfing("XAUUSD", "M5")
    spec["exits"]["stop_loss"] = {"anchor": "entry_price",
                                  "distance": {"unit": "atr_multiple", "value": 1.5}}
    report = _validate(spec, expected_instrument="XAUUSD", broker_meta=XAU_META)
    assert report.status is ReportStatus.INVALID


def test_absolute_distance_needs_no_broker_meta():
    spec = bullish_engulfing("XAUUSD", "M5")
    spec["exits"]["stop_loss"] = {"anchor": "signal_low",
                                  "distance": {"unit": "absolute", "value": 5.0}}
    report = _validate(spec, expected_instrument="XAUUSD")   # NO meta supplied
    assert report.status is ReportStatus.VALID


def test_direction_overrides_validate():
    spec = bullish_engulfing("EURUSD", "M15")
    spec["risk"]["long"] = {"risk_pct": 0.5}
    spec["risk"]["short"] = {"stop_loss": {"anchor": "entry_price",
                                           "distance": {"unit": "pips", "value": 15}}}
    report = _validate(spec, expected_instrument="EURUSD", broker_meta=EUR_META)
    assert report.status is ReportStatus.VALID


# ------------------------------------------------------ execution layer
def test_forming_candle_requires_explicit_permission():
    spec = bullish_engulfing("XAUUSD", "M5")
    spec["execution"]["candle"] = "forming"
    r1 = _validate(spec, expected_instrument="XAUUSD", broker_meta=XAU_META)
    assert r1.status is ReportStatus.INVALID
    assert any(i.code == "E_FORMING_CANDLE_FORBIDDEN" for i in r1.issues)
    spec["execution"]["permit_forming_candle"] = True
    r2 = _validate(spec, expected_instrument="XAUUSD", broker_meta=XAU_META)
    assert r2.status is ReportStatus.VALID


def test_forming_operand_blocked_when_not_permitted():
    spec = bullish_engulfing("XAUUSD", "M5")
    spec["entry"] = {"type": "compare", "op": "gt",
                     "left": {"kind": "forming_candle", "field": "close"},
                     "right": {"kind": "candle", "field": "open", "offset": 0}}
    r = _validate(spec, expected_instrument="XAUUSD", broker_meta=XAU_META)
    assert r.status is ReportStatus.INVALID
    assert any(i.code == "E_FORMING_CANDLE_FORBIDDEN" for i in r.issues)


def test_signal_close_plus_forming_candle_rejected():
    spec = bullish_engulfing("XAUUSD", "M5")
    spec["execution"] = {"entry_timing": "signal_close", "candle": "forming",
                         "permit_forming_candle": True, "order_type": "market",
                         "position_handling": "single"}
    r = _validate(spec, expected_instrument="XAUUSD", broker_meta=XAU_META)
    assert r.status is ReportStatus.INVALID
    assert any(i.code == "E_EXEC_TIMING" for i in r.issues)


# ------------------------------------------------------------ safety layer
def test_absurdly_deep_tree_rejected():
    node = {"type": "compare", "op": "gt",
            "left": {"kind": "candle", "field": "close", "offset": 0},
            "right": {"kind": "constant", "value": 0.0}}
    for _ in range(12):
        node = {"type": "not", "child": node}
    spec = bullish_engulfing("XAUUSD", "M5")
    spec["entry"] = node
    r = _validate(spec, expected_instrument="XAUUSD", broker_meta=XAU_META)
    assert r.status is ReportStatus.INVALID
    assert any(i.code == "E_SAFETY_DEPTH" for i in r.issues)


def test_huge_tree_rejected():
    children = [
        {"type": "compare", "op": "gt",
         "left": {"kind": "candle", "field": "close", "offset": 0},
         "right": {"kind": "constant", "value": float(i)}}
        for i in range(70)
    ]
    # "and" caps at 16 children structurally, so use nested ors instead:
    node = children[0]
    for c in children[1:17]:
        node = {"type": "or", "children": [node, c]}
    for c in children[17:]:
        node = {"type": "or", "children": [node, c]}
    spec = bullish_engulfing("XAUUSD", "M5")
    spec["entry"] = node
    r = _validate(spec, expected_instrument="XAUUSD", broker_meta=XAU_META)
    assert r.status is ReportStatus.INVALID
    assert any(i.code in ("E_SAFETY_SIZE", "E_SAFETY_DEPTH") for i in r.issues)


# ------------------------------------------------- candle/lookahead safety
def test_current_and_previous_candle_semantics_are_explicit():
    spec = bullish_engulfing("XAUUSD", "M15")
    prev_close = spec["entry"]["children"][0]["left"]   # prev close < prev open
    curr_close = spec["entry"]["children"][1]["left"]   # curr close > curr open
    assert (prev_close["offset"], curr_close["offset"]) == (1, 0)
    # cross above compares offset 0 vs offset 1 — never a future candle
    assert spec["execution"]["candle"] == "completed"


def test_indicator_cross_compares_current_with_previous():
    spec = bullish_engulfing("XAUUSD", "M5")
    spec["entry"] = {"type": "compare", "op": "cross_above",
                     "left": {"kind": "indicator", "name": "EMA", "params": {"period": 20}, "offset": 0},
                     "right": {"kind": "indicator", "name": "EMA", "params": {"period": 50}, "offset": 0}}
    r = _validate(spec, expected_instrument="XAUUSD", broker_meta=XAU_META)
    assert r.status is ReportStatus.VALID


def test_cross_operand_must_be_candle_or_indicator():
    spec = bullish_engulfing("XAUUSD", "M5")
    spec["entry"] = {"type": "compare", "op": "cross_below",
                     "left": {"kind": "constant", "value": 1.0},
                     "right": {"kind": "candle", "field": "close", "offset": 0}}
    r = _validate(spec, expected_instrument="XAUUSD", broker_meta=XAU_META)
    assert r.status is ReportStatus.INVALID
    assert any(i.code == "E_CROSS_OPERAND" for i in r.issues)


def test_warmup_exceeds_candle_lookback():
    assert bullish_engulfing("XAUUSD", "M5")["entry"]  # sanity
    base = _validate(bullish_engulfing("XAUUSD", "M5"),
                     expected_instrument="XAUUSD", broker_meta=XAU_META)
    assert base.required_warmup_candles == 1   # no indicators → just completed-candle context
