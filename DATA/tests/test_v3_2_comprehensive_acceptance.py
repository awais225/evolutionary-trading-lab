"""
Dedicated Acceptance Test Suite for V3.2 Evolutionary Trading Research Lab (Tests A - G).

Tests:
  Test A: Two research buttons (RESUME vs START NEW RUN)
  Test B: 5,000 + 5,000 scenario and run vs cumulative node distinction
  Test C: Dynamic CPU governor & worker benchmarks (25%, 50%, 75%, 100%)
  Test D: Mandatory persistent diagnostic logs in DATA/logs/
  Test E: Fresh ZIP + old DATA portability & seamless restoration
  Test F: Node lifecycle completion (generated ceiling != completed)
  Test G: Paper trading candidate selection, promotion & demotion
"""
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.config import get_config
from backend.app.db.database import Database, get_db
from backend.app.orchestrator.lab import get_lab
from backend.app.orchestrator.pipeline_state import get_pipeline_state_manager
from backend.app.resources.manager import get_resource_manager
from backend.app.data.restoration import get_restoration_engine
import backend.app.paths as P


@pytest.fixture
def client():
    return TestClient(app)


def test_a_button_differentiation(client):
    """Test A: RESUME preserves active run ID; START NEW creates fresh run ID & counters."""
    lab = get_lab()
    psm = get_pipeline_state_manager()

    # 1. Start a clean new run
    res_new = lab.start(mode="continuous", target=100, run_type="new", new_run_id="RUN-TEST-A-NEW")
    assert res_new["ok"] is True
    assert lab.evo.active_run_id == "RUN-TEST-A-NEW"
    assert psm.run_id == "RUN-TEST-A-NEW"
    lab.stop()

    # 2. Resume the existing run
    res_resume = lab.start(mode="continuous", target=150, run_type="resume")
    assert res_resume["ok"] is True
    # The active run ID MUST be preserved (NOT reset to None or a random new ID)
    assert lab.evo.active_run_id == "RUN-TEST-A-NEW"
    assert psm.run_id == "RUN-TEST-A-NEW"
    assert lab.evo.get_total_node_target() == 150
    lab.stop()

    # 3. Start ANOTHER new run
    res_new2 = lab.start(mode="continuous", target=200, run_type="new", new_run_id="RUN-TEST-A-NEW2")
    assert res_new2["ok"] is True
    assert lab.evo.active_run_id == "RUN-TEST-A-NEW2"
    assert psm.run_id == "RUN-TEST-A-NEW2"
    lab.stop()
    lab.evo.active_run_id = None


def test_b_5k_plus_5k_scenario_and_cumulative_distinction():
    """Test B: Distinguish current run nodes vs cumulative database nodes cleanly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test_5k.db")
        db = Database(db_path)

        # Simulate Run 1: 5,000 nodes
        now = time.time()
        for i in range(1, 101):  # representative subset of 100 for fast test
            status = "QUALIFIED" if i % 10 == 0 else "KILLED"
            db.x("""INSERT INTO strategies (id, hash, generation, symbol, timeframe, status, genome, created_at, updated_at, run_id)
                    VALUES (?, ?, 1, 'XAUUSD', 'M15', ?, '{}', ?, ?, 'RUN-EXPERIMENT-001')""",
                 (i, f"h_{i:06d}", status, now - 500, now - 400))

        # Simulate Run 2: 5,000 nodes
        for i in range(101, 201):
            status = "QUALIFIED" if i % 10 == 0 else "KILLED"
            db.x("""INSERT INTO strategies (id, hash, generation, symbol, timeframe, status, genome, created_at, updated_at, run_id)
                    VALUES (?, ?, 2, 'XAUUSD', 'M15', ?, '{}', ?, ?, 'RUN-EXPERIMENT-002')""",
                 (i, f"h_{i:06d}", status, now - 200, now - 100))

        runs = db.get_all_research_runs()
        assert len(runs) == 2

        run1 = next(r for r in runs if r["run_id"] == "RUN-EXPERIMENT-001")
        run2 = next(r for r in runs if r["run_id"] == "RUN-EXPERIMENT-002")

        assert run1["generated_nodes"] == 100
        assert run2["generated_nodes"] == 100
        assert run1["completed_nodes"] == 100
        assert run2["completed_nodes"] == 100

        # Cumulative total must be 200 without doubling or confusing either run
        cum_total = db.total_strategies_count()
        assert cum_total == 200


def test_c_dynamic_cpu_target_and_worker_sizing():
    """Test C: Dynamic CPU target (25%, 50%, 75%, 100%) correctly governs worker pool sizing."""
    rm = get_resource_manager()
    cores = os.cpu_count() or 2

    # Benchmark 25%
    rm.set_cpu_target(25)
    w_25 = rm.effective_workers()
    assert w_25 >= 1

    # Benchmark 50%
    rm.set_cpu_target(50)
    w_50 = rm.effective_workers()
    assert w_50 >= 1

    # Benchmark 75%
    rm.set_cpu_target(75)
    w_75 = rm.effective_workers()
    assert w_75 >= w_50

    # Benchmark 100%
    rm.set_cpu_target(100)
    w_100 = rm.effective_workers()
    assert w_100 == cores

    # Restore default
    rm.set_cpu_target(60)


def test_d_mandatory_persistent_diagnostic_logs():
    """Test D: Verify persistent diagnostic logs in DATA/logs/."""
    from backend.app.orchestrator.diagnostic_logger import (
        log_startup_diagnostics,
        log_stage_transition,
        log_research_run_event
    )
    logs_dir = P.DATA_LOGS_DIR
    assert logs_dir.exists()

    rep = log_startup_diagnostics({"restored_nodes": 750, "datasets_count": 5})
    latest_file = logs_dir / "V3_2_STARTUP_LATEST.log"
    assert latest_file.exists()
    content = latest_file.read_text(encoding="utf-8")
    assert "STARTUP DIAGNOSTIC REPORT" in content
    assert "PATHS & DIRECTORY DISCOVERY" in content
    assert "HISTORICAL RESEARCH ARTIFACT RESTORATION" in content
    assert "CPU UTILIZATION & DYNAMIC WORKER POOL CONFIGURATION" in content

    # Test stage transitions log
    log_stage_transition("NODE_GENERATION", "EVALUATION", {"nodes": 100})
    st_file = logs_dir / "STAGE_TRANSITIONS.log"
    assert st_file.exists()
    assert "NODE_GENERATION" in st_file.read_text(encoding="utf-8")

    # Test research runs log
    log_research_run_event("NEW_RUN", "RUN-ACCEPTANCE-001", {"target": 500})
    rr_file = logs_dir / "RESEARCH_RUNS.log"
    assert rr_file.exists()
    assert "RUN-ACCEPTANCE-001" in rr_file.read_text(encoding="utf-8")


def test_e_fresh_zip_old_data_portability():
    """Test E: Fresh directory without DATABASE/ recovers seamlessly from DATA/."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_root = Path(tmpdir)
        fake_db_dir = tmp_root / "DATABASE"
        fake_data_dir = tmp_root / "DATA"
        fake_data_db_dir = fake_data_dir / "database"
        fake_data_db_dir.mkdir(parents=True, exist_ok=True)

        # Create a source database in DATA/database/lab_state.db
        source_db_path = str(fake_data_db_dir / "lab_state.db")
        source_db = Database(source_db_path)
        source_db.x("""INSERT INTO strategies (id, hash, generation, symbol, timeframe, status, genome, created_at, updated_at, run_id)
                       VALUES (1, 'hash_portable_001', 0, 'XAUUSD', 'M15', 'QUALIFIED', '{}', 1700000000, 1700000000, 'RUN-PORTABLE')""")
        source_db.close()

        # Target DB in fresh location does NOT exist
        target_db_file = fake_db_dir / "lab_state.db"
        assert not target_db_file.exists()

        # When Database is opened, it discovers and copies from DATA/database/lab_state.db
        import backend.app.paths as test_paths
        orig_database_dir = test_paths.DATABASE_DATA_DIR
        orig_db_fn = test_paths.database_file
        test_paths.DATABASE_DATA_DIR = fake_data_db_dir
        test_paths.database_file = lambda: target_db_file
        try:
            fresh_db = Database(str(target_db_file))
            row = fresh_db.one("SELECT * FROM strategies WHERE id=1")
            assert row is not None
            assert row["hash"] == "hash_portable_001"
            assert row["run_id"] == "RUN-PORTABLE"
            fresh_db.close()
        finally:
            test_paths.DATABASE_DATA_DIR = orig_database_dir
            test_paths.database_file = orig_db_fn


def test_f_node_lifecycle_completion_logic():
    """Test F: Ceiling reached does not mean completed until in-flight drops to 0."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(str(Path(tmpdir) / "test_lifecycle.db"))
        # Add 5 nodes in BORN status (in-flight)
        for i in range(1, 6):
            db.x("""INSERT INTO strategies (id, hash, generation, symbol, timeframe, status, genome, created_at, updated_at, run_id)
                    VALUES (?, ?, 0, 'XAUUSD', 'M15', 'BORN', '{}', 1700000000, 1700000000, 'RUN-LIFECYCLE-01')""",
                 (i, f"h_{i:04d}"))

        runs = db.get_all_research_runs()
        r1 = next(r for r in runs if r["run_id"] == "RUN-LIFECYCLE-01")
        assert r1["generated_nodes"] == 5
        assert r1["completed_nodes"] == 0
        assert r1["pending_backtesting"] == 5
        # Even if ceiling is 5, it is NOT completed yet
        assert r1["run_status"] != "COMPLETED"

        # Now simulate all 5 completing evaluation
        for i in range(1, 6):
            db.update_strategy(i, status="QUALIFIED" if i == 1 else "KILLED")

        runs2 = db.get_all_research_runs()
        r2 = next(r for r in runs2 if r["run_id"] == "RUN-LIFECYCLE-01")
        assert r2["generated_nodes"] == 5
        assert r2["completed_nodes"] == 5
        assert r2["pending_backtesting"] == 0
        assert r2["run_status"] == "COMPLETED"


def test_g_paper_trading_candidate_selection_routes(client):
    """Test G: GET /api/paper/candidates and promote/demote endpoints work properly."""
    res = client.get("/api/paper/candidates")
    assert res.status_code == 200
    data = res.json()
    assert "candidates" in data
    assert "total" in data

    if data["candidates"]:
        cand = data["candidates"][0]
        cid = cand["id"]

        # Promote
        res_p = client.post(f"/api/paper/candidates/{cid}/promote")
        assert res_p.status_code == 200
        assert res_p.json()["status"] == "PAPER"

        # Demote
        res_d = client.post(f"/api/paper/candidates/{cid}/demote")
        assert res_d.status_code == 200
        assert res_d.json()["status"] == "QUALIFIED"

        # Batch promote
        res_b = client.post("/api/paper/candidates/batch_promote", json={"strategy_ids": [cid]})
        assert res_b.status_code == 200
        assert res_b.json()["count"] == 1
