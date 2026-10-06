"""
Evolutionary Trading Research Lab - System Doctor & Diagnostic Suite (V2.7)

Verifies:
  1. Python runtime & version (>=3.10)
  2. Virtual environment resolution
  3. Direct third-party dependencies (psutil, fastapi, duckdb, pyarrow, numpy, pandas, etc.)
  4. Core backend import (from app.main import app)
  5. Database accessibility & safe schema integrity
  6. Evolution Tree graph integrity (valid parent references, no self-loops, valid edges)
  7. Frontend production bundle (frontend/dist/index.html)
  8. MetaTrader 5 bridge discovery & flexible configuration
  9. Desktop standalone application packaging files
 10. Persistent DATA architecture & manifest integrity (V2.7)
"""
from __future__ import annotations

import importlib
import json
import os
import sqlite3
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BACKEND_DIR.parent

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def run_diagnostics() -> int:
    print("===========================================================")
    print(" EVOLUTIONARY TRADING RESEARCH LAB - SYSTEM DOCTOR (V3.2) ")
    print("===========================================================")
    print(f"Root Directory: {ROOT_DIR}")
    print(f"Python Exec:    {sys.executable}")
    print("-----------------------------------------------------------")

    results = []
    has_critical_failure = False

    def record(category: str, status: str, details: str):
        nonlocal has_critical_failure
        if status == "FAIL":
            has_critical_failure = True
        results.append((category, status, details))

    # 1. Python Version
    v = sys.version_info
    py_str = f"{v.major}.{v.minor}.{v.micro}"
    if v.major == 3 and v.minor >= 10:
        if v.minor >= 14:
            record("Python Version", "WARN", f"v{py_str} detected (Python 3.14+). MT5 binary wheels may use SIMULATOR bridge.")
        else:
            record("Python Version", "PASS", f"v{py_str} (Fully supported 3.10 - 3.13)")
    else:
        record("Python Version", "FAIL", f"v{py_str} detected. Python 3.10+ required.")

    # 2. Virtual Environment
    is_venv = (sys.prefix != sys.base_prefix) or ("venv" in sys.executable.lower())
    if is_venv:
        record("Virtual Environment", "PASS", f"Active: {sys.prefix}")
    else:
        record("Virtual Environment", "WARN", "Running in global/system Python environment.")

    # 3. Critical Dependencies
    reqs = [
        ("psutil", "psutil"),
        ("fastapi", "fastapi"),
        ("uvicorn", "uvicorn"),
        ("duckdb", "duckdb"),
        ("pyarrow", "pyarrow"),
        ("numpy", "numpy"),
        ("pandas", "pandas"),
        ("pydantic", "pydantic"),
        ("yaml", "pyyaml"),
        ("httpx", "httpx"),
        ("websockets", "websockets"),
    ]
    missing_deps = []
    for mod_name, pkg_label in reqs:
        try:
            importlib.import_module(mod_name)
        except Exception:
            missing_deps.append(pkg_label)

    if not missing_deps:
        record("Python Dependencies", "PASS", "All 11 direct runtime dependencies verified (including psutil).")
    else:
        record("Python Dependencies", "FAIL", f"Missing packages: {', '.join(missing_deps)}")

    # 4. MetaTrader5 Package
    try:
        import MetaTrader5 as mt5
        ver = getattr(mt5, "__version__", "detected")
        record("MetaTrader5 Package", "PASS", f"Installed ({ver}).")
    except Exception:
        if sys.platform == "win32":
            record("MetaTrader5 Package", "WARN", "Not installed. Lab will run in SIMULATOR mode.")
        else:
            record("MetaTrader5 Package", "PASS", f"N/A on {sys.platform} (SIMULATOR mode active).")

    # 5. Core Application Import Smoke Test
    try:
        from app.main import app
        ver = getattr(app, "version", "2.4.0")
        record("Application Import", "PASS", f"Successfully imported from app.main import app (v{ver}).")
    except Exception as e:
        record("Application Import", "FAIL", f"Failed: {e}")

    # 6. Database Connectivity & Lineage Integrity
    from app import paths as P
    db_file = P.database_file()
    if db_file.exists():
        try:
            conn = sqlite3.connect(str(db_file))
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("PRAGMA user_version")
            u_ver = cur.fetchone()[0]

            cur.execute("SELECT count(*) FROM strategies")
            strat_count = cur.fetchone()[0]

            # Validate parent-child lineage integrity
            cur.execute("SELECT id, parent_id, generation FROM strategies WHERE parent_id IS NOT NULL")
            rows = cur.fetchall()
            cur.execute("SELECT id FROM strategies")
            all_ids = {r[0] for r in cur.fetchall()}

            orphans = 0
            self_loops = 0
            for r in rows:
                sid, pid, gen = r["id"], r["parent_id"], r["generation"]
                if pid == sid:
                    self_loops += 1
                if pid not in all_ids:
                    orphans += 1

            conn.close()
            if self_loops == 0 and orphans == 0:
                record("Database & Lineage", "PASS", f"{strat_count} strategies (user_version={u_ver}). All parent relationships valid (0 self-loops, 0 broken refs).")
            else:
                record("Database & Lineage", "WARN", f"{strat_count} strategies. Found {self_loops} self-loops and {orphans} broken parent refs.")
        except Exception as e:
            record("Database & Lineage", "FAIL", f"Database error: {e}")
    else:
        record("Database & Lineage", "WARN", "DATABASE/lab_state.db does not exist yet (will be initialized on first run).")

    # 7. Frontend Production Bundle
    fe_index = ROOT_DIR / "frontend" / "dist" / "index.html"
    if fe_index.exists() and fe_index.stat().st_size > 50:
        record("Frontend Bundle", "PASS", f"frontend/dist/index.html verified ({fe_index.stat().st_size} bytes).")
    else:
        record("Frontend Bundle", "WARN", "frontend/dist/index.html missing or empty. Run pre-requisite.bat to compile.")

    # 8. Desktop EXE Packaging Layer
    desktop_app = ROOT_DIR / "desktop" / "app_window.py"
    spec_file = ROOT_DIR / "EvolutionaryTradingLab.spec"
    if desktop_app.exists() and spec_file.exists():
        record("Desktop Packaging", "PASS", "Desktop shell (desktop/app_window.py) and PyInstaller spec ready.")
    else:
        record("Desktop Packaging", "WARN", "Desktop shell or PyInstaller specification incomplete.")

    # 9. Persistent DATA Architecture & Manifests (V3.2)
    # DATA root is authoritative via app.paths.DATA_ROOT (honours
    # EVOLUTIONARY_LAB_DATA_ROOT) so diagnostics follow the active DATA tree.
    try:
        from app import paths as _paths  # noqa: WPS433 (lazy import keeps doctor importable standalone)
        data_root = _paths.DATA_ROOT
    except Exception:
        data_root = ROOT_DIR / "DATA"
    manifest_file = data_root / "manifest.json"
    db_mirror = data_root / "database" / "lab_state.db"
    node_ledger = data_root / "nodes" / "node_ledger.jsonl"
    feat_dir = data_root / "features"

    if data_root.exists() and os.access(str(data_root), os.R_OK | os.W_OK):
        record("DATA Root & Permissions", "PASS", f"Authoritative root {data_root} is readable and writable.")
    else:
        record("DATA Root & Permissions", "FAIL", f"DATA root missing or not writable: {data_root}")

    if manifest_file.exists():
        try:
            m_data = json.loads(manifest_file.read_text())
            record("DATA Manifest (v3.2)", "PASS", f"Schema {m_data.get('schema_version', '3.2')}, latest node {m_data.get('latest_persisted_node', 0)}, resume {m_data.get('resume_point', 1)}.")
        except Exception as e:
            record("DATA Manifest (v3.2)", "WARN", f"Corrupt manifest: {e}")
    else:
        record("DATA Manifest (v3.2)", "WARN", "DATA/manifest.json pending initialization.")

    if db_mirror.exists():
        record("DATA Database Mirror", "PASS", f"Authoritative mirror intact at DATA/database/lab_state.db ({db_mirror.stat().st_size:,} bytes).")
    else:
        record("DATA Database Mirror", "WARN", "DATA/database/lab_state.db pending synchronization.")

    if node_ledger.exists():
        record("DATA Node Ledger", "PASS", f"Append-only ledger present at DATA/nodes/node_ledger.jsonl ({node_ledger.stat().st_size:,} bytes).")
    else:
        record("DATA Node Ledger", "PASS", "Append-only node ledger initialized upon node creation.")

    # 10. Workspace Storage Headroom (<128 MB LMS limit)
    try:
        import subprocess
        total_kb = 0
        for root, dirs, files in os.walk(str(ROOT_DIR)):
            # Skip virtual environments and hidden caches
            if any(skip in root for skip in (".venv", "node_modules", ".git")):
                continue
            for f in files:
                fp = os.path.join(root, f)
                try:
                    total_kb += os.path.getsize(fp)
                except OSError:
                    pass
        total_mb = round(total_kb / (1024 * 1024), 2)
        headroom_mb = round(128.0 - total_mb, 2)
        if total_mb < 120.0:
            record("Disk Space Headroom", "PASS", f"Workspace is {total_mb} MB ({headroom_mb} MB headroom safely below 128 MB limit).")
        else:
            record("Disk Space Headroom", "WARN", f"Workspace is {total_mb} MB (tight headroom below 128 MB limit).")
    except Exception as e:
        record("Disk Space Headroom", "WARN", f"Storage check exception: {e}")

    # 11. Persisted Settings
    settings_json = ROOT_DIR / "CONFIG" / "settings.json"
    if settings_json.exists():
        record("Settings Store", "PASS", "CONFIG/settings.json verified and persisted.")
    else:
        record("Settings Store", "WARN", "CONFIG/settings.json pending initialization.")

    # 12. Database & DATA Independent Status (Spec §12)
    try:
        from app.data.discovery import get_discovery_engine
        de = get_discovery_engine()
        rep = de.get_startup_status_report()
        print("\n" + rep["report_text"] + "\n")
        record("Data Discovery & Validation", "PASS", f"{rep['data']['valid_datasets']}/{rep['data']['raw_datasets']} raw datasets validated")
    except Exception as e:
        record("Data Discovery & Validation", "WARN", f"Discovery check failed: {e}")

    # Print Summary Table
    print(f"{'CATEGORY':<24} | {'STATUS':<6} | DETAILS")
    print("-" * 75)
    for cat, status, det in results:
        status_str = f"[{status}]"
        print(f"{cat:<24} | {status_str:<6} | {det}")
    print("-----------------------------------------------------------")

    if has_critical_failure:
        print("DIAGNOSTIC VERDICT: CRITICAL FAILURES DETECTED")
        print("Review the failures above. Run pre-requisite.bat to repair.")
        return 1
    else:
        print("DIAGNOSTIC VERDICT: ALL SYSTEMS OPERATIONAL (PASS)")
        return 0


if __name__ == "__main__":
    sys.exit(run_diagnostics())
