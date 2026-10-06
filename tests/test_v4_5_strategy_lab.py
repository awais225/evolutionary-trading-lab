"""V4.5 focused tests — Strategy Lab / Backtest Matrix (research interface).

Safety of the test setup (same pattern as the V4.4 suite):

* everything runs against a **temporary SQLite database** (``tmp_path``); the
  authoritative DATA database is never opened, read or written here;
* the engine snapshot is stubbed where a deterministic number is needed;
* an explicit test asserts the new endpoints perform **no writes** at all;
* query counts are asserted, so a listing never degenerates into N+1 queries.

Covered: USER_RESEARCH scope, legacy exclusion (and the explicit diagnostic
escape hatch), server-side filtering, deterministic pagination and sorting,
missing-value handling, the matrix comparison, the side-by-side comparison
(which reuses the V4.4 node statistics), read-only behaviour, the HTTP surface
and the frontend wiring.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db.database import Database
from app.stats import research_stats as rs
from app.stats import strategy_lab as sl

# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #
GENOME = {
    "symbol": "XAUUSD", "timeframe": "M15", "direction": "both",
    "risk": {"risk_per_trade": 0.005, "max_concurrent": 1},
    "exit": {"atr_spec": "atr:7", "sl_atr_mult": 2.0, "tp_atr_mult": 4.0,
             "trailing": None, "min_hold_bars": 1, "max_hold_bars": 24,
             "exit_condition": None},
    "entry_long": {"op": "gt", "left": {"feature": "rsi:14"}, "right": {"const": 55}},
    "entry_short": {"op": "lt", "left": {"feature": "rsi:14"}, "right": {"const": 45}},
    "features": ["rsi:14"], "sessions": ["london"], "days": [1, 2, 3], "regime_filters": [],
}

FULL_METRICS = {
    "total_return_pct": 0.25, "net_profit": 2500.0, "profit_factor": 1.8, "win_rate": 0.60,
    "trades": 100, "max_drawdown_pct": 0.05, "sharpe": 1.5, "sortino": 2.0,
    "expectancy": 0.0025, "avg_trade": 25.0, "avg_hold_bars": 6.0, "consistency": 0.7,
    "gross_profit": 6000.0, "gross_loss": 3500.0, "final_equity": 12500.0,
    "exit_reasons": {"tp": 60, "sl": 40},
}


def add_node(db, node_id: int, *, status="SURVIVED", data_source="USER_RESEARCH",
             generation=3, fitness=None, genome=None, symbol="XAUUSD", timeframe="M15") -> int:
    g = dict(genome or GENOME)
    g["symbol"], g["timeframe"] = symbol, timeframe
    db.x("""INSERT OR REPLACE INTO strategies
            (id, hash, parent_id, generation, symbol, timeframe, direction, status,
             genome, complexity, fitness, created_at, updated_at, origin, run_id,
             data_source, research_node_num)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
         (node_id, f"hash{node_id}", None, generation, symbol, timeframe, g["direction"],
          status, json.dumps(g), 3, fitness, time.time(), time.time(), "research",
          "RUN-V45-TEST", data_source, node_id))
    return node_id


def add_backtest(db, sid: int, *, stage="detail", metrics=None, created_at=None) -> None:
    db.x("""INSERT INTO backtests (strategy_id, stage, dataset_id, window, params, metrics,
                                   fitness, verdict, created_at, fingerprint)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
         (sid, stage, "XAUUSD_M15", json.dumps({"start": 0, "end": 100}),
          json.dumps({"risk": 0.005}), json.dumps(metrics if metrics is not None else FULL_METRICS),
          0.9, "PASS", created_at or time.time(), f"fp{sid}-{stage}"))


def add_validation(db, sid: int, *, passed=True, robustness=0.9, oos_return=0.15,
                   oos_pf=1.4) -> None:
    db.x("""INSERT INTO validations (strategy_id, oos, robustness_score, passed, notes, created_at)
            VALUES (?,?,?,?,?,?)""",
         (sid, json.dumps({"metrics": {"total_return_pct": oos_return, "profit_factor": oos_pf},
                           "degradation": 0.2}),
          robustness, 1 if passed else 0, json.dumps([]), time.time()))


@pytest.fixture
def db(tmp_path):
    """Scratch database: 4 user-research nodes (+ 40 filler) and 1 legacy node."""
    d = Database(str(tmp_path / "v45_lab.db"))
    try:
        d.get_shortlist()          # created lazily by the database layer
    except Exception:
        pass
    for t in ("strategies", "backtests", "validations", "paper_trades", "mt5_demo_trades",
              "live_test_trades", "research_shortlist", "strategy_pipeline_states", "matrices"):
        try:
            d.x(f"DELETE FROM {t}")
        except Exception:
            pass

    # --- the interesting nodes -------------------------------------------------
    add_node(d, 2001, status="QUALIFIED", fitness=0.88, generation=3)
    add_backtest(d, 2001, stage="screen", metrics={**FULL_METRICS, "trades": 30,
                                                   "total_return_pct": 0.05})
    add_backtest(d, 2001, stage="detail")                       # newest detail wins
    add_validation(d, 2001, passed=True, robustness=0.9)
    d.x("INSERT OR REPLACE INTO research_shortlist (strategy_id, notes, created_at) VALUES (?,?,?)",
        (2001, "", time.time()))

    add_node(d, 2002, status="SURVIVED", fitness=0.70, generation=3,
             genome={**GENOME, "exit": {**GENOME["exit"], "sl_atr_mult": 3.0, "tp_atr_mult": 3.0}})
    add_backtest(d, 2002, stage="screen", metrics={**FULL_METRICS, "trades": 40, "win_rate": 0.45,
                                                   "profit_factor": 1.1, "total_return_pct": 0.04,
                                                   "max_drawdown_pct": 0.09})

    add_node(d, 2003, status="FAILED", generation=4)            # no backtest, no validation

    add_node(d, 2004, status="QUALIFIED", fitness=0.90, generation=4, timeframe="M5")
    add_backtest(d, 2004, stage="detail", metrics={"trades": 10})   # partial metrics only

    add_node(d, 2005, status="SURVIVED", generation=4,
             genome={**GENOME, "exit": {"atr_spec": "atr:14", "min_hold_bars": 1,
                                        "max_hold_bars": 10}})       # no SL/TP multiples

    # --- filler so paging / query-count behaviour is meaningful -----------------
    for i in range(40):
        nid = 3000 + i
        add_node(d, nid, status="FAILED" if i % 3 else "SURVIVED", generation=5 + (i % 3),
                 fitness=None if i % 4 else 0.5 + i / 100.0,
                 symbol="XAUUSD" if i % 2 else "EURUSD", timeframe="M15" if i % 2 else "H1")

    # --- legacy infrastructure node: must never show up in the research scope ---
    add_node(d, 901, status="QUALIFIED", data_source="LEGACY_TEST", generation=0, fitness=0.99)
    add_backtest(d, 901, stage="detail", metrics={**FULL_METRICS, "total_return_pct": 9.99})
    add_validation(d, 901, passed=True, robustness=0.99)

    # --- execution / audit rows (separate layer) --------------------------------
    now = time.time()
    d.x("""INSERT INTO paper_trades (strategy_id, symbol, side, signal_ts, lots, pnl, status,
                                     source, exec_ts)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (2001, "XAUUSD", "BUY", now, 0.10, 25.0, "CLOSED", "SIMULATOR", now))
    d.x("""INSERT INTO live_test_trades (strategy_id, symbol, timeframe, side, entry_price, lots,
                                         pnl, pnl_pct, open_ts, status)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (2001, "XAUUSD", "M15", "BUY", 2400.0, 0.2, 10.0, 0.0, now, "CLOSED"))
    return d


@pytest.fixture
def stub_snapshot(monkeypatch):
    """Deterministic 'engine snapshot empty' behaviour for the statistics block."""
    monkeypatch.setattr(rs, "_engine_snapshot", lambda: {})
    return None


class CountingDatabase(Database):
    """A real Database that counts the SQL statements issued through it."""

    def __init__(self, path: str):
        super().__init__(path)
        self.statements = 0

    # nb: Database.one() delegates to self.q(), so counting q() alone counts
    # every statement exactly once.
    def q(self, sql, args=()):
        self.statements += 1
        return super().q(sql, args)

    def x(self, sql, args=()):
        self.statements += 1
        return super().x(sql, args)


def row_counts(db) -> dict:
    return {t: db.one(f"SELECT COUNT(*) c FROM {t}")["c"]
            for t in ("strategies", "backtests", "validations", "paper_trades",
                      "live_test_trades", "research_shortlist")}


def _assert_json_safe(obj, path="payload"):
    """No NaN/Infinity, no raw database objects, no non-JSON values."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            _assert_json_safe(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _assert_json_safe(v, f"{path}[{i}]")
    elif isinstance(obj, float):
        assert not math.isnan(obj) and not math.isinf(obj), f"{path} is not finite: {obj}"
    else:
        assert obj is None or isinstance(obj, (str, int, bool)), \
            f"{path} holds a non-JSON value: {type(obj)}"
    # the whole payload must survive a JSON round trip (no [object Object] risk)
    json.dumps(obj)


# --------------------------------------------------------------------------- #
# 1-4. strategy list: scope, paging, filters, sorting
# --------------------------------------------------------------------------- #
def test_strategy_list_scope_and_shape(db, stub_snapshot):
    res = sl.strategy_list(db, limit=5)
    assert res["scope"] == "USER_RESEARCH"
    assert res["include_legacy"] is False
    assert res["population_total"] == 45           # 5 interesting + 40 filler
    assert res["legacy_excluded_total"] == 1
    assert res["total"] == 45                      # the legacy node is not listed
    assert res["limit"] == 5 and res["returned"] == 5 and res["pages"] == 9
    assert res["labels"]["backtest"] == "BACKTEST" and res["labels"]["live_test"] == "LIVE TEST"
    node = res["nodes"][0]
    assert set(node) >= {"id", "node_id", "generation", "status", "symbol", "timeframe",
                         "research", "validation", "economics", "execution"}
    assert node["research"]["stage"] == "detail"   # detail beats screen
    assert node["research"]["layer"] == "BACKTEST"
    assert node["execution"]["informational_only"] is True
    assert node["execution"]["layer"] == "EXECUTION"
    _assert_json_safe(res)


def test_pagination_is_deterministic_and_non_overlapping(db, stub_snapshot):
    p1 = sl.strategy_list(db, limit=10, offset=0, sort="id", dir="desc")
    p2 = sl.strategy_list(db, limit=10, offset=10, sort="id", dir="desc")
    ids1 = [n["id"] for n in p1["nodes"]]
    ids2 = [n["id"] for n in p2["nodes"]]
    assert len(ids1) == 10 and len(ids2) == 10
    assert not set(ids1) & set(ids2)
    assert ids1 == sorted(ids1, reverse=True) and ids2 == sorted(ids2, reverse=True)
    assert p1["total"] == p2["total"] == 45
    # repeating the same page returns the identical order (tie-broken by id)
    again = sl.strategy_list(db, limit=10, offset=0, sort="status", dir="asc")
    assert [n["id"] for n in again["nodes"]] == \
           [n["id"] for n in sl.strategy_list(db, limit=10, offset=0, sort="status", dir="asc")["nodes"]]
    beyond = sl.strategy_list(db, limit=10, offset=500, sort="id")
    assert beyond["returned"] == 0 and beyond["total"] == 45


def test_filters_are_server_side_and_combined(db, stub_snapshot):
    assert sl.strategy_list(db, generation=3)["total"] == 2
    assert sl.strategy_list(db, generation="3,4")["total"] == 5
    assert sl.strategy_list(db, status="FAILED")["total"] == 1 + 26   # node 2003 + filler
    assert sl.strategy_list(db, qualified=True, generation=4)["total"] == 1
    assert sl.strategy_list(db, timeframe="M5")["total"] == 1
    assert sl.strategy_list(db, symbol="EURUSD")["total"] == 20   # filler only
    assert sl.strategy_list(db, has_backtest=True)["total"] == 3
    assert sl.strategy_list(db, has_validation=True)["total"] == 1
    assert sl.strategy_list(db, validated_passed=True)["total"] == 1
    assert sl.strategy_list(db, stage="screen")["total"] == 1
    assert sl.strategy_list(db, stage="none")["total"] == 42
    assert sl.strategy_list(db, shortlist_only=True)["total"] == 1
    # metric thresholds run in SQL over the stored backtest values
    assert sl.strategy_list(db, min_return=0.2)["total"] == 1
    assert sl.strategy_list(db, min_profit_factor=1.5)["total"] == 1
    assert sl.strategy_list(db, min_trades=50)["total"] == 1
    assert sl.strategy_list(db, max_drawdown=0.06)["total"] == 1
    # search by node id / research node number
    by_id = sl.strategy_list(db, search="2004")
    assert [n["id"] for n in by_id["nodes"]] == [2004]
    # the applied filters are echoed back so the UI can show them
    echo = sl.strategy_list(db, symbol="XAUUSD", qualified=True)["filters"]
    assert echo["scope"] == "USER_RESEARCH" and echo["symbol"] == "XAUUSD" and echo["qualified"] is True
    # an unknown filter value is ignored, never silently turned into a match-all default
    assert sl.strategy_list(db, status="NO_SUCH_STATUS")["total"] == 0


def test_sorting_is_null_safe_and_tie_broken(db, stub_snapshot):
    best = sl.strategy_list(db, sort="return", dir="desc", limit=3)["nodes"]
    assert [n["id"] for n in best] == [2001, 2002, 2003]      # 0.25 / 0.04 / nodata
    assert best[0]["research"]["return_pct"] == pytest.approx(0.25)
    assert best[-1]["research"]["return_pct"] is None         # unknown stays last, never 0
    worst = sl.strategy_list(db, sort="return", dir="asc", limit=4)["nodes"]
    assert [n["id"] for n in worst] == [2002, 2001, 2003, 2004]   # ascending, NULLS LAST
    dd = sl.strategy_list(db, sort="drawdown", dir="asc", limit=2)["nodes"]
    assert dd[0]["id"] == 2001 and dd[0]["research"]["max_drawdown_pct"] == pytest.approx(0.05)
    rob = sl.strategy_list(db, sort="robustness", dir="desc", limit=2)["nodes"]
    assert rob[0]["id"] == 2001 and rob[0]["validation"]["robustness_score"] == pytest.approx(0.9)
    # unknown sort key falls back to the default deterministically
    fb = sl.strategy_list(db, sort="definitely_not_a_column", dir="desc", limit=2)
    assert fb["sort"] == "return" and len(fb["nodes"]) == 2


# --------------------------------------------------------------------------- #
# 5. legacy isolation
# --------------------------------------------------------------------------- #
def test_legacy_excluded_by_default_and_available_as_diagnostic(db, stub_snapshot):
    default = sl.strategy_list(db, limit=100)
    assert 901 not in [n["id"] for n in default["nodes"]]
    assert default["legacy_excluded_total"] == 1
    assert all(n["research_eligible"] for n in default["nodes"])
    # the explicit diagnostic scope still works (V4.0 escape hatch preserved)
    diag = sl.strategy_list(db, include_legacy=True, limit=100)
    assert diag["scope"] == "ALL" and diag["total"] == 46
    legacy_row = [n for n in diag["nodes"] if n["id"] == 901][0]
    assert legacy_row["data_source"] == "LEGACY_TEST"
    assert legacy_row["research_eligible"] is False
    # matrix + compare also stay scoped by default
    assert 901 not in [r["node"]["id"] for r in sl.matrix(db, limit=100)["rows"]]
    assert sl.matrix(db, include_legacy=True, ids="901")["rows"][0]["node"]["id"] == 901
    cmp_legacy = sl.compare(db, ids="901")["rows"][0]
    assert cmp_legacy["identity"]["research_eligible"] is False
    assert "LEGACY_TEST" in cmp_legacy["identity"]["scope_note"]


# --------------------------------------------------------------------------- #
# 6. missing values are explicit
# --------------------------------------------------------------------------- #
def test_missing_values_are_explicit_never_fabricated(db, stub_snapshot):
    node = sl.strategy_list(db, search="2003")["nodes"][0]
    assert node["research"]["available"] is False
    assert node["research"]["trades"] is None
    assert node["research"]["wins"] is None and node["research"]["losses"] is None
    assert node["research"]["profit_factor"] is None
    assert {u["metric"] for u in node["research"]["unavailable"]} >= {"backtest", "wins/losses"}
    assert node["validation"]["available"] is False
    assert node["validation"]["robustness_score"] is None
    assert node["validation"]["unavailable"][0]["reason"]
    # partial metrics: trades exist, win rate does not -> wins stay N/A
    partial = sl.strategy_list(db, search="2004")["nodes"][0]
    assert partial["research"]["trades"] == 10
    assert partial["research"]["wins"] is None
    assert any(u["metric"] == "wins/losses" for u in partial["research"]["unavailable"])
    # economics are genome-derived (independent of backtest metrics) ...
    assert partial["economics"]["reward_risk_ratio"] == pytest.approx(2.0)
    # ... and stay N/A when the genome itself has no stop/target multiple
    bare = sl.strategy_list(db, search="2005")["nodes"][0]
    assert bare["economics"]["reward_risk_ratio"] is None
    assert bare["economics"]["sl_atr_multiple"] is None
    assert bare["economics"]["tp_atr_multiple"] is None
    assert bare["economics"]["atr_reference"] == "atr:14"
    # a full node derives wins/losses from two stored values and flags it
    full = sl.strategy_list(db, search="2001")["nodes"][0]
    assert full["research"]["wins"] == 60 and full["research"]["losses"] == 40
    assert full["research"]["wins_derived"] is True and full["research"]["wins_source"]
    assert full["economics"]["reward_risk_ratio"] == pytest.approx(2.0)
    _assert_json_safe(node)


# --------------------------------------------------------------------------- #
# 7-8. backtest matrix
# --------------------------------------------------------------------------- #
def test_matrix_by_ids_keeps_order_and_separates_layers(db, stub_snapshot):
    res = sl.matrix(db, ids="2002,2001,999999")
    assert res["mode"] == "ids"
    assert [r["node"]["id"] for r in res["rows"]] == [2002, 2001]     # requested order
    assert res["missing_ids"] == [999999]                            # explicit, not silent
    assert res["count"] == 2
    row = res["rows"][1]["row"]
    assert row["id"] == 2001 and row["stage"] == "detail"
    assert row["trades"] == 100 and row["wins"] == 60
    assert row["profit_factor"] == pytest.approx(1.8)
    assert row["reward_risk_ratio"] == pytest.approx(2.0)
    assert row["validation_passed"] is True and row["qualified"] is True
    # execution records live in their own labelled column, never merged
    ex = row["execution_records"]
    assert ex["layer"] == "EXECUTION" and ex["informational_only"] is True
    assert ex["paper_trades"] == 1 and ex["live_test_trades"] == 1 and ex["records_total"] == 2
    # ... and the research numbers are untouched by those audit rows
    assert row["trades"] == 100 and row["net_profit"] == pytest.approx(2500.0)
    columns = {c["key"]: c for c in res["columns"]}
    assert columns["wins"]["derived"] is True
    assert columns["reward_risk_ratio"]["derived"] is True
    assert columns["execution_records"]["layer"] == "EXECUTION"
    _assert_json_safe(res)


def test_matrix_filter_mode_and_limit(db, stub_snapshot):
    res = sl.matrix(db, qualified=True, sort="return", dir="desc")
    assert res["mode"] == "filter"
    assert res["total_matching"] == 2 and res["count"] == 2
    assert [r["node"]["id"] for r in res["rows"]] == [2001, 2004]
    capped = sl.matrix(db, limit=100000)
    assert capped["limit"] == sl.MAX_MATRIX_ROWS
    assert sl.matrix(db, ids="")["mode"] == "filter"      # empty ids -> filter mode


# --------------------------------------------------------------------------- #
# 9. comparison reuses the V4.4 node statistics
# --------------------------------------------------------------------------- #
def test_compare_reuses_v44_node_stats(db, stub_snapshot):
    res = sl.compare(db, ids=[2001, 2002])
    assert res["count"] == 2 and res["max_compare"] == sl.MAX_COMPARE
    assert res["not_found"] == []
    a = res["rows"][0]
    assert a["identity"]["node_id"] == "Node_2001" and a["identity"]["generation"] == 3
    assert a["identity"]["research_eligible"] is True
    assert a["definition"]["indicators"] is not None
    assert a["definition"]["entry_conditions"]["long"]
    assert a["definition"]["exit_conditions"]["stop_loss"].startswith("2.00x ATR")
    assert a["research_results"]["backtest"]["profit_factor"] == pytest.approx(1.8)
    assert a["research_results"]["qualification"]["qualified"] is True
    assert a["research_results"]["trades"] == 100
    # economics come from the V4.4 implementation, not a second formula
    v44 = rs.node_stats(2001, db)["economics"]
    assert a["node_economics"] == v44
    assert a["node_economics"]["risk_per_trade_pct"] == pytest.approx(0.5)
    assert a["node_economics"]["reward_risk_ratio"] == pytest.approx(2.0)
    assert a["node_economics"]["risk_amount"] is None            # unsupported -> N/A
    # execution records stay separated and informational
    assert a["execution"]["layer"] == "EXECUTION"
    _assert_json_safe(res)


def test_compare_guards(db, stub_snapshot):
    over = sl.compare(db, ids=list(range(2001, 2010)))           # 9 ids > MAX_COMPARE
    assert over["count"] == 0 and "at most" in over["error"]
    assert over["not_found"] == list(range(2001, 2010))[sl.MAX_COMPARE:]
    empty = sl.compare(db, ids=[])
    assert empty["count"] == 0 and "no strategy ids" in empty["error"]
    unknown = sl.compare(db, ids=[2001, 777777])
    assert unknown["not_found"] == [777777] and unknown["count"] == 1


# --------------------------------------------------------------------------- #
# 10. no N+1: query counts are constant, listings are SQL-side
# --------------------------------------------------------------------------- #
def test_query_counts_do_not_scale_with_rows(tmp_path):
    d = CountingDatabase(str(tmp_path / "count.db"))
    for i in range(30):
        add_node(d, 5000 + i, status="SURVIVED", generation=2)
    add_backtest(d, 5000, stage="detail")
    d.statements = 0
    sl.strategy_list(d, limit=1)
    one_row = d.statements
    d.statements = 0
    sl.strategy_list(d, limit=30, offset=0)
    many_rows = d.statements
    assert one_row == many_rows <= 6, (one_row, many_rows)
    d.statements = 0
    sl.matrix(d, ids="5000,5001,5002,5003,5004")
    assert d.statements == 1
    d.statements = 0
    sl.strategy_list(d, limit=10, min_return=-0.5, status="SURVIVED", generation=2)
    assert d.statements <= 6
    d.statements = 0
    sl.facets(d)
    assert d.statements <= 16


# --------------------------------------------------------------------------- #
# 11. HTTP surface
# --------------------------------------------------------------------------- #
@pytest.fixture
def client(db, stub_snapshot, monkeypatch):
    monkeypatch.setattr(rs, "get_db", lambda: db)
    import app.db.database as database_mod
    monkeypatch.setattr(database_mod, "get_db", lambda: db)
    from app.main import app
    return TestClient(app)


def test_strategy_lab_http_endpoints(client):
    r = client.get("/api/research/strategies", params={"limit": 3, "sort": "return"})
    assert r.status_code == 200
    body = r.json()
    assert body["scope"] == "USER_RESEARCH" and body["total"] == 45
    assert len(body["nodes"]) == 3 and body["nodes"][0]["id"] == 2001
    assert "NaN" not in r.text and "Infinity" not in r.text

    r = client.get("/api/research/strategies", params={"qualified": "true"})
    assert r.status_code == 200 and [n["id"] for n in r.json()["nodes"]] == [2001, 2004]

    r = client.get("/api/research/facets")
    assert r.status_code == 200
    facets = r.json()
    assert facets["scope"] == "USER_RESEARCH"
    assert facets["scope_info"]["legacy_excluded_nodes"] == 1
    assert {o["value"] for o in facets["options"]["status"]} >= {"QUALIFIED", "SURVIVED", "FAILED"}
    assert facets["population"]["total"] == 45
    assert facets["limits"]["max_compare"] == 8

    r = client.get("/api/research/matrix", params={"ids": "2001,2004"})
    assert r.status_code == 200
    m = r.json()
    assert [x["node"]["id"] for x in m["rows"]] == [2001, 2004]
    assert m["columns"] and m["rows"][0]["row"]["execution_records"]["layer"] == "EXECUTION"

    r = client.get("/api/research/compare", params={"ids": "2001,2004"})
    assert r.status_code == 200
    c = r.json()
    assert c["count"] == 2 and c["rows"][0]["node_economics"]["risk_per_trade_pct"] == 0.5

    # a malformed selection is rejected explicitly instead of guessed
    assert client.get("/api/research/compare").status_code == 422


def test_strategy_lab_is_read_only(db, stub_snapshot):
    before = row_counts(db)
    sl.strategy_list(db, limit=20)
    sl.facets(db)
    sl.matrix(db, ids="2001,2003")
    sl.matrix(db, qualified=True)
    sl.compare(db, ids=[2001, 2002, 2004])
    assert row_counts(db) == before


# --------------------------------------------------------------------------- #
# 12. frontend wiring (the UI is wired to the new endpoints, no duplicate
#     node-detail implementation, styles follow the existing pages)
# --------------------------------------------------------------------------- #
def test_frontend_is_wired_to_the_v45_endpoints():
    root = Path(__file__).resolve().parent.parent
    api_js = (root / "frontend" / "src" / "api.js").read_text()
    for helper in ("researchStrategies:", "researchFacets:", "researchMatrix:", "researchCompare:"):
        assert helper in api_js, helper

    lab = (root / "frontend" / "src" / "pages" / "StrategyLab.jsx").read_text()
    assert "researchStrategies" in lab and "researchFacets" in lab and "researchCompare" in lab
    assert "BacktestMatrixTable" in lab                  # comparison table is shared, not copied
    assert "NodeResearchDetail" in lab                    # V4.4 node detail reused
    assert "selectedIds" in lab                           # multi-selection for comparison

    matrix_page = (root / "frontend" / "src" / "pages" / "BacktestMatrix.jsx").read_text()
    assert "BacktestMatrixTable" in matrix_page
    assert "generateMatrix" in matrix_page                # the V3.6 diagnostic view is preserved

    # one shared per-node detail implementation, reused by both pages
    detail = root / "frontend" / "src" / "components" / "NodeResearchDetail.jsx"
    assert detail.exists()
    detail_src = detail.read_text()
    for lane in ("RESEARCH RESULTS", "NODE ECONOMICS", "EXECUTION RECORDS", "NOT RESEARCH"):
        assert lane in detail_src, lane
    stats_page = (root / "frontend" / "src" / "pages" / "Stats.jsx").read_text()
    assert "NodeResearchDetail" in stats_page and "statsNode" in stats_page
    assert "statsNode" in lab          # the Strategy Lab consumes the same V4.4 payload
