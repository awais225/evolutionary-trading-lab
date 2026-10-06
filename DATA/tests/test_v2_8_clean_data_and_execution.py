"""Tests A through K for Evolutionary Trading Research Lab V2.8:
Clean Data Acquisition Reset, Workspace Cleanup, Node Generation State Repair & Data Management.
"""
import json
import os
import shutil
import time
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app import paths as P
from backend.app.data.discovery import DataDiscoveryEngine, get_discovery_engine
from backend.app.db.database import Database
from backend.app.evolution.engine import EvolutionEngine
from backend.app.orchestrator.pipeline_state import PipelineStateManager, get_pipeline_state_manager
from backend.app.orchestrator.stages import StageManager, CallableString, get_stage_manager


def _create_sample_parquet(path: Path, rows: int = 200, symbol: str = "XAUUSD") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    base_ts = 1774000000.0
    ts_list = [base_ts + i * 900 for i in range(rows)]
    df = pd.DataFrame({
        "ts": ts_list,
        "open": [2000.0 + (i % 10) for i in range(rows)],
        "high": [2005.0 + (i % 10) for i in range(rows)],
        "low": [1995.0 + (i % 10) for i in range(rows)],
        "close": [2002.0 + (i % 10) for i in range(rows)],
        "volume": [100.0 for _ in range(rows)],
    })
    df.to_parquet(path, index=False)


@pytest.fixture
def clean_env(tmp_path, monkeypatch):
    data_dir = tmp_path / "DATA"
    test_data_dir = tmp_path / "TEST_DATA"
    cache_dir = tmp_path / "CACHE"
    db_file = tmp_path / "DATABASE" / "lab_state.db"

    data_dir.mkdir(parents=True)
    test_data_dir.mkdir(parents=True)
    cache_dir.mkdir(parents=True)
    db_file.parent.mkdir(parents=True)

    monkeypatch.setattr(P, "DATA_ROOT", data_dir)
    monkeypatch.setattr(P, "DATA_DIR", data_dir)
    monkeypatch.setattr(P, "TEST_DATA_ROOT", test_data_dir)
    monkeypatch.setattr(P, "TEST_DATA_DIR", test_data_dir)
    monkeypatch.setattr(P, "SIMULATOR_DATA_DIR", test_data_dir / "SIMULATOR_DATA")
    monkeypatch.setattr(P, "CACHE_ROOT", cache_dir)
    monkeypatch.setattr(P, "MT5_DIR", data_dir / "MT5")
    monkeypatch.setattr(P, "MT5_XAUUSD_DIR", data_dir / "MT5" / "XAUUSD")
    monkeypatch.setattr(P, "MT5_RAW_DIR", data_dir / "MT5" / "raw")
    monkeypatch.setattr(P, "MT5_NORMALIZED_DIR", data_dir / "MT5" / "normalized")
    monkeypatch.setattr(P, "NODES_DIR", data_dir / "nodes")
    monkeypatch.setattr(P, "GENOMES_DIR", data_dir / "genomes")
    monkeypatch.setattr(P, "FEATURES_DIR", data_dir / "features")
    monkeypatch.setattr(P, "MANIFESTS_DIR", data_dir / "manifests")

    P.ensure_layout()
    db = Database(str(db_file))
    disc = DataDiscoveryEngine(data_root=data_dir)
    monkeypatch.setattr("backend.app.api.routes.get_db", lambda: db)
    monkeypatch.setattr("backend.app.orchestrator.pipeline_state.get_db", lambda: db)
    monkeypatch.setattr("backend.app.db.database._db", db)
    monkeypatch.setattr("backend.app.data.discovery.get_discovery_engine", lambda: disc)
    return {"data": data_dir, "test_data": test_data_dir, "cache": cache_dir, "db": db, "disc": disc}


def test_test_a_clean_data(clean_env):
    """TEST A: CLEAN DATA
    • start with DATA empty
    • verify pipeline reports:
      - RAW DATA: EMPTY
      - DECISION: FETCH REAL MT5 DATA
      - does not report REUSE
    """
    disc = clean_env["disc"]
    db = clean_env["db"]
    disc.discover_all(emit_logs=False)
    recon = disc.reconcile_with_database(db=db, emit_logs=True)
    rep = disc.get_startup_status_report(db)

    assert rep["data"]["valid_datasets"] == 0
    assert rep["data"]["missing_datasets"] == 5
    assert rep["data"]["data_source"] == "NONE"
    for tf in ("M1", "M5", "M15", "M30", "H1"):
        assert rep["data"]["timeframes"][tf] == "MISSING"

    psm = PipelineStateManager()
    diag = psm.print_startup_diagnostic()
    assert diag["raw_data_status"] == "EMPTY"
    assert diag["raw_data_source"] == "NONE"
    assert diag["resume_decision"] == "FETCH REAL MT5 DATA"
    assert "REUSE" not in diag["resume_decision"]


def test_test_b_real_data_exists(clean_env):
    """TEST B: REAL DATA EXISTS
    • place valid real XAUUSD Parquets in DATA\\MT5\\XAUUSD
    • verify discovery finds them
    • verify validation passes
    • verify decision: ACTION: REUSE REAL RAW DATA
    """
    disc = clean_env["disc"]
    db = clean_env["db"]
    xau_dir = clean_env["data"] / "MT5" / "XAUUSD"

    for tf in ("M1", "M5", "M15", "M30", "H1"):
        _create_sample_parquet(xau_dir / f"{tf}.parquet", rows=150)

    disc.discover_all(emit_logs=False)
    rep = disc.get_startup_status_report(db)
    print("DEBUG TEST B REP:", rep["data"])
    assert rep["data"]["valid_datasets"] == 5
    assert rep["data"]["missing_datasets"] == 0
    assert rep["data"]["data_source"] == "MT5 REAL"
    for tf in ("M1", "M5", "M15", "M30", "H1"):
        assert rep["data"]["timeframes"][tf] == "FOUND"

    val_m15 = disc.validate_timeframe_raw_data("XAUUSD", "M15", emit_logs=False)
    assert val_m15["is_valid"] is True
    assert val_m15["action"] == "ACTION: REUSE REAL RAW DATA"


def test_test_c_simulator_data_only(clean_env):
    """TEST C: SIMULATOR DATA ONLY
    • place simulator dataset in TEST_DATA
    • verify real data discovery does NOT count it
    • verify pipeline reports:
      RAW DATA: EMPTY
      ACTION: REAL RAW DATA NOT FOUND
    """
    disc = clean_env["disc"]
    db = clean_env["db"]
    sim_dir = clean_env["test_data"] / "SIMULATOR_DATA" / "XAUUSD"
    _create_sample_parquet(sim_dir / "M15.parquet", rows=200)

    # Real data discovery must NOT scan TEST_DATA
    disc.discover_all(emit_logs=False)
    rep = disc.get_startup_status_report(db)
    assert rep["data"]["valid_datasets"] == 0
    assert rep["data"]["data_source"] == "NONE"

    # Even if pointed directly at a simulator file, validation must reject it
    val_sim = disc.validate_timeframe_raw_data("XAUUSD", "M15", path=sim_dir / "M15.parquet", emit_logs=False)
    assert val_sim["is_valid"] is False
    assert val_sim["action"] == "ACTION: REAL RAW DATA NOT FOUND"
    assert "SIMULATOR" in val_sim["validation_result"]


def test_test_d_cache_exists_but_data_empty(clean_env):
    """TEST D: CACHE EXISTS BUT DATA EMPTY
    • place cached features in CACHE
    • keep DATA empty
    • verify pipeline reports:
      RAW DATA: EMPTY
      DECISION: FETCH REAL MT5 DATA
      does not use cache as proof of raw data
    """
    cache_feat = clean_env["cache"] / "FEATURES" / "XAUUSD_M15.parquet"
    _create_sample_parquet(cache_feat, rows=100)

    disc = clean_env["disc"]
    db = clean_env["db"]
    disc.discover_all(emit_logs=False)
    rep = disc.get_startup_status_report(db)

    assert rep["data"]["valid_datasets"] == 0
    assert rep["data"]["data_source"] == "NONE"

    psm = PipelineStateManager()
    diag = psm.print_startup_diagnostic()
    assert diag["raw_data_status"] == "EMPTY"
    assert diag["resume_decision"] == "FETCH REAL MT5 DATA"


def test_test_e_database_contains_old_nodes_data_empty(clean_env):
    """TEST E: DATABASE CONTAINS OLD NODES BUT DATA IS EMPTY
    • DATABASE contains 371 nodes from previous run
    • DATA is empty
    • verify pipeline detects previous run
    • logs:
      RUN ID: RUN-...-CLEAN-MT5
      PREVIOUS RUN: PRESERVED BUT NOT RESUMED (371 nodes)
      RAW DATA: EMPTY
      DECISION: FETCH REAL MT5 DATA
      STARTING NODES: 0
      TARGET NODES: 1000
    • pipeline does NOT mark nodes complete
    • pipeline does NOT halt at 371
    • pipeline does NOT treat old nodes as proof of data
    """
    db = clean_env["db"]
    for i in range(1, 372):
        db.x(
            """INSERT INTO strategies (id, hash, symbol, timeframe, status, genome, created_at, updated_at, run_id)
               VALUES (?, ?, 'XAUUSD', 'M15', 'KILLED', '{}', ?, ?, 'RUN-20260927-061830')""",
            (i, f"hash_old_{i}", time.time() - 1000, time.time() - 1000)
        )
    assert db.total_strategies_count() == 371

    psm = PipelineStateManager()
    psm.previous_run_info = {"run_id": "RUN-20260927-061830", "nodes": 371}
    diag = psm.print_startup_diagnostic()

    assert "CLEAN-MT5" in diag["run_id"]
    assert "371" in diag["previous_run_detected"]
    assert diag["raw_data_status"] == "EMPTY"
    assert diag["resume_decision"] == "FETCH REAL MT5 DATA"

    evo = EvolutionEngine(db, run_id=diag["run_id"])
    evo.set_total_node_target(1000)
    state = evo.get_node_generation_state()

    assert state["CURRENT PERSISTED NODES"] == 371
    assert state["CURRENT GENERATED NODES"] == 0
    assert state["TARGET NODES"] == 1000
    assert state["REMAINING NODES"] == 1000
    assert state["is_target_reached"] is False


def test_test_f_existing_old_run(clean_env):
    """TEST F: EXISTING OLD RUN
    • Database has old run state
    • clean run starts with: RUN-...-CLEAN-MT5
    • old run is preserved in database / archive
    • new run does NOT auto-resume old nodes
    • new run starts node generation from 0 towards target (e.g. 1000)
    """
    db = clean_env["db"]
    for i in range(1, 372):
        db.x(
            """INSERT INTO strategies (id, hash, symbol, timeframe, status, genome, created_at, updated_at, run_id)
               VALUES (?, ?, 'XAUUSD', 'M15', 'KILLED', '{}', ?, ?, 'RUN-20260927-061830')""",
            (i, f"old_{i}", time.time() - 500, time.time() - 500)
        )

    psm = PipelineStateManager()
    clean_run_id = psm.run_id
    assert "CLEAN-MT5" in clean_run_id

    evo = EvolutionEngine(db, run_id=clean_run_id)
    assert evo.total_nodes() == 0  # Starts from 0 for the clean run
    assert evo.all_persisted_nodes() == 371  # Old nodes preserved


def test_test_g_node_generation_to_target_and_callable_string(clean_env):
    """TEST G: NODE GENERATION TO TARGET
    • node generation runs continuously
    • passes 371 nodes without stopping
    • reaches target (e.g. 1000) or stops ONLY when target reached
    • no crash: sm.current_stage()
    • no infinite watchdog loop
    """
    import random
    from backend.app.genome import ops as gops

    db = clean_env["db"]
    sm = StageManager()

    # Verify CallableString dual compatibility
    assert sm.current_stage == "DATA_SYNC"
    assert sm.current_stage() == "DATA_SYNC"
    assert isinstance(sm.current_stage, str)
    assert isinstance(sm.current_stage, CallableString)

    evo = EvolutionEngine(db, run_id="RUN-20260927-CLEAN-MT5")
    evo.set_total_node_target(400)

    # Seed 30 strategies
    n_seeded = evo.seed_population(30, "XAUUSD")
    assert n_seeded == 30
    assert evo.total_nodes() == 30

    # Fast forward: generate valid random genomes up to and beyond 371 to 400
    rng = random.Random(12345)
    for i in range(31, 401):
        g = gops.random_genome("XAUUSD", rng)
        evo.try_insert(
            g,
            parent_id=None,
            generation=1,
            status="BORN",
            creation_reason=f"test candidate {i}",
        )

    assert evo.total_nodes() == 400
    assert evo.is_target_reached() is True
    state = evo.get_node_generation_state()
    assert state["STOP CONDITION"] == "TARGET_REACHED"


def test_test_h_clear_xauusd(clean_env):
    """TEST H: CLEAR XAUUSD
    • click CLEAR XAUUSD DATA
    • verify only DATA\\MT5\\XAUUSD is removed
    • verify cache and nodes remain
    """
    client = TestClient(app)
    xau_dir = clean_env["data"] / "MT5" / "XAUUSD"
    cache_feat = clean_env["cache"] / "FEATURES" / "test_feat.parquet"
    node_file = clean_env["data"] / "nodes" / "node_1.json"

    _create_sample_parquet(xau_dir / "M15.parquet", rows=50)
    _create_sample_parquet(cache_feat, rows=20)
    node_file.parent.mkdir(parents=True, exist_ok=True)
    node_file.write_text('{"id": 1}')

    res = client.post("/api/data/clear/xauusd")
    assert res.status_code == 200
    assert res.json()["ok"] is True

    # XAUUSD files should be gone
    assert len(list(xau_dir.glob("*.parquet"))) == 0
    # Cache and nodes should remain intact
    assert cache_feat.exists()
    assert node_file.exists()


def test_test_i_clear_cache(clean_env):
    """TEST I: CLEAR CACHE
    • click CLEAR CACHE
    • verify CACHE is removed
    • verify raw data remains intact
    """
    client = TestClient(app)
    xau_p = clean_env["data"] / "MT5" / "XAUUSD" / "M15.parquet"
    cache_feat = clean_env["cache"] / "FEATURES" / "feat.parquet"

    _create_sample_parquet(xau_p, rows=50)
    _create_sample_parquet(cache_feat, rows=20)

    res = client.post("/api/data/clear/cache")
    assert res.status_code == 200
    assert res.json()["ok"] is True

    # Cache should be removed
    assert not cache_feat.exists()
    # Raw data must remain intact
    assert xau_p.exists()


def test_test_j_clear_nodes(clean_env):
    """TEST J: CLEAR NODES
    • click CLEAR NODES
    • verify node artifacts are removed
    • verify raw data remains intact
    """
    client = TestClient(app)
    xau_p = clean_env["data"] / "MT5" / "XAUUSD" / "M15.parquet"
    node_file = clean_env["data"] / "nodes" / "node_1.json"

    _create_sample_parquet(xau_p, rows=50)
    node_file.parent.mkdir(parents=True, exist_ok=True)
    node_file.write_text('{"id": 1}')

    res = client.post("/api/data/clear/nodes")
    assert res.status_code == 200
    assert res.json()["ok"] is True

    assert not node_file.exists()
    assert xau_p.exists()


def test_test_k_clear_all_data(clean_env):
    """TEST K: CLEAR ALL DATA
    • type CLEAR ALL DATA
    • verify full reset succeeds
    """
    client = TestClient(app)
    xau_p = clean_env["data"] / "MT5" / "XAUUSD" / "M15.parquet"
    cache_feat = clean_env["cache"] / "FEATURES" / "feat.parquet"
    node_file = clean_env["data"] / "nodes" / "node_1.json"

    _create_sample_parquet(xau_p, rows=50)
    _create_sample_parquet(cache_feat, rows=20)
    node_file.parent.mkdir(parents=True, exist_ok=True)
    node_file.write_text('{"id": 1}')

    # Fails if confirmation string does not match
    bad_res = client.post("/api/data/clear/all", json={"confirm": "wrong"})
    assert bad_res.status_code == 400

    # Succeeds with exact confirmation
    good_res = client.post("/api/data/clear/all", json={"confirm": "CLEAR ALL DATA"})
    assert good_res.status_code == 200
    assert good_res.json()["ok"] is True

    assert not xau_p.exists()
    assert not cache_feat.exists()
    assert not node_file.exists()
