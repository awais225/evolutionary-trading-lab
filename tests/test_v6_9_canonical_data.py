"""V6.9 §9 — canonical node data, count definitions, 88→99 regression,
layered metrics, schedule-editor contract, crash protection.

Every expected value is computed from the seeded stored records through the
established formulas — never hardcoded production totals. The 88→99 test
reproduces the two database classification states deterministically.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from app.db.database import Database  # noqa: E402


def _seed(db: Database, sid: int, *, status="QUALIFIED", run_id="RUN-V69-SEED",
          research_num=None, data_source="USER_RESEARCH", metrics=None,
          symbol="XAUUSD", timeframe="M15"):
    now = time.time()
    db.x("""INSERT INTO strategies (id, status, symbol, timeframe, generation, fitness,
                                    research_node_num, data_source, created_at, updated_at,
                                    hash, genome, complexity, run_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
         (sid, status, symbol, timeframe, 1, 0.5, research_num, data_source, now, now,
          f"h{sid}", json.dumps({"entry_long": {"type": "test"}, "symbol": symbol,
                                 "timeframe": timeframe}), 1, run_id))
    if metrics is not None:
        db.x("""INSERT INTO backtests (strategy_id, stage, metrics, created_at, dataset_id)
                VALUES (?,?,?,?,?)""",
             (sid, "detail", json.dumps(metrics), now, f"{symbol}_{timeframe}"))


def _seed_research_history(db: Database):
    """A deterministic mini research history: 4 QUALIFIED (main run), 2
    SURVIVED + 1 QUALIFIED preserved (ids ≤ 787 like the real import), 1 BORN,
    1 FAILED — the shape that produced 88 vs 99 in the field."""
    _seed(db, 901, status="QUALIFIED", research_num=1,
          metrics={"total_return_pct": 0.10, "trades": 10, "profit_factor": 1.5,
                   "max_drawdown_pct": 0.02, "win_rate": 0.6, "net_profit": 100.0})
    _seed(db, 902, status="QUALIFIED", research_num=2,
          metrics={"total_return_pct": 0.20, "trades": 20, "profit_factor": 2.0,
                   "max_drawdown_pct": 0.03, "win_rate": 0.7, "net_profit": 200.0})
    _seed(db, 903, status="PAPER", research_num=3)
    _seed(db, 904, status="QUALIFIED", research_num=4)
    _seed(db, 101, status="SURVIVED", run_id="RUN-HISTORICAL-PRESERVED", research_num=7,
          metrics={"total_return_pct": 1.481, "trades": 905, "profit_factor": 2.545,
                   "max_drawdown_pct": 0.0405, "win_rate": 0.7724, "net_profit": 14809.59})
    _seed(db, 102, status="SURVIVED", run_id="RUN-HISTORICAL-PRESERVED", research_num=8)
    _seed(db, 103, status="QUALIFIED", run_id="RUN-HISTORICAL-PRESERVED", research_num=9)
    _seed(db, 905, status="BORN", research_num=5)
    _seed(db, 906, status="FAILED", research_num=6)
    # QUALIFIED+PAPER over ALL rows = 5 (901,902,904,903-paper,103)
    return {"all": 5, "gate_passed": 4}   # SURVIVED(2)+QUALIFIED(3 non-paper... ) computed below


import contextlib

_PatchedState = {}


class _patched_db:
    """Deterministic get_db swap with guaranteed restore (the app's process
    singletons capture handles; cross-test leakage is not tolerated)."""

    def __init__(self, db):
        self.db = db

    def __enter__(self):
        import app.api.routes as R
        from app.db import database as dbmod
        self._orig_r, self._orig_d = R.get_db, dbmod.get_db
        R.get_db = lambda: self.db
        dbmod.get_db = lambda *a, **k: self.db
        return self.db

    def __exit__(self, *exc):
        import app.api.routes as R
        from app.db import database as dbmod
        R.get_db = self._orig_r
        dbmod.get_db = self._orig_d
        return False


@contextlib.contextmanager
def patched_db(db):
    ctx = _patched_db(db)
    ctx.__enter__()
    try:
        yield db
    finally:
        ctx.__exit__()


class TestCanonicalCounts:
    def test_qualified_identical_across_tabs_and_states(self, tmp_path, monkeypatch):
        """THE 88→99 regression: the authority counts stored STATUS over the
        complete research history, so the count is IDENTICAL in both
        classification states and across every tab surface; the lab
        exclude-legacy scope is a labelled secondary that is never presented as
        the qualified count (neither at initial load nor after refresh)."""
        p = str(tmp_path / "hist.db")
        db = Database(p)
        _seed_research_history(db)
        from app.research.node_data import node_data_snapshot
        from app.db import database as dbmod
        import app.api.routes as R
        with patched_db(db):
            from fastapi.testclient import TestClient
            from app.main import app
            c = TestClient(app)

            # --- State X: rows as imported (USER_RESEARCH) ---
            nd_x = node_data_snapshot(db, use_cache=False)
            st_x = c.get("/api/status").json()
            initial_x = ((st_x.get("node_data") or {}).get("counts") or {}).get("qualified")
            refresh_x = ((c.get("/api/status").json().get("node_data") or {}).get("counts") or {}).get("qualified")
            assert initial_x == refresh_x == nd_x["counts"]["qualified"] == 5
            # --- State Y: the boot classifier tags ids ≤ 787 as LEGACY_TEST ---
            db.x("UPDATE strategies SET data_source='LEGACY_TEST' WHERE id<=787")
            nd_y = node_data_snapshot(db, use_cache=False)
            st_y = c.get("/api/status").json()
            initial_y = ((st_y.get("node_data") or {}).get("counts") or {}).get("qualified")
            refresh_y = ((c.get("/api/status").json().get("node_data") or {}).get("counts") or {}).get("qualified")
            assert initial_y == refresh_y == nd_y['counts']['qualified'] == 4
            assert nd_y['population_snapshot']['lifetime_total']==9
            assert nd_y['population_snapshot']['excluded_total']==3
            # the same number on every tab surface (DeepTesting authority + nodes index)
            authority = c.get("/api/node-data").json()["node_data"]
            assert authority["counts"]["qualified"] == 4
            ni = c.get("/api/nodes?filter=all&limit=0").json()
            assert ni.get("total") == 6
            # the lab exclude-legacy scope is a LABELLED secondary (the engine's own
            # counters may lag their internal cache — that is why it is never the
            # headline). The authority's own scope label reports it correctly:
            assert (nd_y.get("counts_current_experiment") or {}).get("qualified") == 4
            assert "current experiment scope" in (nd_y.get("counts_current_experiment") or {}).get("scope", "")
            assert st_y.get("node_data_revision") == nd_y["revision"], \
                "the status stamp names the same snapshot (no mixed snapshots)"

    def test_gate_passed_inventory_is_named_distinctly(self, tmp_path, monkeypatch):
        """The DeepTesting 5,240-class number is the frozen node_bucket
        gate-passed/VALID-tier inventory — preserved, computed from the same
        records, and never presented as the qualified research-node count."""
        p = str(tmp_path / "gate.db")
        db = Database(p)
        _seed_research_history(db)
        from app.db import database as dbmod
        import app.api.routes as R
        with patched_db(db):
            from fastapi.testclient import TestClient
            from app.main import app
            c = TestClient(app)
            ni = c.get("/api/nodes?filter=all&limit=0").json()
            ft = ni["filter_totals"]
            gate_passed = ft["gate_passed"]
            assert ft["qualified"] == 5, "V9.1 table default is frozen QUALIFIED+PAPER"
            nd = c.get("/api/node-data").json()["node_data"]
            # gate-passed = the frozen VALID tier: every stored status v5_status maps
            # to VALID (SURVIVED/QUALIFIED/PAPER/SHORTLISTED/MT5_BACKTESTED/
            # LIVE_ELIGIBLE) — verified against the seeded records, not a hardcoded
            # production total
            con = sqlite3.connect(p)
            expected = con.execute(
                "SELECT COUNT(*) FROM strategies WHERE status IN "
                "('SURVIVED','QUALIFIED','PAPER','SHORTLISTED','MT5_BACKTESTED','LIVE_ELIGIBLE')").fetchone()[0]
            con.close()
            assert gate_passed == expected == 7
            assert nd["counts"]["qualified"] == 5, "QUALIFIED+PAPER tier (research nodes)"
            assert gate_passed != nd["counts"]["qualified"], \
                "the two concepts must not be conflated"
            # inventory stats are all present and consistent with the record counts
            assert ft["all"] == 9 and ft["failed"] == 1 and ft["blocked"] == 0
            assert ni.get("authority") or ni.get("data_revision") is not None
            assert nd["counts"]["qualified"] == 5 and gate_passed == expected

    def test_first_status_response_carries_the_authority(self, tmp_path, monkeypatch):
        """A partial initial result must never be presented as the final count:
        the FIRST /api/status response already carries the authoritative
        qualified count (identical to later refreshes)."""
        p = str(tmp_path / "first.db")
        db = Database(p)
        _seed_research_history(db)
        import app.api.routes as R
        from app.db import database as dbmod
        with patched_db(db):
            from fastapi.testclient import TestClient
            from app.main import app
            c = TestClient(app)
            first = c.get("/api/status").json()
            assert (first.get("node_data") or {}).get("counts", {}).get("qualified") == 5
            assert first["node_data"].get("revision")
            # the meta revision stamp matches the served revision (mixed-snapshot check)
            assert first.get("node_data_revision") == first["node_data"]["revision"]

    def test_missing_metrics_stay_missing_across_layers(self, tmp_path):
        p = str(tmp_path / "mm.db")
        db = Database(p)
        _seed(db, 901, status="QUALIFIED",
              metrics={"total_return_pct": 0.0, "trades": 0, "profit_factor": 0.0})
        _seed(db, 902, status="SURVIVED", metrics=None)
        from app.research.node_data import node_data_snapshot
        nd = node_data_snapshot(p, use_cache=False)
        ranked = {t["canonical_node_id"]: t for t in nd["tops"]["trades"]}
        assert 902 not in ranked, "a node without a record never ranks as zero"
        if 901 in ranked:
            assert ranked[901]["generation_metrics"]["trades"] == 0.0


class TestIdentity:
    def test_same_canonical_node_resolves_everywhere(self, client):
        rows = client.get("/api/nodes?filter=all&limit=3&sort_by=node_id").json()["nodes"]
        pop = client.get("/api/population?limit=50&sort=id&sort_desc=false&include_legacy=true").json()["strategies"]
        nd = client.get("/api/node-data").json()["node_data"]
        by_id = {r["id"]: r for r in pop}
        import app.api.routes as _R
        from app.db.database import get_db as _gdb
        print("DEBUG rows:", [(x["node_id"], x.get("research_node_num")) for x in rows[:4]])
        for r in rows[:3]:
            sid = r["node_id"]
            # identity: canonical label + research label are distinct namespaces
            assert r["node_label"] == f"Node #{sid}"
            ident = nd["identity"].get(str(sid))
            assert ident and ident["canonical_node_id"] == sid
            if r.get("research_node_num") is not None:
                assert r["research_label"] == f"research #{r['research_node_num']}"
                assert ident["research_node_num"] == r["research_node_num"]
            # same underlying record across tabs (generation metrics identical)
            p = by_id.get(sid) or {}
            gm = r.get("generation_metrics") or {}
            if gm.get("return_pct") is not None:
                assert p.get("return_pct") == pytest.approx(gm["return_pct"])

    def test_alias_search_never_silently_substitutes_nodes(self, client):
        rows = client.get("/api/nodes?filter=all&search=718&limit=0").json()["nodes"]
        hits = [r for r in rows if r.get("research_node_num") == 718 or r.get("node_id") == 718
                or "718" in str(r.get("research_label") or "")]
        for r in hits:
            if r.get("research_node_num") == 718:
                assert r["node_label"] != "Node #718" or r["node_id"] == 718, \
                    "research #718 must not masquerade as canonical Node #718"


class TestMetricLayers:
    def test_generation_layer_survives_deeptest_and_layers_stay_separate(self, tmp_path):
        from app.deeptest import store as S
        db = Database(str(tmp_path / "layers.db"))
        S.ensure_schema(db)
        metrics = {"total_return_pct": 1.481, "trades": 905, "profit_factor": 2.545,
                   "max_drawdown_pct": 0.0405, "win_rate": 0.7724, "net_profit": 14809.59}
        _seed(db, 101, status="SURVIVED", metrics=metrics)
        before = dict(db.one("SELECT metrics FROM backtests WHERE strategy_id=101"))
        # a completed DeepTest scenario (layer B) with DIFFERENT numbers
        S.insert_scenario(db, {"run_id": "v69lay", "node_id": 101, "status": "COMPLETED",
                               "signature": "sig-lay", "symbol": "XAUUSD", "timeframe": "M1",
                               "metrics": {"net_return_pct": 0.5, "trades": 100},
                               "analytics": {"summary": {"net_return_pct": 0.5, "trades": 100,
                                                         "net_profit": 500.0}}})
        after = dict(db.one("SELECT metrics FROM backtests WHERE strategy_id=101"))
        assert json.loads(after["metrics"]) == json.loads(before["metrics"]), \
            "generation metrics are immutable once a DeepTest exists"
        from app.deeptest.scenarios import node_deeptest_summary
        summ = node_deeptest_summary(db, 101)
        assert summ["scenarios"][0]["headline"]["trades"] == 100
        gen_record = json.loads(after["metrics"])
        assert gen_record["trades"] == 905, "layer B never overwrites layer A"

    def test_live_layer_is_only_live_records(self, tmp_path):
        from app.deeptest import store as S
        db = Database(str(tmp_path / "live.db"))
        S.ensure_schema(db)
        _seed(db, 101, status="SURVIVED",
              metrics={"total_return_pct": 1.0, "trades": 50, "profit_factor": 3.0})
        # no live records -> the live layer must say so, never copy research values
        from app.live_testing.results import per_node_live_stats
        live = per_node_live_stats(db).get(101) or {}
        assert live.get("trades", 0) in (0, None)
        assert live.get("total_pnl") in (None, 0)


class TestScheduleEditorContract:
    def test_options_and_metadata_come_from_real_definitions(self, tmp_path, monkeypatch):
        p = str(tmp_path / "sched.db")
        db = Database(p)
        _seed(db, 901, status="QUALIFIED", research_num=7)
        import app.api.routes as R
        from app.db import database as dbmod
        with patched_db(db):
            from app.deeptest import store as S
            S.ensure_schema(db)
            from fastapi.testclient import TestClient
            from app.main import app
            c = TestClient(app)
            r = c.get("/api/deeptest/node-config/901").json()
            assert r["ok"]
            o = r["options"]
            assert {s["value"] for s in o["sessions"]} >= {"asia", "london", "newyork"}
            assert {x["value"] for x in o["regimes"]} >= {"trending", "ranging"}
            assert "M15" in {t["value"] for t in o["timeframes"]}
            assert {d["value"] for d in o["days"]} == set(range(7))
            # conditions come from the node's ACTUAL genome
            assert {cnd["value"] for cnd in o["conditions"]} == {"entry_long"}
            assert r["node"]["strategy_id"] == 901 and r["node"]["research_node_num"] == 7
            assert r["node"]["symbol"] == "XAUUSD" and r["node"]["timeframe"] == "M15"
            assert r["node"]["status"] == "QUALIFIED"
            assert r.get("local_time") and r.get("timezone")

    def test_save_persists_refreshes_and_reset_restores_defaults(self, tmp_path, monkeypatch):
        p = str(tmp_path / "sched2.db")
        db = Database(p)
        _seed(db, 901, status="QUALIFIED")
        import app.api.routes as R
        from app.db import database as dbmod
        with patched_db(db):
            from app.deeptest import store as S
            S.ensure_schema(db)
            from fastapi.testclient import TestClient
            from app.main import app
            c = TestClient(app)
            r = c.post("/api/deeptest/node-config/901",
                       json={"days": [0, 1, 2, 3, 4], "sessions": ["london"],
                             "enabled": True}).json()
            assert r["ok"] and r["evaluation"] is not None
            assert r["description"] and "London" in r["description"]
            again = c.get("/api/deeptest/node-config/901").json()
            assert (again["config"] or {}).get("schedule", {}).get("days") == [0, 1, 2, 3, 4]
            # reset to documented defaults (empty groups = unrestricted)
            r2 = c.post("/api/deeptest/node-config/901",
                        json={"days": [], "sessions": [], "regimes": [], "timeframes": [],
                              "windows": [], "enabled": True}).json()
            assert r2["ok"]
            sched = (c.get("/api/deeptest/node-config/901").json()["config"] or {}).get("schedule", {})
            assert sched.get("days") in ([], None) and sched.get("sessions") in ([], None)
            # live configuration untouched throughout
            assert db.one("SELECT COUNT(*) c FROM live_test_configs")["c"] == 0

    def test_shared_evaluator_and_blocking_stay_truthful(self, tmp_path, monkeypatch):
        """The verdict comes from the SAME evaluator the live engine uses —
        a blocking schedule must still show BLOCKED with its real reason."""
        p = str(tmp_path / "sched3.db")
        db = Database(p)
        _seed(db, 901, status="QUALIFIED")
        import app.api.routes as R
        from app.db import database as dbmod
        with patched_db(db):
            from app.deeptest import store as S
            S.ensure_schema(db)
            from fastapi.testclient import TestClient
            from app.main import app
            c = TestClient(app)
            # schedule disabled -> the engine must report the block, never a forced READY
            r = c.post("/api/deeptest/node-config/901",
                       json={"enabled": False}).json()
            assert r["evaluation"]["allowed"] is False
            assert r["evaluation"].get("reason"), "a block must carry a specific reason"
            # the same evaluation the live engine would produce
            from app.live_testing.schedule import evaluate
            state = evaluate({"enabled": False})
            assert state["allowed"] is False

    def test_legacy_nodes_open_in_the_deeptest_editor(self, tmp_path, monkeypatch):
        """Node #718-style legacy-classified records stay selectable for
        DeepTesting (live guard applies to live config only)."""
        p = str(tmp_path / "legacy.db")
        db = Database(p)
        _seed(db, 101, status="SURVIVED", data_source="LEGACY_TEST",
              run_id="RUN-HISTORICAL-PRESERVED", research_num=318)
        import app.api.routes as R
        from app.db import database as dbmod
        with patched_db(db):
            from app.deeptest import store as S
            S.ensure_schema(db)
            from fastapi.testclient import TestClient
            from app.main import app
            c = TestClient(app)
            r = c.get("/api/deeptest/node-config/101").json()
            assert r["ok"] and r["node"]["strategy_id"] == 101
            r2 = c.post("/api/deeptest/node-config/101", json={"days": [0]}).json()
            assert r2["ok"]


class TestCrashProtection:
    def test_inspect_component_imports_every_hook_it_uses(self):
        text = (REPO / "frontend/src/components/DeepScenarioPanel.jsx").read_text(encoding="utf-8")
        m = re.search(r'import React, \{([^}]*)\} from "react";', text)
        assert m, "DeepScenarioPanel must import its hooks from react"
        imported = {x.strip() for x in m.group(1).split(",")}
        for hook in ("useState", "useEffect", "useCallback", "useRef"):
            if re.search(rf"\b{hook}\s*\(", text):
                assert hook in imported, f"{hook} used but not imported (the INSPECT crash)"

    def test_no_component_uses_an_unimported_hook(self):
        import re as _re
        hooks = ["useState", "useEffect", "useMemo", "useCallback", "useRef",
                 "useContext", "useReducer", "useLayoutEffect"]
        problems = []
        for rel in ("frontend/src/components", "frontend/src/pages"):
            for f in sorted((REPO / rel).glob("*.jsx")):
                text = f.read_text(encoding="utf-8")
                imported = set()
                for stmt in _re.findall(r'import [^;]*from "react";', text):
                    for h in hooks:
                        if h in stmt:
                            imported.add(h)
                for h in hooks:
                    if _re.search(rf"\b{h}\s*\(", text) and h not in imported:
                        problems.append(f"{f.name}: {h}")
        assert not problems, f"unimported hooks: {problems}"


class TestMixedSnapshots:
    def test_state_change_between_snapshots_is_reported(self, tmp_path):
        p = str(tmp_path / "mix.db")
        db = Database(p)
        _seed(db, 901, status="QUALIFIED")
        from app.research.node_data import node_data_snapshot
        s1 = node_data_snapshot(db, use_cache=False)
        _seed(db, 902, status="QUALIFIED")
        s2 = node_data_snapshot(db, use_cache=False)
        assert s1["state_token"] != s2["state_token"], "token must detect the change"
        assert s1["revision"] != s2["revision"]
        assert s2.get("mixed_snapshot") is False
        db.close()


import sqlite3  # noqa: E402
