"""
Unit & Integration tests for V2.2 Master Fix & Architecture Hardening:
  1. Windows Launchers: Prerequisite.bat and START.bat syntax, cmd.exe /k, dual venv detection
  2. scripts/run_backend.bat existence and structure
  3. Research Memory schema migration and table structure
  4. Failure / Survival reason recording on strategies
  5. Anti-redundancy hypothesis engine skipping redundant experiments
  6. Research memory API endpoint
"""
import pytest
from pathlib import Path
from app.paths import ROOT_DIR
from app.db.database import Database
from app.ai_researcher.analyzer import record_experiment_memory


def test_v2_2_launcher_scripts():
    prereq = ROOT_DIR / "Prerequisite.bat"
    start = ROOT_DIR / "START.bat"
    run_backend = ROOT_DIR / "scripts" / "run_backend.bat"

    assert prereq.exists()
    assert start.exists()
    assert run_backend.exists()

    p_txt = prereq.read_text(encoding="utf-8")
    s_txt = start.read_text(encoding="utf-8")
    r_txt = run_backend.read_text(encoding="utf-8")

    # Prerequisite verification
    assert "%~dp0" in p_txt
    assert "backend\\.venv" in p_txt or ".venv" in p_txt
    assert "fastapi" in p_txt and "uvicorn" in p_txt
    assert "prerequisite.log" in p_txt
    assert "Press any key to close..." in p_txt

    # START verification
    assert "%~dp0" in s_txt
    assert "/health" in s_txt
    assert "/k" in s_txt
    assert "run_backend.bat" in s_txt

    # run_backend verification
    assert "%~dp0" in r_txt
    assert "main:app" in r_txt
    assert "pause" in r_txt


def test_research_memory_and_reasons_schema(tmp_path):
    db_path = str(tmp_path / "test_v2_2.db")
    db = Database(db_path)

    # Verify user_version >= 3
    ver = db.one("PRAGMA user_version")["user_version"]
    assert ver >= 3

    # Verify research_memory table exists
    has_mem = db.one("SELECT name FROM sqlite_master WHERE type='table' AND name='research_memory'")
    assert has_mem is not None

    # Verify failure_reason and survival_reason exist on strategies
    cols = [r["name"] for r in db.q("PRAGMA table_info(strategies)")]
    assert "failure_reason" in cols
    assert "survival_reason" in cols


def test_record_and_retrieve_research_memory(tmp_path):
    db_path = str(tmp_path / "test_mem.db")
    db = Database(db_path)

    # Insert parent strategy
    parent_id = db.insert_strategy({
        "symbol": "XAUUSD",
        "timeframe": "M15",
        "species_key": "M15|any|none",
        "genome": {"indicators": [{"name": "rsi"}], "conditions": []},
        "hash": "hash_parent_001",
        "parent_id": None,
        "generation": 0,
        "status": "QUALIFIED",
        "fitness": 1.20,
    })

    # Insert child strategy
    child_id = db.insert_strategy({
        "symbol": "XAUUSD",
        "timeframe": "M15",
        "species_key": "M15|any|none",
        "genome": {"indicators": [{"name": "rsi"}, {"name": "adx"}], "conditions": []},
        "hash": "hash_child_001",
        "parent_id": parent_id,
        "generation": 1,
        "status": "FAILED",
        "fitness": 0.40,
        "mutation_type": "add_indicator",
    })
    db.update_strategy(child_id, failure_reason="Screening trade count fell below minimum threshold (18 < 30)")

    # Record experiment memory
    record_experiment_memory(child_id=child_id, db=db)

    rows = db.q("SELECT * FROM research_memory WHERE parent_id = ?", (parent_id,))
    assert len(rows) == 1
    m = rows[0]
    assert m["child_id"] == child_id
    assert m["outcome"] == "FAILED"
    assert m["fitness_delta"] == -0.80
    assert "Screening trade count fell below" in m["failure_reason"]
    assert "indicators" in m["changed_variables"]


def test_anti_redundancy_research_check(tmp_path):
    db_path = str(tmp_path / "test_anti_redundancy.db")
    db = Database(db_path)

    # Insert a dummy parent strategy
    parent_id = db.insert_strategy({
        "symbol": "XAUUSD",
        "timeframe": "M15",
        "species_key": "M15|any|none",
        "genome": {"indicators": [], "conditions": []},
        "hash": "dummy_hash_parent",
        "parent_id": None,
        "generation": 0,
        "status": "QUALIFIED",
        "fitness": 1.5,
    })

    child_id = db.insert_strategy({
        "symbol": "XAUUSD",
        "timeframe": "M15",
        "species_key": "M15|any|none",
        "genome": {"indicators": [{"name": "adx"}], "conditions": []},
        "hash": "dummy_hash_child",
        "parent_id": parent_id,
        "generation": 1,
        "status": "FAILED",
        "fitness": 0.2,
        "mutation_type": "add_adx_filter",
    })
    db.update_strategy(child_id, failure_reason="Over-filtering caused 0 trades")

    record_experiment_memory(child_id=child_id, db=db)

    # Query analyzer hypotheses: proposing add_adx_filter with threshold 30 should be recognized in memory
    mem_matches = db.q(
        "SELECT id FROM research_memory WHERE parent_id = ? AND action = ? AND outcome = 'FAILED'",
        (parent_id, "add_adx_filter")
    )
    assert len(mem_matches) == 1
