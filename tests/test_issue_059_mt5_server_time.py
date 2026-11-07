"""ISSUE-059 regression: MT5 server-epoch -> UTC conversion at ingestion.

Ground truth (from the owner's actual databases, compared in
/tmp/hero1_market_audit): all 15 "CSV vs MT5" seam conflicts were the
SAME candles - the incoming server-epoch candle labelled 12:29 was
byte-identical to the stored (true-UTC) 09:29 candle. The fetch path
stored server epochs verbatim into a UTC store. These tests lock the
fix: what the terminal returns is converted exactly once, so identical
history is recognised as duplicates (never conflicts), genuine value
differences at the same true instant still conflict, and DeepTesting
readiness refuses periods containing unresolved conflicts.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from app import market_data_service as svc
from hero1_market_data import store as md
from hero1_mt5_adapter import server_time as st
from hero1_mt5_adapter import connection as mt5_connection

STEP = 3600


def _utc(y, mo, d, h=0, mi=0):
    return int(datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp())


# stored (true UTC) H1 candles 2026-10-16 07:00-09:00, distinctive closes
SEAM_UTC = [_utc(2026, 10, 16, 7), _utc(2026, 10, 16, 8), _utc(2026, 10, 16, 9)]


def _candle(ts, close=1901.0):
    return {"ts": ts, "ts_iso": md.ts_iso_of(ts), "open": close - 1.0,
            "high": close + 1.0, "low": close - 2.0, "close": close,
            "tick_volume": 100, "volume": 0.0, "spread": 12}


def _server_rate(utc_ts, close=1901.0):
    """What a real terminal returns: `time` is the SERVER epoch."""
    c = _candle(utc_ts, close)
    return {"time": st.utc_to_server_epoch(utc_ts), "open": c["open"],
            "high": c["high"], "low": c["low"], "close": c["close"],
            "tick_volume": c["tick_volume"], "volume_real": c["volume"],
            "spread": c["spread"]}


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
    with conn:
        md.merge_candles(conn, dataset_id="baseline", instrument="XAUUSD",
                         timeframe="H1",
                         rows=[_candle(t, close=1900.0 + i) for i, t in enumerate(SEAM_UTC)],
                         policy="import")
    conn.close()
    monkeypatch.setattr(svc, "store_db_path", lambda: db)
    monkeypatch.setattr(mt5_connection, "connect",
                        lambda _p: {"state": mt5_connection.STATE_ACCOUNT_LOADED,
                                    "account": {"server": "ICMarketsSC-Demo"}, "terminal": {}})
    return db, monkeypatch


def _fetch(monkeypatch, rates, now, op="FETCH-059"):
    monkeypatch.setattr(svc, "_utc_now_epoch", lambda: now)
    monkeypatch.setattr(mt5_connection, "mt5_module", lambda: _FakeMT5(rates))
    return svc.fetch("XAUUSD", ["H1"], operation_id=op)


def test_conversion_offsets_summer_and_winter():
    # IC Markets: GMT+3 during US daylight time, GMT+2 in standard time
    assert st.server_to_utc_epoch(st.utc_to_server_epoch(_utc(2026, 10, 16, 9))) == _utc(2026, 10, 16, 9)
    assert st.utc_to_server_epoch(_utc(2026, 10, 16, 9)) - _utc(2026, 10, 16, 9) == 3 * 3600
    assert st.utc_to_server_epoch(_utc(2026, 1, 16, 9)) - _utc(2026, 1, 16, 9) == 2 * 3600
    # round-trip holds for an hourly sweep across both 2026 transitions,
    # except the date-granular boundary the module documents: the exact
    # intraday switch instant is not knowable (broker flips over the
    # weekend close), so at most a couple of hours around the transition
    # Sundays - while the market is closed - may be ambiguous
    t = _utc(2026, 1, 1)
    ambiguous = []
    while t < _utc(2027, 1, 1):
        if st.server_to_utc_epoch(st.utc_to_server_epoch(t)) != t:
            ambiguous.append(t)
        t += 3600
    assert len(ambiguous) <= 2, ambiguous
    for t in ambiguous:
        dt = datetime.fromtimestamp(t, tz=timezone.utc)
        assert dt.weekday() in (5, 6)  # weekend: market closed


def test_the_15_conflict_scenario_now_deduplicates(seeded):
    db, monkeypatch = seeded
    # the terminal re-sends the seam (identical values, server-epoch times)
    # plus one genuinely new candle - under the old code this produced
    # exactly 3 conflicts; now every overlap is recognised as a duplicate
    rates = [_server_rate(t, close=1900.0 + i) for i, t in enumerate(SEAM_UTC)]
    rates.append(_server_rate(_utc(2026, 10, 16, 10), close=1905.0))
    report = _fetch(monkeypatch, rates, now=_utc(2026, 10, 16, 12, 30))
    (r,) = report["results"]
    assert r["conflicts"] == 0, r
    assert r["duplicates_skipped"] == 3
    assert r["appended"] == 1 and r["status"] == "UPDATED"
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT ts_iso, close FROM candles ORDER BY ts").fetchall()
    conn.close()
    assert rows[-1] == ("2026-10-16 10:00:00", 1905.0)  # TRUE UTC, not 13:00
    assert len(rows) == 4
    # idempotent: the same fetch again appends nothing, conflicts nothing
    report2 = _fetch(monkeypatch, rates, now=_utc(2026, 10, 16, 12, 30), op="FETCH-059-B")
    (r2,) = report2["results"]
    assert r2["appended"] == 0 and r2["conflicts"] == 0
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM candles").fetchone()[0] == 4


def test_genuine_difference_still_conflicts_and_preserves(seeded):
    db, monkeypatch = seeded
    # same true instant (08:00 UTC), materially different close -> conflict
    rates = [_server_rate(SEAM_UTC[1], close=1950.0)]
    report = _fetch(monkeypatch, rates, now=_utc(2026, 10, 16, 12, 30))
    (r,) = report["results"]
    assert r["conflicts"] == 1 and r["appended"] == 0
    out = svc.list_conflicts("XAUUSD")
    assert out["total"] == 1
    assert out["conflicts"][0]["ts_iso"] == "2026-10-16 08:00:00"  # UTC label
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT close FROM candles WHERE ts=?",
                        (SEAM_UTC[1],)).fetchone()[0] == 1901.0  # stored preserved
    conn.close()


def test_readiness_refuses_period_with_unresolved_conflict(seeded):
    db, monkeypatch = seeded
    rates = [_server_rate(SEAM_UTC[1], close=1950.0)]
    _fetch(monkeypatch, rates, now=_utc(2026, 10, 16, 12, 30))
    inside = svc.data_quality("XAUUSD", "H1",
                              from_epoch=_utc(2026, 10, 16, 0), to_epoch=_utc(2026, 10, 16, 23))
    assert inside["readiness"] == "NOT_READY", inside
    assert inside["conflicts"]["unresolved_in_range"] == 1
    # a period that does not include the conflicted candle is unaffected by it
    clear = svc.data_quality("XAUUSD", "H1",
                             from_epoch=_utc(2026, 10, 16, 9), to_epoch=_utc(2026, 10, 16, 9))
    assert clear["conflicts"]["unresolved_in_range"] == 0
    assert clear["readiness"] == "READY", clear
    # candles after the requested range are excluded (no future leakage),
    # and their existence is reported transparently
    assert clear["candles_after_range"] == 0
    upto9 = svc.data_quality("XAUUSD", "H1",
                             from_epoch=_utc(2026, 10, 16, 7), to_epoch=_utc(2026, 10, 16, 8))
    assert upto9["candles_after_range"] == 1  # the 09:00 candle exists but is out of range
    gap_free_inside = svc.data_quality("XAUUSD", "H1",
                                       from_epoch=_utc(2026, 10, 16, 9), to_epoch=_utc(2026, 10, 16, 9))
    assert gap_free_inside["candles_in_range"] == 1
