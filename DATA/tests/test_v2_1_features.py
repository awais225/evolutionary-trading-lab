"""
Unit & Integration tests for V2.1 iteration:
  1. Windows Launchers: Prerequisite.bat and START.bat syntax & execution rules
  2. Total Node Target enforcement across all creation paths
  3. Incremental expansion without regenerating or deleting historical nodes
  4. RECHECK fast state reconstruction without rerunning backtests
  5. Dead node reproduction prohibition & lineage preservation
"""
import os
import random
from pathlib import Path
import pytest

from app.db.database import get_db, Database
from app.evolution.engine import EvolutionEngine
from app.genome import ops as gops
from app.paths import ROOT_DIR


def test_prerequisite_bat_structure():
    bat_path = ROOT_DIR / "Prerequisite.bat"
    assert bat_path.exists(), "Prerequisite.bat must exist in project root"
    content = bat_path.read_text(encoding="utf-8")

    # Genuine CMD commands
    assert "@echo off" in content
    assert "setlocal enabledelayedexpansion" in content
    assert "%~dp0" in content, "Must determine project root using %~dp0"

    # Required stages & checks
    assert "prerequisite.log" in content, "Must write setup diagnostics to LOGS"
    assert "python" in content.lower(), "Must verify Python"
    assert "3.10" in content, "Must check Python 3.10+ requirement"
    assert "backend\\.venv" in content, "Must verify/create backend\\.venv"
    assert "requirements.txt" in content, "Must install/verify requirements.txt"
    assert "MetaTrader5" in content, "Must check MetaTrader5 package"
    assert "node" in content, "Must verify Node.js"
    assert "npm" in content, "Must verify npm"
    assert "frontend\\dist" in content or "npm run build" in content, "Must verify frontend build"
    assert "nvidia-smi" in content, "Must check NVIDIA GPU availability"
    assert "terminal64.exe" in content, "Must inspect MT5 installation"

    # Safe error handling & completion
    assert "STEP_FAILED" in content, "Must have explicit failure handler"
    assert "Press any key to close..." in content, "Must finish with visible pause"
    assert "exit /b 0" in content, "Must return proper exit codes"
    assert "exit /b !EXIT_CODE!" in content or "exit /b 1" in content


def test_start_bat_structure():
    bat_path = ROOT_DIR / "START.bat"
    assert bat_path.exists(), "START.bat must exist in project root"
    content = bat_path.read_text(encoding="utf-8")

    # Genuine CMD launcher
    assert "@echo off" in content
    assert "%~dp0" in content, "Must determine project root using %~dp0"

    # Validation & launch
    assert "main.py" in content, "Must validate main.py"
    assert "lab_config.yaml" in content, "Must validate config"
    assert "Prerequisite.bat" in content, "Must invoke Prerequisite.bat if venv missing"
    assert "cmd.exe" in content or "%COMSPEC%" in content, "Must explicitly launch through cmd.exe"
    assert "/k" in content, "Must use /k to keep backend window open for diagnostics"

    # Health check & monitoring
    assert "/health" in content, "Must wait for backend /health endpoint"
    assert "http://localhost:8787/" in content or "http://127.0.0.1:8787/" in content
    assert "STARTUP FAILED" in content, "Must display STARTUP FAILED on error"
    assert "MONITOR_LOOP" in content or "timeout" in content, "Must remain open while running"


def test_total_node_target_enforced_in_try_insert(tmp_path):
    # Isolated test database
    db_path = str(tmp_path / "test_target.db")
    db = Database(db_path)
    evo = EvolutionEngine(db)

    # Set target to 5
    evo.set_total_node_target(5)
    assert evo.get_total_node_target() == 5
    assert evo.total_nodes() == 0
    assert evo.remaining_nodes() == 5
    assert not evo.is_target_reached()

    rng = random.Random(101)
    inserted_ids = []
    for _ in range(5):
        g = gops.random_genome("XAUUSD", rng, 3)
        sid = evo.try_insert(g, parent_id=None, generation=0)
        assert sid is not None, "Strategy insertion within target must succeed"
        inserted_ids.append(sid)

    assert evo.total_nodes() == 5
    assert evo.remaining_nodes() == 0
    assert evo.is_target_reached()

    # 6th insertion must be blocked by total node target
    g_extra = gops.random_genome("XAUUSD", rng, 3)
    sid_extra = evo.try_insert(g_extra, parent_id=None, generation=0)
    assert sid_extra is None, "Insertion beyond total node target must be rejected"
    assert evo.total_nodes() == 5, "Total nodes must remain capped at 5"


def test_total_node_target_enforced_in_seed_and_reproduce(tmp_path):
    db_path = str(tmp_path / "test_seed_reproduce.db")
    db = Database(db_path)
    evo = EvolutionEngine(db)

    # Target 8 nodes
    evo.set_total_node_target(8)

    # Request 10 seeds, but target is 8 -> exactly 8 must be created
    created = evo.seed_population(10, "XAUUSD")
    assert created <= 8
    assert evo.total_nodes() == 8
    assert evo.is_target_reached()

    # Further reproduction must be blocked
    rep_res = evo.reproduce(5, "XAUUSD")
    assert rep_res.get("target_reached") is True or (
        rep_res["mutation"] == 0 and rep_res["crossover"] == 0 and rep_res["exploration"] == 0
    )
    assert evo.total_nodes() == 8, "Reproduction must not exceed total node target"


def test_target_can_be_increased_later_and_continues(tmp_path):
    db_path = str(tmp_path / "test_increase.db")
    db = Database(db_path)
    evo = EvolutionEngine(db)

    # Day 1: Target = 4
    evo.set_total_node_target(4)
    evo.seed_population(4, "XAUUSD")
    assert evo.total_nodes() == 4
    assert evo.is_target_reached()

    # Day 2: Increase target to 7 -> continues 5 -> 7
    evo.set_total_node_target(7)
    assert evo.get_total_node_target() == 7
    assert evo.remaining_nodes() == 3
    assert not evo.is_target_reached()

    # Seed or insert remaining 3
    new_seeds = evo.seed_population(3, "XAUUSD")
    assert new_seeds == 3
    assert evo.total_nodes() == 7
    assert evo.is_target_reached()

    # Verify existing nodes 1..4 are intact and untouched
    for i in range(1, 5):
        st = db.get_strategy(i)
        assert st is not None, f"Strategy #{i} from Day 1 must be preserved"


def test_recheck_fast_state_reconstruction(tmp_path):
    db_path = str(tmp_path / "test_recheck.db")
    db = Database(db_path)
    evo = EvolutionEngine(db)

    evo.set_total_node_target(10)
    evo.seed_population(6, "XAUUSD")

    # Mark some statuses
    db.update_strategy(1, status="SURVIVED", fitness=1.5)
    db.update_strategy(2, status="QUALIFIED", fitness=2.1)
    db.update_strategy(3, status="FAILED")
    db.update_strategy(4, status="KILLED")

    state = db.reconstruct_state(10)
    assert state["total_nodes"] == 6
    assert state["target"] == 10
    assert state["remaining"] == 4
    assert state["alive"] + state["dead"] == state["total_nodes"]
    assert state["qualified"] >= 1
    assert state["dead"] >= 2
    assert state["progress"] == "6 / 10"
    assert not state["target_reached"]


def test_dead_strategies_cannot_produce_new_children(tmp_path):
    db_path = str(tmp_path / "test_dead_node.db")
    db = Database(db_path)
    evo = EvolutionEngine(db)
    evo.set_total_node_target(100)

    # Create root strategy
    rng = random.Random(42)
    g1 = gops.random_genome("XAUUSD", rng, 3)
    sid1 = evo.try_insert(g1, parent_id=None, generation=0)
    assert sid1 is not None

    # While alive, it produces a child
    g2 = gops.random_genome("XAUUSD", rng, 3)
    sid2 = evo.spawn_child(g2, parent_id=sid1, mutation_type="directed", reason="while alive")
    assert sid2 is not None

    # Now mark parent as FAILED
    db.update_strategy(sid1, status="FAILED")

    # Dead strategy cannot produce new children
    g3 = gops.random_genome("XAUUSD", rng, 3)
    sid3 = evo.spawn_child(g3, parent_id=sid1, mutation_type="directed", reason="after death")
    assert sid3 is None, "Dead strategy must NOT produce new children"

    # Historical child sid2 remains intact with valid ancestry
    child2 = db.get_strategy(sid2)
    assert child2["parent_id"] == sid1
