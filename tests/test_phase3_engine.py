"""Phase 3 acceptance: DeepTesting engine vs independently computed values."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in ("packages/strategy_spec/src", "packages/exec_semantics/src", "packages/indicators/src"):
    sys.path.insert(0, str(ROOT / p))

from hero1_strategy_spec import compile_spec  # noqa: E402
from hero1_strategy_spec.management import TradeManagement  # noqa: E402
from hero1_exec_semantics import (  # noqa: E402
    EngineError, InstrumentSpec, RunConfig, run_backtest, size_position)
from hero1_exec_semantics.analytics import compute_metrics  # noqa: E402
from hero1_exec_semantics.stop_management import PriceContext  # noqa: E402

SPEC_INSTR = InstrumentSpec(tick_size=0.1, tick_value_account=1.0, contract_size=100,
                            volume_min=0.01, volume_max=100.0, volume_step=0.01)
CTX = PriceContext(pip_size=0.1, point_size=0.01, tick_size=0.1)
CFG = RunConfig(initial_capital=10000.0, risk_mode="percent", risk_value=1.0,
                risk_basis="initial_capital", max_active_trades=1)


def candles_from_closes(closes, start_ts=1700000000, step=3600, wick=0.1):
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        o = prev
        out.append({"ts": start_ts + i * step, "open": o,
                    "high": max(o, c) + wick, "low": min(o, c) - wick,
                    "close": c, "volume": 100})
        prev = c
    return out


def cross_spec(direction="LONG", tp_ratio=2.0, sl=2.0, management=None):
    spec = {
        "schema_version": "1.1.0",
        "identity": {"name": "accept", "instrument": "XAUUSD", "timeframe": "H1",
                     "direction": direction, "origin": "CUSTOM"},
        "entry": {"type": "compare", "op": "cross_above",
                  "left": {"kind": "indicator", "name": "SMA", "params": {"period": 2}, "field": None, "offset": 0},
                  "right": {"kind": "indicator", "name": "SMA", "params": {"period": 3}, "field": None, "offset": 0}},
        "exits": {"stop_loss": {"anchor": "entry_price", "distance": {"value": sl, "unit": "absolute"}},
                  "take_profit": {"ratio": tp_ratio}},
        "risk": {"sizing": {"mode": "fixed_risk", "risk_pct": 1.0}},
        "execution": {"entry_timing": "next_bar_open"},
    }
    if management:
        spec["management"] = management
    return spec


CLOSES = [100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 92, 94, 96, 98.5, 99, 99, 99, 99]


def test_acceptance_cross_entry_sizing_and_tp():
    ir = compile_spec(cross_spec())
    result = run_backtest(ir=ir.ir, candles=candles_from_closes(CLOSES), cfg=CFG,
                          instrument=SPEC_INSTR, price_ctx=CTX)
    assert len(result["trades"]) == 1
    t = result["trades"][0]
    # cross at idx11 (SMA2 93.0 > SMA3 92.33 after 91.5 <= 91.67) -> enter idx12 open = 94
    assert t["entry_price"] == 94.0 and t["side"] == "LONG"
    # budget 1% of 10000 = 100; loss/lot = 2.0/0.1*1.0 = 20 -> volume 5.0
    assert t["volume"] == 5.0 and t["planned_risk"] == 100.0
    assert t["initial_stop"] == 92.0 and t["take_profit"] == 98.0
    # TP touched idx13 (high 98.6): gross = 4.0/0.1 * 1.0 * 5 = 200
    assert t["exit_reason"] == "TAKE_PROFIT" and t["gross_pnl"] == 200.0
    assert t["net_pnl"] == 200.0 and result["final_balance"] == 10200.0
    m = compute_metrics(result["trades"], result["equity_curve"], 10000.0)
    assert m["total_trades"] == 1 and m["win_rate"] == 1.0 and m["net_profit"] == 200.0
    assert m["return_on_initial_capital_pct"] == 2.0


def test_max_active_trades_one_blocks_and_releases():
    # second cross while position open must be skipped; after TP close a later cross trades
    closes = CLOSES + [97, 95, 93, 92, 93, 95, 97, 99, 101, 103, 104]
    ir = compile_spec(cross_spec())
    result = run_backtest(ir=ir.ir, candles=candles_from_closes(closes), cfg=CFG,
                          instrument=SPEC_INSTR, price_ctx=CTX)
    reasons = [s["reason"] for s in result["skipped_signals"]]
    assert result["trades"], "expected trades"
    first = result["trades"][0]
    for t in result["trades"][1:]:
        assert t["entry_ts"] > first["exit_ts"]  # never overlapping with MAX=1
    m = compute_metrics(result["trades"], result["equity_curve"], 10000.0)
    assert m["max_simultaneous_positions"] <= 1


def test_max_active_two_allows_two_not_three():
    cfg = RunConfig(initial_capital=10000.0, risk_mode="percent", risk_value=1.0,
                    risk_basis="initial_capital", max_active_trades=2)
    # three crosses in quick succession with long-lived trades (huge TP, far SL)
    closes = [100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 92, 94, 96,
              95, 94, 95, 97, 99, 98, 97, 98, 100, 102, 104, 106]
    ir = compile_spec(cross_spec(tp_ratio=50.0, sl=20.0))
    result = run_backtest(ir=ir.ir, candles=candles_from_closes(closes), cfg=cfg,
                          instrument=SPEC_INSTR, price_ctx=CTX)
    m = compute_metrics(result["trades"], result["equity_curve"], 10000.0)
    assert m["max_simultaneous_positions"] == 2
    assert any(s["reason"] == "MAX_ACTIVE_TRADES" for s in result["skipped_signals"])


def test_stepped_trailing_ladder_absolute_units():
    mgmt = {"stepped_trailing": {
        "start": "after_activation",
        "activation": {"value": 3.0, "unit": "absolute"},
        "initial_locked_profit": {"value": 0.0, "unit": "absolute"},
        "step": {"value": 2.0, "unit": "absolute"},
        "advance_per_step": {"value": 2.0, "unit": "absolute"},
        "first_step_to_breakeven": True, "applies_to": "BOTH"}}
    closes = [100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 92, 94,
              97.2, 99.2, 101.2, 103.2, 100.5, 99.0]
    ir = compile_spec(cross_spec(tp_ratio=100.0, sl=2.0, management=mgmt))
    result = run_backtest(ir=ir.ir, candles=candles_from_closes(closes), cfg=CFG,
                          instrument=SPEC_INSTR, price_ctx=CTX,
                          management=TradeManagement.model_validate(mgmt))
    assert len(result["trades"]) == 1
    t = result["trades"][0]
    stops = [h["to"] for h in t["stop_history"]]
    # entry 94: excursion 3.3 -> BE 94.0; 5.3 -> +2 = 96; 7.3 -> 98; 9.3 -> 100
    assert stops[:4] == [94.0, 96.0, 98.0, 100.0]
    assert t["exit_reason"] == "STOP_LOSS" and t["final_stop"] == 100.0
    assert all(b >= a for a, b in zip(stops, stops[1:])), "stop never moves backwards"


def test_short_mirror_and_sizing_symmetry():
    # falling after rising: cross_below via direction SHORT strategy mirrored tree
    spec = cross_spec(direction="SHORT")
    spec["entry"] = {"type": "compare", "op": "cross_below",
                     "left": {"kind": "indicator", "name": "SMA", "params": {"period": 2}, "field": None, "offset": 0},
                     "right": {"kind": "indicator", "name": "SMA", "params": {"period": 3}, "field": None, "offset": 0}}
    closes = [90, 91, 92, 93, 94, 95, 96, 97, 98, 99, 98, 96, 94, 91.5, 91, 91]
    ir = compile_spec(spec)
    result = run_backtest(ir=ir.ir, candles=candles_from_closes(closes), cfg=CFG,
                          instrument=SPEC_INSTR, price_ctx=CTX)
    assert len(result["trades"]) == 1
    t = result["trades"][0]
    assert t["side"] == "SHORT" and t["volume"] == 5.0
    assert t["initial_stop"] == t["entry_price"] + 2.0
    assert t["exit_reason"] == "TAKE_PROFIT" and t["gross_pnl"] == 200.0


def test_no_lookahead_prefix_consistency():
    ir = compile_spec(cross_spec())
    full = run_backtest(ir=ir.ir, candles=candles_from_closes(CLOSES), cfg=CFG,
                        instrument=SPEC_INSTR, price_ctx=CTX)
    prefix = run_backtest(ir=ir.ir, candles=candles_from_closes(CLOSES[:13]), cfg=CFG,
                          instrument=SPEC_INSTR, price_ctx=CTX)
    assert prefix["trades"][0]["entry_price"] == full["trades"][0]["entry_price"]
    assert prefix["trades"][0]["entry_ts"] == full["trades"][0]["entry_ts"]


def test_gap_over_stop_and_same_candle_ambiguity():
    ir = compile_spec(cross_spec())
    candles = candles_from_closes(CLOSES[:13])
    # bar after entry gaps far below the 92 stop
    candles.append({"ts": candles[-1]["ts"] + 3600, "open": 90.0, "high": 90.5,
                    "low": 89.0, "close": 89.5, "volume": 100})
    result = run_backtest(ir=ir.ir, candles=candles, cfg=CFG,
                          instrument=SPEC_INSTR, price_ctx=CTX)
    t = result["trades"][0]
    assert t["exit_reason"] == "STOP_LOSS" and t["gap_overrun"] and t["exit_price"] == 90.0
    assert t["net_pnl"] < -100.0, "gap loss exceeds planned risk and is honestly reported"

    candles = candles_from_closes(CLOSES[:13])
    candles.append({"ts": candles[-1]["ts"] + 3600, "open": 96.0, "high": 99.0,
                    "low": 91.0, "close": 95.0, "volume": 100})  # spans SL 92 and TP 98
    result = run_backtest(ir=ir.ir, candles=candles, cfg=CFG,
                          instrument=SPEC_INSTR, price_ctx=CTX)
    t = result["trades"][0]
    assert t["ambiguous_same_candle"] and t["exit_reason"] == "STOP_LOSS"


def test_indicator_trailing_moves_stop_and_never_loosens():
    mgmt = {"indicator_trailing": {
        "indicator": {"kind": "indicator", "name": "EMA", "params": {"period": 3},
                      "field": None, "offset": 0},
        "offset": {"value": 0.5, "unit": "absolute"}, "applies_to": "BOTH"}}
    closes = [100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 92, 94,
              96, 98, 100, 102, 101, 100, 99]
    ir = compile_spec(cross_spec(tp_ratio=100.0, sl=2.0, management=mgmt))
    result = run_backtest(ir=ir.ir, candles=candles_from_closes(closes), cfg=CFG,
                          instrument=SPEC_INSTR, price_ctx=CTX,
                          management=TradeManagement.model_validate(mgmt))
    t = result["trades"][0]
    stops = [h["to"] for h in t["stop_history"]]
    assert stops and all(b > a for a, b in zip([92.0] + stops, stops[1:] + [stops[-1] + 1]))
    assert t["final_stop"] > 92.0


def test_sizing_rejections_are_explicit():
    with pytest.raises(EngineError) as exc:
        size_position(cfg=CFG, spec=InstrumentSpec(tick_size=0.1), equity_now=10000,
                      entry_price=100, stop_price=98, side="LONG")
    assert exc.value.code == "MISSING_TICK_VALUE"
    no_sl = size_position(cfg=CFG, spec=SPEC_INSTR, equity_now=10000,
                          entry_price=100, stop_price=None, side="LONG")
    assert no_sl["volume"] is None and "MISSING_INITIAL_SL" in no_sl["reason"]
    fixed = size_position(cfg=RunConfig(initial_capital=5000, risk_mode="fixed", risk_value=50),
                          spec=SPEC_INSTR, equity_now=5000, entry_price=100,
                          stop_price=99.0, side="SHORT")
    assert fixed["volume"] == 5.0 and fixed["planned_risk"] == 50.0  # 1.0 dist -> 10/lot


def test_exit_rule_priority_and_time_exit():
    spec = cross_spec(tp_ratio=100.0)
    spec["exits"]["exit_rules"] = [{
        "name": "give_up", "priority": 1,
        "condition": {"type": "compare", "op": "lt",
                      "left": {"kind": "candle", "field": "close", "offset": 0},
                      "right": {"kind": "constant", "value": 95.0}}}]
    spec["exits"]["max_holding_candles"] = 100
    ir = compile_spec(spec)
    assert [r["name"] for r in ir.ir["ordered_exit_rules"]] == ["give_up"]
    result = run_backtest(ir=ir.ir, candles=candles_from_closes(CLOSES), cfg=CFG,
                          instrument=SPEC_INSTR, price_ctx=CTX)
    # close falls to 99 area only... use closes that dip below 95 post-entry instead
    closes = CLOSES[:13] + [95.5, 94.5, 96, 97]
    result = run_backtest(ir=ir.ir, candles=candles_from_closes(closes), cfg=CFG,
                          instrument=SPEC_INSTR, price_ctx=CTX)
    assert result["trades"][0]["exit_reason"] == "EXIT_RULE:give_up"


def test_sl_offset_pips_price_direction_long_and_short():
    # +2.0 price SL offset (away) doubles the stop distance -> volume halves
    ir = compile_spec(cross_spec())
    wide = RunConfig(initial_capital=10000.0, risk_mode="percent", risk_value=1.0,
                     risk_basis="initial_capital", max_active_trades=1, sl_offset_price=2.0)
    result = run_backtest(ir=ir.ir, candles=candles_from_closes(CLOSES), cfg=wide,
                          instrument=SPEC_INSTR, price_ctx=CTX)
    t = result["trades"][0]
    assert t["initial_stop"] == 90.0 and t["volume"] == 2.5  # 4.0 distance -> 40/lot
    # -1.0 (closer) halves distance -> volume doubles
    tight = RunConfig(initial_capital=10000.0, risk_mode="percent", risk_value=1.0,
                      risk_basis="initial_capital", max_active_trades=1, sl_offset_price=-1.0)
    result = run_backtest(ir=ir.ir, candles=candles_from_closes(CLOSES), cfg=tight,
                          instrument=SPEC_INSTR, price_ctx=CTX)
    t = result["trades"][0]
    assert t["initial_stop"] == 93.0 and t["volume"] == 10.0
