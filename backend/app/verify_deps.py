"""
Evolutionary Trading Research Lab - Dependency Verification & Self-Repair (V2.3)

Strictly verifies the exact Python environment that the application executes in.
Self-repairs missing runtime packages where safe, checks MetaTrader 5 compatibility,
tests database connectivity, and executes the real application import smoke test.
"""
from __future__ import annotations

import importlib
import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

# Ensure project root & backend are on sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
ROOT_DIR = BACKEND_DIR.parent

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app import paths as P

LOG_DIR = P.LOGS_DIR
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "prerequisite.log"


def log_msg(level: str, msg: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"{timestamp} [{level}] {msg}"
    print(formatted)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as fp:
            fp.write(formatted + "\n")
    except Exception:
        pass


# Required runtime dependencies: (import_module_name, pip_package_spec)
REQUIRED_DEPENDENCIES: List[Tuple[str, str]] = [
    ("psutil", "psutil>=5.9.0"),
    ("fastapi", "fastapi>=0.110"),
    ("uvicorn", "uvicorn[standard]>=0.29"),
    ("pydantic", "pydantic>=2.6"),
    ("yaml", "pyyaml>=6.0"),
    ("httpx", "httpx>=0.27"),
    ("websockets", "websockets>=12.0"),
    ("numpy", "numpy>=1.26"),
    ("pandas", "pandas>=2.1"),
    ("pyarrow", "pyarrow>=15.0"),
    ("duckdb", "duckdb>=0.10"),
]


def verify_python_version() -> bool:
    v = sys.version_info
    log_msg("INFO", f"Python interpreter: {sys.executable}")
    log_msg("INFO", f"Python version: {v.major}.{v.minor}.{v.micro} ({sys.platform})")

    if v.major < 3 or (v.major == 3 and v.minor < 10):
        log_msg("ERROR", f"Python 3.10+ is required. Detected Python {v.major}.{v.minor}.{v.micro}.")
        return False

    if v.major == 3 and v.minor >= 14:
        log_msg("WARN", f"Python {v.major}.{v.minor} detected (pre-release/latest version).")
        log_msg("INFO", "Some binary C-extensions like MetaTrader5 on Windows may require Python 3.10 - 3.13.")
        log_msg("INFO", "The research lab will operate cleanly using the SIMULATOR market data engine.")
    else:
        log_msg("OK", f"Python {v.major}.{v.minor}.{v.micro} is within the fully tested runtime range (3.10 - 3.13).")

    return True


def verify_and_repair_dependency(mod_name: str, pkg_spec: str) -> bool:
    try:
        mod = importlib.import_module(mod_name)
        ver = getattr(mod, "__version__", "detected")
        log_msg("OK", f"{mod_name} verified ({ver}).")
        return True
    except (ImportError, ModuleNotFoundError) as err:
        log_msg("WARN", f"{mod_name} import failed: {err}")
        log_msg("ACTION", f"Attempting automatic self-repair: pip install {pkg_spec}...")

        try:
            cmd = [sys.executable, "-m", "pip", "install", pkg_spec]
            log_msg("INFO", f"Executing: {' '.join(cmd)}")
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if res.returncode != 0:
                log_msg("ERROR", f"Failed to install {pkg_spec}: {res.stderr.strip()}")
                return False

            # Verify import again
            mod = importlib.import_module(mod_name)
            ver = getattr(mod, "__version__", "repaired")
            log_msg("OK", f"{mod_name} installed, repaired, and verified ({ver}).")
            return True
        except Exception as repair_err:
            log_msg("ERROR", f"Self-repair for {mod_name} failed: {repair_err}")
            return False


def verify_metatrader5() -> None:
    if sys.platform != "win32":
        log_msg("INFO", f"Operating system is {sys.platform}. MetaTrader 5 bridge runs in high-fidelity SIMULATOR mode.")
        return

    # Windows environment:
    try:
        import MetaTrader5 as mt5
        ver = getattr(mt5, "__version__", "detected")
        log_msg("OK", f"MetaTrader5 Python package installed ({ver}).")
    except (ImportError, ModuleNotFoundError):
        v = sys.version_info
        if v.major == 3 and v.minor >= 14:
            log_msg("WARN", f"MetaTrader5 binary wheel is not available for Python {v.major}.{v.minor} on PyPI.")
            log_msg("INFO", "The lab will automatically operate with the built-in SIMULATOR bridge.")
            log_msg("INFO", "To connect to live MT5 terminals, install Python 3.11 or 3.12.")
        else:
            log_msg("WARN", "MetaTrader5 Python package is missing on Windows.")
            log_msg("ACTION", "Attempting self-repair: pip install MetaTrader5>=5.0.45...")
            try:
                cmd = [sys.executable, "-m", "pip", "install", "MetaTrader5>=5.0.45"]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                if res.returncode == 0:
                    import MetaTrader5 as mt5
                    ver = getattr(mt5, "__version__", "installed")
                    log_msg("OK", f"MetaTrader5 installed and verified ({ver}).")
                else:
                    log_msg("WARN", f"MetaTrader5 installation failed: {res.stderr.strip()[:200]}")
                    log_msg("INFO", "Lab will operate with SIMULATOR bridge.")
            except Exception as e:
                log_msg("WARN", f"MetaTrader5 package installation error: {e}. Falling back to SIMULATOR.")


def verify_database() -> bool:
    db_file = P.database_file()
    db_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(str(db_file), timeout=10.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("PRAGMA user_version")
        ver = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='strategies'")
        has_strat = cur.fetchone()[0] > 0
        strat_count = 0
        if has_strat:
            cur.execute("SELECT count(*) FROM strategies")
            strat_count = cur.fetchone()[0]
        conn.close()
        log_msg("OK", f"Database verified at DATABASE/lab_state.db (user_version={ver}, strategies={strat_count}). Historical data preserved.")
        return True
    except Exception as e:
        log_msg("ERROR", f"Database verification failed: {e}")
        return False


def verify_application_import() -> bool:
    try:
        from app.main import app
        ver = getattr(app, "version", "unknown")
        log_msg("OK", f"Application main module imported successfully (FastAPI v{ver}).")
        return True
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        log_msg("ERROR", f"Application main module import failed: {e}")
        log_msg("ERROR", f"Traceback:\n{tb}")
        return False


def main() -> int:
    log_msg("INFO", "===========================================================")
    log_msg("INFO", "EVOLUTIONARY TRADING RESEARCH LAB - V2.3 DEPENDENCY VERIFIER")
    log_msg("INFO", "===========================================================")

    if not verify_python_version():
        return 1

    all_passed = True
    for mod_name, pkg_spec in REQUIRED_DEPENDENCIES:
        if not verify_and_repair_dependency(mod_name, pkg_spec):
            all_passed = False

    if not all_passed:
        log_msg("ERROR", "One or more critical runtime dependencies could not be verified or repaired.")
        return 1

    verify_metatrader5()

    if not verify_database():
        return 1

    if not verify_application_import():
        return 1

    log_msg("OK", "ALL BACKEND RUNTIME DEPENDENCIES & APPLICATION ENTRYPOINTS VERIFIED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
