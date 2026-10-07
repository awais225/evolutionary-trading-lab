"""V5.1a §32/§33 — the historical data-loading report and the balance fields.

§32 requires a run to say which data it loaded: the requested period, the period
that was actually used, the bar count, the source (MT5 or the lab simulator),
how complete the slice is and which periods are missing — so a small subset can
never be presented as the whole period.

These tests read the real stored datasets (read-only) and the run payload
builder; they never write to DATA and never place an order.
"""
from __future__ import annotations

import json

import pytest

from app.historical_backtest import runs as hb

#: the MT5 dataset the live installation carries (see /mt5-historical/capabilities)
MT5_DATASET = None


@pytest.fixture(scope="module")
def mt5_dataset():
    """The first eligible MT5 dataset the catalogue offers (real bars)."""
    from app.db.database import get_db
    cat = hb.catalogue(get_db(), refresh=True)
    for ds in cat.get("datasets") or []:
        if str(ds.get("source") or "").upper() == "MT5" and ds.get("eligible"):
            return ds
    pytest.skip("no eligible MT5 dataset is provisioned in this environment")


def _cfg_for(ds, *, window=None):
    return {
        "dataset_id": ds["dataset_id"], "dataset_source": ds.get("source"),
        "data_scope": ds.get("source"), "dataset_bars": ds.get("bars"),
        "dataset_fingerprint": ds.get("fingerprint"), "dataset_version": ds.get("version"),
        "dataset_broker": ds.get("broker"), "dataset_server": ds.get("server"),
        "dataset_first_ts": None, "dataset_last_ts": None,
        "symbol": ds.get("symbol"), "timeframe": ds.get("timeframe"),
        "start_ts": None, "end_ts": None, "requested_start_ts": None, "requested_end_ts": None,
        "start_date": "2026-09-07", "end_date": "2026-10-05",
        "window": window or [0, int(ds.get("bars") or 0)],
        "period_adjusted": False, "initial_balance": 10000.0,
    }


# --------------------------------------------------------------------------- #
# §32 — the coverage report
# --------------------------------------------------------------------------- #
def test_01_the_coverage_report_matches_the_real_dataset(mt5_dataset):
    bars = int(mt5_dataset["bars"])
    cfg = _cfg_for(mt5_dataset)
    cov = hb._coverage_report(cfg, {"trades": 0})
    assert cov["bars_used"] == bars
    assert cov["actual"]["bars"] == bars
    assert cov["dataset"]["id"] == mt5_dataset["dataset_id"]
    assert cov["dataset"]["source"] == mt5_dataset["source"]
    assert cov["is_mt5_data"] is True
    assert "MT5" in cov["source_label"]
    # the timestamps really were re-read from the dataset
    assert cov["gaps_checked"] is True
    assert cov["quality"]["bars"] == bars
    assert cov["quality"]["monotonic_increasing"] is True
    assert cov["quality"]["duplicate_timestamps"] == 0
    assert cov["quality"]["first_bar_iso"] and cov["quality"]["last_bar_iso"]


def test_02_requested_and_actual_are_both_stated(mt5_dataset):
    cfg = _cfg_for(mt5_dataset)
    cfg.update({"requested_start_ts": 1_788_742_800.0, "requested_end_ts": 1_791_206_400.0,
                "start_ts": 1_788_742_800.0, "end_ts": 1_791_179_100.0})
    cov = hb._coverage_report(cfg, {})
    assert cov["requested"]["start"] and cov["requested"]["end"]
    assert cov["actual"]["start"] and cov["actual"]["end"]
    assert cov["requested"]["start_date"] == "2026-09-07"
    assert cov["requested"]["end_date"] == "2026-10-05"
    # the used range is never wider than what was asked for
    assert cov["actual"]["start"] >= cov["requested"]["start"]
    assert cov["actual"]["end"] <= cov["requested"]["end"]


def test_03_completeness_is_measured_and_gaps_are_named(mt5_dataset):
    cfg = _cfg_for(mt5_dataset)
    # a real run's config carries the resolved window bounds; the fixture must too
    cfg["start_ts"] = hb._parse_ts(mt5_dataset.get("start"))
    cfg["end_ts"] = hb._parse_ts(mt5_dataset.get("end"))
    cfg["requested_start_ts"], cfg["requested_end_ts"] = cfg["start_ts"], cfg["end_ts"]
    cov = hb._coverage_report(cfg, {})
    assert cov["expected_bars"] is not None and cov["expected_bars"] > 0
    assert cov["completeness_pct"] is not None
    # a real 15-minute MT5 export trades ~23h/day and closes at the weekend, so it
    # is NOT a full 24/7 grid: the report must say so, not round up to 100%
    assert cov["completeness_pct"] < 100.0
    assert cov["complete"] is False
    assert cov["quality"]["gaps"] > 0
    assert cov["quality"]["missing_bars_total"] > 0
    assert cov["missing_periods"], "the gaps must be listed, not only counted"
    assert cov["expected_bars_in_slice"] >= cov["bars_used"] - 1
    g0 = cov["missing_periods"][0]
    assert g0["after"] < g0["before"] and g0["missing_bars"] >= 1
    assert cov["interpretation"]


def test_04_an_empty_or_missing_window_never_claims_coverage(mt5_dataset):
    cfg = _cfg_for(mt5_dataset, window=[0, 0])
    cov = hb._coverage_report(cfg, {})
    assert cov["bars_used"] == 0
    assert cov["completeness_pct"] is None or cov["completeness_pct"] == 0
    assert cov["complete"] in (None, False)


def test_05_a_non_mt5_dataset_is_labelled_as_simulator(mt5_dataset):
    from app.db.database import get_db
    cat = hb.catalogue(get_db())
    sim = next((d for d in cat.get("datasets") or []
                if str(d.get("source") or "").upper() != "MT5"), None)
    if sim is None:
        pytest.skip("this installation carries no simulator dataset")
    cov = hb._coverage_report(_cfg_for(sim), {})
    assert cov["is_mt5_data"] is False
    assert "SIMULATOR" in (cov["source_label"] or "").upper()


# --------------------------------------------------------------------------- #
# §33 — balances
# --------------------------------------------------------------------------- #
def test_06_balances_are_derived_from_the_run_and_labelled():
    m = hb._apply_balances({"initial_balance": 10000.0},
                           {"net_profit": 97.35, "final_equity": 10097.35})
    assert m["start_balance"] == pytest.approx(10000.0)
    assert m["end_balance"] == pytest.approx(10097.35)
    assert "balance_source" in m

    # when the engine reports no final equity the end balance is start + P&L, and
    # when there is no starting balance nothing is invented
    m2 = hb._apply_balances({"initial_balance": 5000.0}, {"net_profit": -12.5})
    assert m2["end_balance"] == pytest.approx(4987.5)
    m3 = hb._apply_balances({}, {"final_equity": 1234.0})
    assert "start_balance" not in m3
    assert m3["end_balance"] == pytest.approx(1234.0)


def test_07_the_run_payload_surfaces_the_coverage_and_the_balances():
    stored = {"metrics": {"trades": 4, "net_profit": 97.35, "final_equity": 10097.35},
              "derived": {"avg_win": 84.234, "avg_loss": -35.56},
              "coverage": {"bars_used": 1850, "expected_bars": 2708,
                           "completeness_pct": 68.316, "complete": False,
                           "source_label": "MT5 (the terminal's own historical bars)",
                           "missing_periods": [{"after": "2026-09-07T21:15:00+00:00",
                                                "before": "2026-09-08T01:00:00+00:00",
                                                "missing_bars": 14}]},
              "unavailable": []}
    row = {"run_id": "HRUN-TEST-COV", "label": "MT5", "status": "COMPLETED", "strategy_id": 1,
           "strategy_identity": "{}", "symbol": "XAUUSD", "timeframe": "M15",
           "start_date": "2026-09-07", "end_date": "2026-10-05", "start_ts": 1.0, "end_ts": 2.0,
           "bars": 1850, "data_scope": "MT5", "data_source": "MT5", "dataset_id": "ds",
           "dataset_fingerprint": "fp", "created_at": 1.0, "started_at": 1.0, "finished_at": 2.0,
           "runtime_ms": 10, "trade_count": 4, "equity_points": 9, "error": None, "notes": None,
           "diagnostic_legacy": 0, "research_eligible": 1, "window": None, "stage": "mt5_hist",
           "provenance": "{}", "artifacts": "{}", "requests": "{}",
           "metrics": json.dumps(stored), "config": "{}"}
    out = hb._row_to_run(row, None)
    assert out["coverage"]["bars_used"] == 1850
    assert out["results"]["coverage"]["completeness_pct"] == pytest.approx(68.316)
    assert out["results"]["coverage"]["missing_periods"][0]["missing_bars"] == 14
    assert out["verdict"] and "4 trade" in out["verdict"]


def test_08_a_run_without_a_coverage_blob_says_nothing_rather_than_guessing():
    row = {"run_id": "HRUN-TEST-NOCOV", "label": "MT5", "status": "COMPLETED", "strategy_id": 1,
           "strategy_identity": "{}", "symbol": "XAUUSD", "timeframe": "M15",
           "start_date": "2026-09-07", "end_date": "2026-10-05", "start_ts": 1.0, "end_ts": 2.0,
           "bars": 10, "data_scope": "MT5", "data_source": "MT5", "dataset_id": "ds",
           "dataset_fingerprint": "fp", "created_at": 1.0, "started_at": 1.0, "finished_at": 2.0,
           "runtime_ms": 10, "trade_count": 0, "equity_points": 0, "error": None, "notes": None,
           "diagnostic_legacy": 0, "research_eligible": 1, "window": None, "stage": "mt5_hist",
           "provenance": "{}", "artifacts": "{}", "requests": "{}",
           "metrics": json.dumps({"metrics": {"trades": 0}}), "config": "{}"}
    out = hb._row_to_run(row, None)
    assert out["coverage"] is None
    assert out["results"]["coverage"] is None
