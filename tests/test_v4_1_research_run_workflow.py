"""V4.1 focused tests: START NEW RESEARCH RUN workflow (backup / resume-add / reset).

These tests run against the configured real DATA root and are deliberately
NON-DESTRUCTIVE: they exercise backup creation + verification, the confirmation
guards, the duplicate-submission lock, resume-add validation, the failure-abort
path (a backup that cannot be verified must abort before any deletion) and the
statistics/run-identification surfaces. The destructive end-to-end runs
(backup+reset, reset without backup, restore) are executed against a disposable
DATA copy by the acceptance script and are recorded in lmsarena.txt.
"""
from __future__ import annotations

import json
import zipfile

import pytest

from app.research_run import (
    CONFIRM_BACKUP_AND_RESET,
    CONFIRM_RESET_ONLY,
    counts,
    create_research_backup,
    resume_add_nodes,
    start_fresh_run,
    state,
    verify_research_backup,
)


@pytest.fixture()
def legacy_snapshot():
    """Identity of the LEGACY_TEST population - must never change in these tests."""
    from app.db.database import get_db

    rows = get_db().q(
        "SELECT id, status, generation, research_node_num, hash, run_id FROM strategies "
        "WHERE (COALESCE(data_source,'')='LEGACY_TEST' OR id<=787 OR run_id IS NULL "
        "OR run_id LIKE '%TEST%' OR run_id='RUN-HISTORICAL-PRESERVED') ORDER BY id"
    )
    return rows


# --------------------------------------------------------------------------- #
# 1. backup contains USER_RESEARCH only
# --------------------------------------------------------------------------- #
def test_research_backup_contains_user_research_only(legacy_snapshot):
    before = counts()
    res = create_research_backup(note="v4.1 regression test")
    assert res["ok"] is True
    assert res["filename"].startswith("research_backup_")

    ver = verify_research_backup(res["path"], expected_user_nodes=before["user_research_nodes"])
    assert ver["ok"] is True, ver["errors"]
    details = ver["details"]
    # legacy rows are not part of a USER_RESEARCH backup ...
    assert details["legacy_rows_in_backup"] == 0
    # ... and the user research population is fully present
    assert details["strategies_in_backup"] == before["user_research_nodes"]
    # the archive is a real, readable database carrying the run identity
    assert details["manifest"]["kind"] == "RESEARCH_RUN_BACKUP"
    assert details["manifest"]["legacy_test_nodes_excluded"] == before["legacy_test_nodes"]
    assert details["manifest"]["run_id"] in details["research_runs_in_backup"]

    # creating a backup must not modify the live population, user or legacy
    assert counts()["user_research_nodes"] == before["user_research_nodes"]
    assert counts()["legacy_test_nodes"] == before["legacy_test_nodes"]
    assert get_legacy_rows() == legacy_snapshot


def get_legacy_rows():
    from app.db.database import get_db

    return get_db().q(
        "SELECT id, status, generation, research_node_num, hash, run_id FROM strategies "
        "WHERE (COALESCE(data_source,'')='LEGACY_TEST' OR id<=787 OR run_id IS NULL "
        "OR run_id LIKE '%TEST%' OR run_id='RUN-HISTORICAL-PRESERVED') ORDER BY id"
    )


# --------------------------------------------------------------------------- #
# 2. destructive options require explicit confirmation and delete nothing without it
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("mode,confirm", [
    ("backup_and_reset", ""),
    ("backup_and_reset", "backup_and_reset"),
    ("backup_and_reset", "RESET_USER_RESEARCH"),
    ("reset_only", ""),
    ("reset_only", "BACKUP_AND_RESET"),
    ("reset_only", "reset"),
])
def test_destructive_options_require_exact_confirmation(mode, confirm, legacy_snapshot):
    before = counts()
    res = start_fresh_run(mode=mode, confirm=confirm, start=False)
    assert res["ok"] is False
    assert res["stage"] == "CONFIRM"
    assert res["confirmation_required"] == (CONFIRM_BACKUP_AND_RESET if mode == "backup_and_reset"
                                            else CONFIRM_RESET_ONLY)
    # nothing was deleted
    after = counts()
    assert after["user_research_nodes"] == before["user_research_nodes"]
    assert after["legacy_test_nodes"] == before["legacy_test_nodes"]
    assert get_legacy_rows() == legacy_snapshot


def test_invalid_mode_is_rejected():
    res = start_fresh_run(mode="start_over", confirm="BACKUP_AND_RESET")
    assert res["ok"] is False and res["stage"] == "VALIDATE"


# --------------------------------------------------------------------------- #
# 3. backup verification failure aborts BEFORE any deletion (transactional order)
# --------------------------------------------------------------------------- #
def test_unverifiable_backup_aborts_before_reset(tmp_path, monkeypatch, legacy_snapshot):
    import app.research_run.lifecycle as lc

    corrupt = tmp_path / "research_backup_corrupt.zip"
    with zipfile.ZipFile(corrupt, "w") as zf:
        # no DATABASE/lab_state.db, no manifest -> verification must fail
        zf.writestr("notes.txt", "not a backup")

    before = counts()
    monkeypatch.setattr(lc, "create_research_backup",
                        lambda note="": {"ok": True, "path": str(corrupt),
                                         "filename": corrupt.name, "user_research_nodes": 0})
    res = lc.start_fresh_run(mode="backup_and_reset", confirm=CONFIRM_BACKUP_AND_RESET, start=False)

    assert res["ok"] is False
    assert res["stage"] == "BACKUP_VERIFY"
    assert res.get("deleted_anything") is False
    after = counts()
    assert after["user_research_nodes"] == before["user_research_nodes"]
    assert after["legacy_test_nodes"] == before["legacy_test_nodes"]
    assert get_legacy_rows() == legacy_snapshot

    # a corrupt archive must be reported as not restorable
    ver = verify_research_backup(str(corrupt))
    assert ver["ok"] is False and ver["errors"]


def test_verify_rejects_non_backup_zip(tmp_path):
    bogus = tmp_path / "random.zip"
    with zipfile.ZipFile(bogus, "w") as zf:
        zf.writestr("hello.txt", "hi")
    ver = verify_research_backup(str(bogus))
    assert ver["ok"] is False


# --------------------------------------------------------------------------- #
# 4. resume-add: target maths, validation, no regeneration
# --------------------------------------------------------------------------- #
def test_resume_add_rejects_invalid_amounts(legacy_snapshot):
    for bad in (0, -5, "abc", None):
        res = resume_add_nodes(bad, start=False)
        assert res["ok"] is False and res["stage"] == "VALIDATE"
    before = counts()
    assert before["legacy_test_nodes"] == len(legacy_snapshot)
    assert get_legacy_rows() == legacy_snapshot


def test_resume_add_raises_target_and_preserves_existing_nodes():
    """Raise the target by N, then put it back - existing nodes are never touched."""
    before = counts()
    st = state()
    current_target = int(st["target"])

    res = resume_add_nodes(25, start=False)
    assert res["ok"] is True, res
    # the target is raised by exactly N above the *preserved* population the
    # operation itself reports (a mid-transition study can carry nodes of a run
    # that is newer than the persisted pipeline-state run id, so comparing
    # against a separately-read run_nodes would be order/state dependent)
    assert res["new_target"] == res["existing_nodes"] + 25
    assert res["nodes_to_generate"] == 25
    assert res["existing_nodes_preserved"] is True
    assert res["existing_nodes"] == before["user_research_nodes"]

    # test hygiene: leave the study exactly as found (target + run metadata)
    from app.db.database import get_db
    from app.evolution.engine import get_evo_engine

    get_evo_engine().set_total_node_target(current_target)
    get_db().x("UPDATE research_runs SET target_nodes=? WHERE run_id=?",
               (current_target, res["run_id"]))
    after = counts()
    assert after["user_research_nodes"] == before["user_research_nodes"]
    assert after["max_strategy_id"] == before["max_strategy_id"]


# --------------------------------------------------------------------------- #
# 5. duplicate submissions are prevented
# --------------------------------------------------------------------------- #
def test_second_concurrent_operation_is_rejected():
    import app.research_run.lifecycle as lc

    assert lc._op_gate.acquire(blocking=False) is True
    try:
        with pytest.raises(RuntimeError):
            resume_add_nodes(25, start=False)
        with pytest.raises(RuntimeError):
            start_fresh_run(mode="reset_only", confirm=CONFIRM_RESET_ONLY, start=False)
    finally:
        lc._op_gate.release()


# --------------------------------------------------------------------------- #
# 6. run identification + statistics reconstruction surfaces
# --------------------------------------------------------------------------- #
def test_state_exposes_run_identity_and_counts():
    st = state()
    for key in ("user_research_nodes", "legacy_test_nodes", "run_id", "run_nodes", "target",
                "next_strategy_id", "next_research_node_num", "configured_fresh_target",
                "legacy_notice", "operation"):
        assert key in st, key
    assert st["user_research_nodes"] > 0
    assert st["legacy_test_nodes"] > 0
    assert st["configured_fresh_target"] > 0
    assert st["run_id"]
    assert st["operation"]["busy"] is False


def test_operation_status_endpoint_shape(client):
    res = client.get("/api/research-run/status")
    assert res.status_code == 200
    body = res.json()
    assert "busy" in body and "stage" in body


def test_state_endpoint_and_lab_status_agree(client):
    """The dialog and the dashboard must report the same user-research population."""
    st = client.get("/api/research-run/state").json()
    lab = client.get("/api/lab/status").json()
    assert st["user_research_nodes"] == lab["total_nodes"]
    assert st["legacy_test_nodes"] == counts()["legacy_test_nodes"]


def test_confirmation_guard_over_http(client):
    res = client.post("/api/research-run/start-fresh",
                      json={"mode": "reset_only", "confirm": "please"})
    assert res.status_code == 409
    assert res.json()["detail"]["stage"] == "CONFIRM"


def test_http_duplicate_submission_returns_409(client):
    import app.research_run.lifecycle as lc

    assert lc._op_gate.acquire(blocking=False) is True
    try:
        res = client.post("/api/research-run/resume-add", json={"additional_nodes": 10})
        assert res.status_code == 409
    finally:
        lc._op_gate.release()


def test_research_run_endpoints_documented():
    """The V4.1 endpoints are part of the public API surface."""
    import app.main as main

    paths = set(main.app.openapi().get("paths", {}).keys())
    for p in ("/api/research-run/state", "/api/research-run/status", "/api/research-run/backup",
              "/api/research-run/backups", "/api/research-run/start-fresh",
              "/api/research-run/resume-add", "/api/research-run/restore"):
        assert p in paths, p


# --------------------------------------------------------------------------- #
# 8. engine safety: LEGACY_TEST is never a research candidate (isolated temp DB)
# --------------------------------------------------------------------------- #
def _temp_engine(tmp_path, name: str):
    """An evolution engine on its own empty database (no shared DATA involved)."""
    import random

    from app.db.database import Database
    from app.evolution.engine import EvolutionEngine
    from app.genome import ops as gops

    db = Database(str(tmp_path / name))
    evo = EvolutionEngine(db)
    evo.set_total_node_target(10_000)
    return db, evo, gops, random.Random(4242)


def _insert(evo, gops, rng, status: str, fitness: float, symbol: str = "XAUUSD") -> int:
    sid = evo.try_insert(gops.random_genome(symbol, rng, 3), None, 0, status=status)
    assert sid is not None
    from app.db.database import get_db

    evo.db.update_strategy(sid, status=status, fitness=fitness)
    return sid


def test_population_cap_never_retires_legacy_nodes(tmp_path):
    """V4.1: the population cap retires research candidates only.

    LEGACY_TEST infrastructure rows keep their status and metrics forever, even
    when they would be the lowest-fitness SURVIVED rows in the table.
    """
    db, evo, gops, rng = _temp_engine(tmp_path, "cap.db")

    # legacy infrastructure nodes: inserted while no user run is bound -> the
    # project's own classification marks them LEGACY_TEST
    evo.active_run_id = None
    legacy_ids = [_insert(evo, gops, rng, "SURVIVED", 0.01 * (i + 1)) for i in range(5)]

    # user research nodes of the active study: worse fitness than the legacy ones
    evo.active_run_id = "RUN-V41-CAP-CHECK"
    user_ids = [_insert(evo, gops, rng, "SURVIVED", 1.0 + 0.01 * i) for i in range(20)]

    assert len(legacy_ids) == 5 and len(user_ids) == 20
    before = {r["id"]: (r["status"], r["fitness"]) for r in db.q("SELECT id, status, fitness FROM strategies")}

    retired = evo.enforce_population_cap("XAUUSD")
    assert retired > 0, "the cap must still cull surplus research candidates"

    after = {r["id"]: (r["status"], r["fitness"]) for r in db.q("SELECT id, status, fitness FROM strategies")}
    # legacy: nothing changed at all
    for sid in legacy_ids:
        assert after[sid] == before[sid], f"legacy node {sid} was modified by the population cap"
        assert after[sid][0] == "SURVIVED"
    # research: exactly the surplus retired, lowest fitness first
    retired_user = [sid for sid in user_ids if after[sid][0] == "RETIRED"]
    assert len(retired_user) == retired
    assert set(retired_user) == set(sorted(user_ids, key=lambda s: before[s][1])[:retired])


def test_clear_failed_never_deletes_legacy_nodes(tmp_path):
    """V4.1: the failed-node clean-up control is scoped away from LEGACY_TEST."""
    db, evo, gops, rng = _temp_engine(tmp_path, "clear.db")

    evo.active_run_id = None
    legacy_ids = [_insert(evo, gops, rng, "FAILED", 0.1) for _ in range(3)]
    evo.active_run_id = "RUN-V41-CLEAR-CHECK"
    user_ids = [_insert(evo, gops, rng, "FAILED", 0.1) for _ in range(3)]

    evo.clear_failed()

    remaining = {r["id"] for r in db.q("SELECT id FROM strategies")}
    for sid in legacy_ids:
        assert sid in remaining, f"legacy node {sid} was deleted by clear_failed()"
    assert not (set(user_ids) & remaining), "research FAILED leaves should be cleared"


def test_node_state_default_scope_stays_user_research(tmp_path):
    """V4.0 display contract kept in V4.1: the default node snapshot is user-scoped.

    Every dashboard/Live-Activity consumer reads this snapshot, so the default
    must keep describing the user research population while the explicit
    exclude_legacy=False view stays available for raw database-wide readers.
    """
    db, evo, gops, rng = _temp_engine(tmp_path, "scope.db")

    evo.active_run_id = None
    for _ in range(3):
        _insert(evo, gops, rng, "FAILED", 0.1)
    evo.active_run_id = "RUN-V41-SCOPE-CHECK"
    for _ in range(2):
        _insert(evo, gops, rng, "FAILED", 0.1)

    user_scoped = evo.get_node_generation_state(force=True)
    raw = evo.get_node_generation_state(force=True, exclude_legacy=False)
    assert user_scoped["all_persisted_nodes"] == 2, user_scoped["all_persisted_nodes"]
    assert raw["all_persisted_nodes"] == 5, raw["all_persisted_nodes"]
    assert user_scoped["dead_nodes"] == 2 and raw["dead_nodes"] == 5
    # the two scopes must not share a cached snapshot
    assert evo.get_node_generation_state()["all_persisted_nodes"] == 2


# --------------------------------------------------------------------------- #
# 9. safety: the explicit DATA-root override wins over stale absolute CONFIG paths
# --------------------------------------------------------------------------- #
def test_absolute_config_path_follows_explicit_data_root_override(monkeypatch, tmp_path):
    """V4.1: a stale absolute CONFIG path must not defeat the DATA-root override.

    Symptom this prevents: a run persists absolute /.../DATA/... paths into
    CONFIG/lab_config.yaml; a later run started with a different
    EVOLUTIONARY_LAB_DATA_ROOT (e.g. a disposable test copy) silently opened the
    production database - because the absolute path is not under <root>/DATA the
    old resolver returned it untouched.
    """
    from app import paths as P
    from app.config import _resolve_data_tree

    override = tmp_path / "research_root"
    monkeypatch.setattr(P, "DATA_ROOT", override)
    monkeypatch.setattr(P, "DATA_ROOT_EXPLICIT", True)

    stale = "/opt/production/DATA/DATABASE/lab_state.db"
    assert _resolve_data_tree(stale) == str((override / "DATABASE/lab_state.db").resolve())
    assert _resolve_data_tree("/opt/production/DATA") == str(override.resolve())

    # a path without a DATA component is never re-rooted, even with the override
    assert _resolve_data_tree("/tmp/other/lab_state.db") == "/tmp/other/lab_state.db"

    # without the explicit override the persisted absolute path stays untouched
    monkeypatch.setattr(P, "DATA_ROOT_EXPLICIT", False)
    assert _resolve_data_tree(stale) == stale
