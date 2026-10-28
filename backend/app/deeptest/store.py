"""V6.6 — Deep Testing persistence (scenarios, trades, preferences, confirmations,
candle-source provenance, workspace settings).

All tables are ADDITIVE: created with ``CREATE TABLE IF NOT EXISTS`` through the
existing ``Database`` schema bootstrap. Nothing here deletes, resets or rewrites
any existing table (strategies, backtests, validations, live_test_*, mt5_*).
"""
from __future__ import annotations

import json
import threading
import time
import uuid
import weakref

_SCHEMA_READY = weakref.WeakKeyDictionary()
from typing import Any, Dict, List, Optional

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS deeptest_scenarios (
    run_id TEXT PRIMARY KEY,
    node_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    idempotency_key TEXT,
    duration_key TEXT,
    requested_start REAL,
    requested_end REAL,
    actual_start REAL,
    actual_end REAL,
    symbol TEXT,
    timeframe TEXT,
    dataset_id TEXT,
    dataset_fingerprint TEXT,
    bars_used INTEGER,
    coverage_status TEXT,
    gaps TEXT,
    settings TEXT,
    metrics TEXT,
    analytics TEXT,
    provenance TEXT,
    genome_hash TEXT,
    config_hash TEXT,
    engine_versions TEXT,
    confirmed_fingerprint TEXT,
    error TEXT,
    notes TEXT,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS idx_dts_node ON deeptest_scenarios(node_id, created_at);
CREATE INDEX IF NOT EXISTS idx_dts_status ON deeptest_scenarios(status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dts_idem ON deeptest_scenarios(idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS deeptest_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    trade_no INTEGER,
    side TEXT,
    symbol TEXT,
    entry_ts REAL,
    entry_price REAL,
    exit_ts REAL,
    exit_price REAL,
    volume REAL,
    sl REAL,
    tp REAL,
    exit_reason TEXT,
    hold_seconds REAL,
    gross_pnl REAL,
    commission REAL,
    spread_cost REAL,
    slippage_cost REAL,
    swap_cost REAL,
    net_pnl REAL,
    session TEXT,
    dow INTEGER,
    meta TEXT
);
CREATE INDEX IF NOT EXISTS idx_dt_trades_run ON deeptest_trades(run_id, trade_no);

CREATE TABLE IF NOT EXISTS deeptest_prefs (
    node_id INTEGER PRIMARY KEY,
    preferred_run_id TEXT,
    explicitly_selected INTEGER NOT NULL DEFAULT 0,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS deeptest_confirmations (
    fingerprint TEXT PRIMARY KEY,
    decision TEXT NOT NULL,
    decided_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS deeptest_node_configs (
    node_id INTEGER PRIMARY KEY,
    config_json TEXT NOT NULL,
    updated_at REAL
);
CREATE TABLE IF NOT EXISTS deeptest_settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS deeptest_candle_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    source_type TEXT NOT NULL,
    filename TEXT,
    file_sha256 TEXT,
    imported_at REAL,
    timezone TEXT,
    rows_read INTEGER,
    rows_valid INTEGER,
    rows_rejected INTEGER,
    rows_duplicate INTEGER,
    earliest_ts REAL,
    latest_ts REAL,
    fingerprint TEXT,
    user_mapping TEXT,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_dcs_pair ON deeptest_candle_sources(symbol, timeframe);
"""

_WRITE_LOCK = threading.Lock()

DEFAULT_SETTINGS: Dict[str, Any] = {
    "total_capital": 200000.0,
    "risk_pct": 1.0,
    "max_active_trades_per_node": 0,      # 0 = unlimited (engine/strategy rules apply)
}

SETTINGS_LIMITS = {
    "total_capital": (100.0, 100_000_000.0),
    "risk_pct": (0.01, 100.0),
    "max_active_trades_per_node": (0, 100),
}


def _ensure_column(db: Any, table: str, col: str, ddl: str) -> None:
    """Additive, migration-safe column add (V6.7): only ever ADDS a column."""
    try:
        cols = [r["name"] for r in (db.q(f"PRAGMA table_info({table})") or [])]
        if col not in cols:
            db.x(f"ALTER TABLE {table} ADD COLUMN {ddl}")
    except Exception:
        pass


def ensure_schema(db: Any) -> None:
    # No unchanged DDL/commits on every trade page or progress poll. A new
    # connection or ANY external schema change invalidates this proof immediately.
    from contextlib import nullcontext
    # Fast proven reads need no scenario-writer lock: otherwise an HTTP
    # cancellation could not signal while complete trade persistence holds it.
    with (getattr(db, '_lock', None) or nullcontext()):
        conn = getattr(db, '_conn', None)
        token = (conn, conn.execute('PRAGMA schema_version').fetchone()[0]) if conn else None
        if token is not None and _SCHEMA_READY.get(db) == token:return
    with _WRITE_LOCK, (getattr(db, '_lock', None) or nullcontext()):
        conn = getattr(db, '_conn', None)
        token = (conn, conn.execute('PRAGMA schema_version').fetchone()[0]) if conn else None
        if token is not None and _SCHEMA_READY.get(db) == token:
            return
        _ensure_column(db, "deeptest_scenarios", "signature", "signature TEXT")
        for stmt in [s.strip() for s in SCHEMA_SQL.split(";") if s.strip()]:
            db.x(stmt)
        _ensure_column(db, 'deeptest_scenarios', 'signature', 'signature TEXT')
        if conn is not None:
            _SCHEMA_READY[db] = (conn, conn.execute('PRAGMA schema_version').fetchone()[0])


def _jd(v: Any) -> Optional[str]:
    return None if v is None else json.dumps(v, default=str)


def _jload(raw: Any, default: Any = None) -> Any:
    if raw in (None, ""):
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return default


# --------------------------------------------------------------------------- #
# workspace settings
# --------------------------------------------------------------------------- #
def get_settings(db: Any) -> Dict[str, Any]:
    ensure_schema(db)
    out = dict(DEFAULT_SETTINGS)
    for row in db.q("SELECT key, value FROM deeptest_settings") or []:
        try:
            out[str(row["key"])] = json.loads(row["value"])
        except Exception:
            continue
    return out


def validate_settings(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a settings update. Returns {ok, values|errors}."""
    errors = []
    values = {}
    for key, (lo, hi) in SETTINGS_LIMITS.items():
        if key not in (payload or {}):
            continue
        raw = payload[key]
        try:
            v = float(raw)
        except (TypeError, ValueError):
            errors.append({"field": key, "error": f"{key} must be a number"})
            continue
        if key == "max_active_trades_per_node":
            if abs(v - round(v)) > 1e-9:
                errors.append({"field": key, "error": "max_active_trades_per_node must be a whole number"})
                continue
            v = int(round(v))
        if v < lo or v > hi:
            errors.append({"field": key, "error": f"{key} must be between {lo:g} and {hi:g}"})
            continue
        values[key] = v
    return {"ok": not errors, "values": values, "errors": errors}


def save_settings(db: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
    ensure_schema(db)
    res = validate_settings(payload)
    if not res["ok"]:
        return res
    with _WRITE_LOCK:
        for k, v in res["values"].items():
            db.x("INSERT INTO deeptest_settings(key, value, updated_at) VALUES(?,?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                 (k, json.dumps(v), time.time()))
    return {"ok": True, "values": get_settings(db), "errors": []}


# --------------------------------------------------------------------------- #
# scenarios
# --------------------------------------------------------------------------- #
def new_run_id() -> str:
    return "dt-" + uuid.uuid4().hex[:16]


def insert_scenario(db: Any, row: Dict[str, Any]) -> str:
    ensure_schema(db)
    run_id = row.get("run_id") or new_run_id()
    with _WRITE_LOCK:
        db.x(
            """INSERT INTO deeptest_scenarios
               (run_id, node_id, status, idempotency_key, duration_key,
                requested_start, requested_end, actual_start, actual_end,
                symbol, timeframe, dataset_id, dataset_fingerprint, bars_used,
                coverage_status, gaps, settings, metrics, analytics, provenance,
                genome_hash, config_hash, engine_versions, confirmed_fingerprint,
                error, notes, created_at, started_at, finished_at, signature)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id, int(row["node_id"]), row.get("status") or "QUEUED",
             row.get("idempotency_key"), row.get("duration_key"),
             row.get("requested_start"), row.get("requested_end"),
             row.get("actual_start"), row.get("actual_end"),
             row.get("symbol"), row.get("timeframe"),
             row.get("dataset_id"), row.get("dataset_fingerprint"),
             row.get("bars_used"), row.get("coverage_status"),
             _jd(row.get("gaps")), _jd(row.get("settings")),
             _jd(row.get("metrics")), _jd(row.get("analytics")),
             _jd(row.get("provenance")), row.get("genome_hash"),
             row.get("config_hash"), _jd(row.get("engine_versions")),
             row.get("confirmed_fingerprint"), row.get("error"),
             row.get("notes"), row.get("created_at") or time.time(),
             row.get("started_at"), row.get("finished_at"), row.get("signature")))
    return run_id


def update_scenario(db: Any, run_id: str, **cols: Any) -> None:
    if not cols:
        return
    for k in ("gaps", "settings", "metrics", "analytics", "provenance", "engine_versions"):
        if k in cols:
            cols[k] = _jd(cols[k])
    sets = ", ".join(f"{k}=?" for k in cols)
    terminal_guard = " AND status <> 'CANCELLED'" if cols.get('status') not in (None,'CANCELLED') else ''
    with _WRITE_LOCK:
        db.x(f"UPDATE deeptest_scenarios SET {sets} WHERE run_id=?{terminal_guard}", (*cols.values(), run_id))


def get_scenario(db: Any, run_id: str) -> Optional[Dict[str, Any]]:
    ensure_schema(db)
    row = db.one("SELECT * FROM deeptest_scenarios WHERE run_id=?", (run_id,))
    return hydrate(dict(row)) if row else None


def hydrate(row: Dict[str, Any]) -> Dict[str, Any]:
    for k in ("gaps", "settings", "metrics", "analytics", "provenance", "engine_versions"):
        row[k] = _jload(row.get(k))
    return row


def list_scenarios(db: Any, node_id: Optional[int] = None,
                   statuses: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    ensure_schema(db)
    sql = "SELECT * FROM deeptest_scenarios"
    args: List[Any] = []
    where = []
    if node_id is not None:
        where.append("node_id=?")
        args.append(int(node_id))
    if statuses:
        where.append(f"status IN ({','.join('?' * len(statuses))})")
        args.extend(statuses)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC"
    return [hydrate(dict(r)) for r in (db.q(sql, tuple(args)) or [])]


def insert_trades(db: Any, run_id: str, trades: List[Dict[str, Any]], *, cancelled=None) -> None:
    ensure_schema(db)
    with _WRITE_LOCK:
        for i, t in enumerate(trades):
            if cancelled is not None and cancelled():
                from concurrent.futures import CancelledError
                raise CancelledError('operator cancelled result persistence')
            db.x(
                """INSERT INTO deeptest_trades
                   (run_id, trade_no, side, symbol, entry_ts, entry_price,
                    exit_ts, exit_price, volume, sl, tp, exit_reason,
                    hold_seconds, gross_pnl, commission, spread_cost,
                    slippage_cost, swap_cost, net_pnl, session, dow, meta)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (run_id, i + 1, t.get("side"), t.get("symbol"),
                 t.get("entry_ts"), t.get("entry_price"), t.get("exit_ts"),
                 t.get("exit_price"), t.get("volume"), t.get("sl"), t.get("tp"),
                 t.get("exit_reason"), t.get("hold_seconds"),
                 t.get("gross_pnl"), t.get("commission"), t.get("spread_cost"),
                 t.get("slippage_cost"), t.get("swap_cost"), t.get("net_pnl"),
                 t.get("session"), t.get("dow"),
                 _jd({k: v for k, v in t.items()
                       if k not in ("side", "symbol", "entry_ts", "entry_price",
                                    "exit_ts", "exit_price", "volume", "sl", "tp",
                                    "exit_reason", "hold_seconds", "gross_pnl",
                                    "commission", "spread_cost", "slippage_cost",
                                    "swap_cost", "net_pnl", "session", "dow")})))


def list_trades(db: Any, run_id: str, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    ensure_schema(db)
    total = (db.one("SELECT COUNT(*) c FROM deeptest_trades WHERE run_id=?", (run_id,))
             or {}).get("c") or 0
    rows = db.q("""SELECT * FROM deeptest_trades WHERE run_id=?
                   ORDER BY trade_no LIMIT ? OFFSET ?""", (run_id, limit, offset)) or []
    out = []
    for r in rows:
        d = dict(r)
        d["meta"] = _jload(d.get("meta"))
        out.append(d)
    return {"trades": out, "total": int(total), "limit": limit, "offset": offset}


# --------------------------------------------------------------------------- #
# preferences (§12)
# --------------------------------------------------------------------------- #
def get_pref(db: Any, node_id: int) -> Optional[Dict[str, Any]]:
    ensure_schema(db)
    row = db.one("SELECT * FROM deeptest_prefs WHERE node_id=?", (int(node_id),))
    return dict(row) if row else None


def set_pref(db: Any, node_id: int, run_id: str, explicit: bool = True) -> Dict[str, Any]:
    ensure_schema(db)
    with _WRITE_LOCK:
        db.x("""INSERT INTO deeptest_prefs(node_id, preferred_run_id, explicitly_selected, updated_at)
                VALUES(?,?,?,?)
                ON CONFLICT(node_id) DO UPDATE SET
                  preferred_run_id=excluded.preferred_run_id,
                  explicitly_selected=excluded.explicitly_selected,
                  updated_at=excluded.updated_at""",
             (int(node_id), run_id, 1 if explicit else 0, time.time()))
    return get_pref(db, node_id) or {}


def clear_pref(db: Any, node_id: int) -> None:
    ensure_schema(db)
    with _WRITE_LOCK:
        db.x("DELETE FROM deeptest_prefs WHERE node_id=?", (int(node_id),))


# --------------------------------------------------------------------------- #
# coverage confirmations (§9)
# --------------------------------------------------------------------------- #
def get_confirmation(db: Any, fingerprint: str) -> Optional[Dict[str, Any]]:
    ensure_schema(db)
    row = db.one("SELECT * FROM deeptest_confirmations WHERE fingerprint=?", (fingerprint,))
    return dict(row) if row else None


def save_confirmation(db: Any, fingerprint: str, decision: str) -> Dict[str, Any]:
    ensure_schema(db)
    with _WRITE_LOCK:
        db.x("""INSERT INTO deeptest_confirmations(fingerprint, decision, decided_at)
                VALUES(?,?,?)
                ON CONFLICT(fingerprint) DO UPDATE SET
                  decision=excluded.decision, decided_at=excluded.decided_at""",
             (fingerprint, decision, time.time()))
    return get_confirmation(db, fingerprint) or {}


# --------------------------------------------------------------------------- #
# candle-source provenance (CSV imports etc.)
# --------------------------------------------------------------------------- #
def insert_candle_source(db: Any, row: Dict[str, Any]) -> int:
    ensure_schema(db)
    with _WRITE_LOCK:
        db.x(
            """INSERT INTO deeptest_candle_sources
               (symbol, timeframe, source_type, filename, file_sha256, imported_at,
                timezone, rows_read, rows_valid, rows_rejected, rows_duplicate,
                earliest_ts, latest_ts, fingerprint, user_mapping, notes)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (row.get("symbol"), row.get("timeframe"), row.get("source_type"),
             row.get("filename"), row.get("file_sha256"), row.get("imported_at") or time.time(),
             row.get("timezone"), row.get("rows_read"), row.get("rows_valid"),
             row.get("rows_rejected"), row.get("rows_duplicate"),
             row.get("earliest_ts"), row.get("latest_ts"), row.get("fingerprint"),
             _jd(row.get("user_mapping")), row.get("notes")))
    return int(db.one("SELECT MAX(id) m FROM deeptest_candle_sources").get("m") or 0)


def list_candle_sources(db: Any, symbol: Optional[str] = None,
                        timeframe: Optional[str] = None) -> List[Dict[str, Any]]:
    ensure_schema(db)
    sql = "SELECT * FROM deeptest_candle_sources"
    args: List[Any] = []
    where = []
    if symbol:
        where.append("UPPER(symbol)=UPPER(?)")
        args.append(symbol)
    if timeframe:
        where.append("UPPER(timeframe)=UPPER(?)")
        args.append(timeframe)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY imported_at DESC"
    out = []
    for r in db.q(sql, tuple(args)) or []:
        d = dict(r)
        d["user_mapping"] = _jload(d.get("user_mapping"))
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# V6.7 §7.2/§7.3 — per-node DeepTesting scenario/scheduling configuration.
# STAYS SEPARATE from live execution settings: the UI shares the ScheduleDialog
# presentation only; persistence lives here and never touches live_test_configs.
# ---------------------------------------------------------------------------

def get_node_config(db: Any, node_id: int) -> Dict[str, Any]:
    row = db.one("SELECT config_json, updated_at FROM deeptest_node_configs WHERE node_id=?",
                 (int(node_id),))
    if not row:
        return {}
    try:
        cfg = json.loads(row.get("config_json") or "{}")
    except Exception:
        cfg = {}
    return cfg


def save_node_config(db: Any, node_id: int, config: Dict[str, Any]) -> Dict[str, Any]:
    db.x("""INSERT INTO deeptest_node_configs (node_id, config_json, updated_at)
            VALUES (?,?,?)
            ON CONFLICT(node_id) DO UPDATE SET config_json=excluded.config_json,
                                               updated_at=excluded.updated_at""",
         (int(node_id), json.dumps(config, sort_keys=True, default=str), time.time()))
    return get_node_config(db, node_id)


def cancel_if_active(db, run_id):
    """SQLite-atomic terminal race, including another API process/connection."""
    ensure_schema(db)
    with _WRITE_LOCK:
        db.x("UPDATE deeptest_scenarios SET status='CANCELLED',finished_at=?,notes=? "
             "WHERE run_id=? AND status IN ('QUEUED','FETCHING','VALIDATING','RUNNING')",
             (time.time(),'cancelled by operator or backend shutdown',run_id))
        return db.one('SELECT status FROM deeptest_scenarios WHERE run_id=?',(run_id,))['status']=='CANCELLED'


def finish_if_running(db, run_id, status):
    if status not in ('COMPLETED','PARTIAL'):raise ValueError('Invalid successful terminal status')
    ensure_schema(db)
    with _WRITE_LOCK:
        db.x("UPDATE deeptest_scenarios SET status=?,finished_at=? WHERE run_id=? AND status='RUNNING'",
             (status,time.time(),run_id))
        return db.one('SELECT status FROM deeptest_scenarios WHERE run_id=?',(run_id,))['status']==status
