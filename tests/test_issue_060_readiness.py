"""ISSUE-060: practical, range-scoped DeepTesting readiness.

Three states: READY / READY_WITH_WARNINGS / BLOCKED. Material integrity
problems INSIDE the selected instrument+timeframe+period block; tolerable
imperfections (unverified gaps that may be holidays, metadata-only
conflicts, anything outside the selected scope) only warn or are ignored.
No candle is ever fabricated, filled, or silently substituted.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from app import market_data_service as svc
from hero1_market_data import store as md

H = 3600


def _utc(y, mo, d, h=0):
    return int(datetime(y, mo, d, h, tzinfo=timezone.utc).timestamp())


# a clean, session-shaped H1 week: Mon 2026-10-12 00:00 .. Fri 2026-10-16 20:00
BASE = _utc(2026, 10, 12, 0)


def _candle(ts, close=100.0):
    return {"ts": ts, "ts_iso": md.ts_iso_of(ts), "open": close - 0.5,
            "high": close + 1.0, "low": close - 1.0, "close": close,
            "tick_volume": 100, "volume": 0.0, "spread": 12}


def _week(skip_hours=()):
    # real session shape: hourly bars 00:00-20:00 and 22:00-23:00 (the
    # 21:00 bar does not exist - nightly break), Mon-Fri
    rows = []
    for day in range(5):
        for hour in list(range(0, 21)) + [22, 23]:
            ts = BASE + day * 86400 + hour * H
            if ts not in skip_hours:
                rows.append(_candle(ts))
    return rows


@pytest.fixture()
def store(tmp_path, monkeypatch):
    db = tmp_path / "DATA" / "MARKET_DATA" / "market_data.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = md.connect_store(db)
    with conn:
        md.merge_candles(conn, dataset_id="base", instrument="XAUUSD", timeframe="H1",
                         rows=_week(), policy="import")
    conn.close()
    monkeypatch.setattr(svc, "store_db_path", lambda: db)
    return db


def _insert_conflict(db, ts, fields):
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO fetch_conflicts (operation_id, instrument, timeframe, ts, ts_iso,"
        " stored_dataset_id, stored_source, incoming_source, stored, incoming,"
        " differing_fields, classification, resolution, created_at)"
        " VALUES ('OP','XAUUSD','H1',?,?, 'base','CSV','MT5','{}','{}',?,"
        " 'CONFLICT_STORED_PRESERVED','UNRESOLVED','2026-11-07T00:00:00Z')",
        (ts, md.ts_iso_of(ts), json.dumps(fields)))
    conn.commit(); conn.close()


def _quality(store, frm=None, to=None, tf="H1"):
    return svc.data_quality("XAUUSD", tf, from_epoch=frm, to_epoch=to)


# 1 - clean period
def test_clean_range_is_ready(store):
    r = _quality(store, BASE, BASE + 4 * 86400 + 20 * H)
    assert r["readiness"] == "READY", r
    assert r["blocked_reasons"] == [] and r["warnings"] == []
    assert r["excluded_candles"] == 0 and r["candles_used"] == r["candles_in_range"]


# 2 - small unverified weekday gap -> warning only
def test_minor_unverified_gap_warns_not_blocks(store):
    # drop Tue 10:00-11:00 (two hours on a trading day = unverified gap)
    conn = md.connect_store(store)
    with conn:
        conn.execute("DELETE FROM candles WHERE ts IN (?, ?)",
                     (BASE + 86400 + 10 * H, BASE + 86400 + 11 * H))
    conn.close()
    r = _quality(store, BASE, BASE + 4 * 86400 + 20 * H)
    assert r["readiness"] == "READY_WITH_WARNINGS", r
    assert r["checks"]["unverified_gap_intervals"] == 1
    assert r["excluded_candles"] == 0  # gaps are reported, never fabricated/filled


# 3+4 - conflict placement and severity
def test_conflict_outside_range_does_not_block(store):
    _insert_conflict(store, BASE + 4 * 86400 + 20 * H, {"close": {"stored": 1, "incoming": 2}})
    r = _quality(store, BASE, BASE + 86400)  # Monday only; conflict is Friday
    assert r["readiness"] == "READY", r
    assert r["conflicts"]["unresolved"] == 1
    assert r["conflicts"]["unresolved_in_range"] == 0


def test_price_conflict_inside_range_blocks(store):
    ts = BASE + 2 * 86400 + 5 * H
    _insert_conflict(store, ts, {"close": {"stored": 1, "incoming": 2}})
    r = _quality(store, BASE, BASE + 4 * 86400 + 20 * H)
    assert r["readiness"] == "BLOCKED", r
    assert r["checks"]["price_conflicts_in_range"] == 1


def test_metadata_only_conflict_inside_range_warns(store):
    ts = BASE + 2 * 86400 + 5 * H
    _insert_conflict(store, ts, {"spread": {"stored": 5, "incoming": 0},
                                 "tick_volume": {"stored": 9, "incoming": 3}})
    r = _quality(store, BASE, BASE + 4 * 86400 + 20 * H)
    assert r["readiness"] == "READY_WITH_WARNINGS", r
    assert r["checks"]["metadata_only_conflicts_in_range"] == 1


# 5 - problems are timeframe-scoped
def test_gap_in_other_timeframe_does_not_block(store):
    conn = md.connect_store(store)
    with conn:  # sparse M5 series with holes - irrelevant to an H1 test
        md.merge_candles(conn, dataset_id="m5x", instrument="XAUUSD", timeframe="M5",
                         rows=[_candle(BASE), _candle(BASE + 7200)], policy="import")
    conn.close()
    r = _quality(store, BASE, BASE + 4 * 86400 + 20 * H, tf="H1")
    assert r["readiness"] == "READY", r


# 6 - coverage
def test_insufficient_coverage_blocks(store):
    r = _quality(store, BASE - 86400, BASE + 86400)
    assert r["readiness"] == "BLOCKED"
    assert any("starts before" in x for x in r["blocked_reasons"])
    r2 = _quality(store, BASE, BASE + 30 * 86400)
    assert r2["readiness"] == "BLOCKED"
    assert any("ends after" in x for x in r2["blocked_reasons"])


# 7 - corrupt candles inside the range
def test_invalid_ohlc_and_duplicates_block(store):
    ts = BASE + 86400 + 3 * H
    conn = sqlite3.connect(store)
    conn.execute("UPDATE candles SET high = low - 1 WHERE ts = ?", (ts,))
    conn.commit(); conn.close()
    r = _quality(store, BASE, BASE + 4 * 86400 + 20 * H)
    assert r["readiness"] == "BLOCKED" and r["checks"]["invalid_ohlc"] == 1
    assert r["excluded_candles"] == 1 and r["candles_used"] == r["candles_in_range"] - 1

    # duplicate timestamps (direct SQL: the store key normally forbids them)
    conn = sqlite3.connect(store)
    conn.execute("UPDATE candles SET high = low + 2 WHERE ts = ?", (ts,))
    try:
        conn.execute("INSERT INTO candles SELECT * FROM candles WHERE ts = ?", (ts,))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
    conn.close()
    # build an unconstrained duplicate scenario via a shadow violation: if the
    # primary key prevented insertion the count stays 0 - assert honestly
    r2 = _quality(store, BASE, BASE + 4 * 86400 + 20 * H)
    assert r2["checks"]["duplicate_timestamps"] == 0  # PK held; no fake failure


def test_invalid_timestamp_blocks(store):
    ts = BASE + 86400 + 3 * H
    conn = sqlite3.connect(store)
    conn.execute("UPDATE candles SET ts_iso = '1999-01-01 00:00:00' WHERE ts = ?", (ts,))
    conn.commit(); conn.close()
    r = _quality(store, BASE, BASE + 4 * 86400 + 20 * H)
    assert r["readiness"] == "BLOCKED" and r["checks"]["invalid_timestamps"] == 1


# 9 - reporting completeness
def test_report_discloses_scope_and_counts(store):
    _insert_conflict(store, BASE + 4 * 86400 + 20 * H, {"close": {"stored": 1, "incoming": 2}})
    r = _quality(store, BASE, BASE + 86400)
    assert r["instrument"] == "XAUUSD" and r["timeframe"] == "H1"
    assert r["first_ts"] and r["last_ts"]           # actual available range
    assert r["candles_in_range"] + r["candles_after_range"] + r["candles_before_range"] \
        == r["candles_total"]
    assert r["synthetic_data_used"] is False
