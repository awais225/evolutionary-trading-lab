"""V6.8 §10 — node-data authority, cross-tab consistency, scenarios, filters,
coverage and sorting.

Freeze contract: NOTHING in this suite asserts a hardcoded production total.
Every expected value is read from the stored records through the established
formulas (status QUALIFIED+PAPER; best generation record; node_bucket labels),
and every displayed value is compared against its stored source.  Node #718 is
verified whenever the dataset contains it — with its values read from ITS
stored record, never hardcoded.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from app.db.database import Database  # noqa: E402

QUALIFIED_TIER_SQL = "status IN ('QUALIFIED','PAPER')"


def _seed(db: Database, sid: int, *, status="QUALIFIED", run_id="RUN-V68-SEED",
          research_num=None, data_source="USER_RESEARCH", metrics=None, symbol="XAUUSD",
          timeframe="M15"):
    now = time.time()
    db.x("""INSERT INTO strategies (id, status, symbol, timeframe, generation, fitness,
                                    research_node_num, data_source, created_at, updated_at,
                                    hash, genome, complexity, run_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
         (sid, status, symbol, timeframe, 1, 0.5, research_num, data_source, now, now,
          f"h{sid}", "{}", 1, run_id))
    if metrics is not None:
        db.x("""INSERT INTO backtests (strategy_id, stage, metrics, created_at, dataset_id)
                VALUES (?,?,?,?,?)""",
             (sid, "detail", json.dumps(metrics), now, f"{symbol}_{timeframe}"))


# --------------------------------------------------------------------------- #
# A — data consistency: one authority, displayed == stored
# --------------------------------------------------------------------------- #

class TestNodeDataAuthority:
    def test_qualified_count_consistent_across_endpoints_and_formulas(self, client):
        """The qualified count every tab shows equals the established formula
        everywhere: SQL QUALIFIED+PAPER, get_qualified_strategies(), the
        node-data authority and the per-run counters all agree."""
        nd = client.get("/api/node-data").json()["node_data"]
        q = nd["counts"]["qualified"]
        con = sqlite3.connect(str(__import__('app.config',fromlist=['get_config']).get_config().database_path))
        sql_q = con.execute(f"SELECT COUNT(*) FROM strategies WHERE {QUALIFIED_TIER_SQL} AND COALESCE(data_source,'')<>'LEGACY_TEST' AND status<>'EXCLUDED'").fetchone()[0]
        con.close()
        assert q == sql_q
        runs = client.get("/api/lab/runs").json()["runs"]
        run_sum = sum(int(r.get("qualified_nodes") or 0) for r in runs)
        db = Database(str(__import__('app.config',fromlist=['get_config']).get_config().database_path))
        engine_q = len(db.get_qualified_strategies())
        db.close()
        assert engine_q == q, "get_qualified_strategies() and the authority must agree"
        # every QUALIFIED-tier row belongs to exactly one run bucket (or no run)
        assert run_sum <= q
        st = client.get("/api/status").json()
        assert st.get("node_data_revision") == nd["revision"], \
            "the status stamp must name the same authority revision (cheap meta read)"
        rc = client.get("/api/lab/recheck").json()
        assert (rc.get("node_data") or {}).get("counts", {}).get("qualified") == q
        assert (rc.get("node_data") or {}).get("revision")

    def test_recheck_refreshes_the_same_source(self, client):
        a = client.get("/api/node-data").json()["node_data"]
        rc = client.get("/api/lab/recheck").json()["node_data"]
        assert rc["counts"] == a["counts"]
        assert rc["revision"] == a["revision"]

    def test_generation_metrics_displayed_equal_stored_everywhere(self, client):
        rows = client.get("/api/nodes?filter=all&limit=5&sort_by=node_id").json()["nodes"]
        pop = client.get("/api/population?limit=5&sort=id&sort_desc=false&include_legacy=true").json()["strategies"]
        by_id = {r["id"]: r for r in pop}
        con = sqlite3.connect(str(__import__('app.config',fromlist=['get_config']).get_config().database_path))
        con.row_factory = sqlite3.Row
        try:
            for r in rows[:5]:
                raw = con.execute("""SELECT metrics FROM backtests WHERE strategy_id=?
                                     AND stage IN ('detail','screen')
                                     ORDER BY CASE stage WHEN 'detail' THEN 0 ELSE 1 END,
                                              id DESC LIMIT 1""", (r["node_id"],)).fetchone()
                if not raw:
                    assert (r.get("generation_metrics") or {}).get("return_pct") is None
                    continue
                stored = json.loads(raw["metrics"])
                gm = r["generation_metrics"] or {}
                assert gm.get("return_pct") == pytest.approx(stored.get("total_return_pct"))
                assert gm.get("trades") == pytest.approx(float(stored.get("trades")))
                p = by_id.get(r["node_id"]) or {}
                assert p.get("return_pct") == pytest.approx(stored.get("total_return_pct"))
        finally:
            con.close()

    def test_node_718_identity_metrics_and_true_ranking(self, client):
        """The user's Node #718 contract — verified against the stored record
        whenever the dataset contains it; values are never hardcoded."""
        nd = client.get("/api/node-data").json()["node_data"]
        tops = nd["tops"]["return_pct"]
        con = sqlite3.connect(str(__import__('app.config',fromlist=['get_config']).get_config().database_path))
        con.row_factory = sqlite3.Row
        try:
            row718 = con.execute("SELECT id FROM strategies WHERE id=718 AND COALESCE(data_source,'')<>'LEGACY_TEST' AND status<>'EXCLUDED'").fetchone()
            if row718:
                raw = con.execute("""SELECT metrics FROM backtests WHERE strategy_id=718
                                     AND stage IN ('detail','screen')
                                     ORDER BY CASE stage WHEN 'detail' THEN 0 ELSE 1 END,
                                              id DESC LIMIT 1""").fetchone()
                assert raw, "Node #718 must keep its stored generation record"
                stored = json.loads(raw["metrics"])
                # found by identity search under BOTH the default and all filters
                for f in ("qualified", "all"):
                    res = client.get(f"/api/nodes?filter={f}&search=718&limit=0").json()["nodes"]
                    hit = [x for x in res if x["node_id"] == 718]
                    assert hit, f"Node #718 must be discoverable under filter={f}"
                    gm = hit[0]["generation_metrics"] or {}
                    assert gm.get("return_pct") == pytest.approx(stored.get("total_return_pct"))
                    assert gm.get("trades") == pytest.approx(float(stored.get("trades")))
                    assert gm.get("profit_factor") == pytest.approx(stored.get("profit_factor"))
                    assert hit[0]["node_label"] == "Node #718"
                # its TRUE rank: #1 of the stored records by generation return
                assert tops and tops[0]["canonical_node_id"] == 718
                assert tops[0]["generation_metrics"]["return_pct"] == pytest.approx(
                    stored.get("total_return_pct"))
                pop = client.get("/api/population?limit=1&sort=return_pct&sort_desc=true"
                                 "&include_legacy=true").json()["strategies"]
                assert pop and pop[0]["id"] == 718
            else:
                # dataset without preserved history: the same contract must hold
                # for whatever node IS the stored top-return record
                assert tops, "the authority must rank the stored records"
                top_id = tops[0]["canonical_node_id"]
                pop = client.get("/api/population?limit=1&sort=return_pct&sort_desc=true"
                                 "&include_legacy=true").json()["strategies"]
                assert pop and pop[0]["id"] == top_id
        finally:
            con.close()

    def test_frozen_qualification_semantics_unchanged(self, tmp_path):
        """SURVIVED stays VALID-bucket but OUTSIDE the QUALIFIED tier;
        QUALIFIED stays inside both. Neither rule is reinterpreted."""
        from app.status import node_bucket
        db = Database(str(tmp_path / "frozen.db"))
        _seed(db, 10, status="SURVIVED")
        _seed(db, 11, status="QUALIFIED")
        _seed(db, 12, status="BORN")
        surv = dict(db.one("SELECT * FROM strategies WHERE id=10"))
        qual = dict(db.one("SELECT * FROM strategies WHERE id=11"))
        born = dict(db.one("SELECT * FROM strategies WHERE id=12"))
        assert node_bucket(surv)["bucket"] == "qualified"      # frozen V5 mapping
        assert node_bucket(qual)["bucket"] == "qualified"
        assert node_bucket(born)["bucket"] == "alive"
        qs = {r["id"] for r in db.get_qualified_strategies()}
        assert qs == {11}, "the QUALIFIED tier is QUALIFIED+PAPER only (frozen)"
        db.close()

    def test_no_stale_snapshot_is_served(self, tmp_path):
        p = str(tmp_path / "stale.db")
        db = Database(p)
        _seed(db, 901, metrics={"total_return_pct": 0.1, "trades": 5, "profit_factor": 1.2})
        from app.research.populations import inventory_snapshot, db_state_token
        s1 = inventory_snapshot(p)
        _seed(db, 902, metrics={"total_return_pct": 0.2, "trades": 9, "profit_factor": 2.2})
        s2 = inventory_snapshot(p)
        assert s1["state_token"] != s2["state_token"], "state change must change the token"
        assert s1["inventory_revision"] != s2["inventory_revision"]
        db.close()

    def test_sorting_never_mutates_records_or_qualification(self, client):
        con = sqlite3.connect(str(__import__('app.config',fromlist=['get_config']).get_config().database_path))
        before = con.execute(f"SELECT COUNT(*), SUM(CASE WHEN {QUALIFIED_TIER_SQL} THEN 1 ELSE 0 END) "
                             "FROM strategies").fetchone()
        con.close()
        for sort in ("return_pct", "trades", "pf"):
            client.get(f"/api/population?limit=3&sort={sort}&sort_desc=true&include_legacy=true")
            client.get(f"/api/population?limit=3&sort={sort}&sort_desc=false&include_legacy=true")
        con = sqlite3.connect(str(__import__('app.config',fromlist=['get_config']).get_config().database_path))
        after = con.execute(f"SELECT COUNT(*), SUM(CASE WHEN {QUALIFIED_TIER_SQL} THEN 1 ELSE 0 END) "
                            "FROM strategies").fetchone()
        con.close()
        assert before == after

    def test_missing_metrics_stay_missing_and_zeros_stay_zero(self, tmp_path):
        p = str(tmp_path / "mm.db")
        db = Database(p)
        _seed(db, 901, status="QUALIFIED",
              metrics={"total_return_pct": 0.0, "trades": 0, "profit_factor": 0.0})
        _seed(db, 902, status="QUALIFIED", metrics=None)
        from app.research.node_data import node_data_snapshot
        nd = node_data_snapshot(p)
        trades_rank = {t["canonical_node_id"]: t for t in nd["tops"]["trades"]}
        if 901 in trades_rank:
            assert trades_rank[901]["generation_metrics"]["trades"] == 0.0
        assert 902 not in trades_rank, "missing metrics never rank as zero"
        db.close()

    def test_tabs_share_records_and_counts(self, client):
        """Changing tabs neither changes the node count nor replaces the source:
        the DeepTesting row universe and the authority describe the same rows."""
        nd = client.get("/api/node-data").json()["node_data"]
        all_rows = client.get("/api/nodes?filter=all&limit=0").json()
        assert all_rows["total"] == nd["counts"]["total"]
        assert all_rows.get("data_revision") is not None


# --------------------------------------------------------------------------- #
# C — scenarios: multiple per node, comparison, immutability
# --------------------------------------------------------------------------- #

class TestScenarioCompare:
    def _seed_two(self, db):
        from app.deeptest import store as S
        S.ensure_schema(db)
        _seed(db, 501, status="QUALIFIED")
        for i, (ret, pnl, sig) in enumerate(((0.10, 100.0, "s1"), (0.20, 200.0, "s2"))):
            S.insert_scenario(db, {
                "run_id": f"v68sc{i}", "node_id": 501, "status": "COMPLETED",
                "signature": f"sig-{sig}", "symbol": "XAUUSD", "timeframe": "M15",
                "duration_key": "3M", "requested_start": 1000.0 + i, "requested_end": 2000.0 + i,
                "actual_start": 1000.0, "actual_end": 2000.0, "bars_used": 100 + i,
                "dataset_fingerprint": f"fp{i}", "genome_hash": f"g{i}", "config_hash": f"c{i}",
                "settings": {"total_capital": 10000.0, "risk_pct": 1.0},
                "analytics": {"summary": {"net_profit": pnl, "net_return_pct": ret,
                                          "profit_factor": 1.5 + i, "max_drawdown_pct": 0.05,
                                          "trades": 10 + i, "win_rate": 0.6,
                                          "expectancy_per_trade": 1.0, "avg_trade": 2.0,
                                          "sortino": 3.0}}})
        return ["v68sc0", "v68sc1"]

    def test_both_scenarios_individually_addressable(self, tmp_path):
        db = Database(str(tmp_path / "sc.db"))
        from app.deeptest.scenarios import node_deeptest_summary
        self._seed_two(db)
        summ = node_deeptest_summary(db, 501)
        scs = summ["scenarios"]
        by_id = {x["run_id"]: x for x in scs}
        assert set(by_id) == {"v68sc0", "v68sc1"}, "every saved scenario is individually addressable"
        # V10.5: these deliberately summary-only fixtures have NO saved trades,
        # and their legacy return values disagree with their capital/P&L. The
        # source records stay intact; the authoritative read must refuse invention.
        assert by_id['v68sc0']['headline']['net_return_pct'] is None
        assert by_id['v68sc1']['headline']['net_return_pct'] is None
        assert all(row['metric_provenance']['integrity']=='MISSING_TRADE_RECORDS' for row in scs)
        from app.deeptest import store as S
        assert S.get_scenario(db,'v68sc0')['analytics']['summary']['net_return_pct']==.10
        assert S.get_scenario(db,'v68sc1')['analytics']['summary']['net_return_pct']==.20
        assert by_id["v68sc0"]["is_preferred"] or by_id["v68sc1"]["is_preferred"]
        db.close()

    def test_compare_shows_own_metrics_deltas_and_warnings(self, tmp_path):
        db = Database(str(tmp_path / "sc.db"))
        from app.deeptest.scenarios import compare
        ids = self._seed_two(db)
        row0_before = json.dumps(dict(db.one("SELECT * FROM deeptest_scenarios WHERE run_id=?", (ids[0],))),
                                 sort_keys=True, default=str)
        row1_before = json.dumps(dict(db.one("SELECT * FROM deeptest_scenarios WHERE run_id=?", (ids[1],))),
                                 sort_keys=True, default=str)
        res = compare(db, ids)
        assert res["ok"]
        # each side carries its OWN saved metrics
        net = [m for m in res["metrics"] if m["metric"] == "net_profit"][0]
        assert net['left'] is None and net['right'] is None and net['delta'] is None
        assert not db.q('SELECT * FROM deeptest_trades'), 'Summary-only fixture intentionally has no calculable trade population'
        # monthly/exit blocks per side from each scenario's own trades
        assert set(res["monthly_pnl"]) == {"left", "right"}
        # non-equivalence warnings when ranges/coverage/fingerprints differ
        assert len(res["warnings"]) >= 1
        # comparison never overwrites either result
        assert json.dumps(dict(db.one("SELECT * FROM deeptest_scenarios WHERE run_id=?", (ids[0],))),
                          sort_keys=True, default=str) == row0_before
        assert json.dumps(dict(db.one("SELECT * FROM deeptest_scenarios WHERE run_id=?", (ids[1],))),
                          sort_keys=True, default=str) == row1_before
        db.close()

    def test_identical_rerun_reuses_scenario(self, tmp_path):
        db = Database(str(tmp_path / "sc.db"))
        from app.deeptest import store as S
        from app.deeptest.scenarios import _reuse_scenario, effective_signature
        S.ensure_schema(db)
        _seed(db, 501)
        sig = effective_signature(node_id=501, genome_hash="g", config_hash="c", symbol="XAUUSD",
                                  timeframe="M15", duration_key="3Y", requested_start=1.0,
                                  requested_end=2.0, settings={}, node_settings={},
                                  dataset_fingerprint="fp", engine_version="v6.8")
        S.insert_scenario(db, {"run_id": "v68reuse", "node_id": 501, "status": "COMPLETED",
                               "signature": sig, "symbol": "XAUUSD", "timeframe": "M15"})
        assert _reuse_scenario(db, 501, sig)["run_id"] == "v68reuse"
        db.close()


# --------------------------------------------------------------------------- #
# D — performance filters: numbers recalculated from the kept trades
# --------------------------------------------------------------------------- #

class TestPerformanceFilters:
    # deterministic fixture: Mon..Fri london + sat off + thu asia — known PnL
    TRAFFIC = [
        # dow: 0=mon … 5=sat ; sessions as stored
        dict(side="buy", entry_ts=1788894900.0, entry_price=1, exit_ts=1788897600.0,
             exit_price=2, volume=0.1, exit_reason="TP", net_pnl=50.0, session="london", dow=0),
        dict(side="buy", entry_ts=1788981300.0, entry_price=1, exit_ts=1788984000.0,
             exit_price=2, volume=0.1, exit_reason="TP", net_pnl=40.0, session="london", dow=1),
        dict(side="sell", entry_ts=1789067700.0, entry_price=2, exit_ts=1789070400.0,
             exit_price=1, volume=0.1, exit_reason="SL", net_pnl=-30.0, session="london", dow=2),
        dict(side="buy", entry_ts=1789154100.0, entry_price=1, exit_ts=1789156800.0,
             exit_price=2, volume=0.1, exit_reason="TP", net_pnl=70.0, session="asia", dow=3),
        dict(side="buy", entry_ts=1789240500.0, entry_price=1, exit_ts=1789243200.0,
             exit_price=2, volume=0.1, exit_reason="TP", net_pnl=20.0, session="london", dow=4),
        dict(side="sell", entry_ts=1789326900.0, entry_price=2, exit_ts=1789329600.0,
             exit_price=1, volume=0.1, exit_reason="SL", net_pnl=-10.0, session="off", dow=5),
    ]

    @pytest.fixture()
    def run(self, tmp_path, monkeypatch):
        from app.deeptest import store as S
        db = Database(str(tmp_path / "filt.db"))
        S.ensure_schema(db)
        _seed(db, 501, status="QUALIFIED")
        S.insert_scenario(db, {"run_id": "v68flt", "node_id": 501, "status": "COMPLETED",
                               "signature": "sig-f", "symbol": "XAUUSD", "timeframe": "M15",
                               "metrics": {"initial_capital": 10000.0, "net_return_pct": 0.014}})
        S.insert_trades(db, "v68flt", self.TRAFFIC)
        import app.api.routes as R
        monkeypatch.setattr(R, "get_db", lambda: db)
        from fastapi.testclient import TestClient
        from app.main import app
        return db, TestClient(app)

    def _post(self, c, body):
        return c.post("/api/deeptest/run/v68flt/filtered", json=body).json()

    def test_all_enabled_reproduces_original(self, run):
        db, c = run
        r = self._post(c, {})
        assert r["trade_counts"] == {"original": 6, "kept": 6, "excluded": 0}
        total = sum(t["net_pnl"] for t in self.TRAFFIC)
        assert r["filtered"]["summary"]["net_profit"] == pytest.approx(total)

    def test_excluding_thursday_removes_exactly_thursday(self, run):
        db, c = run
        r = self._post(c, {"exclude_days": ["thu"]})
        # only the dow=3 trade (thu) goes
        assert r["trade_counts"] == {"original": 6, "kept": 5, "excluded": 1}
        kept_pnl = sum(t["net_pnl"] for t in self.TRAFFIC if t["dow"] != 3)
        assert r["filtered"]["summary"]["net_profit"] == pytest.approx(kept_pnl)
        # monthly + exit blocks reconcile with the kept trades
        monthly_sum = sum(r["filtered"]["monthly_pnl"].values())
        assert monthly_sum == pytest.approx(kept_pnl)
        exits = r["filtered"]["exit_reasons"]
        assert exits["TP"]["count"] == 3 and exits["SL"]["count"] == 2
        # and the excluded list names the thursday trade
        assert [e["reason"] for e in r["excluded_trades"]] == ["day:thu"]

    def test_session_exclusion_removes_exactly_that_session(self, run):
        db, c = run
        r = self._post(c, {"exclude_sessions": ["off"]})
        kept_pnl = sum(t["net_pnl"] for t in self.TRAFFIC if t["session"] != "off")
        assert r["trade_counts"]["kept"] == 5
        assert r["filtered"]["summary"]["net_profit"] == pytest.approx(kept_pnl)

    def test_combined_or_semantics(self, run):
        db, c = run
        r = self._post(c, {"exclude_days": ["thu"], "exclude_sessions": ["london"]})
        # excluded = thu's trade OR any london trade (union)
        kept = [t for t in self.TRAFFIC if t["dow"] != 3 and t["session"] != "london"]
        assert r["trade_counts"]["kept"] == len(kept) == 1
        assert r["filtered"]["summary"]["net_profit"] == pytest.approx(
            sum(t["net_pnl"] for t in kept))

    def test_restoring_filter_restores_original(self, run):
        db, c = run
        a = self._post(c, {"exclude_days": ["mon"]})
        assert a["trade_counts"]["kept"] == 5
        b = self._post(c, {})
        assert b["trade_counts"]["kept"] == 6
        assert b["filtered"]["summary"]["net_profit"] == pytest.approx(
            sum(t["net_pnl"] for t in self.TRAFFIC))

    def test_drawdown_recomputed_from_filtered_equity_curve(self, run):
        from app.deeptest.analytics import summarize
        db, c = run
        r = self._post(c, {"exclude_days": ["thu"]})
        kept = [t for t in self.TRAFFIC if t["dow"] != 3]
        expected = summarize(kept, initial_capital=10000.0)["summary"]
        assert r["filtered"]["summary"]["max_drawdown_pct"] == pytest.approx(
            expected["max_drawdown_pct"])
        # recomputed from the filtered chronological equity curve — never a
        # scaled leftover of the original summary
        full = summarize(self.TRAFFIC, initial_capital=10000.0)["summary"]
        assert expected["max_drawdown_pct"] is not None
        assert r["filtered"]["summary"]["max_drawdown_pct"] == pytest.approx(
            expected["max_drawdown_pct"])

    def test_empty_result_state_is_safe(self, run):
        db, c = run
        r = self._post(c, {"exclude_days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]})
        assert r["trade_counts"] == {"original": 6, "kept": 0, "excluded": 6}
        s = r["filtered"]["summary"]
        assert s.get("trades") in (None, 0)
        assert s.get("net_profit") in (None, 0)
        assert s.get("profit_factor") is None or isinstance(s.get("profit_factor"), (int, float))
        assert r["filtered"]["monthly_pnl"] == {}
        assert r["filtered"]["exit_reasons"] == {}

    def test_original_scenario_never_mutated(self, run):
        db, c = run
        before_sc = json.dumps(dict(db.one("SELECT * FROM deeptest_scenarios WHERE run_id='v68flt'")),
                               sort_keys=True, default=str)
        before_tr = json.dumps([dict(r) for r in db.q(
            "SELECT * FROM deeptest_trades WHERE run_id='v68flt' ORDER BY trade_no")],
            sort_keys=True, default=str)
        self._post(c, {"exclude_days": ["thu"], "exclude_sessions": ["off"]})
        assert json.dumps(dict(db.one("SELECT * FROM deeptest_scenarios WHERE run_id='v68flt'")),
                          sort_keys=True, default=str) == before_sc
        assert json.dumps([dict(r) for r in db.q(
            "SELECT * FROM deeptest_trades WHERE run_id='v68flt' ORDER BY trade_no")],
            sort_keys=True, default=str) == before_tr


# --------------------------------------------------------------------------- #
# E — coverage (real range counts) + sorting
# --------------------------------------------------------------------------- #

class TestCoverageAndSorting:
    def test_bars_in_range_is_a_real_count(self, monkeypatch):
        from app.deeptest import coverage as C

        class FakeStore:
            def info(self):
                return {"rows": 5, "first_ts": 1000.0, "last_ts": 5000.0,
                        "profile": {"source": "MT5", "broker": "B", "server": "S"}}
            def all_timestamps(self):
                return [1000.0, 2000.0, 3000.0, 4000.0, 5000.0]

        monkeypatch.setattr(C, "_mstore", lambda s, t: FakeStore())
        rec = C.pair_coverage("XAUUSD", "M15", range_start=2500.0, range_end=4500.0)
        assert rec["bars"] == 5                       # total stored
        assert rec["bars_in_range"] == 2              # real count (3000, 4000)
        rec2 = C.pair_coverage("XAUUSD", "M15")
        assert rec2["bars_in_range"] is None          # unavailable ≠ zero

    def test_population_full_history_sort_matches_authority(self, client):
        nd = client.get("/api/node-data").json()["node_data"]
        top = client.get("/api/population?limit=3&sort=return_pct&sort_desc=true"
                         "&include_legacy=true").json()
        assert top["scope"].startswith("complete stored research history")
        assert [r["id"] for r in top["strategies"]] == \
            [t["canonical_node_id"] for t in nd["tops"]["return_pct"][:3]]
        # missing-last + tie-break: the bottom view holds real values too
        asc = client.get("/api/population?limit=3&sort=return_pct&sort_desc=false"
                         "&include_legacy=true").json()["strategies"]
        assert all(r.get("return_pct") is not None for r in asc)

    def test_nodes_index_generation_sorts_are_distinct_keys(self, client):
        a = client.get("/api/nodes?filter=all&limit=3&sort_by=generation_trades&sort_desc=true").json()["nodes"]
        vals = [(r["generation_metrics"] or {}).get("trades") for r in a]
        nums = [v for v in vals if v is not None]
        assert nums == sorted(nums, reverse=True)


# --------------------------------------------------------------------------- #
# B/F — theme contract over the DeepTesting surfaces
# --------------------------------------------------------------------------- #

class TestThemeAndBuild:
    def test_deeptesting_surfaces_use_the_design_system(self):
        for rel in ("frontend/src/pages/DeepBacktest.jsx",
                    "frontend/src/components/DeepScenarioPanel.jsx"):
            text = (REPO / rel).read_text(encoding="utf-8")
            assert 'className="tbl' in text or "className=\"tbl" in text, rel
            assert 'className="btn' in text or 'className="btn"' in text, rel

    def test_theme_layer_maps_every_used_class(self):
        import subprocess
        out = subprocess.run(
            [sys.executable, str(REPO / "frontend" / "tools" / "gen_theme_layer.py"), "--check"],
            capture_output=True, text=True)
        assert out.returncode == 0, out.stdout + out.stderr
        assert "unmapped: 0" in (out.stdout + out.stderr)
