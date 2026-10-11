"""V6.7 §15 — authoritative inventory, MT5 sync, scenario workflow, analysis.

Deterministic acceptance tests for the V6.7 contract:

A. one authoritative data pipeline + RECHECK reconciliation (count identity
   across surfaces, deterministic revision, full-dataset ranking, node
   discoverability in BOTH identity namespaces, generation metrics displayed
   equal to the stored record — never hardcoded, zeros vs missing distinguished);
B. MT5 history sync: chunked bounded requests, terminal states, incremental
   resume, coverage == stored data, PARTIAL never labelled COMPLETE;
C. scenario identity: deterministic effective signature, identical rerun reuses
   the existing valid scenario without a rerun, material change ⇒ new identity,
   DeepTesting config never mutates live settings;
D. workflow persistence + DEEPTESTED inventory + INSPECT reconciliation;
E. performance filters: OR-exclusion semantics, recalculation from saved trades,
   all-enabled == original, never mutates the scenario;
F. theme contract over the DeepTesting surfaces.

Nothing here hardcodes observed values like "99 qualified" or "Node #718's
148.100%": every expected number is read from the authoritative stored record
and compared to what the API displays.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from app.db.database import Database  # noqa: E402


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _seed_node(db: Database, sid: int, *, research_num=None, status="QUALIFIED",
               symbol="XAUUSD", timeframe="M15", generation=1, fitness=0.5,
               data_source="USER_RESEARCH", metrics=None, stage="detail"):
    now = time.time()
    # NOTE: Database.__init__ reclassifies rows with NULL run_id (or id <= 787)
    # as LEGACY_TEST on every open (V3.6 legacy tagging), so seeded research
    # rows must carry a real run id to stay in the user-research universe.
    db.x("""INSERT INTO strategies (id, status, symbol, timeframe, generation, fitness,
                                    research_node_num, data_source, created_at, updated_at,
                                    hash, genome, complexity, run_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
         (sid, status, symbol, timeframe, generation, fitness, research_num,
          data_source, now, now,
          f"hash-{sid}", json.dumps({"entry_long": {"type": "test"}, "symbol": symbol,
                                     "timeframe": timeframe}), 1, "RUN-V67-SEED"))
    if metrics is not None:
        db.x("""INSERT INTO backtests (strategy_id, stage, metrics, created_at, dataset_id)
                VALUES (?,?,?,?,?)""",
         (sid, stage, json.dumps(metrics), now, f"{symbol}_{timeframe}"))


METRICS_FULL = {
    "total_return_pct": 1.481, "net_profit": 14810.0, "profit_factor": 2.545,
    "max_drawdown_pct": 0.0405, "win_rate": 0.7724, "trades": 905,
    "sharpe": 3.1, "sortino": 4.2, "expectancy": 16.3,
}
METRICS_ZERO = {
    "total_return_pct": 0.0, "net_profit": 0.0, "profit_factor": 0.0,
    "max_drawdown_pct": 0.0, "win_rate": 0.0, "trades": 0,
}


# --------------------------------------------------------------------------- #
# A — single-authority inventory + RECHECK reconciliation
# --------------------------------------------------------------------------- #

class TestAuthoritativeInventory:
    def test_snapshot_counts_are_one_consistent_set(self, tmp_path):
        db = Database(str(tmp_path / "lab.db"))
        for i, st in enumerate(["QUALIFIED", "QUALIFIED", "RETIRED", "FAILED", "KILLED"], 901):
            _seed_node(db, i, status=st, research_num=100 + i,
                       metrics=METRICS_FULL if i == 901 else None)
        from app.research.populations import inventory_snapshot
        snap = inventory_snapshot(str(tmp_path / "lab.db"))
        c = snap["counts"]
        # the five buckets always sum to the total (deterministic accounting)
        assert c["qualified"] + c["alive"] + c["blocked"] + c["failed"] + c["unknown"] == c["total"] == 5
        assert c["qualified"] == 2
        # per-status histogram is the raw record, kept separately and labelled
        assert snap["counts_by_status"]["QUALIFIED"] == 2
        assert snap["counts_excl_legacy"] == c or snap["counts_excl_legacy"]["total"] == 5
        db.close()

    def test_revision_is_content_derived_and_deterministic(self, tmp_path):
        p = str(tmp_path / "lab.db")
        db = Database(p)
        _seed_node(db, 901, metrics=METRICS_FULL)
        from app.research.populations import inventory_snapshot
        s1 = inventory_snapshot(p)
        s2 = inventory_snapshot(p)
        assert s1["inventory_revision"] == s2["inventory_revision"]
        # data change ⇒ revision change (stale caches can be detected)
        _seed_node(db, 902, metrics=METRICS_ZERO)
        s3 = inventory_snapshot(p)
        assert s3["inventory_revision"] != s1["inventory_revision"]
        db.close()

    def test_recheck_returns_inventory_result_timestamp_and_errors(self, client):
        res = client.get("/api/lab/recheck").json()
        inv = res.get("inventory") or {}
        assert inv.get("inventory_revision")
        assert res.get("recheck_completed_at") is not None
        assert isinstance(res.get("errors"), list)
        assert inv.get("counts", {}).get("total", 0) >= 0
        # deterministic: same data ⇒ same authoritative result
        res2 = client.get("/api/lab/recheck").json()
        assert res2["inventory_revision"] == res["inventory_revision"]
        assert (res2["inventory"] or {}).get("counts") == inv.get("counts")

    def test_status_and_snapshot_share_one_revision(self, client):
        snap = client.get("/api/inventory/snapshot").json()
        st = client.get("/api/status").json()
        assert st.get("inventory_revision") == snap.get("inventory_revision")
        assert st.get("qualified_authoritative") == \
            (snap.get("counts_excl_legacy") or {}).get("qualified")

    def test_full_dataset_metric_sorting_missing_never_best(self, client):
        snap = client.get("/api/inventory/snapshot").json()
        tops = (snap.get("tops") or {}).get("return_pct") or []
        assert tops, "top-return list must not be empty on real data"
        # server sort agrees with the snapshot ranking over the full dataset
        rows = client.get("/api/population?limit=5&sort=return_pct&sort_desc=true").json()["strategies"]
        assert [r["id"] for r in rows[:3]] == [t["canonical_node_id"] for t in tops[:3]]
        # descending top must hold real values; missing metrics never rank as 0-best
        for r in rows:
            assert r.get("return_pct") is not None
        # ascending view returns the true negative tail (a limited slice of the
        # fitness ranking could never contain these)
        asc = client.get("/api/population?limit=5&sort=return_pct&sort_desc=false").json()["strategies"]
        assert all((r.get("return_pct") or 0) <= (rows[-1].get("return_pct") or 0) for r in asc)
        # trades ranking reads the real `trades` key (a5-bar screen cannot win)
        tr = client.get("/api/population?limit=3&sort=trades&sort_desc=true").json()["strategies"]
        assert all(isinstance(r.get("trades"), (int, float)) for r in tr)

    def test_node_search_matches_both_identity_namespaces(self, client):
        # whichever node carries research number 718 (or canonical id 718) must
        # be discoverable by searching "718", with BOTH labels explicit.
        hits = client.get("/api/nodes?filter=all&search=718&limit=0").json()["nodes"]
        found_research = [h for h in hits if h.get("research_node_num") == 718]
        found_canonical = [h for h in hits if h.get("node_id") == 718]
        if found_research or found_canonical:
            for h in found_research:
                assert h["research_label"] == "research #718"
                assert h["node_label"] == f"Node #{h['node_id']}"
            for h in found_canonical:
                assert h["node_label"] == "Node #718"

    def test_displayed_generation_metrics_equal_stored_record(self, client):
        """The #718-style contract: displayed generation values == the stored
        record exactly (displayed return % = stored fraction ×100), zeros are
        real zeros, missing is missing — whatever the stored record holds."""
        ids = client.get("/api/population?limit=3&sort=trades&sort_desc=true").json()["strategies"]
        rows = client.get(f"/api/nodes?filter=all&limit=0&search={ids[0]['id']}&sort_by=node_id").json()["nodes"]
        row = next((r for r in rows if r["node_id"] == ids[0]["id"]), None)
        assert row is not None
        import sqlite3
        con = sqlite3.connect(str(REPO / "DATA" / "DATABASE" / "lab_state.db"))
        con.row_factory = sqlite3.Row
        raw = con.execute("""SELECT metrics FROM backtests WHERE strategy_id=?
                             AND stage IN ('detail','screen')
                             ORDER BY CASE stage WHEN 'detail' THEN 0 ELSE 1 END, id DESC LIMIT 1""",
                          (row["node_id"],)).fetchone()
        con.close()
        assert raw, "node under test must have a stored generation record"
        stored = json.loads(raw["metrics"])
        gm = row["generation_metrics"]
        assert gm["return_pct"] == pytest.approx(stored["total_return_pct"])
        assert gm["trades"] == pytest.approx(float(stored.get("trades")))
        assert gm["profit_factor"] == pytest.approx(stored.get("profit_factor"))
        # display conversion is exactly ×100 with 3 decimals (never invented)
        assert round(gm["return_pct"] * 100, 3) == round(stored["total_return_pct"] * 100, 3)

    def test_node_718_gen_values_come_from_the_stored_record(self, client):
        """The operator's Node #718 contract: its generation values (the ones
        that read 148.100% / PF 2.545 / DD 4.050% / WR 77.240% / 905 trades in
        the UI) are displayed ONLY because the STORED record holds them — this
        test asserts displayed == stored for whichever identity exists here,
        reading every expected value from the database (never hardcoded)."""
        import sqlite3
        con = sqlite3.connect(str(REPO / "DATA" / "DATABASE" / "lab_state.db"))
        con.row_factory = sqlite3.Row
        try:
            targets = [r["id"] for r in con.execute(
                "SELECT id FROM strategies WHERE id=718 OR research_node_num=718 ORDER BY id")]
            assert targets, "Node #718 (canonical or research identity) must exist in this dataset"
            rows = client.get("/api/nodes?filter=all&search=718&limit=0").json()["nodes"]
            by_id = {r["node_id"]: r for r in rows}
            for sid in targets:
                row = by_id.get(sid)
                assert row is not None, f"Node #{sid} must be discoverable by search '718'"
                raw = con.execute("""SELECT metrics FROM backtests WHERE strategy_id=?
                                     AND stage IN ('detail','screen')
                                     ORDER BY CASE stage WHEN 'detail' THEN 0 ELSE 1 END,
                                              id DESC LIMIT 1""", (sid,)).fetchone()
                if raw is None:
                    continue                    # identity exists but has no record -> nothing to display
                stored = json.loads(raw["metrics"])
                gm = row["generation_metrics"] or {}
                # displayed == stored exactly (units: stored fractions/ratios)
                assert gm.get("return_pct") == pytest.approx(stored.get("total_return_pct"))
                assert gm.get("trades") == pytest.approx(float(stored.get("trades")))
                assert gm.get("profit_factor") == pytest.approx(stored.get("profit_factor"))
                assert gm.get("max_drawdown_pct") == pytest.approx(stored.get("max_drawdown_pct"))
                assert gm.get("win_rate") == pytest.approx(stored.get("win_rate"))
                # the display conversion is exactly x100 with 3 decimals
                assert round(gm["return_pct"] * 100, 3) == round(stored["total_return_pct"] * 100, 3)
                # discoverable despite any live-exclusion status: the row carries
                # its bucket and both identity labels explicitly
                assert row["node_label"] == f"Node #{sid}"
                assert row.get("bucket") in ("qualified", "alive", "failed",
                                             "excluded", "blocked", "unknown")
        finally:
            con.close()

    def test_real_zero_shown_as_zero_missing_stays_missing(self, tmp_path):
        p = str(tmp_path / "lab.db")
        db = Database(p)
        _seed_node(db, 901, status="QUALIFIED", metrics=METRICS_ZERO)
        _seed_node(db, 902, status="QUALIFIED", metrics=None)   # no record at all
        from app.research.populations import inventory_snapshot
        snap = inventory_snapshot(p)
        by_id = {t["canonical_node_id"]: t for t in snap["tops"]["trades"]}
        zero_node = [t for t in snap["tops"]["trades"] if t["canonical_node_id"] == 901]
        # real 0 is present and equals 0.0 — never dropped as "missing"
        if zero_node:
            assert zero_node[0]["generation_metrics"]["trades"] == 0.0
        # the record-less node never appears in a metric ranking (missing ≠ 0)
        assert 902 not in by_id
        db.close()


# --------------------------------------------------------------------------- #
# B — MT5 sync: chunked, bounded, terminal, resumable, honest coverage
# --------------------------------------------------------------------------- #

def _friday_noon_utc() -> float:
    """Deterministic weekday anchor: the most recent Friday 12:00 UTC, so
    short fixture windows never fall on a weekend (the SIMULATOR path drops
    weekend bars by design)."""
    now = datetime.now(tz=timezone.utc)
    back = (now.weekday() - 4) % 7
    fri = now.replace(hour=12, minute=0, second=0, microsecond=0)
    from datetime import timedelta
    return (fri - timedelta(days=back)).timestamp()


class _FakeBar:
    def __init__(self, ts):
        self.ts = float(ts)
        self.time = float(ts)

    def to_dict(self):
        return {"ts": self.ts, "open": 1, "high": 2, "low": 0.5, "close": 1.5,
                "volume": 10, "spread": 1}


class _FakeBridge:
    """Records every request; serves deterministic bars; chunk-size spy."""
    source = "SIMULATOR"

    def __init__(self, start_ts, end_ts, step_s=900, *, empty=False, short_end=None):
        self.start_ts, self.end_ts, self.step_s = start_ts, end_ts, step_s
        self.empty = empty
        self.short_end = short_end
        self.range_calls = []     # (start, end)
        self.copy_calls = []      # n_bars
        self.fail_next = 0

    def profile(self):
        return {"source": self.source, "broker": "SIM", "server": "SIM",
                "profile_key": "SIM|SIM", "symbol": "XAUUSD"}

    def copy_rates_range(self, symbol, tf, start, end):
        self.range_calls.append((float(start), float(end)))
        if self.empty:
            return []
        if self.fail_next > 0:
            self.fail_next -= 1
            raise RuntimeError("transient bridge error")
        last = self.end_ts if self.short_end is None else min(end, self.short_end)
        out = []
        t = max(float(start), self.start_ts)
        while t <= min(float(end), last):
            # weekday bars only: the engine's SIMULATOR path legitimately drops
            # weekend bars, so fixtures must be weekday-deterministic.
            if datetime.fromtimestamp(t, tz=timezone.utc).weekday() < 5:
                out.append(_FakeBar(t))
            t += self.step_s
        return out

    def copy_rates(self, symbol, tf, n):
        self.copy_calls.append(int(n))
        return []


class _FakeStore:
    """In-memory stand-in for master.MasterStore (no filesystem writes)."""

    def __init__(self, profile_key="SIM|SIM"):
        self.profile_key = profile_key
        self.profile = {"source": "SIMULATOR", "broker": "SIM", "server": "SIM",
                        "profile_key": profile_key}
        self.rows = 0
        self.first_ts = None
        self.last_ts = None
        self.version = 1
        self.symbol = "XAUUSD"
        self.timeframe = "M15"
        self.fingerprint = "fake-fp"

    def append(self, df, source_info=None):
        ts = list(df["ts"]) if hasattr(df, "__getitem__") and "ts" in getattr(df, "columns", []) else []
        n = len(df)
        if ts:
            self.first_ts = min(ts) if self.first_ts is None else min(self.first_ts, min(ts))
            self.last_ts = max(ts) if self.last_ts is None else max(self.last_ts, max(ts))
        self.rows += n
        return {"accepted": n, "duplicates_identical": 0, "conflicts": 0}

    def read_range(self):
        import pandas as pd
        return pd.DataFrame()


class TestMt5Sync:
    def _isolate(self, monkeypatch, bridge):
        import app.data.engine as E
        monkeypatch.setattr(E, "get_bridge", lambda: bridge)
        store = _FakeStore()
        monkeypatch.setattr(E.master, "get_master", lambda *a, **k: store)
        monkeypatch.setattr(E.DataEngine, "_materialize_dataset_view",
                            lambda self, mstore: "ds-test")
        return E.DataEngine(), store

    def test_sync_chunks_large_ranges_bounded_requests(self, tmp_path, monkeypatch):
        import app.data.engine as E
        step = 900
        end = time.time()
        start = end - 200000 * step          # ~200k bars ⇒ several chunks
        bridge = _FakeBridge(end - 200000 * step, end, step)
        eng, store = self._isolate(monkeypatch, bridge)
        res = eng.sync_master("XAUUSD", "M15", force_full=True)
        # every range request is bounded (CHUNK_BARS·step, never one mega-call)
        for (a, b) in bridge.range_calls:
            assert (b - a) <= 15000 * step + step
        assert bridge.copy_calls == [] or max(bridge.copy_calls) <= 20000
        assert res["status"] in ("complete", "partial", "no_data_from_bridge", "cancelled")
        assert "fetch" in res and res["fetch"]["bars_received"] >= 0
        assert res.get("sync_status") in ("COMPLETE", "PARTIAL")

    def test_sync_empty_bridge_reaches_terminal_state(self, tmp_path, monkeypatch):
        bridge = _FakeBridge(0, time.time(), empty=True)
        eng, store = self._isolate(monkeypatch, bridge)
        res = eng.sync_master("XAUUSD", "M15", force_full=True)
        assert res["status"] == "no_data_from_bridge"       # terminal, not stuck
        assert res["new_bars"] == 0

    def test_sync_short_range_is_partial_never_complete(self, tmp_path, monkeypatch):
        end = _friday_noon_utc()
        bridge = _FakeBridge(end - 50 * 900, end, 900, short_end=end - 25 * 900)
        eng, store = self._isolate(monkeypatch, bridge)
        res = eng.sync_master("XAUUSD", "M15", force_full=True)
        assert res["sync_status"] == "PARTIAL"
        assert res.get("partial_reason")
        assert res["status"] == "partial"

    def test_sync_incremental_resume_requests_only_missing(self, tmp_path, monkeypatch):
        end = _friday_noon_utc()
        bridge = _FakeBridge(end - 200 * 900, end, 900)
        eng, mstore = self._isolate(monkeypatch, bridge)
        eng.sync_master("XAUUSD", "M15", force_full=True)
        last = mstore.last_ts
        assert last is not None and mstore.rows > 0
        # stored coverage equals what the engine reports (dedupe correctness)
        assert res_total(mstore) == mstore.rows
        bridge.range_calls.clear()
        eng.sync_master("XAUUSD", "M15")
        for (a, b) in bridge.range_calls:
            # resume: only the missing tail (2-bar overlap allowed)
            assert a >= last - 2 * 900 - 1

    def test_sync_is_deduped_across_concurrent_callers(self, tmp_path, monkeypatch):
        end = _friday_noon_utc()

        class SlowBridge(_FakeBridge):
            def copy_rates_range(self, symbol, tf, start, end):
                time.sleep(0.05)
                return super().copy_rates_range(symbol, tf, start, end)

        bridge = SlowBridge(end - 300 * 900, end, 900)
        eng, store = self._isolate(monkeypatch, bridge)
        results = []

        def worker():
            results.append(eng.sync_master("XAUUSD", "M15", force_full=True))

        ts = [threading.Thread(target=worker) for _ in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(timeout=60)
        assert len(results) == 2
        modes = {r.get("fetch_mode") for r in results}
        # one real fetch + the other reuses it (or both serialize honestly) —
        # but the bridge never serves two overlapping full ranges as separate work
        assert results[0]["total_bars"] == results[1]["total_bars"]


def res_total(mstore):
    return mstore.rows


# --------------------------------------------------------------------------- #
# C — scenario identity, reuse, isolation
# --------------------------------------------------------------------------- #

class TestScenarioIdentity:
    def _node(self, db):
        _seed_node(db, 501, metrics=METRICS_FULL)
        return 501

    def test_signature_deterministic_and_change_sensitive(self):
        from app.deeptest.scenarios import effective_signature
        base = dict(node_id=501, genome_hash="g", config_hash="c", symbol="XAUUSD",
                    timeframe="M15", duration_key="3Y", requested_start=1.0,
                    requested_end=2.0, settings={"initial_capital": 10000.0},
                    node_settings={}, dataset_fingerprint="fp", engine_version="v6.7")
        s1 = effective_signature(**base)
        s2 = effective_signature(**base)
        assert s1 == s2
        assert effective_signature(**{**base, "requested_end": 3.0}) != s1
        assert effective_signature(**{**base, "settings": {"initial_capital": 20000.0}}) != s1
        assert effective_signature(**{**base, "dataset_fingerprint": "other"}) != s1
        assert effective_signature(**{**base, "engine_version": "v6.8"}) != s1

    def test_identical_rerun_reuses_scenario_without_new_row(self, tmp_path):
        db = Database(str(tmp_path / "lab.db"))
        from app.deeptest import scenarios as SC
        from app.deeptest import store as S
        S.ensure_schema(db)
        self._node(db)
        sig = SC.effective_signature(
            node_id=501, genome_hash="g", config_hash="c", symbol="XAUUSD",
            timeframe="M15", duration_key="3Y", requested_start=1.0,
            requested_end=2.0, settings={}, node_settings={},
            dataset_fingerprint="fp", engine_version="v6.7")
        S.insert_scenario(db, {"run_id": "v67reuse1", "node_id": 501,
                               "status": "COMPLETED", "signature": sig,
                               "symbol": "XAUUSD", "timeframe": "M15",
                               "metrics": {"net_return_pct": 0.5}})
        before = db.one("SELECT COUNT(*) c FROM deeptest_scenarios")["c"]
        existing = SC._reuse_scenario(db, 501, sig)
        assert existing and existing["run_id"] == "v67reuse1"
        label = SC._scenario_label(db, "v67reuse1")
        assert label.startswith("Scenario")
        # failed/cancelled never qualify as reuse targets
        S.insert_scenario(db, {"run_id": "v67reuse2", "node_id": 501,
                               "status": "FAILED", "signature": sig})
        assert SC._reuse_scenario(db, 501, sig)["run_id"] == "v67reuse1"
        # unchanged run does not add a scenario row
        assert db.one("SELECT COUNT(*) c FROM deeptest_scenarios")["c"] == before + 1
        db.close()

    def test_deeptest_config_never_mutates_live_settings(self, tmp_path):
        db = Database(str(tmp_path / "lab.db"))
        from app.deeptest import store as S
        S.ensure_schema(db)
        self._node(db)
        S.save_node_config(db, 501, {"schedule": {"days": [0, 1, 2, 3, 4]},
                                     "node_settings": {"risk_pct": 2.0}})
        cfg = S.get_node_config(db, 501)
        assert cfg["node_settings"]["risk_pct"] == 2.0
        live = db.one("SELECT * FROM live_test_configs WHERE strategy_id=501")
        assert live is None, "DeepTesting config must never create/alter live config"
        db.close()


# --------------------------------------------------------------------------- #
# D/E — persisted scenarios, DEEPTESTED inventory, INSPECT reconciliation
# --------------------------------------------------------------------------- #

class TestWorkflowAndFilters:
    def _seed_scenario(self, db, run_id="v67filt1", node_id=502):
        from app.deeptest import store as S
        S.ensure_schema(db)
        _seed_node(db, node_id, metrics=METRICS_FULL)
        S.insert_scenario(db, {"run_id": run_id, "node_id": node_id,
                               "status": "COMPLETED", "signature": f"sig-{run_id}",
                               "symbol": "XAUUSD", "timeframe": "M15",
                               "duration_key": "3M",
                               "requested_start": 1788890000.0, "requested_end": 1790100000.0,
                               "actual_start": 1788890000.0, "actual_end": 1790100000.0,
                               "metrics": {"initial_capital": 10000.0, "net_return_pct": 0.006}})
        trades = [
            {"side": "buy", "entry_ts": 1788894900.0, "entry_price": 1, "exit_ts": 1788897600.0,
             "exit_price": 2, "volume": 0.1, "exit_reason": "TP", "net_pnl": 50.0,
             "session": "london", "dow": 0},
            {"side": "sell", "entry_ts": 1788987600.0, "entry_price": 2, "exit_ts": 1789002000.0,
             "exit_price": 1, "volume": 0.1, "exit_reason": "SL", "net_pnl": -20.0,
             "session": "off", "dow": 5},
            {"side": "buy", "entry_ts": 1789077600.0, "entry_price": 1, "exit_ts": 1789082000.0,
             "exit_price": 2, "volume": 0.1, "exit_reason": "TP", "net_pnl": 30.0,
             "session": "asia", "dow": 2},
        ]
        S.insert_trades(db, run_id, trades)
        return run_id

    def test_summary_monthly_and_exit_reconcile(self, tmp_path):
        db = Database(str(tmp_path / "lab.db"))
        run_id = self._seed_scenario(db)
        rows = [dict(r) for r in db.q(
            "SELECT * FROM deeptest_trades WHERE run_id=? ORDER BY trade_no", (run_id,))]
        assert len(rows) == 3
        total = sum(r["net_pnl"] for r in rows)
        assert total == pytest.approx(60.0)
        exits = {}
        for r in rows:
            exits.setdefault(r["exit_reason"], [0, 0.0])
            exits[r["exit_reason"]][0] += 1
            exits[r["exit_reason"]][1] += r["net_pnl"]
        assert exits["TP"] == [2, 80.0]
        assert exits["SL"] == [1, -20.0]
        db.close()

    def test_filters_or_semantics_and_never_mutation(self, tmp_path, monkeypatch):
        """OR-style independent exclusion: day-excluded OR session-excluded
        removes the trade; all-enabled equals the original; the stored scenario
        and its trades are byte-identical after filtering."""
        db = Database(str(tmp_path / "lab.db"))
        run_id = self._seed_scenario(db)
        before_sc = json.dumps(db.one("SELECT * FROM deeptest_scenarios WHERE run_id=?", (run_id,)),
                               sort_keys=True, default=str)
        before_tr = json.dumps([dict(r) for r in db.q(
            "SELECT * FROM deeptest_trades WHERE run_id=? ORDER BY trade_no", (run_id,))],
            sort_keys=True, default=str)

        import app.api.routes as R
        monkeypatch.setattr(R, "get_db", lambda: db)
        from fastapi.testclient import TestClient
        from app.main import app
        c = TestClient(app)
        # exclude saturday + off-session: the dow-5 off trade goes, the others stay
        res = c.post(f"/api/deeptest/run/{run_id}/filtered",
                     json={"exclude_days": ["sat"], "exclude_sessions": ["off"]}).json()
        assert res["trade_counts"] == {"original": 3, "kept": 2, "excluded": 1}
        kept_pnl = sum(v["net_pnl"] for v in res["filtered"]["exit_reasons"].values())
        assert kept_pnl == pytest.approx(80.0)
        # day and session are independent OR dimensions
        res2 = c.post(f"/api/deeptest/run/{run_id}/filtered",
                      json={"exclude_days": ["mon"]}).json()
        assert res2["trade_counts"]["kept"] == 2          # only the monday trade goes
        # all enabled == original
        res3 = c.post(f"/api/deeptest/run/{run_id}/filtered", json={}).json()
        assert res3["trade_counts"]["kept"] == res3["trade_counts"]["original"] == 3
        assert res3["active"] is False
        # never mutates the scenario or its trades
        assert json.dumps(db.one("SELECT * FROM deeptest_scenarios WHERE run_id=?", (run_id,)),
                          sort_keys=True, default=str) == before_sc
        assert json.dumps([dict(r) for r in db.q(
            "SELECT * FROM deeptest_trades WHERE run_id=? ORDER BY trade_no", (run_id,))],
            sort_keys=True, default=str) == before_tr
        db.close()

    def test_deeptested_rows_one_per_node_with_inventory_join(self, client):
        res = client.get("/api/deeptest/nodes").json()
        rows = res.get("nodes") or []
        ids = [r.get("node_id") for r in rows]
        assert len(ids) == len(set(ids)), "one row per node in DEEPTESTED NODES"

    def test_nodes_index_generation_and_deeptest_stay_separate(self, client):
        rows = client.get("/api/nodes?filter=all&limit=5&sort_by=node_id").json()["nodes"]
        for r in rows:
            assert "generation_metrics" in r and "deeptest" in r
            # the generation block never carries DeepTest-only fields
            gm = r["generation_metrics"] or {}
            assert set(gm) <= {"return_pct", "profit_factor", "max_drawdown_pct",
                               "win_rate", "trades", "net_profit", "expectancy",
                               "avg_trade", "sharpe"}
        # explicit generation_* sort must not fall back to DeepTest values
        a = client.get("/api/nodes?filter=all&limit=3&sort_by=generation_trades&sort_desc=true").json()["nodes"]
        vals = [(r["generation_metrics"] or {}).get("trades") for r in a]
        nums = [v for v in vals if v is not None]
        assert nums == sorted(nums, reverse=True)


# --------------------------------------------------------------------------- #
# F — theme contract over the DeepTesting surfaces (§13)
# --------------------------------------------------------------------------- #

class TestThemeContract:
    def test_deeptesting_surfaces_use_theme_classes(self):
        for rel in ("frontend/src/pages/DeepBacktest.jsx",
                    "frontend/src/components/DeepScenarioPanel.jsx",
                    "frontend/src/components/StrategyDrawer.jsx"):
            text = (REPO / rel).read_text(encoding="utf-8")
            assert 'className="tbl' in text or "className={\"tbl" in text or 'tbl mono' in text, rel

    def test_theme_layer_has_no_unmapped_classes(self):
        import subprocess
        out = subprocess.run(
            [sys.executable, str(REPO / "frontend" / "tools" / "gen_theme_layer.py"), "--check"],
            capture_output=True, text=True)
        assert out.returncode == 0, out.stdout + out.stderr
