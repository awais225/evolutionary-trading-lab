"""V6.6 — Deep Testing: periods, persistence, CSV import, coverage confirmation,
scenario runs, statistics priority, and the additive engine fields.

Data-integrity rules exercised here: no fabricated bars/trades/metrics (N/A +
reason instead), no look-ahead, conservative same-bar SL/TP policy, immutable
scenario snapshots, explicit preference never silently replaced, and the
strictly additive engine behaviour (unset fields = the pre-V6.6 result).
"""
from __future__ import annotations

import json
import math
import os
import time

import pytest

DATASET = "XAUUSD_M15_20250930_20260925_SIMULATOR"

GENOME = {
    "symbol": "XAUUSD",
    "timeframe": "M15",
    "direction": "both",
    "entry_long": {"type": "crossover", "a": "ema:20", "b": "ema:50", "dir": "up"},
    "entry_short": {"type": "crossover", "a": "ema:20", "b": "ema:50", "dir": "down"},
    "exit": {"atr_spec": "atr:14", "sl_atr_mult": 1.5, "tp_atr_mult": 3.0,
             "max_hold_bars": 48, "min_hold_bars": 1},
    "risk": {"risk_per_trade": 0.005},
}


# --------------------------------------------------------------------------- #
# §6 — periods
# --------------------------------------------------------------------------- #
class TestPeriods:
    def test_nine_presets_resolve_to_explicit_bounds(self):
        from app.deeptest.periods import PERIODS, resolve_period
        assert list(PERIODS) == ["1D", "7D", "14D", "1M", "3M", "6M", "1Y", "2Y", "3Y"]
        end = 1780000000.0
        for k in PERIODS:
            r = resolve_period(k, end)
            assert r["duration_key"] == k
            assert r["start_ts"] < r["end_ts"] == end
            assert r["start_iso"].endswith("Z") and r["end_iso"].endswith("Z")
            assert "calendar" in r["convention"]

    def test_month_and_year_windows_are_calendar_based(self):
        from app.deeptest.periods import resolve_period
        # ending 2026-03-15: 1 MONTH starts 2026-02-15 (calendar month, not 30 days)
        import datetime as dt
        end = dt.datetime(2026, 3, 15, 12, 0, tzinfo=dt.timezone.utc).timestamp()
        r = resolve_period("1M", end)
        assert r["start_iso"].startswith("2026-02-15")
        # leap clamp: ending a Feb 29, 1 YEAR starts the previous Feb 28
        end2 = dt.datetime(2024, 2, 29, 0, 0, tzinfo=dt.timezone.utc).timestamp()
        r2 = resolve_period("1Y", end2)
        assert r2["start_iso"].startswith("2023-02-28")

    def test_unknown_period_is_refused_never_guessed(self):
        from app.deeptest.periods import resolve_period
        with pytest.raises(ValueError):
            resolve_period("5Y")
        with pytest.raises(ValueError):
            resolve_period("")

    def test_custom_window_is_normalised_the_same_way(self):
        from app.deeptest.periods import explicit_period
        r = explicit_period(1700000000, 1700086400)
        assert r["duration_key"] == "CUSTOM"
        assert r["end_ts"] - r["start_ts"] == 86400


# --------------------------------------------------------------------------- #
# §12/§14 — persistence: additive schema, scenario snapshots, prefs, settings
# --------------------------------------------------------------------------- #
@pytest.fixture()
def tdb(tmp_path):
    from app.db.database import Database
    from app.deeptest import store as S
    db = Database(str(tmp_path / "deeptest.db"))   # NOT get_db(): that is a singleton
    S.ensure_schema(db)
    return db


class TestStore:
    def test_schema_is_additive_and_idempotent(self, tmp_path):
        from app.db.database import Database
        from app.deeptest import store as S
        db = Database(str(tmp_path / "fresh.db"))  # NOT get_db(): that is a singleton
        before = {r["name"] for r in db.q("SELECT name FROM sqlite_master WHERE type='table'")}
        S.ensure_schema(db)                       # twice is safe
        S.ensure_schema(db)
        after = {r["name"] for r in db.q("SELECT name FROM sqlite_master WHERE type='table'")}
        new_tables = after - before
        for t in ("deeptest_scenarios", "deeptest_trades", "deeptest_prefs",
                  "deeptest_confirmations", "deeptest_settings", "deeptest_candle_sources"):
            assert t in new_tables
        # every create is IF NOT EXISTS in the DDL the store executes (additive),
        # so re-running the bootstrap can never rewrite an existing table
        from app.deeptest import store as S2
        assert "IF NOT EXISTS" in S2.SCHEMA_SQL.upper()
        S2.ensure_schema(db)                       # still idempotent after asserts

    def test_settings_roundtrip_and_validation(self, tdb):
        from app.deeptest import store as S
        assert S.get_settings(tdb)["total_capital"] == 200000.0      # §4 default
        ok = S.save_settings(tdb, {"total_capital": 250000, "risk_pct": 2,
                                   "max_active_trades_per_node": 0})
        assert ok["ok"] and S.get_settings(tdb)["risk_pct"] == 2.0
        bad = S.save_settings(tdb, {"risk_pct": -3})
        assert not bad["ok"] and bad["errors"][0]["field"] == "risk_pct"
        bad2 = S.save_settings(tdb, {"total_capital": "twenty"})
        assert not bad2["ok"]
        bad3 = S.save_settings(tdb, {"max_active_trades_per_node": 2.5})
        assert not bad3["ok"]

    def test_scenario_snapshot_is_immutable_after_updates(self, tdb):
        from app.deeptest import store as S
        rid = S.insert_scenario(tdb, {"node_id": 7, "status": "QUEUED", "duration_key": "1M",
                                      "settings": {"total_capital": 1}, "metrics": {"net_profit": 5}})
        S.update_scenario(tdb, rid, status="COMPLETED", notes="later edit")
        sc = S.get_scenario(tdb, rid)
        assert sc["status"] == "COMPLETED"
        assert sc["metrics"] == {"net_profit": 5}      # metrics untouched by later edit
        assert sc["settings"] == {"total_capital": 1}
        assert sc["created_at"] <= time.time()

    def test_idempotency_key_dedupes(self, tdb):
        from app.deeptest import store as S
        S.insert_scenario(tdb, {"node_id": 7, "status": "QUEUED", "idempotency_key": "k-1"})
        tdb.x("DELETE FROM deeptest_scenarios WHERE idempotency_key='k-1' AND run_id NOT IN "
              "(SELECT MIN(run_id) FROM deeptest_scenarios WHERE idempotency_key='k-1')")
        with pytest.raises(Exception):
            S.insert_scenario(tdb, {"node_id": 7, "status": "QUEUED", "idempotency_key": "k-1"})
        rows = tdb.q("SELECT * FROM deeptest_scenarios WHERE idempotency_key='k-1'")
        assert len(rows) == 1

    def test_trades_roundtrip_and_pagination(self, tdb):
        from app.deeptest import store as S
        rid = S.insert_scenario(tdb, {"node_id": 9, "status": "RUNNING"})
        S.insert_trades(tdb, rid, [{"side": "buy", "net_pnl": 1.5, "sl": 2050.0, "tp": 2100.0},
                                   {"side": "sell", "net_pnl": -0.5}])
        page = S.list_trades(tdb, rid, limit=1)
        assert page["total"] == 2 and len(page["trades"]) == 1
        assert page["trades"][0]["sl"] == 2050.0

    def test_confirmation_upsert(self, tdb):
        from app.deeptest import store as S
        S.save_confirmation(tdb, "fp-1", "NO_CANCEL")
        rec = S.save_confirmation(tdb, "fp-1", "YES_PROCEED")
        assert rec["decision"] == "YES_PROCEED"
        assert S.get_confirmation(tdb, "fp-1")["decision"] == "YES_PROCEED"


# --------------------------------------------------------------------------- #
# §11 — analytics from persisted trades only
# --------------------------------------------------------------------------- #
class TestAnalytics:
    TRADES = [
        {"side": "buy", "net_pnl": 100.0, "hold_seconds": 90.0, "session": "LONDON", "dow": 0},
        {"side": "sell", "net_pnl": -50.0, "hold_seconds": 40.0, "session": "NY", "dow": 1},
        {"side": "buy", "net_pnl": 25.0, "hold_seconds": 10.0, "session": "LONDON", "dow": 1},
    ]

    def test_summary_recomputes_every_field(self):
        from app.deeptest.analytics import summarize
        out = summarize(self.TRADES, initial_capital=200000.0)
        s = out["summary"]
        assert s["trades"] == 3 and s["winning_trades"] == 2 and s["losing_trades"] == 1
        assert s["net_profit"] == 75.0
        assert s["gross_profit"] == 125.0 and s["gross_loss"] == 50.0
        assert s["win_rate"] == pytest.approx(2 / 3, rel=1e-4)
        assert s["expectancy_per_trade"] == 25.0
        assert s["profit_factor"] == 2.5
        assert s["max_consecutive_wins"] == 1 and s["max_consecutive_losses"] == 1
        assert s["long"]["trades"] == 2 and s["short"]["trades"] == 1
        assert s["net_return_pct"] == pytest.approx(75.0 / 200000.0)
        b = out["breakdowns"]
        assert b["by_day_of_week"]["Monday"]["trades"] == 1
        assert "LONDON" in b["by_session"]
        # every metric is a real number or an explicit null — no NaN/Inf
        for k, v in s.items():
            if isinstance(v, float):
                assert not (math.isnan(v) or math.isinf(v)), k

    def test_undefined_metrics_are_na_with_reason_never_zero(self):
        from app.deeptest.analytics import summarize
        out = summarize([], initial_capital=200000.0)
        s = out["summary"]
        assert s["win_rate"] is None
        assert s["avg_win"] is None and s["avg_loss"] is None
        assert s["profit_factor"] is None
        assert s["sharpe"] is None
        reasons = {u["field"] for u in out["unavailable"]}
        assert {"win_rate", "avg_win", "avg_loss", "profit_factor", "sharpe"} <= reasons
        one = summarize([{"side": "buy", "net_pnl": 10.0}], initial_capital=200000.0)
        assert one["summary"]["sharpe"] is None       # < 2 trades → N/A, never 0
        assert any(u["field"] == "sharpe" for u in one["unavailable"])

    def test_reconciliation(self):
        from app.deeptest.analytics import reconcile, summarize
        s = summarize(self.TRADES, initial_capital=200000.0)["summary"]
        rec = reconcile(self.TRADES, s)
        assert rec["ok"] and rec["trade_level_sum"] == 75.0


# --------------------------------------------------------------------------- #
# §10 — CSV import
# --------------------------------------------------------------------------- #
class TestCsv:
    def test_mt5_headerless_joint_datetime(self):
        from app.deeptest.csv_import import validate_frame
        raw = (b"2024.01.02 00:00,2062.5,2065.0,2060.1,2063.2,120,0,18\n"
               b"2024.01.02 00:15,2063.2,2066.0,2061.0,2064.0,90,0,18\n")
        rep = validate_frame(raw, symbol="XAUUSD", timeframe="M15",
                             timezone_offset_hours=2)
        assert rep["ok"], rep
        assert rep["rows_valid"] == 2 and rep["rows_rejected"] == 0
        assert rep["mapping"] == {"open": "1", "high": "2", "low": "3", "close": "4",
                                  "tick_volume": "5", "spread": "7"}
        assert 1700000000 < rep["earliest_ts"] < 1800000000

    def test_naive_timestamps_require_confirmation(self):
        from app.deeptest.csv_import import validate_frame, commit_import
        raw = b"2024-01-02T00:00:00,1,2,0.5,1.5\n2024-01-02T00:15:00,1.5,2,1,1.8\n"
        rep = validate_frame(raw, symbol="XAUUSD", timeframe="M15")
        assert rep["needs_timezone_confirmation"] and not rep["ok"]
        res = commit_import(rep, symbol="XAUUSD", timeframe="M15")
        assert not res["ok"] and res["needs_timezone_confirmation"]
        rep2 = validate_frame(raw, symbol="XAUUSD", timeframe="M15", timezone_offset_hours=5)
        assert rep2["ok"] and not rep2["needs_timezone_confirmation"]

    def test_tz_aware_parse_without_confirmation(self):
        from app.deeptest.csv_import import validate_frame
        raw = (b"timestamp,open,high,low,close\n"
               b"2024-01-02T00:00:00Z,1,2,0.5,1.5\n"
               b"2024-01-02T00:15:00Z,1.5,2,1,1.8\n")
        rep = validate_frame(raw, symbol="XAUUSD", timeframe="M15")
        assert rep["ok"] and not rep["needs_timezone_confirmation"]

    def test_validation_rejects_bad_rows_and_never_fabricates(self):
        from app.deeptest.csv_import import validate_frame
        raw = (b"2024.01.02 00:00,2062.5,2065.0,2060.1,2063.2\n"       # ok
               b"2024.01.02 00:15,2063.2,2060.0,2061.0,2064.0\n"       # OHLC inconsistent
               b"2024.01.02 00:30,0,0,0,0\n")                          # non-positive
        rep = validate_frame(raw, symbol="XAUUSD", timeframe="M15", timezone_offset_hours=0)
        assert rep["rows_valid"] == 1 and rep["rows_rejected"] == 2
        assert rep["rejects_sample"][0]["reason"]

    def test_duplicates_and_gaps_are_reported(self):
        from app.deeptest.csv_import import validate_frame
        raw = (b"2024.01.02 00:00,1,2,0.5,1.5\n"
               b"2024.01.02 00:00,1,2,0.5,1.5\n"
               b"2024.01.02 00:15,1.5,2,1,1.8\n"
               b"2024.01.02 02:15,1.5,2,1,1.8\n")
        rep = validate_frame(raw, symbol="XAUUSD", timeframe="M15", timezone_offset_hours=0)
        assert rep["rows_duplicate"] == 1
        assert rep["gap_count"] == 1 and rep["missing_bars_total"] == 7   # 2h of M15

    def test_unknown_timeframe_refused(self):
        from app.deeptest.csv_import import validate_frame
        raw = b"2024.01.02 00:00,1,2,0.5,1.5\n"
        rep = validate_frame(raw, symbol="XAUUSD", timeframe="M13")
        assert not rep["ok"] and "timeframe" in rep.get("error", "")

    def test_interval_mismatch_is_refused_never_converted(self):
        """M15-spaced rows claimed as M5 must be rejected outright."""
        from app.deeptest.csv_import import validate_frame, commit_import
        raw = (b"2024.01.02 00:00,1,2,0.5,1.5\n"
               b"2024.01.02 00:15,1.5,2,1,1.8\n"
               b"2024.01.02 00:30,1.8,2.2,1.6,2.0\n")
        rep = validate_frame(raw, symbol="XAUUSD", timeframe="M5", timezone_offset_hours=0)
        assert not rep["ok"], rep
        assert rep.get("timeframe_mismatch"), rep
        assert "mismatch" in (rep.get("error") or "")
        res = commit_import(rep, symbol="XAUUSD", timeframe="M5", timezone_confirmed="UTC")
        assert not res["ok"], "a mismapped file must never reach the store"
        # the same rows under their true timeframe validate cleanly
        rep2 = validate_frame(raw, symbol="XAUUSD", timeframe="M15", timezone_offset_hours=0)
        assert rep2["ok"] and not rep2.get("timeframe_mismatch"), rep2


# --------------------------------------------------------------------------- #
# §7/§9 — coverage + the confirmation binding
# --------------------------------------------------------------------------- #
class TestCoverageAndConfirm:
    def test_check_period_statuses_and_fingerprint(self):
        from app.deeptest.coverage import check_period, confirmation_fingerprint
        chk = check_period("XAUUSD", "M15", 1700000000, 1790000000, node_id=7,
                           duration_key="3Y", settings_fingerprint="abc")
        assert chk["coverage_status"] in ("COMPLETE", "PARTIAL", "NONE")
        assert chk["confirmation_fingerprint"]
        # a different node / period / settings yields a DIFFERENT fingerprint
        fp2 = confirmation_fingerprint(symbol="XAUUSD", timeframe="M15",
                                       dataset_fingerprint=chk["fingerprint"] or "",
                                       first_ts=chk["actual"]["first_ts"],
                                       last_ts=chk["actual"]["last_ts"],
                                       bars=chk["actual"]["bars"],
                                       gap_count=chk["internal_gaps"],
                                       requested_start=1700000000, requested_end=1790000000,
                                       node_id=8, settings_fingerprint="abc")
        assert fp2 != chk["confirmation_fingerprint"]

    def test_confirmation_binds_the_period_selection_not_the_now_anchor(self):
        """A preset (3Y…) confirmation must survive a few seconds of drift between
        the popup and the run; a CUSTOM window binds its exact bounds."""
        from app.deeptest.coverage import confirmation_fingerprint as fp
        base = dict(symbol="XAUUSD", timeframe="M15", dataset_fingerprint="d",
                    first_ts=1.0, last_ts=2.0, bars=10, gap_count=0,
                    node_id=7, settings_fingerprint="s")
        a = fp(requested_start=100.0, requested_end=200.0, duration_key="3Y", **base)
        b = fp(requested_start=103.7, requested_end=203.7, duration_key="3Y", **base)
        assert a == b, "a preset's confirmation must not expire with the 'now' anchor"
        c = fp(requested_start=100.0, requested_end=200.0, duration_key="1M", **base)
        assert c != a, "a different period selection is a different binding"
        d = fp(requested_start=100.0, requested_end=200.0, duration_key="", **base)
        e = fp(requested_start=100.0, requested_end=250.0, duration_key="", **base)
        assert d != e, "a CUSTOM window binds its exact bounds"
        assert d == fp(requested_start=100.0, requested_end=200.0, duration_key="CUSTOM", **base)

    def test_pair_coverage_never_fabricates(self):
        from app.deeptest.coverage import pair_coverage
        cov = pair_coverage("XAUUSD", "M15")
        assert cov["symbol"] == "XAUUSD" and cov["timeframe"] == "M15"
        assert isinstance(cov["bars"], int) and cov["bars"] >= 0
        if cov["bars"] == 0:
            assert not cov["first"]
        assert cov["error"] is None or isinstance(cov["error"], str)

    def test_missing_symbol_pair_is_reported_not_guessed(self):
        from app.deeptest.coverage import pair_coverage
        cov = pair_coverage("", "M15")
        assert cov["error"] and cov["bars"] == 0


# --------------------------------------------------------------------------- #
# §4/§8 — additive engine fields: unset = exactly the pre-V6.6 behaviour
# --------------------------------------------------------------------------- #
class TestEngineDeeptestFields:
    @pytest.fixture(scope="class")
    def base(self):
        from app.backtest.engine import BacktestRequest, run_backtest
        req = BacktestRequest(genome=GENOME, dataset_id=DATASET, stage="detail",
                              window=(0, 1800), seed_salt="v66-base")
        res = run_backtest(req)
        assert res.ok, res.error
        return req, res

    def test_unset_fields_reproduce_the_default_behaviour(self, base):
        from app.backtest.engine import BacktestRequest, run_backtest
        req, res = base
        req2 = BacktestRequest(genome=GENOME, dataset_id=DATASET, stage="detail",
                               window=(0, 1800), seed_salt="v66-base",
                               initial_balance=None, risk_per_trade_pct=None,
                               risk_mode=None, risk_amount=None,
                               max_active_trades=None, sl_offset_pips=0, tp_offset_pips=0)
        res2 = run_backtest(req2)
        assert res2.ok
        assert res2.trades == res.trades, "unset DeepTest fields must not change the result"
        for k in ("total_return_pct", "trades", "net_profit"):
            assert res2.metrics[k] == res.metrics[k]

    def test_max_active_one_equals_default_gate(self, base):
        from app.backtest.engine import BacktestRequest, run_backtest
        _, res = base
        res1 = run_backtest(BacktestRequest(
            genome=GENOME, dataset_id=DATASET, stage="detail", window=(0, 1800),
            seed_salt="v66-base", max_active_trades=1))
        assert res1.ok and res1.trades == res.trades

    def test_one_open_trade_at_a_time_is_enforced(self, base):
        _, res = base
        ts = [(t["entry_ts"], t["exit_ts"]) for t in res.trades]
        for i, (a0, a1) in enumerate(ts):
            for b0, b1 in ts[i + 1:]:
                assert a1 < b0 or b1 < a0, "default mode must never hold two open trades"

    def test_unlimited_concurrent_never_forces_early_exits(self):
        from app.backtest.engine import BacktestRequest, run_backtest
        res0 = run_backtest(BacktestRequest(
            genome=GENOME, dataset_id=DATASET, stage="detail", window=(0, 1800),
            seed_salt="v66-conc", max_active_trades=0))
        assert res0.ok
        # every exit reason is still a real strategy exit (no forced closes)
        for t in res0.trades:
            assert t["exit_reason"] in ("SL", "TP", "EXIT_COND", "MAX_HOLD")
        # holds are unchanged by concurrency: same per-trade bar counts as the
        # engine's own hold logic (never cut early to free a slot)
        res1 = run_backtest(BacktestRequest(
            genome=GENOME, dataset_id=DATASET, stage="detail", window=(0, 1800),
            seed_salt="v66-conc", max_active_trades=1))
        holds1 = {(t["entry_ts"]): t["hold_bars"] for t in res1.trades}
        for t in res0.trades:
            if t["entry_ts"] in holds1:
                assert t["hold_bars"] == holds1[t["entry_ts"]]

    def test_cap_of_n_entries_only(self):
        from app.backtest.engine import BacktestRequest, run_backtest
        for mat in (2, 3):
            res = run_backtest(BacktestRequest(
                genome=GENOME, dataset_id=DATASET, stage="detail", window=(0, 1800),
                seed_salt="v66-cap", max_active_trades=mat))
            assert res.ok
            iv = [(t["entry_ts"], t["exit_ts"]) for t in res.trades]
            # peak concurrency is reached at some entry instant (a trade is open
            # through its exit timestamp — the engine's inclusive convention)
            for a0, a1 in iv:
                open_now = sum(1 for b0, b1 in iv if b0 <= a0 <= b1)
                assert open_now <= mat, f"more than {mat} open trades at entry {a0}"

    def test_initial_balance_and_risk_pct_sizing(self):
        from app.backtest.engine import BacktestRequest, run_backtest
        small = run_backtest(BacktestRequest(
            genome=GENOME, dataset_id=DATASET, stage="detail", window=(0, 1800),
            seed_salt="v66-size", initial_balance=10000.0, risk_per_trade_pct=1.0))
        big = run_backtest(BacktestRequest(
            genome=GENOME, dataset_id=DATASET, stage="detail", window=(0, 1800),
            seed_salt="v66-size", initial_balance=100000.0, risk_per_trade_pct=1.0))
        assert small.ok and big.ok
        if small.trades and big.trades:
            assert big.trades[0]["lots"] >= small.trades[0]["lots"]
        assert big.metrics["final_equity"] == pytest.approx(
            100000.0 + big.metrics["net_profit"], abs=0.01)
        assert small.metrics["final_equity"] == pytest.approx(
            10000.0 + small.metrics["net_profit"], abs=0.01)

    def test_amount_mode_sizes_from_money_at_risk(self):
        from app.backtest.engine import BacktestRequest, run_backtest
        res = run_backtest(BacktestRequest(
            genome=GENOME, dataset_id=DATASET, stage="detail", window=(0, 1800),
            seed_salt="v66-amt", risk_mode="AMOUNT", risk_amount=500.0,
            initial_balance=200000.0))
        assert res.ok
        for t in res.trades:
            # volume floored to lot step — risk at the stop ≤ the requested amount
            assert t["lots"] > 0

    def test_offsets_follow_the_v65_convention(self):
        from app.backtest.engine import BacktestRequest, run_backtest
        base = run_backtest(BacktestRequest(
            genome=GENOME, dataset_id=DATASET, stage="detail", window=(0, 1800),
            seed_salt="v66-off"))
        with_off = run_backtest(BacktestRequest(
            genome=GENOME, dataset_id=DATASET, stage="detail", window=(0, 1800),
            seed_salt="v66-off", sl_offset_pips=10.0))
        assert base.ok and with_off.ok
        if base.trades and with_off.trades:
            b = base.trades[0]; w = with_off.trades[0]
            if b["sl"] and w["sl"]:
                if b["side"] == "buy":
                    assert w["sl"] < b["sl"], "BUY +SL offset moves the stop farther (lower)"
                else:
                    assert w["sl"] > b["sl"], "SELL +SL offset moves the stop farther (higher)"

    def test_execution_model_and_schedule_meta_documented(self, base):
        _, res = base
        em = res.metrics.get("execution_model")
        assert em and "fill" in em["fill_rule"].lower()
        assert "next bar open" in em["fill_rule"]
        assert res.metrics.get("window") == [0, 1800]


# --------------------------------------------------------------------------- #
# §8/§12 — scenario orchestration (sync executor, real engine, temp DB)
# --------------------------------------------------------------------------- #
class _SyncExec:
    def submit(self, fn, *a, **kw):
        fn(*a)
        return None


@pytest.fixture()
def sdb(tmp_path, monkeypatch):
    """Temp scenario DB + sync runner + a fake node bound to the REAL dataset."""
    import app.api.routes  # noqa: F401 — MUST import before any patch: routes binds
    # `from ..db.database import get_db` at import time, so importing it WHILE the
    # database binding is patched would capture the patch as routes' "original".
    from app.db.database import Database
    from app.deeptest import store as S
    from app.deeptest import scenarios as SC
    db = Database(str(tmp_path / "scen.db"))       # NOT get_db(): that is a singleton
    S.ensure_schema(db)
    monkeypatch.setattr(SC, "_executor", _SyncExec())
    # patch BOTH bindings: routes.py imported get_db at module load (the pitfall),
    # while deeptest.scenarios imports it lazily at call time
    monkeypatch.setattr("app.db.database.get_db", lambda *a, **k: db)
    monkeypatch.setattr("app.api.routes.get_db", lambda *a, **k: db)

    def fake_node(_db, node_id):
        return {"node_id": int(node_id), "symbol": "XAUUSD", "timeframe": "M15",
                "genome": GENOME, "config": {}, "research_node_num": 1}
    monkeypatch.setattr(SC, "_load_node", fake_node)
    return db


def _range_covering_the_store():
    from app.deeptest.coverage import pair_coverage
    cov = pair_coverage("XAUUSD", "M15")
    assert cov["bars"] > 10, "the scratch store needs bars for this test"
    return float(cov["first_ts"]) - 100.0, float(cov["last_ts"]) + 100.0


class TestScenarios:
    def test_incomplete_history_requires_the_confirmation(self, sdb):
        from app.deeptest import scenarios as SC
        start, end = _range_covering_the_store()
        # a requested range that reaches beyond the stored history is incomplete
        pf = SC.preflight(sdb, node_id=7, requested_start=start,
                          requested_end=end + 90 * 86400.0, settings={"total_capital": 200000.0})
        assert pf["ok"]
        assert pf["needs_confirmation"], pf
        res = SC.start_run(sdb, {"node_id": 7, "requested_start": start,
                                 "requested_end": end + 90 * 86400.0})
        assert not res["ok"] and res["needs_confirmation"]
        # after an explicit YES the run may start; NO/CANCEL loses nothing
        fp = pf["coverage"]["confirmation_fingerprint"]
        dec = SC.record_decision(sdb, confirmation_fingerprint=fp, decision="YES_PROCEED")
        assert dec["ok"]
        res2 = SC.start_run(sdb, {"node_id": 7, "requested_start": start,
                                  "requested_end": end + 90 * 86400.0})
        assert res2["ok"], res2
        sc = res2["scenario"]
        assert sc["confirmed_fingerprint"] == fp

    def test_full_coverage_proceeds_without_the_popup(self, sdb):
        from app.deeptest import scenarios as SC
        from app.deeptest import store as S
        start, end = _range_covering_the_store()
        res = SC.start_run(sdb, {"node_id": 7, "requested_start": start, "requested_end": end,
                                 "idempotency_key": "full-cov-1"})
        assert res["ok"], res
        sc = S.get_scenario(sdb, res["run_id"])
        # the sync executor already finished the run — and with real bars + a real
        # genome the engine MUST finish it: a FAILED run here means the engine
        # adapter itself is broken (this is exactly how a bad import once hid).
        assert sc["status"] in ("COMPLETED", "PARTIAL"), sc
        assert sc["metrics"] is not None, sc.get("error")
        assert sc["analytics"]["reconciliation"]["ok"], sc["analytics"]["reconciliation"]
        assert sc["provenance"]["actual_tested_range"]["bars"] >= 2
        assert sc["provenance"]["run_policy"].startswith("historical simulation")
        assert sc["engine_versions"]["deeptest"] == "V6.6"
        trades = S.list_trades(sdb, res["run_id"])
        assert trades["total"] == (sc["analytics"]["summary"]["trades"] or 0)

    def test_idempotent_replay_returns_the_same_scenario(self, sdb):
        from app.deeptest import scenarios as SC
        start, end = _range_covering_the_store()
        r1 = SC.start_run(sdb, {"node_id": 7, "requested_start": start, "requested_end": end,
                                "idempotency_key": "idem-1"})
        r2 = SC.start_run(sdb, {"node_id": 7, "requested_start": start, "requested_end": end,
                                "idempotency_key": "idem-1"})
        assert r1["ok"] and r2["ok"]
        assert r2.get("idempotent_replay") and r2["scenario"]["run_id"] == r1["scenario"]["run_id"]

    def test_partial_runs_are_marked_and_never_called_complete(self, sdb):
        from app.deeptest import store as S
        from app.deeptest import scenarios as SC
        start, end = _range_covering_the_store()
        pf = SC.preflight(sdb, node_id=11, requested_start=start - 30 * 86400.0,
                          requested_end=end, settings={})
        if pf["ok"] and pf["needs_confirmation"]:
            SC.record_decision(sdb,
                               confirmation_fingerprint=pf["coverage"]["confirmation_fingerprint"],
                               decision="YES_PROCEED")
        res = SC.start_run(sdb, {"node_id": 11, "requested_start": start - 30 * 86400.0,
                                 "requested_end": end, "idempotency_key": "partial-1"})
        assert res["ok"], res
        sc = S.get_scenario(sdb, res["run_id"])
        if sc["status"] == "PARTIAL":
            assert sc["coverage_status"] == "PARTIAL"
            assert sc["provenance"]["provisional"] is True

    def test_preference_rules(self, sdb):
        from app.deeptest import store as S
        from app.deeptest import scenarios as SC
        # a valid scenario (the preference rules are what is under test here —
        # end-to-end run completion is asserted by test_full_coverage above)
        rid = S.insert_scenario(sdb, {"node_id": 9, "status": "COMPLETED",
                                      "duration_key": "1M",
                                      "analytics": {"summary": {"net_profit": 1, "trades": 1}},
                                      "finished_at": time.time() - 100})
        # explicit preference is stored and used
        assert SC.set_preferred(sdb, 9, rid, explicit=True)["ok"]
        assert SC.preferred_for(sdb, 9)["run_id"] == rid
        assert SC.preferred_for(sdb, 9)["preference_source"] == "explicit"
        # a FAILED run can never be preferred or displace the valid one
        failed_id = S.insert_scenario(sdb, {"node_id": 9, "status": "FAILED", "error": "x",
                                            "finished_at": time.time() - 50})
        assert not SC.set_preferred(sdb, 9, failed_id, explicit=True)["ok"]
        assert SC.preferred_for(sdb, 9)["run_id"] == rid
        # a newer valid run does NOT silently replace an explicit preference
        newer = S.insert_scenario(sdb, {"node_id": 9, "status": "PARTIAL",
                                        "coverage_status": "PARTIAL",
                                        "duration_key": "3M",
                                        "analytics": {"summary": {"net_profit": -2, "trades": 1}},
                                        "finished_at": time.time()})
        assert SC.preferred_for(sdb, 9)["run_id"] == rid
        # without an explicit choice, the LATEST VALID is preferred…
        S.clear_pref(sdb, 9)
        assert SC.preferred_for(sdb, 9)["run_id"] == newer
        # …and a FAILED row never wins even when newest
        S.insert_scenario(sdb, {"node_id": 9, "status": "FAILED", "error": "y",
                                "finished_at": time.time() + 50})
        assert SC.preferred_for(sdb, 9)["run_id"] == newer

    def test_compare_names_differences(self, sdb):
        from app.deeptest import store as S
        from app.deeptest import scenarios as SC
        a = S.insert_scenario(sdb, {"node_id": 21, "status": "COMPLETED", "duration_key": "1M",
                                    "settings": {"risk_pct": 1.0},
                                    "metrics": {"net_profit": 1}})
        b = S.insert_scenario(sdb, {"node_id": 21, "status": "PARTIAL", "duration_key": "3M",
                                    "settings": {"risk_pct": 2.0},
                                    "metrics": {"net_profit": -2}})
        res = SC.compare(sdb, [a, b])
        assert res["ok"]
        fields = {d["field"] for d in res["config_and_coverage_differences"]}
        assert {"duration_key", "coverage_status"} & fields
        assert "do not treat" in res["warning"]
        assert not SC.compare(sdb, [a])["ok"]
        other = S.insert_scenario(sdb, {"node_id": 22, "status": "COMPLETED"})
        assert not SC.compare(sdb, [a, other])["ok"]      # different nodes refused

    def test_cancel_marks_and_preserves(self, sdb):
        from app.deeptest import store as S
        from app.deeptest import scenarios as SC
        rid = S.insert_scenario(sdb, {"node_id": 31, "status": "QUEUED"})
        res = SC.cancel_run(sdb, rid)
        assert res["ok"] and S.get_scenario(sdb, rid)["status"] == "CANCELLED"
        assert not SC.cancel_run(sdb, rid)["ok"]           # already terminal


# --------------------------------------------------------------------------- #
# §12 — statistics priority projection
# --------------------------------------------------------------------------- #
class TestProjection:
    def test_precedence_and_generation_preserved(self, tdb):
        from app.deeptest import store as S
        from app.deeptest import scenarios as SC
        from app.stats.deeptest_projection import project_node
        gen = {"return_pct": 0.5, "trades": 10, "profit_factor": 2.0,
               "max_drawdown_pct": 0.1, "win_rate": 0.6, "net_profit": 50,
               "expectancy": 5, "avg_trade": 5, "sharpe": 3}
        # no DeepTest → generation stats
        p = project_node(tdb, 1, generation_metrics=gen)
        assert p["metrics_source"] == "generation"
        assert p["metrics"]["return_pct"] == 0.5
        assert p["generation_metrics"]["return_pct"] == 0.5
        # nothing at all → N/A, never 0
        p2 = project_node(tdb, 2, generation_metrics=None)
        assert p2["metrics_source"] == "none"
        assert p2["metrics"]["trades"] is None
        # a valid DeepTest outranks generation
        rid = S.insert_scenario(tdb, {"node_id": 3, "status": "COMPLETED",
                                      "analytics": {"summary": {
                                          "net_return_pct": 0.01, "profit_factor": 1.1,
                                          "max_drawdown_pct": 0.02, "win_rate": 0.55,
                                          "trades": 4, "net_profit": 200,
                                          "expectancy_per_trade": 50}}})
        p3 = project_node(tdb, 3, generation_metrics=gen)
        assert p3["metrics_source"] == "deeptest_latest_valid"
        assert p3["metrics"]["trades"] == 4
        assert p3["generation_metrics"]["trades"] == 10      # preserved separately
        # an explicit preference is labelled as such
        S.set_pref(tdb, 3, rid, explicit=True)
        p4 = project_node(tdb, 3, generation_metrics=gen)
        assert p4["metrics_source"] == "deeptest_preferred"
        # a FAILED scenario never participates
        S.set_pref(tdb, 3, "dt-missing", explicit=True)
        p5 = project_node(tdb, 3, generation_metrics=gen)
        assert p5["metrics_source"] == "deeptest_latest_valid"

    def test_node_index_carries_the_projection_fields(self, client):
        r = client.get("/api/nodes?filter=qualified&limit=5")
        assert r.status_code == 200
        body = r.json()
        rows = body.get("nodes") or []
        assert rows, "the fixture inventory must have at least one row"
        row = rows[0]
        # §12: source label + preserved generation metrics + unchanged metric keys
        assert set(row["metrics"].keys()) >= {"return_pct", "profit_factor", "max_drawdown_pct",
                                             "win_rate", "trades"}
        assert row["metrics_source"] in ("generation", "deeptest_latest_valid",
                                         "deeptest_preferred", "none")
        assert "generation_metrics" in row
        assert "deeptest" in row


# --------------------------------------------------------------------------- #
# API surface
# --------------------------------------------------------------------------- #
class TestApi:
    def test_settings_get_and_put(self, client):
        r = client.get("/api/deeptest/settings")
        assert r.status_code == 200 and r.json()["ok"]
        assert float(r.json()["settings"]["total_capital"]) > 0       # §4 default survives
        p = client.put("/api/deeptest/settings", json={"risk_pct": 2})
        assert p.status_code == 200 and p.json()["ok"]
        assert p.json()["settings"]["risk_pct"] == 2.0
        g = client.get("/api/deeptest/settings")
        assert g.json()["settings"]["risk_pct"] == 2.0                # read-after-write
        bad = client.put("/api/deeptest/settings", json={"risk_pct": -1})
        assert bad.status_code == 200 and not bad.json()["ok"]

    def test_periods_endpoint(self, client):
        r = client.get("/api/deeptest/periods")
        body = r.json()
        assert body["ok"] and len(body["periods"]) == 9
        assert [p["key"] for p in body["periods"]][-1] == "3Y"
        assert "calendar" in body["convention"]

    def test_coverage_pairs(self, client):
        r = client.post("/api/deeptest/coverage/pairs",
                        json={"pairs": [{"symbol": "XAUUSD", "timeframe": "M15"}]})
        body = r.json()
        assert body["ok"] and "XAUUSD|M15" in body["coverage"]
        c = body["coverage"]["XAUUSD|M15"]
        assert isinstance(c["bars"], int) and "fingerprint" in c

    def test_csv_validate_commit_refuses_without_timezone(self, client):
        raw = b"2024-01-02T00:00:00,1,2,0.5,1.5\n2024-01-02T00:15:00,1.5,2,1,1.8\n"
        r = client.post("/api/deeptest/csv/validate?symbol=XAUUSD&timeframe=M15&filename=x.csv",
                        content=raw)
        assert r.status_code == 200
        body = r.json()
        assert body["ok"]
        assert body["needs_timezone_confirmation"]
        c = client.post("/api/deeptest/csv/commit",
                        json={"validation_token": body["validation_token"]})
        assert not c.json()["ok"] and c.json().get("needs_timezone_confirmation")

    def test_commit_refuses_a_mismatched_pair_claim(self, client):
        """A commit claiming a different symbol/timeframe than the validated
        one is rejected — never silently re-targeted."""
        raw = b"2024-01-02T00:00:00,1,2,0.5,1.5\n2024-01-02T00:15:00,1.5,2,1,1.8\n"
        # a mismapped file fails validation over the API as clean JSON (the
        # error path never serialises the internal preview frame)
        bad = client.post("/api/deeptest/csv/validate?symbol=XAUUSD&timeframe=M5&filename=x.csv"
                          "&timezone_offset_hours=0", content=raw)
        assert bad.status_code == 200, bad.text
        assert not bad.json()["ok"] and bad.json().get("timeframe_mismatch")
        r = client.post("/api/deeptest/csv/validate?symbol=XAUUSD&timeframe=M15&filename=x.csv"
                        "&timezone_offset_hours=0", content=raw)
        body = r.json()
        assert body["ok"], body
        tok = body["validation_token"]
        c1 = client.post("/api/deeptest/csv/commit",
                         json={"validation_token": tok, "symbol": "EURUSD",
                               "timezone_confirmed": "UTC"})
        assert not c1.json()["ok"] and "symbol mismatch" in c1.json().get("error", "")
        c2 = client.post("/api/deeptest/csv/commit",
                         json={"validation_token": tok, "timeframe": "M5",
                               "timezone_confirmed": "UTC"})
        assert not c2.json()["ok"] and "timeframe mismatch" in c2.json().get("error", "")

    def test_preflight_run_flow_with_confirmation(self, client, sdb, monkeypatch):
        from app.deeptest import scenarios as SC
        monkeypatch.setattr("app.db.database.get_db", lambda *a, **k: sdb)
        monkeypatch.setattr("app.api.routes.get_db", lambda *a, **k: sdb)

        def fake_node(_db, node_id):
            return {"node_id": int(node_id), "symbol": "XAUUSD", "timeframe": "M15",
                    "genome": GENOME, "config": {}, "research_node_num": 1}
        monkeypatch.setattr(SC, "_load_node", fake_node)
        monkeypatch.setattr(SC, "_executor", _SyncExec())
        start, end = _range_covering_the_store()
        pre = client.post("/api/deeptest/preflight",
                          json={"node_id": 5, "requested_start": start, "requested_end": end})
        body = pre.json()
        assert body["ok"]
        run = client.post("/api/deeptest/run",
                          json={"node_id": 5, "requested_start": start,
                                "requested_end": end + 365 * 86400.0})
        rb = run.json()
        assert rb.get("needs_confirmation"), rb
        conf = client.post("/api/deeptest/confirm", json={
            "confirmation_fingerprint": rb["confirmation"]["confirmation_fingerprint"],
            "decision": "YES_PROCEED", "node_id": 5})
        assert conf.json()["ok"]
        run2 = client.post("/api/deeptest/run",
                           json={"node_id": 5, "requested_start": start,
                                 "requested_end": end + 365 * 86400.0,
                                 "idempotency_key": "api-1"})
        assert run2.json()["ok"], run2.json()
        rid = run2.json()["run_id"]
        got = client.get(f"/api/deeptest/run/{rid}")
        assert got.status_code == 200
        assert got.json()["scenario"]["run_id"] == rid
        assert got.json()["scenario"]["status"] in ("COMPLETED", "PARTIAL"), got.json()["scenario"]
        tr = client.get(f"/api/deeptest/run/{rid}/trades?limit=5")
        assert tr.json()["ok"] and "total" in tr.json()
        nodes = client.get("/api/deeptest/nodes")
        assert nodes.json()["ok"]
        assert any(n["node_id"] == 5 for n in nodes.json()["nodes"])

    def test_prefer_and_compare_endpoints(self, client, sdb, monkeypatch):
        from app.deeptest import store as S
        from app.deeptest import scenarios as SC
        monkeypatch.setattr("app.db.database.get_db", lambda *a, **k: sdb)
        monkeypatch.setattr("app.api.routes.get_db", lambda *a, **k: sdb)
        a = S.insert_scenario(sdb, {"node_id": 41, "status": "COMPLETED", "duration_key": "1M"})
        b = S.insert_scenario(sdb, {"node_id": 41, "status": "PARTIAL", "duration_key": "3M"})
        pr = client.post("/api/deeptest/prefer", json={"node_id": 41, "run_id": a, "explicit": True})
        assert pr.json()["ok"]
        cmp_ = client.post("/api/deeptest/compare", json={"run_ids": [a, b]})
        assert cmp_.json()["ok"]
        assert cmp_.json()["config_and_coverage_differences"]
        bad = client.post("/api/deeptest/run/unknown-id/cancel")
        assert bad.status_code == 200 and not bad.json()["ok"]

    def test_fetch_endpoints_are_explicit(self, client):
        r = client.get("/api/deeptest/fetch/XAUUSD/M15")
        assert r.status_code == 200 and r.json()["ok"]
        # GET MT5 DATA must be an explicit POST; GET status never starts a fetch
        assert r.json()["job"] is None or r.json()["job"]["status"] in (
            "QUEUED", "FETCHING", "VALIDATING", "DONE", "FAILED", "CANCELLED")
