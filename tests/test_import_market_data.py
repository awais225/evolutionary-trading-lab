"""Tests for scripts/import_market_data.py (MT History-Center CSV importer).

Binding guarantees under test: never invents candles (all-or-nothing),
never silently discards (exact duplicates collapse + counted; conflicting
duplicates FAIL), never mixes instruments/timeframes (filename/flag
mismatch refused), gaps reported never filled, AVAILABLE only post-PASS,
idempotent re-import, honest roll-forward overwrite accounting.

All writes go to pytest tmp_path - the real DATA root is never touched.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "import_market_data.py"
_spec = importlib.util.spec_from_file_location("import_market_data", _SCRIPT)
imp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(imp)

INTRADAY_HEADER = ["<DATE>", "<TIME>", "<OPEN>", "<HIGH>", "<LOW>", "<CLOSE>",
                   "<TICKVOL>", "<VOL>", "<SPREAD>"]
DAILY_HEADER = ["<DATE>", "<OPEN>", "<HIGH>", "<LOW>", "<CLOSE>",
                "<TICKVOL>", "<VOL>", "<SPREAD>"]


def _write(path: Path, header: list[str], rows: list[list]) -> Path:
    lines = ["\t".join(header)] + ["\t".join(str(v) for v in r) for r in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _h1_rows(n: int = 3, start_hour: int = 1, base: float = 1900.0) -> list[list]:
    return [["2020.10.01", f"{start_hour + i:02d}:00:00", base + i, base + 2 + i,
             base - 1 + i, base + 1 + i, 100 + i, 0, 12] for i in range(n)]


def _db(tmp_path: Path) -> Path:
    return tmp_path / "DATA" / "MARKET_DATA" / "market_data.db"


def _import(path: Path, tmp_path: Path, *, instrument="XAUUSD", timeframe="H1",
            force=False, dry_run=False) -> dict:
    return imp.import_file(path, instrument=instrument, timeframe=timeframe,
                           db_path=_db(tmp_path), force=force, dry_run=dry_run)


def _candle_count(tmp_path: Path) -> int:
    db = _db(tmp_path)
    if not db.exists():
        return 0
    return sqlite3.connect(str(db)).execute("SELECT COUNT(*) FROM candles").fetchone()[0]


# ---------------------------------------------------------------- parsing ---

def test_daily_without_time_column_parses(tmp_path):
    f = _write(tmp_path / "XAUUSD_Daily.csv", DAILY_HEADER,
               [["2020.10.01", 1885.26, 1911.97, 1884.69, 1904.96, 155252, 0, 2],
                ["2020.10.02", 1905.0, 1915.0, 1900.0, 1910.0, 120000, 0, 2]])
    parsed = imp.parse_rows(f)
    assert parsed["errors"] == []
    assert len(parsed["rows"]) == 2
    # CSV server wall 2020-10-01 00:00 (GMT+3, US DST) converted ONCE to UTC
    assert parsed["rows"][0]["ts_iso"] == "2020-09-30 21:00:00"
    assert parsed["rows"][0]["ts"] == 1601499600
    assert parsed["rows"][0]["tick_volume"] == 155252
    assert parsed["rows"][0]["volume"] == 0.0
    assert parsed["rows"][0]["spread"] == 2


def test_rows_sorted_ascending_regardless_of_file_order(tmp_path):
    rows = _h1_rows(3)
    f = _write(tmp_path / "XAUUSD_H1.csv", INTRADAY_HEADER, list(reversed(rows)))
    parsed = imp.parse_rows(f)
    assert parsed["errors"] == []
    ts = [r["ts"] for r in parsed["rows"]]
    assert ts == sorted(ts)


def test_gap_is_reported_never_filled(tmp_path):
    rows = _h1_rows(2) + _h1_rows(1, start_hour=5)  # 03:00 and 04:00 missing
    f = _write(tmp_path / "XAUUSD_H1.csv", INTRADAY_HEADER, rows)
    res = _import(f, tmp_path)
    assert res["validation_result"] == "PASS"
    assert res["gaps_found"] == 1
    assert res["largest_gap_seconds"] == 3 * 3600
    assert _candle_count(tmp_path) == 3  # the gap was NOT filled with invented candles


# -------------------------------------------------------------- validation ---

def test_high_below_low_rejected_all_or_nothing(tmp_path):
    rows = _h1_rows(2) + [["2020.10.01", "03:00:00", 1900.0, 1890.0, 1910.0, 1900.0, 5, 0, 12]]
    f = _write(tmp_path / "XAUUSD_H1.csv", INTRADAY_HEADER, rows)
    res = _import(f, tmp_path)
    assert res["status"] == "REJECTED"
    assert res["validation_result"] == "FAIL"
    assert any("HIGH" in e for e in res["validation_report"]["errors"])
    assert _candle_count(tmp_path) == 0  # nothing partially loaded
    # the rejected attempt is still recorded for provenance
    conn = sqlite3.connect(str(_db(tmp_path)))
    row = conn.execute("SELECT status, row_count FROM datasets WHERE dataset_id = ?",
                       (res["dataset_id"],)).fetchone()
    assert row == ("REJECTED", 0)


def test_missing_required_column_rejected(tmp_path):
    header = ["<DATE>", "<TIME>", "<OPEN>", "<HIGH>", "<LOW>", "<TICKVOL>"]
    f = _write(tmp_path / "XAUUSD_H1.csv", header,
               [["2020.10.01", "01:00:00", 1.0, 2.0, 0.5, 10]])
    res = _import(f, tmp_path)
    assert res["status"] == "REJECTED"
    assert any("CLOSE" in e for e in res["validation_report"]["errors"])
    assert _candle_count(tmp_path) == 0


def test_unparseable_timestamp_rejected(tmp_path):
    rows = _h1_rows(1) + [["not-a-date", "02:00:00", 1.0, 2.0, 0.5, 1.5, 10, 0, 12]]
    f = _write(tmp_path / "XAUUSD_H1.csv", INTRADAY_HEADER, rows)
    res = _import(f, tmp_path)
    assert res["status"] == "REJECTED"
    assert _candle_count(tmp_path) == 0


# -------------------------------------------------------------- duplicates ---

def test_exact_duplicates_collapse_and_are_counted(tmp_path):
    rows = _h1_rows(2)
    rows.append(list(rows[0]))  # exact copy of the first candle
    f = _write(tmp_path / "XAUUSD_H1.csv", INTRADAY_HEADER, rows)
    res = _import(f, tmp_path)
    assert res["validation_result"] == "PASS"
    assert res["duplicates_collapsed"] == 1
    assert res["row_count"] == 2
    assert _candle_count(tmp_path) == 2


def test_conflicting_duplicates_rejected_nothing_discarded_silently(tmp_path):
    rows = _h1_rows(2)
    conflict = list(rows[0])
    conflict[5] = 1900.5  # same timestamp, different (still valid) CLOSE
    rows.append(conflict)
    f = _write(tmp_path / "XAUUSD_H1.csv", INTRADAY_HEADER, rows)
    res = _import(f, tmp_path)
    assert res["status"] == "REJECTED"
    assert any("conflicting candles" in e for e in res["validation_report"]["errors"])
    assert _candle_count(tmp_path) == 0


# ------------------------------------------------------- identity / mixing ---

def test_filename_flag_mismatch_refused():
    with pytest.raises(SystemExit):
        imp._resolve(Path("XAUUSD_H1.csv"), None, "M5")
    with pytest.raises(SystemExit):
        imp._resolve(Path("XAUUSD_H1.csv"), "EURUSD", None)
    # matching flag is fine, Daily aliases to D1
    assert imp._resolve(Path("XAUUSD_Daily.csv"), None, "D1") == ("XAUUSD", "D1")
    # no filename signal and no flags -> honest refusal, never a guess
    with pytest.raises(SystemExit):
        imp._resolve(Path("export.csv"), None, None)
    assert imp._resolve(Path("export.csv"), "XAUUSD", "H1") == ("XAUUSD", "H1")


def test_timeframes_stay_in_separate_datasets(tmp_path):
    h1 = _write(tmp_path / "XAUUSD_H1.csv", INTRADAY_HEADER, _h1_rows(2))
    m5_header_rows = [["2020.10.01", "01:00:00", 1900.0, 1902.0, 1899.0, 1901.0, 10, 0, 12],
                      ["2020.10.01", "01:05:00", 1901.0, 1903.0, 1900.0, 1902.0, 10, 0, 12]]
    m5 = _write(tmp_path / "XAUUSD_M5.csv", INTRADAY_HEADER, m5_header_rows)
    _import(h1, tmp_path, timeframe="H1")
    _import(m5, tmp_path, timeframe="M5")
    conn = sqlite3.connect(str(_db(tmp_path)))
    counts = dict(conn.execute("SELECT timeframe, COUNT(*) FROM candles GROUP BY timeframe"))
    assert counts == {"H1": 2, "M5": 2}


# ------------------------------------------------------------ idempotency ---

def test_dry_run_writes_nothing(tmp_path):
    f = _write(tmp_path / "XAUUSD_H1.csv", INTRADAY_HEADER, _h1_rows(2))
    res = _import(f, tmp_path, dry_run=True)
    assert res["validation_result"] == "PASS"
    assert res["action"].startswith("DRY-RUN")
    assert not (tmp_path / "DATA").exists()


def test_reimport_skips_then_force_reimports(tmp_path):
    f = _write(tmp_path / "XAUUSD_H1.csv", INTRADAY_HEADER, _h1_rows(3))
    first = _import(f, tmp_path)
    assert first["action"] == "IMPORTED"
    second = _import(f, tmp_path)
    assert second["action"].startswith("SKIPPED")
    assert _candle_count(tmp_path) == 3
    third = _import(f, tmp_path, force=True)
    assert third["action"] == "IMPORTED"
    assert _candle_count(tmp_path) == 3  # re-import replaces, never duplicates


def test_rollforward_overwrite_accounting_is_honest(tmp_path):
    v1 = _write(tmp_path / "v1.csv", INTRADAY_HEADER, _h1_rows(3))
    _import(v1, tmp_path)
    # broker re-export: hours 02:00/03:00 revised, 04:00 new
    v2_rows = _h1_rows(2, start_hour=2, base=2000.0) + _h1_rows(1, start_hour=4, base=2000.0)
    v2 = _write(tmp_path / "v2.csv", INTRADAY_HEADER, v2_rows)
    res = _import(v2, tmp_path)
    assert res["action"] == "IMPORTED"
    assert res["validation_report"]["rows_overwritten_other_source"] == 2
    conn = sqlite3.connect(str(_db(tmp_path)))
    assert conn.execute("SELECT COUNT(*) FROM candles").fetchone()[0] == 4
    close_0200 = conn.execute(
        "SELECT close FROM candles WHERE timeframe='H1' AND ts_iso='2020-09-30 23:00:00'"
    ).fetchone()[0]
    assert close_0200 == 2001.0  # the revised value won, explicitly
    # both source datasets remain on record - provenance is never erased
    assert conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0] == 2


# ---------------------------------------------------------------- manifest ---

def test_manifest_records_full_provenance(tmp_path):
    f = _write(tmp_path / "XAUUSD_H1.csv", INTRADAY_HEADER, _h1_rows(2))
    res = _import(f, tmp_path)
    manifest = json.loads((_db(tmp_path).parent / "import_manifest.json").read_text())
    assert manifest["schema_version"] == 1
    (entry,) = manifest["datasets"]
    assert entry["dataset_id"] == res["dataset_id"]
    assert entry["instrument"] == "XAUUSD"
    assert entry["timeframe"] == "H1"
    assert entry["source_file"] == str(f)
    assert len(entry["source_sha256"]) == 64
    assert entry["row_count"] == 2
    assert entry["first_ts"] == "2020-09-30 22:00:00"  # 01:00 server (GMT+3) in UTC
    assert entry["last_ts"] == "2020-09-30 23:00:00"
    assert entry["fingerprint"] == res["content_fingerprint"]
    assert entry["validation_result"] == "PASS"
    assert entry["status"] == "AVAILABLE"  # AVAILABLE only after PASS


# -------------------------------------------------------------- CLI surface ---

def test_main_exit_codes(tmp_path):
    good = _write(tmp_path / "XAUUSD_H1.csv", INTRADAY_HEADER, _h1_rows(2))
    bad_rows = [["2020.10.01", "01:00:00", 1900.0, 1890.0, 1910.0, 1900.0, 5, 0, 12]]
    bad = _write(tmp_path / "XAUUSD_M5.csv", INTRADAY_HEADER, bad_rows)
    root = str(tmp_path / "DATA")
    assert imp.main(["--data-root", root, str(good)]) == 0
    assert imp.main(["--data-root", root, str(bad)]) == 1
    assert imp.main(["--data-root", root, str(tmp_path / "missing.csv")]) == 2
