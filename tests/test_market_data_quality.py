"""Gap classification, freshness semantics, data quality (ISSUE-055 B5/B6/B7).

All timestamps UTC. 2026-10-16 is a Friday; 2026-10-19 a Monday.
"""
from __future__ import annotations

import pytest

from app import market_data_service as svc
from hero1_market_data import store as md

FRI_2200 = 1792183200   # Fri 2026-10-16 22:00 UTC (computed below, asserted)
MON_0000 = 1792368000   # Mon 2026-10-19 00:00 UTC


def _h(ts_list):
    return ts_list


def test_classifier_weekend_is_expected_closure():
    from datetime import datetime, timezone
    assert datetime.fromtimestamp(FRI_2200, tz=timezone.utc).weekday() == 4
    assert datetime.fromtimestamp(MON_0000, tz=timezone.utc).weekday() == 0
    # hourly candles: Fri 22:00 -> Mon 00:00 (weekend in between)
    ts = [FRI_2200 - 3600, FRI_2200, MON_0000, MON_0000 + 3600]
    g = md.classify_gaps(ts, 3600)
    assert g["total_intervals"] == 1 and g["expected_closure"] == 1
    assert g["unverified_intervals"] == 0 and g["unverified_missing_candles_est"] == 0


def test_classifier_session_break_is_expected():
    from datetime import datetime, timezone
    tue = MON_0000 + 86400 + 20 * 3600  # Tue 20:00 UTC
    assert datetime.fromtimestamp(tue, tz=timezone.utc).weekday() == 1
    ts = [tue, tue + 3600, tue + 3 * 3600, tue + 4 * 3600]  # 2h nightly pause
    g = md.classify_gaps(ts, 3600)
    assert g["expected_session_break"] == 1 and g["unverified_intervals"] == 0


def test_classifier_weekday_gap_is_unverified_with_estimate():
    from datetime import datetime, timezone
    wed_1000 = FRI_2200 + 5 * 86400 - 12 * 3600  # Wed 10:00 UTC
    assert datetime.fromtimestamp(wed_1000, tz=timezone.utc).weekday() == 2
    ts = [wed_1000, wed_1000 + 6 * 3600]  # 6h hole mid-session
    g = md.classify_gaps(ts, 3600)
    assert g["unverified_intervals"] == 1
    assert g["unverified_missing_candles_est"] == 5
    assert g["largest_unverified_seconds"] == 6 * 3600


# ------------------------------------------------------- availability/quality
STEP = 3600
LATEST = 1601503200  # Wed 2020-09-30 22:00 UTC


def _candle(ts):
    return {"ts": ts, "ts_iso": md.ts_iso_of(ts), "open": 1900.0, "high": 1902.0,
            "low": 1899.0, "close": 1901.0, "tick_volume": 100, "volume": 0.0,
            "spread": 12}


@pytest.fixture()
def seeded(tmp_path, monkeypatch):
    db = tmp_path / "DATA" / "MARKET_DATA" / "market_data.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = md.connect_store(db)
    rows = [_candle(LATEST - STEP), _candle(LATEST)]
    with conn:
        md.merge_candles(conn, dataset_id="baseline", instrument="XAUUSD",
                         timeframe="H1", rows=rows, policy="import")
    conn.close()
    monkeypatch.setattr(svc, "store_db_path", lambda: db)
    return db


def test_freshness_up_to_date_when_latest_is_last_completed(seeded, monkeypatch):
    # now = LATEST + 30min -> last completed bar opened at LATEST
    monkeypatch.setattr(svc, "_utc_now_epoch", lambda: LATEST + 1800)
    out = svc.availability("XAUUSD")
    tf = out["timeframes"]["H1"]
    assert tf["up_to_date"] is True and tf["mt5_fetch_required"] is False
    assert tf["missing_completed_candles"] == 0
    assert "H1" in out["summary"]["up_to_date_timeframes"]
    assert out["summary"]["mt5_fetch_required_timeframes"] == []


def test_freshness_counts_missing_completed_bars(seeded, monkeypatch):
    monkeypatch.setattr(svc, "_utc_now_epoch", lambda: LATEST + 5 * STEP + 60)
    out = svc.availability("XAUUSD")
    tf = out["timeframes"]["H1"]
    assert tf["up_to_date"] is False and tf["mt5_fetch_required"] is True
    assert tf["missing_completed_candles"] == 4  # bars at +1..+4 steps closed
    assert tf["gap_analysis"] is not None and tf["gap_analysis"]["total_intervals"] == 0


def test_data_quality_states(seeded):
    ready = svc.data_quality("XAUUSD", "H1", from_epoch=LATEST - STEP, to_epoch=LATEST)
    assert ready["readiness"] == "READY" and ready["candles_in_range"] == 2
    assert ready["synthetic_data_used"] is False

    beyond = svc.data_quality("XAUUSD", "H1", from_epoch=LATEST - STEP,
                              to_epoch=LATEST + 10 * STEP)
    assert beyond["readiness"] == "NOT_READY"

    empty = svc.data_quality("XAUUSD", "M5")
    assert empty["readiness"] == "NOT_READY" and empty["candles_total"] == 0

    with pytest.raises(ValueError):
        svc.data_quality("XAUUSD", "X9")


def test_events_sort_is_server_side_before_pagination(client):
    for sev, msg in [("WARNING", "sort-bravo"), ("INFO", "sort-alpha"), ("ERROR", "sort-charlie")]:
        r = client.post("/api/v1/events", json={
            "category": "SYSTEM", "severity": sev, "subsystem": "sort-test",
            "message": msg, "instrument": "XAUUSD"})
        assert r.status_code in (200, 201), r.text
    asc = client.get("/api/v1/events?subsystem=sort-test&sort=severity&order=asc&limit=10").json()
    msgs = [e["message"] for e in asc["events"]]
    assert msgs == ["sort-charlie", "sort-alpha", "sort-bravo"]  # ERROR < INFO < WARNING
    page = client.get("/api/v1/events?subsystem=sort-test&sort=severity&order=desc&limit=2").json()
    assert [e["message"] for e in page["events"]] == ["sort-bravo", "sort-alpha"]
    assert page["total"] == 3
    default = client.get("/api/v1/events?subsystem=sort-test&limit=10").json()
    assert [e["message"] for e in default["events"]] == ["sort-charlie", "sort-alpha", "sort-bravo"]
