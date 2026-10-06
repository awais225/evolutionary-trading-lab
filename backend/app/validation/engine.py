"""
Validation Engine — the overfitting defense battery (spec §11-12).

Stage 3: Out-of-sample test (held-out tail of the dataset)
Stage 4: Walk-forward (rolling re-window tests across the dataset)
Stage 5: Robustness:
    * parameter perturbation (threshold jitter, multiple seeds)
    * spread stress (2x, 3x)
    * slippage stress (2x, 3x)
    * commission stress
    * Monte Carlo (random entry skipping -> distribution of DD/return)
    * regime holdout (performance per regime bucket must not be entirely
      dependent on one regime)
Produces a robustness score 0..1 and a pass/fail verdict feeding death rules.
"""
from __future__ import annotations

import logging
import statistics
from typing import Any, Dict, List, Optional

import numpy as np

from ..backtest.engine import BacktestRequest, BacktestResult, run_backtest
from ..config import get_config
from ..data.engine import get_data_engine
from ..fitness.evaluator import death_check, fitness

log = logging.getLogger("validation.engine")


def _safe_run(req: BacktestRequest) -> BacktestResult:
    try:
        return run_backtest(req)
    except Exception as e:
        log.exception("validation backtest crashed")
        return BacktestResult(ok=False, error=str(e))


def run_oos(genome: Dict, dataset_id: str, base_metrics: Dict) -> Dict:
    de = get_data_engine()
    split = de.train_test_split(dataset_id)
    res = _safe_run(BacktestRequest(genome=genome, dataset_id=dataset_id,
                                    stage="oos", window=(split["cut"], split["n"])))
    out = {"ok": res.ok, "error": res.error}
    if res.ok:
        out["metrics"] = res.metrics
        out["fitness"], _ = fitness(res.metrics, genome)
        is_fit, _ = fitness(base_metrics, genome)
        out["in_sample_fitness"] = is_fit
        out["degradation"] = round(1.0 - (out["fitness"] / is_fit), 3) if is_fit > 0 else None
    return out


def run_walkforward(genome: Dict, dataset_id: str, n_folds: int = 4) -> Dict:
    de = get_data_engine()
    df = de.get_frame(dataset_id)
    n = len(df)
    fold = n // n_folds
    results = []
    for i in range(n_folds):
        lo, hi = i * fold, min(n, (i + 1) * fold)
        if hi - lo < 300:
            continue
        res = _safe_run(BacktestRequest(genome=genome, dataset_id=dataset_id,
                                        stage="wf", window=(lo, hi),
                                        seed_salt=f"wf{i}"))
        if res.ok:
            f, _ = fitness(res.metrics, genome)
            results.append({"fold": i, "window": [lo, hi], "fitness": f,
                            "trades": res.metrics["trades"],
                            "pf": res.metrics["profit_factor"],
                            "return_pct": res.metrics["total_return_pct"],
                            "dd": res.metrics["max_drawdown_pct"]})
    if not results:
        return {"ok": False, "error": "no valid folds"}
    fits = [r["fitness"] for r in results]
    pos = sum(1 for f in fits if f > 0.30)
    return {"ok": True, "folds": results, "mean_fitness": round(statistics.mean(fits), 4),
            "std_fitness": round(statistics.pstdev(fits), 4) if len(fits) > 1 else 0.0,
            "positive_folds": pos, "n_folds": len(results),
            "consistency": round(pos / len(results), 3)}


def run_perturbation(genome: Dict, dataset_id: str, base_fitness: float,
                     n_runs: int = 4, strength: float = 0.5) -> Dict:
    fits = []
    for i in range(n_runs):
        res = _safe_run(BacktestRequest(genome=genome, dataset_id=dataset_id,
                                        stage="perturbation", perturb=strength,
                                        seed_salt=f"pert{i}"))
        if res.ok:
            f, _ = fitness(res.metrics, genome)
            fits.append(f)
    if not fits:
        return {"ok": False, "error": "all perturbation runs failed"}
    mean_f = statistics.mean(fits)
    instability = round(1.0 - mean_f / base_fitness, 3) if base_fitness > 0 else 1.0
    return {"ok": True, "runs": [round(f, 4) for f in fits],
            "mean_fitness": round(mean_f, 4), "base_fitness": round(base_fitness, 4),
            "instability": instability}


def run_stress(genome: Dict, dataset_id: str, base_fitness: float) -> Dict:
    scenarios = [
        {"name": "spread_x2", "spread_mult": 2.0},
        {"name": "spread_x3", "spread_mult": 3.0},
        {"name": "slippage_x2", "slippage_mult": 2.0},
        {"name": "slippage_x3", "slippage_mult": 3.0},
        {"name": "commission_x2", "commission_mult": 2.0},
        {"name": "all_x2", "spread_mult": 2.0, "slippage_mult": 2.0, "commission_mult": 2.0},
    ]
    out = []
    worst_deg = 0.0
    for s in scenarios:
        req = BacktestRequest(genome=genome, dataset_id=dataset_id, stage="stress",
                              spread_mult=s.get("spread_mult", 1.0),
                              slippage_mult=s.get("slippage_mult", 1.0),
                              commission_mult=s.get("commission_mult", 1.0),
                              seed_salt=s["name"])
        res = _safe_run(req)
        if res.ok:
            f, _ = fitness(res.metrics, genome)
            deg = 1.0 - f / base_fitness if base_fitness > 0 else 1.0
            worst_deg = max(worst_deg, deg)
            out.append({"scenario": s["name"], "fitness": round(f, 4),
                        "degradation": round(deg, 3),
                        "pf": res.metrics["profit_factor"],
                        "net": res.metrics["net_profit"]})
        else:
            out.append({"scenario": s["name"], "error": res.error, "degradation": 1.0})
            worst_deg = 1.0
    return {"ok": True, "scenarios": out, "worst_degradation": round(worst_deg, 3)}


def run_montecarlo(genome: Dict, dataset_id: str, n_sims: int = 12,
                   skip_prob: float = 0.10) -> Dict:
    """Random trade/entry variation -> distribution of outcomes."""
    rets, dds, pfs = [], [], []
    for i in range(n_sims):
        res = _safe_run(BacktestRequest(genome=genome, dataset_id=dataset_id,
                                        stage="montecarlo", mc_skip_prob=skip_prob,
                                        seed_salt=f"mc{i}"))
        if res.ok and res.metrics["trades"] >= 5:
            rets.append(res.metrics["total_return_pct"])
            dds.append(res.metrics["max_drawdown_pct"])
            pfs.append(res.metrics["profit_factor"])
    if not rets:
        return {"ok": False, "error": "no valid simulations"}
    p5 = float(np.percentile(rets, 5))
    return {"ok": True, "sims": len(rets),
            "return_mean": round(statistics.mean(rets), 4),
            "return_p5": round(p5, 4),
            "return_positive_frac": round(sum(1 for r in rets if r > 0) / len(rets), 3),
            "dd_mean": round(statistics.mean(dds), 4),
            "dd_worst": round(max(dds), 4),
            "pf_mean": round(statistics.mean(pfs), 3)}


def run_regime_holdout(genome: Dict, dataset_id: str, trades: List[Dict]) -> Dict:
    """Group realized trades by regime; flag single-regime dependence."""
    if not trades:
        return {"ok": False, "error": "no trades"}
    buckets: Dict[str, List[float]] = {}
    for t in trades:
        key = t.get("regime") or "none"
        buckets.setdefault(key, []).append(t.get("pnl", 0.0))
    per = {k: {"trades": len(v), "pnl": round(sum(v), 2)} for k, v in buckets.items()}
    total = sum(sum(v) for v in buckets.values())
    concentration = 0.0
    if total > 0:
        concentration = max((sum(v) / total) for v in buckets.values() if sum(v) > 0) \
            if any(sum(v) > 0 for v in buckets.values()) else 1.0
    return {"ok": True, "per_regime": per, "total_pnl": round(total, 2),
            "profit_concentration": round(concentration, 3)}


def full_validation(strategy_row: Dict, base_metrics: Dict, base_fitness: float,
                    trades: List[Dict]) -> Dict:
    genome = strategy_row["genome"]
    dataset_id = base_metrics.get("dataset_id")
    cfg = get_config()

    oos = run_oos(genome, dataset_id, base_metrics)
    wf = run_walkforward(genome, dataset_id)
    pert = run_perturbation(genome, dataset_id, base_fitness, n_runs=4)
    stress = run_stress(genome, dataset_id, base_fitness)
    mc = run_montecarlo(genome, dataset_id, n_sims=10)
    hold = run_regime_holdout(genome, dataset_id, trades)

    # ---- robustness score 0..1 ----
    parts = []
    if oos.get("ok"):
        parts.append(max(0.0, min(1.0, oos["fitness"] / max(base_fitness, 1e-6))))
    else:
        parts.append(0.0)
    if wf.get("ok"):
        parts.append(wf.get("consistency", 0.0))
    if pert.get("ok"):
        parts.append(max(0.0, 1.0 - max(pert.get("instability", 1.0), 0.0)))
    if stress.get("ok"):
        parts.append(max(0.0, 1.0 - stress.get("worst_degradation", 1.0)))
    if mc.get("ok"):
        parts.append(mc.get("return_positive_frac", 0.0))
    robustness = round(statistics.mean(parts), 4) if parts else 0.0

    # ---- verdict via death rules ----
    stress_deg = stress.get("worst_degradation") if stress.get("ok") else 1.0
    instab = pert.get("instability") if pert.get("ok") else 1.0
    died, reasons = death_check(base_metrics, genome,
                                oos_metrics=oos.get("metrics"),
                                stress_degradation=stress_deg,
                                perturbation_instability=instab)
    if wf.get("ok") and wf.get("positive_folds", 0) < max(1, (wf.get("n_folds", 1) + 1) // 2):
        died, reasons = True, reasons + [
            f"walk-forward: only {wf.get('positive_folds')}/{wf.get('n_folds')} positive folds "
            f"(majority required)"]
    if mc.get("ok") and mc.get("return_positive_frac", 0) < 0.3:
        died, reasons = True, reasons + ["monte carlo: <30% positive simulations"]
    if robustness < 0.60:
        died, reasons = True, reasons + [
            f"robustness score {robustness} < 0.60 (edge not robust across OOS/WF/stress/MC)"]

    return {
        "oos": oos, "walkforward": wf, "perturbation": pert,
        "spread_slippage_stress": stress, "montecarlo": mc, "regime_holdout": hold,
        "robustness_score": robustness,
        "passed": not died,
        "death_reasons": reasons,
    }
