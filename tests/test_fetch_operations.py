"""Fetch operation lifecycle (ISSUE-052) — MOCKED terminal.

Background operations with queryable state, duplicate-start prevention,
per-timeframe MT5 timeout, stack-trace events, and event pagination.
Genuine terminal behavior remains a Windows acceptance item.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from app import market_data_service as svc
from hero1_market_data import store as md
from hero1_mt5_adapter import connection as mt5_connection

STEP = 3600
LATEST = 1601503200


def _candle(ts: int, close: float = 1901.0) -> dict:
    return {"ts": ts, "ts_iso": md.ts_iso_of(ts), "open": 1900.0, "high": 1902.0,
            "low": 1899.0, "close": close, "tick_volume": 100, "volume": 0.0, "spread": 12}


class _FakeMT5:
    TIMEFRAME_M1, TIMEFRAME_M5, TIMEFRAME_M15, TIMEFRAME_M30 = 1, 5, 15, 30
    TIMEFRAME_H1, TIMEFRAME_H4, TIMEFRAME_D1 = 101, 104, 105

    def __init__(self, rates=(), gate: threading.Event | None = None, error=None):
        self._rates = list(rates)
        self._gate = gate
        self._error = error

    def copy_rates_range(self, symbol, timeframe, from_dt, to_dt):
        if self._gate is not None:
            self._gate.wait(timeout=10)
        if self._error is not None:
            raise self._error
        return [{"time": c["ts"], "open": c["open"], "high": c["high"], "low": c["low"],
                 "close": c["close"], "tick_volume": c["tick_volume"],
                 "volume_real": c["volume"], "spread": c["spread"]} for c in self._rates]

    def last_error(self):
        return (0, "ok")


@pytest.fixture()
def seeded(tmp_path, monkeypatch):
    db = tmp_path / "DATA" / "MARKET_DATA" / "market_data.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = md.connect_store(db)
    rows = [_candle(LATEST - STEP), _candle(LATEST)]
    with conn:
        md.merge_candles(conn, dataset_id="baseline", instrument="XAUUSD",
                         timeframe="H1", rows=rows, policy="import")
        md.record_dataset(conn, dataset_id="baseline", instrument="XAUUSD", timeframe="H1",
                          source_file="baseline.csv", source_sha256="x", source_bytes=1,
                          row_count=2, duplicates_collapsed=0, gaps_found=0,
                          largest_gap_seconds=None, first_ts=rows[0]["ts_iso"],
                          last_ts=rows[-1]["ts_iso"], content_fingerprint="fp",
                          location=str(db), imported_at=md.iso_now(),
                          validation_result="PASS", validation_report={"kind": "csv"},
                          status="AVAILABLE")
    conn.close()
    monkeypatch.setattr(svc, "store_db_path", lambda: db)
    monkeypatch.setattr(svc, "_utc_now_epoch", lambda: LATEST + 2 * STEP)
    monkeypatch.setattr(mt5_connection, "connect",
                        lambda _p: {"state": mt5_connection.STATE_ACCOUNT_LOADED,
                                    "account": {"server": "FakeServer"}, "terminal": {}})
    svc._OPS.clear()
    return db


def _wait_done(operation_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rec = svc.operation_status(operation_id)
        if rec and rec["state"] not in ("QUEUED", "RUNNING"):
            return rec
        time.sleep(0.05)
    raise AssertionError(f"operation {operation_id} never finished")


def test_background_operation_completes_and_is_queryable(seeded, monkeypatch):
    monkeypatch.setattr(mt5_connection, "mt5_module",
                        lambda: _FakeMT5(rates=[_candle(LATEST + STEP)]))
    started = svc.start_fetch("XAUUSD", ["H1"], operation_id="FETCH-TEST-OP1")
    assert started["existing"] is False and started["state"] in ("QUEUED", "RUNNING")
    rec = _wait_done("FETCH-TEST-OP1")
    assert rec["state"] == "COMPLETED"
    assert rec["report"]["results"][0]["appended"] == 1
    assert svc.latest_operation("XAUUSD")["operation_id"] == "FETCH-TEST-OP1"
    assert svc.operation_status("FETCH-UNKNOWN") is None


def test_duplicate_start_returns_running_operation(seeded, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(mt5_connection, "mt5_module",
                        lambda: _FakeMT5(rates=[], gate=gate))
    first = svc.start_fetch("XAUUSD", ["H1"], operation_id="FETCH-TEST-OP2")
    second = svc.start_fetch("XAUUSD", ["H1"], operation_id="FETCH-TEST-OTHER")
    assert second["existing"] is True
    assert second["operation_id"] == first["operation_id"]
    gate.set()
    _wait_done(first["operation_id"])


def test_mt5_timeout_fails_timeframe_with_stage(seeded, monkeypatch):
    monkeypatch.setenv("HERO1_FETCH_TF_TIMEOUT_S", "0.2")
    gate = threading.Event()  # never set during the call; released afterwards
    monkeypatch.setattr(mt5_connection, "mt5_module",
                        lambda: _FakeMT5(rates=[], gate=gate))
    started = svc.start_fetch("XAUUSD", ["H1"], operation_id="FETCH-TEST-OP3")
    rec = _wait_done(started["operation_id"])
    gate.set()
    assert rec["state"] == "FAILED"
    (result,) = rec["report"]["results"]
    assert result["status"] == "FAILED" and "timed out" in result["error"]
    assert "MT5_HISTORY_REQUEST" in result["error"]


def test_terminal_exception_is_recorded_with_traceback(seeded, monkeypatch, client):
    monkeypatch.setattr(mt5_connection, "mt5_module",
                        lambda: _FakeMT5(error=RuntimeError("ipc boom")))
    started = svc.start_fetch("XAUUSD", ["H1"], operation_id="FETCH-TEST-OP4")
    rec = _wait_done(started["operation_id"])
    assert rec["state"] == "FAILED"
    events = client.get("/api/v1/events",
                        params={"search": "FETCH-TEST-OP4", "limit": 100}).json()
    payloads = str([e["payload"] for e in events["events"]])
    assert "TIMEFRAME_FETCH_FAILED" in payloads
    assert "ipc boom" in payloads and "Traceback" in payloads


def test_events_pagination_reports_total(client):
    for i in range(5):
        r = client.post("/api/v1/events", json={
            "category": "SYSTEM", "severity": "INFO", "subsystem": "pagination-test",
            "message": f"page probe {i}", "payload": {"i": i}})
        assert r.status_code == 201
    page1 = client.get("/api/v1/events", params={
        "subsystem": "pagination-test", "limit": 2, "offset": 0, "order": "desc"}).json()
    assert page1["total"] == 5 and page1["count"] == 2
    page2 = client.get("/api/v1/events", params={
        "subsystem": "pagination-test", "limit": 2, "offset": 2, "order": "desc"}).json()
    ids1 = {e["id"] for e in page1["events"]}
    ids2 = {e["id"] for e in page2["events"]}
    assert ids1.isdisjoint(ids2) and page2["count"] == 2
    assert [e["id"] for e in page1["events"]] == sorted(ids1, reverse=True)
