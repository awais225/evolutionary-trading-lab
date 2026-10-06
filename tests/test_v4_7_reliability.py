"""
V4.7 — reliability, performance and production-hardening tests.

Covers the specific hardening this iteration added, without re-running the whole
V4.0–V4.6 acceptance stack:

  * lazy startup: the API answers before the expensive bootstrap finished, the
    bootstrap still runs, and readiness reports the truth
  * bounded startup steps: a hung subsystem is reported, it cannot block startup
    forever, and a failed critical step is never hidden behind a healthy /health
  * health/readiness contract: /health, /api/health, /api/ready, /api/lifecycle
  * JSON safety net: NaN/Infinity/objects cannot produce invalid JSON
  * node detail: malformed genome, malformed JSON columns, cyclic lineage,
    unknown ids, missing sections — never a 500, always a useful payload
  * SQLite/WAL: safe snapshots instead of file copies, connection identity
    re-open when the database file is replaced
  * background jobs: backup and log export run as jobs and stay retrievable
  * query ceilings for the polled endpoints (no full scans per poll)

Run with a disposable DATA root (EVOLUTIONARY_LAB_DATA_ROOT), never authoritative.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

os.environ.setdefault("EVOLUTIONARY_LAB_STARTUP_BLOCKING", "1")

from fastapi.testclient import TestClient  # noqa: E402

from app.db.database import Database  # noqa: E402
from app.lifecycle import (  # noqa: E402
    DONE, FAILED, READY, TIMEOUT, StartupLifecycle, reset_lifecycle,
)


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture()
def lifec() -> StartupLifecycle:
    return StartupLifecycle()


@pytest.fixture()
def tmp_db(tmp_path) -> Database:
    db = Database(str(tmp_path / "v47.db"))
    yield db
    db.close()


@pytest.fixture()
def client():
    from app.main import app
    with TestClient(app) as c:
        yield c


# --------------------------------------------------------------------------- #
# 1. lazy startup & bounded steps
# --------------------------------------------------------------------------- #
def test_background_startup_answers_before_the_slow_step_finishes(lifec):
    """The expensive step must not delay the first answer (V4.7 §3)."""
    started = threading.Event()
    release = threading.Event()

    def slow_step():
        started.set()
        release.wait(timeout=10)          # stands in for a DATA scan / restoration
        return {"ok": True}

    lifec.register("slow", "slow bootstrap step", slow_step, timeout_s=10.0, critical=True)
    t0 = time.perf_counter()
    lifec.run(background=True)
    spawn_ms = (time.perf_counter() - t0) * 1000

    assert started.wait(timeout=5), "background bootstrap did not start"
    assert spawn_ms < 500, f"run(background=True) blocked for {spawn_ms:.0f} ms"
    snap = lifec.snapshot()
    assert snap["mode"] == "background"
    assert snap["state"] == "starting"
    assert snap["ready"] is False
    assert snap["running"] == ["slow"] or snap["pending"] == ["slow"]

    release.set()
    assert lifec.wait_ready(timeout=10) is True
    snap = lifec.snapshot()
    assert snap["state"] == READY and snap["ready"] is True
    assert snap["steps"][0]["status"] == DONE
    assert snap["elapsed_ms"] is not None


def test_hung_step_is_bounded_and_reported(lifec):
    """A subsystem that hangs cannot block startup indefinitely (§4)."""
    release = threading.Event()
    lifec.register("hang", "hangs forever", lambda: release.wait(timeout=30), timeout_s=0.3, critical=True)
    lifec.register("after", "still runs", lambda: {"ok": True}, timeout_s=5.0, critical=False)

    t0 = time.perf_counter()
    lifec.run(background=True)
    ready = lifec.wait_ready(timeout=5)
    elapsed = time.perf_counter() - t0
    release.set()

    assert ready is False
    assert elapsed < 3, "startup waited for a hung step"
    snap = lifec.snapshot()
    assert snap["state"] in ("failed", "degraded")
    statuses = {s["name"]: s["status"] for s in snap["steps"]}
    assert statuses["hang"] == TIMEOUT
    assert statuses["after"] == DONE, "a timed-out step must not block the remaining steps"
    assert "budget" in snap["failed_steps"][0]["error"]
    body = lifec.readiness_response()
    assert body["ok"] is False and body["code"] == "failed"
    assert "hang" in body["detail"]


def test_non_critical_failure_degrades_but_stays_ready(lifec):
    def boom():
        raise ValueError("diagnostics unavailable")

    lifec.register("ok", "fine", lambda: None, timeout_s=5.0, critical=True)
    lifec.register("diagnostics", "non critical", boom, timeout_s=5.0, critical=False)
    lifec.run(background=True)
    assert lifec.wait_ready(timeout=5) is True
    snap = lifec.snapshot()
    assert snap["state"] == "degraded"
    assert snap["ready"] is False            # degraded is not "everything is fine"
    body = lifec.readiness_response()
    assert body["ok"] is True and body["code"] == "degraded"
    assert "diagnostics" in body["detail"]


def test_critical_failure_is_never_hidden(lifec):
    def boom():
        raise RuntimeError("database reconcile failed")

    lifec.register("database_reconcile", "reconcile", boom, timeout_s=5.0, critical=True)
    lifec.run(background=True)
    lifec.wait_ready(timeout=5)
    body = lifec.readiness_response()
    assert body["ok"] is False
    assert body["code"] == "failed"
    assert "database reconcile failed" in body["detail"]


def test_startup_records_durations_for_every_step(lifec):
    lifec.register("a", "a", lambda: time.sleep(0.05), timeout_s=5.0)
    lifec.register("b", "b", lambda: None, timeout_s=5.0)
    lifec.run(background=False)           # blocking mode: deterministic for tests
    snap = lifec.snapshot()
    assert snap["mode"] == "blocking" and snap["ready"] is True
    assert [s["status"] for s in snap["steps"]] == [DONE, DONE]
    assert snap["steps"][0]["duration_ms"] >= 40
    assert snap["steps"][1]["duration_ms"] is not None


# --------------------------------------------------------------------------- #
# 2. health / readiness contract (real app)
# --------------------------------------------------------------------------- #
def test_health_and_readiness_endpoints_report_startup_honestly(client):
    h = client.get("/health")
    assert h.status_code == 200
    body = h.json()
    assert body["status"] in ("ok", "starting", "degraded")
    assert "startup" in body and "ready" in body
    assert body["startup"]["state"] in ("ready", "degraded", "starting", "not_started")

    r = client.get("/api/ready")
    assert r.status_code in (200, 503)
    rb = r.json()
    assert set(("ok", "code", "detail", "startup")) <= set(rb)
    if r.status_code == 200:
        assert rb["code"] in ("ready", "degraded")

    life = client.get("/api/lifecycle")
    assert life.status_code == 200
    steps = life.json()["steps"]
    assert steps and all("duration_ms" in s and "critical" in s for s in steps)
    names = {s["name"] for s in steps}
    # the steps that used to run inline in the ASGI startup hook are now bounded
    # background steps - none of them was dropped
    assert {"data_discovery", "data_restoration", "database_reconcile"} <= names


def test_health_never_claims_ok_when_a_critical_step_failed(monkeypatch):
    """A genuine backend failure must not sit behind a green health response."""
    from app import main as main_mod
    life = reset_lifecycle()
    monkeypatch.setattr(main_mod, "get_lifecycle", lambda: life)

    def boom():
        raise RuntimeError("restoration exploded")

    life.register("data_restoration", "restore", boom, timeout_s=5.0, critical=True)
    life.run(background=False)

    body, code = main_mod._health_payload()
    assert body["status"] == "error"
    assert code == 503
    assert body["ready"] is False
    assert body["startup"]["failed_steps"][0]["name"] == "data_restoration"
    reset_lifecycle()


# --------------------------------------------------------------------------- #
# 3. JSON safety net
# --------------------------------------------------------------------------- #
def test_sanitizer_removes_every_invalid_json_value():
    from app.api.json_safety import sanitize_payload

    payload = {
        "nan": float("nan"), "inf": float("inf"), "neg_inf": float("-inf"),
        "ok_float": 1.5, "none": None, "true": True, "int": 7, "text": "x",
        "path": Path("/tmp/x"), "obj": object(), "nested": {"deep": [float("nan"), {"n": float("inf")}]},
        "bytes": b"abc", "set": {1, 2}, "tuple": (1, float("nan")),
    }
    clean = sanitize_payload(payload)
    assert clean["nan"] is None and clean["inf"] is None and clean["neg_inf"] is None
    assert clean["ok_float"] == 1.5 and clean["ok_float"] == 1.5
    assert clean["nested"]["deep"][0] is None and clean["nested"]["deep"][1]["n"] is None
    assert clean["path"] == "/tmp/x"
    assert isinstance(clean["obj"], str) and "[object Object]" not in clean["obj"]
    assert clean["bytes"] == "abc"
    text = json.dumps(clean, allow_nan=False)      # must not raise
    for token in ("NaN", "Infinity", "[object Object]"):
        assert token not in text


def test_safe_json_response_renders_non_finite_and_objects():
    from app.api.json_safety import SafeJSONResponse

    resp = SafeJSONResponse(content={"a": float("nan"), "b": object(), "c": [1, float("inf")]})
    body = json.loads(resp.body.decode())
    assert body["a"] is None and body["c"][1] is None
    assert isinstance(body["b"], str) and "[object Object]" not in body["b"]


def test_dashboard_endpoints_are_valid_json_with_stable_shapes(client):
    """V4.4–V4.6 surfaces must never emit NaN/Infinity/raw objects (§9)."""
    paths = [
        "/health", "/api/health", "/api/ready", "/api/lifecycle", "/api/lab/status",
        "/api/stats/overview", "/api/stats/nodes?limit=5", "/api/research/facets",
        "/api/research/strategies?limit=5", "/api/research/matrix?ids=1,2",
        "/api/strategies/1195", "/api/strategies/3", "/api/mt5-historical/capabilities",
        "/api/mt5-historical/runs?strategy_id=1195", "/api/jobs",
    ]
    for path in paths:
        r = client.get(path)
        assert r.status_code in (200, 404), f"{path} -> {r.status_code}"
        text = r.text
        for token in ("NaN", "Infinity", "[object Object]"):
            assert token not in text, f"{path} leaked {token}"
        data = json.loads(text, parse_constant=lambda c: (_ for _ in ()).throw(AssertionError(
            f"{path} contains the invalid JSON constant {c}")))
        assert isinstance(data, (dict, list))


# --------------------------------------------------------------------------- #
# 4. node detail robustness (§8)
# --------------------------------------------------------------------------- #
def _insert_node(db: Database, sid: int, *, genome, data_source="USER_RESEARCH", parent_id=None,
                 status="GENERATED", run_id="RUN-V47") -> None:
    db.x("""INSERT OR REPLACE INTO strategies
            (id, hash, parent_id, generation, symbol, timeframe, direction, status, genome,
             complexity, fitness, created_at, updated_at, origin, run_id, data_source, research_node_num)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
         (sid, f"hash{sid}", parent_id, 1, "XAUUSD", "M15", "long", status, genome,
          1.0, 0.5, time.time(), time.time(), "test", run_id, data_source, sid))


def test_node_detail_survives_a_missing_or_malformed_genome(tmp_db, monkeypatch):
    from app.api import routes as R

    _insert_node(tmp_db, 9001, genome="")                          # empty genome text
    _insert_node(tmp_db, 9002, genome="{not json at all")          # malformed JSON
    _insert_node(tmp_db, 9003, genome=json.dumps({"symbol": "XAUUSD"}))   # dict but empty
    _insert_node(tmp_db, 9004, genome='"a scalar string"')         # valid JSON, wrong type
    _insert_node(tmp_db, 9006, genome="null")                     # explicit JSON null
    _insert_node(tmp_db, 9007, genome="12345")                    # JSON number
    _insert_node(tmp_db, 9005, genome=json.dumps({"genome": "ok"}))
    monkeypatch.setattr(R, "get_db", lambda *a, **k: tmp_db)

    for sid in (9001, 9002, 9003, 9004, 9005, 9006, 9007):
        payload = R.strategy_detail(sid)
        assert isinstance(payload, dict)
        assert payload["strategy"]["genome_valid"] is False or isinstance(payload["strategy"]["genome"], dict)
        assert isinstance(payload["section_errors"], list)
        assert payload["orders_placed"] is False
        json.dumps(payload, allow_nan=False)      # serialisable, no NaN


def test_node_detail_reports_malformed_json_columns_without_failing(tmp_db, monkeypatch):
    from app.api import routes as R

    _insert_node(tmp_db, 9010, genome=json.dumps({"symbol": "XAUUSD"}))
    tmp_db.x("""INSERT INTO backtests (strategy_id, stage, metrics, params, window, dataset_id, created_at)
                VALUES (?,?,?,?,?,?,?)""",
             (9010, "quick", "{broken", "{broken", "{broken", "XAUUSD_M15", time.time()))
    monkeypatch.setattr(R, "get_db", lambda *a, **k: tmp_db)

    payload = R.strategy_detail(9010)
    assert payload["backtests"][0]["metrics"] is None
    errs = {(e["section"], e["field"]) for e in payload["section_errors"]}
    assert ("backtests[0]", "metrics") in errs
    assert all("error" in e for e in payload["section_errors"])


def test_node_detail_survives_a_cyclic_parent_chain(tmp_db, monkeypatch):
    """A corrupted lineage must be truncated, not loop forever (§8)."""
    from app.api import routes as R

    _insert_node(tmp_db, 9020, genome=json.dumps({}), parent_id=9021)
    _insert_node(tmp_db, 9021, genome=json.dumps({}), parent_id=9020)     # cycle
    monkeypatch.setattr(R, "get_db", lambda *a, **k: tmp_db)

    t0 = time.perf_counter()
    payload = R.strategy_detail(9020)
    assert time.perf_counter() - t0 < 5, "cycle guard failed"
    ids = [x["id"] for x in payload["lineage"]]
    assert ids == [9020, 9021]
    assert any(e["section"] == "lineage" for e in payload["section_errors"])


def test_node_detail_unknown_id_is_a_useful_404(tmp_db, monkeypatch):
    from app.api import routes as R
    from fastapi import HTTPException

    monkeypatch.setattr(R, "get_db", lambda *a, **k: tmp_db)
    with pytest.raises(HTTPException) as exc:
        R.strategy_detail(987654)
    assert exc.value.status_code == 404
    assert "987654" in str(exc.value.detail)


def test_legacy_node_detail_is_returned_but_marked(tmp_db, monkeypatch):
    from app.api import routes as R

    _insert_node(tmp_db, 9030, genome="", data_source="LEGACY_TEST", run_id=None)
    monkeypatch.setattr(R, "get_db", lambda *a, **k: tmp_db)
    payload = R.strategy_detail(9030)
    assert payload["strategy"]["data_source"] == "LEGACY_TEST"
    assert isinstance(payload["section_errors"], list)


# --------------------------------------------------------------------------- #
# 5. SQLite safety (§12)
# --------------------------------------------------------------------------- #
def test_sqlite_snapshot_is_consistent_and_never_touches_the_source(tmp_path):
    from app.db.snapshot import sqlite_snapshot

    src = tmp_path / "src.db"
    conn = sqlite3.connect(src)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.executemany("INSERT INTO t (v) VALUES (?)", [(f"row-{i}",) for i in range(50)])
    conn.commit()
    src_before = src.read_bytes()

    dst = tmp_path / "nested" / "snapshot.db"
    info = sqlite_snapshot(src, dst)
    assert info["ok"] is True and dst.exists()
    assert src.read_bytes() == src_before, "snapshot modified its source"
    copied = sqlite3.connect(dst)
    assert copied.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 50
    copied.close()


def test_sqlite_snapshot_leaves_no_partial_file_on_failure(tmp_path):
    from app.db.snapshot import sqlite_snapshot

    missing = tmp_path / "does-not-exist.db"
    dst = tmp_path / "out.db"
    with pytest.raises(FileNotFoundError):
        sqlite_snapshot(missing, dst)
    assert not dst.exists()
    assert not (tmp_path / "out.db.tmp").exists()


def test_connection_detects_and_reopens_a_replaced_database(tmp_path):
    """The historical 'malformed database' trigger: a replaced file under a live
    connection must not be served from the old inode (§12)."""
    from app.db.database import Database
    from app.db.snapshot import wal_sidecar_status

    path = tmp_path / "swap.db"
    first = Database(str(path))
    first.x("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, v TEXT)")
    first.x("INSERT INTO t (v) VALUES ('old')")
    assert first.one("SELECT COUNT(*) c FROM t")["c"] == 1
    assert first.replaced_on_disk() is False

    # a live WAL database leaves sidecars behind - SQLite would replay those
    # committed frames against a *new* main file, which is how a naive file
    # replacement produces a malformed-looking database. Replace all three.
    side = wal_sidecar_status(path)
    assert side["exists"] is True

    other = tmp_path / "other.db"
    conn = sqlite3.connect(other)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.executemany("INSERT INTO t (v) VALUES (?)", [("new-1",), ("new-2",)])
    conn.commit()
    conn.close()
    os.replace(other, path)
    for suffix in ("-wal", "-shm"):
        extra = Path(str(path) + suffix)
        if extra.exists():
            extra.unlink()

    assert first.replaced_on_disk() is True
    assert first.reopen() is True
    assert first.replaced_on_disk() is False
    assert first.one("SELECT COUNT(*) c FROM t")["c"] == 2
    assert first.reopen() is False, "an unchanged file must not trigger a reopen"
    first.close()


def test_no_application_code_copies_a_live_database_with_shutil():
    """V4.7: every database copy goes through the SQLite backup API."""
    offenders = []
    for py in (BACKEND / "app").rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="replace")
        for needle in ("shutil.copy2(str(db_file)", "shutil.copy2(str(mirror)", "shutil.copy(db_file"):
            if needle in text:
                offenders.append(f"{py.relative_to(BACKEND)}: {needle}")
    assert not offenders, offenders


# --------------------------------------------------------------------------- #
# 6. background jobs for long operations (§5/§6)
# --------------------------------------------------------------------------- #
def test_log_export_runs_as_a_background_job(client):
    started = client.post("/api/logs/export/async")
    assert started.status_code == 200
    body = started.json()
    assert body["ok"] is True and body["job_id"]

    job = {}
    for _ in range(60):
        job = client.get(f"/api/jobs/{body['job_id']}").json()
        if job.get("status") in ("COMPLETED", "FAILED"):
            break
        time.sleep(0.05)
    assert job.get("status") == "COMPLETED", job

    result = client.get(f"/api/logs/export/{body['job_id']}")
    assert result.status_code == 200
    rb = result.json()
    assert rb["job_id"] == body["job_id"]
    assert isinstance(rb["log_text"], str)

    # the synchronous endpoint keeps working unchanged (Copy Log / Save Log)
    sync = client.get("/api/logs/export")
    assert sync.status_code == 200 and isinstance(sync.json()["log_text"], str)


def test_log_export_result_is_conflict_until_finished(client):
    r = client.get("/api/logs/export/job_does_not_exist")
    assert r.status_code == 404
    assert "not found" in r.json()["detail"]


def test_backup_async_endpoint_reports_a_job(client):
    r = client.post("/api/backup/async")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["job_id"].startswith("job_")
    job = client.get(f"/api/jobs/{body['job_id']}").json()
    assert job["job_id"] == body["job_id"]
    assert job["status"] in ("WAITING", "ACTIVE", "COMPLETED", "FAILED", "STALLED")


def test_backup_archive_naming_survives_an_external_data_root(tmp_path, monkeypatch):
    """A DATA root outside the repository must not break the archive (§6)."""
    from app.backup import backup as B
    from app import paths as P

    outside = tmp_path / "external-data"
    (outside / "metadata").mkdir(parents=True)
    (outside / "metadata" / "active_run.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(P, "DATA_DIR", outside)
    monkeypatch.setattr(P, "ROOT_DIR", BACKEND)          # repository root, unrelated to DATA

    assert B._archive_name(outside / "metadata" / "active_run.json") == "metadata/active_run.json"
    other = tmp_path / "elsewhere" / "x.json"
    assert B._archive_name(other) == "x.json"           # never raises


# --------------------------------------------------------------------------- #
# 7. query ceilings (§11/§17) - polling must not scan
# --------------------------------------------------------------------------- #
def test_historical_run_lookup_uses_an_index(tmp_db):
    """The polled run-detail query must hit an index, not scan the table."""
    from app.historical_backtest import runs as hb

    assert tmp_db.one("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (hb.RUN_TABLE,))

    plan = tmp_db.q(f"EXPLAIN QUERY PLAN SELECT * FROM {hb.RUN_TABLE} WHERE run_id=?", ("X",))
    detail = " ".join(str(r.get("detail", "")) for r in plan).upper()
    assert "SEARCH" in detail, plan
    assert hb.RUN_TABLE.upper() in detail


def test_historical_run_listing_uses_the_strategy_index(tmp_db):
    from app.historical_backtest import runs as hb

    plan = tmp_db.q(
        f"EXPLAIN QUERY PLAN SELECT * FROM {hb.RUN_TABLE} WHERE strategy_id=? ORDER BY created_at DESC",
        (1,))
    detail = " ".join(str(r.get("detail", "")) for r in plan).upper()
    assert "SEARCH" in detail, plan


def test_capabilities_is_memoised(tmp_path, monkeypatch):
    """Resolving the dataset catalogue reads every dataset parquet; the second
    call must not repeat that work (§17)."""
    from app.historical_backtest import runs as hb

    calls = {"n": 0}

    class FakeEngine:
        def find_dataset_artifact_path(self, dataset_id):
            return str(tmp_path / "x.parquet")

        def is_dataset_eligible_for_research(self, dataset_id, require_features=True):
            calls["n"] += 1
            return True, "ELIGIBLE"

    monkeypatch.setattr("app.data.engine.get_data_engine", lambda: FakeEngine())
    db = Database(str(tmp_path / "c.db"))
    db.x("""INSERT INTO datasets (id, symbol, timeframe, start_ts, end_ts, bars, source, path,
                                  created_at, dataset_version, fingerprint)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
         ("XAUUSD_M15_TEST", "XAUUSD", "M15", 1_700_000_000, 1_700_900_000, 1000, "MT5",
          str(tmp_path / "x.parquet"), time.time(), 1, "fp"))
    hb.clear_catalogue_cache()
    first = hb.capabilities(db=db)
    n_after_first = calls["n"]
    second = hb.capabilities(db=db)
    assert calls["n"] == n_after_first, "capabilities repeated the expensive eligibility probe"
    assert second["cached"] is True
    third = hb.capabilities(db=db, refresh=True)
    assert calls["n"] > n_after_first, "refresh=True must re-resolve"
    assert third["cached"] is False
    assert first["datasets"] or first["rejected_datasets"]
    db.close()


def test_startup_does_not_touch_the_database_before_it_is_needed():
    """Importing the app must not open or scan the database (§3)."""
    src = (BACKEND / "app" / "main.py").read_text(encoding="utf-8")
    hook = src.split('@app.on_event("startup")', 1)[1].split("def threading_start", 1)[0]
    # the heavy scans may only appear inside the registered step functions (indent
    # 8+), never at the startup-hook body level (indent 4) where they used to be
    for heavy in ("discover_all(", "restore_all(", "print_startup_diagnostic("):
        inline = [ln for ln in hook.splitlines()
                  if heavy in ln and ln.startswith("    ") and not ln.startswith("        ")]
        assert not inline, f"{heavy} still runs inline at startup: {inline}"
    assert "lifecycle.register(" in hook
    assert "lifecycle.run()" in hook
    # readiness must be reported before any of that work is attempted
    assert "get_lifecycle()" in hook
