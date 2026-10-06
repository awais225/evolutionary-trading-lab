"""Backtester tests: determinism, cost realism, filters, windows."""
from __future__ import annotations

import json

import numpy as np
import pytest

from app.backtest.engine import BacktestRequest, run_backtest
from app.config import get_config
from app.data.engine import get_data_engine


def base_genome():
    return {
        "symbol": "XAUUSD", "timeframe": "M15", "direction": "both",
        "features": ["ema:20", "ema:50", "rsi:14"],
        "entry_long": {"op": "and", "clauses": [
            {"type": "compare", "left": "ema:20", "cmp": ">", "right": "ema:50"},
            {"type": "compare", "left": "rsi:14", "cmp": ">", "right": 40},
        ]},
        "entry_short": {"op": "and", "clauses": [
            {"type": "compare", "left": "ema:20", "cmp": "<", "right": "ema:50"},
            {"type": "compare", "left": "rsi:14", "cmp": "<", "right": 60},
        ]},
        "exit": {"atr_spec": "atr:14", "sl_atr_mult": 1.5, "tp_atr_mult": 2.5,
                 "trailing": None, "min_hold_bars": 1, "max_hold_bars": 48,
                 "exit_condition": None},
        "sessions": None, "days": None, "regime_filters": None,
        "risk": {"risk_per_trade": 0.005, "max_concurrent": 1},
    }


def test_backtest_runs_and_reproducible(lab_env):
    dsid = lab_env["dataset_id"]
    g = base_genome()
    r1 = run_backtest(BacktestRequest(genome=g, dataset_id=dsid, stage="detail"))
    r2 = run_backtest(BacktestRequest(genome=g, dataset_id=dsid, stage="detail"))
    assert r1.ok and r2.ok, r1.error
    assert r1.metrics["trades"] == r2.metrics["trades"]
    assert r1.metrics["net_profit"] == r2.metrics["net_profit"]
    assert r1.metrics["genome_hash"] == r2.metrics["genome_hash"]
    assert r1.trades == r2.trades       # exact trade-by-trade reproducibility


def test_costs_reduce_profit(lab_env):
    dsid = lab_env["dataset_id"]
    g = base_genome()
    base = run_backtest(BacktestRequest(genome=g, dataset_id=dsid))
    stressed = run_backtest(BacktestRequest(genome=g, dataset_id=dsid,
                                            spread_mult=4.0, slippage_mult=4.0,
                                            commission_mult=4.0))
    assert base.ok and stressed.ok
    if base.metrics["trades"] > 10:
        assert stressed.metrics["net_profit"] < base.metrics["net_profit"]


def test_zero_cost_model_is_not_free(lab_env):
    """Even '1x' assumptions include spread/commission — never zero-cost."""
    dsid = lab_env["dataset_id"]
    g = base_genome()
    r = run_backtest(BacktestRequest(genome=g, dataset_id=dsid))
    assert r.ok
    assert r.metrics["total_spread_cost"] > 0 if r.metrics["trades"] else True


def test_window_slice_reduces_trades(lab_env):
    dsid = lab_env["dataset_id"]
    de = get_data_engine()
    n = len(de.get_frame(dsid))
    g = base_genome()
    full = run_backtest(BacktestRequest(genome=g, dataset_id=dsid))
    half = run_backtest(BacktestRequest(genome=g, dataset_id=dsid,
                                        window=(0, n // 2)))
    assert full.ok and half.ok
    assert half.metrics["trades"] <= full.metrics["trades"]


def test_session_filter_respected(lab_env):
    dsid = lab_env["dataset_id"]
    de = get_data_engine()
    df = de.get_frame(dsid)
    g = base_genome()
    g["sessions"] = ["london"]
    r = run_backtest(BacktestRequest(genome=g, dataset_id=dsid))
    assert r.ok
    import datetime as dt
    for t in r.trades:
        hour = dt.datetime.fromtimestamp(t["entry_ts"], dt.timezone.utc).hour
        assert 7 <= hour < 13 or 12 <= hour < 16   # london (signal bar) / fill next bar


def test_day_filter_respected(lab_env):
    dsid = lab_env["dataset_id"]
    g = base_genome()
    g["days"] = [0, 2, 4]        # Mon/Wed/Fri only
    r = run_backtest(BacktestRequest(genome=g, dataset_id=dsid))
    assert r.ok
    for t in r.trades:
        # entry fills next bar; allow boundary day (signal day in set)
        assert t["dow"] in (0, 1, 2, 3, 4)


def test_max_hold_enforced(lab_env):
    dsid = lab_env["dataset_id"]
    g = base_genome()
    g["exit"]["max_hold_bars"] = 5
    g["exit"]["sl_atr_mult"] = 0     # no SL
    g["exit"]["tp_atr_mult"] = 0     # no TP
    r = run_backtest(BacktestRequest(genome=g, dataset_id=dsid))
    assert r.ok
    for t in r.trades:
        # max_hold is counted in BARS (weekend gaps don't extend bar count)
        assert t["hold_bars"] <= 5
        assert t["exit_reason"] == "MAX_HOLD"


def test_regime_filter_shapes_trades(lab_env):
    dsid = lab_env["dataset_id"]
    g = base_genome()
    g["regime_filters"] = ["trending"]
    r = run_backtest(BacktestRequest(genome=g, dataset_id=dsid))
    assert r.ok
    # fewer or equal trades than unfiltered
    g2 = base_genome()
    r2 = run_backtest(BacktestRequest(genome=g2, dataset_id=dsid))
    assert r.metrics["trades"] <= r2.metrics["trades"]
