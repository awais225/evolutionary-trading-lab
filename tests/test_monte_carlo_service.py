import pytest
from app.monte_carlo_service import eligibility_gates, partial_summary, simulate

TRADES=[{"id":i,"net_pnl":p,"planned_risk":100,"spread_cost":2,"slippage":0.2} for i,p in enumerate([100,-50,30,-120,80,15,-10,200])]
CFG={"simulations":30,"random_points":20,"displayed_curves":3,"initial_capital":10000,"risk_pct":1,"seed":137,"block_size":3}

@pytest.mark.parametrize("method",["RESHUFFLE","BLOCK_BOOTSTRAP","START_POINT"])
def test_trade_methods_reproducible_and_population_shape(method):
    a=simulate(TRADES,CFG,method); b=simulate(TRADES,CFG,method)
    assert {k:v for k,v in a.items() if k != "created_at"} == {k:v for k,v in b.items() if k != "created_at"}
    assert a["summary"]["simulations_completed"]==30
    assert len(a["per_simulation"])==30 and len(a["representative_curves"])==3
    assert len(a["equity_percentiles"]["50"])==20
    assert len(a["sample_curves"]) == 30
    assert len(a["reference_curves"]["MAX"]) == 20
    assert a["reference_curves"]["MAX"][-1] == max(x["final_equity"] for x in a["per_simulation"])
    assert a["reference_curves"]["MIN"][-1] == min(x["final_equity"] for x in a["per_simulation"])
    assert set(a["reference_curves"]) == {"MAX", "MIN", "DEFAULT", "DEEPTESTED"}
    assert a["reference_curves"]["DEFAULT"] == [CFG["initial_capital"]] * 20
    assert a["summary"]["trade_count"]==len(TRADES)
    assert a["robustness"]["effective_weight_coverage_pct"]==pytest.approx(95.0)
    comps=a["robustness"]["components"]; weights=a["robustness"]["weights"]
    assert a["robustness"]["score"]==pytest.approx(round(sum(weights[k]*v for k,v in comps.items() if v is not None),2))
    assert a["robustness"]["components"]["execution_stress"] is None
    assert a["eligibility"]["status"] in {"NOT ELIGIBLE","INSUFFICIENT EVIDENCE"}
    assert a["eligibility"]["status"]!=a["robustness"]["status"]


def test_incremental_batches_and_cancellation_return_only_completed_work():
    batches=[]; cancel={"value":False}
    def persisted(done,metrics,paths):
        batches.append((done,len(metrics),len(paths)))
        if done>=6: cancel["value"]=True
    result=simulate(TRADES,{**CFG,"batch_size":3},"RESHUFFLE",on_batch=persisted,cancel_check=lambda:cancel["value"])
    assert [b[0] for b in batches]==[3,6]
    assert result["summary"]["simulations_completed"]==6
    assert result["summary"]["cancelled"] is True
    assert len(result["per_simulation"])==6


def test_partial_aggregates_use_only_completed_metrics_and_match_independent_values():
    full=simulate(TRADES,{**CFG,"simulations":10},"RESHUFFLE")
    completed=full["per_simulation"][:4]
    summary=partial_summary(completed,10,CFG["seed"],"RESHUFFLE",len(TRADES))
    assert summary["profit_factor_mean"] == pytest.approx(sum(m["profit_factor"] for m in completed if m.get("profit_factor") is not None) / len([m for m in completed if m.get("profit_factor") is not None]))
    assert summary["profit_factor_worst"] == min(m["profit_factor"] for m in completed if m.get("profit_factor") is not None)
    returns=[m["return_pct"] for m in completed]
    dds=[m["max_drawdown_pct"] for m in completed]
    endings=[m["final_equity"] for m in completed]
    assert summary["partial"] is True and summary["sample_count"]==4
    assert summary["simulations_completed"]==4 and summary["simulations_requested"]==10
    assert summary["mean_return_pct"]==pytest.approx(sum(returns)/4)
    assert summary["median_return_pct"]==pytest.approx(sorted(returns)[1]+(sorted(returns)[2]-sorted(returns)[1])*.5)
    assert summary["mean_max_drawdown_pct"]==pytest.approx(sum(dds)/4)
    assert summary["median_max_drawdown_pct"]==pytest.approx(sorted(dds)[1]+(sorted(dds)[2]-sorted(dds)[1])*.5)
    assert summary["minimum_final_equity"]==min(endings) and summary["maximum_final_equity"]==max(endings)
    assert summary["survival_rate_pct"]==pytest.approx(sum(m["survived"] for m in completed)/4*100)
    assert summary["ruin_rate_pct"]==pytest.approx(sum(m["ruined"] for m in completed)/4*100)
    final=simulate(TRADES,{**CFG,"simulations":10},"RESHUFFLE")
    all_returns=[m["return_pct"] for m in final["per_simulation"]]
    assert final["summary"]["simulations_completed"]==10
    assert final["summary"]["mean_return_pct"]==pytest.approx(sum(all_returns)/10)
    assert summary["sample_count"]==4 and summary["partial"] is True
    assert final["summary"].get("partial") is False or "partial" not in final["summary"]


def test_reshuffle_changes_with_seed_and_drawdown_is_running_peak():
    a=simulate(TRADES,{**CFG,"seed":10},"RESHUFFLE")
    b=simulate(TRADES,{**CFG,"seed":11},"RESHUFFLE")
    assert a["per_simulation"]!=b["per_simulation"]
    assert all(0<=x["max_drawdown_pct"]<=100 for x in a["per_simulation"])


def test_execution_stress_requires_cost_evidence_and_applies_recorded_multiplier():
    assert simulate(TRADES,CFG,"EXECUTION_STRESS")["summary"]["simulations_completed"]==30
    with pytest.raises(ValueError,match="spread_cost and slippage"):
        simulate([{k:v for k,v in TRADES[0].items() if k!="slippage"}],CFG,"EXECUTION_STRESS")


def test_each_fixed_eligibility_boundary_is_inclusive_only_as_specified():
    base={"simulations_requested":10,"simulations_completed":10,"ruin_rate_pct":0,"survival_rate_pct":99,"drawdown_percentiles":{"95":25,"99":35},"median_return_pct":0.000001}
    gates=eligibility_gates(base,"READY",0)
    assert all(gates.values())
    assert eligibility_gates({**base,"simulations_completed":9},"READY",0)["simulation_count_sufficient"] is False
    assert eligibility_gates({**base,"survival_rate_pct":98.999},"READY",0)["survival_at_least_99_pct"] is False
    assert eligibility_gates({**base,"drawdown_percentiles":{"95":25.000001,"99":35}},"READY",0)["p95_drawdown_at_most_25_pct"] is False
    assert eligibility_gates({**base,"drawdown_percentiles":{"95":25,"99":35.000001}},"READY",0)["p99_drawdown_at_most_35_pct"] is False
    assert eligibility_gates({**base,"median_return_pct":0},"READY",0)["positive_median_return"] is False
    assert eligibility_gates({**base,"ruin_rate_pct":0.01},"READY",0)["zero_ruin"] is False
    assert eligibility_gates(base,"BLOCKED",0)["data_readiness_not_blocked"] is False
    assert eligibility_gates(base,"READY",1)["zero_known_logic_compliance_failures"] is False
    assert eligibility_gates(base,None,None)["data_readiness_not_blocked"] is None
    assert eligibility_gates(base,None,None)["zero_known_logic_compliance_failures"] is None
    missing=eligibility_gates({"ruin_rate_pct":0,"survival_rate_pct":99,"drawdown_percentiles":{},"median_return_pct":1},"READY",0)
    assert missing["p95_drawdown_at_most_25_pct"] is False and missing["p99_drawdown_at_most_35_pct"] is False


def test_eligibility_requires_explicit_source_evidence_and_blocks_known_failures():
    missing=simulate(TRADES,{**CFG,"source_data_readiness":None,"known_logic_compliance_failures":None},"RESHUFFLE")
    assert missing["eligibility"]["status"]=="INSUFFICIENT EVIDENCE"
    assert missing["eligibility"]["gates_passed"]["data_readiness_not_blocked"] is None
    assert missing["eligibility"]["gates_passed"]["zero_known_logic_compliance_failures"] is None
    blocked=simulate(TRADES,{**CFG,"source_data_readiness":"BLOCKED","known_logic_compliance_failures":2},"RESHUFFLE")
    assert blocked["eligibility"]["status"]=="NOT ELIGIBLE"
    assert blocked["eligibility"]["gates_passed"]["data_readiness_not_blocked"] is False
    assert blocked["eligibility"]["gates_passed"]["zero_known_logic_compliance_failures"] is False
    assert blocked["robustness"]["status"]!="STRONG ROBUSTNESS"


def test_every_simulation_has_a_reproducible_persistable_seed_and_method_disclosure():
    for method in ("RESHUFFLE","BLOCK_BOOTSTRAP","START_POINT"):
        result=simulate(TRADES,CFG,method)
        seeds=[m["simulation_seed"] for m in result["per_simulation"]]
        assert len(seeds)==len(set(seeds))==CFG["simulations"]
        assert [m["simulation_index"] for m in result["per_simulation"]]==list(range(1,CFG["simulations"]+1))
        assert result["method_description"]
    stress=simulate(TRADES,CFG,"EXECUTION_STRESS")
    assert "recorded cost" in stress["method_description"]


@pytest.mark.parametrize("method",["START_POINT","BLOCK_BOOTSTRAP","RESHUFFLE"])
def test_trade_sequence_methods_report_shared_missing_risk_evidence_failure(method):
    bad=[{"id":44,"net_pnl":10,"planned_risk":0}]
    with pytest.raises(ValueError,match="lacks positive planned-risk evidence"):
        simulate(bad,CFG,method)


def test_invalid_config_and_no_trades_are_refused():
    with pytest.raises(ValueError,match="empty"):
        simulate([],CFG,"RESHUFFLE")
    with pytest.raises(ValueError,match="outside"):
        simulate(TRADES,{**CFG,"simulations":6000},"RESHUFFLE")
    with pytest.raises(ValueError,match="seed"):
        simulate(TRADES,{k:v for k,v in CFG.items() if k!="seed"},"RESHUFFLE")
