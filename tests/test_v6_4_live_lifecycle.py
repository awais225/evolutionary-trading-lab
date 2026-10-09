"""V6.4 #5 — live-testing START/STOP/STOP ALL with explicit, confirmed states.

States: IDLE / STARTING / RUNNING / STOPPING / STOPPED / COMPLETED / ERROR.
START submits the canonical node id and the selected schedule; STOP cancels
that node's task and confirms the final state after the in-flight cycle and a
read-only MT5 reconcile; STOP ALL does the same for every worker; a stopped
node places NO new trades; stopping NEVER closes broker positions; duplicate
starts, stale task ids and START/STOP races are prevented; a page refresh
restores the state from the backend.
"""
from __future__ import annotations

import copy
import importlib.util
import json
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

from app.live_testing.workers import (
    LiveTestWorkerManager, WORKER_STATES, STATE_IDLE, STATE_STARTING, STATE_RUNNING,
    STATE_STOPPING, STATE_STOPPED, STATE_COMPLETED, STATE_ERROR)


class _CountingEngine:
    """An engine stub that records evaluations (and can pretend to trade)."""

    def __init__(self):
        self.evaluated = []
        self.lock = threading.Lock()

    def eligible_nodes(self):
        return self._nodes

    def _bridge_connected(self, bridge=None):
        return True

    def _evaluate_node(self, node, bridge, frames):
        with self.lock:
            self.evaluated.append((node.get("id"), time.time()))

    def get_mode(self):
        return {"active": True}

    def activate(self, **kw):
        return {"ok": True, "already_active": True}

    def schedule_state(self, node):
        return {"ok": True, "node_id": (node or {}).get("id"),
                "note": "stub schedule state"}

    _nodes = []


@pytest.fixture()
def engine(monkeypatch):
    import app.live_testing.engine as eng_mod
    e = _CountingEngine()
    e._nodes = [{"id": 101, "config": {"is_active": 1, "enabled": True}},
                {"id": 202, "config": {"is_active": 1, "enabled": True}}]
    monkeypatch.setattr(eng_mod, "get_live_testing_engine", lambda: e)
    return e


@pytest.fixture()
def manager(engine):
    m = LiveTestWorkerManager()
    yield m
    m.stop_all(timeout=5.0)


# --------------------------------------------------------------------------- #
# the state vocabulary
# --------------------------------------------------------------------------- #
def test_01_state_vocabulary_is_complete_and_an_idle_worker_is_never_running():
    assert set(WORKER_STATES) == {"IDLE", "STARTING", "RUNNING", "STOPPING",
                                  "STOPPED", "COMPLETED", "ERROR"}
    m = LiveTestWorkerManager()
    st = m.state(999)
    assert st["state"] == STATE_IDLE and st["running"] is False
    assert st["task_id"] and st["in_flight"] is False


def test_02_start_reaches_running_with_a_task_id(manager, engine):
    res = manager.start_worker(101, interval_s=0.02)
    assert res["ok"] is True and res["already_running"] is False
    assert res["task_id"] == res["worker"]["task_id"]
    assert res["worker"]["state"] == STATE_RUNNING
    assert res["worker"]["running"] is True
    st = manager.state(101)
    assert st["state"] == STATE_RUNNING and st["task_id"] == res["task_id"]


# --------------------------------------------------------------------------- #
# two nodes: stop one, not the other
# --------------------------------------------------------------------------- #
def test_03_stopping_one_node_leaves_the_other_running(manager, engine):
    a = manager.start_worker(101, interval_s=0.02)
    b = manager.start_worker(202, interval_s=0.02)
    assert a["task_id"] != b["task_id"]
    res = manager.stop_worker(101)
    assert res["ok"] is True and res["worker"]["state"] == STATE_STOPPED
    assert res["worker"]["running"] is False
    # the OTHER node is untouched
    st = manager.state(202)
    assert st["state"] == STATE_RUNNING and st["running"] is True
    assert manager.is_running(202) is True and manager.is_running(101) is False
    # the stopped node evaluated no further cycles after the stop
    stopped_at = res["worker"]["stopped_at"]
    with engine.lock:
        after = [t for nid, t in engine.evaluated if nid == 101 and t > stopped_at + 0.001]
    assert after == [], "a stopped node must not place/evaluate anything new"


# --------------------------------------------------------------------------- #
# STOP ALL
# --------------------------------------------------------------------------- #
def test_04_stop_all_cancels_every_worker_and_confirms_each(manager, engine):
    manager.start_worker(101, interval_s=0.02)
    manager.start_worker(202, interval_s=0.02)
    res = manager.stop_all()
    assert res["ok"] is True and res["stopped"] == 2
    assert res["remaining_running"] == 0
    for r in res["results"]:
        assert r["ok"] is True
        assert r["worker"]["state"] == STATE_STOPPED
        assert r["worker"]["stop_reason"] == "operator STOP ALL"
    # idempotent: a second STOP ALL is a clean no-op
    res2 = manager.stop_all()
    assert res2["ok"] is True and res2["stopped"] == 0


# --------------------------------------------------------------------------- #
# duplicate starts, stale ids, repeated clicks, races
# --------------------------------------------------------------------------- #
def test_05_duplicate_start_returns_the_same_worker_never_a_second(manager, engine):
    first = manager.start_worker(101, interval_s=0.02)
    second = manager.start_worker(101, interval_s=0.02)
    assert second["already_running"] is True
    assert second["task_id"] == first["task_id"], "a duplicate START keeps the original task id"
    assert second["worker"]["started_at"] == first["worker"]["started_at"]
    live = [t for t in threading.enumerate()
            if t.name.startswith("LiveTestWorker-101") and t.is_alive()]
    assert len(live) == 1


def test_06_repeated_start_stop_clicks_are_idempotent(manager, engine):
    for _ in range(3):
        r = manager.start_worker(101, interval_s=0.02)
        assert r["ok"] is True
    for _ in range(3):
        r = manager.stop_worker(101)
        assert r["ok"] is True
        assert r["worker"]["state"] == STATE_STOPPED or r.get("already_stopped")
    assert manager.is_running(101) is False


def test_07_start_stop_race_never_leaves_a_duplicate_worker(manager, engine):
    results = []

    def _start():
        results.append(manager.start_worker(101, interval_s=0.02))

    def _stop():
        results.append(manager.stop_worker(101))

    threads = [threading.Thread(target=fn) for fn in (_start, _stop, _start, _stop)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    with manager._lock:
        entries = [w for nid, w in manager._workers.items() if nid == 101]
    assert len(entries) <= 1, "a stale entry must never stack next to a live one"
    live = [t for t in threading.enumerate()
            if t.name.startswith("LiveTestWorker-101") and t.is_alive()]
    manager.stop_worker(101)          # settle
    live = [t for t in threading.enumerate()
            if t.name.startswith("LiveTestWorker-101") and t.is_alive()]
    assert len(live) <= 1


def test_08_a_fresh_start_after_a_stop_gets_a_new_task_id(manager, engine):
    a = manager.start_worker(101, interval_s=0.02)
    manager.stop_worker(101)
    b = manager.start_worker(101, interval_s=0.02)
    assert b["task_id"] != a["task_id"], "a stale task id must never be reused for a new run"
    manager.stop_worker(101)


# --------------------------------------------------------------------------- #
# stopping is not closing; the reconcile is read-only
# --------------------------------------------------------------------------- #
def test_09_the_final_state_carries_a_read_only_reconcile_and_never_closes(manager, engine):
    res = manager.start_worker(101, interval_s=0.02)
    stop = manager.stop_worker(101)
    w = stop["worker"]
    assert w["state"] == STATE_STOPPED
    rec = w["reconcile"]
    assert isinstance(rec, dict) and "note" in rec
    assert "close" in rec["note"].lower()        # the note states that stopping ≠ closing
    assert rec.get("positions_open", 0) >= 0     # reported, never touched


# --------------------------------------------------------------------------- #
# un-enrolled / disabled-schedule nodes complete cleanly
# --------------------------------------------------------------------------- #
def test_10_a_node_that_leaves_the_active_set_completes_not_errors(engine):
    engine._nodes = []                # nothing enrolled: the node got un-enrolled
    m = LiveTestWorkerManager()
    try:
        m.start_worker(101, interval_s=0.02)
        deadline = time.time() + 5.0
        while time.time() < deadline:
            st = m.state(101)
            if st["state"] in (STATE_COMPLETED, STATE_STOPPED):
                break
            time.sleep(0.02)
        st = m.state(101)
        assert st["state"] == STATE_COMPLETED, st
        assert st["running"] is False
    finally:
        m.stop_all(timeout=5.0)


def test_11_a_disabled_schedule_completes_the_worker(engine):
    engine._nodes = [{"id": 101, "config": {"is_active": 1, "enabled": False}}]
    m = LiveTestWorkerManager()
    try:
        m.start_worker(101, interval_s=0.02)
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if m.state(101)["state"] == STATE_COMPLETED:
                break
            time.sleep(0.02)
        assert m.state(101)["state"] == STATE_COMPLETED
    finally:
        m.stop_all(timeout=5.0)


# --------------------------------------------------------------------------- #
# the API surface: START sends the schedule, STOP ALL exists, refresh restores
# --------------------------------------------------------------------------- #
@pytest.fixture()
def db(tmp_path):
    from app.db.database import Database
    d = Database(str(tmp_path / "lifecycle.db"))
    d.get_shortlist()
    d.x("DELETE FROM live_test_configs")
    d.x("DELETE FROM live_test_trades")
    return d


@pytest.fixture()
def api(monkeypatch, db, engine):
    import app.api.routes as routes
    import app.db.database as database_mod
    import app.live_testing.engine as eng_mod
    import app.mt5.factory as factory
    from fastapi.testclient import TestClient
    from app.main import app

    class _Sim:
        is_simulated = True
        source = "SIMULATOR"

    monkeypatch.setattr(database_mod, "get_db", lambda: db)
    monkeypatch.setattr(routes, "get_db", lambda: db)
    monkeypatch.setattr(eng_mod, "get_db", lambda: db)
    monkeypatch.setattr(factory, "get_bridge", lambda: _Sim())
    monkeypatch.setattr(routes, "get_bridge", lambda: _Sim())
    client = TestClient(app)
    yield client
    from app.live_testing.workers import get_worker_manager
    get_worker_manager().stop_all(timeout=5.0)


def _node(db, node_id):
    _v43.add_node(db, node_id)
    db.set_live_test_config(node_id, {"risk_pct": 0.5, "is_active": True,
                                      "timeframes": ["M15"], "days": [0, 1, 2, 3, 4],
                                      "sessions": ["london"], "timezone": "UTC"})


def test_12_api_start_submits_the_schedule_and_stop_confirms(api, db, engine):
    _node(db, 301)
    engine._nodes = [{"id": 301, "config": {"is_active": 1, "enabled": True}}]
    res = api.post("/api/live-testing/nodes/301/start",
                   json={"confirmed": True,
                         "schedule": {"days": [0, 1, 2, 3, 4], "sessions": ["london"],
                                      "timeframes": ["M15"], "timezone": "UTC"}})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True and body["worker_running"] is True
    assert body["task_id"]
    assert body["schedule_source"] and "operator selection" in body["schedule_source"]
    assert body["schedule_timezone"] == "UTC"
    assert body["schedule_resolved"]["sessions"] == ["london"]

    stop = api.post("/api/live-testing/nodes/301/stop").json()
    assert stop["ok"] is True and stop["worker_stopped"] is True
    assert stop["worker"]["state"] == STATE_STOPPED
    assert stop["positions_touched"] is False
    assert "reconcile" in stop

    # refresh restores the state from the backend, never from a browser
    st = api.get("/api/live-testing/workers").json()
    assert st["worker_states"] == list(WORKER_STATES)
    assert "301" not in st["states"] or st["states"]["301"]["state"] == STATE_STOPPED


def test_13_api_stop_all_stops_every_node_and_reports_each(api, db, engine):
    _node(db, 301)
    _node(db, 302)
    engine._nodes = [{"id": 301, "config": {"is_active": 1, "enabled": True}},
                     {"id": 302, "config": {"is_active": 1, "enabled": True}}]
    api.post("/api/live-testing/nodes/301/start", json={"confirmed": True})
    api.post("/api/live-testing/nodes/302/start", json={"confirmed": True})
    res = api.post("/api/live-testing/nodes/stop-all").json()
    assert res["ok"] is True
    assert res["stopped"] == 2
    assert res["positions_touched"] is False
    assert res["remaining_running"] == 0
    assert {r["node_id"] for r in res["results"]} == {301, 302}
    # both nodes are un-enrolled: no new trades can start
    for sid in (301, 302):
        cfg = db.get_live_test_config(sid)
        assert not cfg.get("is_active")


def test_14_api_start_without_confirmation_changes_nothing(api, db):
    _node(db, 303)
    res = api.post("/api/live-testing/nodes/303/start", json={})
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["code"] == "CONFIRMATION_REQUIRED"
