"""
Test Suite for V2.6 Features:
1. Windows CMD batch script syntax validation (prevent 'are was unexpected' and unescaped parens)
2. Backend launcher validation (RUN_BACKEND.bat, scripts/run_backend.bat)
3. Port check tool (single '[OK] Port 8787 is available.')
4. Wait ready tool (health polling and logging)
5. Flexible MT5 configuration (CONFIG/mt5_config.json, no passwords)
6. Safe MT5 terminal discovery across directories and registry
7. Dynamic runtime MT5 versioning and simulator fallback
8. FastAPI MT5 management endpoints
9. Database persistence & schema version verification
10. Evolution tree preservation
"""
import re
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend.app.paths import ROOT_DIR
BACKEND_DIR = ROOT_DIR / "backend"

from app.main import app
from app.mt5.config import load_mt5_config, save_mt5_config, get_configured_terminal_path
from app.mt5.discovery import discover_installed_terminals, is_valid_terminal_exe
from app.mt5.factory import get_bridge, bridge_status, connect_mt5_terminal, disconnect_mt5_terminal


def test_batch_scripts_no_cmd_syntax_errors():
    """Validates that batch scripts do not contain the unescaped parenthesis bug that crashed CMD."""
    bat_files = list(ROOT_DIR.glob("*.bat")) + list((ROOT_DIR / "scripts").glob("*.bat"))
    assert len(bat_files) >= 5, f"Expected at least 5 bat files, found {len(bat_files)}"

    for bat in bat_files:
        content = bat.read_text(encoding="utf-8", errors="ignore")
        # Check that 'are was unexpected' does not appear and the old buggy string is gone
        assert "(such as psutil, fastapi) are missing" not in content
        assert "are was unexpected" not in content


def test_backend_launcher_structure():
    """Validates that RUN_BACKEND.bat and scripts/run_backend.bat keep window open on exit."""
    for rel_path in ["RUN_BACKEND.bat", "scripts/run_backend.bat"]:
        bat = ROOT_DIR / rel_path
        assert bat.exists(), f"{rel_path} must exist"
        content = bat.read_text(encoding="utf-8")

        assert "@echo off" in content
        assert "uvicorn" in content
        assert "8787" in content
        assert "pause" in content, f"{rel_path} must have pause to keep console visible on crash"


def test_check_port_tool_single_ok_output():
    """backend/tools/check_port.py must output '[OK] Port 8787 is available.' exactly once when port is free."""
    proc = subprocess.run(
        [sys.executable, str(BACKEND_DIR / "tools" / "check_port.py")],
        capture_output=True,
        text=True,
        cwd=str(ROOT_DIR),
    )
    stdout = proc.stdout.strip()
    if proc.returncode == 0:
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        ok_lines = [l for l in lines if "[OK] Port 8787 is available." in l]
        assert len(ok_lines) == 1, f"Expected exactly one [OK] line, got {ok_lines} in {stdout}"


def test_mt5_config_persistence_and_no_passwords(tmp_path, monkeypatch):
    """CONFIG/mt5_config.json must persist terminal metadata and NEVER store passwords."""
    test_cfg_path = tmp_path / "mt5_config.json"
    monkeypatch.setattr("app.mt5.config.CONFIG_FILE", test_cfg_path)
    monkeypatch.setattr("app.mt5.config.MT5_CONFIG_FILE", test_cfg_path)

    # Initial load returns empty/default
    cfg = load_mt5_config()
    assert cfg.get("terminal_path") == ""

    # Save valid config with an attempted password field
    save_mt5_config({
        "terminal_path": "C:\\MetaTrader5\\terminal64.exe",
        "terminal_company": "MetaQuotes Software Corp.",
        "terminal_build": 5000,
        "password": "SECRET_PASSWORD_DO_NOT_STORE",
        "pass": "123456",
    })

    # Read back and verify password was stripped
    loaded = load_mt5_config()
    assert loaded.get("terminal_path") == "C:\\MetaTrader5\\terminal64.exe"
    assert loaded.get("terminal_company") == "MetaQuotes Software Corp."
    assert loaded.get("terminal_build") == 5000
    assert "password" not in loaded
    assert "pass" not in loaded

    # Verify helper get_configured_terminal_path
    assert get_configured_terminal_path() == "C:\\MetaTrader5\\terminal64.exe"


def test_mt5_discovery_safe_execution():
    """discover_installed_terminals must run safely without raising exceptions on any platform."""
    terminals = discover_installed_terminals()
    assert isinstance(terminals, list)
    for term in terminals:
        assert "path" in term
        assert "name" in term

    # Helper validator
    assert is_valid_terminal_exe("non_existent_file.exe") is False
    assert is_valid_terminal_exe(str(ROOT_DIR / "START.bat")) is False


def test_mt5_runtime_simulator_fallback_and_no_hardcoded_build():
    """Real MT5 bridge must degrade gracefully to simulator and dynamically report build."""
    from app.mt5.mt5_real import MT5RealBridge
    real_b = MT5RealBridge()
    real_st = real_b.status()
    assert "terminal_build" in real_st
    assert not isinstance(real_st["terminal_build"], str) or real_st["terminal_build"] == ""

    status = bridge_status()
    assert "connected" in status
    assert "terminal_build" in status
    assert "is_simulated" in status


def test_fastapi_mt5_endpoints():
    """Tests the new MT5 management REST API endpoints."""
    client = TestClient(app)

    # 1. GET /api/mt5/terminals
    res = client.get("/api/mt5/terminals")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)

    # 2. GET /api/mt5/config
    res_cfg = client.get("/api/mt5/config")
    assert res_cfg.status_code == 200
    cfg_data = res_cfg.json()
    assert isinstance(cfg_data, dict)
    assert "password" not in cfg_data

    # 3. POST /api/mt5/connect (with empty or dummy path, graceful failure in test env without crash)
    res_conn = client.post("/api/mt5/connect", json={"terminal_path": ""})
    assert res_conn.status_code == 200
    conn_data = res_conn.json()
    assert "ok" in conn_data
    assert "status" in conn_data

    # 4. POST /api/mt5/disconnect
    res_disc = client.post("/api/mt5/disconnect")
    assert res_disc.status_code == 200
    disc_data = res_disc.json()
    assert disc_data.get("ok") is True


def test_database_preservation_and_schema_version():
    """Verify DATABASE/lab_state.db retains all 371 strategies and schema user_version == 3."""
    db_path = ROOT_DIR / "DATABASE" / "lab_state.db"
    assert db_path.exists(), "DATABASE/lab_state.db must exist"

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    cur.execute("PRAGMA user_version")
    user_ver = cur.fetchone()[0]
    assert user_ver == 3, f"Expected DB schema user_version 3, got {user_ver}"

    cur.execute("SELECT COUNT(*) FROM strategies")
    strat_count = cur.fetchone()[0]
    assert strat_count >= 371, f"Expected at least 371 strategies in database, got {strat_count}"

    conn.close()


def test_evolution_tree_components_preserved():
    """Verify that Evolution Tree handles, smoothstep edges, and components remain untouched."""
    tree_file = ROOT_DIR / "frontend" / "src" / "pages" / "EvolutionTree.jsx"
    assert tree_file.exists(), "EvolutionTree.jsx must exist"
    tree_code = tree_file.read_text(encoding="utf-8")

    # Handles & React Flow
    assert "Handle" in tree_code
    assert 'type="target"' in tree_code
    assert 'type="source"' in tree_code
    assert "smoothstep" in tree_code
    assert "EvoNode" in tree_code
    assert "STATUS_COLORS" in tree_code
