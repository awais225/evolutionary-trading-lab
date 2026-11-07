#!/usr/bin/env python3
"""Import MT History-Center CSV exports into the Hero1 market-data store.

Source format (as exported by MetaTrader): tab-separated, angle-bracket
column headers, e.g.

    <DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>

(Daily exports omit <TIME>.) Dates are `YYYY.MM.DD`, times `HH:MM:SS`.

Binding guarantees (enforced, not aspirational):

  * never INVENTS candles - any row that fails validation rejects the whole
    dataset, all-or-nothing, in one transaction; nothing is partially loaded
  * never silently DISCARDS rows - exact duplicate candles collapse (counted,
    reported); conflicting candles for the same timestamp FAIL the import
  * never MIXES instruments/timeframes - one dataset per (instrument,
    timeframe) file; the instrument/timeframe comes from the filename or the
    explicit flags, never guessed from content
  * gaps are REPORTED, never filled
  * timestamps stay NAIVE broker-server time (ICMarkets: GMT+2 winter /
    GMT+3 summer). `ts` stores epoch seconds tagged as UTC *only* for stable
    ordering; it is never converted to or from real UTC and must not be
    compared against other feeds
  * `VOL` is stored as-reported (broker exports it all-zero for XAUUSD);
    `TICKVOL` is stored separately as `tick_volume`; `SPREAD` is stored
    separately. Columns are never merged silently
  * a dataset is `AVAILABLE` only after validation passes; otherwise the
    datasets table records a REJECTED entry (provenance of the attempt) and
    ZERO candle rows are written

Usage:
    python scripts/import_market_data.py --data-root /path/to/DATA FILE... [--force] [--dry-run]
    python scripts/import_market_data.py --data-root /path/to/DATA --instrument XAUUSD XAUUSD_H1.csv

Exit codes: 0 = every file PASS or SKIPPED; 1 = at least one file REJECTED; 2 = usage/IO error.
"""
from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from hero1_market_data import store as _store

TIMEFRAME_STEPS = _store.TIMEFRAME_STEPS
TF_ALIASES = _store.TF_ALIASES
FILENAME_RE = re.compile(r"^(?P<instrument>[A-Za-z0-9]{3,16})_(?P<tf>Daily|D1|M1|M5|M15|M30|H1|H4)\.csv$")
REQUIRED_COLS = ("DATE", "OPEN", "HIGH", "LOW", "CLOSE")
OPTIONAL_COLS = ("TIME", "TICKVOL", "VOL", "SPREAD")
MAX_ERRORS_KEEP = 20


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _num(raw: str):
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _int(raw: str):
    f = _num(raw)
    if f is None or f != int(f):
        return None
    return int(f)


def _parse_ts(date_s: str, time_s: str | None) -> tuple[int, str] | None:
    try:
        if time_s:
            dt = datetime.strptime(f"{date_s.strip()} {time_s.strip()}", "%Y.%m.%d %H:%M:%S")
        else:
            dt = datetime.strptime(date_s.strip(), "%Y.%m.%d")
    except ValueError:
        return None
    if dt.year < 1980 or dt.year > 2100:
        return None
    ts = calendar.timegm(dt.utctimetuple())  # naive broker time tagged UTC - ordering only
    return ts, dt.strftime("%Y-%m-%d %H:%M:%S")


def parse_rows(path: Path) -> dict:
    """Parse the raw CSV into normalized candle dicts + a structured report.

    Returns dict with keys: rows (list), errors (list of str), notes (list of str),
    total_data_rows, duplicates_collapsed, inversions_sorted, gaps, largest_gap_seconds."""
    errors: list[str] = []
    notes: list[str] = []
    raw_lines = 0
    candles: list[dict] = []

    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        if reader.fieldnames is None:
            return {"rows": [], "errors": ["empty file / missing header row"], "notes": notes,
                    "total_data_rows": 0, "duplicates_collapsed": 0, "inversions_sorted": 0,
                    "gaps": 0, "largest_gap_seconds": None}
        cols = [str(c or "").strip().strip("<>").upper() for c in reader.fieldnames]
        if len(set(cols)) != len(cols):
            errors.append(f"duplicate column(s) in header: {cols}")
        rename = dict(zip(reader.fieldnames, cols))
        missing_required = [c for c in REQUIRED_COLS if c not in cols]
        if missing_required:
            return {"rows": [], "errors": [f"missing required column(s): {', '.join(missing_required)} "
                                           f"(header was: {' '.join(cols)})"],
                    "notes": notes, "total_data_rows": 0, "duplicates_collapsed": 0,
                    "inversions_sorted": 0, "gaps": 0, "largest_gap_seconds": None}
        has_time = "TIME" in cols

        for line_no, raw_row in enumerate(reader, start=2):
            row = {rename[k]: (v.strip() if isinstance(v, str) else v) for k, v in raw_row.items() if k in rename}
            raw_lines += 1
            date_s = row.get("DATE", "")
            time_s = row.get("TIME") if has_time else None
            parsed = _parse_ts(date_s or "", time_s)
            if parsed is None:
                errors.append(f"line {line_no}: unparseable timestamp DATE={date_s!r} TIME={time_s!r}")
                continue
            ts, ts_iso = parsed
            candle = {"ts": ts, "ts_iso": ts_iso,
                      "open": _num(row.get("OPEN")), "high": _num(row.get("HIGH")),
                      "low": _num(row.get("LOW")), "close": _num(row.get("CLOSE")),
                      "tick_volume": _int(row.get("TICKVOL", "0") or "0"),
                      "volume": _num(row.get("VOL", "0") or "0"),
                      "spread": _int(row.get("SPREAD", "0") or "0")}
            err = _store.validate_candle(candle)
            if err is not None:
                errors.append(f"line {line_no}: {err}")
                continue
            candles.append(candle)

    # de-duplicate / conflict detection on timestamps (never silently discard)
    candles.sort(key=lambda r: r["ts"])
    inversions = 0
    merged: list[dict] = []
    duplicates_collapsed = 0
    conflict_groups = 0
    for group in (list(g) for _, g in _groupby(candles, key=lambda r: r["ts"])):
        if len(group) == 1:
            merged.append(group[0])
            continue
        first = group[0]
        same = all(_store.candle_equal(first, g) for g in group[1:])
        if same:
            duplicates_collapsed += len(group) - 1
            merged.append(first)
        else:
            conflict_groups += 1
            sample = "; ".join(g["ts_iso"] for g in group[:3])
            errors.append(f"conflicting candles for the same timestamp ({sample}) - "
                          f"broker export is ambiguous, refusing to choose silently")

    if len(errors) > MAX_ERRORS_KEEP:
        errors = errors[:MAX_ERRORS_KEEP] + [f"... and {len(errors) - MAX_ERRORS_KEEP} more error(s) (truncated)"]

    return {"rows": merged, "errors": errors, "notes": notes,
            "total_data_rows": raw_lines, "duplicates_collapsed": duplicates_collapsed,
            "inversions_sorted": inversions, "gaps": 0,
            "largest_gap_seconds": None}


def _groupby(seq, key):
    from itertools import groupby
    for k, g in groupby(seq, key=key):
        yield k, list(g)


def canonical_fingerprint(rows: list[dict]) -> str:
    return _store.canonical_fingerprint(rows)


def _connect(db_path: Path) -> sqlite3.Connection:
    return _store.connect_store(db_path)


def _rebuild_manifest(conn: sqlite3.Connection, db_path: Path) -> None:
    _store.rebuild_manifest(conn, db_path)


def import_file(path: Path, *, instrument: str, timeframe: str,
                db_path: Path,
                force: bool, dry_run: bool) -> dict:
    timeframe = TF_ALIASES.get(timeframe, timeframe)
    step = TIMEFRAME_STEPS[timeframe]
    source_sha = sha256_file(path)
    source_bytes = path.stat().st_size
    dataset_id = hashlib.sha256(f"{instrument}|{timeframe}|{source_sha}".encode()).hexdigest()[:16]

    parsed = parse_rows(path)
    # gap stats with the known timeframe step (reported, never filled)
    gaps, largest_gap = _store.gap_stats(parsed["rows"], step)
    parsed["gaps"] = gaps
    parsed["largest_gap_seconds"] = largest_gap

    errors = list(parsed["errors"])
    notes = list(parsed["notes"])
    rows = parsed["rows"]
    if parsed["total_data_rows"] >= 100_000:
        notes.append("source hit the MT 100k-row export cap; older history may be missing - "
                     "import additional (newer) exports with --force to roll forward")
    if rows and all(r["volume"] == 0 for r in rows):
        notes.append("VOL column is all-zero as exported by the broker; tick volume is stored "
                     "separately in tick_volume")

    ok = not errors
    fingerprint = canonical_fingerprint(rows) if rows else hashlib.sha256(b"").hexdigest()
    report = {
        "errors": errors,
        "notes": notes,
        "total_data_rows": parsed["total_data_rows"],
        "duplicates_collapsed": parsed["duplicates_collapsed"],
        "gaps_found": gaps,
        "largest_gap_seconds": largest_gap,
    }
    first_ts = rows[0]["ts_iso"] if rows else None
    last_ts = rows[-1]["ts_iso"] if rows else None

    result = {
        "source_file": str(path), "instrument": instrument, "timeframe": timeframe,
        "dataset_id": dataset_id, "source_sha256": source_sha, "source_bytes": source_bytes,
        "row_count": len(rows), "status": "REJECTED" if not ok else "AVAILABLE",
        "validation_result": "FAIL" if not ok else "PASS",
        "first_ts": first_ts, "last_ts": last_ts,
        "content_fingerprint": fingerprint,
        "duplicates_collapsed": parsed["duplicates_collapsed"],
        "gaps_found": gaps, "largest_gap_seconds": largest_gap,
        "validation_report": report,
    }
    if dry_run:
        result["action"] = "DRY-RUN (nothing written)"
        return result

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect(db_path)
    try:
        existing = conn.execute("SELECT status FROM datasets WHERE dataset_id = ?", (dataset_id,)).fetchone()
        if existing and existing[0] == "AVAILABLE" and not force:
            result["action"] = "SKIPPED (identical source already AVAILABLE; use --force to re-import)"
            result["status"] = "AVAILABLE"
            result["validation_result"] = "PASS"
            return result
        with conn:  # one transaction - all-or-nothing
            if existing:
                conn.execute("DELETE FROM candles WHERE dataset_id = ?", (dataset_id,))
                conn.execute("DELETE FROM datasets WHERE dataset_id = ?", (dataset_id,))
            if ok:
                prior_owner = dict(conn.execute(
                    "SELECT ts, dataset_id FROM candles WHERE instrument = ? AND timeframe = ?",
                    (instrument, timeframe)).fetchall())
                overwritten = sum(1 for r in rows
                                  if r["ts"] in prior_owner and prior_owner[r["ts"]] != dataset_id)
                for r in rows:
                    conn.execute(
                        "INSERT INTO candles (dataset_id, instrument, timeframe, ts, ts_iso,"
                        " open, high, low, close, tick_volume, volume, spread)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
                        " ON CONFLICT(instrument, timeframe, ts) DO UPDATE SET"
                        "   dataset_id=excluded.dataset_id, ts_iso=excluded.ts_iso,"
                        "   open=excluded.open, high=excluded.high, low=excluded.low,"
                        "   close=excluded.close, tick_volume=excluded.tick_volume,"
                        "   volume=excluded.volume, spread=excluded.spread"
                        " WHERE candles.dataset_id != excluded.dataset_id",
                        (dataset_id, instrument, timeframe, r["ts"], r["ts_iso"],
                         r["open"], r["high"], r["low"], r["close"],
                         r["tick_volume"], r["volume"], r["spread"]))
                report["rows_overwritten_other_source"] = overwritten
            conn.execute(
                "INSERT INTO datasets (dataset_id, instrument, timeframe, source_file, source_sha256,"
                " source_bytes, row_count, duplicates_collapsed, gaps_found, largest_gap_seconds,"
                " first_ts, last_ts, content_fingerprint, location, imported_at,"
                " validation_result, validation_report, status)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (dataset_id, instrument, timeframe, str(path), source_sha, source_bytes,
                 len(rows) if ok else 0, parsed["duplicates_collapsed"], gaps, largest_gap,
                 first_ts, last_ts, fingerprint, str(db_path), _iso_now(),
                 result["validation_result"], json.dumps(report), result["status"]))
        _rebuild_manifest(conn, db_path)
        result["action"] = "IMPORTED" if ok else "REJECTED (nothing inserted; report retained)"
        return result
    finally:
        conn.close()


def _resolve(path: Path, instrument_flag: str | None, tf_flag: str | None) -> tuple[str, str]:
    m = FILENAME_RE.match(path.name)
    filename_instrument = m.group("instrument").upper() if m else None
    filename_tf = TF_ALIASES.get(m.group("tf")) if m else None

    instrument = (instrument_flag or filename_instrument or "").strip().upper()
    timeframe = TF_ALIASES.get((tf_flag or filename_tf or "").strip(), (tf_flag or filename_tf or "").strip().upper())
    if not instrument:
        raise SystemExit(f"cannot determine instrument for {path.name}: pass --instrument XAUUSD etc.")
    if instrument_flag and filename_instrument and instrument != filename_instrument:
        raise SystemExit(f"instrument mismatch for {path.name}: --instrument {instrument} but filename says {filename_instrument} - refusing to guess")
    if tf_flag and filename_tf and timeframe != filename_tf:
        raise SystemExit(f"timeframe mismatch for {path.name}: --timeframe {timeframe} but filename says {filename_tf} - refusing to guess")
    if not timeframe or timeframe not in TIMEFRAME_STEPS:
        raise SystemExit(f"cannot determine timeframe for {path.name}: pass --timeframe H1 etc.")
    return instrument, timeframe


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Import MT History-Center CSV exports into the Hero1 market-data SQLite store.")
    ap.add_argument("files", nargs="+", help="CSV file(s) to import")
    ap.add_argument("--data-root", required=True, help="Hero1 DATA root (explicit - never defaulted)")
    ap.add_argument("--instrument", default=None, help="override instrument (must match filename if both present)")
    ap.add_argument("--timeframe", default=None, help="override timeframe: M1 M5 M15 M30 H1 H4 D1")
    ap.add_argument("--force", action="store_true", help="re-import even if the identical source is already AVAILABLE")
    ap.add_argument("--dry-run", action="store_true", help="validate and report only; write nothing")
    ap.add_argument("--json-report", default=None, help="also write the full JSON report to this path")
    args = ap.parse_args(argv)

    data_root = Path(args.data_root).expanduser().resolve()
    if not data_root.is_absolute():
        raise SystemExit("--data-root must be an absolute path")
    db_path = data_root / "MARKET_DATA" / "market_data.db"

    results = []
    for name in args.files:
        path = Path(name).expanduser()
        if not path.is_file():
            print(f"ERROR: file not found: {path}", file=sys.stderr)
            return 2
        instrument, timeframe = _resolve(path, args.instrument, args.timeframe)
        res = import_file(path, instrument=instrument, timeframe=timeframe,
                          db_path=db_path,
                          force=args.force, dry_run=args.dry_run)
        results.append(res)
        head = f"{res['instrument']} {res['timeframe']}: {res['status']} ({res['validation_result']})"
        print(f"== {head}  [{res['action']}]")
        print(f"   rows {res['row_count']}  range {res['first_ts']} .. {res['last_ts']}")
        print(f"   fingerprint {res['content_fingerprint'][:16]}…  source sha256 {res['source_sha256'][:16]}…")
        print(f"   duplicates collapsed {res['duplicates_collapsed']}  gaps {res['gaps_found']}"
              + (f"  largest gap {res['largest_gap_seconds']}s" if res['largest_gap_seconds'] else ""))
        for note in res["validation_report"]["notes"]:
            print(f"   note: {note}")
        for err in res["validation_report"]["errors"]:
            print(f"   ERROR: {err}")
        print()

    summary = {
        "generated_at": _iso_now(), "data_root": str(data_root), "db": str(db_path),
        "dry_run": args.dry_run,
        "files": results,
        "totals": {
            "pass": sum(1 for r in results if r["validation_result"] == "PASS" and r["action"] != "SKIPPED (identical source already AVAILABLE; use --force to re-import)"),
            "skipped": sum(1 for r in results if r["action"].startswith("SKIPPED")),
            "rejected": sum(1 for r in results if r["validation_result"] == "FAIL"),
            "candles": sum(r["row_count"] for r in results),
        },
    }
    if args.json_report:
        Path(args.json_report).write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    t = summary["totals"]
    print(f"TOTAL: {t['pass']} imported, {t['skipped']} skipped, {t['rejected']} rejected, {t['candles']} candles")
    print(f"store: {db_path}")
    return 1 if t["rejected"] else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
