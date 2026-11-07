"""Durable fetch-conflict audit + controlled resolution (ISSUE-054, mocked).

Verifies:
  * conflicts found by a fetch are persisted with stored values, incoming
    values, provenances (CSV source file vs MT5 server) and field diffs;
  * the stored candle is preserved and the conflict is UNRESOLVED by default;
  * a repeat fetch is idempotent for candles (no duplicated rows; the same
    conflicts are re-detected honestly rather than silently absorbed);
  * resolution KEPT_STORED changes no candle; ACCEPTED_INCOMING replaces
    exactly one candle, keeps the pre-resolution values as the audit-row
    backup, and never changes the total candle count;
  * double resolution is refused.
"""
from __future__ import annotations

import sqlite3

import pytest

from app import market_data_service as svc
from hero1_market_data import store as md
from hero1_mt5_adapter import connection as mt5_connection

STEP = 3600
LATEST = 1601503200


def _candle(ts, close=1901.0, spread=12):
    return {"ts": ts, "ts_iso": md.ts_iso_of(ts), "open": 1900.0, "high": 1902.0,
            "low": 1899.0, "close": close, "tick_volume": 100, "volume": 0.0,
            "spread": spread}


def _rate(c):
    return {"time": c["ts"], "open": c["open"], "high": c["high"], "low": c["low"],
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
                                    "account": {"server": "ICMarketsSC-Demo"}, "terminal": {}})
    return db


def _fetch(monkeypatch, rates, operation_id="FETCH-AUDIT-1"):
    monkeypatch.setattr(mt5_connection, "mt5_module", lambda: _FakeMT5(rates))
    return svc.fetch("XAUUSD", ["H1"], operation_id=operation_id)


RATES = [
    _rate(_candle(LATEST - STEP, close=1901.5)),   # seam conflict (revised close)
    _rate(_candle(LATEST)),                        # exact duplicate
    _rate(_candle(LATEST + STEP)),                 # new
]


def test_conflict_is_persisted_with_provenance(seeded, monkeypatch):
    report = _fetch(monkeypatch, RATES)
    (r,) = report["results"]
    assert r["conflicts"] == 1 and r["appended"] == 1

    out = svc.list_conflicts("XAUUSD")
    assert out["total"] == 1
    (c,) = out["conflicts"]
    assert c["operation_id"] == "FETCH-AUDIT-1"
    assert c["resolution"] == "UNRESOLVED"
    assert c["stored"]["close"] == 1901.0 and c["incoming"]["close"] == 1901.5
    assert c["differing_fields"]["close"] == {"stored": 1901.0, "incoming": 1901.5}
    assert "XAUUSD_H1.csv" in c["stored_source"]
    assert "ICMarketsSC-Demo" in c["incoming_source"]

    conn = sqlite3.connect(seeded)
    assert conn.execute("SELECT close FROM candles WHERE ts=?", (LATEST - STEP,)).fetchone()[0] == 1901.0
    assert conn.execute("SELECT COUNT(*) FROM candles").fetchone()[0] == 4
    conn.close()


def test_repeat_fetch_is_idempotent(seeded, monkeypatch):
    _fetch(monkeypatch, RATES)
    second = _fetch(monkeypatch, RATES, operation_id="FETCH-AUDIT-2")
    (r,) = second["results"]
    assert r["appended"] == 0 and r["conflicts"] == 1  # re-detected, not absorbed
    conn = sqlite3.connect(seeded)
    assert conn.execute("SELECT COUNT(*) FROM candles").fetchone()[0] == 4
    assert conn.execute("SELECT COUNT(*) FROM (SELECT 1 FROM candles GROUP BY instrument, timeframe, ts HAVING COUNT(*) > 1)").fetchone()[0] == 0
    conn.close()


def test_resolution_kept_stored_and_accepted_incoming(seeded, monkeypatch):
    _fetch(monkeypatch, RATES)
    (c,) = svc.list_conflicts("XAUUSD")["conflicts"]

    kept = svc.resolve_conflict("XAUUSD", c["id"], "KEPT_STORED", note="verified CSV is authoritative")
    assert kept["conflict"]["resolution"] == "KEPT_STORED"
    assert kept["candle_total_before"] == kept["candle_total_after"] == 4
    conn = sqlite3.connect(seeded)
    assert conn.execute("SELECT close FROM candles WHERE ts=?", (LATEST - STEP,)).fetchone()[0] == 1901.0
    conn.close()
    with pytest.raises(ValueError):
        svc.resolve_conflict("XAUUSD", c["id"], "KEPT_STORED")

    # a fresh conflict (same seam, next operation) resolved the other way
    _fetch(monkeypatch, RATES, operation_id="FETCH-AUDIT-3")
    unresolved = svc.list_conflicts("XAUUSD", resolution="UNRESOLVED")["conflicts"]
    (c2,) = unresolved
    accepted = svc.resolve_conflict("XAUUSD", c2["id"], "ACCEPTED_INCOMING",
                                    note="terminal history proven correct")
    assert accepted["before"]["close"] == 1901.0   # backup retained on audit row
    assert accepted["after"]["close"] == 1901.5
    assert accepted["candle_total_before"] == accepted["candle_total_after"] == 4
    conn = sqlite3.connect(seeded)
    assert conn.execute("SELECT close FROM candles WHERE ts=?", (LATEST - STEP,)).fetchone()[0] == 1901.5
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()
