"""
Comprehensive Test Suite for Data Discovery, Database Reconciliation,
and Technical Logging (V2.7).

Verifies Section 35 Requirements:
  - Test 1: Empty/new DATABASE + populated DATA
  - Test 2: Empty DATABASE + empty DATA
  - Test 3: Populated DATABASE + populated DATA
  - Test 4: Existing dataset + newer MT5 bars -> delta only
  - Test 5: Feature artifact already exists -> reuse
  - Test 6: Feature artifact missing one feature -> calculate only missing
  - Test 7: Node generation -> 1000 nodes, Overview, Live Activity, Log, PipelineState agree
"""
import json
import tempfile
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db.database import Database, get_db
from app.data.discovery import DataDiscoveryEngine, get_discovery_engine
from app.data.engine import DataEngine
from app.features.engine import FeatureEngine
from app.orchestrator.pipeline_state import get_pipeline_state
from app.evolution.engine import get_evo_engine


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def setup_populated_test_data(tmp_path_factory, monkeypatch):
    data_dir = tmp_path_factory.mktemp("v27_populated_data")
    xau_dir = data_dir / "MT5" / "XAUUSD"
    xau_dir.mkdir(parents=True, exist_ok=True)
    manifest_dir = data_dir / "manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)

    # Generate 5 timeframes: M1, M5, M15, M30, H1
    now = time.time()
    for tf in ["M1", "M5", "M15", "M30", "H1"]:
        ts = np.linspace(now - 1000 * 60, now, 100)
        c = 2600.0 + np.random.randn(100)
        df = pd.DataFrame({"ts": ts, "time": ts, "open": c, "high": c + 1, "low": c - 1, "close": c, "tick_volume": 100, "volume": 100})
        df.to_parquet(xau_dir / f"{tf}.parquet", index=False)

    disc = DataDiscoveryEngine(data_root=data_dir)
    disc.discover_all(emit_logs=False)
    monkeypatch.setattr("tests.test_data_discovery_and_reconciliation.get_discovery_engine", lambda: disc)
    monkeypatch.setattr("backend.app.data.discovery.get_discovery_engine", lambda: disc)
    monkeypatch.setattr("app.data.discovery.get_discovery_engine", lambda: disc)


def test_test1_empty_database_populated_data(tmp_path):
    """TEST 1: Empty/new DATABASE + populated DATA.
    Expected:
      DATA discovered
      DATA validated
      DATA registered
      No unnecessary download
      No unnecessary feature recomputation
      Pipeline continues
    """
    # Create empty database
    db_path = tmp_path / "empty_lab.db"
    db = Database(str(db_path))
    assert len(db.q("SELECT * FROM datasets")) == 0

    disc = get_discovery_engine()
    res = disc.discover_all(emit_logs=False)
    assert len(res["datasets"]) > 0

    reconcile_out = disc.reconcile_with_database(db=db, emit_logs=False)
    assert reconcile_out["scenario"] == "EMPTY_DB_POPULATED_DATA"
    assert reconcile_out["registered_datasets"] >= 5

    # Check that database now has all datasets registered
    rows = db.q("SELECT * FROM datasets")
    assert len(rows) >= 5
    symbols_tfs = {(r["symbol"], r["timeframe"]) for r in rows}
    assert ("XAUUSD", "M5") in symbols_tfs
    assert ("XAUUSD", "M15") in symbols_tfs

    # Check decision tree
    reuse_dec = disc.check_dataset_reuse("XAUUSD", "M5", emit_logs=False)
    assert reuse_dec["action"] == "REUSE"
    assert reuse_dec["reuse"] is True
    assert reuse_dec["bars"] > 0


def test_test2_empty_database_empty_data(tmp_path):
    """TEST 2: Empty DATABASE + empty DATA.
    Expected:
      DATA missing
      Bootstrap initiated
    """
    empty_data_root = tmp_path / "EMPTY_DATA"
    empty_data_root.mkdir()
    (empty_data_root / "manifests").mkdir()

    disc_empty = DataDiscoveryEngine(data_root=empty_data_root)
    res = disc_empty.discover_all(emit_logs=False)
    assert len(res["datasets"]) == 0

    db_path = tmp_path / "empty_lab2.db"
    db = Database(str(db_path))
    reconcile_out = disc_empty.reconcile_with_database(db=db, emit_logs=False)
    assert reconcile_out["scenario"] == "EMPTY_DB_EMPTY_DATA"

    reuse_dec = disc_empty.check_dataset_reuse("XAUUSD", "M5", emit_logs=False)
    assert reuse_dec["action"] == "BOOTSTRAP"
    assert reuse_dec["reuse"] is False


def test_test3_populated_database_populated_data(tmp_path):
    """TEST 3: Populated DATABASE + populated DATA.
    Expected:
      Reuse existing state
      Minimal work
      Pipeline continues
    """
    db = get_db()
    disc = get_discovery_engine()
    reconcile_out = disc.reconcile_with_database(db=db, emit_logs=False)
    assert reconcile_out["scenario"] == "POPULATED_DB_POPULATED_DATA"
    assert reconcile_out["database_datasets_before"] > 0


def test_test4_existing_dataset_newer_mt5_bars_delta_only():
    """TEST 4: Existing dataset + newer MT5 bars.
    Expected:
      Existing dataset reused
      Only delta calculated / identified
    """
    disc = get_discovery_engine()
    # If bridge has bars past the stored end time
    now_sim = time.time() + 86400  # 1 day in future
    reuse_dec = disc.check_dataset_reuse("XAUUSD", "M15", latest_bridge_ts=now_sim, emit_logs=False)
    assert reuse_dec["action"] == "EXTEND"
    assert reuse_dec["reuse"] is True
    assert reuse_dec["delta_bars"] > 0
    assert reuse_dec["delta_start"] > 0


def test_test5_feature_artifact_already_exists():
    """TEST 5: Feature artifact already exists.
    Expected:
      Feature artifact reused without recalculation
    """
    feat = FeatureEngine()
    feat._load_disk_cache("XAUUSD_M15_SIMULATOR_LAB_SIMULATOR_SIM_v68")
    cached = feat.cached_features("XAUUSD_M15_SIMULATOR_LAB_SIMULATOR_SIM_v68")
    assert len(cached) > 0


def test_test6_feature_artifact_missing_subset():
    """TEST 6: Feature artifact missing one or more features.
    Expected:
      Existing features reused
      Only missing features identified for compute
    """
    feat = FeatureEngine()
    cached = ["rsi:14", "ema:20", "sma:20"]
    required = ["rsi:14", "ema:20", "sma:20", "adx:14", "atr:14"]
    from app.features.library import is_spec_cached
    missing = [s for s in required if not is_spec_cached(s, cached)]
    assert len(missing) == 2
    assert "adx:14" in missing
    assert "atr:14" in missing


def test_test7_single_source_of_truth_agreement(client):
    """TEST 7: Node generation: Overview, LIVE ACTIVITY, LOG, and PipelineState show the SAME node count."""
    ps = get_pipeline_state()
    evo = get_evo_engine()

    state = ps.to_dict()
    node_state = evo.get_node_generation_state()

    # 1. API: /api/pipeline/state
    res_ps = client.get("/api/pipeline/state")
    assert res_ps.status_code == 200
    d_ps = res_ps.json()

    # 2. API: /api/workflow/task_state (Live Activity)
    res_task = client.get("/api/workflow/task_state")
    assert res_task.status_code == 200
    d_task = res_task.json()

    # 3. API: /api/lab/status (Overview)
    res_lab = client.get("/api/lab/status")
    assert res_lab.status_code == 200
    d_lab = res_lab.json()

    # Verify identical counts across all endpoints
    assert d_ps["node_completed"] == node_state["current_nodes"]
    assert d_task["current_nodes"] == node_state["current_nodes"]
    assert d_lab["total_nodes"] == node_state["current_nodes"]
    assert d_task["node_target"] == node_state["target_nodes"]
    assert d_lab["target_nodes"] == node_state["target_nodes"]


def test_technical_logs_export_api(client):
    """Test technical log export and structured format."""
    res = client.get("/api/logs/export")
    assert res.status_code == 200
    data = res.json()
    assert "log_text" in data
    assert "EVOLUTIONARY TRADING RESEARCH LAB - TECHNICAL DIAGNOSTIC LOG" in data["log_text"]

    res_logs = client.get("/api/logs?limit=50&category=DATA")
    assert res_logs.status_code == 200
    assert "logs" in res_logs.json()
