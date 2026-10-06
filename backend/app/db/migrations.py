"""
Safe schema migrations (spec §2, §40).

Startup: locate the existing database (V1 or V2), read PRAGMA user_version,
apply only the missing migrations inside transactions, preserve every
existing row. On failure: roll back, write LOGS/migration_error.log and
raise MigrationError so the app STOPS instead of silently resetting.

V1 databases have user_version = 0 (SQLite default). V2 target = 2.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import traceback
from typing import Callable, List, Tuple

from .. import paths as P
from ..versions import DB_SCHEMA_VERSION

log = logging.getLogger("db.migrations")


class MigrationError(RuntimeError):
    pass


# ---------------- V2 additions ----------------

V2_TABLES = """
CREATE TABLE IF NOT EXISTS master_datasets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
    source TEXT NOT NULL, broker TEXT DEFAULT '', server TEXT DEFAULT '',
    profile TEXT NOT NULL,
    first_ts REAL, last_ts REAL, rows INTEGER DEFAULT 0,
    version INTEGER DEFAULT 0,
    schema_version INTEGER,
    fingerprint TEXT,
    meta_path TEXT,
    conflicts INTEGER DEFAULT 0,
    updated_at REAL,
    UNIQUE(symbol, timeframe, profile)
);
CREATE TABLE IF NOT EXISTS data_conflicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL, detected_at REAL NOT NULL,
    symbol TEXT, timeframe TEXT, profile TEXT,
    old_values TEXT, new_values TEXT,
    source TEXT, broker TEXT, server TEXT,
    resolution TEXT DEFAULT 'kept_old'
);
CREATE INDEX IF NOT EXISTS idx_conflicts_profile ON data_conflicts(profile, ts);
CREATE TABLE IF NOT EXISTS integrity_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL, symbol TEXT, timeframe TEXT, profile TEXT,
    stage TEXT, report TEXT, quarantined INTEGER DEFAULT 0,
    quarantine_path TEXT
);
CREATE TABLE IF NOT EXISTS feature_meta (
    master_key TEXT NOT NULL,
    column_name TEXT NOT NULL,
    algo_hash TEXT, engine_version INTEGER,
    rows_covered INTEGER DEFAULT 0,
    dataset_version INTEGER DEFAULT 0,
    computed_at REAL,
    PRIMARY KEY (master_key, column_name)
);
CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL, level TEXT NOT NULL, category TEXT NOT NULL,
    message TEXT NOT NULL,
    strategy_id INTEGER, generation INTEGER, experiment_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_activity_ts ON activity(ts);
CREATE INDEX IF NOT EXISTS idx_activity_cat ON activity(category);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS research_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id INTEGER,
    child_id INTEGER,
    hypothesis_id INTEGER,
    hypothesis TEXT,
    action TEXT,
    params TEXT,
    changed_variables TEXT,
    unchanged_variables TEXT,
    symbol TEXT,
    timeframe TEXT,
    regime TEXT,
    parent_fitness REAL,
    child_fitness REAL,
    fitness_delta REAL,
    outcome TEXT,
    failure_reason TEXT,
    survival_reason TEXT,
    learned_rule TEXT,
    created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_research_mem_action ON research_memory(action, symbol, timeframe);
CREATE INDEX IF NOT EXISTS idx_research_mem_outcome ON research_memory(outcome);
"""

# (table, column, ddl-type) — applied idempotently via table_info checks
V2_COLUMNS: List[Tuple[str, str, str]] = [
    ("datasets", "kind", "TEXT DEFAULT 'legacy'"),
    ("datasets", "master_profile", "TEXT"),
    ("datasets", "dataset_version", "INTEGER"),
    ("datasets", "fingerprint", "TEXT"),
    ("backtests", "fingerprint", "TEXT"),
    ("backtests", "dataset_version", "INTEGER"),
    ("backtests", "engine_versions", "TEXT"),
    ("backtests", "legacy_v1", "INTEGER DEFAULT 0"),
    ("backtests", "stale", "INTEGER DEFAULT 0"),
    ("backtests", "research_path", "TEXT"),
    ("validations", "fingerprint", "TEXT"),
    ("validations", "legacy_v1", "INTEGER DEFAULT 0"),
    ("validations", "stale", "INTEGER DEFAULT 0"),
    ("strategies", "seed", "INTEGER"),
    ("strategies", "mutation_params", "TEXT"),
    ("strategies", "config_version", "TEXT"),
    ("strategies", "software_version", "TEXT"),
    ("strategies", "failure_reason", "TEXT"),
    ("strategies", "survival_reason", "TEXT"),
    ("paper_trades", "sl", "REAL"),
    ("paper_trades", "tp", "REAL"),
    ("paper_trades", "trailing_state", "TEXT"),
    ("paper_trades", "atr", "REAL"),
    ("paper_trades", "tf", "TEXT"),
    ("paper_trades", "max_hold_bars", "INTEGER"),
]

V2_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_bt_fingerprint ON backtests(fingerprint);
CREATE INDEX IF NOT EXISTS idx_bt_stale ON backtests(strategy_id, stage, stale);
CREATE INDEX IF NOT EXISTS idx_datasets_kind ON datasets(kind);
"""


def _user_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row else 0


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                       (name,)).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> set:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _fail(stage: str, e: Exception) -> MigrationError:
    P.ensure_layout()
    err_file = P.log_file("migration_error.log")
    with open(err_file, "a", encoding="utf-8") as fh:
        fh.write(f"\n==== {time.strftime('%Y-%m-%d %H:%M:%S')} migration failed "
                 f"at {stage} ====\n{traceback.format_exc()}\n")
    log.error("MIGRATION FAILED at %s: %s — see %s (database NOT replaced)",
              stage, e, err_file)
    return MigrationError(f"migration failed at {stage}: {e}")


def migrate(conn: sqlite3.Connection, base_schema: str) -> int:
    """Bring `conn` to DB_SCHEMA_VERSION. Returns the previous version.

    `base_schema` is the V1 CREATE TABLE script (idempotent) so a brand-new
    database gets the full V1 structure first, then the V2 delta — one code
    path for fresh installs and upgrades.
    """
    before = _user_version(conn)
    try:
        if before >= DB_SCHEMA_VERSION:
            return before
        if not _has_table(conn, "strategies"):
            conn.executescript(base_schema)     # fresh install: V1 base
        # V2 delta — everything is idempotent
        conn.executescript(V2_TABLES)
        for table, col, ddl in V2_COLUMNS:
            if _has_table(conn, table) and col not in _columns(conn, table):
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")

        # Index creation (safe if tables exist)
        if _has_table(conn, "backtests"):
            conn.execute("CREATE INDEX IF NOT EXISTS idx_bt_fingerprint ON backtests(fingerprint)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_bt_stale ON backtests(strategy_id, stage, stale)")
            conn.execute("UPDATE backtests SET legacy_v1=1 WHERE fingerprint IS NULL")
        if _has_table(conn, "datasets"):
            conn.execute("CREATE INDEX IF NOT EXISTS idx_datasets_kind ON datasets(kind)")
        if _has_table(conn, "validations"):
            conn.execute("UPDATE validations SET legacy_v1=1 WHERE fingerprint IS NULL")

        conn.execute("INSERT OR REPLACE INTO meta (key,value) VALUES (?,?)",
                     ("migrated_at", json.dumps({"from": before,
                                                 "to": DB_SCHEMA_VERSION,
                                                 "ts": time.time()})))
        conn.execute(f"PRAGMA user_version = {DB_SCHEMA_VERSION}")
        conn.commit()
        log.info("database schema migrated v%s -> v%s (all rows preserved)",
                 before, DB_SCHEMA_VERSION)
        return before
    except Exception as e:
        conn.rollback()
        raise _fail(f"user_version {before} -> {DB_SCHEMA_VERSION}", e)
