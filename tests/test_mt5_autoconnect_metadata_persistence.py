"""ISSUE-056: automatic MT5 connection planning, metadata auto-refresh
dedupe, post-commit persistence verification, and market-closure-aware
freshness. Everything MT5 here is mocked - genuine Windows behaviour stays
NOT VERIFIED until the owner runs it.
"""
from __future__ import annotations

import sqlite3
import threading
import time as time_mod
from datetime import datetime, timezone

import pytest

from app import market_data_service as svc
from app import mt5_autoconnect
from hero1_market_data import store as md
from hero1_mt5_adapter import connection as mt5_connection

STEP_H1 = 3600
LATEST = 1601503200  # 2020-09-30 22:00 UTC (Wednesday)


def _ts(y, mo, d, h=0, mi=0, s=0):
    return int(datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc).timestamp())


@pytest.fixture(autouse=True)
def _mt5_package_present(monkeypatch):
    monkeypatch.setattr(mt5_connection, "package_available", lambda: True)


# ---------------------------------------------------------- connection plan
def _term(path, running):
    return {"path": path, "running": running, "state": "RUNNING" if running else "DISCOVERED"}


def test_plan_no_terminal_running():
    d = mt5_connection.decide_auto_connect([_term("C:/t1/terminal64.exe", False)])
    assert d["action"] == "none" and "no MT5 terminal is running" in d["reason"]


def test_plan_exactly_one_running_connects():
    d = mt5_connection.decide_auto_connect([_term("C:/t1/terminal64.exe", True),
                                            _term("C:/t2/terminal64.exe", False)])
    assert d["action"] == "connect" and d["path"] == "C:/t1/terminal64.exe"


def test_plan_multiple_running_is_ambiguous():
    d = mt5_connection.decide_auto_connect([_term("C:/t1/terminal64.exe", True),
                                            _term("C:/t2/terminal64.exe", True)])
    assert d["action"] == "none" and "ambiguous" in d["reason"]


def test_plan_saved_terminal_wins():
    d = mt5_connection.decide_auto_connect(
        [_term("C:/t1/terminal64.exe", True), _term("C:/t2/terminal64.exe", True)],
        saved_terminal_path="C:/t2/terminal64.exe")
    assert d["action"] == "connect" and d["path"].endswith("t2/terminal64.exe")


def test_plan_saved_terminal_not_running_never_launches():
    d = mt5_connection.decide_auto_connect([_term("C:/t1/terminal64.exe", False)],
                                           saved_terminal_path="C:/t1/terminal64.exe")
    assert d["action"] == "none" and "not launching" in d["reason"]


def test_run_auto_connect_uses_attach_only(monkeypatch):
    calls = {}

    def fake_connect(path, launch=True):
        calls["path"], calls["launch"] = path, launch
        return {"ok": True, "state": mt5_connection.STATE_ACCOUNT_LOADED,
                "account": {"server": "S", "login": 1}, "terminal": {}}

    monkeypatch.setattr(mt5_autoconnect, "refresh_metadata_after_connect",
                        lambda reason: calls.setdefault("meta", reason))
    out = mt5_autoconnect.run_auto_connect(
        saved_path=None,
        discover_fn=lambda: [{"path": "C:/t1/terminal64.exe"}],
        running_fn=lambda p: True,
        connect_fn=fake_connect)
    assert out["ok"] is True and calls["launch"] is False
    assert calls["meta"] == "auto-connect"


def test_run_auto_connect_multiple_running_connects_nothing():
    called = []
    out = mt5_autoconnect.run_auto_connect(
        saved_path=None,
        discover_fn=lambda: [{"path": "C:/a"}, {"path": "C:/b"}],
        running_fn=lambda p: True,
        connect_fn=lambda *a, **k: called.append(1) or {})
    assert out["action"] == "none" and not called


def test_run_auto_connect_failure_surfaces_reason():
    out = mt5_autoconnect.run_auto_connect(
        saved_path=None,
        discover_fn=lambda: [{"path": "C:/a"}],
        running_fn=lambda p: True,
        connect_fn=lambda *a, **k: {"ok": False, "state": "FAILED",
                                    "error": "initialize failed: terminal busy",
                                    "error_code": "MT5_INIT_FAILED"})
    assert out["ok"] is False and out["error"] == "initialize failed: terminal busy"


# ------------------------------------------------------- metadata refreshing
def test_metadata_refresh_is_deduplicated():
    gate = threading.Event()
    calls = []

    def slow_refresh(sym):
        calls.append(sym)
        gate.wait(timeout=5)
        return {"broker_server": "S"}

    assert mt5_autoconnect.refresh_metadata_after_connect(
        "test", refresh_fn=slow_refresh, instruments=["XAUUSD"]) is True
    assert mt5_autoconnect.refresh_metadata_after_connect(
        "test", refresh_fn=slow_refresh, instruments=["XAUUSD"]) is False
    gate.set()
    time_mod.sleep(0.3)
    assert calls == ["XAUUSD"]
    # after completion a new refresh may start again
    mt5_autoconnect._meta_running = False


def test_metadata_staleness_annotation(tmp_path, monkeypatch):
    from app import metadata_service
    monkeypatch.setenv("HERO1_DATA_ROOT", str(tmp_path))
    metadata_service.save_snapshot({
        "instrument": "XAUUSD", "broker_server": "OldServer-Demo",
        "terminal": {}, "account_login": 1, "source": "mt5-terminal",
        "retrieved_at": "2026-01-01T00:00:00Z", "fingerprint": "x",
        "fields": {}, "derived": {}, "validation": {}, "raw": {}})
    view = metadata_service.annotate_staleness(metadata_service.available_view("XAUUSD"))
    assert "stale" not in (view.get("snapshot") or {})  # nothing connected -> no claim

    monkeypatch.setattr(mt5_connection, "status",
                        lambda: {"account": {"server": "NewServer-Live"}})
    view = metadata_service.annotate_staleness(metadata_service.available_view("XAUUSD"))
    assert view["snapshot"]["stale"] is True
    assert "NewServer-Live" in view["snapshot"]["stale_reason"]


# --------------------------------------------------- persistence verification
def _candle(ts, close=1901.0):
    return {"ts": ts, "ts_iso": md.ts_iso_of(ts), "open": 1900.0, "high": 1902.0,
            "low": 1899.0, "close": close, "tick_volume": 100, "volume": 0.0,
            "spread": 12}


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


RATES = [
    _rate(_candle(LATEST - STEP_H1)),                    # duplicate
    _rate(_candle(LATEST)),                              # duplicate
    _rate(_candle(LATEST + STEP_H1)),                    # new
    _rate(_candle(LATEST + 2 * STEP_H1)),                # new
]


@pytest.fixture()
def seeded(tmp_path, monkeypatch):
    db = tmp_path / "DATA" / "MARKET_DATA" / "market_data.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = md.connect_store(db)
    with conn:
        md.merge_candles(conn, dataset_id="baseline", instrument="XAUUSD",
                         timeframe="H1", rows=[_candle(LATEST - STEP_H1), _candle(LATEST)],
                         policy="import")
    conn.close()
    monkeypatch.setattr(svc, "store_db_path", lambda: db)
    monkeypatch.setattr(svc, "_utc_now_epoch", lambda: LATEST + 4 * STEP_H1)
    monkeypatch.setattr(mt5_connection, "connect",
                        lambda _p: {"state": mt5_connection.STATE_ACCOUNT_LOADED,
                                    "account": {"server": "ICMarketsSC-Demo"}, "terminal": {}})
    monkeypatch.setattr(mt5_connection, "mt5_module", lambda: _FakeMT5(RATES))
    return db


def test_fetch_persists_and_readback_verifies(seeded):
    report = svc.fetch("XAUUSD", ["H1"], operation_id="FETCH-056-OK")
    (r,) = report["results"]
    assert r["status"] == "UPDATED" and r["appended"] == 2, r
    conn = sqlite3.connect(seeded)
    assert conn.execute("SELECT COUNT(*) FROM candles").fetchone()[0] == 4
    assert conn.execute("SELECT close FROM candles WHERE ts=?",
                        (LATEST + 2 * STEP_H1,)).fetchone()[0] == 1901.0
    conn.close()
    # repeat fetch is idempotent: no duplicates, nothing new appended
    report2 = svc.fetch("XAUUSD", ["H1"], operation_id="FETCH-056-AGAIN")
    (r2,) = report2["results"]
    assert r2["appended"] == 0
    assert sqlite3.connect(seeded).execute("SELECT COUNT(*) FROM candles").fetchone()[0] == 4


def test_restart_durability(seeded):
    svc.fetch("XAUUSD", ["H1"], operation_id="FETCH-056-RESTART")
    conn = md.connect_store(seeded)  # fresh process-equivalent connection
    try:
        stats = md.series_stats(conn, "XAUUSD", "H1")
        assert stats["row_count"] == 4
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def test_failed_commit_reports_no_success_and_rolls_back(seeded, monkeypatch):
    real_merge = md.merge_candles

    def exploding_merge(conn, **kwargs):
        result = real_merge(conn, **kwargs)  # inserts happen INSIDE the txn
        raise RuntimeError("simulated crash before commit")

    monkeypatch.setattr(md, "merge_candles", exploding_merge)
    report = svc.fetch("XAUUSD", ["H1"], operation_id="FETCH-056-CRASH")
    (r,) = report["results"]
    assert r["status"] == "FAILED" and r["appended"] == 0
    # rollback: the store still holds only the seeded candles
    assert sqlite3.connect(seeded).execute(
        "SELECT COUNT(*) FROM candles").fetchone()[0] == 2


# -------------------------------------------------------------- freshness
def test_expected_latest_saturday_is_friday_evening():
    saturday = _ts(2026, 11, 7, 15, 29)   # the owner's real fetch moment
    got = svc.expected_latest_completed(saturday, 60)
    dt = datetime.fromtimestamp(got, tz=timezone.utc)
    assert dt.weekday() == 4 and dt.hour == 20 and dt.minute == 59, dt


def test_expected_latest_weekday_counts_completed_bars():
    wed_noon = _ts(2026, 11, 4, 12, 0)
    assert svc.expected_latest_completed(wed_noon, 3600) == _ts(2026, 11, 4, 11, 0)
    # nightly break: 21:30 Wednesday -> last expected bar is 20:00
    assert svc.expected_latest_completed(_ts(2026, 11, 4, 21, 30), 3600) == _ts(2026, 11, 4, 20, 0)


def test_expected_latest_sunday_before_open_is_friday():
    assert svc.expected_latest_completed(_ts(2026, 11, 8, 10, 0), 3600) == _ts(2026, 11, 6, 20, 0)
    # after Sunday 23:00 UTC reopen, Sunday bars count again
    assert svc.expected_latest_completed(_ts(2026, 11, 8, 23, 30), 60) == _ts(2026, 11, 8, 23, 29)
