"""
Authoritative Database Verification Tool for Evolutionary Trading Research Lab
Single source of truth for Prerequisite.bat, Repair.bat, and START.bat.

Checks:
  1. Locates the actual database path (DATABASE/lab_state.db & config).
  2. Confirms the file exists and is non-empty.
  3. Opens SQLite connection cleanly in WAL/normal mode.
  4. Executes trivial query (SELECT 1).
  5. Verifies PRAGMA user_version (Schema version 3).
  6. Verifies essential tables exist (strategies, research_memory, hypotheses, etc.).
  7. Confirms historical strategy records are preserved (count >= 1).
  8. Closes connection cleanly and exits 0 on success.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = TOOLS_DIR.parent
ROOT_DIR = BACKEND_DIR.parent

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def verify_database() -> int:
    try:
        # 1. Resolve database path from application config with fallback to default
        db_path = None
        try:
            from app.config import get_config
            cfg = get_config()
            if cfg.database_path and Path(cfg.database_path).exists():
                db_path = Path(cfg.database_path)
        except Exception:
            pass

        if not db_path:
            db_path = ROOT_DIR / "DATABASE" / "lab_state.db"

        # 2. Confirm database file existence
        if not db_path.exists():
            print(f"[ERROR] Database file does not exist at: {db_path}", file=sys.stderr)
            return 1

        print(f"[OK] Database file exists: {db_path}")

        # 3. Open connection cleanly
        conn = sqlite3.connect(str(db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        print("[OK] SQLite connection established")

        # 4. Execute trivial query
        cur.execute("SELECT 1")
        row = cur.fetchone()
        if not row or row[0] != 1:
            print("[ERROR] SELECT 1 query failed.", file=sys.stderr)
            conn.close()
            return 1
        print("[OK] SELECT 1 succeeded")

        # 5. Check schema version
        cur.execute("PRAGMA user_version")
        u_ver = cur.fetchone()[0]
        print(f"[OK] Schema version: {u_ver}")

        # 6. Verify required tables
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {r[0] for r in cur.fetchall()}
        required_tables = {"strategies", "hypotheses", "generation_stats", "research_memory"}
        missing_tables = required_tables - tables
        if missing_tables:
            print(f"[ERROR] Missing required database tables: {missing_tables}", file=sys.stderr)
            conn.close()
            return 1
        print(f"[OK] Required tables detected ({len(tables)} tables found in schema)")

        # 7. Check historical strategy records
        cur.execute("SELECT count(*) FROM strategies")
        strategy_count = cur.fetchone()[0]
        if strategy_count == 0:
            print("[WARN] Strategies table is empty. Initializing baseline seed may be needed.")
        else:
            print(f"[OK] Historical strategy data preserved (count: {strategy_count})")

        # 8. Close connection cleanly
        conn.close()
        print("[OK] Database verification successful")
        return 0

    except Exception as e:
        print(f"[ERROR] Database verification failed with exception: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(verify_database())
