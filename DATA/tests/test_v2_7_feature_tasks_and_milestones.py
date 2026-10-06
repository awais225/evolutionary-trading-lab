"""
Unit tests for V2.7 Feature Tasks state machine, watchdog stall detection,
milestones route path, and status row API.
"""
import time
import pytest
from fastapi.testclient import TestClient

from backend.app.features.tasks import FeatureTask, FeatureTaskManager, get_feature_task_manager
from backend.app.orchestrator.milestones import MilestoneManager, get_milestone_manager
from backend.app.main import app


def test_feature_task_lifecycle():
    mgr = FeatureTaskManager()
    task, created = mgr.create_or_attach("XAUUSD_M5", "M5", ["rsi:14", "ema:20", "sma:20"], worker_id="worker-test")
    assert created is True
    assert task.status == "QUEUED"
    assert task.progress_pct == 0.0

    task.start(rows_total=5000)
    assert task.status == "RUNNING"

    task.start_feature("rsi:14")
    assert task.current_feature == "rsi:14"
    task.complete_feature("rsi:14", rows=5000)
    assert task.completed_features == 1
    assert task.progress_pct == pytest.approx(33.3, 0.1)

    hb = task.heartbeat()
    assert hb["task_id"] == task.task_id
    assert hb["gpu_processing"] in ("IDLE", "ACTIVE")

    task.complete_feature("ema:20", rows=5000)
    task.complete_feature("sma:20", rows=5000)
    assert task.completed_features == 3
    assert task.progress_pct == 100.0

    task.complete(reused=False)
    assert task.status == "COMPLETED"


def test_feature_task_reused():
    mgr = FeatureTaskManager()
    task, created = mgr.create_or_attach("XAUUSD_H1", "H1", ["price", "time"], worker_id="cached")
    assert created is True
    task.complete(reused=True)
    assert task.status == "REUSED"
    assert task.progress_pct == 100.0


def test_feature_task_stall_detection():
    task = FeatureTask(
        task_id="FP-STALL-TEST",
        dataset_id="XAUUSD_M1",
        timeframe="M1",
        total_features=5,
        feature_specs=["f1", "f2"],
        stall_threshold_s=0.1,  # short threshold for test
    )
    task.start(rows_total=1000)
    task.start_feature("f1")

    # Initially not stalled
    is_stalled, _ = task.check_stall()
    assert is_stalled is False

    # Wait past threshold
    time.sleep(0.15)
    is_stalled, idle_s = task.check_stall()
    assert is_stalled is True
    assert task.status == "STALLED"
    assert idle_s >= 0.1

    # Heartbeat recovers from stalled
    task.heartbeat()
    assert task.status == "RUNNING"


def test_milestone_route_state():
    mm = MilestoneManager()
    state = mm.compute_route_state()
    assert len(state["milestones"]) == 7
    assert state["milestones"][0]["id"] == "system"
    assert state["milestones"][0]["status"] == "COMPLETE"
    assert state["fill_percentage"] >= 0.0

    # Advance milestones
    mm.set_running("data_sync", "Ingesting Parquet data")
    state2 = mm.compute_route_state()
    assert state2["active_milestone"]["id"] in ("system", "mt5", "data_sync")

    mm.set_complete("data_sync", "5 Datasets Verified")
    mm.set_subprocess("feature_precompute", "XAUUSD_M5", {"status": "COMPLETE", "progress": 100.0})
    mm.set_complete("feature_precompute", "All features cached")

    state3 = mm.compute_route_state()
    # At least system, mt5, data_sync, feature_precompute complete
    assert state3["fill_percentage"] > 25.0


def test_api_system_status_row():
    client = TestClient(app)
    res = client.get("/api/system/status_row")
    assert res.status_code == 200
    data = res.json()
    assert "wifi" in data
    assert "mt5" in data
    assert "cpu" in data
    assert "gpu" in data
    assert "ram" in data
    assert "data" in data
    assert "db" in data
    assert "research" in data
    assert "progress" in data
    assert data["db"]["user_version"] == 3
    assert data["db"]["total_strategies"] >= 371
    assert data["gpu"]["processing_state"] in ("IDLE", "ACTIVE")


def test_api_workflow_milestones():
    client = TestClient(app)
    res = client.get("/api/workflow/milestones")
    assert res.status_code == 200
    data = res.json()
    assert "milestones" in data
    assert "active_index" in data
    assert "fill_percentage" in data
    assert len(data["milestones"]) == 7
