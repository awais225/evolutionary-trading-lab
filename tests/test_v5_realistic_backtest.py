"""V5 §5 — the backtest must be realistic and must say what it modelled.

Everything here is checked against a real dataset in the pytest DATA root:
fills, spread, slippage, commission, swap, volume constraints and the absence of
look-ahead. No number is asserted from a fixture — each assertion re-derives the
expected value from the engine's own execution model.
"""
from __future__ import annotations

import math

import pytest

DATASET = "XAUUSD_M15_20250930_20260925_SIMULATOR"

GENOME = {
    "symbol": "XAUUSD",
    "timeframe": "M15",
    "direction": "both",
    "entry_long": {"type": "crossover", "a": "ema:20", "b": "ema:50", "dir": "up"},
    "entry_short": {"type": "crossover", "a": "ema:20", "b": "ema:50", "dir": "down"},
    "exit": {"atr_spec": "atr:14", "sl_atr_mult": 1.5, "tp_atr_mult": 3.0,
             "max_hold_bars": 48, "min_hold_bars": 1},
    "risk": {"risk_per_trade": 0.005},
}


@pytest.fixture(scope="module")
def results():
    from app.backtest.engine import BacktestRequest, run_backtest

    req = BacktestRequest(genome=GENOME, dataset_id=DATASET, stage="detail",
                          window=(0, 1800), seed_salt="v5-realism")
    res = run_backtest(req)
    assert res.ok, res.error
    return req, res


def test_execution_model_is_reported_with_provenance(results):
    _, res = results
    em = res.metrics.get("execution_model")
    assert em, "the result must state the execution model it used"
    assert em["specs_source"] in ("MT5", "TABLE", "CONFIG")
    assert isinstance(em["specs_verified"], bool)
    assert em["contract_size"] > 0 and em["point"] > 0
    assert em["fill_rule"], "the fill rule must be explicit"
    assert em["volume_min"] <= em["volume_max"]
    assert em["volume_step"] > 0
    if em["specs_source"] == "MT5":
        assert em["specs_verified"] is True


def test_fills_happen_on_the_bar_after_the_signal(results):
    """No look-ahead: an entry can never be priced inside its own signal bar."""
    req, res = results
    from app.data.engine import get_data_engine

    df = get_data_engine().get_frame(req.dataset_id).iloc[0:1800]
    opens = df["open"].to_numpy()
    ts = df["ts"].to_numpy()
    index_by_ts = {float(t): i for i, t in enumerate(ts)}
    em = res.metrics["execution_model"]
    max_slip_price = em["slippage_mean_points"] + 5 * max(em["slippage_mean_points"], 0.1)
    assert res.trades, "no trades to check"

    for t in res.trades:
        i = index_by_ts.get(float(t["entry_ts"]))
        assert i is not None, "entry timestamp is not a dataset bar"
        assert i >= 1, "entry on the very first bar is impossible"
        # entry sits within (spread/2 + slippage) of that bar's open
        band = (em["default_spread_points"] / 2.0 + max_slip_price) * em["point"]
        assert abs(t["entry_price"] - opens[i]) <= band + 1e-9, \
            f"entry price {t['entry_price']} is outside the open±cost band around {opens[i]}"
        assert t["entry_ts"] > float(ts[0])


def test_every_trade_pays_spread_commission_and_swap(results):
    req, res = results
    em = res.metrics["execution_model"]
    for t in res.trades:
        # spread cost is charged on both sides
        assert t["spread_paid"] > 0
        expected_commission = t["lots"] * em["commission_per_lot"]
        held_days = max(0.0, (t["exit_ts"] - t["entry_ts"]) / 86400.0)
        expected_swap = t["lots"] * em["swap_per_lot_per_day"] * held_days
        expected = (1.0 if t["side"] == "buy" else -1.0) * (t["exit_price"] - t["entry_price"]) \
            * t["lots"] * em["contract_size"] - expected_commission - expected_swap
        # trade JSON rounds prices to 3 decimals, so allow a cent of rounding slack
        assert math.isclose(t["pnl"], expected, rel_tol=1e-4, abs_tol=0.02), \
            f"pnl {t['pnl']} != re-derived {expected}"
        assert t["exit_reason"] in ("SL", "TP", "EXIT_COND", "MAX_HOLD")
        assert t["exit_ts"] >= t["entry_ts"]


def test_volumes_respect_broker_constraints(results):
    _, res = results
    em = res.metrics["execution_model"]
    step = em["volume_step"]
    for t in res.trades:
        lots = t["lots"]
        assert em["volume_min"] - 1e-9 <= lots <= em["volume_max"] + 1e-9
        steps = round(lots / step)
        assert abs(lots - steps * step) < 1e-9, f"{lots} is not a multiple of lot step {step}"


def test_lot_sizing_never_exceeds_the_risk_budget():
    from app.backtest.execution import lots_for_risk

    # 10k equity, 0.5% risk = $50, 150-point stop on gold (100 oz/lot)
    lots = lots_for_risk(10_000.0, 0.005, 1.50, 100.0, 100.0)
    assert lots == 0.33
    risk = lots * 1.50 * 100.0
    assert risk <= 50.0
    # a risk budget too small for one step yields no trade, never an oversized one
    assert lots_for_risk(10_000.0, 0.000001, 1.50, 100.0, 100.0) == 0.0


def test_symbol_specs_are_resolved_and_never_invented():
    from app.backtest.symbol_specs import get_symbol_specs

    gold = get_symbol_specs("XAUUSD", allow_mt5=False)
    assert gold.contract_size == 100.0 and gold.point == 0.01
    assert gold.tick_value == 1.0
    assert gold.source in ("TABLE", "CONFIG")
    unknown = get_symbol_specs("NOSUCHSYM", allow_mt5=False)
    assert unknown.source == "CONFIG"
    assert unknown.verified is False
    assert unknown.notes


def test_truncating_the_data_does_not_change_earlier_trades():
    """The strongest look-ahead check: future bars cannot influence past fills.

    Running the same genome over the first 900 bars and over all 1,800 bars must
    produce identical trades up to the point where the shorter run ended.
    """
    from app.backtest.engine import BacktestRequest, run_backtest
    from app.data.engine import get_data_engine

    short = run_backtest(BacktestRequest(genome=GENOME, dataset_id=DATASET, stage="detail",
                                         window=(0, 900), seed_salt="v5-nolook"))
    long_ = run_backtest(BacktestRequest(genome=GENOME, dataset_id=DATASET, stage="detail",
                                         window=(0, 1800), seed_salt="v5-nolook"))
    assert short.ok and long_.ok, (short.error, long_.error)
    ts = get_data_engine().get_frame(DATASET)["ts"].to_numpy()
    cutoff = float(ts[899])

    s_prefix = [t for t in short.trades if t["entry_ts"] <= cutoff]
    l_prefix = [t for t in long_.trades if t["entry_ts"] <= cutoff]
    assert s_prefix, "the control run produced no trades to compare"
    assert len(s_prefix) == len(l_prefix), (len(s_prefix), len(l_prefix))
    em = short.metrics["execution_model"]
    # slippage is drawn from a window-seeded RNG (so a run is reproducible per
    # window), therefore the *timing, side and size* must match exactly while the
    # price may differ only within the modelled cost band.
    band = (em["default_spread_points"] / 2.0 + em["slippage_mean_points"] * 3) * em["point"]
    for a, b in zip(s_prefix, l_prefix):
        assert a["entry_ts"] == b["entry_ts"] and a["side"] == b["side"]
        assert a["lots"] == b["lots"], "position sizing must not depend on future bars"
        assert abs(a["entry_price"] - b["entry_price"]) <= band + 1e-9
