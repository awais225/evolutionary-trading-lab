"""MT5 fetch conflict isolation (ISSUE-050) — MOCKED terminal.

Verified behavior:
  * exact duplicates skip; overlaps differing only in spread/volume are
    representation differences (also skipped, counted separately);
  * a candle whose price differs is ISOLATED: the stored candle is
    preserved byte-for-byte, the conflict is recorded with field diffs,
    and genuinely new candles still append in the same transaction;
  * the timeframe reports PARTIAL (not FAILED, not UPDATED);
  * the fetch report carries the caller-supplied operation id.
Genuine terminal behavior remains a Windows acceptance item.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app import market_data_service as svc
from hero1_market_data import store as md
from hero1_mt5_adapter import connection as mt5_connection
from hero1_mt5_adapter import server_time as _server_time

STEP = 3600
LATEST = 1601503200  # 2020-09-30 22:00:00 UTC, H1-aligned


def _candle(ts: int, close: float = 1901.0, spread: int = 12) -> dict:
    return {"ts": ts, "ts_iso": md.ts_iso_of(ts), "open": 1900.0, "high": 1902.0,
            "low": 1899.0, "close": close, "tick_volume": 100, "volume": 0.0,
            "spread": spread}


def _rate(c: dict) -> dict:
    return {"time": _server_time.utc_to_server_epoch(c["ts"]), "open": c["open"], "high": c["high"], "low": c["low"],
            "close": c["close"], "tick_volume": c["tick_volume"],
            "volume_real": c["volume"], "spread": c["spread"]}


class _FakeMT5:
    TIMEFRAME_M1, TIMEFRAME_M5, TIMEFRAME_M15, TIMEFRAME_M30 = 1, 5, 15, 30
    TIMEFRAME_H1, TIMEFRAME_H4, TIMEFRAME_D1 = 101, 104, 105

    def __init__(self, rates):
        self._rates = rates

    def copy_rates_range(self, symbol, timeframe, from_dt, to_dt):
        return list(self._rates)

    def last_error(self):
        return (0, "ok")


@pytest.fixture()
def seeded(tmp_path, monkeypatch):
    db = tmp_path / "DATA" / "MARKET_DATA" / "market_data.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = md.connect_store(db)
    rows = [_candle(LATEST - 2 * STEP), _candle(LATEST - STEP), _candle(LATEST)]
    with conn:
        md.merge_candles(conn, dataset_id="baseline", instrument="XAUUSD",
                         timeframe="H1", rows=rows, policy="import")
        md.record_dataset(conn, dataset_id="baseline", instrument="XAUUSD", timeframe="H1",
                          source_file="DATA/MARKET_DATA/IMPORTS/XAUUSD/XAUUSD_H1.csv",
                          source_sha256="x", source_bytes=1, row_count=3,
                          duplicates_collapsed=0, gaps_found=0, largest_gap_seconds=None,
                          first_ts=rows[0]["ts_iso"], last_ts=rows[-1]["ts_iso"],
                          content_fingerprint="fp", location=str(db),
                          imported_at=md.iso_now(), validation_result="PASS",
                          validation_report={"kind": "csv-import"}, status="AVAILABLE")
    conn.close()
    monkeypatch.setattr(svc, "store_db_path", lambda: db)
    monkeypatch.setattr(svc, "_utc_now_epoch", lambda: LATEST + 3 * STEP)
    monkeypatch.setattr(mt5_connection, "connect",
                        lambda _p: {"state": mt5_connection.STATE_ACCOUNT_LOADED,
                                    "account": {"server": "FakeServer"}, "terminal": {}})
    return db


def _fetch(monkeypatch, rates, operation_id="FETCH-TEST-0001"):
    monkeypatch.setattr(mt5_connection, "mt5_module", lambda: _FakeMT5(rates))
    return svc.fetch("XAUUSD", ["H1"], operation_id=operation_id)


def test_conflicts_are_isolated_and_new_candles_append(seeded, monkeypatch):
    rates = [
        _rate(_candle(LATEST - 2 * STEP)),                    # exact duplicate
        _rate(_candle(LATEST - STEP, close=1901.5)),          # price conflict
        _rate(_candle(LATEST, spread=35)),                    # representation diff
        _rate(_candle(LATEST + STEP)),                        # new
        _rate(_candle(LATEST + 2 * STEP)),                    # new
    ]
    report = _fetch(monkeypatch, rates)
    assert report["operation_id"] == "FETCH-TEST-0001"
    assert report["status"] == "PARTIAL"
    (r,) = report["results"]
    assert r["status"] == "PARTIAL"
    assert r["appended"] == 2
    assert r["duplicates_skipped"] == 2
    assert r["representation_diffs"] == 1
    assert r["conflicts"] == 1
    (detail,) = r["conflict_details"]
    assert detail["ts"] == LATEST - STEP
    assert detail["differing_fields"]["close"] == {"stored": 1901.0, "incoming": 1901.5}

    conn = sqlite3.connect(seeded)
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    rows = dict(conn.execute("SELECT ts, close FROM candles WHERE timeframe='H1'"))
    assert len(rows) == 5
    assert rows[LATEST - STEP] == 1901.0  # stored conflict candle preserved
    assert rows[LATEST + 2 * STEP] == 1901.0
    # conflict audit trail recorded in the datasets registry
    reports = [row[0] for row in conn.execute(
        "SELECT validation_report FROM datasets WHERE source_file LIKE 'mt5-fetch%'")]
    assert reports and '"conflicts": 1' in reports[0]
    conn.close()


def test_clean_fetch_updates_without_conflicts(seeded, monkeypatch):
    rates = [_rate(_candle(LATEST)), _rate(_candle(LATEST + STEP))]
    report = _fetch(monkeypatch, rates)
    (r,) = report["results"]
    assert report["status"] == "COMPLETE"
    assert r["status"] == "UPDATED" and r["appended"] == 1 and r["conflicts"] == 0


def test_only_duplicates_is_up_to_date(seeded, monkeypatch):
    rates = [_rate(_candle(LATEST - STEP)), _rate(_candle(LATEST))]
    report = _fetch(monkeypatch, rates)
    (r,) = report["results"]
    assert r["status"] == "UP_TO_DATE" and r["appended"] == 0
    assert sqlite3.connect(seeded).execute("SELECT COUNT(*) FROM candles").fetchone()[0] == 3


def test_large_fetch_is_not_quadratic(seeded, monkeypatch):
    """ISSUE-053 regression: 6,000 incoming candles against a seeded store
    must complete in seconds. The old code rebuilt the stored-timestamp
    set with a full-table SELECT per incoming candle (O(n*m) - ~24 min
    for the real 21,337-candle M1 fetch) and never committed."""
    import time as _t
    rates = [_rate(_candle(LATEST + i * STEP)) for i in range(1, 6001)]
    monkeypatch.setattr(mt5_connection, "mt5_module", lambda: _FakeMT5(rates))
    monkeypatch.setattr(svc, "_utc_now_epoch", lambda: LATEST + 6002 * STEP)
    started = _t.perf_counter()
    report = svc.fetch("XAUUSD", ["H1"], operation_id="FETCH-TEST-LARGE")
    elapsed = _t.perf_counter() - started
    (r,) = report["results"]
    # the 6,000 synthetic hours cross two DST transitions; the documented
    # date-granular conversion may collapse at most one hour per transition
    # (market closed then), so the exact expectation is the count of
    # DISTINCT converted timestamps not already stored
    from hero1_mt5_adapter import server_time as _st
    distinct_new = {_st.server_to_utc_epoch(r_["time"]) for r_ in rates}
    assert r["status"] == "UPDATED" and r["appended"] == len(distinct_new), r
    assert elapsed < 10, f"large fetch took {elapsed:.1f}s - quadratic lookup is back"
    conn = sqlite3.connect(seeded)
    assert conn.execute("SELECT COUNT(*) FROM candles").fetchone()[0] == 3 + len(distinct_new)
    conn.close()
