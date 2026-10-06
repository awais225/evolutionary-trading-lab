"""Fitness & death-rule tests."""
from __future__ import annotations

from app.config import update_config
from app.fitness.evaluator import death_check, fitness
from tests.test_backtest import base_genome


def good_metrics(**over):
    m = {"trades": 120, "net_profit": 900, "gross_profit": 2200, "gross_loss": 1300,
         "profit_factor": 1.69, "win_rate": 0.52, "avg_trade": 7.5,
         "expectancy": 0.00075, "max_drawdown_pct": 0.08, "sharpe": 1.2,
         "sortino": 1.8, "total_return_pct": 0.09, "consistency": 0.7}
    m.update(over)
    return m


def test_good_strategy_survives_and_scores():
    g = base_genome()
    f, comps = fitness(good_metrics(), g)
    assert f > 0.45
    died, reasons = death_check(good_metrics(), g, stage="detail")
    assert not died, reasons


def test_insufficient_trades_kills():
    died, reasons = death_check(good_metrics(trades=3), base_genome(), stage="detail")
    assert died and any("insufficient trades" in r for r in reasons)


def test_drawdown_kills():
    died, reasons = death_check(good_metrics(max_drawdown_pct=0.55), base_genome())
    assert died and any("drawdown" in r for r in reasons)


def test_low_pf_kills():
    died, reasons = death_check(good_metrics(profit_factor=0.8, net_profit=-300,
                                             expectancy=-0.0003), base_genome())
    assert died and any("profit factor" in r for r in reasons)


def test_oos_collapse_kills():
    g = base_genome()
    oos = good_metrics(profit_factor=0.7, total_return_pct=-0.05, net_profit=-500,
                       expectancy=-0.0005, trades=60)
    died, reasons = death_check(good_metrics(), g, oos_metrics=oos)
    assert died
    assert any("OOS" in r for r in reasons)


def test_stress_degradation_kills():
    died, reasons = death_check(good_metrics(), base_genome(), stress_degradation=0.9)
    assert died and any("stress" in r for r in reasons)


def test_parameter_instability_kills():
    died, reasons = death_check(good_metrics(), base_genome(),
                                perturbation_instability=0.85)
    assert died and any("instability" in r for r in reasons)


def test_complexity_penalty_prefers_simple():
    simple = base_genome()
    complex_g = base_genome()
    complex_g["features"] = simple["features"] + ["adx:14", "bb:20:2.0", "cci:20"]
    complex_g["entry_long"]["clauses"].append(
        {"type": "compare", "left": "adx:14", "cmp": ">", "right": 25})
    complex_g["entry_long"]["clauses"].append(
        {"type": "compare", "left": "cci:20", "cmp": ">", "right": 50})
    m = good_metrics()
    f_simple, c_simple = fitness(m, simple)
    f_complex, c_complex = fitness(m, complex_g)
    assert c_simple["complexity"] > c_complex["complexity"]
    assert f_simple > f_complex      # equal performance => simpler wins


def test_negative_expectancy_damped():
    g = base_genome()
    f_pos, _ = fitness(good_metrics(), g)
    f_neg, _ = fitness(good_metrics(expectancy=-0.0001, net_profit=-50,
                                    profit_factor=1.3), g)
    assert f_neg < f_pos
