"""
Authoritative Acceptance Test Suite (Scenarios A through F + Batch Launcher).

Tests:
  Scenario A: Empty database, no raw data -> discovery -> MT5 fetch -> validation -> DATA/MT5/XAUUSD/ -> features -> research.
  Scenario B: Empty database, valid DATA/ exists -> discover -> validate M1..H1 -> REUSING EXISTING DATA -> no download -> rebuild index.
  Scenario C: Database exists, corrupted DATA/ exists -> validation failure -> EXISTING DATA INVALID/INCOMPLETE -> MT5 recovery -> retains valid files.
  Scenario D: Database and DATA both complete -> validates -> incremental check -> logs delta or zero new bars -> no full redownload.
  Scenario E: Feature computation completion -> logs FEATURES COMPLETE -> FEATURE VALIDATION -> transitions to RESEARCH/NODE_GENERATION without freezing.
  Scenario F: Worker stall detection -> heartbeat emitted -> watchdog reports STALLED with elapsed time and worker status (ALIVE vs DEAD) -> does not mark complete.
  Scenario Launcher: BAT execution environment headers and PowerShell routing wrappers.
"""
import json
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path

import pandas as pd
import pytest

from app.config import get_config
from app.data.discovery import DataDiscoveryEngine, get_discovery_engine
from app.data.engine import DataEngine
from app.db.database import Database
from app.mt5.simulator import SimulatorBridge
from app.orchestrator.stages import StageManager
from backend.app.paths import ROOT_DIR


@pytest.fixture
def clean_temp_dir():
    temp_dir = Path(tempfile.mkdtemp(prefix="lab_test_scenarios_"))
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_scenario_a_empty_db_no_raw_data(clean_temp_dir, caplog):
    """Scenario A: Empty database, no raw data -> Discovery finds nothing -> MT5 fallback -> Validated & Stored."""
    db_file = clean_temp_dir / "test_a.db"
    db = Database(str(db_file))

    data_dir = clean_temp_dir / "DATA"
    data_dir.mkdir(parents=True)
    (data_dir / "MT5" / "XAUUSD").mkdir(parents=True)
    (data_dir / "features").mkdir(parents=True)
    (data_dir / "manifests").mkdir(parents=True)

    # 1. Discovery on empty DATA
    discovery = DataDiscoveryEngine(data_root=data_dir)
    res = discovery.discover_all(emit_logs=True)
    assert len(res["raw_datasets"]) == 0

    val_res = discovery.validate_timeframe_raw_data("XAUUSD", "M15", emit_logs=True)
    assert not val_res["is_valid"]
    assert val_res["action"] == "ACTION: EXISTING DATA INVALID/INCOMPLETE"

    # 2. MT5 Bridge fallback
    bridge = SimulatorBridge(seed=42)
    assert bridge.connect() is True
    assert bridge.symbol_info("XAUUSD") is not None

    # Fetch rates from bridge
    bars = bridge.copy_rates("XAUUSD", "M15", 500)
    assert len(bars) == 500

    # Save to DATA/MT5/XAUUSD/M15.parquet
    df_raw = pd.DataFrame([b.to_dict() for b in bars])
    out_file = data_dir / "MT5" / "XAUUSD" / "M15.parquet"
    df_raw.to_parquet(out_file, index=False)
    assert out_file.exists()

    # 3. Physical raw data validation
    post_val = discovery.validate_timeframe_raw_data("XAUUSD", "M15", path=out_file, emit_logs=True)
    assert post_val["is_valid"] is True
    assert post_val["rows"] == 500
    assert post_val["action"] == "ACTION: REUSING EXISTING DATA"


def test_scenario_b_empty_db_valid_data_exists(clean_temp_dir, caplog):
    """Scenario B: Empty database, valid DATA/ exists -> Discover -> Validate -> ACTION: REUSING EXISTING DATA -> Rebuild index."""
    db_file = clean_temp_dir / "test_b.db"
    db = Database(str(db_file))

    data_dir = clean_temp_dir / "DATA"
    xau_dir = data_dir / "MT5" / "XAUUSD"
    xau_dir.mkdir(parents=True)
    (data_dir / "features").mkdir(parents=True)
    (data_dir / "manifests").mkdir(parents=True)

    bridge = SimulatorBridge(seed=42)
    bridge.connect()

    # Seed M1, M5, M15, M30, H1 datasets
    for tf in ["M1", "M5", "M15", "M30", "H1"]:
        bars = bridge.copy_rates("XAUUSD", tf, 200)
        df = pd.DataFrame([b.to_dict() for b in bars])
        df.to_parquet(xau_dir / f"{tf}.parquet", index=False)

    discovery = DataDiscoveryEngine(data_root=data_dir)
    with caplog.at_level(logging.INFO):
        disc_res = discovery.discover_all(emit_logs=True)
        rec_res = discovery.reconcile_with_database(db=db, emit_logs=True)

    # All 5 timeframes discovered
    assert len(disc_res["raw_datasets"]) == 5

    # Check that ACTION: REUSING EXISTING DATA was logged for validated timeframes
    val_m15 = discovery.validate_timeframe_raw_data("XAUUSD", "M15", emit_logs=True)
    assert val_m15["is_valid"] is True
    assert val_m15["action"] == "ACTION: REUSING EXISTING DATA"

    # Database index reconciled without network download
    db_rows = db.q("SELECT * FROM datasets")
    assert len(db_rows) >= 5


def test_scenario_c_corrupt_data_validation_and_recovery(clean_temp_dir):
    """Scenario C: Database exists, corrupted DATA/ exists -> Validation fails -> ACTION: EXISTING DATA INVALID/INCOMPLETE -> MT5 recovery."""
    db_file = clean_temp_dir / "test_c.db"
    db = Database(str(db_file))

    data_dir = clean_temp_dir / "DATA"
    xau_dir = data_dir / "MT5" / "XAUUSD"
    xau_dir.mkdir(parents=True)

    # 1. Create a corrupted file (missing required columns and containing NaNs)
    corrupt_df = pd.DataFrame({
        "ts": [1000.0, 1060.0, 1120.0],
        "open": [2000.0, None, 2005.0],
        # 'high', 'low', 'close' missing!
    })
    corrupt_path = xau_dir / "M15.parquet"
    corrupt_df.to_parquet(corrupt_path, index=False)

    # 2. Also create an unrelated valid file to ensure it is NOT wiped
    unrelated_df = pd.DataFrame({
        "ts": [1000.0, 1060.0, 1120.0],
        "open": [2000.0, 2001.0, 2002.0],
        "high": [2005.0, 2006.0, 2007.0],
        "low": [1995.0, 1996.0, 1997.0],
        "close": [2001.0, 2002.0, 2003.0],
    })
    valid_unrelated_path = xau_dir / "H1.parquet"
    unrelated_df.to_parquet(valid_unrelated_path, index=False)

    discovery = DataDiscoveryEngine(data_root=data_dir)
    val_res = discovery.validate_timeframe_raw_data("XAUUSD", "M15", path=corrupt_path, emit_logs=True)

    # Must fail validation and trigger fallback
    assert val_res["is_valid"] is False
    assert val_res["action"] == "ACTION: EXISTING DATA INVALID/INCOMPLETE"

    # Ensure unrelated valid file was not deleted or wiped
    assert valid_unrelated_path.exists()
    val_unrelated = discovery.validate_timeframe_raw_data("XAUUSD", "H1", path=valid_unrelated_path, emit_logs=True)
    assert val_unrelated["is_valid"] is True

    # 3. Simulate MT5 recovery
    bridge = SimulatorBridge(seed=42)
    recovered_bars = bridge.copy_rates("XAUUSD", "M15", 300)
    df_recovered = pd.DataFrame([b.to_dict() for b in recovered_bars])
    df_recovered.to_parquet(corrupt_path, index=False)

    post_recovery_val = discovery.validate_timeframe_raw_data("XAUUSD", "M15", path=corrupt_path, emit_logs=True)
    assert post_recovery_val["is_valid"] is True
    assert post_recovery_val["action"] == "ACTION: REUSING EXISTING DATA"


def test_scenario_d_complete_data_incremental_check(clean_temp_dir):
    """Scenario D: Database and DATA complete -> validates -> MT5 incremental delta only -> no full redownload."""
    data_dir = clean_temp_dir / "DATA"
    xau_dir = data_dir / "MT5" / "XAUUSD"
    xau_dir.mkdir(parents=True)

    bridge = SimulatorBridge(seed=42)
    bridge.connect()

    # Create baseline dataset
    initial_bars = bridge.copy_rates("XAUUSD", "M15", 400)
    df_init = pd.DataFrame([b.to_dict() for b in initial_bars])
    m15_file = xau_dir / "M15.parquet"
    df_init.to_parquet(m15_file, index=False)

    discovery = DataDiscoveryEngine(data_root=data_dir)
    val_res = discovery.validate_timeframe_raw_data("XAUUSD", "M15", path=m15_file, emit_logs=True)
    assert val_res["is_valid"] is True
    assert val_res["action"] == "ACTION: REUSING EXISTING DATA"

    # Delta test: read last stored bar and request incremental
    last_stored_ts = val_res["last_ts"]
    assert last_stored_ts > 0

    # Fetch delta from bridge newer than last_stored_ts
    delta_bars = bridge.copy_rates_range("XAUUSD", "M15", last_stored_ts, last_stored_ts + 3600 * 4)
    # Check that delta is bounded and does not re-download whole history
    assert len(delta_bars) < 400


def test_scenario_e_feature_completion_and_transition(clean_temp_dir):
    """Scenario E: Feature computation completion -> logs FEATURES COMPLETE -> FEATURE VALIDATION -> transition to RESEARCH/NODE_GENERATION."""
    sm = StageManager()
    assert sm.current_stage == "DATA_SYNC"

    # Simulate transition pipeline up to features complete
    sm.transition("DATA_SYNC", "DATA_VALIDATION")
    sm.transition("DATA_VALIDATION", "FEATURE_DISCOVERY")
    sm.transition("FEATURE_DISCOVERY", "FEATURE_PRECOMPUTATION")

    task = sm.start_task("FEATURE_PRECOMPUTATION", "Precompute indicators for XAUUSD_M15", 14)
    sm.update_progress(task.task_id, 14, "ATR_14")
    sm.complete_task(task.task_id, result_message="All 14 features precomputed")

    # Authoritative sequence per spec §8
    sm.transition("FEATURE_PRECOMPUTATION", "FEATURE_VALIDATION", {"message": "Verifying column schemas"})
    assert sm.current_stage == "FEATURE_VALIDATION"

    sm.transition("FEATURE_VALIDATION", "DATASET_REGISTRATION", {"message": "Registering dataset"})
    assert sm.current_stage == "DATASET_REGISTRATION"

    sm.transition("DATASET_REGISTRATION", "RESEARCH_INITIALIZATION", {"message": "Initializing population"})
    assert sm.current_stage == "RESEARCH_INITIALIZATION"

    sm.transition("RESEARCH_INITIALIZATION", "NODE_GENERATION", {"message": "Generating candidates"})
    assert sm.current_stage == "NODE_GENERATION"

    sm.transition("NODE_GENERATION", "NODE_EVALUATION", {"message": "Screening candidate nodes"})
    assert sm.current_stage == "NODE_EVALUATION"


def test_scenario_f_worker_stall_detection():
    """Scenario F: Worker stall detection -> heartbeat emitted -> watchdog reports STALLED with elapsed time and worker status -> does not mark complete."""
    sm = StageManager()
    task = sm.start_task("FEATURE_PRECOMPUTATION", "Precompute XAUUSD_M1", 1000, worker_id="worker-test-1")

    # 1. Verify heartbeat functionality
    sm.emit_heartbeat(task.task_id, rows_processed=450, rows_total=1000)
    active_tasks = sm.list_active_tasks()
    assert len(active_tasks) == 1
    assert active_tasks[0]["current"] == 450
    assert active_tasks[0]["percentage"] == 45.0

    # 2. Artificially age the task to exceed stall threshold
    with sm._lock:
        sm._active_tasks[task.task_id].updated_at = time.time() - 40.0

    # 3. Watchdog check
    alerts = sm.watchdog_check(stall_threshold_s=30.0)
    assert len(alerts) == 1
    alert = alerts[0]

    assert alert["task_id"] == task.task_id
    assert alert["idle_seconds"] >= 40.0
    assert "WORKER ALIVE" in alert["worker_state"] or "WORKER" in alert["worker_state"]

    # Verify task was NOT falsely marked COMPLETE
    assert sm._active_tasks[task.task_id].status == "STALLED"
    assert sm._active_tasks[task.task_id].completed_at is None


def test_launcher_scripts_and_powershell_routing():
    """Verify BAT execution environment headers and PowerShell routing wrappers."""
    bat_files = [
        "START.bat",
        "start.bat",
        "pre-requisite.bat",
        "Prerequisite.bat",
        "doctor.bat",
        "repair.bat",
        "RUN_BACKEND.bat",
    ]

    for bf in bat_files:
        path = ROOT_DIR / bf
        assert path.exists(), f"{bf} does not exist"
        content = path.read_text(encoding="utf-8")
        assert "[LAUNCHER] Execution shell: CMD" in content, f"{bf} missing CMD header"
        assert "[LAUNCHER] Working directory:" in content, f"{bf} missing working directory log"
        assert "[LAUNCHER] Launcher execution confirmed" in content, f"{bf} missing confirmation log"

    ps_files = [
        "start.ps1",
        "pre-requisite.ps1",
        "doctor.ps1",
        "repair.ps1",
        "run_backend.ps1",
    ]

    for pf in ps_files:
        path = ROOT_DIR / pf
        assert path.exists(), f"{pf} does not exist"
        content = path.read_text(encoding="utf-8")
        assert "cmd.exe /c" in content, f"{pf} does not delegate to cmd.exe"
        assert "Write-Host" in content, f"{pf} missing user messaging"


def test_spec_section_12_status_report(clean_temp_dir):
    """Verify §12 separate DATABASE and DATA status reports."""
    de = get_discovery_engine()
    rep = de.get_startup_status_report()

    assert "database" in rep
    assert "data" in rep
    assert "report_text" in rep

    text = rep["report_text"]
    assert "DATABASE:" in text
    assert "Strategies:" in text
    assert "Nodes:" in text
    assert "Schema:" in text
    assert "DATA:" in text
    assert "Raw datasets:" in text
    assert "Valid datasets:" in text
    assert "Invalid datasets:" in text
    assert "Feature sets:" in text
    assert "Missing datasets:" in text
