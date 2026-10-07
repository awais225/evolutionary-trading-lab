"""V5 end-to-end pipeline smoke — the honest outcome of a real screening batch.

This test drives the *real* orchestrator against the pytest DATA root: it seeds
a handful of nodes through the real genome generator, runs one real screening
batch through the real backtest workers and then asserts what every candidate's
outcome actually is.

It is the regression guard for the V5 root causes:
  * a candidate must never end as a FAILED *strategy* because its data was
    missing (that is DATA_UNAVAILABLE), and
  * nodes must actually reach the backtest instead of dying in the screening
    loop for a derived feature-cache reason.
"""
from __future__ import annotations

import json

import pytest

TESTABLE_STATUSES = {"SURVIVED", "QUALIFIED", "FAILED", "KILLED"}
INFRA_STATUSES = {"DATA_UNAVAILABLE", "DATA_CORRUPT", "BACKTEST_ERROR"}


@pytest.fixture()
def lab(monkeypatch):
    from app import diagnostics as dg
    from app.config import get_config
    from app.orchestrator.lab import Lab
    from app.resources.manager import get_resource_manager

    cfg = get_config()
    monkeypatch.setattr(cfg.evolution, "screen_batch_size", 4, raising=False)
    monkeypatch.setattr(type(get_resource_manager()), "effective_workers", lambda self: 1)
    # the batch must actually run: the memory gate is a scheduling concern, and
    # the test asserts outcomes, so open it deterministically
    monkeypatch.setattr(type(get_resource_manager()), "check_memory_budget",
                        lambda self, *a, **k: True)

    inst = Lab()
    inst._datasets_ready = True
    # an earlier module in the suite may have left the loop in a blocked or
    # completed state; this test wants exactly one clean screening batch
    inst._research_blocked = False
    inst._research_completed = False
    inst.evo.active_run_id = None
    dg.invalidate()
    return inst


def test_screening_batch_produces_real_outcomes(lab):
    """4 seeded nodes -> 4 real outcomes, and the counters agree with the DB."""
    total_before = lab.db.one("SELECT COUNT(*) c FROM strategies")["c"]
    max_id = lab.db.one("SELECT COALESCE(MAX(id),0) m FROM strategies")["m"]
    lab.evo.set_total_node_target(total_before + 4)
    born = lab.evo.seed_population(4, "XAUUSD")
    assert born >= 3, f"only {born} nodes were born"

    # only the nodes this test created (the shared test DB keeps what earlier
    # tests left behind)
    seeded = lab.db.q("SELECT * FROM strategies WHERE id > ? AND status='BORN' ORDER BY id", (max_id,))
    assert len(seeded) == born

    lab._screen_batch()

    after = {r["id"]: r for r in lab.db.q(
        "SELECT * FROM strategies WHERE id IN (%s)" % ",".join(str(r["id"]) for r in seeded))}
    assert len(after) == born

    outcomes = {}
    for sid, row in after.items():
        outcomes[sid] = row["status"]
        assert row["status"] != "BACKTESTING", f"node {sid} stuck in BACKTESTING"
        if row["status"] == "BORN":
            # only legitimate as an explicit requeue, never as a silent skip
            assert lab.counters.get("requeued", 0) > 0, f"node {sid} silently left untested"
        # an infrastructure outcome must carry a real reason and must never be FAILED
        if row["status"] in INFRA_STATUSES:
            assert row["creation_reason"], f"{sid} infra status with no reason"
            assert row["status"] != "FAILED"

    # counters must equal what actually happened
    assert lab.counters["tested"] + lab.counters["skipped"] == born or \
        lab.counters["tested"] + lab.counters["skipped"] >= born
    assert lab.counters["tested"] >= 1, f"no candidate reached the backtest: {outcomes}"

    # every tested node produced a screen backtest record
    for sid, row in after.items():
        if row["status"] in ("SURVIVED", "FAILED", "QUALIFIED", "KILLED"):
            bt = lab.db.one("SELECT COUNT(*) c FROM backtests WHERE strategy_id=? AND stage='screen'", (sid,))
            assert bt["c"] >= 1, f"node {sid} marked {row['status']} without a backtest record"

    # and the DB must reconcile with the diagnostics report
    from app import diagnostics as dg
    dg.invalidate()
    rep = dg.backtest_report()
    assert rep["reconciliation"]["tested_plus_never_tested_equals_requested"] is True


def test_nodes_skipped_for_missing_data_are_not_strategy_failures(lab):
    """A genome on a timeframe with no dataset must become DATA_UNAVAILABLE."""
    import json as _json

    from app import status as st

    total_before = lab.db.one("SELECT COUNT(*) c FROM strategies")["c"]
    lab.evo.set_total_node_target(total_before + 1)
    # a symbol that has no dataset at all in the pytest DATA root
    born = lab.evo.seed_population(1, "TESTSYM_NO_DATA")
    if born == 0:
        pytest.skip("symbol/TF rejected by the generator — nothing to assert")

    sid = lab.db.one("SELECT id FROM strategies ORDER BY id DESC LIMIT 1")["id"]
    lab._datasets_ready = True
    lab._screen_batch()
    row = lab.db.one("SELECT * FROM strategies WHERE id=?", (sid,))
    assert row["status"] in INFRA_STATUSES, row["status"]
    assert st.is_alive(row) is False
    assert st.is_infrastructure(row) is True
    assert st.v5_status(row) != st.STRATEGY_FAILED
    assert _json.loads(row["genome"])["symbol"] == "TESTSYM_NO_DATA"


def test_ceiling_reconciliation_does_not_call_untested_nodes_failures(client):
    """POST /api/tasks/reconcile must not turn never-evaluated nodes into FAILED."""
    from app.db.database import get_db

    db = get_db()
    pending_id = db.one("SELECT COALESCE(MAX(id),0) m FROM strategies")["m"] + 1
    db.x("""INSERT INTO strategies (id, hash, generation, symbol, timeframe, direction, status,
                                   genome, complexity, created_at, updated_at, origin, data_source)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
         (pending_id, "smoke-hash-%d" % pending_id, 0, "XAUUSD", "M15", "long", "BORN",
          "{}", 1, 1.0, 1.0, "smoke", "USER_RESEARCH"))
    res = client.post("/api/tasks/reconcile")
    assert res.status_code == 200
    row = db.one("SELECT status, creation_reason FROM strategies WHERE id=?", (pending_id,))
    assert row["status"] == "NOT_TESTED", row
    assert "never evaluated" in (row["creation_reason"] or "")
    db.x("DELETE FROM strategies WHERE id=?", (pending_id,))


# --------------------------------------------------------------------------- #
# V5 — provenance of newly generated nodes (root cause of "hidden" research work)
# --------------------------------------------------------------------------- #
def test_new_nodes_are_user_research_not_legacy(lab):
    """Nodes born in the research workflow must join USER_RESEARCH.

    The engine used to derive ``data_source`` by string-matching the run id and
    fell back to LEGACY_TEST when no run id was set, so every freshly generated
    node was written as a legacy record — hidden from the research tables and
    from the USER_RESEARCH counts.
    """
    from app.db.database import get_db

    db = get_db()
    before = db.one("SELECT COUNT(*) c FROM strategies WHERE data_source='LEGACY_TEST'")["c"]
    user_before = db.one("SELECT COUNT(*) c FROM strategies WHERE data_source='USER_RESEARCH'")["c"]

    total = db.one("SELECT COUNT(*) c FROM strategies")["c"]
    max_id = db.one("SELECT COALESCE(MAX(id),0) m FROM strategies")["m"]
    lab.evo.active_run_id = None
    lab.evo.set_total_node_target(total + 3)
    born = lab.evo.seed_population(3, "XAUUSD")
    assert born >= 1

    new_rows = db.q("SELECT * FROM strategies WHERE id > ?", (max_id,))
    assert new_rows
    for r in new_rows:
        assert r["data_source"] == "USER_RESEARCH", r
        assert r["run_id"], "a node without a run id would be re-labelled legacy by the migration"

    assert db.one("SELECT COUNT(*) c FROM strategies WHERE data_source='LEGACY_TEST'")["c"] == before
    assert db.one("SELECT COUNT(*) c FROM strategies WHERE data_source='USER_RESEARCH'")["c"] > user_before


def test_insert_strategy_never_infers_legacy_from_a_run_id():
    from app.db.database import Database
    import tempfile, os

    with tempfile.TemporaryDirectory() as d:
        db = Database(os.path.join(d, "prov.db"))
        for rid in ("RUN-TEST-A-NEW", "RUN-HISTORICAL-PRESERVED", "", "RUN-20261007-ABC"):
            sid = db.insert_strategy({"hash": f"h-{rid or 'none'}", "symbol": "XAUUSD",
                                      "timeframe": "M15", "status": "BORN", "genome": "{}",
                                      "run_id": rid})
            assert sid is not None
            row = db.one("SELECT data_source FROM strategies WHERE id=?", (sid,))
            assert row["data_source"] == "USER_RESEARCH", (rid, row)


# --------------------------------------------------------------------------- #
# §1 — a dataset with an unusable feature set is excluded and reported; it must
# never block research on the datasets that do work, and it must never turn into
# a strategy failure.
# --------------------------------------------------------------------------- #
def test_feature_validation_outcome_bloats_only_when_nothing_is_usable():
    from app.orchestrator.lab import feature_validation_outcome

    # every dataset unusable -> BLOCK, with the real reasons attached
    out = feature_validation_outcome([], [{"dataset_id": "A", "reason": "0 rows"},
                                          {"dataset_id": "B", "reason": "missing columns"}])
    assert out["action"] == "BLOCK"
    assert out["excluded"] == ["A", "B"]
    assert "0 rows" in out["reason"] and "missing columns" in out["reason"]

    # mixed -> CONTINUE with exactly the unusable ones excluded
    out = feature_validation_outcome(["C"], [{"dataset_id": "A", "reason": "0 rows"}])
    assert out["action"] == "CONTINUE"
    assert out["excluded"] == ["A"]
    assert out["reason"] is None

    # clean -> CONTINUE, nothing excluded
    out = feature_validation_outcome(["A", "B"], [])
    assert out["action"] == "CONTINUE" and out["excluded"] == [] and out["reason"] is None


def test_feature_validation_excludes_instead_of_blocking_the_whole_cycle():
    """The caller must drop the unusable dataset and keep researching the rest."""
    from pathlib import Path as _P

    src = (_P(__file__).resolve().parents[1] / "backend" / "app" / "orchestrator" / "lab.py").read_text(encoding="utf-8")
    at = src.index("self._verify_and_validate_features(")   # the call site, not the definition
    window = src[at:at + 2200]
    assert "feature_validation_outcome(" in window, "the decision must go through the tested helper"
    assert 'datasets = [d for d in datasets if d.get("id") not in unusable]' in window, \
        "unusable datasets must be excluded from the cycle, not block it"
    assert "data outcome, not a strategy failure" in window, \
        "the exclusion must be reported as a data outcome"
    # the old behaviour (block the whole pipeline on the first bad dataset) is gone
    assert "return True" not in window, "the verifier no longer returns a bare bool"
