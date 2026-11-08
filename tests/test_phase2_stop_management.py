"""Phase 2 regression: reference stop-management semantics (23-category suite, part)."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in ("packages/strategy_spec/src", "packages/exec_semantics/src"):
    sys.path.insert(0, str(ROOT / p))

from hero1_strategy_spec.management import TradeManagement  # noqa: E402
from hero1_exec_semantics import (  # noqa: E402
    PriceContext, SemanticsError, evaluate_stop_management)

# XAUUSD-like metadata: digits 2, point 0.01, pip = 10 points = 0.10, tick 0.01
CTX = PriceContext(pip_size=0.10, point_size=0.01, tick_size=0.01)


def mgmt(payload):
    return TradeManagement.model_validate(payload)


def P(v):
    return {"value": v, "unit": "pips"}


STEPPED = {"stepped_trailing": {"start": "after_activation", "activation": P(30),
                                "initial_locked_profit": P(0), "step": P(30),
                                "advance_per_step": P(30),
                                "first_step_to_breakeven": True, "applies_to": "BOTH"}}


def test_11_stepped_long_activation_and_first_step():
    m = mgmt(STEPPED)
    # 29.9 pips excursion: not armed, stop untouched
    ev = evaluate_stop_management(side="LONG", entry_price=2000.0, current_stop=1997.5,
                                  best_favourable=2002.99, management=m, ctx=CTX)
    assert ev.new_stop == 1997.5 and ev.rules[0].active is False
    # exactly 30 pips: armed, zero steps beyond activation -> breakeven (entry)
    ev = evaluate_stop_management(side="LONG", entry_price=2000.0, current_stop=1997.5,
                                  best_favourable=2003.0, management=m, ctx=CTX)
    assert ev.rules[0].active and ev.rules[0].steps_completed == 0
    assert ev.new_stop == 2000.0
    # 65 pips: one full step earned -> +30 locked
    ev = evaluate_stop_management(side="LONG", entry_price=2000.0, current_stop=2000.0,
                                  best_favourable=2006.5, management=m, ctx=CTX)
    assert ev.rules[0].steps_completed == 1 and ev.new_stop == 2003.0
    # jump straight to 125 pips: lands on final earned step (3)
    ev = evaluate_stop_management(side="LONG", entry_price=2000.0, current_stop=2003.0,
                                  best_favourable=2012.5, management=m, ctx=CTX)
    assert ev.rules[0].steps_completed == 3 and ev.new_stop == 2009.0


def test_12_stepped_short_mirror():
    m = mgmt(STEPPED)
    ev = evaluate_stop_management(side="SHORT", entry_price=2000.0, current_stop=2002.5,
                                  best_favourable=1993.5, management=m, ctx=CTX)
    assert ev.rules[0].steps_completed == 1 and ev.new_stop == 1997.0
    assert evaluate_stop_management(side="SHORT", entry_price=2000.0, current_stop=2002.5,
                                    best_favourable=1997.01, management=m, ctx=CTX).new_stop == 2002.5


def test_13_idempotent_reevaluation():
    m = mgmt(STEPPED)
    first = evaluate_stop_management(side="LONG", entry_price=2000.0, current_stop=1997.5,
                                     best_favourable=2006.5, management=m, ctx=CTX)
    again = evaluate_stop_management(side="LONG", entry_price=2000.0, current_stop=first.new_stop,
                                     best_favourable=2006.5, management=m, ctx=CTX)
    assert again.new_stop == first.new_stop and again.changed is False


def test_14_never_moves_backwards():
    m = mgmt(STEPPED)
    # price retraced best stays high, but also a tighter manual stop can't loosen
    ev = evaluate_stop_management(side="LONG", entry_price=2000.0, current_stop=2005.0,
                                  best_favourable=2006.5, management=m, ctx=CTX)
    assert ev.new_stop == 2005.0
    ev = evaluate_stop_management(side="SHORT", entry_price=2000.0, current_stop=1995.0,
                                  best_favourable=1993.5, management=m, ctx=CTX)
    assert ev.new_stop == 1995.0


def test_15_pip_point_conversion_explicit():
    # 30 pips on XAUUSD metadata = 3.00 price; 30 points would be 0.30.
    m_pts = mgmt({"stepped_trailing": {**STEPPED["stepped_trailing"],
                                       "activation": {"value": 30, "unit": "points"},
                                       "initial_locked_profit": {"value": 0, "unit": "points"},
                                       "step": {"value": 30, "unit": "points"},
                                       "advance_per_step": {"value": 30, "unit": "points"}}})
    ev = evaluate_stop_management(side="LONG", entry_price=2000.0, current_stop=1999.0,
                                  best_favourable=2000.65, management=m_pts, ctx=CTX)
    assert ev.rules[0].active and ev.rules[0].steps_completed == 1
    assert ev.new_stop == 2000.3
    with pytest.raises(SemanticsError) as exc:
        evaluate_stop_management(side="LONG", entry_price=2000.0, current_stop=1999.0,
                                 best_favourable=2001.0, management=m_pts, ctx=PriceContext())
    assert exc.value.code == "MISSING_PRICE_CONTEXT"


def test_16_tick_rounding_tightens_only():
    m = mgmt({"fixed_trailing": {"start": "after_activation", "activation": P(10),
                                 "trail_distance": P(37), "applies_to": "BOTH"}})
    # trail 37 pips = 3.70 -> candidate 2003.333-3.70=1999.633 -> LONG rounds DOWN to 1999.63
    ev = evaluate_stop_management(side="LONG", entry_price=2000.0, current_stop=1998.0,
                                  best_favourable=2003.333, management=m, ctx=CTX)
    assert ev.new_stop == 1999.63
    ev = evaluate_stop_management(side="SHORT", entry_price=2000.0, current_stop=2002.0,
                                  best_favourable=1996.667, management=m, ctx=CTX)
    assert ev.new_stop == 2000.37  # ceil for shorts


def test_break_even_and_compose_most_protective():
    m = mgmt({"break_even": {"activation": P(20), "locked_profit": P(2), "applies_to": "BOTH"},
              "fixed_trailing": {"start": "after_activation", "activation": P(40),
                                 "trail_distance": P(15), "applies_to": "BOTH"}})
    ev = evaluate_stop_management(side="LONG", entry_price=2000.0, current_stop=1997.0,
                                  best_favourable=2005.0, management=m, ctx=CTX)
    # BE candidate 2000.2; fixed candidate 2005-1.5=2003.5 (more protective)
    assert ev.new_stop == 2003.5
    ev = evaluate_stop_management(side="LONG", entry_price=2000.0, current_stop=1997.0,
                                  best_favourable=2002.5, management=m, ctx=CTX)
    assert ev.new_stop == 2000.2  # only BE armed


def test_candidate_beyond_market_clamped_and_flagged():
    m = mgmt({"break_even": {"activation": P(5), "locked_profit": P(50), "applies_to": "BOTH"}})
    ev = evaluate_stop_management(side="LONG", entry_price=2000.0, current_stop=1999.0,
                                  best_favourable=2001.0, management=m, ctx=CTX)
    assert ev.clamped_inside_market and ev.new_stop == 2000.99


def test_indicator_trailing_refused_explicitly():
    m = mgmt({"indicator_trailing": {"indicator": {"kind": "indicator", "name": "ATR",
                                                   "params": {"period": 14}, "field": None,
                                                   "offset": 0},
                                     "applies_to": "BOTH"}})
    with pytest.raises(SemanticsError) as exc:
        evaluate_stop_management(side="LONG", entry_price=2000.0, current_stop=1999.0,
                                 best_favourable=2010.0, management=m, ctx=CTX)
    assert exc.value.code == "INDICATOR_TRAILING_NOT_EVALUABLE_PHASE2"
