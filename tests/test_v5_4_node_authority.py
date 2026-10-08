"""V5.4 §1 — ONE authoritative node-count source (regression tests).

The defect these tests lock down: Overview printed LIVE ELIGIBLE 33 / TOTAL 10,000
while the NODES strip printed 10,787 and Stats printed QUALIFIED 5. Every number
came from a *different* derivation. V5.4 makes
``app.research.populations.node_state_snapshot`` the single source and makes every
panel consume it; these tests fail if a second derivation, a second shape or a
process-wide side effect creeps back in.

Covered:
  * the partition invariants (TOTAL = ALIVE + DEAD + LEGACY; DEAD = FAILED +
    BLOCKED + UNKNOWN; QUALIFIED ⊆ ALIVE; infrastructure != strategy failure);
  * every counter surface returns the IDENTICAL snapshot (status, populations,
    lab status, stats overview, strategy-lab facets);
  * a read-only snapshot never re-binds the process-wide engine, and a run id is
    adopted only when the database actually holds its rows (the leak that made a
    scratch database "have" the live run's id);
  * the counter audit agrees with the authority under BOTH definitions;
  * the frontend has no local re-derivation left.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db.database import Database
from app.research import populations as P

REPO = Path(__file__).resolve().parent.parent

GENOME = {
    "symbol": "XAUUSD", "timeframe": "M15", "direction": "both",
    "risk": {"risk_per_trade": 0.005, "max_concurrent": 1},
    "exit": {"atr_spec": "atr:7", "sl_atr_mult": 2.0, "tp_atr_mult": 3.0},
    "entry_long": {"op": "gt", "left": {"feature": "rsi:14"}, "right": {"const": 55}},
    "entry_short": {"op": "lt", "left": {"feature": "rsi:14"}, "right": {"const": 45}},
    "features": ["rsi:14"], "regime_filters": [],
}

RUN_ID = "RUN-V54-AUTHORITY"          # never contains TEST: '%TEST%' reads as legacy


def add_row(db, node_id: int, status: str, *, data_source: str = "USER_RESEARCH",
            run_id: str = RUN_ID, genome=None, generation: int = 3) -> int:
    db.x("""INSERT OR REPLACE INTO strategies
            (id, hash, parent_id, generation, symbol, timeframe, direction, status,
             genome, complexity, fitness, created_at, updated_at, origin, run_id,
             data_source, research_node_num)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
         (node_id, f"hash{node_id}", None, generation, "XAUUSD", "M15", "both", status,
          json.dumps(genome if genome is not None else GENOME), 3, 0.5, time.time(),
          time.time(), "research", run_id, data_source, node_id))
    return node_id


@pytest.fixture
def authority_db(tmp_path):
    """Scratch database with every bucket represented, legacy rows included."""
    d = Database(str(tmp_path / "v54_authority.db"))
    for t in ("strategies", "backtests", "validations", "paper_trades", "mt5_demo_trades",
              "live_test_trades", "research_shortlist", "strategy_pipeline_states",
              "matrices", "live_test_configs", "mt5_demo_configs"):
        try:
            d.x(f"DELETE FROM {t}")
        except Exception:
            pass
    # 3 nodes that cleared the research gates (3 different stored vocabularies)
    add_row(d, 1001, "QUALIFIED", generation=4)
    add_row(d, 1002, "SURVIVED", generation=4)
    add_row(d, 1003, "PAPER", generation=4)
    # 2 genuinely failed strategies
    add_row(d, 1004, "FAILED", generation=3)
    add_row(d, 1005, "RETIRED", generation=3)
    # 1 infrastructure-blocked node -- never a strategy failure
    add_row(d, 1006, "DATA_UNAVAILABLE", generation=3)
    # 1 row whose stored status the vocabulary cannot explain
    add_row(d, 1007, "SOMETHING_ELSE", generation=3)
    # 2 legacy infrastructure rows living in the same table
    add_row(d, 1, "FAILED", data_source="LEGACY_TEST", run_id="RUN-HISTORICAL-PRESERVED")
    add_row(d, 2, "SURVIVED", data_source="LEGACY_TEST", run_id="RUN-HISTORICAL-PRESERVED")
    d.set_meta("total_node_target", "10")
    return d


#: every module that took its own reference to ``get_db`` — a test that patches
#: only one of them silently exercises the LIVE database in a full-suite run
_DB_CONSUMERS = ("app.db.database", "app.api.routes", "app.orchestrator.lab",
                 "app.stats.research_stats")


@pytest.fixture
def scratch_engine(authority_db, monkeypatch):
    """A real engine bound to the scratch database, without leaking globally."""
    from app.evolution import engine as engine_mod

    monkeypatch.setattr(engine_mod, "_evo_engine", None)
    monkeypatch.setattr(engine_mod, "get_db", lambda: authority_db)         if hasattr(engine_mod, "get_db") else None
    evo = engine_mod.EvolutionEngine(authority_db)
    # the analytics layer reads the engine snapshot through this helper
    import app.stats.research_stats as rs
    monkeypatch.setattr(rs, "_engine_snapshot",
                        lambda: evo.get_node_generation_state(exclude_legacy=True))
    return evo


@pytest.fixture
def client(authority_db, scratch_engine, monkeypatch):
    """HTTP client whose app, lab and engine all read the scratch database."""
    import importlib

    for name in _DB_CONSUMERS:
        mod = importlib.import_module(name)
        if hasattr(mod, "get_db"):
            monkeypatch.setattr(mod, "get_db", lambda: authority_db)
    from app.main import app

    return TestClient(app)


def expect() -> dict:
    """The numbers the fixture must produce — stated once, asserted everywhere."""
    return {
        "TOTAL": 9,          # 7 user-research + 2 legacy
        "ALIVE": 3,          # the three rows that cleared the gates
        "DEAD": 4,           # 2 judged failures + 1 blocked + 1 unrecognised
        "QUALIFIED": 3,
        "BACKTESTING": 0,
        "VALIDATING": 0,
        "CURRENT_GENERATION": 4,
        "TARGET": 10,
        "REMAINING": 3,      # 10 - 7 experiment nodes
    }


# --------------------------------------------------------------------------- #
# 1. the partition
# --------------------------------------------------------------------------- #
def test_01_the_partition_adds_up(authority_db):
    snap = P.node_state_snapshot(db=authority_db)
    st = snap["state"]
    for key, value in expect().items():
        assert st[key] == value, (key, st[key], value)
    assert snap["invariants"]["total_equals_alive_dead_legacy"] is True
    assert snap["invariants"]["dead_split_reported"] is True
    assert snap["invariants"]["qualified_within_alive"] is True


def test_02_infrastructure_is_blocked_not_failed_and_never_alive(authority_db):
    out = P.populations(db=authority_db)
    c = out["counts"]
    assert c["failed"] == 2 and c["blocked"] == 1 and c["unknown"] == 1
    assert c["dead"] == c["failed"] + c["blocked"] + c["unknown"] == 4
    assert c["legacy_excluded"] == 2
    assert c["user_research"] == 7
    assert c["total"] == c["user_research"] + c["legacy_excluded"]


def test_03_legacy_rows_are_in_the_total_and_in_no_research_number(authority_db):
    out = P.populations(db=authority_db)
    detail = out["detail"]
    assert detail["buckets"]["excluded"] == 2                 # both legacy rows
    assert detail["buckets"]["qualified"] == 3                # legacy SURVIVED is NOT one
    assert detail["user_research_total"] == 7


# --------------------------------------------------------------------------- #
# 2. one snapshot, every surface
# --------------------------------------------------------------------------- #
def test_04_every_counter_surface_returns_the_same_snapshot(client, scratch_engine, authority_db):
    from app.stats import research_stats as st
    from app.orchestrator.lab import Lab

    lab = Lab()
    lab.evo = scratch_engine
    state = expect()
    surfaces = {
        "/api/status": client.get("/api/status").json()["node_state"]["state"],
        "/api/nodes/populations": client.get("/api/nodes/populations").json()["node_state"]["state"],
        "/api/lab/status": lab.status()["node_state"]["state"],
        "stats.population()": st.population(authority_db)["node_state"],
        "populations()": P.node_state_snapshot(db=authority_db)["state"],
    }
    for name, got in surfaces.items():
        for key, value in state.items():
            assert got[key] == value, (name, key, got[key], value)

    # the HTTP payloads for the research facets and the population agree as well
    pop = client.get("/api/stats/overview").json()["population"]
    facets = client.get("/api/research/facets").json()["population"]
    for key in ("total", "alive", "dead", "qualified", "user_research", "legacy_excluded"):
        assert pop[key] == facets[key] == (
            {"total": 9, "alive": 3, "dead": 4, "qualified": 3,
             "user_research": 7, "legacy_excluded": 2}[key]), (key, pop[key], facets[key])


def test_05_the_node_state_payload_has_one_shape(client):
    a = client.get("/api/status").json()["node_state"]
    b = client.get("/api/nodes/populations").json()["node_state"]
    assert a["state"] == b["state"]
    assert a["authority"] == b["authority"] == "app.research.populations.node_state_snapshot"
    assert a["classifier"] == b["classifier"]
    assert a["invariants"] == b["invariants"]
    assert a["progress"]["nodes"] == b["progress"]["nodes"]


def test_06_the_research_facets_and_population_agree(client):
    pop = client.get("/api/stats/overview").json()["population"]
    facets = client.get("/api/research/facets").json()["population"]
    assert pop["total"] == facets["total"] == expect()["TOTAL"]
    assert pop["alive"] == facets["alive"] == expect()["ALIVE"]
    assert pop["qualified"] == facets["qualified"] == expect()["QUALIFIED"]
    assert pop["user_research"] == facets["user_research"] == 7


# --------------------------------------------------------------------------- #
# 3. no hidden global state, no guessed run
# --------------------------------------------------------------------------- #
def test_07_a_snapshot_never_re_binds_the_process_engine(authority_db):
    """Reading the numbers must not change what the next reader sees."""
    from app.evolution.engine import get_evo_engine

    before = get_evo_engine().db
    P.node_state_snapshot(db=authority_db)
    P.node_state_snapshot(db=authority_db)
    assert get_evo_engine().db is before, \
        "a read-only snapshot re-bound the process-wide engine to another database"


def test_08_a_run_id_is_adopted_only_when_the_database_holds_its_rows(authority_db, monkeypatch):
    import app.orchestrator.pipeline_state as psm

    class _PSM:
        run_id = RUN_ID

    monkeypatch.setattr(psm, "get_pipeline_state_manager", lambda *a, **k: _PSM())
    snap = P.node_state_snapshot(db=authority_db)
    assert snap["progress"]["run_id"] == RUN_ID            # this database has its rows
    assert snap["progress"]["nodes"] == 7
    assert snap["progress"]["target"] == 10
    assert snap["progress"]["remaining"] == 3
    assert snap["progress"]["ceiling_reached"] is False

    class _Other:
        run_id = "RUN-SOME-OTHER-EXPERIMENT"

    monkeypatch.setattr(psm, "get_pipeline_state_manager", lambda *a, **k: _Other())
    snap2 = P.node_state_snapshot(db=authority_db)
    assert snap2["progress"]["run_id"] is None, "adopted a run row this database does not hold"
    assert snap2["progress"]["nodes"] == 7                  # the database's own scope
    assert any("RUN-SOME-OTHER-EXPERIMENT" in n for n in snap2["notes"])


def test_09_two_databases_never_bleed_into_each_other(tmp_path, authority_db):
    """The exact regression: a scratch database must not report the live numbers."""
    other = Database(str(tmp_path / "v54_other.db"))
    for t in ("strategies",):
        try:
            other.x(f"DELETE FROM {t}")
        except Exception:
            pass
    add_row(other, 5001, "QUALIFIED", run_id="RUN-OTHER")
    assert P.populations(db=other)["counts"]["total"] == 1
    assert P.node_state_snapshot(db=other)["state"]["TOTAL"] == 1
    # ...and the first database is unaffected
    assert P.node_state_snapshot(db=authority_db)["state"]["TOTAL"] == 9


# --------------------------------------------------------------------------- #
# 4. the audit proves it instead of describing it
# --------------------------------------------------------------------------- #
def test_10_the_counter_audit_matches_the_authority_on_both_definitions(authority_db,
                                                                       scratch_engine):
    from app.stats import research_stats as st

    audit = st.counter_audit(authority_db)
    assert audit["consistent"] is True
    assert audit["mismatched_surfaces"] == []
    assert audit["authority_values"] == {"ALL_STORED": 9, "USER_RESEARCH": 7}
    assert audit["distinct_values"] == [7]
    assert audit["distinct_values_all_stored"] == [9]
    assert all(c["matches_authority"] is True for c in audit["checks"])
    assert all(c["definition"] in audit["definitions"] for c in audit["checks"])
    assert audit["legacy_excluded_nodes"] == 2


def test_11_every_definition_ships_with_its_number(authority_db):
    out = P.populations(db=authority_db)
    snap = P.node_state_snapshot(db=authority_db)
    for key in out["counts"]:
        assert out["definitions"].get(key), f"the count {key} has no definition"
    for name in snap["state"]:
        assert snap["definitions"].get(name), f"the state {name} has no definition"
    assert snap["progress"]["scope"] == "current experiment"
    assert "TOTAL counts every stored node" in snap["progress"]["note"]


# --------------------------------------------------------------------------- #
# 5. the frontend must not re-derive anything
# --------------------------------------------------------------------------- #
def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def test_12_the_overview_reads_the_snapshot_and_not_a_fallback_chain():
    src = _read("frontend/src/pages/Overview.jsx")
    assert "status?.node_state" in src or "status.node_state" in src
    for banned in ("recheckData?.total_nodes", "lab?.total_nodes", "recheckData?.target",
                   "recheckData?.qualified", "counts.QUALIFIED"):
        assert banned not in src, f"Overview still falls back to {banned!r}"
    # every card is derived from the ONE snapshot, under its explicit name
    for expr in ("const totalNodes = nnum(nsState?.TOTAL)",
                 "const alive = nnum(nsState?.ALIVE)",
                 "const dead = nnum(nsState?.DEAD)",
                 "const qualified = nnum(nsState?.QUALIFIED)",
                 "const experimentNodes = nnum(nsProgress?.nodes)"):
        assert expr in src, f"Overview does not read {expr!r} from the snapshot"


def test_13_no_page_adds_its_own_population_numbers():
    """A total recomputed from two other counters is a second derivation again."""
    pattern = re.compile(r"(user_research|legacy_excluded)\s*[+]\s*\w*(legacy|research)", re.I)
    for path in (REPO / "frontend/src").rglob("*.jsx"):
        text = path.read_text(encoding="utf-8")
        assert not pattern.search(text), f"{path.name} re-adds the population counters"
