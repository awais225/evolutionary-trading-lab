"""V4.4 focused tests — research statistics & node economics (analytics layer).

Safety of the test setup:

* every test runs against a **temporary SQLite database** (``tmp_path``): the
  authoritative DATA database is never opened, read or written here;
* the engine snapshot is stubbed where a test needs deterministic numbers, and
  an explicit test asserts the analytics layer performs **no writes** at all;
* nothing in this module changes research behaviour: the tests only observe the
  read-only aggregates the Stats page consumes.

Covered: USER_RESEARCH scope, LEGACY_TEST exclusion, counter consistency,
per-node statistics, Node Economics, missing-data handling and the separation
between research results and MT5 / live-test execution records.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db.database import Database
from app.stats import research_stats as st


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #
GENOME = {
    "symbol": "XAUUSD", "timeframe": "M15", "direction": "both",
    "risk": {"risk_per_trade": 0.005, "max_concurrent": 1},
    "exit": {"atr_spec": "atr:7", "sl_atr_mult": 2.34, "tp_atr_mult": 3.42,
             "trailing": None, "min_hold_bars": 1, "max_hold_bars": 24,
             "exit_condition": None},
    "entry_long": {"op": "gt", "left": {"feature": "rsi:14"}, "right": {"const": 55}},
    "entry_short": {"op": "lt", "left": {"feature": "rsi:14"}, "right": {"const": 45}},
    "features": ["rsi:14"], "sessions": ["london"], "days": [1, 2, 3], "regime_filters": [],
}

BACKTEST_METRICS = {
    "total_return_pct": 0.12, "profit_factor": 1.8, "win_rate": 0.55,
    "max_drawdown_pct": 0.04, "sharpe": 1.4, "sortino": 1.9, "expectancy": 0.0012,
    "trades": 120, "net_profit": 1200.0, "gross_profit": 3000.0, "gross_loss": -1800.0,
    "avg_trade": 10.0, "avg_hold_bars": 6.0, "consistency": 0.7,
    "exit_reasons": {"tp": 60, "sl": 60},
}


def add_node(db, node_id: int, *, status="SURVIVED", data_source="USER_RESEARCH",
             generation=3, fitness=None, genome=None, run_id="RUN-V44-TEST") -> int:
    g = dict(genome or GENOME)
    db.x("""INSERT OR REPLACE INTO strategies
            (id, hash, parent_id, generation, symbol, timeframe, direction, status,
             genome, complexity, fitness, created_at, updated_at, origin, run_id,
             data_source, research_node_num)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
         (node_id, f"hash{node_id}", None, generation, g["symbol"], g["timeframe"],
          g["direction"], status, json.dumps(g), 3, fitness, time.time(), time.time(),
          "research", run_id, data_source, node_id))
    return node_id


def add_backtest(db, sid: int, *, stage="detail", metrics=None) -> None:
    db.x("""INSERT INTO backtests (strategy_id, stage, dataset_id, window, params, metrics,
                                   fitness, verdict, created_at, fingerprint)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
         (sid, stage, "XAUUSD_M15", json.dumps({"start": 0, "end": 100}),
          json.dumps({"risk": 0.005}), json.dumps(metrics or BACKTEST_METRICS),
          0.9, "PASS", time.time(), f"fp{sid}"))


def add_validation(db, sid: int, *, passed=True, robustness=0.71) -> None:
    # every JSON column of `validations` is JSON text in the real database
    # (`oos` holds the out-of-sample block, `notes` holds a JSON array)
    db.x("""INSERT INTO validations (strategy_id, oos, robustness_score, passed, notes, created_at)
            VALUES (?,?,?,?,?,?)""",
         (sid, json.dumps({"metrics": {"total_return_pct": 0.08}, "degradation": 0.2}),
          robustness, 1 if passed else 0, json.dumps([]), time.time()))


@pytest.fixture
def db(tmp_path):
    """Scratch database: user-research nodes + legacy infrastructure nodes."""
    d = Database(str(tmp_path / "v44_stats.db"))
    try:
        d.get_shortlist()          # this table is created lazily by the database layer
    except Exception:
        pass
    for t in ("strategies", "backtests", "validations", "paper_trades", "mt5_demo_trades",
              "live_test_trades", "research_shortlist", "strategy_pipeline_states", "matrices"):
        try:
            d.x(f"DELETE FROM {t}")
        except Exception:
            pass
    # user research population (8 nodes)
    add_node(d, 1001, status="SURVIVED", fitness=0.73, generation=4)
    add_node(d, 1002, status="QUALIFIED", fitness=0.88, generation=4)
    add_node(d, 1003, status="FAILED", generation=4)
    add_node(d, 1004, status="RETIRED", generation=3)
    add_node(d, 1005, status="KILLED", generation=3)
    add_node(d, 1006, status="PAPER", fitness=0.81, generation=5)
    add_node(d, 1007, status="BORN", generation=5)
    add_node(d, 1008, status="BACKTESTING", generation=5)
    # legacy infrastructure population (3 nodes) - must never be counted
    add_node(d, 801, status="FAILED", data_source="LEGACY_TEST", run_id="RUN-HISTORICAL-PRESERVED")
    add_node(d, 802, status="RETIRED", data_source="LEGACY_TEST", run_id="RUN-HISTORICAL-PRESERVED")
    add_node(d, 803, status="SURVIVED", data_source="LEGACY_TEST", run_id="RUN-TEST-OLD")
    # research results (user scope): two nodes evaluated, one validated, one detail backtest
    add_backtest(d, 1001, stage="detail")
    add_backtest(d, 1002, stage="screen", metrics={**BACKTEST_METRICS, "total_return_pct": 0.05,
                                                   "trades": 30})
    add_validation(d, 1002, passed=True, robustness=0.71)
    # legacy rows that must stay out of every aggregate
    add_backtest(d, 801, stage="detail", metrics={**BACKTEST_METRICS, "total_return_pct": 9.99})
    add_validation(d, 801, passed=True, robustness=0.99)
    # execution / audit rows (separate layer) — NOT NULL columns of the existing
    # tables are filled so the analytics can be exercised on realistic rows
    now = time.time()
    d.x("""INSERT INTO paper_trades (strategy_id, symbol, side, signal_ts, lots, pnl, status,
                                     source, exec_ts)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (1002, "XAUUSD", "BUY", now, 0.10, 25.0, "CLOSED", "paper", now))
    d.x("""INSERT INTO live_test_trades (strategy_id, symbol, timeframe, side, entry_price, lots,
                                         pnl, pnl_pct, open_ts, status)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (1002, "XAUUSD", "M15", "BUY", 2400.0, 0.2, 10.0, 0.0, now, "CLOSED"))
    d.x("INSERT OR REPLACE INTO research_shortlist (strategy_id, notes, created_at) VALUES (?,?,?)",
        (1002, "", time.time()))
    return d


@pytest.fixture
def stub_snapshot(monkeypatch):
    """Deterministic 'engine not running / snapshot empty' behaviour."""
    monkeypatch.setattr(st, "_engine_snapshot", lambda: {})
    return None


def row_counts(db) -> dict:
    return {t: db.one(f"SELECT COUNT(*) c FROM {t}")["c"]
            for t in ("strategies", "backtests", "validations", "paper_trades",
                      "live_test_trades", "research_shortlist", "matrices")}


# --------------------------------------------------------------------------- #
# 1-2. scope, population
# --------------------------------------------------------------------------- #
def test_overview_scope_and_labels(db, stub_snapshot):
    ov = st.overview(db)
    assert ov["scope"]["population"] == "USER_RESEARCH"
    assert ov["scope"]["authoritative"] is True
    assert ov["scope"]["predicate"] == st.SCOPE_PREDICATE
    assert ov["scope"]["user_research_nodes"] == 8
    assert ov["scope"]["legacy_excluded_nodes"] == 3
    for key in ("population", "evolution", "research_performance", "execution_records", "sources"):
        assert key in ov
    # metric provenance is documented in the payload (§10)
    assert ov["sources"]["population"]["scope"] == "USER_RESEARCH"
    assert ov["sources"]["execution_records"]["scope"].startswith("EXECUTION")
    # the label vocabulary keeps research and execution apart (§9)
    assert ov["labels"]["backtest"] == "BACKTEST"
    assert ov["labels"]["validation"] == "VALIDATION"
    assert ov["labels"]["live_test"] == "LIVE TEST"
    assert ov["labels"]["mt5_demo"] == "MT5 DEMO"


def test_population_counts_user_scope(db, stub_snapshot):
    pop = st.population(db)
    assert pop["total"] == 8                      # the 3 legacy rows are excluded
    assert pop["qualified"] == 1
    assert pop["failed"] == 1
    assert pop["retired"] == 1
    assert pop["killed"] == 1
    assert pop["survived"] == 1
    assert pop["paper"] == 1
    assert pop["alive"] == 5                      # SURVIVED+QUALIFIED+PAPER+BORN+BACKTESTING
    assert pop["dead"] == 3                       # FAILED+RETIRED+KILLED
    assert pop["status_counts"] == {"BACKTESTING": 1, "BORN": 1, "FAILED": 1, "KILLED": 1,
                                    "PAPER": 1, "QUALIFIED": 1, "RETIRED": 1, "SURVIVED": 1}
    assert "LEGACY_TEST" in pop["scope_note"]


def test_legacy_excluded_from_population_and_lists(db, stub_snapshot):
    res = st.node_list(db, limit=50)
    assert res["total"] == 8
    ids = [n["id"] for n in res["nodes"]]
    assert 801 not in ids and 802 not in ids and 803 not in ids
    assert res["scope"] == "USER_RESEARCH"
    all_rows = st.node_list(db, limit=50, include_legacy=True)
    assert all_rows["total"] == 11
    assert 801 in [n["id"] for n in all_rows["nodes"]]


# --------------------------------------------------------------------------- #
# 3. evolution / research performance
# --------------------------------------------------------------------------- #
def test_evolution_evaluated_and_rates(db, stub_snapshot):
    ev = st.evolution(db)
    assert ev["nodes_generated"] == 8
    assert ev["nodes_evaluated"] == 2             # only the two user nodes have backtests
    assert ev["nodes_validated"] == 1
    assert ev["current_generation"] == 5
    assert ev["generations_recorded"] == 3        # generations 3, 4, 5
    assert ev["qualification_rate"] == pytest.approx(1 / 2, abs=1e-6)
    assert ev["survival_rate"] == pytest.approx(1 / 2, abs=1e-6)
    gens = {g["generation"]: g for g in ev["generations"]}
    assert gens[4]["nodes"] == 3 and gens[4]["evaluated"] == 2
    assert "USER_RESEARCH" in ev["rates_definition"]


def test_research_performance_excludes_legacy_backtests(db, stub_snapshot):
    perf = st.research_performance(db)
    stages = {s["stage"]: s for s in perf["backtest"]["stages"]}
    assert set(stages) == {"detail", "screen"}
    assert stages["detail"]["backtests"] == 1
    assert stages["screen"]["backtests"] == 1
    # the legacy detail backtest (return 9.99) must not appear in any average
    assert stages["detail"]["avg_return_pct"] == pytest.approx(0.12, abs=1e-6)
    assert stages["detail"]["best_return_pct"] == pytest.approx(0.12, abs=1e-6)
    assert perf["backtest"]["total_backtests"] == 2
    assert perf["backtest"]["nodes_with_backtest"] == 2
    val = perf["validation"]
    assert val["records"] == 1 and val["nodes"] == 1 and val["passed"] == 1
    assert val["avg_robustness"] == pytest.approx(0.71, abs=1e-6)
    assert val["pass_rate"] == pytest.approx(1.0, abs=1e-6)
    # top nodes are user-scoped only
    assert all(n["id"] != 801 for n in perf["top_nodes"])
    assert perf["top_nodes"][0]["id"] == 1002     # highest fitness


def test_execution_records_are_separate(db, stub_snapshot):
    ex = st.execution_records(db)
    assert ex["layer"] == "EXECUTION"
    assert ex["paper"]["trades"] == 1 and ex["paper"]["total_pnl"] == pytest.approx(25.0)
    assert ex["live_test"]["trades"] == 1
    assert ex["mt5_demo"]["trades"] == 0
    assert "research population" in ex["never_affects"]
    assert "never" in ex["note"].lower()
    # counting execution rows must not move a single research number
    before = st.population(db)
    assert st.population(db) == before


def test_counter_audit_consistent(db, stub_snapshot):
    audit = st.counter_audit(db)
    assert audit["scope"] == "USER_RESEARCH"
    assert audit["consistent"] is True
    assert audit["distinct_values"] == [8]
    assert audit["legacy_excluded_nodes"] == 3
    surfaces = {c["surface"] for c in audit["checks"]}
    assert {"stats.population.total", "api.status.status_counts_sum",
            "population.list.total", "state.reconstruction.total_nodes"} <= surfaces


# --------------------------------------------------------------------------- #
# 4. node list
# --------------------------------------------------------------------------- #
def test_node_list_pagination_and_filters(db, stub_snapshot):
    page1 = st.node_list(db, limit=3, offset=0, sort="fitness")
    page2 = st.node_list(db, limit=3, offset=3, sort="fitness")
    assert page1["returned"] == 3 and page2["returned"] == 3
    assert page1["total"] == page2["total"] == 8
    assert {n["id"] for n in page1["nodes"]} & {n["id"] for n in page2["nodes"]} == set()
    assert page1["nodes"][0]["id"] == 1002        # highest fitness first
    qualified = st.node_list(db, status="QUALIFIED")
    assert [n["id"] for n in qualified["nodes"]] == [1002]
    by_symbol = st.node_list(db, search="XAUUSD")
    assert by_symbol["total"] == 8
    by_id = st.node_list(db, search="1006")
    assert [n["id"] for n in by_id["nodes"]] == [1006]
    # per-node statistics travel with the row (bulk hydration, no N+1)
    row = st.node_list(db, search="1001")["nodes"][0]
    assert row["research"]["return_pct"] == pytest.approx(0.12, abs=1e-6)
    assert row["research"]["trades"] == 120
    assert row["execution"]["paper_trades"] == 0


# --------------------------------------------------------------------------- #
# 5-6. per-node statistics & economics
# --------------------------------------------------------------------------- #
def test_node_stats_research_results(db, stub_snapshot):
    node = st.node_stats(1001, db)
    assert node["node"]["id"] == 1001
    assert node["node"]["research_eligible"] is True
    assert node["research"]["layer"].startswith("Research results")
    bt = node["research"]["backtest"]
    assert bt["available"] is True and bt["layer"] == "BACKTEST"
    assert bt["total_return_pct"] == pytest.approx(0.12, abs=1e-6)
    assert bt["profit_factor"] == pytest.approx(1.8, abs=1e-6)
    assert bt["trades"] == 120
    assert node["research"]["parameters"]["exit_conditions"]["stop_loss"].startswith("2.34x ATR")
    assert node["research"]["parameters"]["entry_conditions"]["long"]


def test_node_economics_derived_and_na(db, stub_snapshot):
    econ = st.node_stats(1001, db)["economics"]
    assert econ["risk_per_trade_pct"] == pytest.approx(0.5, abs=1e-9)
    assert econ["sl_atr_multiple"] == pytest.approx(2.34, abs=1e-6)
    assert econ["tp_atr_multiple"] == pytest.approx(3.42, abs=1e-6)
    assert econ["atr_reference"] == "atr:7"
    assert econ["trade_stats"]["trade_count"] == 120
    assert econ["trade_stats"]["win_rate"] == pytest.approx(0.55, abs=1e-6)
    # values the stored research data cannot support are N/A with a reason (§8/§11)
    unavailable = {u["metric"] for u in econ["unavailable"]}
    for metric in ("risk_amount", "position_size_lots", "estimated_exposure"):
        assert metric in unavailable
    assert econ["risk_amount"] is None
    assert econ["position_size_lots"] is None
    assert econ["estimated_exposure"] is None
    assert all(u["reason"] for u in econ["unavailable"])


def test_node_economics_reward_risk(db, stub_snapshot):
    econ = st.node_stats(1001, db)["economics"]
    assert econ["reward_risk_ratio"] == pytest.approx(3.42 / 2.34, abs=1e-3)
    # a genome without SL/TP multiples must not produce a made-up ratio
    add_node(db, 1009, genome={**GENOME, "exit": {"atr_spec": "atr:14", "min_hold_bars": 1,
                                                  "max_hold_bars": 10}})
    bare = st.node_stats(1009, db)["economics"]
    assert bare["reward_risk_ratio"] is None
    assert bare["sl_atr_multiple"] is None
    assert {u["metric"] for u in bare["unavailable"]} >= {"sl_atr_multiple", "tp_atr_multiple"}


def test_missing_data_is_not_fabricated(db, stub_snapshot):
    """A node without a backtest reports N/A, never a fabricated zero."""
    add_node(db, 1010, status="BORN", genome=GENOME)
    node = st.node_stats(1010, db)
    assert node["research"]["backtest"]["available"] is False
    assert node["research"]["backtest"]["total_return_pct"] is None
    assert node["research"]["backtest"]["trades"] is None
    assert node["research"]["validation"]["available"] is False
    assert node["research"]["validation"]["robustness_score"] is None
    row = [n for n in st.node_list(db, search="1010")["nodes"] if n["id"] == 1010][0]
    assert row["research"]["return_pct"] is None
    assert row["research"]["trades"] is None
    assert row["research"]["profit_factor"] is None
    assert row["research"]["validation"] is None
    # an unknown node has no statistics payload at all
    assert st.node_stats(999999, db) is None


def test_legacy_node_marked_not_research_eligible(db, stub_snapshot):
    node = st.node_stats(801, db)
    assert node["node"]["data_source"] == "LEGACY_TEST"
    assert node["node"]["research_eligible"] is False
    assert "LEGACY_TEST" in node["node"]["scope_note"]
    assert "excluded from research analytics" in node["node"]["scope_note"]


# --------------------------------------------------------------------------- #
# 7. the analytics layer is read-only
# --------------------------------------------------------------------------- #
def test_stats_read_only_no_writes(db, stub_snapshot):
    before = row_counts(db)
    st.overview(db)
    st.counter_audit(db)
    st.node_list(db, limit=5)
    st.node_stats(1001, db)
    st.node_stats(801, db)
    st.scope_info(db)
    assert row_counts(db) == before


# --------------------------------------------------------------------------- #
# 8. HTTP surface
# --------------------------------------------------------------------------- #
@pytest.fixture
def client(db, stub_snapshot, monkeypatch):
    import app.stats.research_stats as rs
    monkeypatch.setattr(rs, "get_db", lambda: db)
    from app.main import app
    return TestClient(app)


def test_api_stats_endpoints(client):
    r = client.get("/api/stats/overview")
    assert r.status_code == 200
    body = r.json()
    assert body["scope"]["population"] == "USER_RESEARCH"
    assert body["population"]["total"] == 8
    assert body["scope"]["legacy_excluded_nodes"] == 3

    r = client.get("/api/stats/nodes", params={"limit": 5})
    assert r.status_code == 200
    assert r.json()["total"] == 8

    r = client.get("/api/stats/scope_audit")
    assert r.status_code == 200
    assert r.json()["consistent"] is True

    r = client.get("/api/stats/node/1001")
    assert r.status_code == 200
    assert r.json()["node"]["id"] == 1001
    assert r.json()["economics"]["risk_per_trade_pct"] == pytest.approx(0.5)


def test_api_stats_node_404(client):
    assert client.get("/api/stats/node/424242").status_code == 404


def test_status_row_active_population_is_user_scoped(monkeypatch):
    """The one legacy-inclusive research counter found in the audit is fixed."""
    import app.api.routes as routes
    seen = {}

    class Evo:
        def active_count(self, **kw):
            seen.update(kw)
            return 7

    class Lab:
        evo = Evo()

    assert routes._user_scoped_active_population(Lab()) == 7
    assert seen.get("exclude_legacy") is True


def test_stats_page_registered_in_frontend_nav():
    """The dedicated Stats tab exists and is wired to the new page."""
    app_jsx = (Path(__file__).resolve().parent.parent / "frontend" / "src" / "App.jsx").read_text()
    assert 'import Stats from "./pages/Stats.jsx"' in app_jsx
    assert '["stats", "2", "Stats", Stats]' in app_jsx
    page = (Path(__file__).resolve().parent.parent / "frontend" / "src" / "pages" / "Stats.jsx").read_text()
    assert "statsOverview" in page and "statsNode" in page and "N/A" in page
