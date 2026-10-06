"""
Comprehensive tests for MT5 Data Synchronization Fix & Live Activity Monitor:
1. NumPy structured array rate conversion (no ambiguous truth evaluation error)
2. Safe handling of None, empty array, single row, multi-row arrays
3. Connection monitor Path definition and status_details keys
4. Activity event model (timestamp, level, category, status, operation_id, progress, details)
5. Current task tracking with started_at and elapsed time
6. Immediate failure reporting with reason, operation_id, platform, next_action
7. No silent fallback to SIMULATOR when bridge is MT5_REAL
8. Independent timeframe synchronization (one failure does not kill remaining)
9. Database and Evolution Tree preservation
"""
import time
from pathlib import Path
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.activity import activity
from app.mt5.mt5_real import MT5RealBridge
from app.mt5.monitor import get_connection_monitor
from app.mt5.factory import get_bridge
from app.data.engine import get_data_engine
from backend.app.paths import ROOT_DIR


def test_numpy_structured_array_conversion_no_truth_error():
    """Validates that multi-row NumPy structured arrays from MT5 convert without boolean ambiguity errors."""
    bridge = MT5RealBridge()

    dtype = [
        ('time', '<i8'), ('open', '<f8'), ('high', '<f8'), ('low', '<f8'),
        ('close', '<f8'), ('tick_volume', '<u8'), ('spread', '<i4'), ('real_volume', '<u8')
    ]

    # 1. Multi-row array (what previously triggered 'truth value of array is ambiguous')
    rows_multi = np.array([
        (1700000000, 2000.0, 2010.0, 1995.0, 2005.0, 100, 15, 50),
        (1700000060, 2005.0, 2015.0, 2002.0, 2012.0, 120, 16, 60),
        (1700000120, 2012.0, 2018.0, 2008.0, 2015.0, 90, 14, 40),
    ], dtype=dtype)

    bars = bridge._rows_to_bars(rows_multi)
    assert len(bars) == 3
    assert bars[0].ts == 1700000000.0
    assert bars[0].close == 2005.0
    assert bars[0].open == 2000.0
    assert bars[0].spread == 15.0
    assert bars[0].tick_volume == 100
    assert bars[2].close == 2015.0

    # 2. Single-row array
    rows_single = np.array([(1700000000, 2000.0, 2010.0, 1995.0, 2005.0, 100, 15, 50)], dtype=dtype)
    bars_s = bridge._rows_to_bars(rows_single)
    assert len(bars_s) == 1

    # 3. Empty structured array
    rows_empty = np.array([], dtype=dtype)
    assert bridge._rows_to_bars(rows_empty) == []

    # 4. None
    assert bridge._rows_to_bars(None) == []


def test_mt5_monitor_path_defined_and_status_keys():
    """Validates that Path is properly imported and monitor tick executes without NameError."""
    mon = get_connection_monitor()
    assert mon is not None

    # Run tick explicitly
    mon._tick()

    st = mon.status_details()
    assert "internet" in st
    assert "mt5" in st
    assert "data_feed" in st
    details = st.get("details", {})
    assert "terminal_detected" in details
    assert "terminal_available" in details
    assert "account_available" in details
    assert "platform_mode" in details
    assert "last_heartbeat_ts" in details
    assert "reconnect_failures" in details
    assert "latency_ms" in details


def test_activity_event_model_and_current_task():
    """Validates structured activity events, current task tracking, and history limits."""
    # 1. Started task
    op_id = "test_sync_op_1"
    ev_start = activity.started("DATA", "Starting XAUUSD M1 synchronization", operation_id=op_id, progress=10.0)
    assert ev_start["status"] == "STARTED"
    assert ev_start["category"] == "DATA"
    assert ev_start["operation_id"] == op_id
    assert "timestamp" in ev_start
    assert "time_str" in ev_start

    task = activity.get_current_task()
    assert task["status"] == "RUNNING"
    assert task["name"] == "Starting XAUUSD M1 synchronization"
    assert task["operation_id"] == op_id
    assert task["started_at"] is not None

    # 2. Running task with progress
    ev_run = activity.running("DATA", "Validating timestamps for XAUUSD M1", operation_id=op_id, progress=65.0)
    assert ev_run["status"] == "RUNNING"
    assert ev_run["progress"] == 65.0

    task2 = activity.get_current_task()
    assert task2["progress"] == 65.0

    # 3. Success completion
    ev_ok = activity.success("DATA", "XAUUSD M1 synchronization complete", operation_id=op_id, progress=100.0)
    assert ev_ok["status"] == "SUCCESS"
    task3 = activity.get_current_task()
    assert task3["status"] == "IDLE"

    # 4. Failed task with technical reason
    fail_op = "test_sync_fail"
    ev_fail = activity.failed(
        "DATA",
        "XAUUSD H1 synchronization failed",
        operation_id=fail_op,
        reason="The truth value of an array with more than one element is ambiguous.",
        platform="MT5 REAL",
        next_action="Synchronization stopped for this timeframe."
    )
    assert ev_fail["status"] == "FAILED"
    assert ev_fail["level"] == "ERROR"
    assert ev_fail["details"]["reason"] == "The truth value of an array with more than one element is ambiguous."
    assert ev_fail["details"]["platform"] == "MT5 REAL"
    assert ev_fail["details"]["next_action"] == "Synchronization stopped for this timeframe."

    task_failed = activity.get_current_task()
    assert task_failed["status"] == "FAILED"
    assert "failed" in task_failed["name"]


def test_activity_recent_history_bounded():
    """Recent activity events must be bounded to at most 500 in memory."""
    for i in range(550):
        activity.info("SYSTEM", f"Test event #{i}")

    recent = activity.recent(limit=600)
    assert len(recent) <= 500


def test_no_silent_fallback_to_simulator_when_require_real(monkeypatch):
    """When require_real=True, latest_dataset must never return a SIMULATOR dataset."""
    de = get_data_engine()

    # Query with require_real=True
    # If no MT5 real dataset exists in test environment, it must return None rather than a SIMULATOR dataset
    res = de.latest_dataset("XAUUSD", "M15", require_real=True)
    if res is not None:
        assert res.get("source") != "SIMULATOR"


def test_independent_timeframes_in_ingest_default(monkeypatch):
    """Failure in one timeframe must not kill the remaining timeframes in ingest_default."""
    de = get_data_engine()
    from app.config import get_config
    cfg = get_config()
    monkeypatch.setattr(cfg.data, "timeframes", ["M1", "M5", "M15"])

    call_counts = []

    def mock_sync_master(sym, tf, force_full=False):
        call_counts.append(tf)
        if tf == "M1":
            raise ValueError("Simulated M1 failure")
        return {"symbol": sym, "timeframe": tf, "dataset_id": f"test_{sym}_{tf}", "new_bars": 100}

    monkeypatch.setattr(de, "sync_master", mock_sync_master)

    results = de.ingest_default()
    # Verify all configured timeframes were attempted even though M1 failed
    assert len(results) == 3
    assert "M1" in call_counts
    assert "M5" in call_counts
    assert "M15" in call_counts
    m1_res = next(r for r in results if r.get("timeframe") == "M1")
    assert m1_res.get("error") == "Simulated M1 failure"
    assert m1_res.get("dataset_id") is None

    # Other timeframes succeeded
    other_res = [r for r in results if r.get("timeframe") != "M1"]
    assert len(other_res) == 2
    for r in other_res:
        assert r.get("dataset_id") is not None


def test_api_activity_and_current_task_endpoints():
    """Validates GET /api/activity and GET /api/activity/current_task."""
    client = TestClient(app)

    # 1. GET /api/activity
    res = client.get("/api/activity?limit=20")
    assert res.status_code == 200
    events = res.json()
    assert isinstance(events, list)

    # 2. GET /api/activity/current_task
    res_task = client.get("/api/activity/current_task")
    assert res_task.status_code == 200
    task_data = res_task.json()
    assert "name" in task_data
    assert "status" in task_data

    # 3. GET /api/status includes current_task
    res_status = client.get("/api/status")
    assert res_status.status_code == 200
    st_data = res_status.json()
    assert "current_task" in st_data
    assert "bridge" in st_data


def test_database_and_tree_preserved():
    """Verify lab_state.db strategies (>=371) and Evolution Tree components intact."""
    import sqlite3
    db_path = ROOT_DIR / "DATABASE" / "lab_state.db"
    assert db_path.exists()
    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()
    c.execute("PRAGMA user_version")
    assert c.fetchone()[0] == 3
    c.execute("SELECT count(*) FROM strategies")
    assert c.fetchone()[0] >= 371
    conn.close()

    tree_path = ROOT_DIR / "frontend" / "src" / "pages" / "EvolutionTree.jsx"
    assert tree_path.exists()
    content = tree_path.read_text(encoding="utf-8")
    assert "EvoNode" in content
    assert "Handle" in content
    assert "smoothstep" in content
