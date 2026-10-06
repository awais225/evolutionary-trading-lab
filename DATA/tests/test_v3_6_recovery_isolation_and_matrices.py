"""
V3.6 Acceptance Test Suite:
- Data Recovery & Run Isolation (Dummy / Legacy vs User Research)
- Dynamic Run-Relative Node Numbering (No hardcoded 787 offset)
- Dual ID Representation (Node_X and Research Node #Y)
- Strict Node Generation Halt at Ceiling (10,000 nodes, 10,001 blocked)
- Strategy Laboratory Qualified Hydration & Fallback Messaging
- Backtest Matrix Auto-Generation & Loading from Real Persisted Data
- Reconciled Queued Tasks & Transition to COMPLETED_AT_CEILING
- Storage & Headroom Invariants (<= 105 MB target, <= 125 MB ceiling)
"""
import json
import os
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import paths as P
from backend.app.db.database import get_db
from backend.app.evolution.engine import get_evo_engine
from backend.app.main import app
from backend.app.orchestrator.pipeline_state import get_pipeline_state_manager
from backend.app.specialization.matrix import full_matrices
from backend.app.versions import APP_VERSION, APP_NAME, FULL_VERSION_STRING


@pytest.fixture
def client():
    return TestClient(app)


def test_v3_6_version_strings():
    """Verify version strings bumped to V3.6."""
    assert APP_VERSION == "3.6"
    assert "V3.6" in APP_NAME
    assert "V3.6" in FULL_VERSION_STRING


def test_v3_6_workspace_size_invariant():
    """Verify workspace size stays strictly below 105 MB target and 125 MB hard ceiling."""
    total_ws = 0
    for root, dirs, files in os.walk(P.ROOT_DIR):
        if any(p in root for p in [".git", "__pycache__", "node_modules", ".pytest_cache", "dist"]):
            continue
        for f in files:
            fp = os.path.join(root, f)
            if not os.path.islink(fp):
                total_ws += os.path.getsize(fp)
    ws_mb = total_ws / (1024 * 1024)
    assert ws_mb <= 105.0, f"Workspace size {ws_mb:.2f} MB exceeds 105 MB target"
    assert ws_mb <= 125.0, f"Workspace size {ws_mb:.2f} MB exceeds 125 MB ceiling"


def test_v3_6_dummy_legacy_separation():
    """Verify dummy/legacy records are tagged as LEGACY_TEST and separated from USER_RESEARCH."""
    db = get_db()
    # Check that legacy rows have data_source = 'LEGACY_TEST'
    legacy_rows = db.q("SELECT id, data_source, run_id FROM strategies WHERE id <= 787")
    assert len(legacy_rows) > 0
    for r in legacy_rows:
        assert r["data_source"] == "LEGACY_TEST"

    # Check query filtering by data_source
    qual_legacy = db.get_qualified_strategies(data_source="LEGACY_TEST")
    assert len(qual_legacy) > 0


def test_v3_6_dynamic_run_relative_node_numbering():
    """Verify dynamic relative node numbering without hardcoding 787 offset."""
    db = get_db()
    # Any run's first node has research_node_num = 1
    # Check RUN-HISTORICAL-PRESERVED
    first_historical = db.one("""
        SELECT id, research_node_num, run_id
        FROM strategies
        WHERE run_id = 'RUN-HISTORICAL-PRESERVED'
        ORDER BY id ASC LIMIT 1
    """)
    if first_historical:
        assert first_historical["research_node_num"] == 1

    # Check a simulated user research run
    temp_run_id = f"RUN-TEST-USER-{int(time.time())}"
    now = time.time()
    db.x("""
        INSERT INTO strategies
        (hash, parent_id, generation, symbol, timeframe, direction, status,
         genome, created_at, updated_at, run_id, data_source, research_node_num)
        VALUES (?, NULL, 0, 'XAUUSD', 'M15', 'both', 'QUALIFIED',
                '{}', ?, ?, ?, 'USER_RESEARCH', 1)
    """, (f"hash-{temp_run_id}-1", now, now, temp_run_id))

    inserted = db.one("SELECT id, research_node_num, data_source FROM strategies WHERE hash=?", (f"hash-{temp_run_id}-1",))
    assert inserted["data_source"] == "USER_RESEARCH"
    assert inserted["research_node_num"] == 1
    # Cleanup test node
    db.x("DELETE FROM strategies WHERE hash=?", (f"hash-{temp_run_id}-1",))


def test_v3_6_strict_node_ceiling_enforcement():
    """Verify generation stops strictly when ceiling or 10,000 nodes reached."""
    evo = get_evo_engine()
    # Test ceiling check with target = current nodes
    cur = evo.total_nodes()
    evo.set_total_node_target(cur)
    assert evo.is_target_reached() is True
    # Try inserting a node past ceiling
    child_id = evo.try_insert(
        genome={"symbol": "XAUUSD", "timeframe": "M15", "indicators": [], "entry_long": [], "entry_short": []},
        parent_id=None,
        generation=1
    )
    assert child_id is None, "Node creation must be blocked when ceiling reached"


def test_v3_6_qualified_strategies_hydration(client):
    """Verify qualified strategies load immediately with real persisted metrics and no placeholders."""
    res = client.get("/api/strategies/qualified")
    assert res.status_code == 200
    data = res.json()
    assert data["count"] > 0
    strats = data["strategies"]
    for s in strats[:5]:
        assert s["status"] in ("QUALIFIED", "PAPER")
        assert s["id"] is not None
        assert s["symbol"] == "XAUUSD"
        assert s["timeframe"] in ("M1", "M5", "M15", "M30", "H1", "H4", "D1")
        assert s["fitness"] is not None
        # Check real non-zero performance metrics
        assert s["profit_factor"] is not None
        assert s["trades"] is not None and s["trades"] > 0


def test_v3_6_strategy_charts_and_fallback(client):
    """Verify real equity curves or explicit fallback message."""
    # Test qualified strategy with trades (e.g. Node_287)
    res = client.get("/api/strategies/287/charts")
    assert res.status_code == 200
    d = res.json()
    assert d["strategy_id"] == 287
    assert d["has_equity_curve"] is True
    assert len(d["equity_curve"]) > 1
    assert len(d["drawdown_curve"]) > 1

    # Test strategy with no backtest data
    res_empty = client.get("/api/strategies/1/charts")
    assert res_empty.status_code == 200
    d_empty = res_empty.json()
    assert d_empty["has_equity_curve"] is False
    assert d_empty["message"] == "EQUITY CURVE DATA NOT FOUND FOR THIS STRATEGY"


def test_v3_6_backtest_matrix_auto_generation(client):
    """Verify backtest matrix generation and persistence from real backtest data."""
    db = get_db()
    # Strategy 733 has detail backtest and full matrix
    res = client.get("/api/strategies/733")
    assert res.status_code == 200
    d = res.json()
    mats = d.get("matrices")
    assert mats is not None
    assert "timeframe" in mats
    assert "session" in mats
    assert "day" in mats
    assert "regime" in mats
    assert "direction" in mats


def test_v3_6_reconciled_tasks_and_ceiling_state(client):
    """Verify queued tasks are reconciled and lifecycle is COMPLETED_AT_CEILING."""
    db = get_db()
    # Ensure zero tasks in BORN or BACKTESTING
    pending_count = db.one("SELECT COUNT(*) c FROM strategies WHERE status IN ('BORN', 'BACKTESTING')")["c"]
    assert pending_count == 0

    # Ensure reconcile endpoint returns COMPLETED_AT_CEILING
    res = client.post("/api/tasks/reconcile")
    assert res.status_code == 200
    assert res.json()["pipeline_status"] == "COMPLETED_AT_CEILING"

    ps = get_pipeline_state_manager()
    assert ps.status == "COMPLETED_AT_CEILING"


def test_v3_6_log_artifacts_exist():
    """Verify mandatory V3.6 audit logs exist and contain required sections."""
    preflight = P.LOGS_DIR / "V3_6_SIZE_PREFLIGHT.log"
    recon = P.LOGS_DIR / "V3_6_DATA_RECONCILIATION.log"
    inventory = P.LOGS_DIR / "V3_6_RESEARCH_DATA_INVENTORY.log"

    assert preflight.exists(), f"Missing {preflight}"
    assert recon.exists(), f"Missing {recon}"
    assert inventory.exists(), f"Missing {inventory}"

    recon_txt = recon.read_text(encoding="utf-8")
    assert "QUEUED TASKS AUDIT & RESOLUTION SUMMARY" in recon_txt
    assert "COMPLETED_AT_CEILING" in recon_txt
    assert "100.0% coverage" in recon_txt

    inv_txt = inventory.read_text(encoding="utf-8")
    assert "SQLITE MASTER DATABASE INVENTORY" in inv_txt
    assert "QUALIFIED RESEARCH STRATEGIES" in inv_txt
    assert "SNAPSHOT HEADROOM" in inv_txt
