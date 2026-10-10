"""V6.5 §9/§12-F — experimental per-node SL/TP offsets (zero = exact defaults).

Pure calculation tests over the project's symbol conventions.  Zero offsets
preserve the EXACT strategy levels; BUY/SELL SL offsets move outward; BUY/SELL
TP offsets move inward; conversions are instrument-specific; invalid broker
distances are refused safely; negative offsets follow the documented reverse;
custom values are isolated per node and always recorded distinguishably from
the defaults.
"""
from __future__ import annotations

import pytest

from app.backtest.symbol_specs import SymbolSpecs, get_symbol_specs
from app.live_testing.offsets import apply_offsets


def gold():
    """XAUUSD on the lab's 2-digit convention: pip = 0.01 (100 pips = $1.00)."""
    return SymbolSpecs(symbol="XAUUSD", source="TABLE", verified=True, point=0.01,
                       tick_size=0.01, tick_value=1.0, contract_size=100.0,
                       digits=2)


def test_01_zero_offsets_are_exact_noop():
    r = apply_offsets(side="BUY", entry=4200.0, sl=4190.0, tp=4230.0,
                      sl_offset_pips=0, tp_offset_pips=0, specs=gold())
    assert r["ok"] and r["sl"] == 4190.0 and r["tp"] == 4230.0
    assert r["meta"]["offsets_applied"] is False
    assert r["meta"]["sl_default"] == 4190.0 and r["meta"]["tp_default"] == 4230.0


def test_01b_unset_offsets_behave_like_zero():
    r = apply_offsets(side="SELL", entry=4200.0, sl=4210.0, tp=4180.0,
                      sl_offset_pips=None, tp_offset_pips=None, specs=gold())
    assert r["ok"] and r["sl"] == 4210.0 and r["tp"] == 4180.0


def test_02_buy_sl_offset_moves_farther_below_default():
    r = apply_offsets(side="BUY", entry=4200.0, sl=4190.0, tp=4230.0,
                      sl_offset_pips=100, specs=gold())       # 100 pips = 1.00
    assert r["ok"]
    assert r["sl"] == pytest.approx(4189.0)                   # farther from entry
    assert r["meta"]["sl_offset_pips"] == 100.0
    assert r["meta"]["sl_default"] == 4190.0                  # default preserved in meta


def test_03_sell_sl_offset_moves_farther_above_default():
    r = apply_offsets(side="SELL", entry=4200.0, sl=4210.0, tp=4180.0,
                      sl_offset_pips=100, specs=gold())
    assert r["ok"] and r["sl"] == pytest.approx(4211.0)


def test_04_buy_tp_offset_moves_inward():
    r = apply_offsets(side="BUY", entry=4200.0, sl=4190.0, tp=4230.0,
                      tp_offset_pips=200, specs=gold())       # 2.00 closer to entry
    assert r["ok"] and r["tp"] == pytest.approx(4228.0)


def test_05_sell_tp_offset_moves_inward():
    r = apply_offsets(side="SELL", entry=4200.0, sl=4210.0, tp=4180.0,
                      tp_offset_pips=200, specs=gold())
    assert r["ok"] and r["tp"] == pytest.approx(4182.0)


def test_06_negative_offsets_follow_the_documented_reverse():
    # negative SL offset = stop closer to entry; negative TP = target farther out
    r = apply_offsets(side="BUY", entry=4200.0, sl=4190.0, tp=4230.0,
                      sl_offset_pips=-50, tp_offset_pips=-50, specs=gold())
    assert r["ok"]
    assert r["sl"] == pytest.approx(4190.5)
    assert r["tp"] == pytest.approx(4230.5)


def test_07_instrument_specific_conversion_3digit_fx():
    fx = SymbolSpecs(symbol="USDJPY", source="TABLE", point=0.001, tick_size=0.001,
                     tick_value=1.0, digits=3)
    assert fx.pip_size == pytest.approx(0.01)                 # 10 points per pip
    r = apply_offsets(side="BUY", entry=150.000, sl=149.900, tp=150.200,
                      sl_offset_pips=10, specs=fx)            # 10 pips = 0.10
    assert r["ok"] and r["sl"] == pytest.approx(149.800)


def test_08_zero_offsets_never_renormalize_the_strategy_level():
    odd = SymbolSpecs(symbol="XAUUSD", source="TABLE", point=0.01, tick_size=0.01,
                      tick_value=1.0, digits=2)
    level = 4190.123456                                        # not tick-aligned
    r = apply_offsets(side="BUY", entry=4200.0, sl=level, tp=None,
                      sl_offset_pips=0, specs=odd)
    assert r["ok"] and r["sl"] == level                        # EXACTLY as computed


def test_09_invalid_placement_is_refused_never_submitted():
    # an offset so large the SL crosses the entry price
    r = apply_offsets(side="BUY", entry=4200.0, sl=4199.5, tp=4230.0,
                      sl_offset_pips=-100, specs=gold())       # -1.00 crosses entry
    assert not r["ok"] and r["code"] == "OFFSET_LEVEL_INVALID"
    # TP pushed past entry
    r2 = apply_offsets(side="BUY", entry=4200.0, sl=4190.0, tp=4200.5,
                       tp_offset_pips=100, specs=gold())
    assert not r2["ok"] and r2["code"] == "OFFSET_LEVEL_INVALID"


def test_10_broker_minimum_distance_is_enforced():
    strict = SymbolSpecs(symbol="XAUUSD", source="TABLE", point=0.01, tick_size=0.01,
                         tick_value=1.0, digits=2, stops_level_points=500)
    # default SL distance 1.00; a -99 pip offset makes it 0.01 < 500*0.01=5.00
    r = apply_offsets(side="BUY", entry=4200.0, sl=4199.0, tp=4210.0,
                      sl_offset_pips=-99, specs=strict)
    assert not r["ok"] and r["code"] == "OFFSET_STOPS_LEVEL"


def test_11_unknown_pip_convention_refuses_offsets():
    # a truly unknown convention (no digits/point established) must refuse
    class NoPip:
        pip_size = None
        tick_size = None
        digits = None
        point = None
        stops_level_points = 0
        freeze_level_points = 0
    r = apply_offsets(side="BUY", entry=4200.0, sl=4190.0, tp=None,
                      sl_offset_pips=5, specs=NoPip())
    assert not r["ok"] and r["code"] == "OFFSET_NO_PIP_CONVENTION"


def test_12_non_numeric_offsets_are_refused():
    r = apply_offsets(side="BUY", entry=4200.0, sl=4190.0, tp=None,
                      sl_offset_pips="abc", specs=gold())
    assert not r["ok"] and r["code"] == "OFFSET_INVALID"


def test_13_meta_always_distinguishes_default_and_effective():
    r = apply_offsets(side="BUY", entry=4200.0, sl=4190.0, tp=4230.0,
                      sl_offset_pips=25, tp_offset_pips=25, specs=gold())
    m = r["meta"]
    assert m["sl_default"] == 4190.0 and m["sl_effective"] != m["sl_default"]
    assert m["tp_default"] == 4230.0 and m["tp_effective"] != m["tp_default"]
    assert m["offsets_applied"] is True
