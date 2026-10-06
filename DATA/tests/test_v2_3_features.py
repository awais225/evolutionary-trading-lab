"""
Unit & Integration tests for V2.3 Full Stability, Installation, Startup and Deployment Repair:
  1. backend/requirements.txt consistency (must explicitly include psutil)
  2. verify_deps.py verification and self-repair mechanism
  3. Prerequisite.bat and START.bat V2.3 structure and commands
  4. Frontend production build and index.html validation
  5. Desktop EXE packaging architecture (desktop/app_window.py, EvolutionaryTradingResearchLab.spec)
"""
import os
import subprocess
import sys
from pathlib import Path
import pytest

from app.paths import ROOT_DIR


def test_requirements_contains_psutil_and_critical_deps():
    req_file = ROOT_DIR / "backend" / "requirements.txt"
    assert req_file.exists(), "backend/requirements.txt must exist"
    content = req_file.read_text(encoding="utf-8")

    # Critical packages that were previously missing or essential
    assert "psutil" in content, "psutil must be explicitly declared in requirements.txt"
    assert "fastapi" in content
    assert "uvicorn" in content
    assert "numpy" in content
    assert "pandas" in content
    assert "pyarrow" in content
    assert "duckdb" in content
    assert "pydantic" in content
    assert "pyyaml" in content
    assert "httpx" in content
    assert "websockets" in content
    assert "MetaTrader5" in content


def test_verify_deps_script_execution():
    verify_script = ROOT_DIR / "backend" / "app" / "verify_deps.py"
    assert verify_script.exists(), "backend/app/verify_deps.py must exist"

    # Execute verify_deps.py using current python interpreter
    res = subprocess.run([sys.executable, str(verify_script)], capture_output=True, text=True)
    assert res.returncode == 0, f"verify_deps.py failed with output:\n{res.stdout}\n{res.stderr}"
    assert "[OK] psutil verified" in res.stdout
    assert "[OK] fastapi verified" in res.stdout
    assert "[OK] Application main module imported successfully" in res.stdout
    assert "[OK] ALL BACKEND RUNTIME DEPENDENCIES & APPLICATION ENTRYPOINTS VERIFIED." in res.stdout


def test_prerequisite_bat_v2_3_structure():
    prereq = ROOT_DIR / "Prerequisite.bat"
    assert prereq.exists()
    content = prereq.read_text(encoding="utf-8")

    assert "@echo off" in content
    assert "%~dp0" in content
    assert "prerequisite.log" in content
    assert "verify_deps.py" in content, "Must invoke verify_deps.py for runtime verification & self-repair"
    assert "requirements.txt" in content
    assert "frontend\\dist\\index.html" in content
    assert "MetaTrader5" in content
    assert "STEP_FAILED" in content
    assert "Press any key to close..." in content
    assert "exit /b 0" in content
    assert "exit /b 1" in content


def test_start_bat_v2_3_structure():
    start = ROOT_DIR / "START.bat"
    assert start.exists()
    content = start.read_text(encoding="utf-8")

    assert "@echo off" in content
    assert "%~dp0" in content
    assert "startup.log" in content
    assert "from app.main import app" in content
    assert "verify_deps.py" in content or "Prerequisite.bat" in content
    assert "frontend\\dist\\index.html" in content
    assert "run_backend.bat" in content
    assert "/health" in content
    assert "http://127.0.0.1:8787/health" in content or "http://localhost:8787/health" in content
    assert "STARTUP_FAILED" in content
    assert "MONITOR_LOOP" in content


def test_desktop_exe_packaging_layer():
    desktop_app = ROOT_DIR / "desktop" / "app_window.py"
    spec_file = ROOT_DIR / "EvolutionaryTradingResearchLab.spec"
    build_exe = ROOT_DIR / "scripts" / "build_exe.bat"

    assert desktop_app.exists(), "desktop/app_window.py must exist"
    assert spec_file.exists(), "EvolutionaryTradingResearchLab.spec must exist"
    assert build_exe.exists(), "scripts/build_exe.bat must exist"

    app_content = desktop_app.read_text(encoding="utf-8")
    assert "uvicorn" in app_content
    assert "app.main" in app_content
    assert "8787" in app_content

    spec_content = spec_file.read_text(encoding="utf-8")
    assert "desktop/app_window.py" in spec_content or "desktop\\app_window.py" in spec_content
    assert "frontend/dist" in spec_content or "frontend\\dist" in spec_content
    assert "psutil" in spec_content
    assert "EvolutionaryTradingResearchLab" in spec_content


def test_frontend_dist_validity():
    dist_html = ROOT_DIR / "frontend" / "dist" / "index.html"
    assert dist_html.exists(), "frontend/dist/index.html must exist"
    html_content = dist_html.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in html_content or "<html" in html_content
    assert len(html_content) > 50, "index.html must not be empty"
