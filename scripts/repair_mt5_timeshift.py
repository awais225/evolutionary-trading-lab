#!/usr/bin/env python3
"""Repair MT5-fetched candles stored with broker server-epoch timestamps.

Background (ISSUE-059): before the fix, the fetch path stored
`copy_rates_range()` server-epoch times verbatim into the canonical
TRUE-UTC store, so every MT5-appended candle sits 2-3 hours ahead of its
true UTC instant (IC Markets GMT+2/+3, America/New_York DST dates). This
tool re-labels ONLY candles belonging to mt5-fetch datasets
(`datasets.source_file LIKE 'mt5-fetch:%'`), converting each `ts` with
`hero1_mt5_adapter.server_time.server_to_utc_epoch()` - the same
conversion the fixed fetch path and the CSV importer apply.

Safety contract:
  * explicit `--db PATH` required; the path is used exactly as given and
    is NEVER defaulted to a repository database;
  * dry-run by default - nothing is written without `--apply`;
  * `--apply` first writes a timestamped backup next to the database
    (refuses to run if the backup cannot be created);
  * one transaction; stored (CSV) candles are never modified;
  * a shifted candle that lands on an existing stored candle with
    identical OHLCV is dropped as a duplicate; one that lands on a
    DIFFERENT stored candle is left untouched and reported as an
    unresolved collision (never overwritten);
  * pre-existing `fetch_conflicts` rows are never modified; a shifted
    candle colliding with a DIFFERENT stored candle is removed from the
    series and preserved as a NEW UNRESOLVED audit row (both values kept)
    for the operator's explicit per-record decision;
  * integrity_check runs before and after; any failure aborts.

Usage:
  python scripts/repair_mt5_timeshift.py --db "E:\\...\\market_data.db"
  python scripts/repair_mt5_timeshift.py --db "E:\\...\\market_data.db" --apply
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                       / "packages" / "mt5_adapter" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                       / "packages" / "market_data" / "src"))

from hero1_mt5_adapter import server_time  # noqa: E402
from hero1_market_data import store as md  # noqa: E402

_CANDLE_KEYS = ("open", "high", "low", "close", "tick_volume", "volume", "spread")
_OHLC = slice(0, 4)  # duplicate = same candle: OHLC equal (spread/volume are
# representation metadata - the fetch merge treats them the same way)


def _fetch_dataset_ids(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT dataset_id, validation_report FROM datasets").fetchall()
    out = []
    for ds_id, report in rows:
        try:
            kind = (json.loads(report) or {}).get("kind") if report else None
        except Exception:  # noqa: BLE001
            kind = None
        src = conn.execute("SELECT source_file FROM datasets WHERE dataset_id = ?",
                           (ds_id,)).fetchone()[0]
        if kind == "mt5-fetch" or str(src or "").startswith("mt5-fetch:"):
            out.append(ds_id)
    return out


def plan(conn: sqlite3.Connection) -> dict:
    fetch_ids = _fetch_dataset_ids(conn)
    if not fetch_ids:
        return {"fetch_datasets": 0, "shifted": 0, "per_timeframe": {}}
    ph = ",".join("?" * len(fetch_ids))
    shifted = conn.execute(
        f"SELECT instrument, timeframe, ts, dataset_id, {', '.join(_CANDLE_KEYS)}"
        f" FROM candles WHERE dataset_id IN ({ph}) ORDER BY timeframe, ts",
        fetch_ids).fetchall()
    stored_keys = {}
    for row in conn.execute(
            f"SELECT instrument, timeframe, ts, {', '.join(_CANDLE_KEYS)} FROM candles"
            f" WHERE dataset_id NOT IN ({ph})", fetch_ids):
        stored_keys[(row[0], row[1], int(row[2]))] = tuple(row[3:])
    moves, dupes, collisions = [], [], []
    collision_rows: list = []
    seen_targets: dict = {}
    for row in shifted:
        inst, tf, ts, ds_id, vals = row[0], row[1], int(row[2]), row[3], tuple(row[4:])
        new_ts = server_time.server_to_utc_epoch(ts)
        key = (inst, tf, new_ts)
        if key in stored_keys:
            if tuple(stored_keys[key])[_OHLC] == vals[_OHLC]:
                dupes.append({"timeframe": tf, "from": md.ts_iso_of(ts), "to": md.ts_iso_of(new_ts)})
            else:
                collisions.append({"timeframe": tf, "from": md.ts_iso_of(ts),
                                   "to": md.ts_iso_of(new_ts)})
                collision_rows.append({"instrument": inst, "timeframe": tf,
                                       "old_ts": ts, "new_ts": new_ts,
                                       "dataset_id": ds_id, "values": vals,
                                       "stored_values": tuple(stored_keys[key])})
        elif key in seen_targets and seen_targets[key][_OHLC] != vals[_OHLC]:
            collisions.append({"timeframe": tf, "from": md.ts_iso_of(ts),
                               "to": md.ts_iso_of(new_ts), "note": "two shifted candles collide"})
            collision_rows.append({"instrument": inst, "timeframe": tf,
                                   "old_ts": ts, "new_ts": new_ts,
                                   "dataset_id": ds_id, "values": vals,
                                   "stored_values": tuple(seen_targets[key])})
        else:
            seen_targets[key] = vals
            moves.append({"instrument": inst, "timeframe": tf, "old_ts": ts,
                          "new_ts": new_ts, "dataset_id": ds_id, "values": vals})
    per_tf: dict = {}
    for m in moves:
        per_tf[m["timeframe"]] = per_tf.get(m["timeframe"], 0) + 1
    return {"fetch_datasets": len(fetch_ids), "shifted": len(shifted),
            "to_move": len(moves), "duplicates_dropped": len(dupes),
            "collisions_recorded_as_conflicts": len(collisions),
            "collisions_examples": collisions[:10], "per_timeframe": per_tf,
            "_moves": moves, "_fetch_ids": fetch_ids,
            "_collision_rows": collision_rows}


def apply_plan(conn: sqlite3.Connection, p: dict) -> dict:
    moves = p.pop("_moves")
    fetch_ids = p.pop("_fetch_ids")
    collision_rows = p.pop("_collision_rows", [])
    ph = ",".join("?" * len(fetch_ids))
    with conn:
        conn.execute(f"DELETE FROM candles WHERE dataset_id IN ({ph})", fetch_ids)
        conn.executemany(
            "INSERT INTO candles (dataset_id, instrument, timeframe, ts, ts_iso,"
            " open, high, low, close, tick_volume, volume, spread)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(m["dataset_id"], m["instrument"], m["timeframe"], m["new_ts"],
              md.ts_iso_of(m["new_ts"]), *m["values"]) for m in moves])
        # collisions: stored stays; the shifted incoming candle leaves the
        # series and is preserved as an UNRESOLVED audit conflict with both
        # values - the same contract as fetch-time conflicts (ISSUE-054)
        for cr in collision_rows:
            stored_row = conn.execute(
                "SELECT dataset_id FROM candles WHERE instrument = ? AND timeframe = ? AND ts = ?",
                (cr["instrument"], cr["timeframe"], cr["new_ts"])).fetchone()
            stored_ds = stored_row[0] if stored_row else cr["dataset_id"]
            src_row = conn.execute("SELECT source_file FROM datasets WHERE dataset_id = ?",
                                   (stored_ds,)).fetchone()
            stored_d = dict(zip(_CANDLE_KEYS, cr["stored_values"]))
            incoming_d = dict(zip(_CANDLE_KEYS, cr["values"]))
            conn.execute(
                "INSERT INTO fetch_conflicts (operation_id, instrument, timeframe, ts, ts_iso,"
                " stored_dataset_id, stored_source, incoming_source, stored, incoming,"
                " differing_fields, classification, resolution, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'CONFLICT_STORED_PRESERVED',"
                " 'UNRESOLVED', ?)",
                (f"REPAIR-TIMESHIFT-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}",
                 cr["instrument"], cr["timeframe"], cr["new_ts"], md.ts_iso_of(cr["new_ts"]),
                 stored_ds, (src_row[0] if src_row else stored_ds),
                 "MT5 copy_rates_range (re-labelled by repair_mt5_timeshift)",
                 json.dumps(stored_d), json.dumps(incoming_d),
                 json.dumps(md.candle_diff({**stored_d, "ts": cr["new_ts"]},
                                           {**incoming_d, "ts": cr["new_ts"]})),
                 md.iso_now()))
        # refresh fetch dataset ranges/counts from the candles themselves
        for ds_id in fetch_ids:
            row = conn.execute(
                "SELECT timeframe, COUNT(*), MIN(ts), MAX(ts) FROM candles"
                " WHERE dataset_id = ? GROUP BY timeframe", (ds_id,)).fetchone()
            if row:
                conn.execute(
                    "UPDATE datasets SET row_count = ?, first_ts = ?, last_ts = ?"
                    " WHERE dataset_id = ?",
                    (row[1], md.ts_iso_of(row[2]), md.ts_iso_of(row[3]), ds_id))
    p["applied"] = True
    return p


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True, help="explicit path to the market_data.db to repair")
    ap.add_argument("--apply", action="store_true", help="actually write (default: dry-run)")
    args = ap.parse_args()

    db = Path(args.db).expanduser()
    if not db.is_file():
        print(f"refusing: {db} does not exist"); return 2
    conn = sqlite3.connect(str(db))
    try:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            print("refusing: integrity_check failed before repair"); return 2
        p = plan(conn)
        summary = {k: v for k, v in p.items() if not k.startswith("_")}
        print(json.dumps(summary, indent=1))
        if not p.get("shifted"):
            print("no mt5-fetch candles found - nothing to repair"); return 0
        if not args.apply:
            print("\nDRY-RUN only. Re-run with --apply to write (a backup is created first).")
            print("NOTE: fetch_conflicts rows are never modified by this tool; the conflicts"
                  " recorded under the shifted labels remain for your explicit per-record"
                  " decisions (after repair the incoming values equal the stored ones).")
            return 0
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup = db.with_name(f"{db.name}.pre-timeshift-repair-{stamp}.bak")
        shutil.copy2(db, backup)
        print(f"backup written: {backup}")
        out = apply_plan(conn, p)
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            print("ERROR: integrity_check failed after repair - restore from the backup!")
            return 1
        print(json.dumps({k: v for k, v in out.items() if not k.startswith('_')}, indent=1))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
