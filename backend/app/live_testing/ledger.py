"""V6.5 §6 — the canonical managed-trade ledger, stored in the existing DATA root.

One queryable current-state row per managed trade lives in ``live_test_trades``
(the established live-execution audit table, extended additively exactly like
V4.3 did).  Every lifecycle fact that can happen more than once per trade
(fill evidence, SL/TP modifications, partial closes, exits, reconciliation
findings) is an APPEND-ONLY row in ``trade_ledger_events``, keyed by an
``event_uid`` that makes ingestion IDEMPOTENT: replaying the same MT5 history
poll can never duplicate a record.

Rules of the ledger (§6.1/§6.2):

* a field MT5 did not provide stays NULL with an ``evidence_source``/reason —
  values are never fabricated;
* order ticket, deal ticket and position ticket are DISTINCT identities and are
  stored as such;
* the row is written at the moment the execution outcome is known, from the
  actual broker response; reconciliation only ever ADDS evidence (exit deal,
  realized P/L, closure classification) and records discrepancies instead of
  silently overwriting conflicting facts;
* research tables (backtests/metrics) are never touched by anything here.

Everything in this module is stdlib + the project's Database facade
(``q``/``x``/``one``); no MT5 imports, no trading logic.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2          # live_test_trades row schema (V1=V4.3, V2=V6.5)

#: additive columns on live_test_trades — (name, ddl type)
TRADE_COLUMNS = (
    ("trade_uid", "TEXT"),
    ("position_ticket", "INTEGER"),
    ("order_ticket", "INTEGER"),
    ("exit_order_ticket", "INTEGER"),
    ("exit_deal_ticket", "INTEGER"),
    ("comment", "TEXT"),
    ("strategy_identity", "TEXT"),
    ("signal_id", "TEXT"),
    ("signal_ts", "REAL"),
    ("submit_ts", "REAL"),
    ("response_ts", "REAL"),
    ("fill_ts", "REAL"),
    ("order_type", "TEXT"),
    ("fill_status", "TEXT"),
    ("sl_requested", "REAL"),
    ("tp_requested", "REAL"),
    ("sl_confirmed", "REAL"),
    ("tp_confirmed", "REAL"),
    ("sl_default", "REAL"),
    ("tp_default", "REAL"),
    ("sl_offset_pips", "REAL"),
    ("tp_offset_pips", "REAL"),
    ("spread_points", "REAL"),
    ("slippage_points", "REAL"),
    ("broker_comment", "TEXT"),
    ("entry_reason", "TEXT"),
    ("account_login", "INTEGER"),
    ("account_server", "TEXT"),
    ("volume_filled", "REAL"),
    ("volume_current", "REAL"),
    ("closed_volume", "REAL"),
    ("close_kind", "TEXT"),
    ("exit_reason", "TEXT"),
    ("exit_reason_source", "TEXT"),
    ("gross_pnl", "REAL"),
    ("commission", "REAL"),
    ("swap", "REAL"),
    ("fees", "REAL"),
    ("net_pnl", "REAL"),
    ("duration_s", "REAL"),
    ("point", "REAL"),
    ("digits", "INTEGER"),
    ("tick_size", "REAL"),
    ("tick_value", "REAL"),
    ("evidence_source", "TEXT"),
    ("reconcile_flags", "TEXT"),
    ("data_complete", "INTEGER"),
    ("ledger_schema", "INTEGER"),
    ("last_sync_ts", "REAL"),
)

#: per-node experimental offsets (§9) — NULL/0 = strategy defaults preserved
CONFIG_COLUMNS = (
    ("sl_offset_pips", "REAL"),
    ("tp_offset_pips", "REAL"),
)

_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS trade_ledger_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_uid TEXT NOT NULL,
    ts REAL NOT NULL,
    trade_uid TEXT,
    strategy_id INTEGER,
    position_ticket INTEGER,
    order_ticket INTEGER,
    deal_ticket INTEGER,
    event_type TEXT NOT NULL,
    stage TEXT,
    status TEXT,
    symbol TEXT,
    side TEXT,
    payload TEXT,
    source TEXT,
    created_at REAL NOT NULL
)
"""

_INDEXES_DDL = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_ledger_events_uid ON trade_ledger_events(event_uid);
CREATE INDEX IF NOT EXISTS idx_ledger_events_trade ON trade_ledger_events(trade_uid);
CREATE INDEX IF NOT EXISTS idx_ledger_events_pos ON trade_ledger_events(position_ticket);
CREATE INDEX IF NOT EXISTS idx_ledger_events_ts ON trade_ledger_events(ts);
CREATE INDEX IF NOT EXISTS idx_live_trades_position ON live_test_trades(position_ticket);
"""


def ensure_schema(db) -> None:
    """Additive + idempotent. Never drops, truncates or rewrites anything."""
    try:
        db.ensure_live_trade_columns()                    # V4.3 columns first
    except Exception as e:                                # pragma: no cover - defensive
        log.warning("ledger: V4.3 column ensure failed: %s", e)
    cols = {r["name"] for r in db.q("PRAGMA table_info(live_test_trades)")}
    for name, typ in TRADE_COLUMNS:
        if name not in cols:
            db.x(f"ALTER TABLE live_test_trades ADD COLUMN {name} {typ}")
    try:
        ccols = {r["name"] for r in db.q("PRAGMA table_info(live_test_configs)")}
        for name, typ in CONFIG_COLUMNS:
            if name not in ccols:
                db.x(f"ALTER TABLE live_test_configs ADD COLUMN {name} {typ}")
    except Exception as e:                                # pragma: no cover
        log.warning("ledger: config column ensure failed: %s", e)
    for stmt in _EVENTS_DDL.split(";"):
        if stmt.strip():
            db.x(stmt)
    for stmt in _INDEXES_DDL.split(";"):
        if stmt.strip():
            db.x(stmt)


def new_trade_uid() -> str:
    return f"ltt-{uuid.uuid4().hex[:16]}"


# --------------------------------------------------------------------------- #
# events — append-only, idempotent
# --------------------------------------------------------------------------- #
def record_event(db, *, event_uid: str, event_type: str, ts: Optional[float] = None,
                 trade_uid: Optional[str] = None, strategy_id: Any = None,
                 position_ticket: Any = None, order_ticket: Any = None,
                 deal_ticket: Any = None, stage: Optional[str] = None,
                 status: Optional[str] = None, symbol: Optional[str] = None,
                 side: Optional[str] = None, payload: Optional[Dict[str, Any]] = None,
                 source: Optional[str] = None) -> bool:
    """Insert one ledger event. Returns False when the uid was already recorded
    (idempotent ingestion of repeated MT5 history polls)."""
    try:
        ensure_schema(db)
        if event_recorded(db, event_uid):
            return False                      # already ingested — idempotent replay
        db.x(
            "INSERT OR IGNORE INTO trade_ledger_events "
            "(event_uid, ts, trade_uid, strategy_id, position_ticket, order_ticket,"
            " deal_ticket, event_type, stage, status, symbol, side, payload, source,"
            " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (str(event_uid), float(ts if ts is not None else time.time()), trade_uid,
             strategy_id, position_ticket, order_ticket, deal_ticket,
             str(event_type), stage, status, symbol, side,
             json.dumps(payload, default=str) if payload is not None else None,
             source, time.time()))
        return True
    except Exception as e:
        log.warning("ledger.record_event failed (%s): %s", event_type, e)
        return False


def event_recorded(db, event_uid: str) -> bool:
    try:
        return bool(db.one("SELECT id FROM trade_ledger_events WHERE event_uid=?",
                           (str(event_uid),)))
    except Exception:
        return False


def get_events(db, *, trade_uid: Optional[str] = None,
               position_ticket: Any = None, limit: int = 200) -> List[Dict[str, Any]]:
    ensure_schema(db)
    where, args = [], []
    if trade_uid:
        where.append("trade_uid=?"); args.append(trade_uid)
    if position_ticket not in (None, ""):
        where.append("position_ticket=?"); args.append(int(position_ticket))
    sql = "SELECT * FROM trade_ledger_events"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(int(limit))
    rows = db.q(sql, tuple(args)) or []
    out = []
    for r in rows:
        d = dict(r)
        if d.get("payload"):
            try:
                d["payload"] = json.loads(d["payload"])
            except Exception:
                pass
        out.append(d)
    return out


# --------------------------------------------------------------------------- #
# current-state rows — live_test_trades with V6.5 evidence fields
# --------------------------------------------------------------------------- #
_LEDGER_WRITE_FIELDS = (
    "trade_uid", "position_ticket", "order_ticket", "exit_order_ticket",
    "exit_deal_ticket", "comment", "strategy_identity", "signal_id", "signal_ts",
    "submit_ts", "response_ts", "fill_ts", "order_type", "fill_status",
    "sl_requested", "tp_requested", "sl_confirmed", "tp_confirmed",
    "sl_default", "tp_default", "sl_offset_pips", "tp_offset_pips",
    "spread_points", "slippage_points", "broker_comment", "entry_reason",
    "account_login", "account_server", "volume_filled", "volume_current",
    "closed_volume", "close_kind", "exit_reason", "exit_reason_source",
    "gross_pnl", "commission", "swap", "fees", "net_pnl", "duration_s",
    "point", "digits", "tick_size", "tick_value", "evidence_source",
    "reconcile_flags", "data_complete", "ledger_schema", "last_sync_ts",
)


def upsert_trade(db, row: Dict[str, Any]) -> Optional[int]:
    """Insert (or update-by-identity) one canonical trade row.

    Matching order: ``trade_uid`` first, then broker ``position_ticket``,
    then ``client_order_id`` — so a reconciliation pass can never create a
    second row for the same position.  Unmatched fields are left untouched on
    update (NULL means 'not available', never 'zero').
    """
    ensure_schema(db)
    row = dict(row)
    uid = row.get("trade_uid") or new_trade_uid()
    row["trade_uid"] = uid
    row.setdefault("ledger_schema", SCHEMA_VERSION)
    pos = row.get("position_ticket")
    cid = row.get("client_order_id")
    existing = None
    try:
        if row.get("_row_id"):
            existing = db.one("SELECT * FROM live_test_trades WHERE id=?", (int(row["_row_id"]),))
        if existing is None:
            existing = db.one("SELECT * FROM live_test_trades WHERE trade_uid=?", (uid,))
        if existing is None and pos not in (None, "", 0):
            existing = db.one(
                "SELECT * FROM live_test_trades WHERE position_ticket=? "
                "ORDER BY id DESC LIMIT 1", (int(pos),))
        if existing is None and cid:
            existing = db.one(
                "SELECT * FROM live_test_trades WHERE client_order_id=? "
                "ORDER BY id DESC LIMIT 1", (str(cid),))
    except Exception as e:
        log.warning("ledger.upsert_trade lookup failed: %s", e)
        return None

    fields = [f for f in _LEDGER_WRITE_FIELDS if f in row]
    try:
        if existing is not None:
            sets, vals = [], []
            for f in fields:
                if row.get(f) is not None:
                    sets.append(f"{f}=?"); vals.append(row[f])
            # update only fields that arrived with a value; evidence accretes
            if sets:
                sets.append("updated_at=?"); vals.append(time.time())
                vals.append(int(existing["id"]))
                db.x(f"UPDATE live_test_trades SET {','.join(sets)} WHERE id=?", tuple(vals))
            return int(existing["id"])
        # fresh row: legacy writer first (keeps legacy column defaults/NOT NULL), then evidence
        payload = {k: row.get(k) for k in row}
        payload["trade_uid"] = uid
        row_id = db.record_live_test_execution(payload)
        if row_id and fields:
            sets, vals = [], []
            for f in fields:
                sets.append(f"{f}=?"); vals.append(row.get(f))
            sets.append("ledger_schema=?"); vals.append(SCHEMA_VERSION)
            vals.append(int(row_id))
            db.x(f"UPDATE live_test_trades SET {','.join(sets)} WHERE id=?", tuple(vals))
        return int(row_id) if row_id else None
    except Exception as e:
        log.warning("ledger.upsert_trade write failed: %s", e)
        return None


def update_trade(db, row_id: int, **fields: Any) -> None:
    """Accrete evidence onto one row. NULL values are never written (they would
    erase facts); explicit clearing uses a dedicated reason field."""
    ensure_schema(db)
    allowed = set(_LEDGER_WRITE_FIELDS) | {"exit_price", "close_ts", "close_reason",
                                           "status", "result_json", "retcode",
                                           "ticket", "deal_ticket", "sl", "tp",
                                           "lots", "pnl", "pnl_pct"}
    sets, vals = [], []
    for k, v in fields.items():
        if k not in allowed or v is None:
            continue
        sets.append(f"{k}=?"); vals.append(v)
    if not sets:
        return
    sets.append("updated_at=?"); vals.append(time.time())
    vals.append(int(row_id))
    db.x(f"UPDATE live_test_trades SET {','.join(sets)} WHERE id=?", tuple(vals))


def find_open_managed(db, statuses=("POSITION_OPEN", "PENDING_ORDER", "EXECUTED_UNCONFIRMED",
                                    "UNKNOWN", "PARTIALLY_CLOSED")) -> List[Dict[str, Any]]:
    ensure_schema(db)
    ph = ",".join("?" for _ in statuses)
    return db.q(f"SELECT * FROM live_test_trades WHERE status IN ({ph}) "
                f"ORDER BY id DESC LIMIT 500", tuple(statuses)) or []


def mark_sync(db, row_id: int, ts: Optional[float] = None) -> None:
    update_trade(db, row_id, last_sync_ts=(ts if ts is not None else time.time()))
