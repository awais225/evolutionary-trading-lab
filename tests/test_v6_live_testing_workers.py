"""V6 — real per-node live-testing workers (START/STOP lifecycle).

The Live Testing node table's START must create a REAL backend worker for the
node and the UI may only show STOP after the backend confirms the worker is
running; STOP must cancel it and confirm it stopped; a node can have only ONE
worker (duplicate START never creates a second one); STOP is idempotent; and
the state a page refresh reads is the BACKEND's state, never React state.
"""
from __future__ import annotations

import copy
import importlib.util
import threading
import time
from pathlib import Path

import pytest


def _load(name: str):
    p = Path(__file__).resolve().parent / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_v43 = _load("test_v4_3_live_testing.py")
FakeMT5Terminal = _v43.FakeMT5Terminal
LiveTestBridge = _v43.LiveTestBridge


class _StubEngine:
    """Just enough engine surface for a worker cycle (no nodes -> idle cycle)."""

    def eligible_nodes(self):
        return []

    def _bridge_connected(self, bridge=None):
        return True

    def _evaluate_node(self, node, bridge, frames):
        raise AssertionError("no node should be evaluated in these lifecycle tests")


@pytest.fixture()
def clean_manager():
    from app.live_testing.workers import LiveTestWorkerManager
    m = LiveTestWorkerManager()
    yield m
    m.stop_all(timeout=5.0)


@pytest.fixture()
def stub_engine(monkeypatch):
    import app.live_testing.engine as eng_mod
    eng = _StubEngine()
    monkeypatch.setattr(eng_mod, "get_live_testing_engine", lambda: eng)
    return eng


# =========================================================================== #
# 1 — START creates a real worker and CONFIRMS it is running
# =========================================================================== #
def test_01_start_creates_a_worker_that_is_confirmed_running(clean_manager, stub_engine):
    res = clean_manager.start_worker(101, interval_s=0.02)
    assert res["ok"] is True and res["already_running"] is False
    w = res["worker"]
    assert w["state"] == "RUNNING" and w["running"] is True and w["alive"] is True
    assert w["started_at"] is not None
    assert clean_manager.is_running(101) is True


# =========================================================================== #
# 2 — duplicate START: ONE worker only, existing state returned
# =========================================================================== #
def test_02_duplicate_start_never_creates_a_second_worker(clean_manager, stub_engine):
    first = clean_manager.start_worker(102, interval_s=0.02)
    assert first["ok"] is True

    def live_workers():
        return [t for t in threading.enumerate()
                if t.name == "live-test-node-102" and t.is_alive()]

    assert len(live_workers()) == 1
    second = clean_manager.start_worker(102, interval_s=0.02)
    assert second["ok"] is True and second["already_running"] is True
    assert second["worker"]["started_at"] == first["worker"]["started_at"], \
        "the duplicate START must return the EXISTING worker's state"
    assert len(live_workers()) == 1, "no second thread may be created"
    assert len(clean_manager.states()) == 1


# =========================================================================== #
# 3 — STOP cancels the worker, confirms it stopped, and is idempotent
# =========================================================================== #
def test_03_stop_cancels_the_worker_and_is_idempotent(clean_manager, stub_engine):
    clean_manager.start_worker(103, interval_s=0.02)
    res = clean_manager.stop_worker(103)
    assert res["ok"] is True and res["already_stopped"] is False
    w = res["worker"]
    assert w["running"] is False and w["state"] == "STOPPED"
    assert clean_manager.is_running(103) is False

    again = clean_manager.stop_worker(103)          # idempotent
    assert again["ok"] is True and again["already_stopped"] is True
    assert again["worker"]["running"] is False


# =========================================================================== #
# 4 — backend state is authoritative (what a page refresh would read)
# =========================================================================== #
def test_04_worker_state_reconstructed_from_the_backend(clean_manager, stub_engine):
    st = clean_manager.state(104)
    assert st["state"] == "STOPPED" and st["running"] is False   # never started
    clean_manager.start_worker(104, interval_s=0.02)
    st = clean_manager.state(104)                                # "refresh"
    assert st["state"] == "RUNNING" and st["running"] is True
    clean_manager.stop_worker(104)
    st = clean_manager.state(104)                                # "refresh"
    assert st["state"] == "STOPPED" and st["running"] is False


# =========================================================================== #
# 5 — a node can never have two simultaneous live-testing workers
# =========================================================================== #
def test_05_a_node_never_has_two_simultaneous_workers(clean_manager, stub_engine):
    clean_manager.start_worker(105, interval_s=0.02)
    for _ in range(3):
        res = clean_manager.start_worker(105, interval_s=0.02)
        assert res["already_running"] is True
    live = [w for w in threading.enumerate()
            if w.name == "live-test-node-105" and w.is_alive()]
    assert len(live) == 1


# =========================================================================== #
# 6 — the API: START/STOP round trip with backend confirmation
# =========================================================================== #
@pytest.fixture()
def term():
    return FakeMT5Terminal()


@pytest.fixture()
def bridge(term):
    return LiveTestBridge(term)


@pytest.fixture()
def db(tmp_path):
    from app.db.database import Database
    d = Database(str(tmp_path / "v6_live_workers.db"))
    d.x("DELETE FROM live_test_configs")
    d.x("DELETE FROM live_test_trades")
    return d


@pytest.fixture()
def live_cfg(monkeypatch):
    import app.config as config_mod
    from app.config import get_config
    from app.live_testing import engine as eng_mod
    from app.live_testing import risk as risk_mod
    cfg = copy.deepcopy(get_config())
    cfg.live_testing.risk_pct_default = 1.0
    cfg.live_testing.risk_pct_max = 2.0
    cfg.live_testing.max_active_trades = 5
    cfg.live_testing.tick_interval_s = 3600.0
    cfg.live_testing.max_data_age_s = 120
    monkeypatch.setattr(eng_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(risk_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(config_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(config_mod, "save_config", lambda *a, **k: None)
    return cfg


@pytest.fixture()
def api(monkeypatch, db, bridge, term):
    import app.api.routes as routes
    import app.db.database as database_mod
    import app.live_testing.engine as eng_mod
    import app.mt5.execution as ex
    import app.mt5.factory as factory
    import app.mt5.mt5_real as real
    from fastapi.testclient import TestClient
    from app.main import app

    monkeypatch.setattr(database_mod, "get_db", lambda: db)
    monkeypatch.setattr(routes, "get_db", lambda: db)
    monkeypatch.setattr(eng_mod, "get_db", lambda: db)
    monkeypatch.setattr(factory, "get_bridge", lambda: bridge)
    monkeypatch.setattr(routes, "get_bridge", lambda: bridge)
    monkeypatch.setattr(real, "MT5_PACKAGE_AVAILABLE", True)
    monkeypatch.setattr(ex, "mt5_module", lambda: term)
    monkeypatch.setattr(ex, "_db", lambda: db)
    client = TestClient(app)
    yield client
    from app.live_testing.engine import get_live_testing_engine
    from app.live_testing.workers import get_worker_manager
    get_worker_manager().stop_all(timeout=5.0)     # never leak a worker into the next test
    e = get_live_testing_engine()
    e.stop(interrupted=True, reason="test teardown")
    e.reset()


def _node(db, node_id: int):
    _v43.add_node(db, node_id)


def test_07_api_start_confirms_the_worker_and_stop_confirms_it_stopped(db, live_cfg, api):
    _node(db, 61)
    res = api.post("/api/live-testing/nodes/61/start", json={"confirmed": True})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["is_active"] is True
    assert body["worker"]["state"] == "RUNNING" and body["worker_running"] is True

    # "refresh" — the table/state the frontend rebuilds from
    st = api.get("/api/live-testing/nodes/61/worker").json()
    assert st["running"] is True and st["worker"]["state"] == "RUNNING"

    res = api.post("/api/live-testing/nodes/61/stop")
    assert res.status_code == 200
    body = res.json()
    assert body["is_active"] is False
    assert body["worker"]["running"] is False and body["worker_stopped"] is True

    st = api.get("/api/live-testing/nodes/61/worker").json()
    assert st["running"] is False and st["worker"]["state"] == "STOPPED"


def test_08_api_duplicate_start_returns_the_existing_worker(db, live_cfg, api):
    _node(db, 62)
    first = api.post("/api/live-testing/nodes/62/start", json={"confirmed": True}).json()

    def live_workers():
        return [t for t in threading.enumerate()
                if t.name == "live-test-node-62" and t.is_alive()]

    assert len(live_workers()) == 1
    second = api.post("/api/live-testing/nodes/62/start", json={"confirmed": True}).json()
    assert second["ok"] is True and second["already_running"] is True
    assert second["worker"]["started_at"] == first["worker"]["started_at"]
    assert len(live_workers()) == 1, "a duplicate START must never create a second worker thread"

    # and a duplicate STOP is a no-op that still reports success
    res1 = api.post("/api/live-testing/nodes/62/stop").json()
    res2 = api.post("/api/live-testing/nodes/62/stop").json()
    assert res1["ok"] is True and res1["worker_stopped"] is True
    assert res2["ok"] is True and res2["already_stopped"] is True


def test_09_the_nodes_table_rows_carry_the_backend_worker_state(db, live_cfg, api):
    _node(db, 63)
    table = api.get("/api/live-testing/nodes-table").json()
    rows = {r["node_id"]: r for r in table.get("nodes", table.get("rows", []))}
    assert 63 in rows
    assert rows[63]["worker"]["state"] == "STOPPED"

    api.post("/api/live-testing/nodes/63/start", json={"confirmed": True})
    table = api.get("/api/live-testing/nodes-table").json()
    rows = {r["node_id"]: r for r in table.get("nodes", table.get("rows", []))}
    assert rows[63]["worker"]["running"] is True
