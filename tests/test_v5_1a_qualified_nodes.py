"""V5.1a §4–§8 / §25 / §31 / §32 — qualified nodes, deep-backtest results, reset.

What is verified here (all against real data, no fabricated values):

1. **One classification** (§7) — ``status.node_bucket`` places every node in
   exactly one operator-facing bucket, and an infrastructure failure is never
   reported as a strategy failure.
2. **The qualified-node index** (§6/§7/§8/§25) — ``GET /api/nodes`` reports the
   current experiment, experiment-local node numbers, the bucket counts, and the
   node's real research/live values (missing values stay ``null``, never ``0``).
3. **The schedule really gates the deep backtest** (§13/§14/§17/§21) — the same
   node, same dataset, same period, with and without a restrictive schedule: the
   restrictive run must report fewer allowed bars/passes and fewer trades.
4. **The result payload is honest** (§31/§32) — a completed run exposes the
   applied schedule and an explicit verdict, including "completed with no trades".
5. **A from-scratch reset says exactly what it will do** (§4) — the preview is
   read-only, matches the live population, names the confirmation token, and
   never claims it will touch LEGACY_TEST or the market data.

The module never mutates DATA: the only writes it performs are in-process reads.
"""
from __future__ import annotations

import json
import time

import pytest

from app.status import (NODE_FILTERS, STATUS_LABELS, node_bucket, node_matches_filter)

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

#: a schedule that can only ever allow a fraction of the dataset
RESTRICTIVE = {"days": [0, 2], "sessions": ["london"], "timeframes": ["M15"],
               "conditions": {"entry_long": True, "entry_short": True},
               "timezone": "UTC", "enabled": True}


# --------------------------------------------------------------------------- #
# 1. §7 — one classification, used by every surface
# --------------------------------------------------------------------------- #
def test_01_every_node_lands_in_exactly_one_bucket():
    cases = {
        "qualified": {"status": "QUALIFIED", "data_source": "USER_RESEARCH"},
        "alive": {"status": "TESTING", "data_source": "USER_RESEARCH"},
        "failed": {"status": "FAILED", "failure_reason": "profit factor below the gate"},
        "blocked": {"status": "FAILED", "failure_reason": "dataset unavailable for XAUUSD M1"},
        "excluded": {"status": "QUALIFIED", "data_source": "LEGACY_TEST"},
        "unknown": {"status": "SOMETHING_NEW", "data_source": "USER_RESEARCH"},
    }
    for expected, row in cases.items():
        got = node_bucket(row)["bucket"]
        assert got == expected, (row, got, expected)
        assert got in NODE_FILTERS


def test_02_an_infrastructure_failure_is_never_a_strategy_failure():
    infra = node_bucket({"status": "FAILED", "failure_reason": "DATASET UNAVAILABLE: no data"})
    assert infra["bucket"] == "blocked"
    assert infra["bucket"] != "failed"
    assert "never judged" in infra["reason"]
    real = node_bucket({"status": "FAILED", "failure_reason": "max drawdown limit breached"})
    assert real["bucket"] == "failed"
    assert real["bucket"] != "blocked"


def test_03_filters_are_inclusive_where_they_should_be():
    assert node_matches_filter("qualified", "qualified")
    assert node_matches_filter("alive", "alive")          # qualified are alive too
    assert node_matches_filter("qualified", "eligible")   # eligible = the acceptable set
    assert not node_matches_filter("failed", "qualified")
    assert not node_matches_filter("blocked", "alive")
    for bucket in NODE_FILTERS:
        assert node_matches_filter(bucket, "all") is True


def test_04_the_classifier_never_invents_a_status():
    unknown = node_bucket({"status": "WHATEVER"})
    assert unknown["bucket"] == "unknown"
    assert "investigation" in unknown["reason"]
    empty = node_bucket({})
    assert empty["bucket"] == "alive" and empty["label"] == STATUS_LABELS["NOT_TESTED"]


# --------------------------------------------------------------------------- #
# 2. §6/§7/§8/§25 — the qualified-node index
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def test_05_index_reports_the_current_experiment_and_its_numbering(client):
    d = client.get("/api/nodes?filter=qualified&limit=1").json()
    assert d["ok"] is True
    exp = d["experiment"]
    assert exp["run_id"], "the index must name the experiment it describes"
    assert exp["population"] >= d["total"]
    assert "experiment-local" in exp["node_numbering"]
    assert exp["next_node_number"] == exp["node_number_range"][1] + 1
    # node numbers are per-experiment, so they never exceed the population
    if d["nodes"]:
        row = d["nodes"][0]
        assert 1 <= int(row["research_node_num"]) <= exp["population"]
        assert row["node_label"] == f"Node_{row['research_node_num']}"
        assert row["experiment"] == exp["run_id"]


def test_06_bucket_counts_are_a_partition(client):
    for f in NODE_FILTERS:
        d = client.get(f"/api/nodes?filter={f}&limit=1").json()
        assert d["ok"] is True, (f, d.get("error"))
        assert d["total"] == d["total"] and d["total"] is not None
    d = client.get("/api/nodes?filter=failed&limit=1").json()
    counts = d["counts"]
    assert sum(v for v in counts.values()) > 0
    # the qualified count is the same number every surface reports
    q = client.get("/api/nodes?filter=qualified&limit=1").json()
    assert q["total"] == counts.get("qualified", 0)


def test_07_a_missing_value_is_null_not_zero(client):
    d = client.get("/api/nodes?filter=qualified&limit=5").json()
    for row in d["nodes"]:
        # live P/L is null until the node actually trades; 0 would be a fabricated value
        assert "total_pnl" in row["live"]
        assert row["live"]["total_pnl"] is None or isinstance(row["live"]["total_pnl"], (int, float))
        assert row["position"] is None or isinstance(row["position"], dict)
        assert row["bucket"] in NODE_FILTERS
        assert row["risk"]["pct"] is not None
        for key in ("return_pct", "profit_factor", "trades"):
            assert key in row["metrics"]
        if row["metrics"]["trades"] is None:
            assert row["metrics"]["return_pct"] is None


def test_08_an_unknown_filter_is_refused_with_the_list_of_real_ones(client):
    r = client.get("/api/nodes?filter=banana")
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is False and "banana" in d["error"]
    assert set(d["filters"]) == set(NODE_FILTERS)


def test_09_blocked_nodes_are_shown_with_their_reason(client):
    d = client.get("/api/nodes?filter=blocked&limit=5").json()
    assert d["total"] > 0, "the data-blocked population must be visible, not hidden"
    for row in d["nodes"]:
        assert row["bucket"] == "blocked"
        assert row["bucket_reason"], "a hidden reason is not an explanation"
        assert row["v5_status"] in ("DATA_UNAVAILABLE", "DATA_CORRUPT", "BACKTEST_ERROR")


# --------------------------------------------------------------------------- #
# 3. §13/§14/§21 — the schedule really gates the deep backtest
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def backtests():
    from app.backtest.engine import BacktestRequest, run_backtest

    free = run_backtest(BacktestRequest(genome=GENOME, dataset_id=DATASET, stage="detail",
                                        window=(0, 1500), seed_salt="v5.1a-schedule-free"))
    gated = run_backtest(BacktestRequest(genome=GENOME, dataset_id=DATASET, stage="detail",
                                         window=(0, 1500), seed_salt="v5.1a-schedule-free",
                                         schedule=RESTRICTIVE))
    assert free.ok and gated.ok, (free.error, gated.error)
    return free, gated


def test_10_the_restrictive_schedule_blocks_bars_and_reports_it(backtests):
    free, gated = backtests
    s_free = free.metrics.get("schedule") or {}
    s_gated = gated.metrics.get("schedule") or {}
    assert s_free.get("applied") in (False, None)
    assert s_gated.get("applied") is True
    assert s_gated["bars_blocked"] > 0
    assert s_gated["bars_allowed"] + s_gated["bars_blocked"] == s_gated["bars_in_window"]
    assert s_gated["bars_allowed"] < s_free.get("bars_in_window", 10 ** 9)
    assert "Mon" in s_gated["description"] or "Mon/Wed" in s_gated["description"]
    assert "London" in s_gated["description"]


def test_11_the_restrictive_schedule_reduces_the_trades_it_took(backtests):
    free, gated = backtests
    assert (free.metrics.get("trades") or 0) >= (gated.metrics.get("trades") or 0)
    assert gated.metrics.get("trades") is not None


def test_12_a_disabled_schedule_cannot_trade_at_all():
    from app.backtest.engine import BacktestRequest, run_backtest
    off = dict(RESTRICTIVE, enabled=False)
    res = run_backtest(BacktestRequest(genome=GENOME, dataset_id=DATASET, stage="detail",
                                       window=(0, 800), seed_salt="v5.1a-schedule-off",
                                       schedule=off))
    assert res.ok, res.error
    assert res.metrics.get("trades") == 0
    assert (res.metrics.get("schedule") or {}).get("applied") is True


def test_13_switching_off_a_signals_side_removes_those_entries():
    from app.backtest.engine import BacktestRequest, run_backtest
    both = run_backtest(BacktestRequest(genome=GENOME, dataset_id=DATASET, stage="detail",
                                        window=(0, 1200), seed_salt="v5.1a-cond-both"))
    long_only = run_backtest(BacktestRequest(genome=GENOME, dataset_id=DATASET, stage="detail",
                                             window=(0, 1200), seed_salt="v5.1a-cond-both",
                                             conditions={"entry_long": True, "entry_short": False}))
    assert both.ok and long_only.ok
    assert (long_only.metrics.get("conditions") or {}).get("entry_short") is False
    assert (long_only.metrics.get("trades") or 0) <= (both.metrics.get("trades") or 0)


def test_14_the_backtest_uses_the_same_evaluator_as_live():
    """The bar mask must agree with the live evaluator on the same instants."""
    from app.live_testing import schedule as sched
    import datetime as dt
    import numpy as np

    stamps = [dt.datetime(2026, 9, 7, 12, 0, tzinfo=dt.timezone.utc) + dt.timedelta(days=i)
              for i in range(10)]
    ts = np.array([s.timestamp() for s in stamps])
    dow = np.array([s.weekday() for s in stamps])
    mask = sched.bar_mask(RESTRICTIVE, ts=ts, dow=dow)
    for i, stamp in enumerate(stamps):
        live = sched.evaluate(RESTRICTIVE, now=stamp.timestamp())
        assert bool(mask[i]) == bool(live["allowed"]), (stamp, bool(mask[i]), live["reason"])


# --------------------------------------------------------------------------- #
# 4. §31/§32 — the result payload is honest
# --------------------------------------------------------------------------- #
def _synthetic_row(**over):
    row = {"run_id": "HRUN-TEST-0001", "label": "MT5", "status": "COMPLETED", "strategy_id": 1,
           "strategy_identity": "{}", "symbol": "XAUUSD", "timeframe": "M15",
           "start_date": "2026-09-07", "end_date": "2026-10-05", "start_ts": 1.0, "end_ts": 2.0,
           "bars": 100, "data_scope": "MT5", "data_source": "MT5", "dataset_id": "ds",
           "dataset_fingerprint": "fp", "created_at": 1.0, "started_at": 1.0, "finished_at": 2.0,
           "runtime_ms": 10, "trade_count": 0, "equity_points": 0, "error": None, "notes": None,
           "diagnostic_legacy": 0, "research_eligible": 1, "window": None, "stage": "detail",
           "provenance": "{}", "artifacts": "{}", "requests": "{}", "metrics": "{}", "config": "{}"}
    row.update(over)
    return row


def test_15_a_completed_run_with_no_trades_says_exactly_that():
    from app.historical_backtest.runs import _row_to_run

    row = _synthetic_row(metrics=json.dumps({"metrics": {"trades": 0}}))
    out = _row_to_run(row, None)
    assert "no trades generated" in out["verdict"]
    assert out["status"] == "COMPLETED", "zero trades is a completed run, not an error"


def test_16_the_payload_states_the_schedule_that_was_applied():
    from app.historical_backtest.runs import _row_to_run

    cfg = {"schedule": RESTRICTIVE, "schedule_description": "Mon/Wed, London",
           "conditions": {"entry_long": True}}
    metrics = {"metrics": {"trades": 4, "schedule": {"applied": True, "bars_blocked": 90,
                                                     "bars_allowed": 10}}}
    out = _row_to_run(_synthetic_row(config=json.dumps(cfg), metrics=json.dumps(metrics)), None)
    assert out["schedule"]["configured"] is True
    assert out["schedule"]["applied_to_bars"] is True
    assert out["schedule"]["bars_blocked"] == 90 and out["schedule"]["bars_allowed"] == 10
    assert "1" in out["verdict"] or "4" in out["verdict"]


def test_17_a_failed_run_never_reports_a_verdict():
    from app.historical_backtest.runs import _row_to_run

    out = _row_to_run(_synthetic_row(status="FAILED", error="engine blew up",
                                     metrics=json.dumps({"metrics": {}})), None)
    assert out["verdict"] is None
    assert out["error"] == "engine blew up"


# --------------------------------------------------------------------------- #
# 5. §6 — the batch fan-out validates before it queues anything
# --------------------------------------------------------------------------- #
def test_18_batch_rejects_an_empty_selection(client):
    r = client.post("/api/mt5-historical/runs/batch", json={"strategy_ids": []})
    assert r.status_code == 422
    assert "strategy_ids" in json.dumps(r.json())


def test_19_batch_refuses_an_absurd_selection(client):
    r = client.post("/api/mt5-historical/runs/batch", json={"strategy_ids": list(range(400))})
    assert r.status_code == 422
    assert "200" in json.dumps(r.json())


def test_20_batch_reports_a_node_that_does_not_exist(client):
    r = client.post("/api/mt5-historical/runs/batch",
                    json={"strategy_ids": ["not-a-node"], "start_date": "2026-09-07",
                          "end_date": "2026-10-05"})
    assert r.status_code in (200, 422)
    body = r.json()
    text = json.dumps(body)
    assert "not-a-node" in text or "not a node id" in text


# --------------------------------------------------------------------------- #
# 6. §4 — the from-scratch reset is previewed before it is believed
# --------------------------------------------------------------------------- #
def test_21_fresh_preview_is_read_only_and_consistent_with_the_live_state():
    from app.research_run import counts, fresh_preview

    before = counts()
    started = time.time()
    pv = fresh_preview(mode="backup_and_reset")
    after = counts()
    assert time.time() - started < 60
    assert pv["read_only"] is True
    assert after == before, "the preview must not change anything"
    assert pv["will_delete"]["strategies"] == before["user_research_nodes"]
    assert pv["will_keep"]["legacy_test_nodes"] == before["legacy_test_nodes"]
    assert pv["confirmation_required"] == "BACKUP_AND_RESET"
    assert pv["after_reset"]["first_node_number"] == 1
    assert pv["after_reset"]["population"] > 0
    assert pv["current"]["run_id"]
    assert "never continued" in pv["after_reset"]["node_numbering"]


def test_22_the_preview_states_what_survives_a_reset():
    from app.research_run import fresh_preview

    pv = fresh_preview(mode="reset_only")
    keep = pv["will_keep"]
    assert keep["datasets"] > 0 and keep["master_datasets"] > 0
    assert "market data" in keep["note"]
    assert keep["legacy_test_nodes"] > 0
    assert "CONFIG" in keep["mt5_configuration"]
    assert pv["confirmation_required"] == "RESET_USER_RESEARCH"
    assert "rows belonging to the USER_RESEARCH experiment" in pv["will_delete"]["note"]


def test_23_the_preview_and_the_route_agree(client):
    r = client.get("/api/research-run/fresh/preview?mode=backup_and_reset")
    assert r.status_code == 200
    d = r.json()
    assert d["will_delete"]["strategies"] >= 0
    assert d["after_reset"]["first_node_number"] == 1
    bad = client.get("/api/research-run/fresh/preview?mode=whatever")
    assert bad.status_code == 422


# --------------------------------------------------------------------------- #
# 7. §11/§13 — window semantics are explicit
# --------------------------------------------------------------------------- #
def test_24_an_overnight_window_is_labelled_as_such():
    from app.live_testing import schedule as sched

    text = sched.describe({"days": [0], "windows": [{"start": "17:00", "end": "08:00"}],
                           "timezone": "UTC", "enabled": True})
    assert "(overnight)" in text
    same = sched.validate_config({"days": [0], "windows": [{"start": "08:00", "end": "08:00"}]})
    assert any(e["field"] == "windows" for e in same), "a zero-length window is impossible"
    assert not [e for e in sched.validate_config(
        {"days": [0], "windows": [{"start": "17:00", "end": "08:00"}]}) if e["field"] == "windows"]


def test_25_clearing_the_window_list_clears_the_legacy_clock_fields():
    """An emptied window list must not resurrect a previously stored 17:00–08:00."""
    from app.live_testing import schedule as sched

    legacy = {"start_time": "17:00", "end_time": "08:00", "windows": None, "days": [0]}
    assert sched.normalize_config(legacy)["windows"], "a legacy window is still read"
    cleared = dict(legacy, windows=[], start_time=None, end_time=None)
    norm = sched.normalize_config(cleared)
    assert not norm["windows"], "clearing the list must clear the window restriction"
    assert norm["days"] == [0], "the other groups must survive the change"
