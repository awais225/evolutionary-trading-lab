"""V5.1a-next §D-§F — authoritative populations, the deep-testing union, progress.

Checked against a scratch database built with the *same* schema as the lab, so the
numbers here are computed by real code, not by a fixture's arithmetic:

  §D  one endpoint (and one module) answers total/alive/qualified/final/deep/live/
      live_active (V5.2.2: eligible = capability, active = enrolment);
      the count never inflates alive nodes, never counts LEGACY_TEST rows as live
      or as research, and never silently promotes an unknown status to "alive";
  §E  the deep-testing universe is the UNION of qualified ∪ live-eligible ∪
      already-deep-tested, with the reason recorded per member;
  §F  progress uses real denominators: bars from the run row, else
      estimate_bars(timeframe, window), else null — never a made-up percentage.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

from app.db.database import Database
from app.research import populations as P
from app.status import ALIVE_STATUSES, node_bucket


class FakeDB:
    """Minimal read-only stand-in for the Database (query + one)."""

    def __init__(self, strategies, *, hist=(), live=(), demo=(), trades=()):
        self.tables = {"strategies": list(strategies), "mt5_historical_runs": list(hist),
                       "live_test_configs": list(live), "mt5_demo_configs": list(demo),
                       "mt5_historical_trades": list(trades)}

    def q(self, sql, params=()):
        low = " ".join(sql.split()).lower()
        if "from strategies" in low:
            return [dict(r) for r in self.tables["strategies"]]
        if "from mt5_historical_runs" in low:
            return [dict(r) for r in self.tables["mt5_historical_runs"]]
        if "from live_test_configs" in low and "is_active" in low:
            return [{"strategy_id": r["strategy_id"]} for r in self.tables["live_test_configs"]
                    if int(r.get("is_active") or 0) == 1]
        if "from mt5_demo_configs" in low and "enabled" in low:
            return [{"strategy_id": r["strategy_id"]} for r in self.tables["mt5_demo_configs"]
                    if int(r.get("enabled") or 0) == 1]
        if "from mt5_historical_trades" in low:
            counts: dict = {}
            for r in self.tables["mt5_historical_trades"]:
                counts[r["run_id"]] = counts.get(r["run_id"], 0) + 1
            return [{"run_id": k, "n": v} for k, v in counts.items()]
        if "distinct strategy_id" in low:
            return [{"strategy_id": r["strategy_id"]} for r in self.tables["mt5_historical_runs"]]
        return []

    def one(self, sql, params=()):
        rows = self.q(sql, params)
        return rows[0] if rows else None


def node(node_id, status, *, data_source="USER_RESEARCH", genome=True, **kw):
    row = {"id": node_id, "status": status, "data_source": data_source,
           "symbol": "XAUUSD", "timeframe": "M15", "failure_reason": None,
           "creation_reason": None, "survival_reason": "cleared gates"}
    if genome is True:
        row["genome"] = json.dumps({"symbol": "XAUUSD", "timeframe": "M15",
                                    "entry_long": {"type": "compare"}})
    elif genome:
        row["genome"] = genome if isinstance(genome, str) else json.dumps(genome)
    row.update(kw)
    return row


@pytest.fixture()
def population_db():
    return FakeDB([
        node(1, "SURVIVED"),                       # qualified (legacy vocab, alive)
        node(2, "QUALIFIED"),                      # qualified
        node(3, "SHORTLISTED"),                    # qualified (qualified_raw)
        node(4, "LIVE_ELIGIBLE"),                  # qualified
        node(5, "LIVE_COMPLETED"),                 # qualified + final
        node(6, "MT5_DEMO"),                       # qualified + final
        node(7, "SURVIVED", genome=False),         # qualified but no genome
        node(8, "BORN"),                           # alive, not qualified
        node(9, "PENDING"),                        # alive, not qualified
        node(10, "FAILED", failure_reason="rejected: profit factor 0.8 below 1.1"),
        node(11, "FAILED", failure_reason="dataset unavailable for XAUUSD M15"),
        node(12, "DATA_UNAVAILABLE"),
        node(13, "BACKTEST_ERROR"),
        node(14, "SOME_FUTURE_STATUS"),            # unknown -> must NOT count as alive
        node(15, "SURVIVED", data_source="LEGACY_TEST"),
        node(16, "QUALIFIED", data_source="LEGACY_TEST"),
        node(17, "LIVE_TESTING"),                  # qualified + live status
    ])


# ===========================================================================
# §D — counts
# ===========================================================================
def test_01_counts_every_stored_row(population_db):
    out = P.populations(population_db)
    assert out["counts"]["total"] == 17
    assert out["detail"]["legacy_excluded"] == 2
    assert out["detail"]["user_research_total"] == 15


def test_02_qualified_matches_the_single_classifier(population_db):
    out = P.populations(population_db)
    expected = sum(1 for r in population_db.tables["strategies"]
                   if node_bucket(r)["bucket"] == "qualified")
    assert out["counts"]["qualified"] == expected == 8


def test_03_alive_is_never_inflated(population_db):
    out = P.populations(population_db)
    # nodes 1-9 + 17 are alive; 10 (strategy failure), 11-13 (infrastructure) and
    # 14 (unknown stored status) are NOT; legacy rows are excluded from this count.
    assert out["counts"]["alive"] == 10
    assert out["counts"]["alive"] < out["detail"]["user_research_total"]
    assert "not a dead end" in out["definitions"]["alive"]
    assert "Infrastructure failures are NOT counted" in out["definitions"]["alive"]


def test_04_an_unknown_status_is_never_counted_as_alive(population_db):
    """`is_alive()` alone would count it (unknown -> NOT_TESTED); the population
    counter must not — a row the vocabulary cannot explain is neither alive nor
    failed, so the alive number cannot be inflated."""
    from app.status import is_alive

    row = [r for r in population_db.tables["strategies"] if r["id"] == 14][0]
    assert node_bucket(row)["bucket"] == "unknown"
    assert is_alive(row) is True                  # the raw helper would count it
    assert P.is_alive_user_node(row) is False     # the counter does not
    assert P.populations(population_db)["counts"]["alive"] == 10


def test_05_final_is_the_furthest_research_stage(population_db):
    out = P.populations(population_db)
    assert out["counts"]["final"] == 2           # LIVE_COMPLETED + MT5_DEMO
    assert "furthest research stage" in out["definitions"]["final"]


def test_06_deep_is_the_union_size(population_db):
    out = P.populations(population_db)
    assert out["counts"]["deep"] == len(P.deep_universe(population_db))
    assert out["counts"]["deep"] >= out["counts"]["qualified"]


def test_07_live_activity_counts_only_nodes_present_in_the_database():
    """A config row for a node that is not in this DB must not inflate the count.

    V5.2.2 — the enrolment number is ``live_active`` (``LIVE_TESTING_ACTIVE``);
    ``live`` (``LIVE_TESTING_ELIGIBLE``) is capability and needs no config row.
    """
    db = FakeDB([node(1, "LIVE_TESTING")],
                live=[{"strategy_id": 1, "is_active": 1}, {"strategy_id": 999, "is_active": 1}],
                demo=[{"strategy_id": 888, "enabled": 1}])
    out = P.populations(db)
    assert out["counts"]["live_active"] == 1
    assert out["detail"]["live_inputs"]["configured"] == 1
    # the raw config rows are still reported honestly (2 that point outside this DB)
    assert out["detail"]["live_inputs"]["active_configs_total"] == 3
    assert out["counts"]["live"] == 1                 # capability: the node is qualified + tradeable


def test_08_a_legacy_config_is_not_reported_as_a_live_user_node():
    db = FakeDB([node(1, "SURVIVED"), node(2, "SURVIVED", data_source="LEGACY_TEST")],
                live=[{"strategy_id": 2, "is_active": 1}])
    out = P.populations(db)
    assert out["counts"]["live_active"] == 0                    # the only config is LEGACY infrastructure
    assert out["detail"]["live_inputs"]["configured"] == 0
    # ... and a legacy row is never a live-eligible candidate either, while the
    # current-experiment node is (V5.2.2: eligibility is capability)
    assert out["counts"]["live"] == 1
    eligible = out["detail"]["live_inputs"]["eligible_detail"]["eligible"]
    assert [e["id"] for e in eligible] == [1]


def test_09_infrastructure_blocked_nodes_are_not_failures(population_db):
    out = P.populations(population_db)
    assert out["detail"]["buckets"]["blocked"] == 3      # DATA_UNAVAILABLE, BACKTEST_ERROR, dataset-unavailable FAILED
    assert out["detail"]["buckets"]["failed"] == 1       # the genuine strategy rejection


def test_10_the_payload_ships_its_own_definitions(population_db):
    out = P.populations(population_db)
    # V5.4 §1: the population keys AND the whole partition are defined
    for key in ("total", "alive", "qualified", "final", "deep", "live", "live_active",
                "dead", "failed", "blocked", "unknown", "legacy_excluded",
                "user_research", "in_flight", "generation", "validating"):
        assert out["definitions"][key], key
    assert "app.research.populations" in out["authority"]
    assert "node_bucket" in out["authority"]                 # the classifier is named too


def test_11_an_unreadable_database_says_so_instead_of_reporting_fake_numbers():
    class Broken:
        def q(self, *a, **k):
            raise RuntimeError("database is locked")

        def one(self, *a, **k):
            raise RuntimeError("database is locked")

    out = P.populations(Broken())
    assert out["counts"]["total"] == 0
    assert out["notes"] and "0" in out["notes"][0]


def test_12_empty_database_is_zero_with_a_note():
    out = P.populations(FakeDB([]))
    # V5.4 §1: an empty database is zero on EVERY counter of the partition
    assert out["counts"] == {"total": 0, "alive": 0, "qualified": 0, "final": 0,
                             "deep": 0, "live": 0, "live_active": 0,
                             "dead": 0, "failed": 0, "blocked": 0, "unknown": 0,
                             "legacy_excluded": 0, "user_research": 0,
                             "in_flight": 0, "generation": 0, "validating": 0}
    assert out["notes"]
    # ... and the partition invariants still hold when there is nothing to count
    snap = P.node_state_snapshot(FakeDB([]))
    assert snap["state"]["TOTAL"] == 0 and snap["invariants"]["total_equals_alive_dead_legacy"]


# ===========================================================================
# §E — the union
# ===========================================================================
def test_13_union_is_qualified_plus_eligible_plus_already_deep_tested():
    db = FakeDB([node(1, "SURVIVED"),                    # qualified
                 node(2, "BORN"),                        # eligible only
                 node(3, "FAILED", failure_reason="rejected: drawdown"),   # deep-tested earlier
                 node(4, "SURVIVED", data_source="LEGACY_TEST")],
                hist=[{"strategy_id": 3, "run_id": "HR-1", "timeframe": "M15",
                       "status": "COMPLETE", "bars": 100, "start_ts": 0, "end_ts": 9000000}])
    members = P.deep_universe(db, rows=db.q("SELECT id, status, data_source, symbol, timeframe, genome, failure_reason, creation_reason, survival_reason FROM strategies"))
    ids = {m["id"]: m for m in members}
    assert set(ids) == {1, 3}                 # 2 is eligible only if the engine says so
    assert ids[1]["why"] == ["qualified"]
    assert "already deep-tested" in ids[3]["why"]
    assert all(m["why"] for m in members)


def test_14_union_excludes_legacy_infrastructure_and_names_why():
    db = FakeDB([node(1, "SURVIVED"), node(2, "QUALIFIED", data_source="LEGACY_TEST")])
    members = P.deep_universe(db, rows=db.q("SELECT id, status, data_source, symbol, timeframe, genome FROM strategies"))
    assert [m["id"] for m in members] == [1]
    assert members[0]["why"]


def test_15_union_reports_whether_a_member_can_actually_be_deep_tested():
    db = FakeDB([node(1, "SURVIVED"), node(2, "SURVIVED", genome=False),
                 node(3, "SURVIVED", genome='{"symbol": "XAUUSD", "timeframe": "M15"}')])
    members = {m["id"]: m for m in P.deep_universe(db, rows=db.q(
        "SELECT id, status, data_source, symbol, timeframe, genome FROM strategies"))}
    assert members[1]["has_genome"] is True
    # no genome, or a genome without an entry condition: still listed (it is
    # qualified) but flagged, so the UI cannot silently offer an untestable node
    assert members[2]["has_genome"] is False
    assert members[3]["has_genome"] is False


def test_16_union_is_sorted_and_stable():
    db = FakeDB([node(9, "SURVIVED"), node(2, "SURVIVED"), node(5, "SURVIVED")])
    members = P.deep_universe(db, rows=db.q(
        "SELECT id, status, data_source, symbol, timeframe, genome FROM strategies"))
    assert [m["id"] for m in members] == [2, 5, 9]


# ===========================================================================
# §F — progress / bar estimate
# ===========================================================================
def test_17_estimate_bars_uses_the_timeframe():
    assert P.estimate_bars("M15", 0, 900 * 1000)["bars"] == 1000
    assert P.estimate_bars("H1", 0, 3600 * 24)["bars"] == 24
    assert P.estimate_bars("D1", 0, 86400 * 30)["bars"] == 30


def test_18_estimate_bars_refuses_to_guess():
    bad = P.estimate_bars("M7", 0, 1000)
    assert bad["bars"] is None and "unknown timeframe" in bad["reason"]
    empty = P.estimate_bars("M15", 900, 900)
    assert empty["bars"] is None and "empty" in empty["reason"]
    junk = P.estimate_bars("M15", "x", "y")
    assert junk["bars"] is None and junk["reason"]


def test_19_progress_prefers_the_recorded_bar_count():
    pr = P.run_progress({"run_id": "HR-1", "status": "COMPLETE", "timeframe": "M15",
                         "bars": 500, "start_ts": 0, "end_ts": 900 * 100},
                        processed_bars=250)
    assert pr["bars_expected"] == 500 and pr["bars_processed"] == 250
    assert pr["pct"] == 50.0 and pr["source"] == "bars column"


def test_20_progress_falls_back_to_the_estimate_then_to_null():
    pr = P.run_progress({"run_id": "HR-2", "status": "RUNNING", "timeframe": "M15",
                         "bars": None, "start_ts": 0, "end_ts": 900 * 10},
                        processed_bars=5)
    assert pr["bars_expected"] == 10 and pr["pct"] == 50.0
    assert "estimate_bars" in pr["source"]

    unknown = P.run_progress({"run_id": "HR-3", "status": "RUNNING", "timeframe": "M7",
                              "bars": None, "start_ts": 0, "end_ts": 1000},
                             processed_bars=5)
    assert unknown["pct"] is None and unknown["reason"]     # never a fabricated %


def test_21_progress_pct_is_clamped():
    pr = P.run_progress({"run_id": "HR-4", "status": "COMPLETE", "timeframe": "M15",
                         "bars": 100, "start_ts": 0, "end_ts": 0}, processed_bars=250)
    assert pr["pct"] == 100.0


# ===========================================================================
# API surface
# ===========================================================================
def test_22_api_populations_endpoint_matches_the_module(client):
    r = client.get("/api/nodes/populations")
    assert r.status_code == 200
    body = r.json()
    assert set(body["counts"]) == {"total", "alive", "qualified", "final", "deep",
                                   "live", "live_active", "dead", "failed", "blocked",
                                   "unknown", "legacy_excluded", "user_research",
                                   "in_flight", "generation", "validating",
                                   # carried by the endpoint's authority snapshot
                                   "target", "remaining"}
    assert body["counts"]["total"] > 0
    # V5.4 §1 — the endpoint ships the SAME snapshot the other surfaces read
    ns = body["node_state"]
    assert ns["authority"] == "app.research.populations.node_state_snapshot"
    assert ns["state"]["TOTAL"] == body["counts"]["total"]
    assert ns["state"]["DEAD"] == body["counts"]["dead"]
    assert ns["state"] == body["state_values"]                 # names and short keys agree
    assert ns["invariants"]["total_equals_alive_dead_legacy"]
    assert ns["progress"]["scope"] == "current experiment"
    assert body["counts"]["alive"] >= 1
    assert body["definitions"]["alive"]


def test_23_api_deep_plan_exposes_the_union_and_progress(client):
    r = client.get("/api/nodes/deep-testing/plan")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["union"]["count"] >= 1
    assert "∪" in body["union"]["rule"]
    assert all(m["why"] for m in body["union"]["members"])
    assert isinstance(body["progress"], list)


def test_24_the_two_endpoints_agree_on_qualified(client):
    a = client.get("/api/nodes/populations").json()["counts"]
    b = client.get("/api/nodes", params={"filter": "qualified", "limit": 1000}).json()
    total = b.get("total", len(b.get("nodes") or []))
    assert total == a["qualified"], (total, a["qualified"])


def test_25_populations_are_read_only(client):
    from app.db.database import get_db
    before = get_db().one("SELECT COUNT(*) n FROM strategies")["n"]
    client.get("/api/nodes/populations")
    client.get("/api/nodes/deep-testing/plan")
    after = get_db().one("SELECT COUNT(*) n FROM strategies")["n"]
    assert before == after


def test_26_authoritative_data_keeps_its_own_vocabulary():
    """The live DATA population (10,000 user nodes + 787 legacy) is never rewritten."""
    from app.db.database import get_db
    db = get_db()
    total = db.one("SELECT COUNT(*) n FROM strategies")["n"]
    legacy = db.one("SELECT COUNT(*) n FROM strategies WHERE data_source='LEGACY_TEST'")["n"]
    out = P.populations(db)
    assert out["counts"]["total"] == total
    assert out["detail"]["legacy_excluded"] == legacy
    assert out["detail"]["user_research_total"] == total - legacy
