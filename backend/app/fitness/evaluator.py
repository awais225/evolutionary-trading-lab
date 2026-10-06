"""
Multi-objective fitness system.

Fitness is a weighted blend (weights configurable from the dashboard):
  profitability, risk-adjusted (sharpe/sortino), drawdown, profit factor,
  consistency, out-of-sample, robustness, complexity penalty.

Hard death rules (configurable thresholds) kill strategies outright:
  excessive drawdown, insufficient trades, PF below threshold, OOS failure,
  severe stress degradation, severe parameter instability, excessive complexity.

The objective is NOT maximal historical profit — it is robust positive
expectancy. Complexity is penalized so a simple strategy that performs
similarly to a complex one is preferred.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from ..config import get_config
from ..genome.schema import complexity


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def complexity_score(genome: Dict) -> int:
    _, _, score = complexity(genome)
    return score


def component_scores(metrics: Dict[str, Any], genome: Dict,
                     oos_metrics: Optional[Dict] = None,
                     robustness: Optional[float] = None) -> Dict[str, float]:
    """Normalize each objective to 0..1 (1 = best)."""
    cfg = get_config()
    trades = metrics.get("trades", 0)
    s: Dict[str, float] = {}

    ret = metrics.get("total_return_pct", 0.0)
    s["profitability"] = _clip01(ret / 0.50)          # 50% return -> 1.0

    sharpe = metrics.get("sharpe", 0.0)
    sortino = metrics.get("sortino", 0.0)
    s["risk_adjusted"] = _clip01((0.6 * sharpe + 0.4 * sortino) / 2.5)

    dd = metrics.get("max_drawdown_pct", 1.0)
    s["drawdown"] = _clip01(1.0 - dd / cfg.fitness.max_drawdown_pct)

    pf = metrics.get("profit_factor", 0.0)
    s["profit_factor"] = _clip01((pf - 1.0) / 1.5)    # PF 2.5 -> 1.0

    s["consistency"] = _clip01(metrics.get("consistency", 0.0))

    # trade count adequacy (soft; hard rule handled separately)
    tc = trades / max(cfg.fitness.min_trades, 1)
    s["trade_count"] = _clip01(tc)

    if oos_metrics is not None:
        oos_pf = oos_metrics.get("profit_factor", 0.0)
        oos_ret = oos_metrics.get("total_return_pct", 0.0)
        s["oos"] = _clip01(0.5 * (oos_pf - 1.0) / 1.0 + 0.5 * oos_ret / 0.25)
    else:
        s["oos"] = 0.5   # neutral until measured

    s["robustness"] = _clip01(robustness) if robustness is not None else 0.5

    n_ind, n_cond, cscore = complexity(genome)
    pen = (n_ind * cfg.fitness.complexity_penalty_per_indicator +
           n_cond * cfg.fitness.complexity_penalty_per_condition)
    s["complexity"] = _clip01(1.0 - pen / 0.25)
    return s


def fitness(metrics: Dict[str, Any], genome: Dict,
            oos_metrics: Optional[Dict] = None,
            robustness: Optional[float] = None) -> Tuple[float, Dict[str, float]]:
    cfg = get_config()
    s = component_scores(metrics, genome, oos_metrics, robustness)
    w = cfg.fitness.weights
    total = 0.0
    wsum = 0.0
    for k, weight in w.items():
        if k in s:
            total += weight * s[k]
            wsum += weight
    score = total / wsum if wsum > 0 else 0.0
    # expectancy must be positive; otherwise heavily damp
    if metrics.get("expectancy", 0.0) <= 0:
        score *= 0.25
    return round(score, 5), {k: round(v, 4) for k, v in s.items()}


def death_check(metrics: Dict[str, Any], genome: Dict,
                oos_metrics: Optional[Dict] = None,
                stress_degradation: Optional[float] = None,
                perturbation_instability: Optional[float] = None,
                stage: str = "detail") -> Tuple[bool, List[str]]:
    """Return (should_die, reasons). Hard rules from FitnessConfig."""
    cfg = get_config()
    reasons: List[str] = []
    min_trades = cfg.backtest.screen_min_trades if stage == "screen" else cfg.fitness.min_trades

    if metrics.get("trades", 0) < min_trades:
        reasons.append(f"insufficient trades ({metrics.get('trades',0)} < {min_trades})")
    min_trade_time = getattr(cfg.backtest, "min_trade_duration_seconds", None)
    if min_trade_time and min_trade_time > 0 and metrics.get("trades", 0) >= min_trades:
        avg_dur = metrics.get("avg_trade_duration_seconds", 0.0)
        if avg_dur > 0 and avg_dur < min_trade_time:
            reasons.append(f"avg trade duration {avg_dur:.1f}s < minimum required {min_trade_time}s")
    if metrics.get("max_drawdown_pct", 1.0) > cfg.fitness.max_drawdown_pct:
        reasons.append(f"drawdown {metrics.get('max_drawdown_pct',0):.1%} > limit "
                       f"{cfg.fitness.max_drawdown_pct:.1%}")
    pf = metrics.get("profit_factor", 0.0)
    if metrics.get("trades", 0) >= min_trades and pf < cfg.fitness.min_profit_factor:
        reasons.append(f"profit factor {pf} < {cfg.fitness.min_profit_factor}")
    if metrics.get("trades", 0) >= min_trades and metrics.get("sharpe", -9) < cfg.fitness.min_sharpe \
            and metrics.get("net_profit", 0) <= 0:
        reasons.append(f"sharpe {metrics.get('sharpe')} < {cfg.fitness.min_sharpe} with no profit")
    n_ind, n_cond, cscore = complexity(genome)
    if n_ind > cfg.evolution.max_indicators_hard:
        reasons.append(f"excessive complexity ({n_ind} indicators)")
    if n_cond > 12:
        reasons.append(f"excessive complexity ({n_cond} conditions)")
    if oos_metrics is not None:
        is_fit, _ = fitness(metrics, genome)
        oos_fit, _ = fitness(oos_metrics, genome)
        if is_fit > 0.15 and oos_fit < is_fit * (1.0 - cfg.fitness.oos_degradation_limit):
            reasons.append(f"OOS collapse (fitness {is_fit:.3f} -> {oos_fit:.3f})")
        if oos_metrics.get("profit_factor", 0) < 1.0 and oos_metrics.get("trades", 0) >= 10:
            reasons.append("OOS profit factor < 1.0")
    if stress_degradation is not None and stress_degradation > cfg.fitness.max_stress_degradation:
        reasons.append(f"execution stress degradation {stress_degradation:.0%} > "
                       f"{cfg.fitness.max_stress_degradation:.0%}")
    if perturbation_instability is not None and \
            perturbation_instability > cfg.fitness.max_perturbation_instability:
        reasons.append(f"parameter instability {perturbation_instability:.0%} > "
                       f"{cfg.fitness.max_perturbation_instability:.0%}")
    return (len(reasons) > 0), reasons
