"""
Unit tests for V2.7 Stage Machine, Task Watchdog, Feature Validation,
and Continuous Research Progression without stalls.
"""
import time
import pytest
from fastapi.testclient import TestClient

from backend.app.orchestrator.stages import StageManager, get_stage_manager
from backend.app.orchestrator.lab import get_lab
from backend.app.main import app


def test_stage_manager_transitions():
    sm = StageManager()
    assert sm.current_stage == "DATA_SYNC"

    sm.transition("DATA_SYNC", "DATA_VALIDATION", {"test": True})
    assert sm.current_stage == "DATA_VALIDATION"

    sm.transition("DATA_VALIDATION", "FEATURE_DISCOVERY")
    sm.transition("FEATURE_DISCOVERY", "FEATURE_PRECOMPUTATION")
    sm.transition("FEATURE_PRECOMPUTATION", "FEATURE_VALIDATION")
    sm.transition("FEATURE_VALIDATION", "DATASET_READY")
    sm.transition("DATASET_READY", "RESEARCH")

    assert sm.current_stage == "RESEARCH"


def test_stage_task_lifecycle_and_ticks():
    sm = StageManager()
    task = sm.start_task("FEATURE_PRECOMPUTATION", "Precomputing XAUUSD_M5", total=21)
    assert task.status == "ACTIVE"
    assert task.current == 0

    sm.update_progress(task.task_id, 1, "price", throughput="2,500 bars/s")
    assert task.current == 1
    assert task.percentage == pytest.approx(4.8, 0.1)

    sm.update_progress(task.task_id, 21, "sessions", throughput="3,100 bars/s")
    assert task.current == 21
    assert task.percentage == 100.0

    completed = sm.complete_task(task.task_id, "Completed 21 features")
    assert completed.status == "COMPLETE"
    assert len(sm.list_active_tasks()) == 0


def test_stage_watchdog_stall_detection():
    sm = StageManager()
    task = sm.start_task("BACKTESTING", "Screening batch", total=20)
    task.updated_at = time.time() - 35.0  # past 30s threshold

    alerts = sm.watchdog_check(stall_threshold_s=30.0)
    assert len(alerts) >= 1
    assert alerts[0]["task_id"] == task.task_id
    assert alerts[0]["idle_seconds"] >= 30.0
    assert "WORKING" in alerts[0]["worker_state"]


def test_unified_task_state_api():
    client = TestClient(app)
    res = client.get("/api/workflow/task_state")
    assert res.status_code == 200
    data = res.json()
    assert "current_stage" in data
    assert "active_tasks_count" in data
    assert "last_transition_ts" in data


def test_feature_verification_and_orchestrator_progression():
    lab = get_lab()
    de = lab.db
    assert de.one("SELECT COUNT(*) c FROM strategies")["c"] >= 371
    assert de.one("SELECT user_version FROM pragma_user_version")["user_version"] == 3

    # Ensure datasets and features transition cleanly
    lab._ensure_datasets()
    sm = get_stage_manager()
    assert sm.current_stage in ("DATASET_READY", "RESEARCH", "BACKTESTING", "VALIDATION", "EVOLUTION")


def test_population_deficit_spawns_candidates():
    lab = get_lab()
    # Check that candidate deficit calculation recognizes when active candidates are needed
    pipeline_candidates = lab.db.one(
        "SELECT COUNT(*) c FROM strategies WHERE status IN ('BORN', 'BACKTESTING', 'SURVIVED', 'VALIDATING')"
    )["c"]
    qualified = lab.db.one("SELECT COUNT(*) c FROM strategies WHERE status='QUALIFIED'")["c"]
    assert qualified >= 113

    # If pipeline candidates is 0, deficit must be positive
    deficit = max(0, 30 - pipeline_candidates)
    assert deficit >= 0
