from app.execution_evidence_verifiers import (
    verify_decision_outcomes, verify_entry_conditions, verify_no_lookahead,
    verify_position_sizing, verify_reference_levels,
)


def test_independent_entry_verifier_detects_corrupt_condition_result():
    spec = {"entry": {"type": "and", "children": [{"type": "compare", "op": "gt",
        "left": {"kind": "candle", "field": "close", "offset": 0},
        "right": {"kind": "candle", "field": "open", "offset": 0}}]}}
    candle = {"ts": 100, "open": 1, "high": 3, "low": 0, "close": 2}
    event = {"event": "CONDITION_EVALUATED", "event_id": "e1", "candle_ts": 100, "result": True}
    assert verify_entry_conditions(spec, [event], [candle])["verdict"] == "PASS"
    result = verify_entry_conditions(spec, [{**event, "result": False}], [candle])
    assert result["verdict"] == "FAIL"
    assert result["mismatches"]


def test_decision_reconciliation_detects_missing_duplicate_and_bad_trade_links():
    sig = {"event": "SIGNAL_DETECTED", "signal_id": "s1"}
    trade = {"id": "t1"}
    ok = {"event": "TRADE_EXECUTED", "signal_id": "s1", "trade_id": "t1"}
    assert verify_decision_outcomes([sig, ok], [trade])["verdict"] == "PASS"
    assert verify_decision_outcomes([sig], [trade])["missing_outcomes"] == ["s1"]
    dup = verify_decision_outcomes([sig, ok, ok], [trade])
    assert dup["duplicate_outcomes"] == ["s1"]
    broken = verify_decision_outcomes([sig, {**ok, "trade_id": "absent"}], [trade])
    assert broken["bad_trade_links"] == ["absent"]


def test_no_lookahead_rejects_future_input_and_missing_refs_are_unverified():
    ev = {"event": "CONDITION_EVALUATED", "event_id": "e", "evaluation_ts": 100,
          "max_permitted_input_ts": 100, "actual_max_input_ts": 101}
    assert verify_no_lookahead([ev])["verdict"] == "FAIL"
    assert verify_no_lookahead([{"event": "CONDITION_EVALUATED"}])["verdict"] == "UNVERIFIED"


def test_position_sizing_verifier_recalculates_and_detects_mismatch():
    calc = {"equity_before_entry": 10000, "initial_capital": 10000, "risk_mode": "percent", "risk_value": 1,
            "risk_basis": "current_equity", "tick_size": 0.1, "tick_value_used": 1,
            "volume_step": 0.01, "volume_min": 0.01, "volume_max": 100,
            "stop_distance": 2, "volume": 5, "planned_risk": 100, "budget": 100,
            "loss_per_lot": 20}
    assert verify_position_sizing([{"id": "t", "volume": 5, "sizing_calculation": calc}])["verdict"] == "PASS"
    broken = {"id": "t", "volume": 4, "sizing_calculation": calc}
    assert verify_position_sizing([broken])["verdict"] == "FAIL"
    missing = {"id": "t", "volume": 5, "sizing_calculation": {}}
    assert verify_position_sizing([missing])["verdict"] == "UNVERIFIED"


def test_reference_verifier_does_not_promote_missing_evidence():
    assert verify_reference_levels([], [], True)["verdict"] == "UNVERIFIED"
    assert verify_reference_levels([], [], False)["verdict"] == "NOT_APPLICABLE"
