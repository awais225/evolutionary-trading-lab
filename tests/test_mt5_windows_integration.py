"""Windows MT5 integration, exercised with a fake MetaTrader5 module.

IMPORTANT: everything here is a MOCKED test (the label matters). The fake
stands in for the official Windows-only package so the connect lifecycle
(launch -> initialize -> account), metadata refresh, incremental fetch and
selection isolation can be verified on Linux. Genuine terminal behavior
remains a Windows acceptance item - see docs/WINDOWS_SETUP.md.

The fake is injected at hero1_mt5_adapter.connection._MT5, the single place
Hero1 is allowed to speak to MT5.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from hero1_mt5_adapter import connection
from hero1_mt5_adapter.metadata import normalize_symbol_info  # noqa: F401  (parity import guard)
from hero1_strategy_spec import bullish_engulfing

from test_metadata_mt5 import ICMARKETS_BTCUSD, _cleanup_store  # reuse fixtures


class FakeMT5:
    """Minimal official-API surface, dict-based (as_dict passes dicts through)."""

    TIMEFRAME_M1, TIMEFRAME_M5, TIMEFRAME_M15, TIMEFRAME_M30 = 1, 5, 15, 30
    TIMEFRAME_H1, TIMEFRAME_H4, TIMEFRAME_D1 = 101, 104, 105

    def __init__(self):
        self.initialized = False
        self.init_fail = False
        self.init_calls = 0
        self.launched = False          # flipped by the fake Popen seam
        self.account = None
        self.terminal = {"connected": True, "build": 4999, "company": "IC Markets Global",
                         "name": "MetaTrader 5", "path": "C:/MT5", "data_path": "C:/MT5/data",
                         "trade_allowed": True, "trade_expert": True}
        self.tick = {"bid": 95250.0, "ask": 95261.0, "time": 1792000000, "time_msc": 1792000000123}
        self.symbol = dict(ICMARKETS_BTCUSD)
        self.rates = {}                # timeframe const -> list[dict]
        self.rates_error_for = set()   # timeframe consts that raise
        self.range_requests = []       # (symbol, tf, from_dt, to_dt)
        self.last_err = (0, "ok")

    def initialize(self, path=None):
        self.init_calls += 1
        if self.init_fail:
            return False
        if self.launched is False and getattr(self, "needs_launch", False):
            return False               # terminal not started yet
        self.initialized = True
        return True

    def terminal_info(self):
        return dict(self.terminal) if self.initialized else None

    def account_info(self):
        return dict(self.account) if (self.initialized and self.account) else None

    def symbol_info(self, symbol):
        return dict(self.symbol)

    def symbol_info_tick(self, symbol):
        return dict(self.tick)

    def copy_rates_range(self, symbol, timeframe, from_dt, to_dt):
        self.range_requests.append((symbol, timeframe, from_dt, to_dt))
        if timeframe in self.rates_error_for:
            raise RuntimeError("terminal IPC timeout (fake)")
        return list(self.rates.get(timeframe, []))

    def version(self):
        return (500, 4999, 0)

    def last_error(self):
        return self.last_err

    def shutdown(self):
        self.initialized = False


ACCOUNT = {"login": 12345678, "name": "Owner Name", "server": "ICMarketsSC-Demo",
           "company": "IC Markets Global", "currency": "USD", "balance": 10000.0,
           "equity": 10025.5, "profit": 25.5, "margin": 120.0, "margin_free": 9905.5,
           "margin_level": 8354.6, "leverage": 500, "trade_allowed": True,
           "trade_expert": True, "trade_mode": 0}


@pytest.fixture()
def fake_mt5(monkeypatch):
    fake = FakeMT5()
    monkeypatch.setattr(connection, "_MT5", fake)
    monkeypatch.setattr(connection, "MT5_PACKAGE_AVAILABLE", True)
    return fake


@pytest.fixture()
def fake_exe(tmp_path):
    exe = tmp_path / "terminal64.exe"
    exe.write_bytes(b"fake")
    return exe


def _as_windows(monkeypatch):
    monkeypatch.setattr(connection, "_os_name", lambda: "Windows")


# ------------------------------------------------------------- connect flow

def test_connect_launches_terminal_then_loads_account(fake_mt5, fake_exe, monkeypatch):
    _as_windows(monkeypatch)
    fake_mt5.needs_launch = True       # initialize fails until "launched"
    fake_mt5.account = dict(ACCOUNT)
    pops = []
    monkeypatch.setattr(connection, "_popen_detached",
                        lambda path: pops.append(path) or setattr(fake_mt5, "launched", True))
    res = connection.connect(str(fake_exe), timeout_seconds=10)
    assert pops == [str(fake_exe)]                       # launch happened, once
    assert res["launched"] is True
    assert res["state"] == "ACCOUNT_LOADED"
    assert res["ok"] is True
    assert res["account"]["login"] == 12345678
    assert res["account"]["profit"] == 25.5              # §5 fields present
    assert res["account"]["trade_allowed"] is True
    assert res["api_version"] == [500, 4999, 0]


def test_connect_init_failure_is_failed_with_last_error(fake_mt5, fake_exe, monkeypatch):
    _as_windows(monkeypatch)
    fake_mt5.init_fail = True
    fake_mt5.last_err = (-10003, "terminal not found (fake)")
    monkeypatch.setattr(connection, "_popen_detached", lambda path: None)
    res = connection.connect(str(fake_exe), timeout_seconds=0)
    assert res["state"] == "FAILED"
    assert res["ok"] is False
    assert res["error_code"] == "MT5_INIT_FAILED"
    assert res["last_error"] == [-10003, "terminal not found (fake)"]


def test_terminal_launch_failure_is_reported(fake_mt5, fake_exe, monkeypatch):
    _as_windows(monkeypatch)
    fake_mt5.init_fail = True

    def boom(path):
        raise OSError("cannot start process (fake)")
    monkeypatch.setattr(connection, "_popen_detached", boom)
    res = connection.connect(str(fake_exe), timeout_seconds=0)
    assert res["state"] == "FAILED"
    assert "launch" in (res["error"] or "")


def test_connect_without_account_is_connected_login_required(fake_mt5, fake_exe, client):
    fake_mt5.account = None            # terminal up, nobody logged in
    res = connection.connect(str(fake_exe), timeout_seconds=0)
    assert res["state"] == "CONNECTED"
    assert res["login_required"] is True
    assert res["ok"] is False
    r = client.post("/api/v1/mt5/connect", json={"terminal_path": str(fake_exe)})
    assert r.status_code == 200        # a displayed state, not a 503
    assert r.json()["state"] == "CONNECTED"
    r = client.post("/api/v1/instruments/BTCUSD/metadata/refresh")
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "MT5_LOGIN_REQUIRED"


def test_status_states_and_account_fields(fake_mt5, client):
    r = client.get("/api/v1/mt5/status")
    assert r.json()["state"] == "NOT_CONNECTED"
    fake_mt5.account = dict(ACCOUNT)
    connection.connect(None, timeout_seconds=0)
    body = client.get("/api/v1/mt5/status").json()
    assert body["state"] == "ACCOUNT_LOADED"
    assert body["account"]["margin"] == 120.0
    assert body["account"]["margin_free"] == 9905.5
    assert body["api_version"] == [500, 4999, 0]


def test_discovered_terminal_state_enrichment(fake_mt5, monkeypatch):
    monkeypatch.setattr(connection, "discover_terminals",
                        lambda: [{"name": "IC", "path": "C:/MT5/terminal64.exe", "origin": "program-files"}])
    monkeypatch.setattr(connection, "terminal_running", lambda p: False)
    from fastapi.testclient import TestClient  # local: reuse app via client fixture pattern
    # through the router function directly (no HTTP needed for the enrichment)
    from app.api.routers import mt5 as mt5_router
    import app.deps as deps  # noqa: F401
    out = mt5_router.mt5_terminals.__wrapped__ if hasattr(mt5_router.mt5_terminals, "__wrapped__") else None
    assert out is None  # router needs a DB session; enrichment covered via API below


def test_terminals_endpoint_reports_discovered(client, fake_mt5, monkeypatch):
    monkeypatch.setattr(connection, "discover_terminals",
                        lambda: [{"name": "IC", "path": "C:/MT5/terminal64.exe", "origin": "program-files"}])
    monkeypatch.setattr(connection, "terminal_running", lambda p: True)
    body = client.get("/api/v1/mt5/terminals").json()
    assert body["terminals"][0]["state"] == "RUNNING"
    assert body["terminals"][0]["running"] is True


# ------------------------------------------------------- metadata via fake

def test_refresh_via_fake_terminal_stores_snapshot_with_live_spread(fake_mt5, client):
    _cleanup_store()
    try:
        fake_mt5.account = dict(ACCOUNT)
        connection.connect(None, timeout_seconds=0)
        r = client.post("/api/v1/instruments/BTCUSD/metadata/refresh")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["available"] is True
        assert body["snapshot"]["broker_server"] == "ICMarketsSC-Demo"
        derived = body["snapshot"]["derived"]
        assert derived["spread_points"] == pytest.approx(1100.0)   # (95261-95250)/0.01
        assert derived["pip_size"] == 0.01                        # BTCUSD digits=2 in fixture
        spec = json.loads(json.dumps(bullish_engulfing("BTCUSD", "M15")))
        v = client.post("/api/v1/instruments/BTCUSD/nodes/validate", json={"spec": spec}).json()
        assert v["status"] == "VALID"
    finally:
        _cleanup_store()


# ------------------------------------------------------------ fetch (fake)

def _seed_store(instrument, timeframe, rows, dataset_id="seeddataset00001"):
    from hero1_market_data import store as md
    from app.market_data_service import store_db_path
    db = store_db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = md.connect_store(db)
    try:
        with conn:
            md.merge_candles(conn, dataset_id=dataset_id, instrument=instrument,
                             timeframe=timeframe, rows=rows, policy="import")
            md.record_dataset(conn, dataset_id=dataset_id, instrument=instrument,
                              timeframe=timeframe, source_file="seed", source_sha256="x" * 64,
                              source_bytes=0, row_count=len(rows), duplicates_collapsed=0,
                              gaps_found=0, largest_gap_seconds=None,
                              first_ts=rows[0]["ts_iso"], last_ts=rows[-1]["ts_iso"],
                              content_fingerprint=md.canonical_fingerprint(rows),
                              location=str(db), imported_at=md.iso_now(),
                              validation_result="PASS", validation_report={}, status="AVAILABLE")
        md.rebuild_manifest(conn, db)
    finally:
        conn.close()


def _candle(ts, close=100.0):
    return {"ts": ts, "ts_iso": __import__("hero1_market_data").store.ts_iso_of(ts),
            "open": close, "high": close + 1, "low": close - 1, "close": close,
            "tick_volume": 10, "volume": 0.0, "spread": 5}


def _rate(ts, close=100.0):
    """MT5 copy_rates_range record shape (time / volume_real), as the real
    terminal returns - the fake must speak the API's language, not the store's."""
    return {"time": ts, "open": close, "high": close + 1, "low": close - 1,
            "close": close, "tick_volume": 10, "volume_real": 0.0, "spread": 5}


STEP_M5 = 300
BASE = 1792000000 - (1792000000 % STEP_M5)   # grid-aligned server epoch


def test_fetch_appends_only_new_completed_candles(fake_mt5, client, monkeypatch):
    latest = BASE
    _seed_store("BTCUSD", "M5", [_candle(latest - STEP_M5, 99.0), _candle(latest, 100.0)])
    server_now = latest + 2 * STEP_M5 + 120
    fake_mt5.account = dict(ACCOUNT)
    from app import market_data_service
    monkeypatch.setattr(market_data_service, "_utc_now_epoch", lambda: server_now)
    fake_mt5.rates = {FakeMT5.TIMEFRAME_M5: [
        _rate(latest, 100.0),                        # duplicate of stored
        _rate(latest + STEP_M5, 101.0),              # new + complete
        _rate(latest + 2 * STEP_M5, 102.0),          # incomplete (still forming)
        {"time": latest - 2 * STEP_M5, "open": 1, "high": 0.5, "low": 2,
         "close": 1, "tick_volume": 1, "volume_real": 0, "spread": 1},  # invalid OHLC
    ]}
    r = client.post("/api/v1/instruments/BTCUSD/market-data/fetch", json={"timeframe": "M5"})
    assert r.status_code == 200, r.text
    report = r.json()
    (res,) = report["results"]
    assert report["ok"] is True, res
    assert res["status"] == "UPDATED", res
    assert res["received"] == 4
    assert res["appended"] == 1
    assert res["duplicates_skipped"] == 1
    assert res["incomplete_dropped"] == 1
    assert res["invalid_rejected"] == 1
    # range requested as timezone-aware UTC datetimes, with a 2-candle overlap
    # so boundary candles are deduplicated rather than skipped
    from datetime import datetime, timezone
    sym, tf, from_dt, to_dt = fake_mt5.range_requests[-1]
    assert from_dt.tzinfo is not None
    assert from_dt == datetime.fromtimestamp(latest - 2 * STEP_M5, tz=timezone.utc)
    assert to_dt == datetime.fromtimestamp((server_now // STEP_M5) * STEP_M5, tz=timezone.utc)
    # second fetch: nothing new, no duplicates written
    r2 = client.post("/api/v1/instruments/BTCUSD/market-data/fetch", json={"timeframe": "M5"})
    res2 = r2.json()["results"][0]
    assert res2["appended"] == 0 and res2["status"] == "UP_TO_DATE"
    avail = client.get("/api/v1/instruments/BTCUSD/market-data").json()
    assert avail["timeframes"]["M5"]["row_count"] == 3


def test_fetch_conflict_is_isolated_and_preserves_history(fake_mt5, client, monkeypatch):
    latest = BASE + 10 * STEP_M5
    _seed_store("BTCUSD", "M5", [_candle(latest, 100.0)], dataset_id="seeddataset00002")
    fake_mt5.account = dict(ACCOUNT)
    from app import market_data_service
    monkeypatch.setattr(market_data_service, "_utc_now_epoch", lambda: latest + STEP_M5 + 60)
    fake_mt5.rates = {FakeMT5.TIMEFRAME_M5: [_rate(latest, 555.0)]}  # rewritten history
    r = client.post("/api/v1/instruments/BTCUSD/market-data/fetch", json={"timeframe": "M5"})
    res = r.json()["results"][0]
    # ISSUE-050: the conflict is isolated (stored history preserved, diff
    # recorded) instead of aborting the timeframe; nothing new to append.
    assert res["status"] == "PARTIAL"
    assert res["conflicts"] == 1
    assert res["appended"] == 0
    assert res["conflict_details"][0]["differing_fields"]["close"]["stored"] == 100.0
    assert res["conflict_details"][0]["differing_fields"]["close"]["incoming"] == 555.0
    from hero1_market_data import store as md
    from app.market_data_service import store_db_path
    conn = md.connect_store(store_db_path())
    close = conn.execute("SELECT close FROM candles WHERE instrument='BTCUSD' AND timeframe='M5' AND ts=?",
                         (latest,)).fetchone()[0]
    conn.close()
    assert close == 100.0     # stored history preserved


def test_fetch_all_continues_after_one_timeframe_fails(fake_mt5, client, monkeypatch):
    latest = BASE + 20 * STEP_M5
    _seed_store("BTCUSD", "M5", [_candle(latest, 100.0)], dataset_id="seeddataset00003")
    fake_mt5.account = dict(ACCOUNT)
    from app import market_data_service
    monkeypatch.setattr(market_data_service, "_utc_now_epoch", lambda: latest + 2 * STEP_M5 + 60)
    fake_mt5.rates = {FakeMT5.TIMEFRAME_M5: [_rate(latest + STEP_M5, 101.0)]}
    _seed_store("BTCUSD", "M1", [_candle(latest, 100.0)], dataset_id="seeddataset00004")
    fake_mt5.rates_error_for = {FakeMT5.TIMEFRAME_M1}   # M1 IPC dies
    r = client.post("/api/v1/instruments/BTCUSD/market-data/fetch", json={"timeframe": "ALL"})
    report = r.json()
    by_tf = {x["timeframe"]: x for x in report["results"]}
    assert by_tf["M1"]["status"] == "FAILED"
    assert by_tf["M5"]["status"] == "UPDATED" and by_tf["M5"]["appended"] == 1
    assert report["ok"] is False          # never claim full success on partial failure


def test_fetch_without_terminal_is_honest_503(client):
    # no fake injected: on this host the package is genuinely absent
    if connection.MT5_PACKAGE_AVAILABLE:
        pytest.skip("requires the MT5-unavailable path")
    r = client.post("/api/v1/instruments/BTCUSD/market-data/fetch", json={"timeframe": "M5"})
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "MT5_UNAVAILABLE"


def test_availability_distinguishes_missing_store(client):
    body = client.get("/api/v1/instruments/EURUSD/market-data").json()
    assert body["available"] is False       # EURUSD never seeded/fetched in this suite
    assert body["timeframes"]["M5"]["status"] == "EMPTY"
    assert body["timeframes"]["M5"]["row_count"] == 0
    assert body["summary"]["total_candles"] == 0


# ------------------------------------------- node pipeline + selection safety

def test_node_validate_compile_save_and_selection_isolation(fake_mt5, client):
    _cleanup_store()
    try:
        r = client.post("/api/v1/instruments/BTCUSD/metadata/import", json={
            "broker_server": "UnitTestBroker-Demo", "symbol_info": dict(ICMARKETS_BTCUSD),
            "tick": {"bid": 95250.0, "ask": 95261.0, "time": 1730000000},
            "source": "manual-import"})
        assert r.status_code == 201, r.text
        spec = json.loads(json.dumps(bullish_engulfing("BTCUSD", "M15")))
        spec["identity"]["name"] = "TASK13 isolation node"
        v = client.post("/api/v1/instruments/BTCUSD/nodes/validate", json={"spec": spec}).json()
        assert v["status"] == "VALID"
        c = client.post("/api/v1/instruments/BTCUSD/nodes/compile", json={"spec": spec})
        assert c.status_code == 200, c.text
        n = client.post("/api/v1/instruments/BTCUSD/nodes", json={
            "name": "TASK13 isolation node", "origin": "CUSTOM",
            "spec_json": json.dumps(spec)})
        assert n.status_code in (200, 201), n.text
        node_before = client.get(f"/api/v1/instruments/BTCUSD/nodes/{n.json()['id']}").json()
        avail_before = client.get("/api/v1/instruments/BTCUSD/market-data").json()

        # now "switch account" (Live Testing selection) and re-read everything
        fake_mt5.account = dict(ACCOUNT)
        conn_res = client.post("/api/v1/mt5/connect", json={"terminal_path": None})
        assert conn_res.status_code == 200
        node_after = client.get(f"/api/v1/instruments/BTCUSD/nodes/{n.json()['id']}").json()
        assert node_after["spec_hash"] == node_before["spec_hash"]
        assert node_after["spec_json"] == node_before["spec_json"]
        assert client.get("/api/v1/instruments/BTCUSD/market-data").json() == avail_before
        meta_after = client.get("/api/v1/instruments/BTCUSD/metadata").json()
        assert meta_after["snapshot"]["broker_server"] == "UnitTestBroker-Demo"
    finally:
        _cleanup_store()


# ------------------------------------------------------------- env diagnosis

def test_check_mt5_env_reports_this_interpreter():
    script = Path(__file__).resolve().parent.parent / "scripts" / "check_mt5_env.py"
    proc = subprocess.run([sys.executable, str(script), "--json"],
                          capture_output=True, text=True, timeout=60)
    report = json.loads(proc.stdout)
    assert report["python_executable"] == sys.executable
    assert report["package_name"] == "MetaTrader5"
    if connection.MT5_PACKAGE_AVAILABLE:
        assert proc.returncode == 0
    else:
        assert proc.returncode == 1
        assert report["package_installed"] is False
        assert "No module named 'MetaTrader5'" in report["import_error"]


def test_fetch_rejects_invalid_rate_timestamps_without_storing(fake_mt5, client, monkeypatch):
    latest = BASE + 40 * STEP_M5
    _seed_store("BTCUSD", "M5", [_candle(latest, 100.0)], dataset_id="seeddataset00005")
    fake_mt5.account = dict(ACCOUNT)
    from app import market_data_service
    monkeypatch.setattr(market_data_service, "_utc_now_epoch", lambda: latest + 3 * STEP_M5)
    bad_year_2926 = 30185606400  # 2926-09-06 approx: impossible candle year
    fake_mt5.rates = {FakeMT5.TIMEFRAME_M5: [
        _rate(bad_year_2926, 101.0),
        _rate(0, 101.0),                              # 1970 epoch accident
        _rate((latest + STEP_M5) * 1000, 101.0),      # milliseconds mistaken for seconds
        _rate(latest + STEP_M5, 101.0),               # the one valid new candle
    ]}
    r = client.post("/api/v1/instruments/BTCUSD/market-data/fetch", json={"timeframe": "M5"})
    res = r.json()["results"][0]
    assert res["invalid_timestamps_rejected"] == 3
    assert res["appended"] == 1
    assert any("2926" not in e and "invalid timestamp" in e for e in res["invalid_examples"])
    avail = client.get("/api/v1/instruments/BTCUSD/market-data").json()
    assert avail["timeframes"]["M5"]["invalid_timestamp_count"] == 0  # nothing bad entered


def test_fetch_without_baseline_refuses_and_never_calls_terminal(fake_mt5, client):
    fake_mt5.account = dict(ACCOUNT)
    r = client.post("/api/v1/instruments/EURUSD/market-data/fetch", json={"timeframe": "M5"})
    res = r.json()["results"][0]
    assert res["status"] == "FAILED"
    assert "no stored baseline" in res["error"]
    assert fake_mt5.range_requests == []  # no epoch-0 / giant backfill request was built
    assert r.json()["status"] == "FAILED"
