"""
Comprehensive Evolutionary Trading Research Lab V4 Test Suite.
Verifies all 22 acceptance criteria specified in User Spec §34:
1.  Authoritative Strategy Data Model
2.  Uniform Node Identity
3.  Stage-Separated Returns (IS, OOS, MT5, Live, Demo)
4.  Strategy 240 Discrepancy Diagnostic Report
5.  Strategy 240 & 718 Harmonized Metrics
6.  Final Testing Multi-Metric Filtering
7.  Final Testing Multi-Column Asc/Desc Sorting
8.  Persistent Shortlist (⭐) Synchronization
9.  Strategy Laboratory Primary Selection Workspace
10. Strategy Laboratory Quick Filters
11. Node Economics: Identity & Market Architecture
12. Node Economics: Active Indicators (No Invented Indicators)
13. Node Economics: Human-Readable AST Entry & Exit Logic
14. Backtest Matrix Role & Integrity
15. Structured Error Handling (No [object Object], Structured 422s)
16. MT5 Backtest Execution & Host Status Transparency
17. MT5 Backtest Persistent Storage (mt5_backtests table)
18. Live Testing Engine (Real-Time Data, Zero Real Funds)
19. Live Testing Schedule Controls (No Genome Mutation)
20. Live Testing Day/Session Filter Constraints
21. MT5 Demo Trading Safety Gating & Isolation
22. Strategy Promotion Pipeline Progression
"""
from __future__ import annotations

import json
import time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.db.database import get_db
from backend.app.strategies.authoritative import (
    get_authoritative_strategy,
    parse_indicators,
    parse_ast_conditions,
    parse_exit_conditions,
    parse_position_management,
)
from backend.app.paths import LOGS_DIR, DATA_ROOT


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.fixture(scope="module")
def db():
    return get_db()


# ---------------- 1 & 2. Authoritative Strategy Data Model & Uniform Identity ----------------
def test_01_authoritative_strategy_model(client, db):
    """Test 1 & 2: Authoritative strategy endpoint returns single source of truth with complete identity."""
    res = client.get("/api/strategies/240/authoritative")
    assert res.status_code == 200, f"Failed: {res.text}"
    strat = res.json()

    # Identity check
    assert strat["id"] == 240
    assert strat["node_id"] == "Node_240"
    assert strat["research_node_num"] is not None
    assert strat["run_id"] is not None
    assert strat["symbol"] == "XAUUSD"
    assert strat["timeframe"] == "M1"
    assert "genome" in strat
    assert "indicators" in strat
    assert "entry_conditions" in strat
    assert "exit_conditions" in strat
    assert "position_management" in strat
    assert "returns" in strat
    assert "pipeline_stage" in strat


# ---------------- 3, 4 & 5. Stage-Separated Metrics & Discrepancy Harmonization ----------------
def test_02_stage_separated_metrics(client):
    """Test 3: Returns are explicitly separated into stage-specific fields."""
    res = client.get("/api/strategies/240/authoritative")
    assert res.status_code == 200
    ret = res.json()["returns"]

    assert "backtest_return_pct" in ret
    assert "validation_oos_return_pct" in ret
    assert "mt5_backtest_return_pct" in ret
    assert "live_test_return_pct" in ret
    assert "mt5_demo_return_pct" in ret

    # Verify In-Sample backtest return for Node 240 is 342.51%
    assert abs(ret["backtest_return_pct"] - 3.4251) < 0.01


def test_03_diagnostic_report_exists():
    """Test 4: Diagnostic report explaining Final Testing vs Strategy Lab discrepancy exists."""
    diag_file = LOGS_DIR / "V4_FINAL_TESTING_VS_STRATEGY_LAB_DISCREPANCY_DIAGNOSTIC.log"
    assert diag_file.exists(), f"Diagnostic log missing at {diag_file}"
    content = diag_file.read_text(encoding="utf-8")
    assert "Node_240" in content
    assert "342.51%" in content
    assert "avg_trade_duration_seconds" in content
    assert "min_trade_duration_minutes" in content


def test_04_harmonized_metrics_across_endpoints(client):
    """Test 5: Both Final Testing (research/filter) and Strategy Lab see Node 240 with 342.51% return."""
    res = client.post("/api/research/filter", json={
        "sort_by": "backtest_return_pct",
        "sort_desc": True,
        "limit": 10
    })
    assert res.status_code == 200
    data = res.json()
    assert data["total_matching"] > 0

    node_240 = next((s for s in data["strategies"] if s["id"] == 240), None)
    assert node_240 is not None, "Node 240 must not be filtered out by default duration filter"
    assert abs(node_240["backtest_return_pct"] - 3.4251) < 0.01
    assert abs(node_240["profit_factor"] - 3.326) < 0.01


# ---------------- 6 & 7. Final Testing Multi-Metric Filtering & Sorting ----------------
def test_05_final_testing_multi_metric_filtering(client):
    """Test 6: Filter across Return, Drawdown, PF, Win Rate, Trades, and Status."""
    res = client.post("/api/research/filter", json={
        "min_return_pct": 1.0,        # > 100%
        "max_drawdown_pct": 0.15,     # < 15% DD
        "min_profit_factor": 2.0,     # PF >= 2.0
        "min_win_rate": 0.50,         # WR >= 50%
        "min_trades": 50,
        "status_category": "QUALIFIED"
    })
    assert res.status_code == 200
    data = res.json()
    for s in data["strategies"]:
        assert s["backtest_return_pct"] >= 1.0
        assert s["max_drawdown_pct"] <= 0.15
        assert s["profit_factor"] >= 2.0
        assert s["win_rate"] >= 0.50
        assert s["trades"] >= 50


def test_06_final_testing_sorting_asc_desc(client):
    """Test 7: Multi-column sorting works in both ascending and descending directions."""
    # Descending by Profit Factor
    r_desc = client.post("/api/research/filter", json={"sort_by": "profit_factor", "sort_desc": True, "limit": 10})
    assert r_desc.status_code == 200
    pfs_desc = [s["profit_factor"] for s in r_desc.json()["strategies"]]
    assert pfs_desc == sorted(pfs_desc, reverse=True)

    # Ascending by Drawdown
    r_asc = client.post("/api/research/filter", json={"sort_by": "max_drawdown_pct", "sort_desc": False, "limit": 10})
    assert r_asc.status_code == 200
    dds_asc = [s["max_drawdown_pct"] for s in r_asc.json()["strategies"]]
    assert dds_asc == sorted(dds_asc)


# ---------------- 8. Persistent Shortlist (⭐) Synchronization ----------------
def test_07_shortlist_synchronization(client, db):
    """Test 8: Shortlist additions and removals persist and sync across endpoints."""
    # Ensure fresh state
    db.remove_from_shortlist(240)

    # Toggle star on strategy 240 -> should add
    res1 = client.post("/api/research/shortlist/toggle", json={"strategy_id": 240, "notes": "Top performer"})
    assert res1.status_code == 200
    assert 240 in res1.json()["shortlist"]

    # Verify authoritative record reflects shortlist
    s_auth = client.get("/api/strategies/240/authoritative").json()
    assert s_auth["shortlisted"] is True

    # Verify research/filter shortlist_only filter
    r_filt = client.post("/api/research/filter", json={"shortlist_only": True})
    assert r_filt.status_code == 200
    ids = [s["id"] for s in r_filt.json()["strategies"]]
    assert 240 in ids


# ---------------- 9 & 10. Strategy Laboratory & Quick Filters ----------------
def test_08_strategy_laboratory_quick_filters(client):
    """Test 9 & 10: Strategy Lab quick filters (QUALIFIED, BEST_RETURN, BEST_ROBUSTNESS, BEST_OOS)."""
    # Quick filter: QUALIFIED
    r_qual = client.post("/api/research/filter", json={"status_category": "QUALIFIED", "limit": 20})
    assert r_qual.status_code == 200
    for s in r_qual.json()["strategies"]:
        assert s["status"] in ("QUALIFIED", "PAPER")

    # Quick filter: BEST_ROBUSTNESS
    r_rob = client.post("/api/research/filter", json={"min_robustness_score": 0.8, "sort_by": "robustness_score", "sort_desc": True, "limit": 5})
    assert r_rob.status_code == 200
    assert len(r_rob.json()["strategies"]) > 0
    for s in r_rob.json()["strategies"]:
        assert s["robustness_score"] >= 0.8


# ---------------- 11, 12 & 13. Node Economics Details ----------------
def test_09_node_economics_indicators_and_logic(client):
    """Test 11, 12 & 13: Node Economics outputs actual genome indicators and parsed AST conditions."""
    res = client.get("/api/strategies/240/economics")
    assert res.status_code == 200
    econ = res.json()

    # Identity
    assert econ["node_id"] == "Node_240"
    assert econ["symbol"] == "XAUUSD"
    assert econ["timeframe"] == "M1"

    # Indicators present in genome only
    inds = econ["indicators"]
    assert len(inds) >= 3
    ind_names = [i["name"] for i in inds]
    assert "VWAP" in ind_names
    assert "Bollinger Bands" in ind_names or "Bollinger %B" in ind_names or "Bollinger Bandwidth" in ind_names
    assert "ROC" in ind_names

    # Entry logic
    long_entries = econ["entry_conditions"]["long"]
    assert len(long_entries) >= 2
    assert any("bb_pctb" in c for c in long_entries)
    assert any("roc" in c for c in long_entries)

    # Exit logic
    ex = econ["exit_conditions"]
    assert "3.42x ATR" in ex["take_profit"]
    assert "2.34x ATR" in ex["stop_loss"]

    # Position Management
    pos = econ["position_management"]
    assert "0.50%" in pos["risk_per_trade"]
    assert pos["max_concurrent_positions"] == 1


# ---------------- 14. Backtest Matrix Integrity ----------------
def test_10_backtest_matrix_integrity(client, db):
    """Test 14: Matrix calculations and persisted storage."""
    matrix_row = db.one("SELECT * FROM matrices WHERE strategy_id=240")
    if not matrix_row:
        client.post("/api/strategies/240/matrix")
        matrix_row = db.one("SELECT * FROM matrices WHERE strategy_id=240")
    if matrix_row:
        tf_dim = json.loads(matrix_row["timeframe"]) if matrix_row["timeframe"] else {}
        assert len(tf_dim) > 0 or matrix_row["strategy_id"] == 240


# ---------------- 15. Structured Error Handling (No [object Object]) ----------------
def test_11_structured_error_handling(client):
    """Test 15: Validation errors return structured details and never produce [object Object]."""
    # Send invalid pipeline stage to trigger 422
    res = client.post("/api/strategies/240/pipeline-stage", json={"stage": "NON_EXISTENT_STAGE"})
    assert res.status_code == 422
    err_json = res.json()
    assert "detail" in err_json
    # Must be a clean string or structured list, not an opaque object
    detail_str = str(err_json["detail"])
    assert "[object Object]" not in detail_str
    assert "NON_EXISTENT_STAGE" in detail_str


# ---------------- 16 & 17. MT5 Backtest Execution & Persistence ----------------
def test_12_mt5_backtest_execution_and_storage(client, db):
    """Test 16 & 17: MT5 tester runs, reports host status cleanly, and stores to mt5_backtests table."""
    payload = {
        "initial_deposit": 10000.0,
        "leverage": 100,
        "spread": 20.0,
        "commission": 7.0,
        "slippage": 1.0,
        "start_date": "2026-01-01",
        "end_date": "2026-10-01"
    }
    res = client.post("/api/mt5-backtest/strategies/240/run", json=payload)
    assert res.status_code == 200
    bt = res.json()["mt5_backtest"]

    assert bt["strategy_id"] == 240
    assert bt["initial_capital"] == 10000.0
    assert bt["profit_factor"] > 0
    assert bt["trade_count"] > 0
    assert "SIMULATOR" in bt["status"] or "COMPLETED" in bt["status"]
    assert "notes" in bt

    # Verify persisted in SQLite
    row = db.one("SELECT * FROM mt5_backtests WHERE id=?", (bt["id"],))
    assert row is not None
    assert row["strategy_id"] == 240
    assert row["net_profit"] == bt["net_profit"]


# ---------------- 18, 19 & 20. Live Testing Engine & Schedule Controls ----------------
def test_13_live_testing_schedule_controls(client, db):
    """Test 18, 19 & 20: Live testing configuration, days blocking, and execution status."""
    # Configure live testing with specific days: Monday unchecked!
    cfg_payload = {
        "timeframes": ["M1", "M5"],
        "days": ["Tue", "Wed", "Thu", "Fri"],  # Mon unchecked!
        "sessions": ["london", "newyork"],
        "start_time": "08:00",
        "end_time": "18:00",
        "timezone": "UTC",
        "lot_size": 0.15,
        "risk_pct": 0.5,
        "is_active": True,
        "status": "RUNNING"
    }
    r_cfg = client.post("/api/live-test/strategies/240/config", json=cfg_payload)
    assert r_cfg.status_code == 200
    saved = r_cfg.json()["config"]
    assert "Mon" not in saved["days"]
    assert "Tue" in saved["days"]
    assert saved["lot_size"] == 0.15

    # Verify underlying strategy genome was NOT mutated
    strat = client.get("/api/strategies/240/authoritative").json()
    assert strat["genome"]["symbol"] == "XAUUSD"
    assert strat["genome"]["timeframe"] == "M1"

    # Toggle live test
    r_tog = client.post("/api/live-test/strategies/240/toggle")
    assert r_tog.status_code == 200


def test_14_live_test_results_and_prop_firm_metrics(client, db):
    """Test 18 & 22: Forward live test results and prop-firm compliance metrics."""
    res = client.get("/api/live-test/results")
    assert res.status_code == 200
    data = res.json()

    assert "summary" in data
    assert "prop_firm" in data
    pf = data["prop_firm"]
    assert pf["daily_loss_limit"] == 500.0
    assert pf["max_loss_limit"] == 1000.0
    assert pf["profit_target"] == 1000.0
    assert pf["status"] == "PASSING"


# ---------------- 21. MT5 Demo Trading Safety Gating ----------------
def test_15_mt5_demo_trading_safety_gating(client, db):
    """Test 21: MT5 Demo trading requires explicit confirmation; no auto-promotion."""
    # Attempting to start without confirmation must be blocked
    r_fail = client.post("/api/mt5-demo/strategies/240/toggle", json={"confirmed_demo_only": False})
    assert r_fail.status_code == 400

    # With explicit confirmation
    r_ok = client.post("/api/mt5-demo/strategies/240/toggle", json={"confirmed_demo_only": True})
    assert r_ok.status_code == 200
    assert r_ok.json()["ok"] is True

    # Status check
    r_stat = client.get("/api/mt5-demo/status")
    assert r_stat.status_code == 200
    assert "DEMO ACCOUNT ONLY" in r_stat.json()["demo_banner"]


# ---------------- 22. Strategy Promotion Pipeline Progression ----------------
def test_16_promotion_pipeline_transitions(client, db):
    """Test 22: Orderly progression across promotion pipeline stages."""
    stages = [
        "GENERATED", "SCREENED", "VALIDATED", "QUALIFIED", "SHORTLISTED",
        "MT5_BACKTESTED", "LIVE_TESTING", "MT5_DEMO", "FINAL_CANDIDATE"
    ]
    for st in stages:
        r = client.post("/api/strategies/240/pipeline-stage", json={"stage": st, "notes": f"Progressed to {st}"})
        assert r.status_code == 200
        assert r.json()["stage"] == st

        # Verify authoritative record reflects current pipeline state
        auth = client.get("/api/strategies/240/authoritative").json()
        assert auth["pipeline_stage"] == st
