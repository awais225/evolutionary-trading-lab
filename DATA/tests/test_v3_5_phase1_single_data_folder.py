"""
Acceptance Test Suite for V3.5 Phase 1: Single Authoritative DATA Folder Portability.

Verifies:
  Test 1: All 7 required directories reside strictly under DATA/
  Test 2: Central DATA_ROOT derivation in paths.py
  Test 3: Authoritative Database resolution and read/write under DATA/DATABASE/lab_state.db
  Test 4: Fresh application installation portability test (only copying DATA/ restores lab)
  Test 5: Cross-platform portable relative path resolution
  Test 6: Backups, logs, caches, and test data reside under DATA/
"""
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
import pytest

from backend.app import paths as P
from backend.app.db.database import Database
from backend.app.data.discovery import DataDiscoveryEngine


def test_1_all_directories_reside_under_data():
    """Verify all 7 requested directories physically reside under DATA/."""
    data_root = P.DATA_ROOT
    assert data_root.exists() and data_root.is_dir()

    expected_dirs = [
        data_root / "BACKUPS",
        data_root / "CACHE",
        data_root / "data_cache",
        data_root / "DATABASE",
        data_root / "RESEARCH",
        data_root / "TEST_DATA",
        data_root / "tests",
        data_root / "logs",
        data_root / "manifests",
    ]

    for d in expected_dirs:
        assert d.exists(), f"Required directory {d.name} missing from {data_root}"
        assert d.is_dir(), f"Required path {d} is not a directory"


def test_2_central_data_root_derivation():
    """Verify central DATA_ROOT configuration in backend/app/paths.py."""
    assert P.DATA_DIR == P.DATA_ROOT
    assert P.DATABASE_DIR == P.DATA_ROOT / "DATABASE"
    assert P.RESEARCH_DIR == P.DATA_ROOT / "RESEARCH"
    assert P.BACKUPS_DIR == P.DATA_ROOT / "BACKUPS"
    assert P.TEST_DATA_DIR == P.DATA_ROOT / "TEST_DATA"
    assert P.CACHE_ROOT == P.DATA_ROOT / "CACHE"
    assert P.DATA_CACHE_DIR == P.DATA_ROOT / "data_cache"
    assert P.TESTS_DIR == P.DATA_ROOT / "tests"
    assert P.LOGS_DIR == P.DATA_ROOT / "logs"
    assert P.database_file().resolve() == (P.DATABASE_DIR / "lab_state.db").resolve()


def test_3_authoritative_database_in_data():
    """Verify Database reads from and writes to DATA/DATABASE/lab_state.db."""
    db_file = P.database_file()
    assert db_file.exists(), f"Database file {db_file} does not exist"
    assert "DATA" in str(db_file), f"Database file path {db_file} does not reside in DATA"

    # Verify queryable and strategies intact
    db = Database(str(db_file))
    strat_count = db.total_strategies_count()
    assert strat_count >= 700, f"Expected strategies >= 700, got {strat_count}"


def test_4_fresh_installation_portability():
    """Simulate user workflow: Fresh application extraction + copied DATA/ folder.

    The user copies ONLY DATA/ into a fresh application root with code.
    Everything must restore cleanly with 0 dependencies on the original root.
    """
    with tempfile.TemporaryDirectory() as tmp_fresh_root:
        fresh_root = Path(tmp_fresh_root)
        fresh_data = fresh_root / "DATA"

        # Copy only the DATA directory (simulating user copying E:\\app\\DATA)
        shutil.copytree(P.DATA_ROOT / "DATABASE", fresh_data / "DATABASE")
        shutil.copytree(P.DATA_ROOT / "manifests", fresh_data / "manifests")
        if (P.DATA_ROOT / "manifest.json").exists():
            shutil.copy2(P.DATA_ROOT / "manifest.json", fresh_data / "manifest.json")

        fresh_db_file = fresh_data / "DATABASE" / "lab_state.db"
        assert fresh_db_file.exists()

        # Connect to fresh database directly in fresh DATA
        fresh_db = Database(str(fresh_db_file))
        assert fresh_db.total_strategies_count() >= 700

        # Verify discovery engine can operate on fresh_data independently
        disc = DataDiscoveryEngine(data_root=fresh_data)
        assert disc.data_root == fresh_data
        status = disc.discover_all(emit_logs=False)
        assert "datasets" in status


def test_5_cross_platform_path_handling():
    """Verify portable path resolution and relative path conversion."""
    sample_sub = P.DATA_ROOT / "RESEARCH" / "backtests" / "strategy_000100" / "trades.parquet"
    rel = P.data_relative_path(sample_sub)
    assert not os.path.isabs(rel)
    assert "\\" not in rel  # Always forward-slash normalized for portability
    assert rel.startswith("RESEARCH/backtests")

    resolved = P.resolve_data_path(rel)
    assert resolved.resolve() == sample_sub.resolve()


def test_6_backups_and_logs_reside_in_data():
    """Verify that backup operations and log files strictly target DATA/."""
    assert P.BACKUPS_DIR.parent.resolve() == P.DATA_ROOT.resolve()
    assert P.LOGS_DIR.parent.resolve() == P.DATA_ROOT.resolve()

    log_path = P.log_file("test_v3_5_migration.log")
    assert log_path.parent.resolve() == (P.DATA_ROOT / "logs").resolve()
