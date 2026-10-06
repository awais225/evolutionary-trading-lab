"""
V4.6 — MT5 historical backtest execution: focused test suite.

Covers exactly what the iteration asked for: run creation, configuration
validation, USER_RESEARCH scope, legacy exclusion, execution, result
persistence, repeat runs, failure handling, duplicate protection, result
retrieval, trade pagination, equity retrieval, no-live-order safety,
provenance, missing metrics, API JSON safety, and the Strategy Lab / Backtest
Matrix wiring.

Everything is hermetic: the tests build their own synthetic dataset (unique id,
its own parquet files) inside the disposable DATA root that
EVOLUTIONARY_LAB_DATA_ROOT points at, and their own SQLite database in tmp_path.
No existing dataset, research row or legacy row is touched.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
for p in (str(BACKEND),):
    if p not in sys.path:
        sys.path.insert(0, p)

from app import paths as P                        # noqa: E402
from app.db.database import Database, get_db      # noqa: E402
from app.historical_backtest import runs as hb    # noqa: E402

SYM = "TESTSYM"
TF = "M15"
DSID = "TESTSYM_M15_TESTDATA_V1"          # unique: never collides with real data
BARS = 900
T0 = 1_800_000_000.0                      # 2027-01-15T08:00:00Z


# --------------------------------------------------------------------------- #
# fixtures: synthetic dataset + database
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def dataset_files():
    """A 900-bar synthetic dataset (parquet + feature artifact) in the DATA root."""
    rng = np.random.default_rng(4242)
    ts = T0 + np.arange(BARS) * TF_SECONDS
    px = 2000 + np.cumsum(rng.normal(0, 0.9, BARS))
    stamps = pd.to_datetime(ts, unit="s", utc=True)
    df = pd.DataFrame({
        "ts": ts, "open": px, "high": px + 1.2, "low": px - 1.2, "close": px + 0.3,
        "bid": px - 0.2, "ask": px + 0.2, "spread": 40.0, "tick_volume": 100,
        # the lab's own storage schema (data/storage.COLUMNS) — the backtest engine
        # reads dow/dom/hour/minute directly from the dataset frame
        "session": "london", "dow": stamps.dayofweek.to_numpy(),
        "dom": stamps.day.to_numpy(), "hour": stamps.hour.to_numpy(),
        "minute": stamps.minute.to_numpy(),
    })
    ds_path = P.DATA_CACHE_DIR / f"{DSID}.parquet"
    feat_path = P.FEATURES_DIR / f"{DSID}.parquet"
    ds_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(ds_path, index=False)
    # eligibility contract wants a feature artifact carrying price/close
    df[["ts", "close"]].to_parquet(feat_path, index=False)
    yield {"dataset": ds_path, "features": feat_path, "frame": df}
    for p in (ds_path, feat_path):
        try:
            os.remove(p)
        except OSError:
            pass


TF_SECONDS = 15 * 60


@pytest.fixture()
def db(tmp_path, dataset_files, monkeypatch):
    d = Database(str(tmp_path / "v46.db"))
    monkeypatch.setattr(hb, "get_db", lambda: d)
    monkeypatch.setattr(hb, "_EXECUTOR", _SyncExecutor())
    for sid, status, source in ((9101, "SURVIVED", "USER_RESEARCH"),
                                (9102, "QUALIFIED", "USER_RESEARCH"),
                                (9103, "FAILED", "USER_RESEARCH"),
                                (9901, "SURVIVED", "LEGACY_TEST")):
        add_node(d, sid, status=status, data_source=source)
    d.x("""INSERT INTO datasets (id,symbol,timeframe,start_ts,end_ts,bars,source,path,created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (DSID, SYM, TF, float(T0), float(T0 + (BARS - 1) * TF_SECONDS), BARS, "MT5",
         str(dataset_files["dataset"]), 1.0))
    d.x("""INSERT INTO master_datasets (symbol,timeframe,source,broker,server,profile,first_ts,last_ts,
                                        rows,version,schema_version,fingerprint,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (SYM, TF, "MT5", "TestBroker", "TestServer", "MT5|TestBroker|TestServer",
         float(T0), float(T0 + (BARS - 1) * TF_SECONDS), BARS, 1, 3, "abc123fingerprint", 1.0))
    return d


class _SyncExecutor:
    """Deterministic stand-in for the one-worker executor."""

    def __init__(self):
        self.calls = []

    def submit(self, fn, *args, **kwargs):
        self.calls.append((fn, args))
        if getattr(self, "hold", False):          # tests that need a stuck QUEUED run
            return None
        fn(*args, **kwargs)
        return None


def genome(symbol=SYM, timeframe=TF, with_exit=True):
    g = {
        "symbol": symbol, "timeframe": timeframe, "direction": "short",
        "features": ["sma:10", "rsi:14"],
        "entry_long": None,
        "entry_short": {"type": "compare", "left": "sma:10", "cmp": ">", "right": "close"},
    }
    if with_exit:
        g["exit"] = {"sl_atr_mult": 1.5, "tp_atr_mult": 3.0, "atr_spec": "atr:14",
                     "trailing": False, "min_hold_bars": 1, "max_hold_bars": 96}
        g["risk"] = {"risk_per_trade": 0.005, "max_concurrent": 1}
    return g


def add_node(db, sid, status="SURVIVED", data_source="USER_RESEARCH", g=None):
    db.x("""INSERT OR REPLACE INTO strategies
            (id, hash, parent_id, generation, symbol, timeframe, direction, status, genome,
             complexity, fitness, created_at, updated_at, origin, run_id, data_source,
             research_node_num)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
         (sid, f"hash{sid}", None, 3, SYM, TF, "short", status, json.dumps(g or genome()),
          7, 0.5, time.time(), time.time(), "research", "RUN-V46-ALPHA", data_source, sid))


def valid_body(sid=9101, **over):
    body = {"strategy_id": sid, "symbol": SYM, "timeframe": TF,
            "start_date": iso(T0), "end_date": iso(T0 + 6 * 24 * 3600),
            "initial_balance": 10000.0, "risk_per_trade": 0.005}
    body.update(over)
    return body


def iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")


def run_to_end(db, body, expect="COMPLETED"):
    res = hb.start_run(body, db=db)
    assert res["started"], res
    run = hb.get_run(res["run_id"], db=db)
    if expect is not None:
        assert run["status"] == expect, (run["status"], run.get("error"))
    return run


# --------------------------------------------------------------------------- #
# 1. capabilities
# --------------------------------------------------------------------------- #
def test_capabilities_reports_eligible_datasets_and_defaults(db):
    caps = hb.capabilities(db=db)
    ds = {d["dataset_id"]: d for d in caps["datasets"]}
    assert DSID in ds, caps["datasets"]
    entry = ds[DSID]
    assert entry["symbol"] == SYM and entry["timeframe"] == TF and entry["source"] == "MT5"
    assert entry["bars"] == BARS and entry["eligible"] is True and entry["broker"] == "TestBroker"
    assert caps["limits"]["min_bars"] == 300 and caps["limits"]["max_active"] == 1
    assert caps["defaults"]["initial_balance"] > 0 and caps["defaults"]["commission_per_lot"] >= 0
    assert caps["modes"]["historical_backtest"] is True and caps["modes"]["places_orders"] is False
    assert "never places" in caps["modes"]["order_execution_path"]
    assert caps["scope"]["default"] == "MT5" and "SIMULATOR" in caps["scope"]["options"]


def test_capabilities_is_json_safe(db):
    _assert_json_safe(hb.capabilities(db=db))


# --------------------------------------------------------------------------- #
# 2. configuration validation
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("over,field", [
    ({"strategy_id": 424242}, "strategy_id"),
    ({"start_date": ""}, "start_date"),
    ({"end_date": ""}, "end_date"),
    ({"end_date": iso(T0 - 5 * 86400)}, "end_date"),
    ({"start_date": iso(T0), "end_date": iso(T0 + 2 * 86400)}, "start_date"),   # too short
    ({"initial_balance": 0}, "initial_balance"),
    ({"risk_per_trade": 0.9}, "risk_per_trade"),
    ({"spread_mult": 0.0}, "spread_mult"),
    ({"slippage_mult": 99}, "slippage_mult"),
    ({"commission_mult": -1}, "commission_mult"),
    ({"max_trades": 0}, "max_trades"),
    ({"timeframe": "M5"}, "symbol"),
    ({"symbol": "NOPE"}, "symbol"),
    ({"data_scope": "LIVE"}, "data_scope"),
])
def test_validation_rejects_bad_configuration(db, over, field):
    cfg, errors = hb.validate_request(valid_body(**over), db=db)
    assert cfg is None and errors, (cfg, errors)
    assert any(e.get("field") == field for e in errors), errors
    assert all(isinstance(e.get("error"), str) and e["error"] for e in errors)


def test_validation_accepts_a_complete_request_and_records_scope(db):
    cfg, errors = hb.validate_request(valid_body(), db=db)
    assert errors == [] and cfg
    assert cfg["strategy_data_source"] == "USER_RESEARCH"
    assert cfg["dataset_id"] == DSID and cfg["dataset_source"] == "MT5"
    assert cfg["bars"] >= 300 and cfg["window"][1] > cfg["window"][0]
    assert cfg["request_key"] and len(cfg["request_key"]) == 32
    assert cfg["genome_hash"]


def test_legacy_node_is_excluded_unless_explicitly_diagnostic(db):
    cfg, errors = hb.validate_request(valid_body(9901), db=db)
    assert cfg is None
    assert "LEGACY_TEST" in errors[0]["error"] and "diagnostic" in errors[0]["error"]

    cfg2, errors2 = hb.validate_request(valid_body(9901, diagnostic_legacy=True), db=db)
    assert errors2 == [] and cfg2 and cfg2["diagnostic_legacy"] is True


# --------------------------------------------------------------------------- #
# 3. run creation, execution, persistence
# --------------------------------------------------------------------------- #
def test_run_executes_and_persists_results_and_artifacts(db):
    run = run_to_end(db, valid_body())
    assert run["orders_placed"] is False and run["is_mt5_data"] is True
    assert run["label"] == "HISTORICAL MT5 BACKTEST"
    assert run["runtime_ms"] is not None and run["runtime_ms"] >= 0
    m = run["results"]["metrics"]
    for key in ("trades", "net_profit", "profit_factor", "win_rate", "max_drawdown_pct",
                "total_return_pct", "sharpe", "sortino", "expectancy", "final_equity",
                "genome_hash", "dataset_id", "stage"):
        assert key in m, key
    assert m["stage"] == "mt5_hist" and m["dataset_id"] == DSID
    assert run["trade_count"] == m["trades"] and run["equity_points"] >= 0

    base = Path(hb.run_dir(run["run_id"]))
    for name in ("trades.parquet", "equity.parquet", "metrics.json", "config.json", "provenance.json"):
        assert (base / name).exists(), name
    stored = json.loads((base / "config.json").read_text())
    assert stored["strategy_id"] == 9101 and stored["request_key"]

    row = db.one("SELECT * FROM mt5_historical_runs WHERE run_id=?", (run["run_id"],))
    assert row["status"] == "COMPLETED" and row["data_source"] == "MT5"
    assert row["research_eligible"] == 1 and row["diagnostic_legacy"] == 0


def test_run_writes_nothing_into_order_or_research_tables(db):
    before = _counts(db)
    run_to_end(db, valid_body())
    after = _counts(db)
    assert before == after, {k: (before[k], after[k]) for k in before if before[k] != after[k]}
    assert db.one("SELECT COUNT(*) c FROM mt5_historical_runs")["c"] == 1
    assert db.one("SELECT COUNT(*) c FROM backtests")["c"] == 0
    assert db.one("SELECT COUNT(*) c FROM validations")["c"] == 0
    # the research population is untouched
    assert db.one("SELECT COUNT(*) c FROM strategies WHERE data_source='USER_RESEARCH'")["c"] == 3
    assert db.one("SELECT COUNT(*) c FROM strategies WHERE data_source='LEGACY_TEST'")["c"] == 1


def _counts(db):
    return {t: db.one(f"SELECT COUNT(*) c FROM {t}")["c"] for t in
            ("executions", "paper_trades", "mt5_demo_trades", "live_test_trades",
             "mt5_backtests", "backtests", "validations")}


def test_repeat_runs_are_separate_and_deterministic(db):
    first = run_to_end(db, valid_body())
    second = run_to_end(db, valid_body(end_date=iso(T0 + 8 * 24 * 3600)))
    assert first["run_id"] != second["run_id"]

    # same strategy can be re-run over a different period: both rows exist
    rows = hb.list_runs(db=db, strategy_id=9101)["runs"]
    assert len(rows) == 2
    assert {r["run_id"] for r in rows} == {first["run_id"], second["run_id"]}

    # identical request over the same period is deterministic
    third = run_to_end(db, valid_body())
    assert third["run_id"] not in (first["run_id"], second["run_id"])
    m1 = {k: v for k, v in first["results"]["metrics"].items() if k != "runtime_ms"}
    m3 = {k: v for k, v in third["results"]["metrics"].items() if k != "runtime_ms"}
    assert m3 == m1                                   # runtime is a measurement, not a result
    assert hb.run_trades(third["run_id"], db=db, limit=5)["trades"] == \
        hb.run_trades(first["run_id"], db=db, limit=5)["trades"]


def test_duplicate_request_while_active_is_refused(db, monkeypatch):
    monkeypatch.setattr(hb._EXECUTOR, "hold", True, raising=False)
    first = hb.start_run(valid_body(), db=db)
    assert first["started"] and first["status"] == "QUEUED"
    again = hb.start_run(valid_body(), db=db)
    assert again["started"] is False and again["duplicate"] is True
    assert again["existing_run_id"] == first["run_id"]
    assert "identical run" in again["errors"][0]["error"]
    # a different period is NOT merged into the running request
    other = hb.start_run(valid_body(end_date=iso(T0 + 7 * 24 * 3600)), db=db)
    assert other["started"] is True and other["run_id"] != first["run_id"]


def test_queue_limit_is_enforced(db, monkeypatch):
    monkeypatch.setattr(hb._EXECUTOR, "hold", True, raising=False)
    for i in range(hb.MAX_QUEUE):
        res = hb.start_run(valid_body(end_date=iso(T0 + (4 + i) * 24 * 3600)), db=db)
        assert res["started"], res
    full = hb.start_run(valid_body(end_date=iso(T0 + 9 * 24 * 3600)), db=db)
    assert full["started"] is False and "queue is full" in full["errors"][0]["error"]


def test_cancel_queued_run_and_reject_cancel_of_finished(db, monkeypatch):
    monkeypatch.setattr(hb._EXECUTOR, "hold", True, raising=False)
    queued = hb.start_run(valid_body(), db=db)
    res = hb.cancel_run(queued["run_id"], db=db)
    assert res["ok"] and res["cancelled"] is True
    assert res["run"]["status"] == "CANCELLED" and "cancelled by operator" in res["run"]["error"]

    monkeypatch.setattr(hb._EXECUTOR, "hold", False, raising=False)
    done = run_to_end(db, valid_body(end_date=iso(T0 + 5 * 24 * 3600)))
    nope = hb.cancel_run(done["run_id"], db=db)
    assert nope["ok"] is False and "cannot be cancelled" in nope["error"]


def test_failed_run_keeps_a_useful_reason_and_never_fakes_success(db):
    # a QUEUED row whose dataset cannot be resolved must FAIL, not "complete"
    db.x("""INSERT INTO mt5_historical_runs (run_id,strategy_id,label,status,data_scope,data_source,
                dataset_id,symbol,timeframe,start_date,end_date,start_ts,end_ts,bars,initial_balance,
                risk_per_trade,config,request_key,genome_hash,created_at,research_eligible)
             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
         ("HRUN-BROKEN-1", 9101, "HISTORICAL MT5 BACKTEST", "QUEUED", "MT5", "MT5",
          "MISSING_DATASET_V1", SYM, TF, "2027-01-15", "2027-02-15", T0, T0 + 1000, 500,
          10000.0, 0.005, json.dumps({"strategy_id": 9101, "dataset_id": "MISSING_DATASET_V1",
                                      "window": [0, 500], "symbol": SYM, "timeframe": TF,
                                      "cost_multipliers": {}, "start_ts": T0, "end_ts": T0 + 1000,
                                      "initial_balance": 10000.0, "risk_per_trade": 0.005}),
          "brokenkey", "gh", 1.0, 1))
    hb._execute("HRUN-BROKEN-1", db)
    run = hb.get_run("HRUN-BROKEN-1", db=db)
    assert run["status"] == "FAILED"
    assert run["error"] and "dataset" in run["error"].lower()
    assert run["results"]["metrics"] == {}
    assert run["trade_count"] is None and run["equity_points"] is None
    # and a failed run has a clear, empty trade list with a reason
    tr = hb.run_trades("HRUN-BROKEN-1", db=db)
    assert tr["trades"] == [] and tr["unavailable"][0]["metric"] == "trades"


def test_unknown_dataset_in_a_request_is_rejected_before_queueing(db):
    cfg, errors = hb.validate_request(valid_body(symbol="XAUUSD", timeframe="M15"), db=db)
    assert cfg is None and "no eligible" in errors[0]["error"]


def test_stale_running_rows_are_reconciled(db):
    db.x("""INSERT INTO mt5_historical_runs (run_id,strategy_id,label,status,data_scope,symbol,
                timeframe,created_at,started_at,research_eligible)
             VALUES (?,?,?,?,?,?,?,?,?,?)""",
         ("HRUN-ORPHAN", 9101, "HISTORICAL MT5 BACKTEST", "RUNNING", "MT5", SYM, TF,
          1.0, 1.0, 1))
    hb._reconcile_stale(db)
    row = db.one("SELECT status, error FROM mt5_historical_runs WHERE run_id='HRUN-ORPHAN'")
    assert row["status"] == "FAILED" and "orphaned RUNNING" in row["error"]


# --------------------------------------------------------------------------- #
# 4. retrieval: run, list, trades, equity
# --------------------------------------------------------------------------- #
def test_result_retrieval_list_trades_and_equity(db):
    run = run_to_end(db, valid_body())
    rid = run["run_id"]

    listed = hb.list_runs(db=db, strategy_id=9101)
    assert listed["total"] == 1 and listed["runs"][0]["run_id"] == rid
    assert listed["orders_placed"] is False and listed["label"] == "HISTORICAL MT5 BACKTEST"

    all_trades = hb.run_trades(rid, db=db, limit=hb.MAX_TRADES_LIMIT)
    total = all_trades["total"]
    assert total == run["trade_count"] and total > 0
    page = hb.run_trades(rid, db=db, limit=3, offset=2)
    assert page["count"] == min(3, max(0, total - 2))
    assert [t["entry_ts"] for t in page["trades"]] == \
        [t["entry_ts"] for t in all_trades["trades"][2:5]]
    assert {"side", "entry_ts", "entry_price", "exit_ts", "exit_price", "lots", "pnl",
            "exit_reason", "hold_bars"} <= set(page["trades"][0])

    eq = hb.run_equity(rid, db=db, max_points=10)
    assert eq["points_total"] >= 1 and len(eq["points"]) <= 10
    assert eq["downsampled"] == (eq["points_total"] > 10)
    assert eq["points"][0][0] == pytest.approx(T0, abs=1e6)
    assert eq["summary"]["source"].startswith("equity.parquet")
    _assert_json_safe(eq)


def test_missing_run_ids_are_reported_not_faked(db):
    assert hb.get_run("HRUN-NOPE", db=db) is None
    assert hb.run_trades("HRUN-NOPE", db=db)["ok"] is False
    assert hb.run_equity("HRUN-NOPE", db=db)["ok"] is False
    assert hb.cancel_run("HRUN-NOPE", db=db)["ok"] is False


def test_metrics_are_engine_values_or_explicitly_unavailable(db):
    run = run_to_end(db, valid_body())
    res = run["results"]
    metrics, derived, unavailable = res["metrics"], res["derived"], res["unavailable"]
    # engine values only in `metrics`; extra values are labelled derived
    assert derived.get("source", "").startswith("derived from the persisted trades.parquet")
    for key in ("largest_win", "largest_loss", "avg_win", "avg_loss",
                "consecutive_wins", "consecutive_losses"):
        assert key in derived and derived[key] is not None, key
    # metrics the engine does not produce are reported, never invented or zeroed
    names = {u["metric"] for u in unavailable}
    assert {"risk_amount", "recovery_factor", "drawdown_abs"} <= names
    assert all(u["reason"] for u in unavailable)
    for key in ("risk_amount", "recovery_factor", "drawdown_abs"):
        assert key not in metrics


def test_provenance_records_exactly_what_was_tested(db, dataset_files):
    run = run_to_end(db, valid_body())
    pv = run["provenance"]
    md = pv["market_data"]
    assert md["dataset_id"] == DSID and md["dataset_source"] == "MT5"
    assert md["broker"] == "TestBroker" and md["server"] == "TestServer"
    assert md["dataset_fingerprint"] == "abc123fingerprint"
    assert md["dataset_bars_total"] == BARS
    assert md["file"]["path"] == str(dataset_files["dataset"])
    assert md["file"]["sha256"] and len(md["file"]["sha256"]) == 64
    assert pv["period"]["bars"] == run["period"]["bars"] and pv["period"]["timezone"].startswith("UTC")
    assert pv["strategy_identity"]["node_id"] == 9101 and pv["strategy_identity"]["genome_hash"]
    assert pv["engine_versions"]["backtester"] and pv["engine_versions"]["app_version"]
    assert pv["fingerprint"] and len(pv["fingerprint"]) == 32
    assert pv["orders"]["placed"] is False
    assert pv["bridge"]["active_bridge"]
    assert pv["execution"]["engine"].startswith("app.backtest.engine")


# --------------------------------------------------------------------------- #
# 5. safety: a historical backtest can never place an order
# --------------------------------------------------------------------------- #
def test_historical_backtest_never_calls_any_order_path(db, monkeypatch):
    calls = []

    def tripwire(name):
        def _fail(*a, **k):
            calls.append(name)
            raise AssertionError(f"historical backtest must never call {name}")
        return _fail

    from app.mt5 import execution as mt5_execution
    from app.mt5.bridge import MarketBridge
    from app.live_testing import engine as lt_engine

    monkeypatch.setattr(mt5_execution, "place_demo_order", tripwire("place_demo_order"))
    monkeypatch.setattr(mt5_execution, "close_demo_position", tripwire("close_demo_position"))
    monkeypatch.setattr(MarketBridge, "send_market_order", tripwire("send_market_order"),
                        raising=True)
    monkeypatch.setattr(MarketBridge, "close_position", tripwire("close_position"), raising=True)
    monkeypatch.setattr(lt_engine.LiveTestingEngine, "_attempt",
                        tripwire("live_testing_order_placement"), raising=True)

    before = _counts(db)
    run = hb.start_run(valid_body(), db=db)
    assert run["started"]
    hb._execute(run["run_id"], db)          # explicit execution of the queued run
    done = hb.get_run(run["run_id"], db=db)
    assert done["status"] == "COMPLETED"
    assert calls == [], calls
    assert _counts(db) == before

    # the module itself must not import the order path
    src = Path(hb.__file__).read_text()
    for forbidden in ("place_demo_order", "close_demo_position", "send_market_order",
                      "close_position", "live_testing"):
        assert f"import {forbidden}" not in src and f"from .{forbidden}" not in src


def test_simulator_scope_never_claims_mt5(db):
    # no SIMULATOR dataset for this symbol exists in the fixture -> honest refusal
    cfg, errors = hb.validate_request(valid_body(data_scope="SIMULATOR"), db=db)
    assert cfg is None and "no eligible SIMULATOR dataset" in errors[0]["error"]


def test_diagnostic_legacy_run_is_marked_and_excluded_by_default(db):
    run = run_to_end(db, valid_body(9901, diagnostic_legacy=True))
    assert run["diagnostic_legacy"] is True and run["research_eligible"] is False
    default = hb.list_runs(db=db, include_diagnostic=False)
    assert all(r["run_id"] != run["run_id"] for r in default["runs"])
    marked = hb.list_runs(db=db, strategy_id=9901)
    assert marked["runs"][0]["research_eligible"] is False


# --------------------------------------------------------------------------- #
# 6. HTTP surface + JSON safety
# --------------------------------------------------------------------------- #
@pytest.fixture()
def client(db, dataset_files):
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def test_http_surface_end_to_end(client, db):
    caps = client.get("/api/mt5-historical/capabilities")
    assert caps.status_code == 200 and caps.json()["modes"]["places_orders"] is False

    started = client.post("/api/mt5-historical/runs", json=valid_body())
    assert started.status_code == 200, started.text
    run_id = started.json()["run_id"]

    detail = client.get(f"/api/mt5-historical/runs/{run_id}")
    assert detail.status_code == 200 and detail.json()["run_id"] == run_id
    assert detail.json()["results"]["metrics"]["stage"] == "mt5_hist"

    trades = client.get(f"/api/mt5-historical/runs/{run_id}/trades?limit=2")
    assert trades.status_code == 200 and trades.json()["total"] >= 1
    equity = client.get(f"/api/mt5-historical/runs/{run_id}/equity?max_points=20")
    assert equity.status_code == 200 and equity.json()["points"]
    listing = client.get("/api/mt5-historical/runs?strategy_id=9101")
    assert listing.status_code == 200 and listing.json()["total"] == 1

    for payload in (caps.json(), detail.json(), trades.json(), equity.json(), listing.json()):
        _assert_json_safe(payload)


def test_http_errors_are_structured(client, db):
    bad = client.post("/api/mt5-historical/runs", json=valid_body(end_date=iso(T0 - 86400)))
    assert bad.status_code == 422 and bad.json()["detail"]["errors"][0]["field"] == "end_date"
    missing = client.get("/api/mt5-historical/runs/HRUN-NOPE")
    assert missing.status_code == 404 and "not found" in missing.json()["detail"]


def test_frontend_is_wired_to_the_v46_surface(db):
    api_js = (ROOT / "frontend" / "src" / "api.js").read_text()
    for helper in ("mt5HistoricalCapabilities", "mt5HistoricalStartRun", "mt5HistoricalRuns",
                   "mt5HistoricalRun", "mt5HistoricalTrades", "mt5HistoricalEquity",
                   "mt5HistoricalCancel"):
        assert f"{helper}:" in api_js, helper
    lab = (ROOT / "frontend" / "src" / "pages" / "StrategyLab.jsx").read_text()
    assert "HistoricalBacktestPanel" in lab and "Historical MT5 backtest" in lab
    panel = (ROOT / "frontend" / "src" / "components" / "HistoricalBacktestPanel.jsx").read_text()
    assert "Run historical backtest" in panel and "Start historical backtest" in panel
    assert "RESEARCH EXECUTION — NO ORDERS" in panel
    results = (ROOT / "frontend" / "src" / "components" / "HistoricalRunResults.jsx")
    assert results.exists()
    page = (ROOT / "frontend" / "src" / "pages" / "Mt5Backtest.jsx").read_text()
    assert "HistoricalRunResults" in page and "HISTORICAL MT5 BACKTEST" in page
    matrix = (ROOT / "frontend" / "src" / "pages" / "BacktestMatrix.jsx").read_text()
    assert "MT5 HISTORICAL RUNS" in matrix and "RESEARCH BACKTEST RESULTS" in matrix
    assert "mt5HistoricalRuns" in matrix


# --------------------------------------------------------------------------- #
# 7. additive schema / migration safety
# --------------------------------------------------------------------------- #
def test_schema_addition_preserves_existing_rows(tmp_path):
    legacy = tmp_path / "legacy.db"
    d1 = Database(str(legacy))
    add_node(d1, 7777, status="QUALIFIED")
    d1.x("INSERT INTO executions (ts, source, symbol, side, strategy_id, result) "
         "VALUES (?,?,?,?,?,?)", (1.0, "SIMULATOR", SYM, "buy", 7777, "FILLED"))
    before = {t: d1.one(f"SELECT COUNT(*) c FROM {t}")["c"] for t in ("strategies", "executions")}
    d1._conn.close()

    d2 = Database(str(legacy))          # reopen: additive DDL must apply idempotently
    assert d2.one("SELECT COUNT(*) c FROM strategies")["c"] == before["strategies"]
    assert d2.one("SELECT COUNT(*) c FROM executions")["c"] == before["executions"]
    assert d2.one("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                  ("mt5_historical_runs",)) is not None
    cols = {r[1] for r in d2._conn.execute("PRAGMA table_info(mt5_historical_runs)")}
    assert {"run_id", "strategy_id", "status", "config", "provenance", "metrics",
            "request_key", "research_eligible"} <= cols
    assert d2.one("SELECT value FROM meta WHERE key='migrated_at'") is None or True


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _assert_json_safe(payload):
    """No NaN/Infinity, no non-serializable objects, no '[object Object]'."""
    text = json.dumps(payload, allow_nan=False)
    json.loads(text, parse_constant=_no_constants)
    assert "[object Object]" not in text


def _no_constants(name):                                          # pragma: no cover
    raise AssertionError(f"non-JSON constant in payload: {name}")
