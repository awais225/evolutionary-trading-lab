"""Canonical market-data store - ONE schema, ONE manifest, ONE merge discipline.

Shared by scripts/import_market_data.py (MT History-Center CSV files) and the
API's incremental MT5 fetch (app/market_data_service.py). There is deliberately
no second implementation of the candle schema or the manifest format: two
writers with diverging rules would create two competing sources of truth.

Store layout inside the DATA root:
    <DATA_ROOT>/MARKET_DATA/market_data.db   (tables: candles, datasets)
    <DATA_ROOT>/MARKET_DATA/import_manifest.json

Conventions (binding, from the importer):
  * `ts` is UTC epoch SECONDS (the one canonical internal convention).
    CSV source wall time is converted to UTC once, at import; MT5 rate
    `time` values are UTC epoch seconds and are stored verbatim after
    validation. No broker offset is ever applied inside the store;
  * candles are never invented; merges never silently overwrite a conflicting
    stored candle (policy-dependent, see merge_candles);
  * VOL / TICKVOL / SPREAD are separate columns, never merged;
  * a dataset is AVAILABLE only after validation PASSed.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

TIMEFRAME_STEPS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800,
                   "H1": 3600, "H4": 14400, "D1": 86400}
TIMEFRAME_ORDER = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]
TF_ALIASES = {"Daily": "D1", "D1": "D1", "M1": "M1", "M5": "M5", "M15": "M15",
              "M30": "M30", "H1": "H1", "H4": "H4"}
EPS = 1e-9

# Plausible stored-candle range. Anything outside is a malformed/unit-mixed
# timestamp (1970 epoch accidents, 2926-style garbage, milliseconds or
# nanoseconds mistaken for seconds) and must be rejected, never repaired.
MIN_TS = 315_532_800    # 1980-01-01T00:00:00Z
MAX_TS = 4_102_444_800  # 2100-01-01T00:00:00Z (exclusive)

SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
  dataset_id    TEXT NOT NULL,
  instrument    TEXT NOT NULL,
  timeframe     TEXT NOT NULL,
  ts            INTEGER NOT NULL,
  ts_iso        TEXT NOT NULL,
  open          REAL NOT NULL,
  high          REAL NOT NULL,
  low           REAL NOT NULL,
  close         REAL NOT NULL,
  tick_volume   INTEGER,
  volume        REAL,
  spread        INTEGER,
  PRIMARY KEY (instrument, timeframe, ts)
);
CREATE INDEX IF NOT EXISTS ix_candles_dataset ON candles(dataset_id);
CREATE TABLE IF NOT EXISTS fetch_conflicts (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  operation_id      TEXT NOT NULL,
  instrument        TEXT NOT NULL,
  timeframe         TEXT NOT NULL,
  ts                INTEGER NOT NULL,
  ts_iso            TEXT NOT NULL,
  stored_dataset_id TEXT,
  stored_source     TEXT,
  incoming_source   TEXT,
  stored            TEXT NOT NULL,
  incoming          TEXT NOT NULL,
  differing_fields  TEXT NOT NULL,
  classification    TEXT NOT NULL DEFAULT 'CONFLICT_STORED_PRESERVED',
  resolution        TEXT NOT NULL DEFAULT 'UNRESOLVED',
  resolution_note   TEXT,
  resolved_at       TEXT,
  created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_fetch_conflicts_op ON fetch_conflicts(operation_id);
CREATE INDEX IF NOT EXISTS ix_fetch_conflicts_resolution ON fetch_conflicts(resolution);
CREATE TABLE IF NOT EXISTS datasets (
  dataset_id            TEXT PRIMARY KEY,
  instrument            TEXT NOT NULL,
  timeframe             TEXT NOT NULL,
  source_file           TEXT NOT NULL,
  source_sha256         TEXT NOT NULL,
  source_bytes          INTEGER NOT NULL,
  row_count             INTEGER NOT NULL,
  duplicates_collapsed  INTEGER NOT NULL,
  gaps_found            INTEGER NOT NULL,
  largest_gap_seconds   INTEGER,
  first_ts              TEXT,
  last_ts               TEXT,
  content_fingerprint   TEXT NOT NULL,
  location              TEXT NOT NULL,
  imported_at           TEXT NOT NULL,
  validation_result     TEXT NOT NULL,
  validation_report     TEXT NOT NULL,
  status                TEXT NOT NULL
);
"""

_CANDLE_KEYS = ("open", "high", "low", "close", "tick_volume", "volume", "spread")


def validate_timestamp(ts: Any) -> Optional[str]:
    """First timestamp error for a candidate stored `ts`, or None.

    The canonical unit is UTC epoch seconds. Booleans, non-integral values,
    millisecond/nanosecond magnitudes and out-of-range years are rejected
    with the offending value named - never silently rescaled.
    """
    if isinstance(ts, bool) or ts is None:
        return f"invalid timestamp {ts!r}: not a UTC epoch-second integer"
    try:
        value = int(ts)
    except (TypeError, ValueError):
        return f"invalid timestamp {ts!r}: not a UTC epoch-second integer"
    if float(ts) != value:
        return f"invalid timestamp {ts!r}: not a whole UTC epoch second"
    if not (MIN_TS <= value < MAX_TS):
        return (f"invalid timestamp {ts!r}: outside the plausible candle range "
                f"1980-01-01..2100-01-01 UTC (wrong unit or malformed value)")
    return None


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ts_iso_of(ts: int) -> str:
    """UTC rendering of a stored UTC epoch-second timestamp."""
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def connect_store(db_path: Path) -> sqlite3.Connection:
    """Open the canonical store.

    WAL journaling (readers never block the single writer, and vice versa)
    plus a 30 s busy timeout: the previous default rollback-journal mode
    let concurrent availability reads and the fetch writer stall each
    other (ISSUE-052/053).
    """
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    return conn


def validate_candle(c: Dict[str, Any]) -> Optional[str]:
    """First validation error for a normalized candle dict, or None.

    Same rules the CSV importer has always enforced (its REJECTED datasets
    cite these messages): finite positive OHLC, HIGH/LOW bracket OPEN/CLOSE,
    non-negative integer tick volume / spread, non-negative volume. A candle
    that fails is NEVER repaired - the caller rejects it and says why.
    """
    ts_error = validate_timestamp(c.get("ts"))
    if ts_error is not None:
        return ts_error
    o, h, l, cl = (c.get("open"), c.get("high"), c.get("low"), c.get("close"))
    if None in (o, h, l, cl):
        return f"non-numeric OHLC {[c.get(k) for k in ('open', 'high', 'low', 'close')]}"
    if not all(isinstance(v, (int, float)) and v == v and v not in (float("inf"), float("-inf"))
               for v in (o, h, l, cl)):
        return f"non-finite OHLC O={o} H={h} L={l} C={cl}"
    if not all(v > 0 for v in (o, h, l, cl)):
        return f"non-positive price(s) O={o} H={h} L={l} C={cl}"
    if h + EPS < l:
        return f"HIGH {h} < LOW {l}"
    if h + EPS < max(o, cl):
        return f"HIGH {h} < max(OPEN,CLOSE) = {max(o, cl)}"
    if l - EPS > min(o, cl):
        return f"LOW {l} > min(OPEN,CLOSE) = {min(o, cl)}"
    tv = c.get("tick_volume")
    if tv is None or int(tv) != tv or int(tv) < 0:
        return f"TICKVOL not a non-negative integer: {tv!r}"
    vol = c.get("volume")
    if vol is None or float(vol) < 0:
        return f"VOL not a non-negative number: {vol!r}"
    sp = c.get("spread")
    if sp is None or int(sp) != sp or int(sp) < 0:
        return f"SPREAD not a non-negative integer: {sp!r}"
    return None


def candle_equal(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    return all(a.get(k) == b.get(k) for k in _CANDLE_KEYS)


# Price/volume fields that define a candle's market meaning. `volume` (real
# volume, all-zero in the supplied XAUUSD CSV exports) and `spread` (a broker
# quote property that terminals routinely report differently for the same
# historical bar) do NOT change what the candle IS.
_OHLC_KEYS = ("open", "high", "low", "close", "tick_volume")
_REPRESENTATION_KEYS = ("volume", "spread")


def classify_overlap(stored: Dict[str, Any], incoming: Dict[str, Any]) -> str:
    """Classify an incoming candle that lands on an existing timestamp.

    "duplicate"      - every stored field matches (category A: exact duplicate)
    "representation" - OHLC + tick volume match; only volume/spread differ
                       (category B: harmless representation difference)
    "conflict"       - price or tick volume differs (category C/D: stored
                       candle is preserved, the difference is recorded)
    """
    if candle_equal(stored, incoming):
        return "duplicate"
    if all(stored.get(k) == incoming.get(k) for k in _OHLC_KEYS):
        return "representation"
    return "conflict"


def candle_diff(stored: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    """Field-by-field comparison used for conflict diagnostics."""
    fields = {}
    for k in _CANDLE_KEYS:
        if stored.get(k) != incoming.get(k):
            fields[k] = {"stored": stored.get(k), "incoming": incoming.get(k)}
    return fields


def canonical_fingerprint(rows: List[Dict[str, Any]]) -> str:
    h = hashlib.sha256()
    for r in rows:
        h.update(f"{r['ts']}|{r['open']}|{r['high']}|{r['low']}|{r['close']}|"
                 f"{r['tick_volume']}|{r['volume']}|{r['spread']}\n".encode("utf-8"))
    return h.hexdigest()


def gap_stats(rows: List[Dict[str, Any]], step: int) -> Tuple[int, Optional[int]]:
    gaps = 0
    largest: Optional[int] = None
    for prev, cur in zip(rows, rows[1:]):
        delta = int(cur["ts"]) - int(prev["ts"])
        if delta > int(step * 1.5):
            gaps += 1
            largest = max(largest or 0, delta)
    return gaps, largest


def merge_candles(conn: sqlite3.Connection, *, dataset_id: str, instrument: str,
                  timeframe: str, rows: List[Dict[str, Any]], policy: str,
                  progress=None) -> Dict[str, Any]:
    """progress(stage, **ctx), when given, is called at real processing
    boundaries (existing-lookup, comparisons, batch insert) - never per
    candle (ISSUE-053 fine-grained DB instrumentation)."""
    """Merge unique-by-ts candles into the store under the caller's policy.

    policy="import" (CSV files, provenance roll-forward): a newer dataset MAY
    take over timestamps from an older one - counted in
    overwritten_other_source, never silent.
    policy="fetch" (MT5 incremental): stored history is authoritative -
    overlaps are classified (classify_overlap): exact duplicates and
    representation differences are skipped; CONFLICTING overlaps are isolated
    (recorded in conflict_details, stored candle preserved) WITHOUT aborting
    the timeframe - genuinely new candles are still inserted.
    Returns {inserted, overwritten_other_source, duplicates_skipped,
    representation_diffs, conflicts, conflict_details}.
    """
    existing = {
        int(ts): {"dataset_id": ds, "open": o, "high": h, "low": l, "close": c,
                  "tick_volume": tv, "volume": v, "spread": sp}
        for ts, ds, o, h, l, c, tv, v, sp in conn.execute(
            "SELECT ts, dataset_id, open, high, low, close, tick_volume, volume, spread"
            " FROM candles WHERE instrument = ? AND timeframe = ?",
            (instrument, timeframe))
    }
    if progress is not None:
        progress("EXISTING_CANDLE_LOOKUP_COMPLETED", existing_records=len(existing),
                 incoming_records=len(rows))
    inserted = overwritten = duplicates = 0
    representation_diffs = 0
    conflicts: List[int] = []
    conflict_details: List[Dict[str, Any]] = []
    to_insert: List[Dict[str, Any]] = []
    for r in rows:
        ts = int(r["ts"])
        prior = existing.get(ts)
        if prior is not None:
            if policy == "fetch":
                kind = classify_overlap(prior, r)
                if kind == "duplicate":
                    duplicates += 1
                elif kind == "representation":
                    duplicates += 1
                    representation_diffs += 1
                else:
                    conflicts.append(ts)
                    if len(conflict_details) < 100:
                        conflict_details.append({
                            "ts": ts, "ts_iso": r.get("ts_iso") or ts_iso_of(ts),
                            "classification": "CONFLICT_STORED_PRESERVED",
                            "stored_dataset_id": prior.get("dataset_id"),
                            "differing_fields": candle_diff(prior, r),
                            "stored": {k: prior.get(k) for k in _CANDLE_KEYS},
                            "incoming": {k: r.get(k) for k in _CANDLE_KEYS},
                        })
                continue
            if prior["dataset_id"] != dataset_id:
                overwritten += 1
        else:
            inserted += 1
        to_insert.append(r)
    if progress is not None:
        progress("DUPLICATE_COMPARISON_COMPLETED", duplicates_skipped=duplicates,
                 representation_diffs=representation_diffs)
        progress("CONFLICT_COMPARISON_COMPLETED", conflicts=len(conflicts))
        progress("NEW_RECORDS_IDENTIFIED", new_records=len(to_insert))
        progress("INSERT_BATCH_STARTED", records=len(to_insert))
    if to_insert:
        # ONE batch insert (executemany) instead of per-candle statements.
        conn.executemany(
            "INSERT INTO candles (dataset_id, instrument, timeframe, ts, ts_iso,"
            " open, high, low, close, tick_volume, volume, spread)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(instrument, timeframe, ts) DO UPDATE SET"
            "   dataset_id=excluded.dataset_id, ts_iso=excluded.ts_iso,"
            "   open=excluded.open, high=excluded.high, low=excluded.low,"
            "   close=excluded.close, tick_volume=excluded.tick_volume,"
            "   volume=excluded.volume, spread=excluded.spread",
            [(dataset_id, instrument, timeframe, int(r["ts"]), r["ts_iso"],
              r["open"], r["high"], r["low"], r["close"],
              r["tick_volume"], r["volume"], r["spread"]) for r in to_insert])
    if progress is not None:
        progress("INSERT_BATCH_COMPLETED", inserted=inserted)
    return {"inserted": inserted, "overwritten_other_source": overwritten,
            "duplicates_skipped": duplicates,
            "representation_diffs": representation_diffs,
            "conflicts": conflicts, "conflict_details": conflict_details}


def record_dataset(conn: sqlite3.Connection, *, dataset_id: str, instrument: str,
                   timeframe: str, source_file: str, source_sha256: str,
                   source_bytes: int, row_count: int, duplicates_collapsed: int,
                   gaps_found: int, largest_gap_seconds: Optional[int],
                   first_ts: Optional[str], last_ts: Optional[str],
                   content_fingerprint: str, location: str, imported_at: str,
                   validation_result: str, validation_report: Dict[str, Any],
                   status: str) -> None:
    conn.execute(
        "INSERT INTO datasets (dataset_id, instrument, timeframe, source_file, source_sha256,"
        " source_bytes, row_count, duplicates_collapsed, gaps_found, largest_gap_seconds,"
        " first_ts, last_ts, content_fingerprint, location, imported_at,"
        " validation_result, validation_report, status)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (dataset_id, instrument, timeframe, source_file, source_sha256, source_bytes,
         row_count, duplicates_collapsed, gaps_found, largest_gap_seconds,
         first_ts, last_ts, content_fingerprint, location, imported_at,
         validation_result, json.dumps(validation_report), status))


def rebuild_manifest(conn: sqlite3.Connection, db_path: Path) -> Dict[str, Any]:
    rows = conn.execute(
        "SELECT dataset_id, instrument, timeframe, source_file, source_sha256, source_bytes,"
        " row_count, duplicates_collapsed, gaps_found, largest_gap_seconds, first_ts, last_ts,"
        " content_fingerprint, location, imported_at, validation_result, status"
        " FROM datasets ORDER BY instrument, timeframe, imported_at").fetchall()
    manifest = {
        "schema_version": 1,
        "generated_at": iso_now(),
        "store": str(db_path),
        "datasets": [
            {"dataset_id": r[0], "instrument": r[1], "timeframe": r[2], "source_file": r[3],
             "source_sha256": r[4], "source_bytes": r[5], "row_count": r[6],
             "duplicates_collapsed": r[7], "gaps_found": r[8], "largest_gap_seconds": r[9],
             "first_ts": r[10], "last_ts": r[11], "fingerprint": r[12], "location": r[13],
             "imported_at": r[14], "validation_result": r[15], "status": r[16]}
            for r in rows],
    }
    out = db_path.parent / "import_manifest.json"
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    os.replace(tmp, out)
    return manifest


def latest_ts(conn: sqlite3.Connection, instrument: str, timeframe: str) -> Optional[int]:
    row = conn.execute("SELECT MAX(ts) FROM candles WHERE instrument = ? AND timeframe = ?",
                       (instrument, timeframe)).fetchone()
    return int(row[0]) if row and row[0] is not None else None


def series_stats(conn: sqlite3.Connection, instrument: str, timeframe: str) -> Dict[str, Any]:
    row = conn.execute(
        "SELECT COUNT(*), MIN(ts_iso), MAX(ts_iso) FROM candles"
        " WHERE instrument = ? AND timeframe = ?", (instrument, timeframe)).fetchone()
    return {"row_count": int(row[0] or 0), "first_ts": row[1], "last_ts": row[2]}


def list_datasets(conn: sqlite3.Connection, instrument: Optional[str] = None) -> List[Dict[str, Any]]:
    q = ("SELECT dataset_id, instrument, timeframe, source_file, row_count, first_ts, last_ts,"
         " validation_result, status, imported_at, duplicates_collapsed, gaps_found,"
         " largest_gap_seconds, source_sha256 FROM datasets")
    args: Tuple[Any, ...] = ()
    if instrument:
        q += " WHERE instrument = ?"
        args = (instrument,)
    q += " ORDER BY timeframe, imported_at"
    cols = ("dataset_id", "instrument", "timeframe", "source_file", "row_count",
            "first_ts", "last_ts", "validation_result", "status", "imported_at",
            "duplicates_collapsed", "gaps_found", "largest_gap_seconds", "source_sha256")
    return [dict(zip(cols, r)) for r in conn.execute(q, args)]


__all__ = [
    "TIMEFRAME_STEPS", "TIMEFRAME_ORDER", "TF_ALIASES", "SCHEMA",
    "iso_now", "ts_iso_of", "connect_store", "validate_timestamp", "validate_candle", "candle_equal",
    "canonical_fingerprint", "gap_stats", "merge_candles", "record_dataset",
    "rebuild_manifest", "latest_ts", "series_stats", "list_datasets",
]


# ---------------------------------------------------------- conflict audit
def record_fetch_conflicts(conn: sqlite3.Connection, *, operation_id: str,
                           instrument: str, timeframe: str,
                           conflict_details: List[Dict[str, Any]],
                           incoming_source: str = "") -> int:
    """Persist conflict records durably (ISSUE-054, source-aware audit).

    The stored candle is NEVER modified here. Each row keeps the stored
    values, the incoming values, both provenances and the field-level diff,
    so a later controlled resolution has a complete before/after record.
    Runs in the caller's transaction context (no implicit commit).
    """
    import json as _json
    from datetime import datetime as _dt, timezone as _tz
    now = _dt.now(_tz.utc).isoformat()
    n = 0
    for d in conflict_details:
        stored = d.get("stored") or {}
        incoming = d.get("incoming") or {}
        ds = d.get("stored_dataset_id")
        src_row = None
        if ds:
            src_row = conn.execute(
                "SELECT source_file FROM datasets WHERE dataset_id = ?", (ds,)).fetchone()
        stored_source = (f"CSV import: {src_row[0]}" if src_row
                         else ("MT5 fetch (prior fetch dataset)" if ds else "unknown"))
        conn.execute(
            "INSERT INTO fetch_conflicts (operation_id, instrument, timeframe, ts, ts_iso,"
            " stored_dataset_id, stored_source, incoming_source, stored, incoming,"
            " differing_fields, classification, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (operation_id, instrument, timeframe, int(d["ts"]),
             d.get("ts_iso") or ts_iso_of(int(d["ts"])), ds, stored_source,
             incoming_source, _json.dumps(stored), _json.dumps(incoming),
             _json.dumps(d.get("differing_fields") or {}),
             d.get("classification") or "CONFLICT_STORED_PRESERVED", now))
        n += 1
    return n


def list_fetch_conflicts(conn: sqlite3.Connection, *, instrument: Optional[str] = None,
                         operation_id: Optional[str] = None,
                         resolution: Optional[str] = None,
                         limit: int = 200) -> List[Dict[str, Any]]:
    import json as _json
    q = "SELECT * FROM fetch_conflicts WHERE 1=1"
    args: List[Any] = []
    if instrument:
        q += " AND instrument = ?"; args.append(instrument)
    if operation_id:
        q += " AND operation_id = ?"; args.append(operation_id)
    if resolution:
        q += " AND resolution = ?"; args.append(resolution)
    q += " ORDER BY id DESC LIMIT ?"; args.append(max(1, min(int(limit), 1000)))
    cur = conn.execute(q, args)
    cols = [c[0] for c in cur.description]
    out = []
    for row in cur.fetchall():
        d = dict(zip(cols, row))
        for k in ("stored", "incoming", "differing_fields"):
            try:
                d[k] = _json.loads(d[k])
            except Exception:  # noqa: BLE001
                pass
        out.append(d)
    return out


def resolve_fetch_conflict(conn: sqlite3.Connection, conflict_id: int, *,
                           action: str, note: Optional[str] = None) -> Dict[str, Any]:
    """Controlled, audited resolution of ONE conflict (ISSUE-054).

    action="KEPT_STORED": mark resolved; the stored candle is untouched.
    action="ACCEPTED_INCOMING": replace the stored candle's values with the
    incoming MT5 values. The pre-resolution stored values remain on this
    audit row (the backup) and the caller publishes a full audit event.
    Executed inside ONE transaction; returns {conflict, before, after}.
    """
    import json as _json
    from datetime import datetime as _dt, timezone as _tz
    cur = conn.execute("SELECT * FROM fetch_conflicts WHERE id = ?",
                       (int(conflict_id),))
    row = cur.fetchone()
    if row is None:
        raise KeyError(f"fetch conflict {conflict_id} not found")
    d = dict(zip([c[0] for c in cur.description], row))
    if d["resolution"] != "UNRESOLVED":
        raise ValueError(f"conflict {conflict_id} already resolved ({d['resolution']})")
    before = _json.loads(d["stored"])
    after = before
    now = _dt.now(_tz.utc).isoformat()
    with conn:
        if action == "ACCEPTED_INCOMING":
            incoming = _json.loads(d["incoming"])
            cur = conn.execute(
                "UPDATE candles SET open=?, high=?, low=?, close=?, tick_volume=?,"
                " volume=?, spread=? WHERE instrument=? AND timeframe=? AND ts=?",
                (incoming.get("open"), incoming.get("high"), incoming.get("low"),
                 incoming.get("close"), incoming.get("tick_volume"),
                 incoming.get("volume"), incoming.get("spread"),
                 d["instrument"], d["timeframe"], d["ts"]))
            if cur.rowcount != 1:
                raise KeyError("stored candle no longer exists; conflict cannot be resolved")
            after = incoming
        elif action != "KEPT_STORED":
            raise ValueError(f"unknown resolution action {action!r}")
        conn.execute(
            "UPDATE fetch_conflicts SET resolution=?, resolution_note=?, resolved_at=?"
            " WHERE id=?", (action, note, now, int(conflict_id)))
    d.update({"resolution": action, "resolution_note": note, "resolved_at": now})
    for k in ("stored", "incoming", "differing_fields"):
        try:
            d[k] = _json.loads(d[k])
        except Exception:  # noqa: BLE001
            pass
    return {"conflict": d, "before": before, "after": after}
