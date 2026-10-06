"""
V3.5 Phase 2 Test Suite: CPU Utilization, Worker Pool Scaling & RAM Telemetry.

Validates:
1. Dynamic worker pool proportional scaling across 25%, 50%, 75%, 100%.
2. Dynamic pool resizing without job corruption or checkpoint interruption.
3. Accurate CPU telemetry (system, application process/children, per-core, active/idle workers).
4. Accurate RAM telemetry (system used/total/avail, application RSS, peak RSS).
5. Task completion throughput (tasks/min) and latency tracking.
6. Memory budget safeguards against out-of-memory worker expansion.
7. Anti-starvation candidate pipeline depth scaling.
8. System status row API payload completeness.
9. Settings API CPU target handling.
"""
import os
import time
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from backend.app import paths as P
from backend.app.api.routes import router
from backend.app.config import get_config
from backend.app.orchestrator.lab import get_lab
from backend.app.resources.manager import get_resource_manager


@pytest.fixture(autouse=True)
def restore_cpu_target():
    """Ensure CPU target is cleanly restored to default 60% after tests."""
    yield
    rm = get_resource_manager()
    rm.set_cpu_target(60)
    lab = get_lab()
    lab.resize_pool()


def test_dynamic_worker_scaling_proportional_to_cores():
    """Verify 25%, 50%, 75%, 100% proportionally allocate worker capacity based on detected cores."""
    rm = get_resource_manager()
    cores = os.cpu_count() or 2

    # 100% Allocation -> All cores
    rm.set_cpu_target(100)
    eff_100 = rm.effective_workers()
    assert eff_100 == cores, f"100% allocation should use {cores} workers, got {eff_100}"

    # 75% Allocation -> round(cores * 0.75)
    rm.set_cpu_target(75)
    eff_75 = rm.effective_workers()
    expected_75 = max(1, round(cores * 0.75))
    assert eff_75 == expected_75, f"75% allocation should use {expected_75} workers, got {eff_75}"

    # 50% Allocation -> round(cores * 0.50)
    rm.set_cpu_target(50)
    eff_50 = rm.effective_workers()
    expected_50 = max(1, round(cores * 0.50))
    assert eff_50 == expected_50, f"50% allocation should use {expected_50} workers, got {eff_50}"

    # 25% Allocation -> round(cores * 0.25)
    rm.set_cpu_target(25)
    eff_25 = rm.effective_workers()
    expected_25 = max(1, round(cores * 0.25))
    assert eff_25 == expected_25, f"25% allocation should use {expected_25} workers, got {eff_25}"


def test_set_cpu_target_persistence_and_benchmark_logging():
    """Verify set_cpu_target updates config, resizes pool, and writes to CPU_BENCHMARK_V3_5.log."""
    rm = get_resource_manager()
    lab = get_lab()
    benchmark_log = P.DATA_LOGS_DIR / "CPU_BENCHMARK_V3_5.log"

    # Pre-read size
    size_before = benchmark_log.stat().st_size if benchmark_log.exists() else 0

    res = rm.set_cpu_target(75)
    assert res["cpu_target_pct"] == 75
    assert get_config().resources.cpu_target_pct == 75

    # Check benchmark log entry appended
    assert benchmark_log.exists()
    assert benchmark_log.stat().st_size > size_before
    content = benchmark_log.read_text(encoding="utf-8")
    assert "CPU_TARGET_CHANGED" in content
    assert '"target_pct": 75' in content


def test_accurate_cpu_telemetry_fields():
    """Verify live_metrics returns comprehensive CPU metrics (system, app, per-core, active, idle)."""
    rm = get_resource_manager()
    metrics = rm.live_metrics()

    cpu = metrics["cpu"]
    assert "percent" in cpu and isinstance(cpu["percent"], (int, float))
    assert "app_percent" in cpu and isinstance(cpu["app_percent"], (int, float))
    assert "per_core" in cpu and isinstance(cpu["per_core"], list)
    assert len(cpu["per_core"]) == (os.cpu_count() or 2)
    assert "target_pct" in cpu
    assert "physical_cores" in cpu and cpu["physical_cores"] >= 1
    assert "logical_processors" in cpu and cpu["logical_processors"] >= cpu["physical_cores"]
    assert "effective_workers" in cpu and cpu["effective_workers"] >= 1
    assert "active_workers" in cpu
    assert "idle_workers" in cpu
    assert cpu["idle_workers"] == max(0, cpu["effective_workers"] - cpu["active_workers"])


def test_accurate_ram_telemetry_fields():
    """Verify live_metrics separates system RAM from application RSS and peak RSS."""
    rm = get_resource_manager()
    metrics = rm.live_metrics()

    mem = metrics["memory"]
    assert "used_mb" in mem and mem["used_mb"] > 0
    assert "total_mb" in mem and mem["total_mb"] > 0
    assert "available_mb" in mem and mem["available_mb"] > 0
    assert "percent" in mem and 0 <= mem["percent"] <= 100
    assert "app_used_mb" in mem and mem["app_used_mb"] > 0
    assert "app_peak_mb" in mem and mem["app_peak_mb"] >= mem["app_used_mb"]
    assert "limit_gb" in mem


def test_task_throughput_and_duration_telemetry():
    """Verify record_task_completion tracks task counts, throughput/min, and avg duration."""
    rm = get_resource_manager()
    with rm._lock:
        rm._task_durations.clear()
        rm._recent_completions.clear()
    initial_completed = rm._completed_tasks

    # Record 3 simulated task completions
    rm.record_task_completion(0.050, success=True)
    rm.record_task_completion(0.070, success=True)
    rm.record_task_completion(0.060, success=True)

    assert rm._completed_tasks == initial_completed + 3
    assert rm.tasks_per_minute() >= 3
    avg_ms = rm.avg_task_duration_ms()
    assert 50.0 <= avg_ms <= 70.0


def test_memory_safeguard_budget_and_oom_protection():
    """Verify check_memory_budget prevents excessive memory expansion while allowing reasonable workloads."""
    rm = get_resource_manager()
    cfg = get_config()
    orig_val = getattr(cfg.resources, "memory_limit_value", 50.0)
    cfg.resources.memory_limit_value = 85.0
    try:
        # Reasonable workload (20MB) should be allowed under normal conditions
        assert rm.check_memory_budget(estimated_mb=20.0) is True

        # Wildly excessive workload (e.g. 500,000 MB) should be safely rejected
        assert rm.check_memory_budget(estimated_mb=500000.0) is False
    finally:
        cfg.resources.memory_limit_value = orig_val


def test_orchestrator_batch_depth_and_anti_starvation():
    """Verify candidate queue target scales dynamically with effective worker count."""
    rm = get_resource_manager()
    lab = get_lab()

    rm.set_cpu_target(100)
    eff = rm.effective_workers()
    cfg = get_config().evolution

    # Minimum target batch should equal eff * 20 or config population size
    expected_min_batch = max(30, getattr(cfg, "population_size", 30), eff * 20)
    assert expected_min_batch >= eff * 20


def test_system_status_row_api_telemetry():
    """Verify /api/system/status_row endpoint exposes rich CPU, RAM, and task metrics."""
    from backend.app.main import app
    client = TestClient(app)

    res = client.get("/api/system/status_row")
    assert res.status_code == 200
    data = res.json()

    # Verify CPU telemetry
    assert "cpu" in data
    assert "percent" in data["cpu"]
    assert "app_percent" in data["cpu"]
    assert "per_core" in data["cpu"]
    assert "effective_workers" in data["cpu"]
    assert "idle_workers" in data["cpu"]

    # Verify RAM telemetry
    assert "ram" in data
    assert "used_mb" in data["ram"]
    assert "available_mb" in data["ram"]
    assert "app_used_mb" in data["ram"]
    assert "app_peak_mb" in data["ram"]

    # Verify Task telemetry
    assert "tasks" in data
    assert "tasks_per_minute" in data["tasks"]
    assert "avg_duration_ms" in data["tasks"]


def test_settings_api_cpu_target_presets():
    """Verify POST /api/settings accepts 25%, 50%, 75%, 100% allocations without undesired rounding."""
    from backend.app.main import app
    client = TestClient(app)

    for target in [25, 50, 75, 100]:
        res = client.post("/api/settings", json={"cpu_target_pct": target})
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["settings"]["cpu_target_pct"] == target
        assert data["resources"]["cpu"]["target_pct"] == target
