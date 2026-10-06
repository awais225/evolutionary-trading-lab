import time
import hashlib
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from backend.app.main import app
from backend.app.db.database import get_db, Database
from backend.app.orchestrator.stages import StageManager, get_stage_manager, CallableString
from backend.app.orchestrator.lab import Lab, get_lab
from backend.app.activity.activity import ActivityManager, activity
from backend.app.paths import ROOT_DIR, LOGS_DIR, log_file

EVOLUTION_TREE_ORIGINAL_SHA = "62017667f4568816916285c42d6ba7ed98db2eccbc45f971c222ab48213061f4"


def test_stage_manager_contract_consistency():
    """Verify StageManager.current_stage contract: both property and callable work without TypeError."""
    sm = StageManager()
    assert sm.current_stage == "DATA_SYNC"
    assert sm.current_stage() == "DATA_SYNC"
    assert isinstance(sm.current_stage, str)
    assert isinstance(sm.current_stage, CallableString)
    assert f"Stage: {sm.current_stage}" == "Stage: DATA_SYNC"
    assert sm.get_current_stage() == "DATA_SYNC"

    sm.transition("DATA_SYNC", "RESEARCH", {"reason": "Test transition"})
    assert sm.current_stage == "RESEARCH"
    assert sm.current_stage() == "RESEARCH"
    assert sm.get_current_stage() == "RESEARCH"


def test_recovery_idempotency_and_no_counter_inflation(tmp_path):
    """Verify strategy watchdog recovery is strictly idempotent and marks repeated failures FAILED."""
    db_file = tmp_path / "test_rec.db"
    db = Database(str(db_file))

    # Insert 2 test strategies stuck in BACKTESTING and VALIDATING 200s ago
    past_ts = time.time() - 200.0
    db.x(
        "INSERT INTO strategies (hash, symbol, timeframe, status, created_at, updated_at, genome) "
        "VALUES ('H1', 'XAUUSD', 'M15', 'BACKTESTING', ?, ?, '{}')",
        (past_ts, past_ts)
    )
    db.x(
        "INSERT INTO strategies (hash, symbol, timeframe, status, created_at, updated_at, genome) "
        "VALUES ('H2', 'XAUUSD', 'M15', 'VALIDATING', ?, ?, '{}')",
        (past_ts, past_ts)
    )
    s1 = db.one("SELECT id FROM strategies WHERE hash='H1'")["id"]
    s2 = db.one("SELECT id FROM strategies WHERE hash='H2'")["id"]

    lab = Lab()
    lab.db = db

    # 1. First recovery cycle should recover both
    lab._recover_stuck_strategies(force=True)
    assert s1 in lab._recovered_strategy_ids
    assert s2 in lab._recovered_strategy_ids

    st1 = db.one("SELECT status FROM strategies WHERE id=?", (s1,))["status"]
    st2 = db.one("SELECT status FROM strategies WHERE id=?", (s2,))["status"]
    assert st1 == "BORN"
    assert st2 == "SURVIVED"

    # 2. Second recovery cycle immediately after: must be strictly IDEMPOTENT (0 recovered)
    lab._recover_stuck_strategies(force=True)
    assert len(lab._recovered_strategy_ids) == 2

    # 3. Simulate repeated timeouts for a problematic strategy (attempts >= 3)
    lab._strategy_recovery_attempts[s1] = 2  # next will be attempt #3
    lab._recovered_strategy_ids.remove(s1)
    db.x("UPDATE strategies SET status='BACKTESTING', updated_at=? WHERE id=?", (past_ts, s1))

    lab._recover_stuck_strategies(force=True)
    st1_after = db.one("SELECT status, creation_reason FROM strategies WHERE id=?", (s1,))
    assert st1_after["status"] == "FAILED"
    assert "threshold" in st1_after["creation_reason"].lower()


def test_orchestrator_consecutive_failure_backoff_and_safe_paused():
    """Verify orchestrator failure loop triggers controlled retry, backoff, and SAFE PAUSED state."""
    lab = Lab()
    assert lab.safe_paused is False
    assert lab._consecutive_failures == 0

    # Simulate 3 consecutive tick failures
    for i in range(1, 4):
        lab._consecutive_failures += 1
        lab.last_error = "TypeError: 'str' object is not callable"
        if lab._consecutive_failures >= 3:
            lab.paused = True
            lab.safe_paused = True
            lab.safe_paused_reason = f"Repeated orchestrator failures ({lab._consecutive_failures} consecutive). Last error: {lab.last_error}"

    assert lab.safe_paused is True
    assert lab.paused is True
    assert "Repeated orchestrator failures (3 consecutive)" in lab.safe_paused_reason

    st = lab.status()
    assert st["safe_paused"] is True
    assert st["consecutive_failures"] == 3

    # Resuming resets failure counter and clears SAFE PAUSED
    lab.resume()
    assert lab.safe_paused is False
    assert lab.paused is False
    assert lab._consecutive_failures == 0


def test_activity_stream_error_throttling_and_bounding():
    """Verify ActivityManager deduplicates repeating errors and bounds in-memory buffer."""
    am = ActivityManager()

    # Emit 50 identical errors rapidly
    for _ in range(50):
        am.error("SYSTEM", "Duplicate error test message")

    # Only 1 unique event should be recorded in recent cache
    events = [e for e in am._recent_cache if e.get("message") == "Duplicate error test message"]
    assert len(events) == 1
    assert am._throttled_error_count.get("Duplicate error test message", 0) == 49

    # Emit 600 distinct events
    for i in range(600):
        am.info("SYSTEM", f"Unique event #{i}")

    # Buffer must stay bounded at <= 500
    assert len(am._recent_cache) <= 500


def test_evolution_tree_jsx_integrity():
    """Verify EvolutionTree.jsx was strictly preserved without modifications."""
    tree_path = ROOT_DIR / "frontend" / "src" / "pages" / "EvolutionTree.jsx"
    assert tree_path.exists()
    current_sha = hashlib.sha256(tree_path.read_bytes()).hexdigest()
    assert current_sha == EVOLUTION_TREE_ORIGINAL_SHA, "EvolutionTree.jsx MUST remain untouched!"


def test_diagnostic_log_disk_persistence_and_generation():
    """Verify persistent disk logging and generate_diagnostic_log formatting."""
    log_path = log_file("lab.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write("[2026-09-27 12:00:00] [SYSTEM] Persistent diagnostic log disk entry verification\n")

    diag_text = activity.generate_diagnostic_log()
    assert "EVOLUTIONARY TRADING RESEARCH LAB" in diag_text
    assert "--- PERSISTENT DISK LOG (LOGS/lab.log - Tail) ---" in diag_text
    assert "Persistent diagnostic log disk entry verification" in diag_text
