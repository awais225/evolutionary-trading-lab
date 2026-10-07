"""V5 regression tests — status taxonomy, feature contract, feature specs,
timeframe scope and the §4 diagnostics reports.

The suite runs against ``EVOLUTIONARY_LAB_DATA_ROOT`` (the pytest copy of DATA),
so nothing here can touch the authoritative tree.
"""
from __future__ import annotations

import random
from pathlib import Path

import pytest

# a dataset that exists in the pytest DATA root
DATASET = "XAUUSD_M15_20250930_20260925_SIMULATOR"


# --------------------------------------------------------------------------- #
# 1. status taxonomy (spec §3)
# --------------------------------------------------------------------------- #
def test_classify_failure_separates_infrastructure_from_strategy():
    from app import status as st

    assert st.classify_failure("DATASET UNAVAILABLE: No eligible dataset for XAUUSD_H1") == st.DATA_UNAVAILABLE
    assert st.classify_failure("TRAIN_WINDOW_FAILED: insufficient bars in window") == st.DATA_UNAVAILABLE
    assert st.classify_failure("Failed to load dataset artifact from disk: X (not a parquet)") == st.DATA_CORRUPT
    assert st.classify_failure("parquet magic bytes not found in footer") == st.DATA_CORRUPT
    assert st.classify_failure("Evaluation worker error") == st.BACKTEST_ERROR
    assert st.classify_failure("REJECTED: profit factor 0.52 < 1.05; sharpe -0.4") == st.STRATEGY_FAILED
    assert st.classify_failure("REJECTED: insufficient trades (0 < 2)") == st.STRATEGY_FAILED
    assert st.classify_failure("Sub-threshold backtest performance (PF=1.02)") == st.STRATEGY_FAILED
    # nothing recognisable: never invented
    assert st.classify_failure("") is None
    assert st.classify_failure(None) is None


def test_v5_status_never_reports_a_data_failure_as_a_strategy_failure():
    from app import status as st

    row = {"status": "FAILED", "creation_reason": "DATASET UNAVAILABLE: No eligible dataset for XAUUSD_M1",
           "failure_reason": None}
    assert st.v5_status(row) == st.DATA_UNAVAILABLE
    assert st.is_alive(row) is False
    assert st.is_infrastructure(row) is True
    payload = st.status_payload(row)
    assert payload["v5_status"] == "DATA_UNAVAILABLE"
    assert payload["is_infrastructure_failure"] is True
    assert payload["v5_status_label"] == "Data unavailable"

    survived = {"status": "SURVIVED", "survival_reason": "SURVIVED: Win Rate 42%, PF 1.4"}
    assert st.v5_status(survived) == st.VALID
    assert st.is_alive(survived) is True


def test_status_taxonomy_endpoint(client):
    res = client.get("/api/status/taxonomy")
    assert res.status_code == 200
    body = res.json()
    assert "STRATEGY_FAILED" in body["statuses"]
    assert "DATA_UNAVAILABLE" in body["statuses"]
    assert set(body["infrastructure_statuses"]) == {"DATA_UNAVAILABLE", "DATA_CORRUPT", "BACKTEST_ERROR"}


# --------------------------------------------------------------------------- #
# 2. feature contract (present / absent / corrupt)
# --------------------------------------------------------------------------- #
def test_feature_artifact_status_is_three_way_and_never_blocks(monkeypatch):
    monkeypatch.setenv("LAB_NO_FEATURE_PERSIST", "1")
    from app.data.engine import get_data_engine

    de = get_data_engine()
    state, reason, path = de.feature_artifact_status(DATASET)
    assert state in ("present", "absent", "corrupt"), (state, reason)
    ok, why = de.is_dataset_eligible_for_research(DATASET, require_features=True)
    assert ok is True, why
    assert why == "ELIGIBLE"
    if state == "absent":
        assert path is None
    else:
        assert path is not None and Path(path).exists()


def test_missing_feature_cache_is_not_a_dataset_failure(monkeypatch):
    """An absent derived artifact must not make the dataset ineligible (root cause A)."""
    monkeypatch.setenv("LAB_NO_FEATURE_PERSIST", "1")
    from app.data.engine import get_data_engine

    de = get_data_engine()
    monkeypatch.setattr(de, "feature_artifact_status", lambda *a, **k: ("absent", "no cache", None))
    ok, why = de.is_dataset_eligible_for_research(DATASET, require_features=True)
    assert ok is True, why


def test_feature_gate_resolves_the_writers_filename(monkeypatch, tmp_path):
    """The gate must find the artifact the writer actually writes."""
    monkeypatch.setenv("LAB_NO_FEATURE_PERSIST", "1")
    from app.data.engine import get_data_engine
    from app.features.engine import DEFAULT_FEATURE_SET_ID, DEFAULT_SCHEMA_VERSION
    import app.paths as P
    import pandas as pd

    canonical = P.FEATURES_DIR / f"{DATASET}__{DEFAULT_FEATURE_SET_ID}__{DEFAULT_SCHEMA_VERSION}.parquet"
    assert canonical.name == f"{DATASET}__core_v1__core_v1.parquet"
    created = not canonical.exists()
    if created:
        pd.DataFrame({"a": [1.0, 2.0, 3.0]}).to_parquet(canonical, index=False)
    try:
        state, reason, path = get_data_engine().feature_artifact_status(DATASET)
        assert path is not None and Path(path).name == canonical.name, (state, reason, path)
    finally:
        if created:
            canonical.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# 3. feature specs (root cause of the remaining "feature error" nodes)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("spec", [
    "close", "open", "low", "price", "atr:10", "atr:14", "ema:14",
    "bb_pctb:20:2.5", "bb_pctb:50:2.0", "bb_width:20:2.0",
    "relvol:50", "range_expansion:50", "rolling_vol:20",
    "macd_hist:8:21:5", "macd_hist:12:26:9", "stoch_k:9:3", "stoch_d:9:3",
    "regime:trending", "regime:momentum", "session_london", "hour",
    "momentum:5", "adx:10", "rsi:14",
])
def test_derived_feature_specs_with_custom_parameters_compute(spec, monkeypatch):
    """A derived column with non-default parameters must compute (was KeyError)."""
    monkeypatch.setenv("LAB_NO_FEATURE_PERSIST", "1")
    import numpy as np
    from app.features.engine import get_feature_engine

    arr = get_feature_engine().get(DATASET, spec)
    assert len(arr) > 0
    assert np.isfinite(arr).sum() > 0, f"{spec} produced no finite values"


def test_family_spec_rebuild_preserves_parameters():
    from app.features.engine import FeatureEngine

    f = FeatureEngine._family_spec_for
    assert f("bb_pctb:20:2.5", "bb") == "bb:20:2.5"
    assert f("relvol:50", "volume") == "volume:50"
    assert f("range_expansion:50", "volatility") == "volatility:50"
    assert f("macd_hist:8:21:5", "macd") == "macd:8:21:5"
    assert f("stoch_k:9:3", "stoch") == "stoch:9:3"
    assert f("close", "price") == "price"
    assert f("ema:14", "ema") == "ema:14"


# --------------------------------------------------------------------------- #
# 4. timeframe scope (root cause B — 5,960 nodes born on timeframes with no data)
# --------------------------------------------------------------------------- #
def test_timeframe_scope_restricts_generation():
    from app.genome import ops

    try:
        ops.set_timeframe_scope(["M15"])
        assert ops.timeframe_scope() == ["M15"]
        rng = random.Random(1234)
        for _ in range(25):
            g = ops.random_genome("XAUUSD", rng)
            assert g["timeframe"] == "M15", g["timeframe"]
    finally:
        ops.set_timeframe_scope(None)
    # reset restores the full universe (the evolution engine narrows it per symbol)
    assert ops.timeframe_scope() == ops.TF_LIST


def test_timeframe_scope_is_applied_by_the_evolution_engine():
    """Both generation entry points must pull untestable timeframes back in."""
    import inspect

    from app.genome import ops
    from app.evolution import engine as ev

    src = inspect.getsource(ev.EvolutionEngine)
    seed_src = inspect.getsource(ev.EvolutionEngine.seed_population)
    repro_src = inspect.getsource(ev.EvolutionEngine.reproduce)
    assert "_apply_timeframe_scope" in src
    assert "_apply_timeframe_scope" in seed_src
    assert "_apply_timeframe_scope" in repro_src

    try:
        ops.set_timeframe_scope(["M30"])
        assert callable(ev.EvolutionEngine._apply_timeframe_scope)
    finally:
        ops.set_timeframe_scope(None)


# --------------------------------------------------------------------------- #
# 5. diagnostics reports (spec §4) — shape + honesty + read-only
# --------------------------------------------------------------------------- #
def test_diagnostics_reports_have_consistent_counts(client):
    res = client.get("/api/diagnostics/all")
    assert res.status_code == 200
    body = res.json()

    ds_rows = client.get("/api/data/datasets").json()
    n_datasets = len(ds_rows.get("datasets", ds_rows) if isinstance(ds_rows, dict) else ds_rows)

    data = body["data"]
    assert data["dataset_rows"] == n_datasets
    assert data["reported"] == min(n_datasets, 400)
    assert sum(data["by_usability"].values()) == data["reported"]
    for d in data["datasets"]:
        assert d["usability"] in ("VALID", "UNAVAILABLE", "CORRUPT")

    elig = body["eligibility"]
    assert elig["timeframes"], "no timeframes reported"
    for tf in elig["timeframes"]:
        assert tf["candidates"] >= 0
        assert tf["testable"] is (tf["resolved_dataset"] is not None)

    bt = body["backtest"]
    c = bt["counts"]
    assert c["requested"] == c["generated"] >= c["tested"]
    # partition: every node is either tested or never tested
    assert c["tested"] + c["never_tested"] == c["requested"]
    assert bt["reconciliation"]["tested_plus_never_tested_equals_requested"] is True
    # nodes skipped for missing data were never judged on performance
    assert c["skipped"] <= c["never_tested"] + c["rejected"]
    assert c["user_research"] + c["legacy_test"] == c["requested"]

    ev = body["evolution"]
    assert ev["counts"]["parent_candidates"] >= 0
    assert isinstance(ev["generations"], list)


def test_diagnostics_do_not_write_to_the_data_tree(client):
    """§26: diagnostics must be read-only — DATA stays byte-identical."""
    import app.paths as P

    def tree_state():
        out = {}
        for p in sorted(Path(P.DATA_ROOT).rglob("*")):
            if p.is_file() and "-wal" not in p.name and "-shm" not in p.name:
                st = p.stat()
                out[str(p.relative_to(P.DATA_ROOT))] = (st.st_size, int(st.st_mtime))
        return out

    before = tree_state()
    assert client.get("/api/diagnostics/all").status_code == 200
    after = tree_state()
    assert before == after, "diagnostics modified the DATA tree"


def test_dataset_diagnostics_expose_missing_period_detection(client):
    body = client.get("/api/diagnostics/data?include_rows=20").json()
    assert body["datasets"]
    some = [d for d in body["datasets"] if d.get("gaps")]
    assert some, "no dataset carried gap diagnostics"
    g = some[0]["gaps"]
    assert "expected_interval_minutes" in g
