"""ISSUE-059: the explicit MT5 time-shift repair script.

Builds a temporary database (pytest tmp_path - outside the repository)
containing PRE-FIX damage: MT5-fetched candles labelled with broker
server epochs next to correct-UTC CSV candles, then proves the repair
  * shifts only mt5-fetch candles, by the date-dependent offset;
  * drops shifted candles that duplicate stored ones exactly;
  * never overwrites a stored candle on a conflicting collision;
  * leaves the fetch_conflicts audit table untouched;
  * is a no-write dry-run by default.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from hero1_market_data import store as md
from hero1_mt5_adapter import server_time as st

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "repair_mt5_timeshift", REPO / "scripts" / "repair_mt5_timeshift.py")
repair = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(repair)


def _utc(y, mo, d, h=0):
    return int(datetime(y, mo, d, h, tzinfo=timezone.utc).timestamp())


def _candle(ts, close, dataset_id):
    return {"dataset_id": dataset_id, "ts": ts, "ts_iso": md.ts_iso_of(ts),
            "open": close - 1.0, "high": close + 1.0, "low": close - 2.0,
            "close": close, "tick_volume": 100, "volume": 0.0, "spread": 12}


def _build(db: Path) -> None:
    conn = md.connect_store(db)
    with conn:
        # CSV baseline in TRUE UTC: 2026-10-16 07:00..09:00 (closes 1900,1901,1902)
        md.merge_candles(conn, dataset_id="csvbase", instrument="XAUUSD", timeframe="H1",
                         rows=[dict(_candle(_utc(2026, 10, 16, 7 + i), 1900.0 + i, "csvbase"),
                                    ) | {} for i in range(3)], policy="import")
        # PRE-FIX MT5 rows: server-epoch labels for the same instants plus
        # one new bar; stored verbatim by the old code (labels 10,11,12,13)
        rows = []
        for i, close in enumerate((1900.0, 1901.0, 1902.0)):
            srv = st.utc_to_server_epoch(_utc(2026, 10, 16, 7 + i))
            rows.append(_candle(srv, close, "fetchds"))
        rows.append(_candle(st.utc_to_server_epoch(_utc(2026, 10, 16, 10)), 1905.0, "fetchds"))
        md.merge_candles(conn, dataset_id="fetchds", instrument="XAUUSD", timeframe="H1",
                         rows=rows, policy="import")
        md.record_dataset(conn, dataset_id="csvbase", instrument="XAUUSD", timeframe="H1",
                          source_file="XAUUSD_H1.csv", source_sha256="x", source_bytes=1,
                          row_count=3, duplicates_collapsed=0, gaps_found=0,
                          largest_gap_seconds=None, first_ts="2026-10-16 07:00:00",
                          last_ts="2026-10-16 09:00:00", content_fingerprint="a",
                          location=str(db), imported_at=md.iso_now(),
                          validation_result="PASS", validation_report={"kind": "csv-import"},
                          status="AVAILABLE")
        md.record_dataset(conn, dataset_id="fetchds", instrument="XAUUSD", timeframe="H1",
                          source_file="mt5-fetch:ICMarketsSC-Demo", source_sha256="y",
                          source_bytes=1, row_count=4, duplicates_collapsed=0, gaps_found=0,
                          largest_gap_seconds=None, first_ts="2026-10-16 10:00:00",
                          last_ts="2026-10-16 13:00:00", content_fingerprint="b",
                          location=str(db), imported_at=md.iso_now(),
                          validation_result="PASS",
                          validation_report={"kind": "mt5-fetch"}, status="AVAILABLE")
        conn.execute(
            "INSERT INTO fetch_conflicts (operation_id, instrument, timeframe, ts, ts_iso,"
            " stored_dataset_id, stored_source, incoming_source, stored, incoming,"
            " differing_fields, classification, resolution, created_at)"
            " VALUES ('OP1','XAUUSD','H1',?,'2026-10-16 10:00:00','csvbase','CSV','MT5','{}','{}','{}',"
            " 'CONFLICT_STORED_PRESERVED','UNRESOLVED','2026-11-07T00:00:00Z')",
            (_utc(2026, 10, 16, 10),))
    conn.close()


@pytest.fixture()
def damaged(tmp_path):
    db = tmp_path / "market_data.db"
    _build(db)
    return db


def test_plan_recognises_shift_and_duplicates(damaged):
    conn = sqlite3.connect(damaged)
    p = repair.plan(conn)
    conn.close()
    assert p["fetch_datasets"] == 1 and p["shifted"] == 4
    assert p["to_move"] == 1 and p["duplicates_dropped"] == 3
    assert p["collisions_recorded_as_conflicts"] == 0


def test_apply_repairs_labels_and_preserves_audit(damaged, tmp_path, monkeypatch, capsys):
    before = sqlite3.connect(damaged).execute("SELECT COUNT(*) FROM candles").fetchone()[0]
    backups = []
    import shutil as _sh
    real_copy = _sh.copy2
    monkeypatch.setattr(repair.shutil, "copy2",
                        lambda a, b: (backups.append(str(b)), real_copy(a, b))[1])
    monkeypatch.setattr("sys.argv", ["repair", "--db", str(damaged), "--apply"])
    assert repair.main() == 0
    assert len(backups) == 1 and Path(backups[0]).is_file()  # backup created first

    conn = sqlite3.connect(damaged)
    rows = conn.execute("SELECT ts_iso, close, dataset_id FROM candles ORDER BY ts").fetchall()
    assert rows == [("2026-10-16 07:00:00", 1900.0, "csvbase"),
                    ("2026-10-16 08:00:00", 1901.0, "csvbase"),
                    ("2026-10-16 09:00:00", 1902.0, "csvbase"),
                    ("2026-10-16 10:00:00", 1905.0, "fetchds")], rows
    # audit table untouched, candle total only lost the 3 exact duplicates
    assert conn.execute("SELECT COUNT(*) FROM fetch_conflicts").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM candles").fetchone()[0] == before - 3
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    ds = conn.execute("SELECT row_count, first_ts, last_ts FROM datasets WHERE dataset_id='fetchds'").fetchone()
    assert ds == (1, "2026-10-16 10:00:00", "2026-10-16 10:00:00"), ds
    conn.close()


def test_dry_run_writes_nothing(damaged, monkeypatch):
    before = damaged.read_bytes()
    monkeypatch.setattr("sys.argv", ["repair", "--db", str(damaged)])
    assert repair.main() == 0
    assert damaged.read_bytes() == before


def test_repair_on_healthy_db_is_noop(tmp_path, monkeypatch):
    db = tmp_path / "healthy.db"
    conn = md.connect_store(db)
    with conn:
        md.merge_candles(conn, dataset_id="csvbase", instrument="XAUUSD", timeframe="H1",
                         rows=[_candle(_utc(2026, 10, 16, 7), 1900.0, "csvbase")], policy="import")
    conn.close()
    monkeypatch.setattr("sys.argv", ["repair", "--db", str(db), "--apply"])
    assert repair.main() == 0
