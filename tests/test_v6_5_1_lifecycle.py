"""V6.5.1 §2/§11 — lifecycle transitions and the /api/nodes lifecycle payload.

Deterministic and offline: the worker's trading cycle is stubbed (no broker,
no orders), while the state machine, the thread, the manager registry and the
API endpoints are the REAL ones. Covers STOPPED -> STARTING -> RUNNING ->
STOPPING -> STOPPED, failure handling, duplicate-request idempotency, node
independence, and the regression that caused the V6.5 UI defect: `GET /api/nodes`
must carry the worker state + lifecycle the Nodes table renders from.
"""
from __future__ import annotations

import time

import pytest

from app.live_testing.workers import (
    LiveTestWorkerManager,
    STATE_COMPLETED,
    STATE_ERROR,
    STATE_IDLE,
    STATE_RUNNING,
    STATE_STARTING,
    STATE_STOPPED,
    STATE_STOPPING,
    WORKER_STATES,
)


@pytest.fixture(autouse=True)
def _stub_cycles(monkeypatch):
    """One bad or busy cycle must never touch a broker in tests."""
    from app.live_testing import workers as W

    def _quiet_cycle(self):
        time.sleep(0.02)
        return None

    monkeypatch.setattr(W.NodeLiveWorker, "_cycle", _quiet_cycle)

    def _quiet_reconcile(self):
        return {"checked": False, "positions": [], "orders": [],
                "note": "test run - no broker involved"}

    monkeypatch.setattr(W.NodeLiveWorker, "_reconcile", _quiet_reconcile)


def _wait_state(worker, wanted, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = worker.state_dict().get("state")
        if callable(wanted):
            if wanted(st):
                return st
        elif st == wanted:
            return st
        time.sleep(0.02)
    return worker.state_dict().get("state")


# --------------------------------------------------------------------------- #
# the worker state machine itself (V6.5.1 §2 lifecycle model)
# --------------------------------------------------------------------------- #
def test_01_stopped_starting_running_stopping_stopped():
    wm = LiveTestWorkerManager()
    # never started -> IDLE (mapped to STOPPED by the API layer)
    st = wm.state(718)
    assert st["state"] == STATE_IDLE and st["running"] is False

    res = wm.start_worker(718)
    assert res["ok"] is True
    w = wm._workers[718]
    got = _wait_state(w, STATE_RUNNING)
    assert got == STATE_RUNNING, f"worker never reached RUNNING (state={got})"
    assert wm.is_running(718) is True

    # STOP: the final state is only reported after the thread finished
    res = wm.stop_worker(718, reason="test STOP")
    assert res["ok"] is True
    assert res["worker"]["running"] is False
    assert res["worker"]["state"] == STATE_STOPPED
    assert res["worker"]["stop_reason"] == "test STOP"
    assert wm.is_running(718) is False
    # the final-state entry is retained so a refresh keeps reporting the truth
    assert wm.state(718)["state"] == STATE_STOPPED


def test_02_duplicate_start_is_idempotent_and_duplicate_stop_is_a_noop():
    wm = LiveTestWorkerManager()
    r1 = wm.start_worker(719)
    assert r1["ok"] is True
    _wait_state(wm._workers[719], STATE_RUNNING)
    r2 = wm.start_worker(719)
    assert r2["ok"] is True and r2["already_running"] is True
    assert r2["task_id"] == r1["task_id"]          # same worker, never duplicated
    assert len([1 for _n, w in wm._all() if w.alive]) == 1

    s1 = wm.stop_worker(719)
    assert s1["ok"] is True and s1["already_stopped"] is False
    s2 = wm.stop_worker(719)
    assert s2["ok"] is True and s2["already_stopped"] is True
    assert s2["worker"]["state"] == STATE_STOPPED


def test_03_start_failure_reports_error_and_leaves_no_phantom_worker(monkeypatch):
    wm = LiveTestWorkerManager()

    def _fail_start(self):
        self.last_error = "simulated start failure"
        with self._state_lock:
            self._state = STATE_ERROR
        return False

    monkeypatch.setattr("app.live_testing.workers.NodeLiveWorker.start", _fail_start)
    res = wm.start_worker(720)
    assert res["ok"] is False
    assert "simulated start failure" in (res.get("error") or "")
    # no half-alive entry is kept: a later START can create a fresh worker
    assert wm.state(720)["state"] in (STATE_IDLE, STATE_ERROR)


def test_04_stop_failure_does_not_claim_stopped(monkeypatch):
    wm = LiveTestWorkerManager()
    wm.start_worker(721)
    w = wm._workers[721]
    _wait_state(w, STATE_RUNNING)

    def _refuse_join(timeout=10.0):
        return None                                    # thread never exits

    monkeypatch.setattr(w, "join", _refuse_join)
    monkeypatch.setattr(w._thread, "is_alive", lambda: True)   # thread refuses to die
    res = wm.stop_worker(721)
    assert res["ok"] is False, "a stop that could not be confirmed must not report success"
    assert res["worker"]["state"] == STATE_STOPPING, \
        "an unconfirmed stop must keep showing STOPPING, never STOPPED"


def test_05_multiple_nodes_are_independent_stopping_one_never_stops_another():
    wm = LiveTestWorkerManager()
    wm.start_worker(722)
    wm.start_worker(723)
    _wait_state(wm._workers[722], STATE_RUNNING)
    _wait_state(wm._workers[723], STATE_RUNNING)

    wm.stop_worker(722)
    assert wm.state(722)["state"] == STATE_STOPPED
    assert wm.is_running(723) is True, "stopping node 722 must not stop node 723"

    wm.stop_all(reason="test STOP ALL")
    assert wm.is_running(723) is False
    assert wm.state(723)["state"] == STATE_STOPPED


# --------------------------------------------------------------------------- #
# GET /api/nodes — the payload the unified Nodes table renders from
# --------------------------------------------------------------------------- #
def _row_for(client, node_id):
    r = client.get("/api/nodes", params={"filter": "all", "limit": 0})
    assert r.status_code == 200
    for row in r.json().get("nodes") or []:
        if int(row.get("node_id")) == int(node_id):
            return row
    return None


def test_06_nodes_index_rows_carry_worker_and_lifecycle(client):
    """REGRESSION for the V6.5 defect: the index must include the worker state.

    V6.5 put these fields only on the legacy /live-testing/nodes-table
    projection; the Live Testing Nodes table consumes /api/nodes, so START
    never saw RUNNING and flipped back to START after every reload.
    """
    r = client.get("/api/nodes", params={"filter": "all", "limit": 0})
    assert r.status_code == 200
    rows = r.json().get("nodes") or []
    if not rows:
        pytest.skip("no nodes in this DATA snapshot")
    for row in rows:
        assert "worker" in row, "every /api/nodes row must carry its worker state"
        assert "lifecycle" in row
        assert "is_active" in row
        w = row["worker"]
        assert isinstance(w, dict) and "state" in w and "running" in w
        assert row["lifecycle"] in ("STOPPED", "STARTING", "RUNNING", "STOPPING",
                                    "COMPLETED", "ERROR", "UNKNOWN", "IDLE")


def test_07_nodes_index_rows_carry_limits_offsets_and_risk_mode(client):
    r = client.get("/api/nodes", params={"filter": "all", "limit": 0})
    rows = r.json().get("nodes") or []
    if not rows:
        pytest.skip("no nodes in this DATA snapshot")
    for row in rows:
        for key in ("max_active_trades", "max_active_trades_source",
                    "max_active_trades_effective", "max_active_trades_default",
                    "sl_offset_pips", "tp_offset_pips", "offsets_active", "risk_mode"):
            assert key in row, f"/api/nodes row is missing {key}"
        rm = row["risk_mode"]
        assert rm["mode"] in ("PERCENT", "AMOUNT")
        assert rm["capital_basis"] in ("EQUITY", "BALANCE")


def test_08_lifecycle_mapping_idle_is_truthful(client):
    """never-started + not enrolled reads STOPPED; enrolled-with-no-worker reads
    UNKNOWN (never a false RUNNING/STOPPED claim)."""
    from app.api.routes import _lifecycle_of
    idle = {"state": STATE_IDLE, "running": False}
    assert _lifecycle_of(idle, is_active=False, cfg_status="STOPPED") == "STOPPED"
    assert _lifecycle_of(idle, is_active=True, cfg_status="RUNNING") == "UNKNOWN"
    assert _lifecycle_of({"state": STATE_RUNNING, "running": True}, True, "RUNNING") == "RUNNING"
    assert _lifecycle_of({"state": STATE_STOPPING}, True, "RUNNING") == "STOPPING"
    assert _lifecycle_of({"state": STATE_ERROR, "last_error": "x"}, False, "ERROR") == "ERROR"
    assert _lifecycle_of({"state": "UNKNOWN"}, True, "RUNNING") == "UNKNOWN"


def test_09_start_and_stop_update_authoritative_state_for_the_index(client, monkeypatch):
    """Real start/stop endpoints (the cycle body is stubbed, the machinery is
    real): the response confirms the worker, the config flips, and the index
    then reports the same lifecycle."""
    from app.live_testing.workers import get_worker_manager

    # pick a real node
    r = client.get("/api/nodes", params={"filter": "all", "limit": 1})
    rows = r.json().get("nodes") or []
    if not rows:
        pytest.skip("no nodes in this DATA snapshot")
    sid = int(rows[0]["node_id"])

    # a START needs a deliberate schedule provenance path; submit one explicitly
    sched = {"days": [0, 1, 2, 3, 4], "sessions": ["london"],
             "timeframes": [rows[0].get("timeframe") or "M15"], "timezone": "UTC"}
    res = client.post(f"/api/live-testing/nodes/{sid}/start",
                      json={"confirmed": True, "schedule": sched})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["is_active"] is True
    assert body["worker_running"] is True, "START must confirm the worker is RUNNING"
    assert body["worker"]["state"] == STATE_RUNNING

    row = _row_for(client, sid)
    assert row is not None
    assert row["lifecycle"] == "RUNNING", "the index must show RUNNING after a confirmed START"
    assert row["is_active"] is True

    res = client.post(f"/api/live-testing/nodes/{sid}/stop")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["worker_stopped"] is True
    assert body["worker"]["state"] == STATE_STOPPED
    assert body["positions_touched"] is False

    row = _row_for(client, sid)
    assert row["lifecycle"] == "STOPPED", "the index must show STOPPED after a confirmed STOP"
    assert row["is_active"] is False

    # idempotent repeat STOP
    res = client.post(f"/api/live-testing/nodes/{sid}/stop")
    assert res.status_code == 200
    assert res.json()["already_stopped"] is True


def test_10_start_failure_endpoint_reports_error_and_unenrolls(client, monkeypatch):
    r = client.get("/api/nodes", params={"filter": "all", "limit": 2})
    rows = r.json().get("nodes") or []
    if len(rows) < 1:
        pytest.skip("no nodes in this DATA snapshot")
    sid = int(rows[-1]["node_id"])

    from app.live_testing import workers as W

    def _fail_start(self):
        self.last_error = "simulated start failure"
        return False

    monkeypatch.setattr(W.NodeLiveWorker, "start", _fail_start)
    sched = {"days": [0, 1, 2, 3, 4], "sessions": ["london"],
             "timeframes": ["M15"], "timezone": "UTC"}
    res = client.post(f"/api/live-testing/nodes/{sid}/start",
                      json={"confirmed": True, "schedule": sched})
    assert res.status_code == 422
    body = res.json()["detail"]
    assert body.get("code") == "WORKER_START_FAILED"
    # the node is NOT left enrolled
    assert body.get("worker") is not None or body.get("error")
    from app.api.routes import get_db
    cfg = get_db().get_live_test_config(sid) or {}
    assert not cfg.get("is_active"), "a failed START must not leave the node enrolled"
