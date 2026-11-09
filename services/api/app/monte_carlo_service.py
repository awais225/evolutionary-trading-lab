"""Reproducible trade-sequence Monte Carlo research over immutable DeepTesting trades.

This module never reruns/fabricates market candles. Methods clearly identify
whether they resample completed trade outcomes or apply recorded-cost stress.
"""
from __future__ import annotations
import math, random, statistics, time, uuid
from typing import Any

METHODS = {"RESHUFFLE", "BLOCK_BOOTSTRAP", "START_POINT", "EXECUTION_STRESS"}


def _percentile(values: list[float], p: float) -> float | None:
    if not values: return None
    xs = sorted(values); pos = (len(xs)-1)*p; lo = int(pos); hi = min(lo+1, len(xs)-1)
    return xs[lo] + (xs[hi]-xs[lo])*(pos-lo)


def eligibility_gates(summary: dict, readiness: str | None, logic_failures: int | None) -> dict[str, bool | None]:
    """Fixed mandatory review gates. Unknown upstream evidence remains unknown."""
    return {
      "simulation_count_sufficient":int(summary.get("simulations_completed",0))>=10 and int(summary.get("simulations_completed",0))==int(summary.get("simulations_requested",0)),
      "zero_ruin":summary.get("ruin_rate_pct")==0,
      "survival_at_least_99_pct":summary.get("survival_rate_pct") is not None and summary["survival_rate_pct"]>=99,
      "p95_drawdown_at_most_25_pct":summary.get("drawdown_percentiles",{}).get("95") is not None and summary["drawdown_percentiles"]["95"]<=25,
      "p99_drawdown_at_most_35_pct":summary.get("drawdown_percentiles",{}).get("99") is not None and summary["drawdown_percentiles"]["99"]<=35,
      "positive_median_return":summary.get("median_return_pct") is not None and summary["median_return_pct"]>0,
      "zero_known_logic_compliance_failures":None if logic_failures is None else int(logic_failures)==0,
      "data_readiness_not_blocked":None if readiness is None else str(readiness).upper()!="BLOCKED",
    }


def partial_summary(metrics: list[dict], requested: int, seed: int, method: str, trade_count: int, baseline_return_pct: float | None = None) -> dict:
    """Describe only persisted completed simulations; never impute pending work."""
    if not metrics:
        return {"simulations_requested":requested,"simulations_completed":0,"partial":True,"sample_count":0,"seed":seed,"method":method,"trade_count":trade_count}
    returns=[float(m["return_pct"]) for m in metrics]
    dds=[float(m["max_drawdown_pct"]) for m in metrics]
    final=[float(m["final_equity"]) for m in metrics]
    pf=[float(m["profit_factor"]) for m in metrics if m.get("profit_factor") is not None]
    recoveries=[float(m["recovery_factor"]) for m in metrics if m.get("recovery_factor") is not None]
    avg_wins=[float(m["average_win"]) for m in metrics if m.get("average_win") is not None]
    avg_losses=[float(m["average_loss"]) for m in metrics if m.get("average_loss") is not None]
    result={"simulations_requested":requested,"simulations_completed":len(metrics),"partial":True,"sample_count":len(metrics),"seed":seed,"method":method,"trade_count":trade_count,
      "mean_return_pct":statistics.fmean(returns),"median_return_pct":_percentile(returns,.5),"best_return_pct":max(returns),"worst_return_pct":min(returns),
      "return_percentiles":{str(p):_percentile(returns,p/100) for p in (5,10,25,50,75,90,95,99)},
      "mean_max_drawdown_pct":statistics.fmean(dds),"median_max_drawdown_pct":_percentile(dds,.5),"worst_max_drawdown_pct":max(dds),
      "drawdown_percentiles":{str(p):_percentile(dds,p/100) for p in (90,95,99)},
      "survival_rate_pct":sum(bool(m["survived"]) for m in metrics)/len(metrics)*100,"ruin_rate_pct":sum(bool(m["ruined"]) for m in metrics)/len(metrics)*100,
      "minimum_final_equity":min(final),"maximum_final_equity":max(final),
      "mean_win_rate_pct":statistics.fmean(float(m["win_rate_pct"]) for m in metrics),
      "max_consecutive_losses_p95":_percentile([float(m["max_consecutive_losses"]) for m in metrics],.95),
      "max_consecutive_wins_p95":_percentile([float(m["max_consecutive_wins"]) for m in metrics],.95),
      "profit_factor_median":_percentile(pf,.5),"profit_factor_mean":statistics.fmean(pf) if pf else None,"profit_factor_worst":min(pf) if pf else None,"expectancy_mean":statistics.fmean(float(m["expectancy"]) for m in metrics),
      "average_win_mean":statistics.fmean(avg_wins) if avg_wins else None,"average_loss_mean":statistics.fmean(avg_losses) if avg_losses else None,
      "recovery_factor_median":_percentile(recoveries,.5),
      "average_drawdown_duration_trades":statistics.fmean(float(m["average_drawdown_duration_trades"]) for m in metrics),
      "longest_drawdown_duration_trades":max(int(m["longest_drawdown_duration_trades"]) for m in metrics)}
    deviations = [abs(float(m["return_pct"]) - float(baseline_return_pct)) for m in metrics] if baseline_return_pct is not None else []
    result["deep_baseline_return_pct"] = baseline_return_pct
    result["mean_deviation_from_baseline_pct"] = statistics.fmean(deviations) if deviations else None
    result["worst_deviation_from_baseline_pct"] = max(deviations) if deviations else None
    return result


def simulate(trades: list[dict], config: dict, method: str, *, on_batch=None, cancel_check=None) -> dict:
    if method not in METHODS: raise ValueError(f"unsupported Monte Carlo method {method}")
    if not trades: raise ValueError("completed DeepTesting trade ledger is empty")
    n = int(config.get("simulations", 500)); points = int(config.get("random_points", 1000))
    curves_display = int(config.get("displayed_curves", 10))
    if config.get("seed") is None: raise ValueError("a reproducible random seed is required")
    seed = int(config["seed"])
    capital = float(config.get("initial_capital", 200000)); risk_pct = float(config.get("risk_pct", 1))
    block = int(config.get("block_size", 10))
    batch_size = int(config.get("batch_size", 25))
    if not 1 <= batch_size <= 250: raise ValueError("batch_size must be between 1 and 250")
    if not 10 <= n <= 5000 or not 1 <= points <= 10000 or not 1 <= curves_display <= 100:
        raise ValueError("simulation count, points or displayed curves outside supported bounds")
    if capital <= 0 or not 0 < risk_pct <= 100 or block < 1:
        raise ValueError("capital, risk or block size is invalid")
    # Trade outcomes are converted to R using the original recorded planned risk.
    r_values=[]
    for t in trades:
        risk = float(t.get("planned_risk") or t.get("sizing_calculation", {}).get("planned_risk") or 0)
        pnl = float(t.get("net_pnl") or 0)
        if risk <= 0: raise ValueError(f"trade {t.get('id')} lacks positive planned-risk evidence")
        r_values.append(pnl/risk)
    master_rng=random.Random(seed); metrics=[]; raw_paths=[]
    eq_factor=risk_pct/100.0
    cancelled = False
    for sim_i in range(n):
        if sim_i % batch_size == 0 and cancel_check and cancel_check():
            cancelled = True
            break
        sim_seed=master_rng.getrandbits(64); rng=random.Random(sim_seed)
        if method == "RESHUFFLE": seq=rng.sample(r_values, len(r_values))
        elif method == "BLOCK_BOOTSTRAP":
            seq=[]
            while len(seq)<len(r_values):
                start=rng.randrange(len(r_values)); seq.extend(r_values[(start+j)%len(r_values)] for j in range(min(block,len(r_values))))
            seq=seq[:len(r_values)]
        elif method == "START_POINT":
            start=rng.randrange(len(r_values)); seq=(r_values[start:]+r_values[:start])
        else:
            if not all(t.get("spread_cost") is not None and t.get("slippage") is not None for t in trades):
                raise ValueError("execution stress unavailable: spread_cost and slippage evidence required for every trade")
            stress=float(config.get("stress_multiplier", 1.5))
            if stress < 1: raise ValueError("stress multiplier must be >= 1")
            # Explicitly stress recorded cost per trade; no historical fill is rewritten.
            costs=[(float(t.get("spread_cost") or 0)+abs(float(t.get("slippage") or 0))) for t in trades]
            seq=[r-(costs[i]*(stress-1)/float(t.get("planned_risk") or t.get("sizing_calculation",{}).get("planned_risk"))) for i,(r,t) in enumerate(zip(r_values,trades))]
            rng.shuffle(seq)
        equity=capital; peak=capital; max_dd=0.0; curve=[capital]; wins=losses=0; consec_l=consec_w=run_l=run_w=0; max_l=max_w=0
        pnls=[]; dd_lengths=[]; dd_len=0; worst_dd=0.0
        for r in seq:
            pnl=equity*eq_factor*r
            pnls.append(pnl)
            equity=max(0.0,equity+pnl)
            curve.append(equity); peak=max(peak,equity)
            dd=(peak-equity)/peak if peak else 1.0; max_dd=max(max_dd,dd)
            worst_dd=max(worst_dd,peak-equity)
            if dd>0: dd_len+=1
            elif dd_len: dd_lengths.append(dd_len); dd_len=0
            if pnl>0: wins+=1; run_w+=1; run_l=0; max_w=max(max_w,run_w)
            else: losses+=1; run_l+=1; run_w=0; max_l=max(max_l,run_l)
        if dd_len: dd_lengths.append(dd_len)
        raw_paths.append(curve)
        wins_p=[p for p in pnls if p>0]; losses_p=[p for p in pnls if p<0]
        net=sum(pnls)
        metrics.append({"simulation_index":sim_i+1,"simulation_seed":sim_seed,"final_equity":equity,"return_pct":(equity/capital-1)*100,
          "max_drawdown_pct":max_dd*100,"max_drawdown_amount":worst_dd,"survived":equity>0,"ruined":equity<=0,
          "negative_return":equity<capital,"win_rate_pct":wins/max(1,len(seq))*100,
          "profit_factor": (sum(max(0, r) for r in seq)/abs(sum(min(0,r) for r in seq))) if any(r<0 for r in seq) else None,
          "max_consecutive_losses":max_l,"max_consecutive_wins":max_w,"trades":len(seq),
          "average_win":statistics.fmean(wins_p) if wins_p else None,
          "average_loss":statistics.fmean(losses_p) if losses_p else None,
          "expectancy":statistics.fmean(pnls) if pnls else None,
          "recovery_factor":net/worst_dd if worst_dd>0 else None,
          "average_drawdown_duration_trades":statistics.fmean(dd_lengths) if dd_lengths else 0,
          "longest_drawdown_duration_trades":max(dd_lengths) if dd_lengths else 0})
        if on_batch and (len(metrics) % batch_size == 0 or sim_i + 1 == n):
            on_batch(len(metrics), metrics, raw_paths)
    # Percentile paths normalized by trade progress, never actual timestamps.
    aligned=[]
    for curve in raw_paths:
        aligned.append([curve[-1] if points == 1 else curve[min(len(curve)-1, round(j*(len(curve)-1)/(points-1)))] for j in range(points)])
    percentile_curves={str(p):[_percentile([c[j] for c in aligned],p/100) for j in range(points)] for p in (5,50,95)}
    if not metrics: raise ValueError("cancelled before the first simulation completed")
    returns=[m["return_pct"] for m in metrics]; dds=[m["max_drawdown_pct"] for m in metrics]
    baseline_return_pct=config.get("deep_baseline_return_pct")
    baseline_deviations=[abs(float(v)-float(baseline_return_pct)) for v in returns] if baseline_return_pct is not None else []
    summary={"simulations_requested":n,"simulations_completed":len(metrics),"trade_count":len(trades),"seed":seed,"method":method,
      "mean_return_pct":statistics.fmean(returns),"median_return_pct":_percentile(returns,.5),"best_return_pct":max(returns),"worst_return_pct":min(returns),
      "return_percentiles":{str(p):_percentile(returns,p/100) for p in (5,10,25,50,75,90,95,99)},
      "mean_max_drawdown_pct":statistics.fmean(dds),"median_max_drawdown_pct":_percentile(dds,.5),"worst_max_drawdown_pct":max(dds),
      "drawdown_percentiles":{str(p):_percentile(dds,p/100) for p in (90,95,99)},
      "survival_rate_pct":sum(m["survived"] for m in metrics)/max(1,len(metrics))*100,"ruin_rate_pct":sum(m["ruined"] for m in metrics)/max(1,len(metrics))*100,
      "probability_below_initial_pct":sum(m["negative_return"] for m in metrics)/max(1,len(metrics))*100,
      "probability_negative_return_pct":sum(m["negative_return"] for m in metrics)/max(1,len(metrics))*100,
      "mean_win_rate_pct":statistics.fmean(m["win_rate_pct"] for m in metrics),
      "average_win_mean":statistics.fmean([m["average_win"] for m in metrics if m["average_win"] is not None]) if any(m["average_win"] is not None for m in metrics) else None,
      "average_loss_mean":statistics.fmean([m["average_loss"] for m in metrics if m["average_loss"] is not None]) if any(m["average_loss"] is not None for m in metrics) else None,
      "expectancy_mean":statistics.fmean(m["expectancy"] for m in metrics),
      "recovery_factor_median":_percentile([m["recovery_factor"] for m in metrics if m["recovery_factor"] is not None],.5),
      "average_drawdown_duration_trades":statistics.fmean(m["average_drawdown_duration_trades"] for m in metrics),
      "longest_drawdown_duration_trades":max(m["longest_drawdown_duration_trades"] for m in metrics),
      "max_consecutive_losses_p95":_percentile([m["max_consecutive_losses"] for m in metrics],.95),
      "profit_factor_median":_percentile([m["profit_factor"] for m in metrics if m["profit_factor"] is not None],.5),
      "profit_factor_mean":statistics.fmean([float(m["profit_factor"]) for m in metrics if m["profit_factor"] is not None]) if any(m["profit_factor"] is not None for m in metrics) else None,
      "profit_factor_worst":min(float(m["profit_factor"]) for m in metrics if m["profit_factor"] is not None) if any(m["profit_factor"] is not None for m in metrics) else None,
      "deep_baseline_return_pct":baseline_return_pct,
      "mean_deviation_from_baseline_pct":statistics.fmean(baseline_deviations) if baseline_deviations else None,
      "worst_deviation_from_baseline_pct":max(baseline_deviations) if baseline_deviations else None,
      "random_points":points,"trade_sequence_start_sampling":method=="START_POINT","cancelled":cancelled,
      "seed_strategy":"Persisted master seed deterministically generates and records an independent 64-bit seed for every simulation."}
    distribution_fields=("win_rate_pct","profit_factor","average_win","average_loss","expectancy","recovery_factor","max_consecutive_losses","max_consecutive_wins","average_drawdown_duration_trades","longest_drawdown_duration_trades","max_drawdown_amount")
    summary["simulation_distributions"]={name:{str(p):_percentile([float(m[name]) for m in metrics if m.get(name) is not None],p/100) for p in (5,25,50,75,95,99)} for name in distribution_fields}
    constraints={"max_p95_drawdown_pct":25.0,"min_survival_pct":99.0,"max_p99_drawdown_pct":35.0}
    summary["probability_exceed_max_drawdown_pct"]=sum(m["max_drawdown_pct"]>constraints["max_p95_drawdown_pct"] for m in metrics)/max(1,len(metrics))*100
    readiness=config.get("source_data_readiness")
    logic_failures=config.get("known_logic_compliance_failures")
    gates=eligibility_gates(summary,readiness,logic_failures)
    components={"survival":max(0,min(10,10-summary["ruin_rate_pct"]/10)),"drawdown":max(0,min(10,10-summary["median_max_drawdown_pct"]/10)),"returns":max(0,min(10,5+summary["median_return_pct"]/10)),"consistency":max(0,min(10,10-statistics.pstdev(returns)/10)),"sequence":max(0,min(10,10-summary["worst_max_drawdown_pct"]/10)),"execution_stress":None if method!="EXECUTION_STRESS" else max(0,min(10,10-summary["worst_max_drawdown_pct"]/10))}
    weights={"survival":.25,"drawdown":.25,"returns":.2,"consistency":.15,"sequence":.1,"execution_stress":.05}
    active_weight=sum(weights[k] for k,v in components.items() if v is not None)
    score=sum(weights[k]*v for k,v in components.items() if v is not None)
    score_status="STRONG ROBUSTNESS" if score>=8 and all(v is True for v in gates.values()) else "MODERATE ROBUSTNESS" if score>=7 else "WEAK ROBUSTNESS"
    mandatory_failed=any(v is False for v in gates.values())
    all_evidence=all(v is True for v in gates.values())
    eligibility_status="NOT ELIGIBLE" if mandatory_failed else "ELIGIBLE FOR FURTHER REVIEW" if all_evidence else "INSUFFICIENT EVIDENCE"
    return {"summary":summary,"per_simulation":metrics,"equity_percentiles":percentile_curves,
      "representative_curves":raw_paths[:curves_display],"robustness":{"score":round(score,2),"components":components,"weights":weights,"effective_weight_coverage_pct":active_weight*100,"status":score_status,"score_band":"Exceptional" if score>=9 else "Strong" if score>=8 else "Moderate" if score>=7 else "Weak" if score>=5 else "Poor","normalization":"Each component rating is clipped to 0–10, multiplied by its published fractional weight, then summed. Unavailable components contribute zero and reduce effective weight coverage; weights are never renormalized.","component_methods":{"survival":"10 - ruin_rate_pct / 10","drawdown":"10 - median_max_drawdown_pct / 10","returns":"5 + median_return_pct / 10","consistency":"10 - population_stddev_of_simulation_return_pct / 10","sequence":"10 - worst_simulation_max_drawdown_pct / 10","execution_stress":"10 - worst stressed-simulation max drawdown pct / 10; unavailable outside EXECUTION_STRESS"}},
      "eligibility":{"gates_passed":gates,"thresholds":constraints,"status":eligibility_status,"unverified_requirements":[name for name,value in gates.items() if value is None],"known_logic_compliance_failures":logic_failures,"source_data_readiness":readiness,"note":"Eligibility is only for further review, not live-trading approval; Monte Carlo does not independently validate source logic or execution evidence."},
      "method_description":{"RESHUFFLE":"trade-level random permutation","BLOCK_BOOTSTRAP":"circular contiguous trade blocks","START_POINT":"TRADE-SEQUENCE START-POINT SAMPLING: rotates completed trade outcomes; not historical candle replay","EXECUTION_STRESS":"recorded cost multiplier; only when costs are captured"}[method],"created_at":time.time()}
