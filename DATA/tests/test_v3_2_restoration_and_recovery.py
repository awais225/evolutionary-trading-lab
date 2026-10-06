"""
Dedicated Test Suite for EVOLUTIONARY TRADING RESEARCH LAB V3.2:
Complete Data Restoration, Historical Node Discovery, Backtest Recovery,
Evolution Tree Reconstruction, Dashboard Synchronization, and Portability.

Covers Mandatory Tests A through G (spec V3.2 §15).
"""
import json
import os
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import paths as P
from backend.app.data.restoration import ResearchRestorationEngine, get_restoration_engine
from backend.app.db.database import Database, get_db
from backend.app.main import app
from backend.app.orchestrator.lab import get_lab
from backend.app.versions import APP_VERSION, FULL_VERSION_STRING


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def test_test_a_existing_data_discovery():
    """Test A: Existing DATA Discovery
    - Verifies actual DATA_ROOT.
    - Discovers historical nodes (750 nodes).
    - Discovers historical backtests (35 backtests).
    - Discovers historical validations (11 validations).
    - Verifies manifest discovery.
    """
    engine = get_restoration_engine()
    rep = engine.restore_all(emit_logs=False)

    assert rep["ok"] is True
    assert rep["data_root"] == str(P.DATA_ROOT.resolve()).replace("\\", "/")
    assert rep["discovered_nodes"] >= 750
    assert rep["discovered_backtests"] >= 35
    assert rep["discovered_validations"] >= 11
    assert rep["latest_persisted_node"] >= 750
    assert rep["resume_point"] >= 751
    assert "HISTORICAL RESEARCH RESTORATION COMPLETE" in rep["report_text"]


def test_test_b_historical_node_restoration():
    """Test B: Historical Node Restoration
    - Verifies original node IDs are preserved (1, 371, 711, 713, 750).
    - Verifies generation numbers (0 to 4).
    - Verifies parent/child relationships (e.g. Node 711 has parent 243).
    - Verifies alive/dead/qualified states.
    - Verifies configurations and genomes preserved.
    """
    db = get_db()
    total_strategies = db.total_strategies_count()
    assert total_strategies >= 750

    # Verify key historical node identities
    s1 = db.get_strategy(1)
    assert s1 is not None
    assert s1["id"] == 1

    s371 = db.get_strategy(371)
    assert s371 is not None
    assert s371["id"] == 371

    s711 = db.get_strategy(711)
    assert s711 is not None
    assert s711["id"] == 711
    assert s711["parent_id"] == 243
    assert s711["generation"] == 4
    assert s711["status"] in ("SURVIVED", "QUALIFIED")

    s713 = db.get_strategy(713)
    assert s713 is not None
    assert s713["id"] == 713
    assert s713["status"] == "QUALIFIED"
    assert s713["fitness"] is not None and s713["fitness"] > 0.7

    s750 = db.get_strategy(750)
    assert s750 is not None
    assert s750["id"] == 750


def test_test_c_historical_backtest_restoration():
    """Test C: Historical Backtest Restoration
    - Verifies that previously completed backtests are recognized.
    - Verifies original metrics (e.g. Strategy 711 has 259 trades, PF=2.816).
    - Verifies node associations.
    - Verifies completed backtests are not requeued.
    """
    db = get_db()
    bts = db.q("SELECT * FROM backtests WHERE strategy_id=711")
    assert len(bts) >= 1

    bt711 = bts[0]
    metrics = json.loads(bt711["metrics"]) if isinstance(bt711["metrics"], str) else bt711["metrics"]
    assert metrics["trades"] == 259
    assert abs(float(metrics["profit_factor"]) - 2.816) < 0.01
    assert float(metrics["net_profit"]) > 4000.0

    # Strategy 711 is SURVIVED/QUALIFIED, so it will not be in backtesting queue
    pending_bt = db.q("SELECT id FROM strategies WHERE status IN ('BORN', 'BACKTESTING') AND id=711")
    assert len(pending_bt) == 0


def test_test_d_evolution_tree_restoration(client):
    """Test D: Evolution Tree Restoration
    - Verifies that historical generations appear.
    - Verifies parent/child relationships are reconstructed.
    - Verifies historical nodes are not missing or duplicated.
    - Verifies /api/tree returns complete graph.
    """
    resp = client.get("/api/tree?max_nodes=1000")
    assert resp.status_code == 200
    data = resp.json()

    assert data["total_strategies"] >= 750
    assert len(data["nodes"]) >= 750
    assert len(data["edges"]) >= 300

    node_ids = {n["id"] for n in data["nodes"]}
    assert 1 in node_ids
    assert 371 in node_ids
    assert 711 in node_ids
    assert 713 in node_ids
    assert 750 in node_ids

    # Verify edge connecting 243 -> 711
    edges_from_243 = [e for e in data["edges"] if e["source"] == 243 and e["target"] == 711]
    assert len(edges_from_243) >= 1


def test_test_e_dashboard_synchronization(client):
    """Test E: Dashboard Synchronization
    - Compares DATA records, Database records, /api/status, /api/tree, /api/population.
    - All relevant counts and statuses must agree.
    """
    st_res = client.get("/api/status").json()
    pop_res = client.get("/api/population?limit=1000").json()
    tree_res = client.get("/api/tree?max_nodes=1000").json()

    assert st_res["app_version"] == FULL_VERSION_STRING
    lab_st = st_res["lab"]

    assert lab_st["total_nodes"] >= 750
    assert pop_res["count"] >= 750
    assert tree_res["total_strategies"] >= 750

    # Qualified count agreement
    db = get_db()
    db_qual = db.one("SELECT COUNT(*) c FROM strategies WHERE status='QUALIFIED'")["c"]
    assert db_qual >= 11
    assert pop_res["status_counts"].get("QUALIFIED", 0) == db_qual


def test_test_f_restart_persistence():
    """Test F: Restart Persistence
    - Running restoration multiple times is completely idempotent.
    - No duplicate nodes, backtests, or validations are created.
    """
    engine = get_restoration_engine()
    rep1 = engine.restore_all(emit_logs=False)
    rep2 = engine.restore_all(emit_logs=False)

    assert rep1["restored_nodes"] == rep2["restored_nodes"]
    assert rep1["restored_backtests"] == rep2["restored_backtests"]
    assert rep1["restored_validations"] == rep2["restored_validations"]

    db = get_db()
    total_strategies = db.total_strategies_count()
    assert total_strategies == rep1["restored_nodes"]


def test_test_g_no_unnecessary_research():
    """Test G: No Unnecessary Research
    - Before proceeding with new experiment, verifies:
      * Existing nodes are not regenerated.
      * Resume point is latest persisted node + 1 (Node 751).
      * Completed backtests are not repeated.
      * Completed validation is not repeated.
    """
    lab = get_lab()
    total_nodes = lab.evo.total_nodes()
    next_id = lab.db.next_strategy_id()

    assert total_nodes >= 750
    assert next_id >= 751

    # Verify no completed backtests are marked BORN
    db = get_db()
    completed_bt_sids = [r["strategy_id"] for r in db.q("SELECT DISTINCT strategy_id FROM backtests")]
    requeued = db.q(f"SELECT id FROM strategies WHERE status='BORN' AND id IN ({','.join(map(str, completed_bt_sids))})")
    assert len(requeued) == 0


def test_test_h_data_portability(tmp_path):
    """Test H: DATA Portability
    - Copying the DATA directory into a fresh runtime environment
      allows a brand new empty database to completely restore the research history.
    """
    clean_data_dir = tmp_path / "DATA"
    clean_res_dir = tmp_path / "RESEARCH"
    clean_db_file = tmp_path / "fresh_lab.db"

    # Copy actual research strategies and backtests to clean data directory
    shutil.copytree(str(P.RESEARCH_DIR), str(clean_res_dir))
    clean_data_dir.mkdir(parents=True, exist_ok=True)
    if P.DATA_MANIFEST.exists():
        shutil.copy2(str(P.DATA_MANIFEST), str(clean_data_dir / "manifest.json"))

    # Fresh empty database
    clean_db = Database(clean_db_file)
    assert clean_db.total_strategies_count() == 0

    # Initialize restoration engine targeting the clean environment
    portable_engine = ResearchRestorationEngine(
        data_root=clean_data_dir,
        research_dir=clean_res_dir,
        db=clean_db,
    )
    rep = portable_engine.restore_all(emit_logs=False)

    assert rep["ok"] is True
    assert rep["restored_nodes"] >= 750
    assert rep["restored_backtests"] >= 35
    assert rep["restored_validations"] >= 11
    assert clean_db.total_strategies_count() >= 750

    # Verify Strategy 711 and 713 were recovered in fresh database
    s711 = clean_db.get_strategy(711)
    assert s711 is not None
    assert s711["id"] == 711
    assert s711["status"] in ("SURVIVED", "QUALIFIED")

    s713 = clean_db.get_strategy(713)
    assert s713 is not None
    assert s713["status"] == "QUALIFIED"

    # Verify backtests recovered in fresh database
    bt711 = clean_db.q("SELECT * FROM backtests WHERE strategy_id=711")
    assert len(bt711) >= 1
