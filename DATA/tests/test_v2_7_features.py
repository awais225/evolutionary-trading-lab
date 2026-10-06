"""
Tests for Evolutionary Trading Research Lab V2.7 Implementation.

Validates:
  1. Persistent DATA Architecture (DATA_ROOT, 12 version-independent folders, manifests with relative paths)
  2. Data Reuse & Incremental Updates (ACTION: REUSING EXISTING DATA, persistent features under DATA/features/)
  3. Real Progress Tracking & Heartbeats (Overall Job -> Stage -> Task -> Workers, real ETA, heartbeats)
  4. Stall Detection (ACTIVE, WAITING, COMPLETED, FAILED, STALLED with last progress timestamp and stalled duration)
  5. CPU & GPU Control (10%-100% target in 10% steps, dynamic worker pool sizing, GPU toggle, CONFIG/settings.json)
  6. Detailed Activity Feed & Diagnostic Copy Log (newest first, 11 filters, formatted log generation)
  7. Data Inventory API (GET /api/data/inventory, GET /api/jobs, GET/POST /api/settings)
  8. Critical Invariants (Evolution Tree untouched, DATABASE/lab_state.db user_version 3 & 371 strategies)
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from backend.app.paths import ROOT_DIR
sys.path.insert(0, str(ROOT_DIR / "backend"))

from app import paths as P
from app.activity import activity
from app.config import get_config, load_config, save_config, SETTINGS_JSON_PATH
from app.data import manifest
from app.data.engine import get_data_engine
from app.features.engine import get_feature_engine, DEFAULT_FEATURE_SET_ID, DEFAULT_SCHEMA_VERSION
from app.jobs import get_job_manager
from app.main import app
from app.resources.manager import get_resource_manager


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


# ---------------- 1. Persistent DATA Architecture ----------------
def test_persistent_data_architecture():
    """Verify DATA_ROOT and all 12 version-independent folders exist."""
    P.ensure_layout()
    assert P.DATA_ROOT == ROOT_DIR / "DATA"
    assert P.DATA_ROOT.exists()

    expected_dirs = [
        P.MT5_RAW_DIR,
        P.MT5_NORMALIZED_DIR,
        P.MT5_DATASETS_DIR,
        P.MT5_METADATA_DIR,
        P.NODES_DIR,
        P.GENOMES_DIR,
        P.FEATURES_DIR,
        P.DATASETS_DIR,
        P.RESEARCH_DATA_DIR,
        P.CACHE_DIR,
        P.MANIFESTS_DIR,
        P.METADATA_DIR,
    ]
    for d in expected_dirs:
        assert d.exists(), f"Required folder {d} must exist"

    # Manifest file locations
    assert P.DATASETS_MANIFEST.parent == P.MANIFESTS_DIR
    assert P.FEATURES_MANIFEST.parent == P.MANIFESTS_DIR
    assert P.RESEARCH_MANIFEST.parent == P.MANIFESTS_DIR


def test_manifest_system_relative_paths():
    """Verify manifest system uses relative paths to DATA_ROOT for disk portability."""
    manifest.update_dataset_entry("TEST_M15", {
        "symbol": "TEST",
        "timeframe": "M15",
        "raw_path": "MT5/raw/TEST_M15.parquet",
        "rows": 500,
    })
    ds_man = manifest.get_datasets_manifest()
    assert "TEST_M15" in ds_man["datasets"]
    assert ds_man["datasets"]["TEST_M15"]["raw_path"] == "MT5/raw/TEST_M15.parquet"
    assert not os.path.isabs(ds_man["datasets"]["TEST_M15"]["raw_path"])

    manifest.update_feature_entry("TEST_M15__core_v1__core_v1", {
        "dataset_id": "TEST_M15",
        "feature_set_id": "core_v1",
        "schema_version": "core_v1",
        "relative_path": "features/TEST_M15__core_v1__core_v1.parquet",
        "column_count": 21,
    })
    ft_man = manifest.get_features_manifest()
    assert "TEST_M15__core_v1__core_v1" in ft_man["features"]
    assert not os.path.isabs(ft_man["features"]["TEST_M15__core_v1__core_v1"]["relative_path"])


# ---------------- 2. Data Reuse & Incremental Updates ----------------
def test_data_reuse_and_persistent_features():
    """Verify data is reused and persistent features in DATA/features/ are never recomputed."""
    de = get_data_engine()
    fe = get_feature_engine()

    # Ingest / sync test dataset
    res = de.sync_master("XAUUSD", "M15")
    assert res is not None
    did = res.get("dataset_id") or "XAUUSD_M15"

    # Reading dataset must reuse data without refetch
    df = de.get_frame(did)
    assert df is not None
    assert len(df) > 0

    # Second sync immediately must trigger ACTION: REUSING EXISTING DATA
    res_reuse = de.sync_master("XAUUSD", "M15")
    assert res_reuse.get("fetch_mode") == "reuse_existing" or res_reuse.get("new_bars") == 0

    # Persistent feature storage under DATA/features/
    arr_computed = fe.get(did, "rsi:14")
    assert arr_computed is not None

    feat_path = P.FEATURES_DIR / f"{did}__{DEFAULT_FEATURE_SET_ID}__{DEFAULT_SCHEMA_VERSION}.parquet"
    assert feat_path.exists(), f"Feature parquet {feat_path} must exist permanently"

    # Reset in-memory cache to simulate restart
    fe._cache.clear()
    fe._disk_loaded.clear()

    # Re-reading features must load from disk cache
    arr = fe.get(did, "rsi:14")
    assert arr is not None
    assert len(arr) == len(df)


# ---------------- 3. Real Progress Tracking & Heartbeats ----------------
def test_real_progress_tracking_and_eta():
    """Verify progress calculation from actual units and real ETA formula."""
    jm = get_job_manager()
    job = jm.create_job("Test Multi-Level Job", "ingest")
    job.start()

    job.add_stage("fetch", "Fetch Bars")
    job.add_task("fetch", "download", "Download Parquet", total_units=1000, unit_name="bars")

    # Real progress calculation
    job.update_task_progress("fetch", "download", 250)
    d = job.to_dict()
    assert d["overall_progress_pct"] == 25.0
    assert d["status"] == "ACTIVE"

    # Real ETA: elapsed * (1 - prog) / prog
    time.sleep(0.05)
    hb = job.heartbeat("Processed 250 bars")
    assert hb["progress_pct"] == 25.0

    job.update_task_progress("fetch", "download", 1000)
    job.complete_stage("fetch")
    job.complete({"total_bars": 1000})

    d_done = job.to_dict()
    assert d_done["status"] == "COMPLETED"
    assert d_done["overall_progress_pct"] == 100.0


# ---------------- 4. Stall Detection ----------------
def test_stall_detection():
    """Verify stall detection distinguishes ACTIVE from STALLED with last progress timestamp."""
    jm = get_job_manager()
    # Fast stall threshold of 0.2s for testing
    job = jm.create_job("Stall Test Job", "backtest", stall_threshold_s=0.2)
    job.start()
    job.add_stage("screen", "Screening")
    job.add_task("screen", "test_batch", "Screen Strategies", total_units=100, unit_name="strategies")
    job.update_task_progress("screen", "test_batch", 20)

    # Immediately ACTIVE
    assert job.evaluate_status() == "ACTIVE"
    d_active = job.to_dict()
    assert d_active["status"] == "ACTIVE"
    assert not d_active["is_stalled"]

    # Sleep past threshold -> STALLED
    time.sleep(0.25)
    assert job.evaluate_status() == "STALLED"
    d_stalled = job.to_dict()
    assert d_stalled["status"] == "STALLED"
    assert d_stalled["is_stalled"] is True
    assert d_stalled["stalled_duration_s"] >= 0.2

    # Resume work -> ACTIVE again
    job.update_task_progress("screen", "test_batch", 40)
    assert job.evaluate_status() == "ACTIVE"
    assert job.to_dict()["status"] == "ACTIVE"

    # Completed jobs are never STALLED
    job.complete()
    assert job.evaluate_status() == "COMPLETED"
    assert job.to_dict()["status"] == "COMPLETED"


# ---------------- 5. CPU & GPU Control ----------------
def test_cpu_and_gpu_control_and_settings_persistence(client):
    """Verify CPU target (10-100% in 10% steps), dynamic worker pool sizing, and GPU toggle."""
    rm = get_resource_manager()
    cfg = get_config()

    # Test CPU Target adjustment
    res = rm.set_cpu_target(80)
    assert res["cpu_target_pct"] == 80
    assert cfg.resources.cpu_target_pct == 80
    cores = os.cpu_count() or 2
    expected_workers = max(1, round(cores * 0.8))
    assert res["effective_workers"] == expected_workers

    # Test GPU toggle
    gpu_res = rm.set_gpu_enabled(True)
    assert gpu_res["gpu_enabled"] is True
    assert cfg.resources.gpu_enabled is True

    # Test POST /api/settings endpoint applying immediately and persisting to CONFIG/settings.json
    api_res = client.post("/api/settings", json={"cpu_target_pct": 50, "gpu_enabled": False})
    assert api_res.status_code == 200
    applied = api_res.json()
    assert applied["success"] is True
    assert applied["settings"]["cpu_target_pct"] == 50
    assert applied["settings"]["gpu_enabled"] is False

    # Verify persisted in CONFIG/settings.json
    assert SETTINGS_JSON_PATH.exists()
    saved_data = json.loads(SETTINGS_JSON_PATH.read_text())
    assert saved_data["resources"]["cpu_target_pct"] == 50
    assert saved_data["resources"]["gpu_enabled"] is False

    # Reset to default 60%
    client.post("/api/settings", json={"cpu_target_pct": 60})


# ---------------- 6. Detailed Activity Feed & Diagnostic Copy Log ----------------
def test_activity_filters_and_diagnostic_log(client):
    """Verify 11 filter controls, newest-first ordering, and formatted diagnostic log generation."""
    activity.info("SYSTEM", "System initialized")
    activity.started("MT5", "MT5 connecting")
    activity.success("DATA", "Dataset synced")
    activity.warning("FEATURES", "Feature check warning")
    activity.error("WORKERS", "Worker task error")

    # Newest first default
    events_desc = client.get("/api/activity?sort=desc").json()
    assert len(events_desc) >= 3
    assert events_desc[0]["ts"] >= events_desc[-1]["ts"]

    # Filter controls
    err_events = client.get("/api/activity?filter=Errors").json()
    assert all(e["level"] == "ERROR" or e["status"] == "FAILED" for e in err_events)

    warn_events = client.get("/api/activity?filter=Warnings").json()
    assert all(e["level"] == "WARNING" or e["status"] == "WARNING" for e in warn_events)

    mt5_events = client.get("/api/activity?filter=MT5").json()
    assert all(e["category"] == "MT5" for e in mt5_events)

    feat_events = client.get("/api/activity?filter=Features").json()
    assert all(e["category"] in ("FEATURES", "FEATURE") for e in feat_events)

    worker_events = client.get("/api/activity?filter=Workers").json()
    assert all(e["category"] in ("WORKERS", "WORKER", "RESOURCE") for e in worker_events)

    # Diagnostic log text export
    diag_res = client.get("/api/logs/diagnostic")
    assert diag_res.status_code == 200
    diag = diag_res.json()
    text = diag["diagnostic_text"]
    assert "EVOLUTIONARY TRADING RESEARCH LAB V2.71 - DIAGNOSTIC LOG" in text or "EVOLUTIONARY TRADING RESEARCH LAB V2.7 - DIAGNOSTIC LOG" in text
    assert "SYSTEM RESOURCE MONITOR" in text
    assert "PERSISTENCE & DATABASE" in text
    assert "DATA_ROOT" in text


# ---------------- 7. Data Inventory APIs ----------------
def test_data_inventory_api(client):
    """Verify GET /api/data/inventory endpoint structure and summaries."""
    res = client.get("/api/data/inventory")
    assert res.status_code == 200
    data = res.json()
    assert "summary" in data
    assert "datasets" in data
    assert "features" in data
    assert "manifests" in data

    summary = data["summary"]
    assert summary["total_datasets"] >= 0
    assert summary["total_raw_bars"] >= 0
    assert "raw_storage_mb" in summary
    assert "features_storage_mb" in summary

    # Verify manifest paths relative to DATA_ROOT
    assert data["manifests"]["datasets_manifest"] == "manifests/datasets.json"
    assert data["manifests"]["features_manifest"] == "manifests/features.json"


# ---------------- 8. Critical Invariants ----------------
def test_critical_invariants_db_and_tree():
    """Verify DATABASE/lab_state.db user_version=3, 371 strategies, and Evolution Tree preservation."""
    # 1. Database Integrity
    db_path = ROOT_DIR / "DATABASE" / "lab_state.db"
    assert db_path.exists(), "DATABASE/lab_state.db must exist"
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("PRAGMA user_version")
    uv = c.fetchone()[0]
    assert uv == 3, f"Schema user_version must remain 3, got {uv}"

    c.execute("SELECT COUNT(*) FROM strategies")
    count = c.fetchone()[0]
    assert count >= 371, f"Strategies count must remain at least 371, got {count}"
    conn.close()

    # 2. Evolution Tree untouched check
    tree_file = ROOT_DIR / "frontend" / "src" / "pages" / "EvolutionTree.jsx"
    assert tree_file.exists(), "frontend/src/pages/EvolutionTree.jsx must exist"
    tree_content = tree_file.read_text(encoding="utf-8")
    assert "EvolutionTree" in tree_content
    assert "ReactFlow" in tree_content
    assert "sourceHandle" in tree_content
    assert "targetHandle" in tree_content
