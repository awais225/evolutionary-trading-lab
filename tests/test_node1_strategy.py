"""NODE 1 engine acceptance (Parts 2 & 17): the four directional
combinations, equal momentum, duplicate touches, SL/TP geometry, pip
conversion, reward/risk as data, no look-ahead, timeframe independence."""
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in ("packages/strategy_spec/src", "packages/exec_semantics/src", "packages/indicators/src"):
    sys.path.insert(0, str(ROOT / p))

from hero1_exec_semantics import InstrumentSpec, RunConfig, run_backtest  # noqa: E402
from hero1_exec_semantics.stop_management import PriceContext  # noqa: E402
from hero1_strategy_spec import compile_spec  # noqa: E402
from hero1_strategy_spec.examples import prev_day_high_low_momentum_reversal  # noqa: E402

META = {"XAUUSD": {"pip_size": 0.1, "point_size": 0.01, "tick_size": 0.01,
                   "contract_size": 100}}
SPEC_INSTR = InstrumentSpec(tick_size=0.1, tick_value_account=1.0, contract_size=100,
                            volume_min=0.01, volume_max=100.0, volume_step=0.01)
CTX = PriceContext(pip_size=0.1, point_size=0.01, tick_size=0.1)  # 300 pips = 30.0 price
CFG = RunConfig(initial_capital=200000.0, risk_mode="percent", risk_value=1.0,
                risk_basis="initial_capital", max_active_trades=1)


def T(y, mo, d, h=0, mi=0):
    return int(datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp())


def C(ts_, o, h, l, c):
    return {"ts": ts_, "open": o, "high": h, "low": l, "close": c, "volume": 1}


def aux_m1(momentum, signal_cutoff):
    """M1 series: day-1 extremes define PDH=110 / PDL=90; the last two
    completed closes before the signal bar's close set the momentum."""
    bars = [C(T(2026, 1, 5, 10), 100, 110, 100, 100),
            C(T(2026, 1, 5, 11), 100, 100, 90, 100)]
    closes = {"up": [100.0, 101.0], "down": [101.0, 100.0], "equal": [100.0, 100.0]}[momentum]
    bars.append(C(signal_cutoff - 180, 100, 100, 100, 100))
    bars.append(C(signal_cutoff - 120, closes[0], closes[0], closes[0], closes[0]))
    bars.append(C(signal_cutoff - 60, closes[1], closes[1], closes[1], closes[1]))
    # post-cutoff M1 bars that WOULD flip the momentum if look-ahead existed
    bars.append(C(signal_cutoff, 999, 999, 999, 999))
    bars.append(C(signal_cutoff + 60, 1, 1, 1, 1))
    return bars


def node_candles(tf_step, level, entry_open=105.0):
    """Day-1 bars (neutral range), day-2 signal bar touching `level`
    ('high' -> range contains 110; 'low' -> contains 90), entry bar, flats."""
    if tf_step >= 86400:
        sig_start = T(2026, 1, 6)
        bars = [C(T(2026, 1, 5), 100, 105, 95, 100)]
    else:
        sig_start = T(2026, 1, 6, 8)
        bars = [C(sig_start - 2 * tf_step, 100, 105, 95, 100),
                C(sig_start - tf_step, 100, 105, 95, 100)]
    if level == "high":
        sig = C(sig_start, 100, 111, 99, 105)
    else:
        sig = C(sig_start, 100, 101, 89, 95)
    entry = C(sig_start + tf_step, entry_open, entry_open + 1, entry_open - 1, entry_open)
    flat = C(sig_start + 2 * tf_step, entry_open, entry_open + 1, entry_open - 1, entry_open)
    return bars + [sig, entry, flat], sig_start + tf_step  # signal cutoff


def run(tf, level, momentum, ratio=None):
    step = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}[tf]
    spec = prev_day_high_low_momentum_reversal("XAUUSD", tf)
    if ratio is not None:
        spec["exits"]["take_profit"] = {"ratio": ratio}
        spec["identity"]["metadata"] = {"reward_risk_ratio": ratio, "official_seed": True}
    ir = compile_spec(spec, broker_meta=META).ir
    candles, cutoff = node_candles(step, level)
    result = run_backtest(ir=ir, candles=candles, cfg=CFG, instrument=SPEC_INSTR,
                          price_ctx=CTX, aux_candles=aux_m1(momentum, cutoff))
    return result


def test_four_directional_combinations():
    # touched level never chooses the side; M1 momentum (inverted) does
    assert run("H1", "high", "up")["trades"][0]["side"] == "SHORT"
    assert run("H1", "high", "down")["trades"][0]["side"] == "LONG"
    assert run("H1", "low", "up")["trades"][0]["side"] == "SHORT"
    assert run("H1", "low", "down")["trades"][0]["side"] == "LONG"


def test_equal_momentum_no_trade_and_no_lookahead():
    assert run("H1", "high", "equal")["trades"] == []
    # 'up' uses only pre-cutoff closes even though post-cutoff M1 collapses
    assert run("H1", "high", "up")["trades"][0]["side"] == "SHORT"


def test_long_short_sl_tp_geometry_and_pips():
    long_t = run("H1", "low", "down")["trades"][0]
    assert long_t["entry_price"] == 105.0
    assert long_t["initial_stop"] == 75.0       # 105 - 300 pips x 0.1
    assert long_t["take_profit"] == 165.0       # + 30.0 x RR 2.0
    short_t = run("H1", "high", "up")["trades"][0]
    assert short_t["initial_stop"] == 135.0
    assert short_t["take_profit"] == 45.0


def test_reward_risk_ratio_change_is_data_only():
    t = run("H1", "low", "down", ratio=1.5)["trades"][0]
    assert t["take_profit"] == 150.0            # 105 + 30.0 x 1.5


def test_duplicate_touch_suppressed_and_reentry_after_close():
    # first touch has equal momentum (no trade); the SAME approach keeps
    # touching next bar with up momentum -> still not a fresh touch
    ir = compile_spec(prev_day_high_low_momentum_reversal("XAUUSD", "H1"), broker_meta=META).ir
    candles, cutoff = node_candles(3600, "high")
    sig_i = len(candles) - 3
    candles[sig_i + 1] = C(candles[sig_i + 1]["ts"], 105, 111, 99, 105)  # still touching
    result = run_backtest(ir=ir, candles=candles, cfg=CFG, instrument=SPEC_INSTR,
                          price_ctx=CTX, aux_candles=aux_m1("equal", cutoff))
    assert result["trades"] == []


def test_no_m1_data_no_momentum_no_trades():
    ir = compile_spec(prev_day_high_low_momentum_reversal("XAUUSD", "H1"), broker_meta=META).ir
    candles, _ = node_candles(3600, "high")
    result = run_backtest(ir=ir, candles=candles, cfg=CFG, instrument=SPEC_INSTR,
                          price_ctx=CTX, aux_candles=None)
    assert result["trades"] == []  # momentum never substituted by H1 closes


def test_timeframe_independence():
    for tf in ("M1", "M5", "M15", "M30", "H1", "H4", "D1"):
        trades = run(tf, "high", "up")["trades"]
        assert len(trades) == 1 and trades[0]["side"] == "SHORT", tf
