"""
Test Suite for V2.9: Dashboard Freeze, Backend Non-Blocking & Stability Repairs.

Validates:
  1. Startup status report TTL caching (no physical parquet thrashing on rapid polls).
  2. Cache invalidation on data changes.
  3. EvolutionEngine node generation state TTL caching.
  4. Entropy salting on duplicate collisions in seed_population (prevents infinite starvation loops).
  5. Asynchronous Data Clearance with job_id return and dual confirmation ('CLEAR ALL DATA' and 'DELETE THE DATA').
  6. COPY LOG independence from SQLite locks and lab state contention.
  7. Single-Instance PID Lease Manager & Port 8787 Pre-Bind Check.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.app.activity import activity
from backend.app.data.discovery import DataDiscoveryEngine, get_discovery_engine
from backend.app.db.database import Database, get_db
from backend.app.evolution.engine import EvolutionEngine
from backend.app.lease import PidLeaseManager, check_port_free, is_pid_alive
from backend.app.main import app
from backend.app.orchestrator.pipeline_state import get_pipeline_state_manager
from backend.app.orchestrator.stages import StageManager


@pytest.fixture
def test_env(tmp_path, monkeypatch):
    data_dir = tmp_path / "DATA"
    cache_dir = tmp_path / "CACHE"
    logs_dir = tmp_path / "LOGS"
    db_file = tmp_path / "test_lab.db"

    data_dir.mkdir(parents=True)
    cache_dir.mkdir(parents=True)
    logs_dir.mkdir(parents=True)
    (data_dir / "MT5" / "XAUUSD").mkdir(parents=True)
    (data_dir / "features").mkdir(parents=True)
    (data_dir / "manifests").mkdir(parents=True)

    db = Database(str(db_file))

    # Monkeypatch paths
    from backend.app import paths as P
    monkeypatch.setattr(P, "DATA_ROOT", data_dir)
    monkeypatch.setattr(P, "DATA_DIR", data_dir)
    monkeypatch.setattr(P, "CACHE_ROOT", cache_dir)
    monkeypatch.setattr(P, "LOGS_DIR", logs_dir)
    monkeypatch.setattr(P, "MT5_XAUUSD_DIR", data_dir / "MT5" / "XAUUSD")
    monkeypatch.setattr("backend.app.api.routes.get_db", lambda: db)

    disc = DataDiscoveryEngine(data_root=data_dir)

    return {
        "data": data_dir,
        "cache": cache_dir,
        "logs": logs_dir,
        "db": db,
        "disc": disc,
    }


def _create_sample_parquet(path: Path, rows: int = 100):
    path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    df = pd.DataFrame({
        "ts": [now - (rows - i) * 60.0 for i in range(rows)],
        "open": [2000.0 + i * 0.1 for i in range(rows)],
        "high": [2001.0 + i * 0.1 for i in range(rows)],
        "low": [1999.0 + i * 0.1 for i in range(rows)],
        "close": [2000.5 + i * 0.1 for i in range(rows)],
    })
    df.to_parquet(path, index=False)


def test_startup_status_report_ttl_caching(test_env):
    """Test 1: Startup status report is cached for 20s and avoids disk thrashing."""
    disc = test_env["disc"]
    db = test_env["db"]
    xau_dir = test_env["data"] / "MT5" / "XAUUSD"

    for tf in ["M1", "M5", "M15", "M30", "H1"]:
        _create_sample_parquet(xau_dir / f"{tf}.parquet", rows=50)

    # First call validates from disk
    t0 = time.perf_counter()
    rep1 = disc.get_startup_status_report(db, force_refresh=True)
    t1 = time.perf_counter()
    dur_disk = t1 - t0

    assert rep1["data"]["valid_datasets"] == 5

    # Second call uses TTL cache (sub-millisecond)
    t2 = time.perf_counter()
    rep2 = disc.get_startup_status_report(db, force_refresh=False)
    t3 = time.perf_counter()
    dur_cache = t3 - t2

    assert rep2["data"]["valid_datasets"] == 5
    assert dur_cache < dur_disk
    assert dur_cache < 0.005  # < 5ms


def test_startup_status_report_cache_invalidation(test_env):
    """Test 2: Startup status cache is properly invalidated on demand."""
    disc = test_env["disc"]
    db = test_env["db"]
    xau_dir = test_env["data"] / "MT5" / "XAUUSD"

    for tf in ["M1", "M5"]:
        _create_sample_parquet(xau_dir / f"{tf}.parquet", rows=50)

    rep1 = disc.get_startup_status_report(db, force_refresh=True)
    assert rep1["data"]["valid_datasets"] == 2

    # Add remaining datasets
    for tf in ["M15", "M30", "H1"]:
        _create_sample_parquet(xau_dir / f"{tf}.parquet", rows=50)

    # Cached call still returns old count
    rep_cached = disc.get_startup_status_report(db, force_refresh=False)
    assert rep_cached["data"]["valid_datasets"] == 2

    # Invalidate cache
    disc.invalidate_startup_status_cache()
    rep_refreshed = disc.get_startup_status_report(db, force_refresh=False)
    assert rep_refreshed["data"]["valid_datasets"] == 5


def test_node_generation_state_ttl_cache(test_env):
    """Test 3: EvolutionEngine.get_node_generation_state() caches result for rapid polling."""
    db = test_env["db"]
    evo = EvolutionEngine(db, run_id="RUN-TEST-CACHE")
    evo.set_total_node_target(500)

    t0 = time.perf_counter()
    st1 = evo.get_node_generation_state(force=True)
    t1 = time.perf_counter()

    # Second call within 1.0s returns immediately
    t2 = time.perf_counter()
    st2 = evo.get_node_generation_state(force=False)
    t3 = time.perf_counter()

    assert st1["target_nodes"] == 500
    assert st2["target_nodes"] == 500
    assert (t3 - t2) < 0.005  # < 5ms


def test_entropy_duplicate_salting_no_starvation(test_env):
    """Test 4: seed_population injects entropy on collision, guaranteeing novel generation."""
    db = test_env["db"]
    evo = EvolutionEngine(db, run_id="RUN-TEST-ENTROPY")
    evo.set_total_node_target(100)

    # First batch of 20
    n1 = evo.seed_population(20, "XAUUSD")
    assert n1 == 20
    assert evo.total_nodes() == 20

    # Second batch of 20 (must generate novel candidates despite pre-existing 20 hashes)
    n2 = evo.seed_population(20, "XAUUSD")
    assert n2 == 20
    assert evo.total_nodes() == 40

    # Check distinct hashes
    hashes = [r["hash"] for r in db.q("SELECT hash FROM strategies")]
    assert len(hashes) == 40
    assert len(set(hashes)) == 40


def test_async_clear_endpoints_and_dual_confirmation(test_env):
    """Test 5: Clear endpoints execute asynchronously, return job_id, and support dual confirmation."""
    client = TestClient(app)
    xau_dir = test_env["data"] / "MT5" / "XAUUSD"
    m15_p = xau_dir / "M15.parquet"
    _create_sample_parquet(m15_p, rows=50)

    # 1. Invalid confirmation fails
    bad = client.post("/api/data/clear/all", json={"confirm": "INVALID"})
    assert bad.status_code == 400

    # 2. Confirmation with 'DELETE THE DATA' succeeds
    del_res = client.post("/api/data/clear/all", json={"confirm": "DELETE THE DATA"})
    assert del_res.status_code == 200
    data_del = del_res.json()
    assert data_del["ok"] is True
    assert "job_id" in data_del
    assert not m15_p.exists()

    # Re-create and test 'CLEAR ALL DATA'
    _create_sample_parquet(m15_p, rows=50)
    clear_res = client.post("/api/data/clear/all", json={"confirm": "CLEAR ALL DATA"})
    assert clear_res.status_code == 200
    data_clear = clear_res.json()
    assert data_clear["ok"] is True
    assert "job_id" in data_clear
    assert not m15_p.exists()


def test_copy_log_independent_of_db_locks():
    """Test 6: generate_diagnostic_log operates in microseconds without DB queries or locks."""
    t0 = time.perf_counter()
    log_text = activity.generate_diagnostic_log(limit=100)
    t1 = time.perf_counter()

    assert "EVOLUTIONARY TRADING RESEARCH LAB" in log_text
    assert "SYSTEM RESOURCE MONITOR" in log_text
    assert (t1 - t0) < 0.05  # < 50ms (pure memory operation)


def test_pid_lease_manager_and_port_prebind(tmp_path):
    """Test 7: PidLeaseManager enforces single instance and cleans up stale/dead leases."""
    pid_file = tmp_path / "test_lab.pid"
    lease_mgr = PidLeaseManager(pid_file=pid_file, port=8787)

    # 1. Acquire lease
    ok, msg = lease_mgr.acquire()
    assert ok is True
    assert pid_file.exists()
    lease_data = json.loads(pid_file.read_text(encoding="utf-8"))
    assert lease_data["pid"] == os.getpid()

    # 2. Heartbeat update
    lease_mgr.update_heartbeat()

    # 3. Same PID acquiring is fine (re-entrant/idempotent)
    ok2, _ = lease_mgr.acquire()
    assert ok2 is True

    # 4. Another process check: simulate dead PID
    fake_lease = {"pid": 9999999, "port": 8787, "started_at": time.time()}
    pid_file.write_text(json.dumps(fake_lease), encoding="utf-8")
    assert not is_pid_alive(9999999)

    # Overwrites stale dead PID lease
    ok3, _ = lease_mgr.acquire()
    assert ok3 is True
    new_data = json.loads(pid_file.read_text(encoding="utf-8"))
    assert new_data["pid"] == os.getpid()

    # 5. Release lease
    lease_mgr.release()
    assert not pid_file.exists()
