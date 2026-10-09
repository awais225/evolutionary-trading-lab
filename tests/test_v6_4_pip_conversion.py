"""V6.4 #1 — ONE explicit pip-size conversion, derived from digits and point.

MT5_BRIDGE_DETAILS.txt "PIP SIZE (the digits rule)":

    pip_size = 10.0 * point if digits in (3, 5) else 1.0 * point
    XAUUSD on a 2-digit feed: digits=2, point=0.01, pip_size=0.01

The V6.3 product converted gold pips as 10 x point (0.10), so a "100 pip" stop
was a $10 price distance — ten times too far. These tests pin the digits rule
for 2-, 3-, 4- and 5-digit instruments (including the actual XAUUSD
configuration available to this lab: digits=2, point=0.01), the refusal when
the convention cannot be established, the preview's conversion diagnostics,
and stops-level validation. No live order is ever sent.
"""
from __future__ import annotations

import pytest

from app.backtest.symbol_specs import (
    SymbolSpecs, pip_size_from_digits, points_per_pip_from_digits,
    pips_to_price, price_to_pips, round_to_tick, stops_distance_check,
    pip_conversion_report, get_symbol_specs,
)


# --------------------------------------------------------------------------- #
# the digits rule, across every quoting family
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("digits,point,expected_pip,expected_ppp", [
    (2, 0.01, 0.01, 1.0),          # XAUUSD on a 2-digit feed (the lab's gold)
    (3, 0.001, 0.01, 10.0),        # USDJPY-style 3-digit
    (4, 0.0001, 0.0001, 1.0),      # 4-digit FX
    (5, 0.00001, 0.0001, 10.0),    # EURUSD-style 5-digit
])
def test_01_digits_rule(digits, point, expected_pip, expected_ppp):
    assert pip_size_from_digits(digits, point) == pytest.approx(expected_pip)
    assert points_per_pip_from_digits(digits) == pytest.approx(expected_ppp)


def test_02_actual_xauudp_configuration_maps_to_one_cent_pips():
    """The XAUUSD configuration available in this lab: digits=2, point=0.01.

    100 pips = $1.00 of price distance, 600 pips = $6.00 — the handoff's own
    worked example. The pre-V6.4 product rule (10 x point) produced $10/$60.
    """
    pip = pip_size_from_digits(2, 0.01)
    assert pip == pytest.approx(0.01)
    assert pips_to_price(100, pip) == pytest.approx(1.0)      # was 10.0
    assert pips_to_price(600, pip) == pytest.approx(6.0)      # was 60.0
    assert price_to_pips(1.0, pip) == pytest.approx(100.0)


def test_03_xauudp_table_spec_agrees_with_the_rule():
    """The documented XAUUSD contract facts resolve through the SAME rule."""
    specs = get_symbol_specs("XAUUSD", allow_mt5=False)
    assert specs.digits == 2 and float(specs.point) == pytest.approx(0.01)
    assert specs.pip_size == pytest.approx(0.01)
    assert specs.points_per_pip == pytest.approx(1.0)


def test_04_symbol_specs_pip_size_is_no_longer_ten_points_unconditionally():
    """A 2-digit symbol must NOT get the old '1 pip = 10 points' treatment."""
    gold = SymbolSpecs(symbol="XAUUSD", digits=2, point=0.01)
    assert gold.pip_size == pytest.approx(0.01)
    fx5 = SymbolSpecs(symbol="EURUSD", digits=5, point=0.00001)
    assert fx5.pip_size == pytest.approx(0.0001)
    jpy3 = SymbolSpecs(symbol="USDJPY", digits=3, point=0.001)
    assert jpy3.pip_size == pytest.approx(0.01)


# --------------------------------------------------------------------------- #
# refusal when the convention cannot be established — never a guess
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("digits,point", [
    (None, 0.01), (2, None), (2, 0.0), (1, 0.1), (6, 0.000001), ("x", 0.01),
])
def test_05_unestablished_convention_returns_none_not_a_guess(digits, point):
    assert pip_size_from_digits(digits, point) is None


@pytest.mark.parametrize("digits", [None, 1, 6, "x", 0])
def test_05b_points_per_pip_needs_a_known_digit_family(digits):
    assert points_per_pip_from_digits(digits) is None


def test_06_symbol_specs_refuses_when_convention_unestablished():
    bad = SymbolSpecs(symbol="WEIRD", digits=0, point=0.0)
    with pytest.raises(ValueError, match="refuse the order"):
        _ = bad.pip_size
    with pytest.raises(ValueError):
        _ = bad.points_per_pip
    payload = bad.to_payload()
    assert payload["pip_size"] is None
    assert payload["pip_convention_established"] is False


# --------------------------------------------------------------------------- #
# conversion report — the diagnostics the preview must show
# --------------------------------------------------------------------------- #
def test_07_conversion_report_states_digits_point_pip_and_distances():
    rep = pip_conversion_report(symbol="XAUUSD", digits=2, point=0.01, tick_size=0.01,
                                stops_level_points=0, freeze_level_points=0,
                                sl_pips=100, tp_pips=600, side="BUY", entry=4190.79)
    assert rep["convention_established"] is True
    assert rep["digits"] == 2 and rep["point"] == pytest.approx(0.01)
    assert rep["pip_size"] == pytest.approx(0.01)
    assert rep["points_per_pip"] == pytest.approx(1.0)
    assert rep["sl_price_distance"] == pytest.approx(1.0)      # 100 pips = $1.00
    assert rep["tp_price_distance"] == pytest.approx(6.0)      # 600 pips = $6.00
    assert rep["sl_price"] == pytest.approx(4189.79)           # BUY: stop below
    assert rep["tp_price"] == pytest.approx(4196.79)           # BUY: target above
    # SELL mirrors the sides
    rep_s = pip_conversion_report(symbol="XAUUSD", digits=2, point=0.01,
                                  sl_pips=100, tp_pips=600, side="SELL", entry=4190.79)
    assert rep_s["sl_price"] == pytest.approx(4191.79)
    assert rep_s["tp_price"] == pytest.approx(4184.79)


def test_08_conversion_report_refusal_states_the_reason():
    rep = pip_conversion_report(symbol="XAUUSD", digits=None, point=None, sl_pips=100)
    assert rep["convention_established"] is False
    assert "refused" in rep["refusal_reason"].lower() or "cannot be established" in rep["refusal_reason"].lower()


# --------------------------------------------------------------------------- #
# tick-size rounding + stops-level validation (MT5_BRIDGE_DETAILS.txt)
# --------------------------------------------------------------------------- #
def test_09_round_to_tick():
    assert round_to_tick(4189.786, 0.01, 2) == pytest.approx(4189.79)
    assert round_to_tick(1.234567, 0.00001, 5) == pytest.approx(1.23457)
    assert round_to_tick(None, 0.01, 2) is None


def test_10_stops_level_validation_refuses_a_stop_below_the_broker_minimum():
    # broker: stops_level=50 points of 0.01 => min stop distance 0.50
    chk = stops_distance_check(0.30, 0.01, 50, 0)
    assert chk["checked"] is True and chk["ok"] is False
    assert "below the broker minimum" in chk["reason"]
    ok = stops_distance_check(1.0, 0.01, 50, 0)
    assert ok["ok"] is True
    # nothing to validate when the broker reports no minimum
    assert stops_distance_check(0.01, 0.01, 0, 0)["checked"] is False
    # freeze level participates in the minimum
    chk2 = stops_distance_check(0.20, 0.01, 0, 30)
    assert chk2["ok"] is False and chk2["min_stop_dist"] == pytest.approx(0.30)


def test_11_conversion_report_carries_the_stops_checks():
    rep = pip_conversion_report(symbol="XAUUSD", digits=2, point=0.01,
                                stops_level_points=100, freeze_level_points=0,
                                sl_pips=50, side="BUY", entry=4190.0)   # 50 pips = 0.50 < 1.00 min
    assert rep["stops_check"]["sl"]["checked"] is True
    assert rep["stops_check"]["sl"]["ok"] is False
