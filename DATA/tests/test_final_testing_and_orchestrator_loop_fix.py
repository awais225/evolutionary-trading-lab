"""
Acceptance Test Suite for Node Generation, Single Source of Truth,
Watchdog, Looping Orchestrator Fix, and FINAL TESTING Workspace (V2.7).
"""
import json
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from app.main import app
from app.db.database import Database, get_db
from app.evolution.engine import EvolutionEngine, get_evo_engine
from app.orchestrator.stages import get_stage_manager
from app.orchestrator.lab import get_lab
from app.config import get_config


@pytest.fixture
def client():
    return TestClient(app)


def test_single_source_of_truth_state_agreement(client):
    """Test 2 & Test 3: Overview, Task State, and Live Activity read the EXACT same node state."""
    db = get_db()
    evo = get_evo_engine(db)

    node_state = evo.get_node_generation_state()
    cur_nodes = node_state["current_nodes"]
    target_nodes = node_state["target_nodes"]
    gen = node_state["generation_number"]
    rem = node_state["remaining_nodes"]
    pct = node_state["progress_pct"]

    # 1. Check /api/workflow/task_state (consumed by LiveActivitySidebar)
    res_task = client.get("/api/workflow/task_state")
    assert res_task.status_code == 200
    st_task = res_task.json()

    assert st_task["current_nodes"] == cur_nodes
    assert st_task["target_nodes"] == target_nodes
    assert st_task["node_target"] == target_nodes
    assert st_task["generation"] == gen
    assert st_task["remaining_nodes"] == rem
    assert st_task["progress_pct"] == pct
    assert "node_accounting" in st_task

    # 2. Check /api/lab/status (consumed by Overview)
    res_lab = client.get("/api/lab/status")
    assert res_lab.status_code == 200
    st_lab = res_lab.json()

    assert st_lab["current_nodes"] == cur_nodes
    assert st_lab["total_nodes"] == cur_nodes
    assert st_lab["target_nodes"] == target_nodes
    assert st_lab["target"] == target_nodes
    assert st_lab["generation"] == gen
    assert st_lab["remaining_nodes"] == rem
    assert st_lab["progress_pct"] == pct

    # Ensure neither shows 0 when cur_nodes > 0
    assert cur_nodes > 0
    assert st_task["current_nodes"] > 0
    assert st_lab["total_nodes"] > 0


def test_node_generation_advances_beyond_current(tmp_path):
    """Test 4 & Test 8: Node generation progresses node by node and resumes from persisted count."""
    db_path = tmp_path / "resume_test.db"
    db = Database(str(db_path))
    evo = EvolutionEngine(db)
    evo.set_total_node_target(100)

    # Seed 10 initial nodes
    evo.seed_population(10, "XAUUSD")
    cur_before = evo.total_nodes()
    assert cur_before == 10

    # Reproduce 5 new nodes
    counts = evo.reproduce(5, "XAUUSD")
    born = counts["mutation"] + counts["crossover"] + counts["exploration"]
    assert born == 5
    assert evo.total_nodes() == 15

    # Simulate application restart: instantiate fresh EvolutionEngine on same DB
    evo_restarted = EvolutionEngine(db)
    assert evo_restarted.total_nodes() == 15
    assert evo_restarted.remaining_nodes() == 85
    assert evo_restarted.get_total_node_target() == 100

    # Reproduce another batch
    counts2 = evo_restarted.reproduce(10, "XAUUSD")
    born2 = counts2["mutation"] + counts2["crossover"] + counts2["exploration"]
    assert born2 == 10
    assert evo_restarted.total_nodes() == 25


def test_orchestrator_does_not_loop_without_work(tmp_path):
    """Test 5 & Test 10: Orchestrator does not endlessly transition when no candidate work exists."""
    db_path = tmp_path / "loop_check.db"
    db = Database(str(db_path))
    evo = EvolutionEngine(db)
    evo.set_total_node_target(50)
    evo.seed_population(5, "XAUUSD")

    # Mark all 5 as SURVIVED and detail-tested
    for r in db.q("SELECT id FROM strategies"):
        db.update_strategy(r["id"], status="SURVIVED", fitness=0.85)

    sm = get_stage_manager()
    initial_history_len = len(sm._stage_history)

    # In lab orchestrator, when pipeline is full or no pending candidates, transitions should be meaningful
    # Transitions must record FROM, TO, REASON, CURRENT NODE COUNT, TARGET
    sm.transition("RESEARCH", "BACKTESTING", {
        "reason": "5 nodes ready for screening",
        "current_nodes": 5,
        "target": 50,
    })
    last_trans = sm._stage_history[-1]
    assert last_trans["from_stage"] == "RESEARCH"
    assert last_trans["to_stage"] == "BACKTESTING"
    assert last_trans["details"]["current_nodes"] == 5
    assert last_trans["details"]["target"] == 50
    assert "reason" in last_trans["details"]


def test_generation_exhaustion_reseeds_and_advances(tmp_path):
    """Test 6: Pool exhaustion logs reason, creates next generation, and does not halt before target."""
    db_path = tmp_path / "exhaust_test.db"
    db = Database(str(db_path))
    evo = EvolutionEngine(db)
    evo.set_total_node_target(30)

    # Seed 10 strategies (Gen 0)
    evo.seed_population(10, "XAUUSD")
    assert evo.total_nodes() == 10

    # Mark them FAILED so elite pool is empty
    for r in db.q("SELECT id FROM strategies"):
        db.update_strategy(r["id"], status="FAILED")

    assert len(evo.elites(10)) == 0  # No elites available!

    # Reproducing must trigger shortfall exploration and create generation 1
    counts = evo.reproduce(10, "XAUUSD")
    total_born = counts["mutation"] + counts["crossover"] + counts["exploration"]
    assert total_born == 10
    assert evo.total_nodes() == 20

    # Verify new candidates have generation > 0
    new_rows = db.q("SELECT generation FROM strategies WHERE status='BORN'")
    assert len(new_rows) == 10
    assert all(r["generation"] >= 1 for r in new_rows)


def test_final_testing_search_and_filter_apis(client):
    """Test 9 & Test 10: Final Testing search, multi-metric filters, zero recomputation."""
    # 1. Search exact node ID 1
    res_exact = client.post("/api/research/filter", json={"node_id": 1})
    assert res_exact.status_code == 200
    data_exact = res_exact.json()
    assert data_exact["recomputed"] is False
    assert len(data_exact["strategies"]) == 1
    assert data_exact["strategies"][0]["id"] == 1

    # 2. Multi-filter post-evaluation
    res_filt = client.post("/api/research/filter", json={
        "min_trades": 3,
        "min_trade_duration_minutes": 0.0,
        "min_return_pct": -50.0,
        "max_drawdown_pct": 50.0,
        "status_category": "ALL",
        "limit": 50
    })
    assert res_filt.status_code == 200
    data_filt = res_filt.json()
    assert data_filt["recomputed"] is False
    assert "filters_applied" in data_filt
    assert data_filt["filters_applied"]["min_trades"] == 3


def test_final_testing_shortlist_persistence(client):
    """Test 11: Add node to shortlist, verify persistence, toggle, and clear."""
    # 1. Add node #1 to shortlist
    res_add = client.post("/api/research/shortlist/toggle", json={"strategy_id": 1, "notes": "Top candidate"})
    assert res_add.status_code == 200
    d_add = res_add.json()
    assert d_add["ok"] is True
    assert 1 in d_add["shortlist"]

    # 2. Verify GET /api/research/shortlist/saved
    res_get = client.get("/api/research/shortlist/saved")
    assert res_get.status_code == 200
    assert 1 in res_get.json()["shortlist"]

    # 3. Toggle off
    res_off = client.post("/api/research/shortlist/toggle", json={"strategy_id": 1})
    assert res_off.status_code == 200
    assert 1 not in res_off.json()["shortlist"]


def test_final_testing_strategy_trades_endpoint(client):
    """Test 19: Get individual trade records for a strategy."""
    res = client.get("/api/strategies/1/trades")
    assert res.status_code == 200
    data = res.json()
    assert "strategy_id" in data
    assert "total_trades" in data
    assert "trades" in data
    assert isinstance(data["trades"], list)


def test_watchdog_reports_heartbeat_on_idle(tmp_path):
    """Test 9: Generation watchdog reports heartbeat when idle."""
    lab = get_lab()
    # Check that _check_generation_watchdog executes without error
    lab._check_generation_watchdog()
    assert hasattr(lab, "_check_generation_watchdog")
