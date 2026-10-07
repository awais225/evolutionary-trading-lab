"""V5 §10 — the Power button closes the dashboard and nothing else.

What is verified here:

  * the session record names the processes the dashboard actually started, and
    every public entry point opens the session even without a startup hook;
  * the shutdown order is the one the spec requires, and a shutdown without the
    confirmation phrase does nothing at all;
  * ``dry_run`` performs every check and stops nothing;
  * a REAL shutdown stops only dashboard-owned processes: a registered child is
    terminated and verified gone, while an unrelated process (never registered)
    and an *external* process (recorded but not owned) keep running;
  * a process whose identity cannot be re-verified is never force-killed;
  * DATA is never written by any of this, and the report survives for read-back;
  * the source contains no pattern/name-based broad kill.

Tests never signal the pytest process itself: the backend step is always skipped
or dry-run.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.power import PowerManager, SHUTDOWN_PHRASE, accepting_tasks, pid_alive


@pytest.fixture
def pm(tmp_path):
    """A manager with its session/report files inside the test's tmp dir."""
    return PowerManager(session_file=tmp_path / "session.json",
                        report_file=tmp_path / "report.json")


def _sleeper() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])


# ===========================================================================
# 1. the session record
# ===========================================================================
def test_01_the_session_records_the_backend_and_what_is_never_touched(pm):
    session = pm.start_session()
    assert session["session_id"] and session["backend_pid"] == os.getpid()
    backend = session["owned"]["backend"]
    assert len(backend) == 1 and backend[0]["pid"] == os.getpid()
    assert backend[0]["alive"] is True
    assert backend[0]["launched_by"] == "dashboard"

    never = " ".join(session["never_touched"]).lower()
    for phrase in ("browser", "operating system", "did not start", "mt5", "data"):
        assert phrase in never
    assert session["shutdown_phrase"] == SHUTDOWN_PHRASE


def test_02_the_session_opens_without_a_startup_hook(pm):
    """The Power button must not depend on a lifespan event having run."""
    assert pm.session_id is None
    assert pm.session()["session_id"]                      # opens it on first use
    assert pm.session_id is not None
    assert not pm.plan()[0]["skipped"]


def test_03_the_plan_is_the_required_order(pm):
    steps = [s["step"] for s in pm.plan()]
    assert steps == ["stop_new_tasks", "stop_active_tasks", "disconnect_mt5",
                     "stop_background_workers", "stop_research_workers",
                     "stop_scheduler", "stop_frontend", "stop_backend", "verify"]


def test_04_registering_and_forgetting(pm):
    pm.start_session()
    child = _sleeper()
    try:
        rec = pm.register("research_workers", child.pid, label="pool worker", external=False)
        assert rec["launched_by"] == "dashboard" and rec["external"] is False
        assert [r["pid"] for r in pm.session()["owned"]["research_workers"]] == [child.pid]
        with pytest.raises(ValueError):
            pm.register("browser", child.pid)
    finally:
        child.terminate()
        child.wait(timeout=10)
    dropped = pm.forget_dead()
    assert any(d["pid"] == child.pid for d in dropped)
    assert pm.session()["owned"]["research_workers"] == []


def test_05_external_processes_are_marked_and_must_not_be_owned(pm):
    pm.start_session()
    ext = pm.register("mt5_bridge", os.getpid(), label="MT5 terminal (started by the operator)",
                      external=True)
    assert ext["external"] is True and ext["launched_by"] == "operator"


# ===========================================================================
# 2. confirmation and dry run
# ===========================================================================
def test_06_without_the_phrase_nothing_happens(pm):
    pm.start_session()
    child = _sleeper()
    try:
        pm.register("background_workers", child.pid, label="test worker")
        res = pm.shutdown(confirmed=False, dry_run=False, skip=("stop_backend",))
        assert res["ok"] is False and res["code"] == "CONFIRMATION_REQUIRED"
        assert res["executed"] is False
        assert pid_alive(child.pid)                       # untouched
        assert accepting_tasks() is True                  # still accepting tasks
    finally:
        child.terminate()
        child.wait(timeout=10)


def test_07_a_dry_run_stops_nothing(pm):
    pm.start_session()
    child = _sleeper()
    try:
        pm.register("research_workers", child.pid, label="pool worker")
        res = pm.shutdown(confirmed=True, dry_run=True, skip=("stop_backend",))
        assert res["dry_run"] is True and res["backend_stopping"] is False
        assert pid_alive(child.pid)
        assert pid_alive(os.getpid())
        steps = {s["step"]: s for s in res["steps"]}
        assert steps["stop_research_workers"]["status"] == "OK"
        assert steps["stop_research_workers"]["result"]["dry_run"] is True
        assert steps["stop_backend"]["status"] == "SKIPPED"      # excluded from this run
    finally:
        child.terminate()
        child.wait(timeout=10)
    # a dry run still leaves an audit report behind
    assert json.loads(pm.report_file.read_text())["dry_run"] is True


# ===========================================================================
# 3. a real shutdown stops owned processes only
# ===========================================================================
def test_08_a_real_shutdown_stops_the_owned_child_and_nothing_else(pm):
    pm.start_session()
    owned = _sleeper()
    unrelated = _sleeper()          # never registered: must survive
    external = _sleeper()           # recorded but not launched by the dashboard
    try:
        pm.register("research_workers", owned.pid, label="pool worker")
        pm.register("mt5_bridge", external.pid, label="MT5 terminal", external=True)

        res = pm.shutdown(confirmed=True, dry_run=False, skip=("stop_backend",),
                          term_timeout_s=10.0, verify_timeout_s=10.0)
        assert res["ok"] is True, res
        steps = {s["step"]: s for s in res["steps"]}
        assert steps["stop_research_workers"]["result"]["terminated"][0]["pid"] == owned.pid
        time.sleep(0.2)
        assert not pid_alive(owned.pid)                 # it really stopped
        assert pid_alive(unrelated.pid)                 # never touched
        assert pid_alive(external.pid)                  # external: disconnected, not killed
        assert steps["disconnect_mt5"]["result"]["terminal_process_signalled"] is False
        assert steps["stop_frontend"]["result"]["browsers_touched"] == 0
        verify = steps["verify"]["result"]
        assert verify["data_touched"] is False and verify["browser_touched"] is False
        assert verify["verified"].get(f"research_workers:{owned.pid}") == "gone"
        assert pid_alive(os.getpid())                   # the backend step was skipped
        assert accepting_tasks() is False               # no new tasks are accepted
    finally:
        for p in (owned, unrelated, external):
            if p.poll() is None:
                p.terminate()
            try:
                p.wait(timeout=10)
            except Exception:
                p.kill()


def test_09_an_unverified_identity_is_never_force_killed(pm):
    pm.start_session()
    child = _sleeper()
    try:
        # a record whose start time does not match the process actually running
        pm.register("frontend", child.pid, label="dashboard frontend",
                    start_time=time.time() - 86400, external=False)
        res = pm.shutdown(confirmed=True, dry_run=False, skip=("stop_backend",),
                          term_timeout_s=0.2, verify_timeout_s=0.5)
        front = next(s for s in res["steps"] if s["step"] == "stop_frontend")
        assert front["result"]["signalled"] == [] and front["result"]["skipped_external"], front
        assert "reused" in front["result"]["skipped_external"][0]["reason"]
        assert pid_alive(child.pid)                     # not killed on a stale record
    finally:
        child.terminate()
        child.wait(timeout=10)


def test_10_a_shutdown_cancels_queued_runs_without_deleting_them(pm, monkeypatch):
    """Queued work is stopped through the run module's own transition."""
    pm.start_session()
    calls = []

    class _Runs:
        RUN_TABLE = "mt5_historical_runs"

        @staticmethod
        def _update_from(db, run_id, from_statuses, **cols):
            calls.append((run_id, from_statuses, cols.get("status")))
            return 1

    class _DB:
        @staticmethod
        def q(sql, *a):
            return [{"run_id": "r-1", "status": "QUEUED"}, {"run_id": "r-2", "status": "RUNNING"}]

        @staticmethod
        def log_event(*a, **k):
            return None

    import app.db.database as database_mod
    import app.historical_backtest.runs as hb_mod
    monkeypatch.setattr(database_mod, "get_db", lambda: _DB())
    monkeypatch.setattr(hb_mod, "_update_from", _Runs._update_from)
    monkeypatch.setattr(hb_mod, "RUN_TABLE", "mt5_historical_runs")

    res = pm.shutdown(confirmed=True, dry_run=False, skip=("stop_backend", "stop_research_workers",
                                                          "disconnect_mt5", "stop_background_workers"))
    step = next(s for s in res["steps"] if s["step"] == "stop_scheduler")["result"]
    assert step["cancelled_queued"] == ["r-1"]          # QUEUED -> CANCELLED
    assert step["left_running"] == ["r-2"]              # RUNNING is stopped by the lab, not by SQL
    assert step["deleted_rows"] == 0
    assert calls == [("r-1", ("QUEUED",), "CANCELLED")]


def test_11_the_report_is_written_and_readable(pm):
    pm.start_session()
    pm.shutdown(confirmed=True, dry_run=True, skip=("stop_backend",))
    stored = pm.last_report()
    assert stored["session_id"] == pm.session_id
    assert stored["dry_run"] is True
    assert [s["step"] for s in stored["steps"]][-1] == "verify"
    assert "browser and its tabs" in " ".join(stored["never_touched"])


# ===========================================================================
# 4. the API surface + the no-broad-kill guarantee
# ===========================================================================
def test_12_the_power_api_reports_the_session_and_requires_confirmation(client, monkeypatch,
                                                                        tmp_path):
    import app.power as power_mod
    monkeypatch.setattr(power_mod, "SESSION_FILE", tmp_path / "s.json")
    monkeypatch.setattr(power_mod, "REPORT_FILE", tmp_path / "r.json")
    fresh = power_mod.PowerManager(session_file=tmp_path / "s.json",
                                   report_file=tmp_path / "r.json")
    monkeypatch.setattr(power_mod, "_MANAGER", fresh)

    session = client.get("/api/power/session")
    assert session.status_code == 200
    body = session.json()
    assert body["backend_pid"] == os.getpid()
    assert body["plan"][0]["step"] == "stop_new_tasks"
    assert body["shutdown_phrase"] == SHUTDOWN_PHRASE

    refused = client.post("/api/power/shutdown", json={"confirm": "yes"})
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "CONFIRMATION_REQUIRED"

    dry = client.post("/api/power/shutdown",
                      json={"confirm": SHUTDOWN_PHRASE, "dry_run": True})
    assert dry.status_code == 200, dry.text
    report = dry.json()
    assert report["dry_run"] is True and report["backend_stopping"] is False
    assert pid_alive(os.getpid())                       # the API never killed the backend

    read_back = client.get("/api/power/shutdown-report").json()
    assert read_back["session_id"] == report["session_id"]


def test_13_new_tasks_are_refused_once_a_shutdown_starts(client, monkeypatch, tmp_path):
    import app.power as power_mod
    fresh = power_mod.PowerManager(session_file=tmp_path / "s.json",
                                   report_file=tmp_path / "r.json")
    monkeypatch.setattr(power_mod, "_MANAGER", fresh)
    try:
        fresh.shutdown(confirmed=True, dry_run=True, skip=("stop_backend",))
        assert accepting_tasks() is False
        res = client.post("/api/research-run/start-fresh", json={"mode": "reset_only"})
        assert res.status_code == 503
        assert res.json()["detail"]["code"] == "SHUTTING_DOWN"
        run = client.post("/api/mt5-historical/runs", json={"symbol": "XAUUSD", "timeframe": "M15"})
        assert run.status_code == 503
    finally:
        # leave the process able to run the rest of the suite
        with power_mod._STATE_LOCK:
            power_mod._STATE["accepting_tasks"] = True
        with power_mod._STATE_LOCK:
            power_mod._STATE["shutdown_started_at"] = None


def test_14_there_is_no_broad_or_name_based_kill_anywhere_in_the_power_module():
    """Checked structurally (AST), so comments and prose cannot satisfy it."""
    import ast

    src = Path("backend/app/power.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    literals = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
                and isinstance(n.value, str)]
    for forbidden in ("killall", "pkill", "/IM ", "taskkill /IM", "os.system(", "sh -c"):
        assert not any(forbidden in lit for lit in literals), \
            f"power.py must not build a command containing {forbidden!r}"
    # the taskkill command is built from a pid, never from an image name
    assert any('taskkill' in lit for lit in literals)
    assert any('/PID' in lit for lit in literals)
    assert not any(lit.strip() == '/F' for lit in literals if 'IM' in lit)
    # no os.system / subprocess with shell=True anywhere in the module
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = getattr(fn, "attr", None) or getattr(fn, "id", None)
            assert name != "system", "os.system must never be used"
            for kw in node.keywords:
                assert not (kw.arg == "shell" and getattr(kw.value, "value", False) is True), \
                    "shell=True must never be used"


def test_15_the_shutdown_never_touches_data_or_the_repository(pm, monkeypatch):
    """No DATA file and no git-tracked path is written by a shutdown."""
    import app.power as power_mod
    touched = []
    real_write = Path.write_text

    def spy(self, *a, **k):
        touched.append(str(self))
        return real_write(self, *a, **k)
    monkeypatch.setattr(Path, "write_text", spy)

    pm.start_session()
    pm.shutdown(confirmed=True, dry_run=True, skip=("stop_backend",))
    for path in touched:
        assert "DATA" not in path or "power_session" in path or "power_shutdown" in path
        assert ".git" not in path
    assert all("lab_state.db" not in p for p in touched)
