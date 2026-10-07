"""V5.2.2 §10 — LIVE TESTING eligibility is CAPABILITY, activity is ENROLMENT.

The operator asked the obvious question about the dashboard:

    "why live eligible are 0 nodes? what's the logic here? should not all
     active/qualified nodes be live eligible?"

He was right to ask.  ``LIVE_TESTING_ELIGIBLE`` used to be computed as *"nodes
wired into a live layer"* — an **enrolment** count — while the Live Testing page
listed every qualified node with a START button.  On real DATA that produced the
contradiction he saw: QUALIFIED 33 next to LIVE ELIGIBLE 0, with the only 16
config rows in the database belonging to LEGACY_TEST infrastructure nodes (which
are permanently banned from live trading).

This file pins the repaired model:

  A. ONE predicate (:mod:`app.live_testing.eligibility`) decides whether a stored
     node may trade live; the engine and the population counters both call it, so
     the number the operator reads and the nodes the engine would trade cannot
     disagree.
  B. ``LIVE_TESTING_ELIGIBLE`` = capability = qualified nodes that pass that
     predicate (the nodes the START path can enrol) — computed WITHOUT needing any
     config row to exist.
  C. ``LIVE_TESTING_ACTIVE`` = enrolment = nodes wired into the live layer right
     now (active live-test config / enabled MT5-demo config / LIVE_TESTING status).
     A fresh lab is legitimately 0 there, and a legacy enrolment is never counted.
  D. the reasons behind both numbers are exposed (never just a total), and the UI
     shows both names.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
from app.db.database import Database
from app.live_testing import eligibility as E
from app.research import populations as P

STRIP = REPO / "frontend" / "src" / "components" / "NodePopulationStrip.jsx"
NODE_INDEX = REPO / "frontend" / "src" / "components" / "LiveNodeIndex.jsx"


# --------------------------------------------------------------------------- #
# fixtures — the lab's own schema, so every number is produced by real code
# --------------------------------------------------------------------------- #
def _genome(symbol="XAUUSD", timeframe="M15", *, entry=True):
    g = {"symbol": symbol, "timeframe": timeframe, "direction": "long",
         "entry_long": ({"op": "and", "clauses": [
             {"type": "compare", "left": "close", "cmp": ">", "right": "sma:50"}]} if entry else None),
         "entry_short": None,
         "exit": {"atr_spec": "atr:14", "sl_atr_mult": 1.5, "tp_atr_mult": 3.0}}
    return g


def _insert(db, node_id, *, status="VALID", data_source="USER_RESEARCH", genome=None,
            symbol="XAUUSD", timeframe="M15"):
    db.x("""INSERT OR REPLACE INTO strategies
            (id, hash, parent_id, generation, symbol, timeframe, direction, status,
             genome, complexity, fitness, created_at, updated_at, origin, run_id, data_source,
             research_node_num)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
         (node_id, f"h{node_id}", None, 39, symbol, timeframe, "long", status,
          json.dumps(genome if genome is not None else _genome(symbol, timeframe)),
          3, 1.0, 1.0, 1.0, "research", "RUN-20261007-000000", data_source, node_id))
    return node_id


@pytest.fixture()
def db(tmp_path):
    d = Database(str(tmp_path / "v522_live_eligibility.db"))
    d.x("DELETE FROM live_test_configs")
    return d


# --------------------------------------------------------------------------- #
# A — the predicate itself
# --------------------------------------------------------------------------- #
def test_01_the_predicate_answers_with_the_engines_own_wording():
    ok = E.tradeability({"data_source": "USER_RESEARCH", "status": "VALID",
                         "genome": _genome()})
    assert ok["ok"] is True and ok["code"] == E.CODE_OK and ok["reason"] is None

    legacy = E.tradeability({"data_source": "LEGACY_TEST", "status": "VALID",
                             "genome": _genome()})
    assert legacy["ok"] is False and legacy["code"] == E.CODE_LEGACY
    assert legacy["reason"] == E.REASON_LEGACY, "the operator-facing wording must not drift"

    dead = E.tradeability({"data_source": "USER_RESEARCH", "status": "DEAD",
                           "genome": _genome()})
    assert dead["ok"] is False and dead["code"] == E.CODE_STATUS and "DEAD" in dead["reason"]

    no_symbol = E.tradeability({"data_source": "USER_RESEARCH", "status": "VALID",
                                "genome": {"timeframe": "M15", "entry_long": {"op": "and"}}})
    assert no_symbol["ok"] is False and no_symbol["reason"] == E.REASON_NO_CONFIG

    no_entry = E.tradeability({"data_source": "USER_RESEARCH", "status": "VALID",
                               "genome": _genome(entry=False)})
    assert no_entry["ok"] is False and no_entry["reason"] == E.REASON_NO_ENTRY

    # a stored genome as JSON TEXT is understood exactly like a dict
    as_text = E.tradeability({"data_source": "USER_RESEARCH", "status": "VALID",
                              "genome": json.dumps(_genome())})
    assert as_text["ok"] is True

    # an unreadable genome is a missing trading configuration, never a crash
    assert E.tradeability({"data_source": "USER_RESEARCH", "status": "VALID",
                           "genome": "[1, 2"})["code"] == E.CODE_NO_CONFIG
    assert E.tradeability(None)["ok"] is False


# --------------------------------------------------------------------------- #
# B/C — capability vs enrolment on the population numbers
# --------------------------------------------------------------------------- #
def test_02_qualified_nodes_are_live_eligible_before_anything_is_started(db):
    _insert(db, 101)                                  # qualified, tradeable
    _insert(db, 102)                                  # qualified, tradeable
    _insert(db, 103, genome=_genome(entry=False))     # qualified shape, but no entry rule
    _insert(db, 104, data_source="LEGACY_TEST")       # infrastructure, never tradeable
    _insert(db, 105, status="STRATEGY_FAILED")        # dead end

    state = P.population_state(db=db)["state"]
    assert state["QUALIFIED"] == 3
    assert state["LIVE_TESTING_ELIGIBLE"] == 2, "eligible == qualified ∩ tradeable"
    assert state["LIVE_TESTING_ACTIVE"] == 0, "nothing is enrolled, and that is a different number"

    detail = P.populations(db=db)["detail"]["live_inputs"]
    assert detail["eligible"] == 2 and detail["active"] == 0
    assert {e["id"] for e in detail["eligible_detail"]["eligible"]} == {101, 102}
    rejected = detail["eligible_detail"]["rejected"]
    assert rejected.get(E.REASON_NO_ENTRY) == 1, "the reason is exposed, never just the total"
    # the legacy row and the failed row are not even candidates (not qualified)
    assert state["TOTAL"] == 5 and state["ALIVE"] == 3


def test_03_enrolling_a_node_moves_activity_only(db):
    _insert(db, 201)
    _insert(db, 202)
    assert P.population_state(db=db)["state"]["LIVE_TESTING_ELIGIBLE"] == 2

    db.set_live_test_config(201, {"strategy_id": 201, "is_active": 1,
                                  "timeframes": ["M15"], "status": "RUNNING"})
    state = P.population_state(db=db)["state"]
    assert state["LIVE_TESTING_ACTIVE"] == 1
    assert state["LIVE_TESTING_ELIGIBLE"] == 2, "STOPping/STARTing never changes capability"

    db.set_live_test_config(201, {"strategy_id": 201, "is_active": 0, "status": "STOPPED"})
    state = P.population_state(db=db)["state"]
    assert state["LIVE_TESTING_ACTIVE"] == 0 and state["LIVE_TESTING_ELIGIBLE"] == 2


def test_04_a_legacy_enrolment_never_counts_as_active_or_eligible(db):
    """The real DATA has 16 active config rows, all belonging to LEGACY_TEST nodes.

    They must never appear as live activity for the current experiment — the reason
    the dashboards showed ``active 0`` — and they must never make a legacy node
    "eligible" either.
    """
    _insert(db, 301, data_source="LEGACY_TEST", status="QUALIFIED")
    _insert(db, 302)
    db.set_live_test_config(301, {"strategy_id": 301, "is_active": 1, "status": "RUNNING"})
    db.set_mt5_demo_config(301, {"strategy_id": 301, "enabled": 1, "status": "RUNNING"})

    state = P.population_state(db=db)["state"]
    assert state["LIVE_TESTING_ACTIVE"] == 0, "a legacy enrolment is not live activity"
    assert state["LIVE_TESTING_ELIGIBLE"] == 1, "only node 302 can trade live"
    detail = P.populations(db=db)["detail"]["live_inputs"]
    assert detail["active_configs_total"] == 1, "the raw config count is still reported, honestly"
    assert detail["configured"] == 0


# --------------------------------------------------------------------------- #
# A (agreement) — the engine and the counter use the same verdict
# --------------------------------------------------------------------------- #
def test_05_the_engine_and_the_population_agree_once_a_node_is_enrolled(db, monkeypatch):
    import app.live_testing.engine as engine_mod
    _insert(db, 401)                                  # qualified + tradeable
    _insert(db, 402, genome=_genome(entry=False))     # qualified, not tradeable
    _insert(db, 403, data_source="LEGACY_TEST")       # legacy

    monkeypatch.setattr(engine_mod, "get_db", lambda: db, raising=False)
    engine = engine_mod.LiveTestingEngine()

    # before enrolment: the engine has nothing to trade, the counter says who COULD be enrolled
    engine.eligible_nodes()
    assert [n["id"] for n in engine.eligible_nodes()] == []
    assert P.population_state(db=db)["state"]["LIVE_TESTING_ELIGIBLE"] == 1

    for sid in (401, 402, 403):
        db.set_live_test_config(sid, {"strategy_id": sid, "is_active": 1, "status": "RUNNING"})

    accepted = [n["id"] for n in engine.eligible_nodes()]
    assert accepted == [401], "the engine accepts exactly the node the predicate accepts"
    reasons = {n["id"]: n["reason"] for n in engine.excluded_nodes()}
    assert reasons[402] == E.REASON_NO_ENTRY
    assert reasons[403] == E.REASON_LEGACY
    assert P.population_state(db=db)["state"]["LIVE_TESTING_ACTIVE"] == 1, \
        "ACTIVE == what the engine really has enrolled and accepted"


def test_06_both_numbers_are_named_and_defined_for_every_page():
    from app.research.populations import POPULATION_STATE_KEYS, POPULATION_STATE_DEFINITIONS
    assert list(POPULATION_STATE_KEYS) == ["TOTAL", "ALIVE", "QUALIFIED",
                                           "FINAL_TESTING_ELIGIBLE", "DEEP_TESTING_ELIGIBLE",
                                           "LIVE_TESTING_ELIGIBLE", "LIVE_TESTING_ACTIVE"]
    eligible = POPULATION_STATE_DEFINITIONS["LIVE_TESTING_ELIGIBLE"].lower()
    active = POPULATION_STATE_DEFINITIONS["LIVE_TESTING_ACTIVE"].lower()
    assert "capability" in eligible and "can enrol" in eligible
    assert "enrolment" in active and "right now" in active


def test_07_the_endpoint_serves_the_repaired_model(db, monkeypatch):
    import app.api.routes as routes
    from app.db import database as database_mod
    _insert(db, 501)
    # the endpoint (and the population module behind it) must read THIS database
    monkeypatch.setattr(database_mod, "get_db", lambda: db, raising=False)
    monkeypatch.setattr(routes, "get_db", lambda: db, raising=False)
    out = routes.nodes_populations()
    assert out["state_order"][-2:] == ["LIVE_TESTING_ELIGIBLE", "LIVE_TESTING_ACTIVE"]
    assert out["state"]["LIVE_TESTING_ELIGIBLE"] == out["counts"]["live"] == 1
    assert out["state"]["LIVE_TESTING_ACTIVE"] == out["counts"]["live_active"] == 0
    assert "capability" in out["definitions"]["LIVE_TESTING_ELIGIBLE"].lower()


# --------------------------------------------------------------------------- #
# D — the operator sees both numbers, in the strip and in the empty state
# --------------------------------------------------------------------------- #
def test_08_the_strip_and_the_live_index_show_both_numbers(db=None):
    strip = STRIP.read_text(encoding="utf-8")
    idx = NODE_INDEX.read_text(encoding="utf-8")
    for name in ("LIVE_TESTING_ELIGIBLE", "LIVE_TESTING_ACTIVE"):
        assert name in strip, f"{name} missing from the population strip"
        assert f'"{name}"' in idx, f"{name} missing from the Live Testing empty state"
    assert "LIVE ELIGIBLE" in strip and "LIVE ACTIVE" in strip
    # the strip must not present eligibility as activity
    assert "capability" in strip.lower() and "enrolment" in strip.lower()


def test_09_the_empty_state_copy_matches_the_repaired_semantics():
    idx = NODE_INDEX.read_text(encoding="utf-8")
    assert "A node becomes live-testing eligible by being QUALIFIED" in idx, \
        "the page's own explanation must match the counter it prints"
