"""V5.1a §4 / §25 — two consecutive FROM SCRATCH runs are genuinely isolated.

The isolation claim has to hold at the *domain* level, not in the UI: after a
FROM SCRATCH reset, the new experiment must start from node #1 with an empty
population, no inherited backtests/live configs/generation history, and no
reused strategy ids — while the market data, the MT5 configuration and the
LEGACY_TEST infrastructure records survive untouched.

These tests drive the real machinery (``Database``, ``EvolutionEngine``,
``research_run.lifecycle.reset_user_research`` / ``fresh_preview``) against a
throwaway database in ``tmp_path``. Nothing here can touch the provisioned DATA
tree: every write goes to a temporary file, and the module-level engine global
is restored afterwards.
"""
from __future__ import annotations

import json

import pytest

from app.db import database as dbmod
from app.evolution import engine as evo_mod
from app.research_run import lifecycle


# --------------------------------------------------------------------------- #
# a throwaway lab (real schema, real reset machinery, empty population)
# --------------------------------------------------------------------------- #
@pytest.fixture()
def lab(tmp_path, monkeypatch):
    """A real ``Database`` + ``EvolutionEngine`` bound to a temporary file.

    Strategy ids are advanced past 787 first: the historical LEGACY_TEST set is
    protected by ``id <= 787`` as well as by its label, so a temporary database
    must place its research nodes above that boundary to be a faithful stand-in
    for a provisioned installation (where the legacy rows already occupy 1..787).
    """
    tmp_db = dbmod.Database(str(tmp_path / "lab_state.db"))
    tmp_db.x("DELETE FROM sqlite_sequence WHERE name='strategies'")
    tmp_db.x("INSERT INTO sqlite_sequence (name, seq) VALUES ('strategies', 787)")
    saved_engine = evo_mod._evo_engine
    monkeypatch.setattr(lifecycle, "get_db", lambda *a, **k: tmp_db)
    monkeypatch.setattr(dbmod, "get_db", lambda *a, **k: tmp_db)
    evo = evo_mod.EvolutionEngine(tmp_db)
    monkeypatch.setattr(evo_mod, "get_evo_engine", lambda db=None: evo)
    try:
        yield tmp_db, evo, lifecycle
    finally:
        # the engine global must never be left pointing at the temporary file
        evo_mod._evo_engine = saved_engine


def _node(db, run_id: str, n: int, *, data_source: str = "USER_RESEARCH",
          status: str = "QUALIFIED", generation: int = 0) -> int:
    """Insert one persisted node the way the real insert path does."""
    sid = db.insert_strategy({
        "hash": f"{run_id}-{n}-{'L' if data_source == 'LEGACY_TEST' else 'U'}",
        "generation": generation,
        "symbol": "XAUUSD", "timeframe": "M15", "direction": "both",
        "status": status, "creation_reason": "test population",
        "mutation_type": "seed", "species_key": f"sp-{n}",
        "genome": {"symbol": "XAUUSD", "timeframe": "M15",
                   "entry_long": {"type": "crossover"}, "seed": n},
        "complexity": 3, "fitness": 0.5 + n / 100.0,
        "run_id": run_id, "data_source": data_source,
    })
    assert sid is not None, "the temp database refused an insert"
    return int(sid)


def _dependents(db, sid: int) -> None:
    """The strategy-scoped state a run owns (backtest, live config, pipeline)."""
    db.x("INSERT INTO backtests (strategy_id, stage, dataset_id, metrics, fitness,"
         " verdict, created_at) VALUES (?,?,?,?,?,?,?)",
         (sid, "screen", "XAUUSD_M15_TEST", json.dumps({"trades": 3}), 0.5, "PASS", 1.0))
    db.x("INSERT INTO live_test_configs (strategy_id, days, sessions, is_active,"
         " status, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
         (sid, '["0","2"]', '["london"]', 0, "IDLE", 1.0, 1.0))
    db.x("INSERT INTO strategy_pipeline_states (strategy_id, stage, notes, updated_at)"
         " VALUES (?,?,?,?)", (sid, "QUALIFIED", "seeded by the isolation test", 1.0))


def _generation_stats(db, generation: int) -> None:
    db.x("INSERT INTO generation_stats (generation, born, tested, survived, qualified, ts)"
         " VALUES (?,?,?,?,?,?)", (generation, 5, 5, 4, 1, 1.0))


def _numbers(db, run_id: str):
    return [int(r["research_node_num"]) for r in
            db.q("SELECT research_node_num FROM strategies WHERE run_id=? "
                 "ORDER BY research_node_num", (run_id,))]


# --------------------------------------------------------------------------- #
# §4 — what a FROM SCRATCH reset actually does
# --------------------------------------------------------------------------- #
def test_01_the_reset_removes_the_experiment_and_keeps_the_infrastructure(lab):
    db, evo, lc = lab
    legacy = _node(db, "RUN-HISTORICAL-PRESERVED", 1, data_source="LEGACY_TEST")
    _dependents(db, legacy)
    _generation_stats(db, 1)
    user = [_node(db, "RUN-A", n) for n in (1, 2, 3)]
    for sid in user:
        _dependents(db, sid)

    before = lc.counts()
    assert before["user_research_nodes"] == 3 and before["legacy_test_nodes"] == 1

    result = lc.reset_user_research()

    after = lc.counts()
    assert result["ok"] is True
    assert result["deleted_strategies"] == 3
    assert result["legacy_untouched"] is True
    assert (after["user_research_nodes"], after["legacy_test_nodes"]) == (0, 1)
    # the retired experiment's own state is gone...
    for tbl in ("backtests", "live_test_configs", "strategy_pipeline_states"):
        n = db.one(f"SELECT COUNT(*) c FROM {tbl} WHERE strategy_id IN "
                   "(" + ",".join(str(s) for s in user) + ")")["c"]
        assert n == 0, f"{tbl} still references the retired experiment"
    # ...the unscoped generation history is cleared as a whole...
    assert db.one("SELECT COUNT(*) c FROM generation_stats")["c"] == 0
    # ...and the infrastructure row keeps its nodes and its own state
    assert db.get_strategy(legacy) is not None
    assert db.one("SELECT COUNT(*) c FROM backtests WHERE strategy_id=?", (legacy,))["c"] == 1
    assert db.one("SELECT COUNT(*) c FROM live_test_configs WHERE strategy_id=?",
                  (legacy,))["c"] == 1


def test_02_the_next_run_numbers_from_one_again_and_the_allocator_restarts(lab):
    """V5.3 §4 — the identity contract after "delete all previous data".

    The study-local node number restarts at 1 AND the row-id allocator stops
    continuing the deleted study's range (an AUTOINCREMENT high-water mark is not
    lowered by DELETE, which is exactly how a fresh study used to start at
    Node_10788 instead of Node_1). The allocator lands on the surviving
    population — never below the classifier's legacy id band.
    """
    db, evo, lc = lab
    run_a = [_node(db, "RUN-A", n) for n in (1, 2, 3)]
    for sid in run_a:
        _dependents(db, sid)
    assert _numbers(db, "RUN-A") == [1, 2, 3]
    old_high_water = db.one("SELECT seq FROM sqlite_sequence WHERE name='strategies'")["seq"]

    result = lc.reset_user_research()
    assert lc.counts()["user_research_nodes"] == 0
    alloc = result["identity_allocator"]
    surviving_max = db.one("SELECT COALESCE(MAX(id),0) m FROM strategies")["m"]
    assert alloc["sequence_before"] == old_high_water
    assert alloc["next_strategy_id_after_reset"] > surviving_max
    assert alloc["next_strategy_id_after_reset"] > alloc["legacy_id_ceiling"], \
        "a fresh node must never be allocated an id the classifier calls LEGACY_TEST"
    assert alloc["next_strategy_id_after_reset"] <= old_high_water, \
        "the fresh study must not continue the deleted study's row numbering"
    assert alloc["next_node_number"] == 1

    run_b = [_node(db, "RUN-B", n) for n in (1, 2, 3)]
    # study-local numbering restarts at 1 — never 4, 5, 6 or an offset
    assert _numbers(db, "RUN-B") == [1, 2, 3]
    # the new study's rows are allocated from the surviving population, and its
    # nodes are part of the research population (never misread as legacy)
    assert min(run_b) == alloc["next_strategy_id_after_reset"]
    assert lc.counts()["user_research_nodes"] == 3
    for sid, num in zip(run_b, (1, 2, 3)):
        assert db.get_strategy(sid)["research_node_num"] == num
    # rows are unique while they exist (ids may be reused only after a delete-all)
    assert len(set(run_b)) == 3


def test_03_nothing_from_the_previous_experiment_follows_the_new_one(lab):
    db, evo, lc = lab
    a_nodes = [_node(db, "RUN-A", n) for n in (1, 2, 3)]
    for sid in a_nodes:
        _dependents(db, sid)
    _generation_stats(db, 1)
    fp_a = lc._population_fingerprint()
    assert fp_a["count"] == 3 and fp_a["max_id"] == max(a_nodes)

    lc.reset_user_research()
    # after the reset the experiment has no population at all
    assert lc._population_fingerprint()["count"] == 0
    for sid in a_nodes:
        assert db.get_strategy(sid) is None, "a retired node is still reachable"

    b = [_node(db, "RUN-B", 1)]
    for sid in b:
        _dependents(db, sid)
    fp_b = lc._population_fingerprint()
    assert fp_b["count"] == 1 and fp_b["sha256"] != fp_a["sha256"]
    # the new experiment carries none of the previous run's records. V5.3 §4: row
    # ids may be REUSED after a delete-all (that is the allocator reset), so the
    # check is scoped by the run the rows belong to — which is the property that
    # actually matters — instead of by id.
    for tbl in ("backtests", "live_test_configs"):
        stray = db.q("SELECT t.strategy_id FROM %s t JOIN strategies s ON s.id=t.strategy_id "
                     "WHERE COALESCE(s.run_id,'') <> 'RUN-B'" % tbl)
        assert stray == [], f"{tbl} still references the retired experiment: {stray}"
        assert db.one("SELECT COUNT(*) c FROM %s t JOIN strategies s ON s.id=t.strategy_id "
                      "WHERE s.run_id='RUN-B'" % tbl)["c"] >= 1
    assert db.one("SELECT COUNT(*) c FROM generation_stats")["c"] == 0
    assert lc.counts()["max_user_generation"] in (0, None) or \
        lc.counts()["max_user_generation"] == 0


def test_04_the_node_limit_belongs_to_the_current_experiment(lab):
    db, evo, lc = lab
    evo.set_total_node_target(10000)
    assert evo.get_total_node_target() == 10000

    for n in (1, 2, 3):
        _node(db, "RUN-A", n)
    # the run's own population is what the limit counts (the engine's real accessor)
    evo.active_run_id = "RUN-A"
    assert evo.run_nodes() == 3
    assert evo.remaining_nodes() == 9997
    assert evo.is_target_reached() is False

    lc.reset_user_research()
    for n in (1, 2, 3):
        _node(db, "RUN-B", n)
    # ...and a second experiment gets the whole budget again, not what is left
    evo.active_run_id = "RUN-B"
    assert evo.run_nodes() == 3
    assert evo.remaining_nodes() == 9997
    # the database-wide count covers what it covers; the run's own is 3 either way
    assert evo.run_nodes("RUN-B") == 3 and evo.run_nodes("RUN-A") == 0


def test_05_the_preview_states_exactly_what_the_reset_will_do(lab):
    db, evo, lc = lab
    _node(db, "RUN-HISTORICAL-PRESERVED", 1, data_source="LEGACY_TEST")
    for n in (1, 2):
        sid = _node(db, "RUN-A", n)
        _dependents(db, sid)

    preview = lc.fresh_preview(mode="reset_only", target=10000)

    assert preview["will_delete"]["strategies"] == 2
    assert preview["will_keep"]["legacy_test_nodes"] == 1
    assert preview["after_reset"]["first_node_number"] == 1
    assert preview["after_reset"]["population"] == 10000
    # the preview is read-only
    assert lc.counts()["user_research_nodes"] == 2
    # market data / configuration / master datasets are explicitly kept
    for key in ("datasets", "master_datasets", "feature_meta"):
        assert key in preview["will_keep"]


def test_06_two_consecutive_runs_share_no_state_at_all(lab):
    """The end-to-end claim: A → reset → B leaves A with nothing in B and vice versa."""
    db, evo, lc = lab
    a = [_node(db, "RUN-A", n, generation=5) for n in (1, 2, 3)]
    for sid in a:
        _dependents(db, sid)
    _generation_stats(db, 5)
    a_sha = lc._population_fingerprint()["sha256"]

    lc.reset_user_research()
    b = [_node(db, "RUN-B", 1, generation=0)]
    _dependents(db, b[0])
    b_sha = lc._population_fingerprint()["sha256"]

    assert a_sha != b_sha
    assert [r["id"] for r in db.q("SELECT id FROM strategies WHERE run_id='RUN-B'")] == b
    assert [r["id"] for r in db.q("SELECT id FROM strategies WHERE run_id='RUN-A'")] == []
    # generations do not leak either: B's node is generation 0, not A's 5
    assert db.get_strategy(b[0])["generation"] == 0
