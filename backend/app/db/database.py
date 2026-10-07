"""
SQLite persistence layer for lab state: strategies (genomes + ancestry),
backtests, validations, paper trades, execution stats, hypotheses, events.

Large market/feature datasets live in Parquet (see app/data/storage.py) and
can be queried with DuckDB; this DB holds metadata & research state only.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS strategies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hash TEXT UNIQUE NOT NULL,
    parent_id INTEGER,
    generation INTEGER NOT NULL DEFAULT 0,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    direction TEXT NOT NULL DEFAULT 'both',
    status TEXT NOT NULL DEFAULT 'BORN',
    creation_reason TEXT,
    mutation_type TEXT,
    species_key TEXT,
    genome TEXT NOT NULL,
    complexity INTEGER NOT NULL DEFAULT 0,
    fitness REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    origin TEXT NOT NULL DEFAULT 'seed',
    hypothesis_id INTEGER,
    run_id TEXT,
    data_source TEXT DEFAULT 'USER_RESEARCH',
    research_node_num INTEGER
);
CREATE INDEX IF NOT EXISTS idx_strategies_status ON strategies(status);
CREATE INDEX IF NOT EXISTS idx_strategies_gen ON strategies(generation);
CREATE INDEX IF NOT EXISTS idx_strategies_fitness ON strategies(fitness);
CREATE INDEX IF NOT EXISTS idx_strategies_parent ON strategies(parent_id);
CREATE INDEX IF NOT EXISTS idx_strategies_run ON strategies(run_id);
CREATE INDEX IF NOT EXISTS idx_strategies_data_source ON strategies(data_source);
CREATE INDEX IF NOT EXISTS idx_strategies_research_node_num ON strategies(research_node_num);

CREATE TABLE IF NOT EXISTS backtests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id INTEGER NOT NULL,
    stage TEXT NOT NULL,            -- screen | detail | oos | walkforward | stress | perturbation | montecarlo | matrix
    dataset_id TEXT NOT NULL,
    window TEXT,                    -- json: {start,end,tag}
    params TEXT,                    -- json: stress/perturbation params if any
    metrics TEXT NOT NULL,          -- json blob
    fitness REAL,
    verdict TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bt_strategy ON backtests(strategy_id);

CREATE TABLE IF NOT EXISTS validations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id INTEGER NOT NULL UNIQUE,
    oos TEXT, walkforward TEXT, perturbation TEXT,
    spread_stress TEXT, slippage_stress TEXT, montecarlo TEXT,
    regime_holdout TEXT,
    robustness_score REAL,
    passed INTEGER,
    notes TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS matrices (
    strategy_id INTEGER NOT NULL PRIMARY KEY,
    timeframe TEXT, session TEXT, day TEXT, regime TEXT, direction TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    signal_ts REAL NOT NULL,
    requested_price REAL,
    bid REAL, ask REAL, spread_points REAL,
    exec_ts REAL, exec_price REAL,
    exec_delay_ms REAL, slippage_points REAL,
    exit_ts REAL, exit_price REAL, exit_reason TEXT,
    lots REAL, pnl REAL, status TEXT NOT NULL DEFAULT 'OPEN',
    regime TEXT, genome_hash TEXT, source TEXT NOT NULL DEFAULT 'SIMULATOR',
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_pt_strategy ON paper_trades(strategy_id);

CREATE TABLE IF NOT EXISTS executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL, source TEXT NOT NULL,   -- SIMULATOR | MT5_DEMO | MT5_LIVE
    symbol TEXT, side TEXT, strategy_id INTEGER,
    requested_price REAL, bid REAL, ask REAL, spread_points REAL,
    signal_ts REAL, exec_ts REAL, exec_delay_ms REAL,
    slippage_points REAL, result TEXT, rejected_by TEXT
);

CREATE TABLE IF NOT EXISTS hypotheses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id INTEGER,
    source TEXT NOT NULL DEFAULT 'rule_analyzer',  -- rule_analyzer | llm
    observation TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    proposal TEXT NOT NULL,     -- json: genome mutation proposal
    status TEXT NOT NULL DEFAULT 'PROPOSED',   -- PROPOSED | APPLIED | REJECTED
    child_strategy_id INTEGER,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    type TEXT NOT NULL,
    payload TEXT
);

CREATE TABLE IF NOT EXISTS datasets (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
    start_ts REAL, end_ts REAL, bars INTEGER,
    source TEXT NOT NULL,   -- SIMULATOR | MT5
    path TEXT NOT NULL,
    feature_cache TEXT,     -- json list of cached feature specs
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS calibration (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL, source TEXT NOT NULL, symbol TEXT NOT NULL,
    metric TEXT NOT NULL, backtest_assumption REAL, observed REAL,
    samples INTEGER, notes TEXT
);

CREATE TABLE IF NOT EXISTS generation_stats (
    generation INTEGER PRIMARY KEY,
    born INTEGER, tested INTEGER, failed INTEGER, survived INTEGER,
    validated INTEGER, qualified INTEGER, killed INTEGER, retired INTEGER,
    duplicates_blocked INTEGER, best_fitness REAL, avg_fitness REAL,
    ts REAL
);

CREATE TABLE IF NOT EXISTS research_runs (
    run_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    target_nodes INTEGER NOT NULL DEFAULT 500,
    generated_nodes INTEGER NOT NULL DEFAULT 0,
    completed_nodes INTEGER NOT NULL DEFAULT 0,
    qualified_nodes INTEGER NOT NULL DEFAULT 0,
    current_generation INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'IDLE',
    persistence_status TEXT NOT NULL DEFAULT 'PERSISTED',
    last_checkpoint REAL,
    meta TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_created ON research_runs(created_at);

-- V4.6 — MT5 historical backtest runs (additive; one row per user-triggered run).
-- A historical backtest is a RESEARCH execution: it is stored here, never in
-- mt5_backtests (legacy V4 record), never in executions / paper_trades /
-- mt5_demo_trades / live_test_trades (order execution audit layers).
CREATE TABLE IF NOT EXISTS mt5_historical_runs (
    run_id TEXT PRIMARY KEY,
    strategy_id INTEGER NOT NULL,
    label TEXT NOT NULL DEFAULT 'HISTORICAL MT5 BACKTEST',
    status TEXT NOT NULL,
    data_scope TEXT NOT NULL DEFAULT 'MT5',
    data_source TEXT,
    dataset_id TEXT,
    dataset_path TEXT,
    dataset_fingerprint TEXT,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    start_ts REAL,
    end_ts REAL,
    start_date TEXT,
    end_date TEXT,
    bars INTEGER,
    initial_balance REAL,
    risk_per_trade REAL,
    config TEXT,
    request_key TEXT,
    genome_hash TEXT,
    strategy_identity TEXT,
    provenance TEXT,
    engine_versions TEXT,
    metrics TEXT,
    trade_count INTEGER,
    equity_points INTEGER,
    artifacts TEXT,
    runtime_ms REAL,
    error TEXT,
    notes TEXT,
    diagnostic_legacy INTEGER DEFAULT 0,
    research_eligible INTEGER DEFAULT 1,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS idx_mt5_hr_strategy ON mt5_historical_runs(strategy_id, created_at);
CREATE INDEX IF NOT EXISTS idx_mt5_hr_status ON mt5_historical_runs(status);
CREATE INDEX IF NOT EXISTS idx_mt5_hr_key ON mt5_historical_runs(request_key, status);

CREATE TABLE IF NOT EXISTS mt5_backtests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id INTEGER NOT NULL,
    run_id TEXT,
    config TEXT,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    start_date TEXT,
    end_date TEXT,
    initial_capital REAL NOT NULL DEFAULT 10000.0,
    final_capital REAL NOT NULL DEFAULT 10000.0,
    net_profit REAL NOT NULL DEFAULT 0.0,
    gross_profit REAL NOT NULL DEFAULT 0.0,
    gross_loss REAL NOT NULL DEFAULT 0.0,
    profit_factor REAL NOT NULL DEFAULT 0.0,
    win_rate REAL NOT NULL DEFAULT 0.0,
    trade_count INTEGER NOT NULL DEFAULT 0,
    max_drawdown_pct REAL NOT NULL DEFAULT 0.0,
    relative_drawdown_pct REAL NOT NULL DEFAULT 0.0,
    recovery_factor REAL NOT NULL DEFAULT 0.0,
    sharpe REAL NOT NULL DEFAULT 0.0,
    avg_trade REAL NOT NULL DEFAULT 0.0,
    largest_win REAL NOT NULL DEFAULT 0.0,
    largest_loss REAL NOT NULL DEFAULT 0.0,
    consecutive_wins INTEGER NOT NULL DEFAULT 0,
    consecutive_losses INTEGER NOT NULL DEFAULT 0,
    mt5_build TEXT,
    status TEXT NOT NULL,
    notes TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mt5_bt_strategy ON mt5_backtests(strategy_id);

CREATE TABLE IF NOT EXISTS live_test_configs (
    strategy_id INTEGER PRIMARY KEY,
    timeframes TEXT,
    days TEXT,
    sessions TEXT,
    start_time TEXT,
    end_time TEXT,
    timezone TEXT DEFAULT 'UTC',
    lot_size REAL DEFAULT 0.1,
    risk_pct REAL DEFAULT 1.0,
    is_active INTEGER DEFAULT 0,
    status TEXT DEFAULT 'IDLE',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS live_test_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id INTEGER NOT NULL,
    ticket INTEGER,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    side TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL,
    sl REAL,
    tp REAL,
    lots REAL NOT NULL DEFAULT 0.1,
    pnl REAL NOT NULL DEFAULT 0.0,
    pnl_pct REAL NOT NULL DEFAULT 0.0,
    open_ts REAL NOT NULL,
    close_ts REAL,
    close_reason TEXT,
    session TEXT,
    day_of_week TEXT,
    status TEXT NOT NULL DEFAULT 'OPEN'
);
CREATE INDEX IF NOT EXISTS idx_live_trades_strategy ON live_test_trades(strategy_id);

CREATE TABLE IF NOT EXISTS mt5_demo_configs (
    strategy_id INTEGER PRIMARY KEY,
    enabled INTEGER DEFAULT 0,
    confirmed_demo_only INTEGER DEFAULT 0,
    magic_number INTEGER,
    max_positions INTEGER DEFAULT 1,
    lot_size REAL DEFAULT 0.05,
    risk_pct REAL DEFAULT 0.5,
    status TEXT DEFAULT 'STOPPED',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS mt5_demo_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id INTEGER NOT NULL,
    order_id INTEGER,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    side TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL,
    sl REAL,
    tp REAL,
    lots REAL NOT NULL,
    pnl REAL NOT NULL DEFAULT 0.0,
    open_ts REAL NOT NULL,
    close_ts REAL,
    status TEXT NOT NULL DEFAULT 'OPEN'
);
CREATE INDEX IF NOT EXISTS idx_mt5_demo_strategy ON mt5_demo_trades(strategy_id);

CREATE TABLE IF NOT EXISTS strategy_pipeline_states (
    strategy_id INTEGER PRIMARY KEY,
    stage TEXT NOT NULL DEFAULT 'GENERATED',
    notes TEXT,
    updated_at REAL NOT NULL
);
"""


def _safe_genome(raw: Any) -> Any:
    """Parse a stored genome without ever raising (V4.7 node-detail hardening).

    Returns the parsed object when it is a JSON object, the parsed scalar when a
    legacy row stored a scalar, and an empty dict when the payload is missing or
    unparseable - callers then render N/A instead of crashing the page.
    """
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = raw.decode("utf-8", errors="replace")
        except Exception:
            return None
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


class Database:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        p = Path(path).resolve()
        from .. import paths as P
        default_db = P.database_file().resolve()
        if p == default_db and (not p.exists() or p.stat().st_size == 0):
            mirror = P.DATABASE_DATA_DIR / "lab_state.db"
            if mirror.exists() and mirror.stat().st_size > 0:
                p.parent.mkdir(parents=True, exist_ok=True)
                # V4.7: a consistent snapshot (the mirror may be a live WAL
                # database; a plain file copy can be inconsistent)
                try:
                    from .snapshot import sqlite_snapshot
                    sqlite_snapshot(mirror, p)
                except Exception as e:
                    import logging
                    logging.getLogger("db").warning("database mirror bootstrap failed: %s", e)
        p.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._identity = _db_identity(path)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            # Ensure run_id column exists in existing strategies table before SCHEMA executes indexes
            tables = [r[0] for r in self._conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='strategies'").fetchall()]
            if tables:
                cols = [col[1] for col in self._conn.execute("PRAGMA table_info(strategies)").fetchall()]
                if "run_id" not in cols:
                    self._conn.execute("ALTER TABLE strategies ADD COLUMN run_id TEXT")
                    self._conn.commit()
                if "data_source" not in cols:
                    self._conn.execute("ALTER TABLE strategies ADD COLUMN data_source TEXT DEFAULT 'USER_RESEARCH'")
                    self._conn.commit()
                if "research_node_num" not in cols:
                    self._conn.execute("ALTER TABLE strategies ADD COLUMN research_node_num INTEGER")
                    self._conn.commit()

                # V3.6: Non-destructive run isolation & dummy/legacy classification
                # Classify the ~787 dummy/legacy development records
                self._conn.execute("""
                    UPDATE strategies
                    SET data_source = 'LEGACY_TEST'
                    WHERE (id <= 787 OR run_id IS NULL OR run_id LIKE '%TEST%' OR run_id = 'RUN-HISTORICAL-PRESERVED')
                      AND (data_source IS NULL OR data_source != 'LEGACY_TEST')
                """)
                self._conn.execute("""
                    UPDATE strategies
                    SET data_source = 'USER_RESEARCH'
                    WHERE data_source IS NULL
                """)
                # Dynamic relative node calculation: relative index within partition
                self._conn.execute("""
                    WITH numbered AS (
                        SELECT id, ROW_NUMBER() OVER (PARTITION BY COALESCE(run_id, data_source) ORDER BY id) as rn
                        FROM strategies
                    )
                    UPDATE strategies
                    SET research_node_num = (SELECT rn FROM numbered WHERE numbered.id = strategies.id)
                    WHERE research_node_num IS NULL
                """)
                self._conn.commit()
            self._conn.executescript(SCHEMA)
            self._conn.commit()
            # V2: safe schema migration (preserves every V1 row; on failure
            # raises MigrationError and writes LOGS/migration_error.log)
            from .migrations import migrate
            migrate(self._conn, SCHEMA)

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    # ---------- meta key/value ----------
    def get_meta(self, key: str, default=None):
        row = self.one("SELECT value FROM meta WHERE key=?", (key,))
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self.x("INSERT OR REPLACE INTO meta (key,value) VALUES (?,?)", (key, value))

    def replaced_on_disk(self) -> bool:
        """True when the database file was replaced since this connection opened.

        V4.7: detects a restored/copied-over database so the caller can reopen
        instead of reading from a stale inode (and instead of surfacing the
        confusing "database disk image is malformed" that follows a replacement).
        """
        current = _db_identity(self.path)
        return current is not None and getattr(self, "_identity", None) != current

    def reopen(self) -> bool:
        """Reopen the connection against the current file. Returns True if it did."""
        if not self.replaced_on_disk():
            return False
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = sqlite3.connect(self.path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._identity = _db_identity(self.path)
        log.warning("database file %s was replaced on disk; connection reopened", self.path)
        return True

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    # ---------- helpers ----------
    def q(self, sql: str, args: tuple = ()) -> List[Dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(sql, args)
            rows = [dict(r) for r in cur.fetchall()]
        return rows

    def one(self, sql: str, args: tuple = ()) -> Optional[Dict[str, Any]]:
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def x(self, sql: str, args: tuple = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, args)
            self._conn.commit()
            return cur.lastrowid or cur.rowcount

    def xm(self, sql: str, seq: List[tuple]) -> None:
        with self._lock:
            self._conn.executemany(sql, seq)
            self._conn.commit()

    def transaction(self, statements: List[tuple]) -> List[int]:
        """Execute (sql, args) statements atomically.

        V4.1: used by the research-run reset/restore so a study reset or recovery
        is all-or-nothing. `args` may be a tuple (single statement) or a list of
        tuples (executemany). Returns the affected rowcount of each statement;
        rolls back everything and re-raises on error.
        """
        with self._lock:
            counts: List[int] = []
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                for sql, args in statements:
                    if isinstance(args, list):
                        cur = self._conn.executemany(sql, args)
                        counts.append(int(cur.rowcount if cur.rowcount is not None else len(args)))
                    else:
                        cur = self._conn.execute(sql, args)
                        counts.append(int(cur.rowcount if cur.rowcount is not None else 0))
                self._conn.commit()
                return counts
            except Exception:
                self._conn.rollback()
                raise

    # ---------- strategies ----------
    def insert_strategy(self, s: Dict[str, Any]) -> Optional[int]:
        now = time.time()
        rid = s.get("run_id") or ""
        data_source = s.get("data_source")
        if not data_source:
            # V5: default to the research population. LEGACY_TEST is a
            # classification of *imported historical* records and is applied by
            # the one-time V3.6 migration (and by explicit callers), never
            # inferred here from the run id's spelling.
            data_source = "USER_RESEARCH"

        research_node_num = s.get("research_node_num")
        if research_node_num is None:
            if rid:
                cnt = self.one("SELECT COUNT(*) c FROM strategies WHERE run_id=?", (rid,))["c"]
            else:
                cnt = self.one("SELECT COUNT(*) c FROM strategies WHERE data_source=?", (data_source,))["c"]
            research_node_num = cnt + 1

        try:
            return self.x(
                """INSERT INTO strategies
                   (hash,parent_id,generation,symbol,timeframe,direction,status,
                    creation_reason,mutation_type,species_key,genome,complexity,
                    fitness,created_at,updated_at,origin,hypothesis_id,run_id,
                    data_source,research_node_num)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (s["hash"], s.get("parent_id"), s.get("generation", 0), s["symbol"],
                 s["timeframe"], s.get("direction", "both"), s.get("status", "BORN"),
                 s.get("creation_reason"), s.get("mutation_type"), s.get("species_key"),
                 json.dumps(s["genome"]), s.get("complexity", 0), s.get("fitness"),
                 now, now, s.get("origin", "seed"), s.get("hypothesis_id"),
                 s.get("run_id"), data_source, research_node_num),
            )
        except sqlite3.IntegrityError:
            # duplicate genome hash -> caller handles dedupe
            return None

    def find_by_hash(self, h: str) -> Optional[Dict[str, Any]]:
        return self.one("SELECT * FROM strategies WHERE hash=?", (h,))

    def update_strategy(self, sid: int, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = time.time()
        sets = ",".join(f"{k}=?" for k in fields)
        self.x(f"UPDATE strategies SET {sets} WHERE id=?", tuple(fields.values()) + (sid,))

    def get_strategy(self, sid: int) -> Optional[Dict[str, Any]]:
        row = self.one("SELECT * FROM strategies WHERE id=?", (sid,))
        if row:
            # V4.7: a single node with a missing/malformed/non-object genome must
            # never turn into a 500 for the whole node-detail page. The raw value
            # is preserved for diagnosis and a parse problem is reported inline.
            row["genome"] = _safe_genome(row.get("genome"))
            row["genome_valid"] = isinstance(row["genome"], dict)
            if not row["genome_valid"]:
                log.warning("strategy #%s has an unusable genome payload (%s); "
                            "served with genome_valid=false", sid, type(row.get("genome")).__name__)
                row["genome"] = row["genome"] if row["genome"] is not None else {}
            if row.get("research_node_num") is None:
                rid = row.get("run_id")
                if rid:
                    rel = self.one("SELECT COUNT(*) c FROM strategies WHERE run_id=? AND id<=?", (rid, sid))["c"]
                else:
                    rel = self.one("SELECT COUNT(*) c FROM strategies WHERE data_source=? AND id<=?", (row.get("data_source", "LEGACY_TEST"), sid))["c"]
                row["research_node_num"] = rel
        return row

    def get_qualified_strategies(self, data_source: Optional[str] = None, run_id: Optional[str] = None,
                                 exclude_legacy: bool = False) -> List[Dict[str, Any]]:
        where = ["s.status IN ('QUALIFIED', 'PAPER')"]
        args = []
        if run_id:
            where.append("s.run_id=?")
            args.append(run_id)
        if data_source and data_source.upper() != "ALL":
            where.append("s.data_source=?")
            args.append(data_source)
        elif exclude_legacy:
            # V4.0: hide LEGACY_TEST records from user-facing qualified lists
            # unless the caller explicitly asks for that data source.
            where.append("COALESCE(s.data_source, 'USER_RESEARCH') <> 'LEGACY_TEST'")
        wsql = "WHERE " + " AND ".join(where)

        rows = self.q(f"""
            SELECT s.id, s.parent_id, s.generation, s.symbol, s.timeframe, s.direction,
                   s.status, s.fitness, s.species_key, s.complexity, s.mutation_type,
                   s.creation_reason, s.survival_reason, s.created_at, s.run_id,
                   s.data_source, s.research_node_num,
                   (SELECT b.metrics FROM backtests b WHERE b.strategy_id=s.id
                      AND b.stage IN ('detail', 'screen') ORDER BY
                      CASE b.stage WHEN 'detail' THEN 0 ELSE 1 END, b.id DESC LIMIT 1) AS m,
                   (SELECT v.robustness_score FROM validations v WHERE v.strategy_id=s.id LIMIT 1) AS val_robustness,
                   (SELECT v.passed FROM validations v WHERE v.strategy_id=s.id LIMIT 1) AS val_passed,
                   (SELECT 1 FROM matrices mx WHERE mx.strategy_id=s.id LIMIT 1) AS has_matrix
            FROM strategies s
            {wsql}
            ORDER BY COALESCE(s.fitness, 0) DESC
        """, tuple(args))

        out = []
        for r in rows:
            m = json.loads(r.pop("m")) if r.get("m") else {}
            r["pf"] = m.get("profit_factor")
            r["profit_factor"] = m.get("profit_factor")
            r["return_pct"] = m.get("total_return_pct")
            r["total_return_pct"] = m.get("total_return_pct")
            r["dd"] = m.get("max_drawdown_pct")
            r["max_drawdown_pct"] = m.get("max_drawdown_pct")
            r["win_rate"] = m.get("win_rate")
            r["trades"] = m.get("trades")
            r["sharpe"] = m.get("sharpe")
            r["net_profit"] = m.get("net_profit")
            r["has_equity_curve"] = bool(m.get("equity_curve") and len(m.get("equity_curve")) > 1)
            r["has_matrix"] = bool(r.get("has_matrix"))
            out.append(r)
        return out

    def get_strategy_charts(self, sid: int) -> Dict[str, Any]:
        row = self.get_strategy(sid)
        if not row:
            return {"strategy_id": sid, "has_equity_curve": False, "equity_curve": [], "drawdown_curve": [], "trades": [], "message": "STRATEGY NOT FOUND"}
        
        rel_num = row.get("research_node_num") or sid
        # Check backtests for equity curve or trades
        bt = self.one("""SELECT metrics FROM backtests WHERE strategy_id=? AND stage='detail' ORDER BY id DESC LIMIT 1""", (sid,))
        if not bt:
            bt = self.one("""SELECT metrics FROM backtests WHERE strategy_id=? ORDER BY id DESC LIMIT 1""", (sid,))
        
        if not bt or not bt.get("metrics"):
            return {
                "strategy_id": sid,
                "research_node_num": rel_num,
                "has_equity_curve": False,
                "equity_curve": [],
                "drawdown_curve": [],
                "trades": [],
                "message": "EQUITY CURVE DATA NOT FOUND FOR THIS STRATEGY"
            }
        
        m = json.loads(bt["metrics"])
        eq = m.get("equity_curve") or []
        trades = m.get("trades_sample") or []
        
        # If equity curve is empty but trades exist, reconstruct from real trades
        if (not eq or len(eq) <= 1) and trades:
            cur_eq = 10000.0
            eq = [[trades[0].get("entry_ts") or time.time(), round(cur_eq, 2)]]
            peak = cur_eq
            dd = [[trades[0].get("entry_ts") or time.time(), 0.0]]
            for t in trades:
                cur_eq += float(t.get("pnl") or 0.0)
                ts = t.get("exit_ts") or t.get("entry_ts") or time.time()
                eq.append([ts, round(cur_eq, 2)])
                if cur_eq > peak:
                    peak = cur_eq
                cur_dd = ((peak - cur_eq) / peak) * 100.0 if peak > 0 else 0.0
                dd.append([ts, round(cur_dd, 2)])
        elif eq and len(eq) > 1:
            peak = eq[0][1] if eq else 10000.0
            dd = []
            for ts, val in eq:
                if val > peak:
                    peak = val
                cur_dd = ((peak - val) / peak) * 100.0 if peak > 0 else 0.0
                dd.append([ts, round(cur_dd, 2)])
        else:
            return {
                "strategy_id": sid,
                "research_node_num": rel_num,
                "has_equity_curve": False,
                "equity_curve": [],
                "drawdown_curve": [],
                "trades": trades,
                "message": "EQUITY CURVE DATA NOT FOUND FOR THIS STRATEGY"
            }

        return {
            "strategy_id": sid,
            "research_node_num": rel_num,
            "has_equity_curve": True,
            "equity_curve": eq,
            "drawdown_curve": dd,
            "trades": trades,
            "message": "OK"
        }

    def count_by_status(self, run_id: Optional[str] = None, exclude_legacy: bool = False) -> Dict[str, int]:
        """Count strategies grouped by status.

        exclude_legacy (V4.0): when True the ~787 LEGACY_TEST infrastructure
        records are left out of the result. The status classification itself is
        untouched - only the population scope of the count changes. Defaults to
        False so every existing (engine/orchestrator) caller behaves as before.
        """
        where, args = [], []
        if run_id:
            where.append("run_id=?"); args.append(run_id)
        if exclude_legacy:
            where.append("COALESCE(data_source, 'USER_RESEARCH') <> 'LEGACY_TEST'")
        wsql = ("WHERE " + " AND ".join(where)) if where else ""
        rows = self.q(f"SELECT status, COUNT(*) c FROM strategies {wsql} GROUP BY status", tuple(args))
        return {r["status"]: r["c"] for r in rows}

    def get_shortlist(self) -> List[int]:
        self.x("""CREATE TABLE IF NOT EXISTS research_shortlist (
            strategy_id INTEGER PRIMARY KEY,
            notes TEXT,
            created_at REAL NOT NULL
        )""")
        rows = self.q("SELECT strategy_id FROM research_shortlist ORDER BY created_at DESC")
        return [int(r["strategy_id"]) for r in rows]

    def add_to_shortlist(self, strategy_id: int, notes: str = "") -> None:
        self.x("""CREATE TABLE IF NOT EXISTS research_shortlist (
            strategy_id INTEGER PRIMARY KEY,
            notes TEXT,
            created_at REAL NOT NULL
        )""")
        self.x("INSERT OR REPLACE INTO research_shortlist (strategy_id, notes, created_at) VALUES (?,?,?)",
               (strategy_id, notes, time.time()))

    def remove_from_shortlist(self, strategy_id: int) -> None:
        self.x("""CREATE TABLE IF NOT EXISTS research_shortlist (
            strategy_id INTEGER PRIMARY KEY,
            notes TEXT,
            created_at REAL NOT NULL
        )""")
        self.x("DELETE FROM research_shortlist WHERE strategy_id=?", (strategy_id,))

    def clear_shortlist(self) -> None:
        self.x("""CREATE TABLE IF NOT EXISTS research_shortlist (
            strategy_id INTEGER PRIMARY KEY,
            notes TEXT,
            created_at REAL NOT NULL
        )""")
        self.x("DELETE FROM research_shortlist")

    def total_strategies_count(self, run_id: Optional[str] = None, exclude_legacy: bool = False) -> int:
        """Total persisted strategies.

        exclude_legacy (V4.0): omit the ~787 LEGACY_TEST infrastructure records.
        Defaults to False so existing engine/orchestrator callers are unchanged.
        """
        where, args = [], []
        if run_id:
            where.append("run_id=?"); args.append(run_id)
        if exclude_legacy:
            where.append("COALESCE(data_source, 'USER_RESEARCH') <> 'LEGACY_TEST'")
        wsql = ("WHERE " + " AND ".join(where)) if where else ""
        row = self.one(f"SELECT COUNT(*) c FROM strategies {wsql}", tuple(args))
        return int(row["c"]) if row else 0

    # ---------- research runs (spec §6, §8) ----------
    def upsert_research_run(self, rec: Dict[str, Any]) -> None:
        with self._lock:
            meta_str = json.dumps(rec.get("meta", {})) if isinstance(rec.get("meta"), dict) else rec.get("meta")
            self.x("""INSERT INTO research_runs
                    (run_id, experiment_id, created_at, target_nodes, generated_nodes,
                     completed_nodes, qualified_nodes, current_generation, status,
                     persistence_status, last_checkpoint, meta)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(run_id) DO UPDATE SET
                      experiment_id=excluded.experiment_id,
                      target_nodes=excluded.target_nodes,
                      generated_nodes=excluded.generated_nodes,
                      completed_nodes=excluded.completed_nodes,
                      qualified_nodes=excluded.qualified_nodes,
                      current_generation=excluded.current_generation,
                      status=excluded.status,
                      persistence_status=excluded.persistence_status,
                      last_checkpoint=excluded.last_checkpoint,
                      meta=excluded.meta""",
                 (rec["run_id"], rec.get("experiment_id", "EXP-XAUUSD-M15"),
                  rec.get("created_at", time.time()), rec.get("target_nodes", 500),
                  rec.get("generated_nodes", 0), rec.get("completed_nodes", 0),
                  rec.get("qualified_nodes", 0), rec.get("current_generation", 0),
                  rec.get("status", "IDLE"), rec.get("persistence_status", "PERSISTED"),
                  rec.get("last_checkpoint", time.time()), meta_str))

    def get_research_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        row = self.one("SELECT * FROM research_runs WHERE run_id=?", (run_id,))
        if row:
            d = dict(row)
            if d.get("meta"):
                try:
                    d["meta"] = json.loads(d["meta"])
                except Exception:
                    pass
            return d
        return None

    def get_all_research_runs(self) -> List[Dict[str, Any]]:
        known_rows = {r["run_id"]: dict(r) for r in self.q("SELECT * FROM research_runs ORDER BY created_at DESC")}

        strat_stats = self.q("""
            SELECT COALESCE(NULLIF(run_id, ''), 'RUN-HISTORICAL-PRESERVED') as run_id,
                   COUNT(*) as generated_nodes,
                   SUM(CASE WHEN status IN ('QUALIFIED','KILLED','FAILED','RETIRED','PAPER') THEN 1 ELSE 0 END) as completed_nodes,
                   SUM(CASE WHEN status IN ('QUALIFIED','PAPER') THEN 1 ELSE 0 END) as qualified_nodes,
                   SUM(CASE WHEN status IN ('BORN','BACKTESTING','SURVIVED') THEN 1 ELSE 0 END) as pending_backtesting,
                   SUM(CASE WHEN status IN ('VALIDATING') THEN 1 ELSE 0 END) as pending_validation,
                   MAX(generation) as current_generation,
                   MIN(created_at) as created_at,
                   MAX(updated_at) as last_checkpoint
            FROM strategies
            GROUP BY COALESCE(NULLIF(run_id, ''), 'RUN-HISTORICAL-PRESERVED')
            ORDER BY created_at DESC
        """)

        out = []
        seen_runs = set()

        for s in strat_stats:
            rid = s["run_id"]
            seen_runs.add(rid)
            known = known_rows.get(rid, {})
            target = known.get("target_nodes") or s["generated_nodes"]
            exp_id = known.get("experiment_id") or "EXP-XAUUSD-M15"
            is_done = (s["pending_backtesting"] == 0 and s["pending_validation"] == 0 and s["completed_nodes"] == s["generated_nodes"] and s["generated_nodes"] > 0)
            status = known.get("status") or ("COMPLETED" if is_done else "IDLE")

            out.append({
                "run_id": rid,
                "experiment_id": exp_id,
                "created_at": known.get("created_at") or s["created_at"],
                "node_ceiling": target,
                "target_nodes": target,
                "generated_nodes": s["generated_nodes"],
                "completed_nodes": s["completed_nodes"],
                "qualified_nodes": s["qualified_nodes"],
                "pending_backtesting": s["pending_backtesting"],
                "pending_validation": s["pending_validation"],
                "current_generation": s["current_generation"] or 0,
                "status": status,
                "run_status": status,
                "persistence_status": "PERSISTED",
                "last_checkpoint": s["last_checkpoint"] or s["created_at"],
                "meta": known.get("meta"),
            })

        for rid, known in known_rows.items():
            if rid not in seen_runs:
                out.append({
                    "run_id": rid,
                    "experiment_id": known.get("experiment_id", "EXP-XAUUSD-M15"),
                    "created_at": known.get("created_at", time.time()),
                    "node_ceiling": known.get("target_nodes", 500),
                    "target_nodes": known.get("target_nodes", 500),
                    "generated_nodes": known.get("generated_nodes", 0),
                    "completed_nodes": known.get("completed_nodes", 0),
                    "qualified_nodes": known.get("qualified_nodes", 0),
                    "pending_backtesting": 0,
                    "pending_validation": 0,
                    "current_generation": known.get("current_generation", 0),
                    "status": known.get("status", "IDLE"),
                    "run_status": known.get("status", "IDLE"),
                    "persistence_status": "PERSISTED",
                    "last_checkpoint": known.get("last_checkpoint", time.time()),
                    "meta": known.get("meta"),
                })

        return out

    def max_strategy_id(self) -> int:
        row = self.one("SELECT COALESCE(MAX(id), 0) m FROM strategies")
        return int(row["m"]) if row else 0

    def next_strategy_id(self) -> int:
        return self.max_strategy_id() + 1

    def reconstruct_state(self, total_node_target: int = 500, exclude_legacy: bool = False) -> Dict[str, Any]:
        """Fast state reconstruction from persisted SQLite database and disk (spec §V2.1 G).

        exclude_legacy (V4.0): when True, the population/status statistics
        (status_counts, total_nodes, alive, dead, qualified, remaining,
        target_reached, backtesting, validating) describe only user research
        nodes - the ~787 LEGACY_TEST infrastructure records are excluded so they
        no longer contaminate the dashboard statistics. The calculation rules
        are unchanged; only the counted population differs. Defaults to False,
        so existing callers keep the previous behaviour exactly.
        """
        counts = self.count_by_status(exclude_legacy=exclude_legacy)
        total_nodes = self.total_strategies_count(exclude_legacy=exclude_legacy)
        next_id = self.next_strategy_id()
        gen_row = self.one("SELECT COALESCE(MAX(generation), 0) g FROM strategies")
        current_gen = int(gen_row["g"]) if gen_row else 0

        # ALIVE: BORN, BACKTESTING, SURVIVED, VALIDATING, PAPER, QUALIFIED
        alive_statuses = ("BORN", "BACKTESTING", "SURVIVED", "VALIDATING", "PAPER", "QUALIFIED")
        alive = sum(counts.get(s, 0) for s in alive_statuses)

        # DEAD: FAILED, KILLED, RETIRED
        dead_statuses = ("FAILED", "KILLED", "RETIRED")
        dead = sum(counts.get(s, 0) for s in dead_statuses)

        backtesting = counts.get("BACKTESTING", 0)
        validating = counts.get("VALIDATING", 0)
        qualified = counts.get("QUALIFIED", 0) + counts.get("PAPER", 0)

        target = max(1, int(total_node_target))
        remaining = max(0, target - total_nodes)
        target_reached = total_nodes >= target

        # Ancestry metrics
        edges_row = self.one("SELECT COUNT(*) e FROM strategies WHERE parent_id IS NOT NULL")
        roots_row = self.one("SELECT COUNT(*) r FROM strategies WHERE parent_id IS NULL")

        # Fingerprints metrics
        has_bt = self.one("SELECT name FROM sqlite_master WHERE type='table' AND name='backtests'")
        if has_bt:
            bt_cnt = self.one("""SELECT COUNT(*) c,
                                        COUNT(CASE WHEN fingerprint IS NOT NULL AND fingerprint != '' THEN 1 END) fp,
                                        COUNT(CASE WHEN stale=1 THEN 1 END) st
                                 FROM backtests""")
        else:
            bt_cnt = {"c": 0, "fp": 0, "st": 0}

        has_val = self.one("SELECT name FROM sqlite_master WHERE type='table' AND name='validations'")
        if has_val:
            val_cnt = self.one("""SELECT COUNT(*) c,
                                         COUNT(CASE WHEN fingerprint IS NOT NULL AND fingerprint != '' THEN 1 END) fp
                                  FROM validations""")
        else:
            val_cnt = {"c": 0, "fp": 0}

        # Research files on disk
        from ..paths import RESEARCH_DIR
        def safe_file_count(p):
            try:
                return len(list(p.glob("*"))) if p.exists() else 0
            except Exception:
                return 0

        research_state = {
            "strategies_exported": safe_file_count(RESEARCH_DIR / "strategies"),
            "generations_exported": safe_file_count(RESEARCH_DIR / "generations"),
            "backtests_exported": safe_file_count(RESEARCH_DIR / "backtests"),
            "validations_exported": safe_file_count(RESEARCH_DIR / "validations"),
        }

        # Datasets metrics
        has_ds = self.one("SELECT name FROM sqlite_master WHERE type='table' AND name='datasets'")
        ds_row = self.one("SELECT COUNT(*) c FROM datasets") if has_ds else {"c": 0}

        eval_row = self.one("SELECT COUNT(DISTINCT strategy_id) e FROM backtests")
        bt_stage_row = self.one("SELECT COUNT(*) b FROM backtests WHERE stage IN ('screen', 'detail')")
        node_accounting = {
            "total_created": total_nodes,
            "total_evaluated": int(eval_row["e"]) if eval_row else 0,
            "total_backtested": int(bt_stage_row["b"]) if bt_stage_row else 0,
            "total_validated": int(val_cnt.get("c", 0)),
            "total_qualified": qualified,
            "total_rejected": counts.get("FAILED", 0) + counts.get("KILLED", 0),
            "total_dead": dead,
            "total_active": counts.get("BORN", 0) + backtesting + counts.get("SURVIVED", 0) + validating,
            "total_remaining": remaining,
        }

        return {
            "total_nodes": total_nodes,
            "target": target,
            "remaining": remaining,
            "alive": alive,
            "dead": dead,
            "backtesting": backtesting,
            "validating": validating,
            "qualified": qualified,
            "node_accounting": node_accounting,
            "current_generation": current_gen,
            "next_strategy_id": next_id,
            "target_reached": target_reached,
            "progress": f"{total_nodes} / {target}",
            "progress_pct": round((total_nodes / target) * 100, 1) if target > 0 else 100.0,
            "status_counts": counts,
            "ancestry": {
                "total_edges": int(edges_row["e"]) if edges_row else 0,
                "root_nodes": int(roots_row["r"]) if roots_row else 0,
                "max_depth": current_gen,
            },
            "fingerprints": {
                "backtests_total": int(bt_cnt["c"]) if bt_cnt else 0,
                "backtests_fingerprinted": int(bt_cnt["fp"]) if bt_cnt else 0,
                "backtests_stale": int(bt_cnt["st"]) if bt_cnt else 0,
                "validations_total": int(val_cnt["c"]) if val_cnt else 0,
                "validations_fingerprinted": int(val_cnt["fp"]) if val_cnt else 0,
            },
            "research": research_state,
            "evolution_seed": self.get_meta("evolution_seed") or "20250925",
            "datasets_count": int(ds_row["c"]) if ds_row else 0,
        }

    # ---------- V4: MT5 Backtests, Live Testing & Demo Trading ----------
    def record_mt5_backtest(self, rec: Dict[str, Any]) -> int:
        from ..jsonutil import jd
        sql = """
            INSERT INTO mt5_backtests (
                strategy_id, run_id, config, symbol, timeframe, start_date, end_date,
                initial_capital, final_capital, net_profit, gross_profit, gross_loss,
                profit_factor, win_rate, trade_count, max_drawdown_pct, relative_drawdown_pct,
                recovery_factor, sharpe, avg_trade, largest_win, largest_loss,
                consecutive_wins, consecutive_losses, mt5_build, status, notes, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """
        cfg_str = rec.get("config")
        if isinstance(cfg_str, (dict, list)):
            cfg_str = jd(cfg_str)
        return self.x(sql, (
            rec.get("strategy_id"),
            rec.get("run_id"),
            cfg_str,
            rec.get("symbol", "XAUUSD"),
            rec.get("timeframe", "M15"),
            rec.get("start_date", ""),
            rec.get("end_date", ""),
            rec.get("initial_capital", 10000.0),
            rec.get("final_capital", 10000.0),
            rec.get("net_profit", 0.0),
            rec.get("gross_profit", 0.0),
            rec.get("gross_loss", 0.0),
            rec.get("profit_factor", 0.0),
            rec.get("win_rate", 0.0),
            rec.get("trade_count", 0),
            rec.get("max_drawdown_pct", 0.0),
            rec.get("relative_drawdown_pct", 0.0),
            rec.get("recovery_factor", 0.0),
            rec.get("sharpe", 0.0),
            rec.get("avg_trade", 0.0),
            rec.get("largest_win", 0.0),
            rec.get("largest_loss", 0.0),
            rec.get("consecutive_wins", 0),
            rec.get("consecutive_losses", 0),
            rec.get("mt5_build", "SIMULATOR_V4"),
            rec.get("status", "COMPLETED"),
            rec.get("notes", ""),
            rec.get("created_at", time.time()),
        ))

    def get_mt5_backtests(self, strategy_id: Optional[int] = None) -> List[Dict[str, Any]]:
        if strategy_id is not None:
            return self.q("SELECT * FROM mt5_backtests WHERE strategy_id=? ORDER BY id DESC", (strategy_id,))
        return self.q("SELECT * FROM mt5_backtests ORDER BY id DESC")

    def get_live_test_config(self, strategy_id: int) -> Optional[Dict[str, Any]]:
        row = self.one("SELECT * FROM live_test_configs WHERE strategy_id=?", (strategy_id,))
        if not row:
            return None
        import json
        return {
            "strategy_id": row["strategy_id"],
            "timeframes": json.loads(row["timeframes"]) if row.get("timeframes") else ["M1", "M5", "M15"],
            "days": json.loads(row["days"]) if row.get("days") else ["Mon", "Tue", "Wed", "Thu", "Fri"],
            "sessions": json.loads(row["sessions"]) if row.get("sessions") else ["asia", "london", "newyork"],
            "start_time": row.get("start_time") or "00:00",
            "end_time": row.get("end_time") or "23:59",
            "timezone": row.get("timezone") or "UTC",
            "lot_size": row.get("lot_size") or 0.1,
            # None means "no per-node override": resolving the effective risk is the
            # risk engine's job (GLOBAL default vs CUSTOM override). Substituting a
            # number here would silently turn every node into a custom-risk node.
            "risk_pct": row.get("risk_pct"),
            "is_active": bool(row.get("is_active")),
            "status": row.get("status") or "IDLE",
            # V5 §11 execution attributes (None = not configured)
            "spread_limit_points": row.get("spread_limit_points"),
            "cooldown_minutes": row.get("cooldown_minutes"),
            "max_trades_per_day": row.get("max_trades_per_day"),
            "max_positions": row.get("max_positions"),
            "slippage_limit_points": row.get("slippage_limit_points"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }

    def set_live_test_config(self, strategy_id: int, cfg: Dict[str, Any]) -> None:
        from ..jsonutil import jd
        now = time.time()
        sql = """
            INSERT INTO live_test_configs (
                strategy_id, timeframes, days, sessions, start_time, end_time,
                timezone, lot_size, risk_pct, is_active, status, created_at, updated_at,
                spread_limit_points, cooldown_minutes, max_trades_per_day, max_positions,
                slippage_limit_points
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(strategy_id) DO UPDATE SET
                timeframes=excluded.timeframes,
                days=excluded.days,
                sessions=excluded.sessions,
                start_time=excluded.start_time,
                end_time=excluded.end_time,
                timezone=excluded.timezone,
                lot_size=excluded.lot_size,
                risk_pct=excluded.risk_pct,
                is_active=excluded.is_active,
                status=excluded.status,
                updated_at=excluded.updated_at
        """
        self.ensure_live_test_config_columns()
        self.x(sql, (
            strategy_id,
            jd(cfg.get("timeframes", ["M1", "M5", "M15"])),
            jd(cfg.get("days", ["Mon", "Tue", "Wed", "Thu", "Fri"])),
            jd(cfg.get("sessions", ["asia", "london", "newyork"])),
            cfg.get("start_time", "00:00"),
            cfg.get("end_time", "23:59"),
            cfg.get("timezone", "UTC"),
            cfg.get("lot_size", 0.1),
            cfg.get("risk_pct", 1.0),
            1 if cfg.get("is_active") else 0,
            cfg.get("status", "IDLE"),
            now,
            now,
            self._num_or_none(cfg.get("spread_limit_points")),
            self._num_or_none(cfg.get("cooldown_minutes")),
            self._num_or_none(cfg.get("max_trades_per_day")),
            self._num_or_none(cfg.get("max_positions")),
            self._num_or_none(cfg.get("slippage_limit_points")),
        ))

    def record_live_test_trade(self, t: Dict[str, Any]) -> int:
        sql = """
            INSERT INTO live_test_trades (
                strategy_id, ticket, symbol, timeframe, side, entry_price, exit_price,
                sl, tp, lots, pnl, pnl_pct, open_ts, close_ts, close_reason,
                session, day_of_week, status
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """
        return self.x(sql, (
            t.get("strategy_id"),
            t.get("ticket"),
            t.get("symbol", "XAUUSD"),
            t.get("timeframe", "M15"),
            t.get("side", "BUY"),
            t.get("entry_price", 0.0),
            t.get("exit_price"),
            t.get("sl"),
            t.get("tp"),
            t.get("lots", 0.1),
            t.get("pnl", 0.0),
            t.get("pnl_pct", 0.0),
            t.get("open_ts", time.time()),
            t.get("close_ts"),
            t.get("close_reason"),
            t.get("session", "london"),
            t.get("day_of_week", "Mon"),
            t.get("status", "OPEN"),
        ))

    def get_live_test_trades(self, strategy_id: Optional[int] = None) -> List[Dict[str, Any]]:
        if strategy_id is not None:
            return self.q("SELECT * FROM live_test_trades WHERE strategy_id=? ORDER BY id DESC", (strategy_id,))
        return self.q("SELECT * FROM live_test_trades ORDER BY id DESC")

    def get_mt5_demo_config(self, strategy_id: int) -> Optional[Dict[str, Any]]:
        row = self.one("SELECT * FROM mt5_demo_configs WHERE strategy_id=?", (strategy_id,))
        if not row:
            return None
        return {
            "strategy_id": row["strategy_id"],
            "enabled": bool(row.get("enabled")),
            "confirmed_demo_only": bool(row.get("confirmed_demo_only")),
            "magic_number": row.get("magic_number") or (100000 + strategy_id),
            "max_positions": row.get("max_positions") or 1,
            "lot_size": row.get("lot_size") or 0.05,
            "risk_pct": row.get("risk_pct") or 0.5,
            "status": row.get("status") or "STOPPED",
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }

    def set_mt5_demo_config(self, strategy_id: int, cfg: Dict[str, Any]) -> None:
        now = time.time()
        sql = """
            INSERT INTO mt5_demo_configs (
                strategy_id, enabled, confirmed_demo_only, magic_number,
                max_positions, lot_size, risk_pct, status, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(strategy_id) DO UPDATE SET
                enabled=excluded.enabled,
                confirmed_demo_only=excluded.confirmed_demo_only,
                magic_number=excluded.magic_number,
                max_positions=excluded.max_positions,
                lot_size=excluded.lot_size,
                risk_pct=excluded.risk_pct,
                status=excluded.status,
                updated_at=excluded.updated_at
        """
        self.x(sql, (
            strategy_id,
            1 if cfg.get("enabled") else 0,
            1 if cfg.get("confirmed_demo_only") else 0,
            cfg.get("magic_number", 100000 + strategy_id),
            cfg.get("max_positions", 1),
            cfg.get("lot_size", 0.05),
            cfg.get("risk_pct", 0.5),
            cfg.get("status", "STOPPED"),
            now,
            now,
        ))

    def record_mt5_demo_trade(self, t: Dict[str, Any]) -> int:
        sql = """
            INSERT INTO mt5_demo_trades (
                strategy_id, order_id, symbol, timeframe, side,
                entry_price, exit_price, sl, tp, lots, pnl, open_ts, close_ts, status
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """
        return self.x(sql, (
            t.get("strategy_id"),
            t.get("order_id"),
            t.get("symbol", "XAUUSD"),
            t.get("timeframe", "M15"),
            t.get("side", "BUY"),
            t.get("entry_price", 0.0),
            t.get("exit_price"),
            t.get("sl"),
            t.get("tp"),
            t.get("lots", 0.05),
            t.get("pnl", 0.0),
            t.get("open_ts", time.time()),
            t.get("close_ts"),
            t.get("status", "OPEN"),
        ))

    def get_mt5_demo_trades(self, strategy_id: Optional[int] = None) -> List[Dict[str, Any]]:
        if strategy_id is not None:
            return self.q("SELECT * FROM mt5_demo_trades WHERE strategy_id=? ORDER BY id DESC", (strategy_id,))
        return self.q("SELECT * FROM mt5_demo_trades ORDER BY id DESC")

    # ---------- V4.2 manual MT5 demo execution log ----------
    # Audit trail of manual demo orders ONLY. Deliberately separate from
    # `executions` (paper calibration reads that table) and from
    # `mt5_demo_trades` (strategy-driven demo trades) so a manual test order can
    # never change existing research/statistics behaviour.
    MT5_MANUAL_ORDER_COLUMNS = (
        "client_order_id", "ts", "symbol", "side", "volume", "requested_price",
        "sl", "tp", "strategy_id", "status", "retcode", "order_ticket", "deal_ticket",
        "position_ticket", "exec_price", "broker_sl", "broker_tp", "sl_tp_verified",
        "message", "error_code", "account_login", "account_server", "duration_ms",
        "raw_json",
    )

    def _ensure_manual_order_table(self) -> None:
        self.x("""CREATE TABLE IF NOT EXISTS mt5_manual_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_order_id TEXT UNIQUE,
            ts REAL NOT NULL,
            symbol TEXT, side TEXT, volume REAL, requested_price REAL,
            sl REAL, tp REAL, strategy_id INTEGER,
            status TEXT NOT NULL,
            retcode INTEGER, order_ticket INTEGER, deal_ticket INTEGER,
            position_ticket INTEGER, exec_price REAL,
            broker_sl REAL, broker_tp REAL, sl_tp_verified INTEGER,
            message TEXT, error_code TEXT,
            account_login INTEGER, account_server TEXT, duration_ms REAL,
            raw_json TEXT
        )""")
        self.x("CREATE INDEX IF NOT EXISTS idx_mt5_manual_orders_ts ON mt5_manual_orders(ts)")

    def record_manual_mt5_order(self, row: Dict[str, Any]) -> int:
        self._ensure_manual_order_table()
        cols = list(self.MT5_MANUAL_ORDER_COLUMNS)
        sql = (f"INSERT OR REPLACE INTO mt5_manual_orders ({','.join(cols)}) "
               f"VALUES ({','.join('?' * len(cols))})")
        return self.x(sql, tuple(
            (int(bool(row[c])) if c == "sl_tp_verified" and row.get(c) is not None else row.get(c))
            for c in cols))

    def get_manual_mt5_order(self, client_order_id: str) -> Optional[Dict[str, Any]]:
        self._ensure_manual_order_table()
        return self.one("SELECT * FROM mt5_manual_orders WHERE client_order_id=?",
                        (str(client_order_id),))

    def get_manual_mt5_orders(self, limit: int = 20) -> List[Dict[str, Any]]:
        self._ensure_manual_order_table()
        return self.q("SELECT * FROM mt5_manual_orders ORDER BY id DESC LIMIT ?", (int(limit),))

    # ---------- V4.3 live-testing trade records (reuses live_test_trades) ----------
    V43_TRADE_COLUMNS = (
        ("run_id", "TEXT"), ("risk_pct", "REAL"), ("risk_amount", "REAL"),
        ("magic", "INTEGER"), ("client_order_id", "TEXT"), ("deal_ticket", "INTEGER"),
        ("exec_price", "REAL"), ("retcode", "INTEGER"), ("result_json", "TEXT"),
        ("updated_at", "REAL"),
    )

    @staticmethod
    def _num_or_none(v):
        try:
            return None if v is None or v == "" else float(v)
        except (TypeError, ValueError):
            return None

    #: V5 §11 — execution attributes the live engine enforces
    V5_CONFIG_COLUMNS = (
        ("spread_limit_points", "REAL"), ("cooldown_minutes", "REAL"),
        ("max_trades_per_day", "REAL"), ("max_positions", "REAL"),
        ("slippage_limit_points", "REAL"),
    )

    def ensure_live_test_config_columns(self) -> None:
        """Additive, idempotent V5 migration for the live-test config."""
        cols = {r["name"] for r in self.q("PRAGMA table_info(live_test_configs)")}
        for name, typ in self.V5_CONFIG_COLUMNS:
            if name not in cols:
                self.x(f"ALTER TABLE live_test_configs ADD COLUMN {name} {typ}")

    def ensure_live_trade_columns(self) -> None:
        """Additive, idempotent migration for the V4.3 traceability columns."""
        self._ensure_live_trade_columns()

    def _ensure_live_trade_columns(self) -> None:
        cols = {r["name"] for r in self.q("PRAGMA table_info(live_test_trades)")}
        for name, typ in self.V43_TRADE_COLUMNS:
            if name not in cols:
                self.x(f"ALTER TABLE live_test_trades ADD COLUMN {name} {typ}")

    def record_live_test_execution(self, t: Dict[str, Any]) -> int:
        """Insert a V4.3 live-testing trade record (node -> MT5 ticket traceability)."""
        self._ensure_live_trade_columns()
        from ..jsonutil import jd
        cols = ["strategy_id", "ticket", "deal_ticket", "symbol", "timeframe", "side",
                "entry_price", "exec_price", "exit_price", "sl", "tp", "lots", "pnl",
                "pnl_pct", "open_ts", "close_ts", "close_reason", "session", "day_of_week",
                "status", "run_id", "risk_pct", "risk_amount", "magic", "client_order_id",
                "retcode", "result_json", "updated_at"]
        # the pre-existing table declares several NOT NULL columns, so records that
        # describe a *blocked* attempt still need concrete values there (0.0 / "")
        def _nn(v, default):
            return default if v is None else v

        vals = (t.get("strategy_id"), t.get("ticket"), t.get("deal_ticket"),
                _nn(t.get("symbol"), "XAUUSD"), _nn(t.get("timeframe"), "M15"),
                _nn(t.get("side"), "BUY"),
                _nn(t.get("entry_price"), _nn(t.get("exec_price"), 0.0)),
                t.get("exec_price"), t.get("exit_price"), t.get("sl"),
                t.get("tp"), _nn(t.get("lots"), 0.01), _nn(t.get("pnl"), 0.0),
                _nn(t.get("pnl_pct"), 0.0),
                _nn(t.get("open_ts"), time.time()), t.get("close_ts"), t.get("close_reason"),
                t.get("session"), t.get("day_of_week"), _nn(t.get("status"), "OPEN"),
                t.get("run_id"), t.get("risk_pct"), t.get("risk_amount"), t.get("magic"),
                t.get("client_order_id"), t.get("retcode"),
                jd(t.get("result")) if t.get("result") is not None else None,
                time.time())
        return self.x(f"INSERT INTO live_test_trades ({','.join(cols)}) "
                      f"VALUES ({','.join('?' * len(cols))})", tuple(vals))

    def update_live_test_trade(self, row_id: int, **fields: Any) -> None:
        self._ensure_live_trade_columns()
        allowed = {"ticket", "deal_ticket", "entry_price", "exec_price", "exit_price", "sl",
                   "tp", "lots", "pnl", "pnl_pct", "close_ts", "close_reason", "status",
                   "result_json", "retcode", "updated_at"}
        sets, vals = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            sets.append(f"{k}=?"); vals.append(v)
        if not sets:
            return
        sets.append("updated_at=?"); vals.append(time.time())
        vals.append(int(row_id))
        self.x(f"UPDATE live_test_trades SET {','.join(sets)} WHERE id=?", tuple(vals))

    def get_live_test_executions(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        self._ensure_live_trade_columns()
        if status:
            return self.q("SELECT * FROM live_test_trades WHERE status=? ORDER BY id DESC", (status,))
        return self.q("SELECT * FROM live_test_trades ORDER BY id DESC")

    # ---------- V4.3 live-testing stage log ----------
    def _ensure_live_event_table(self) -> None:
        self.x("""CREATE TABLE IF NOT EXISTS live_test_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            node_id INTEGER,
            symbol TEXT,
            side TEXT,
            stage TEXT NOT NULL,
            status TEXT,
            message TEXT,
            detail_json TEXT
        )""")
        self.x("CREATE INDEX IF NOT EXISTS idx_live_events_ts ON live_test_events(ts)")
        self.x("CREATE INDEX IF NOT EXISTS idx_live_events_node ON live_test_events(node_id)")

    def record_live_test_event(self, e: Dict[str, Any]) -> int:
        self._ensure_live_event_table()
        return self.x(
            "INSERT INTO live_test_events (ts,node_id,symbol,side,stage,status,message,detail_json)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (e.get("ts") or time.time(), e.get("node_id"), e.get("symbol"), e.get("side"),
             e.get("stage"), e.get("status"), e.get("message"),
             e.get("detail_json")))

    def get_live_test_events(self, limit: int = 100, node_id: Optional[int] = None,
                             since_ts: Optional[float] = None) -> List[Dict[str, Any]]:
        self._ensure_live_event_table()
        where, args = [], []
        if node_id is not None:
            where.append("node_id=?"); args.append(int(node_id))
        if since_ts is not None:
            where.append("ts>=?"); args.append(float(since_ts))
        wsql = ("WHERE " + " AND ".join(where)) if where else ""
        args.append(int(limit))
        return self.q(f"SELECT * FROM live_test_events {wsql} ORDER BY id DESC LIMIT ?", tuple(args))

    def clear_live_test_events(self) -> int:
        self._ensure_live_event_table()
        return self.x("DELETE FROM live_test_events")

    def get_pipeline_stage(self, strategy_id: int) -> str:
        row = self.one("SELECT stage FROM strategy_pipeline_states WHERE strategy_id=?", (strategy_id,))
        if row:
            return row["stage"]
        s = self.get_strategy(strategy_id)
        if s and s.get("status") == "QUALIFIED":
            return "QUALIFIED"
        return s.get("status", "GENERATED") if s else "GENERATED"

    def set_pipeline_stage(self, strategy_id: int, stage: str, notes: Optional[str] = None) -> None:
        now = time.time()
        sql = """
            INSERT INTO strategy_pipeline_states (strategy_id, stage, notes, updated_at)
            VALUES (?,?,?,?)
            ON CONFLICT(strategy_id) DO UPDATE SET
                stage=excluded.stage,
                notes=excluded.notes,
                updated_at=excluded.updated_at
        """
        self.x(sql, (strategy_id, stage, notes or "", now))

    def log_event(self, etype: str, payload: Dict[str, Any]) -> int:
        from ..jsonutil import jd
        return self.x("INSERT INTO events (ts,type,payload) VALUES (?,?,?)",
                      (time.time(), etype, jd(payload)))


_db: Optional[Database] = None


def _db_identity(path: str) -> tuple:
    """Identity of the database file a connection was opened against (V4.7).

    If the file is replaced (a backup restored over it, a clear/reset flow, an
    operator copying a new database in), a connection opened against the old
    inode keeps reading the old file - or worse, reports the confusing
    "database disk image is malformed". Device+inode is the correct replacement
    identity: it changes exactly when the path now points at a different file,
    while ordinary writes (which change size/mtime) do not trigger it. A file
    that is rewritten *in place* keeps its inode and is handled by SQLite's own
    header change counter, so it needs no action here.
    """
    try:
        st = os.stat(path)
        return (st.st_dev, st.st_ino)
    except OSError:
        return None


def get_db(path: str | None = None) -> Database:
    global _db
    if _db is not None:
        try:
            _db._conn.execute("SELECT 1")
        except (sqlite3.ProgrammingError, sqlite3.OperationalError):
            _db = None
        else:
            # V4.7: if the database file was replaced underneath the connection
            # (restore, reset, operator copy), reopen against the new file
            # instead of serving reads from a stale inode.
            try:
                _db.reopen()
            except Exception as e:                      # pragma: no cover - defensive
                logging.getLogger("db").warning("reopen after replacement failed: %s", e)
                try:
                    _db.close()
                except Exception:
                    pass
                _db = None
    if _db is None:
        from ..config import get_config
        _db = Database(path or get_config().database_path)
    return _db
