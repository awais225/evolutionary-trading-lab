"""
Execution & cost model for backtests and paper trading.

Models realistic conditions: bid/ask spread, commission, swap, slippage,
execution delay. Slippage is drawn from a seeded RNG so any backtest is
exactly reproducible (seed = f(strategy_hash, stage, params)).

The same classes are used by the paper engine with statistics observed from
the live feed, which is what makes backtest-vs-paper calibration meaningful.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class ExecutionParams:
    commission_per_lot: float = 7.0
    swap_per_lot_per_day: float = -2.5
    contract_size: float = 100.0
    point: float = 0.01
    slippage_model: str = "normal"     # normal | uniform | empirical
    slippage_mean_points: float = 0.8
    slippage_std_points: float = 0.6
    slippage_max_points: float = 5.0
    execution_delay_ms: int = 120
    # stress multipliers (1.0 = base assumptions)
    spread_mult: float = 1.0
    slippage_mult: float = 1.0
    commission_mult: float = 1.0
    # empirical calibration (optional; filled from paper observations)
    empirical_slippage_points: Optional[np.ndarray] = None

    @classmethod
    def from_config(cls, cfg=None) -> "ExecutionParams":
        from ..config import get_config
        bt = (cfg or get_config()).backtest
        return cls(
            commission_per_lot=bt.commission_per_lot,
            swap_per_lot_per_day=bt.swap_per_lot_per_day,
            contract_size=bt.contract_size,
            point=bt.point_value,
            slippage_model=bt.slippage_model,
            slippage_mean_points=bt.slippage_mean_points,
            slippage_std_points=bt.slippage_std_points,
            slippage_max_points=bt.slippage_max_points,
            execution_delay_ms=bt.execution_delay_ms,
        )


class SlippageModel:
    """Adverse slippage in points, always >= 0 (worst-case direction)."""

    def __init__(self, params: ExecutionParams, seed: str):
        h = int(hashlib.sha256(seed.encode()).hexdigest()[:12], 16)
        self._rng = np.random.default_rng(h)
        self._p = params

    def draw(self, n: int = 1) -> np.ndarray:
        p = self._p
        m = p.slippage_mult
        if p.slippage_model == "uniform":
            s = self._rng.uniform(0, 2 * p.slippage_mean_points * m, n)
        elif p.slippage_model == "empirical" and p.empirical_slippage_points is not None \
                and len(p.empirical_slippage_points) > 0:
            idx = self._rng.integers(0, len(p.empirical_slippage_points), n)
            s = p.empirical_slippage_points[idx] * m
        else:
            s = np.abs(self._rng.normal(p.slippage_mean_points * m,
                                        max(p.slippage_std_points * m, 1e-9), n))
        return np.minimum(s, p.slippage_max_points * max(m, 1.0))


def entry_fill_price(side: str, bar_open: float, half_spread: float,
                     slip_points: float, point: float) -> float:
    """Fill price for a market entry: buy at ask+slip, sell at bid-slip."""
    if side == "buy":
        return bar_open + half_spread + slip_points * point
    return bar_open - half_spread - slip_points * point


def exit_fill_price(side: str, price: float, half_spread: float,
                    slip_points: float, point: float) -> float:
    """Closing a buy position = selling at bid-slip; closing short = ask+slip."""
    if side == "buy":   # closing long -> sell
        return price - half_spread - slip_points * point
    return price + half_spread + slip_points * point


def lots_for_risk(equity: float, risk_per_trade: float, sl_distance: float,
                  contract_size: float, max_lots: float,
                  min_lots: float = 0.01, lot_step: float = 0.01) -> float:
    if sl_distance <= 0 or contract_size <= 0:
        return 0.0
    raw = (equity * risk_per_trade) / (sl_distance * contract_size)
    lots = max(min_lots, min(max_lots, np.floor(raw / lot_step) * lot_step))
    return round(lots, 2)


def commission_cost(lots: float, p: ExecutionParams) -> float:
    return lots * p.commission_per_lot * p.commission_mult


def swap_cost(lots: float, days_held: float, p: ExecutionParams) -> float:
    if days_held <= 0:
        return 0.0
    return lots * p.swap_per_lot_per_day * days_held
