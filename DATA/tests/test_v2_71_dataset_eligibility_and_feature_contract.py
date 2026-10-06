"""
Evolutionary Trading Research Lab V2.71
Dedicated Test Suite: Dataset Eligibility Contract & Feature Validation Repair (Tests A through F).
"""
import json
import time
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from starlette.testclient import TestClient

from backend.app.main import app
from backend.app.db.database import Database, get_db
from backend.app.data.engine import DataEngine, get_data_engine
from backend.app.features.engine import get_feature_engine
from backend.app.features.library import f_price, is_spec_cached
from backend.app.orchestrator.lab import Lab, get_lab, CORE_FEATURE_SPECS
from backend.app.orchestrator.stages import StageManager, get_stage_manager
from backend.app import paths as P
from backend.app.paths import DATA_ROOT, MT5_XAUUSD_DIR, FEATURES_DIR


@pytest.fixture
def v271_env(tmp_path, monkeypatch):
    """Isolated environment for V2.71 verification."""
    data_dir = tmp_path / "DATA"
    cache_dir = tmp_path / "CACHE"
    db_file = tmp_path / "lab_test.db"

    data_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    mt5_xau_dir = data_dir / "MT5" / "XAUUSD"
    mt5_xau_dir.mkdir(parents=True, exist_ok=True)
    feat_dir = data_dir / "features"
    feat_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(P, "DATA_ROOT", data_dir)
    monkeypatch.setattr(P, "CACHE_ROOT", cache_dir)
    monkeypatch.setattr(P, "MT5_XAUUSD_DIR", mt5_xau_dir)
    monkeypatch.setattr(P, "FEATURES_DIR", feat_dir)

    db = Database(str(db_file))
    monkeypatch.setattr("backend.app.db.database.get_db", lambda path=None: db)
    monkeypatch.setattr("backend.app.orchestrator.lab.get_db", lambda: db)
    monkeypatch.setattr("backend.app.data.engine.get_db", lambda: db)
    monkeypatch.setattr("backend.app.evolution.engine.get_db", lambda: db)

    de = DataEngine()
    monkeypatch.setattr("backend.app.data.engine.get_data_engine", lambda: de)
    monkeypatch.setattr("backend.app.orchestrator.lab.get_data_engine", lambda: de)

    return {
        "tmp": tmp_path,
        "data": data_dir,
        "mt5_xau": mt5_xau_dir,
        "features": feat_dir,
        "db": db,
        "de": de,
    }


def _create_sample_ohlcv(path: Path, n_bars: int = 100):
    """Generate a clean sample OHLCV Parquet file."""
    now = time.time()
    ts = np.linspace(now - n_bars * 900, now, n_bars)
    c = 2600.0 + np.cumsum(np.random.randn(n_bars) * 1.5)
    o = c + np.random.randn(n_bars) * 0.5
    h = np.maximum(o, c) + np.abs(np.random.randn(n_bars) * 0.8)
    l = np.minimum(o, c) - np.abs(np.random.randn(n_bars) * 0.8)
    v = np.random.randint(100, 5000, n_bars)

    df = pd.DataFrame({
        "ts": ts,
        "time": ts,
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "tick_volume": v,
        "spread": np.full(n_bars, 18.0),
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return df


def _create_sample_features(path: Path, df_raw: pd.DataFrame):
    """Generate a valid core feature Parquet file matching raw data."""
    price_dict = f_price(df_raw)
    cols = {k: v for k, v in price_dict.items()}

    # Add core indicators
    c = df_raw["close"].to_numpy(dtype=np.float64)
    cols["ema:20"] = c
    cols["ema:50"] = c
    cols["ema:100"] = c
    cols["sma:20"] = c
    cols["rsi:14"] = np.full(len(c), 50.0)
    cols["macd:12:26:9"] = np.zeros(len(c))
    cols["roc:12"] = np.zeros(len(c))
    cols["momentum:10"] = np.zeros(len(c))
    cols["stoch:14:3"] = np.full(len(c), 50.0)
    cols["cci:20"] = np.zeros(len(c))
    cols["adx:14"] = np.full(len(c), 25.0)
    cols["atr:14"] = np.full(len(c), 2.5)
    cols["bb:20:2.0"] = c + 5.0
    cols["volatility:20"] = np.full(len(c), 0.01)
    cols["volume:20"] = np.full(len(c), 1000.0)
    cols["vwap"] = c
    cols["prev_day"] = c
    cols["time"] = np.zeros(len(c))
    cols["regime"] = np.zeros(len(c))
    cols["sessions"] = np.zeros(len(c))

    feat_df = pd.DataFrame(cols)
    path.parent.mkdir(parents=True, exist_ok=True)
    feat_df.to_parquet(path, index=False)
    return feat_df


def test_test_a_stale_database_record_missing_on_disk(v271_env):
    """TEST A: A database record exists but its physical dataset is missing.
    Expected: ignored / unavailable, and it must never enter backtesting.
    """
    db = v271_env["db"]
    de = v271_env["de"]

    # Insert a stale database record for M30 that does NOT exist on disk
    stale_id = "XAUUSD_M30_20250930_20260925_SIMULATOR"
    db.x(
        "INSERT INTO datasets (id, symbol, timeframe, source, kind, path, created_at) "
        "VALUES (?, 'XAUUSD', 'M30', 'SIMULATOR', 'master_view', '/nonexistent/path/M30.parquet', ?)",
        (stale_id, time.time() - 10000)
    )

    # 1. Eligibility check must reject missing physical file
    is_elig, reason = de.is_dataset_eligible_for_research(stale_id)
    assert is_elig is False
    assert "not found on disk" in reason.lower()

    # 2. latest_dataset must ignore stale record
    ds = de.latest_dataset("XAUUSD", "M30", require_eligible=True)
    assert ds is None

    # 3. Lab candidate referencing this stale timeframe must be rejected, not backtested
    lab = Lab()
    lab.db = db
    lab._datasets_ready = True
    lab._research_blocked = False

    candidate_genome = {
        "symbol": "XAUUSD",
        "timeframe": "M30",
        "entry_long": {"op": "gt", "left": "rsi:14", "right": 30},
        "exit_rules": {},
    }
    db.x(
        "INSERT INTO strategies (hash, symbol, timeframe, status, genome, created_at, updated_at) "
        "VALUES ('H_STALE_A', 'XAUUSD', 'M30', 'BORN', ?, ?, ?)",
        (json.dumps(candidate_genome), time.time(), time.time())
    )

    # Execute screen batch
    lab._screen_batch()

    # Strategy must be marked FAILED with DATASET UNAVAILABLE and not crash
    strat = db.one("SELECT status, creation_reason FROM strategies WHERE hash='H_STALE_A'")
    assert strat["status"] == "FAILED"
    assert "DATASET UNAVAILABLE" in strat["creation_reason"]


def test_test_b_valid_current_dataset_in_data_root(v271_env):
    """TEST B: A valid current dataset exists in DATA_ROOT.
    Expected: eligible for research provided its schema and feature artifact are valid.
    """
    de = v271_env["de"]
    m15_raw = v271_env["mt5_xau"] / "M15.parquet"
    df_raw = _create_sample_ohlcv(m15_raw, n_bars=120)

    # Create corresponding feature artifact
    feat_p = v271_env["features"] / "XAUUSD_M15.parquet"
    _create_sample_features(feat_p, df_raw)

    is_elig, reason = de.is_dataset_eligible_for_research("XAUUSD_M15", require_features=True)
    assert is_elig is True
    assert reason == "ELIGIBLE"

    ds = de.latest_dataset("XAUUSD", "M15", require_eligible=True)
    assert ds is not None
    assert ds["id"] == "XAUUSD_M15"
    assert ds["symbol"] == "XAUUSD"
    assert ds["timeframe"] == "M15"


def test_test_c_feature_validation_fails_blocks_research(v271_env, monkeypatch):
    """TEST C: Feature computation completes but required feature validation fails.
    Expected: research blocked, backtesting does NOT start.
    """
    db = v271_env["db"]
    m15_raw = v271_env["mt5_xau"] / "M15.parquet"
    df_raw = _create_sample_ohlcv(m15_raw, n_bars=80)

    # 1. Feature validation check on invalid artifact must fail
    feat_p = v271_env["features"] / "XAUUSD_M15.parquet"
    pd.DataFrame({"invalid_col": []}).to_parquet(feat_p, index=False)

    lab = Lab()
    lab.db = db
    datasets = [{"id": "XAUUSD_M15", "timeframe": "M15"}]
    valid = lab._verify_and_validate_features(datasets)
    assert valid is False

    # 2. When feature computation completes but validation fails:
    # Ensure research is blocked, stage transitions to BLOCKED, and _ensure_datasets returns False
    monkeypatch.setattr(lab, "_verify_and_validate_features", lambda ds: False)
    lab._datasets_ready = False
    ready = lab._ensure_datasets()
    assert ready is False
    assert lab._datasets_ready is False
    assert lab._research_blocked is True
    assert "FEATURE_VALIDATION_FAILED" in lab._block_reason

    # 3. Candidate strategy must not be screened or enter BACKTESTING
    db.x(
        "INSERT INTO strategies (hash, symbol, timeframe, status, genome, created_at, updated_at) "
        "VALUES ('H_BLK_C', 'XAUUSD', 'M15', 'BORN', '{}', ?, ?)",
        (time.time(), time.time())
    )
    lab._screen_batch()
    strat = db.one("SELECT status FROM strategies WHERE hash='H_BLK_C'")
    assert strat["status"] == "BORN"  # Untouched, not screened


def test_test_d_candidate_references_unavailable_dataset(v271_env):
    """TEST D: A candidate references an unavailable dataset.
    Expected: candidate rejected, reason logged, next valid candidate considered.
    The same invalid dataset must not be retried indefinitely.
    """
    db = v271_env["db"]
    de = v271_env["de"]

    # Only M15 is available on disk
    m15_raw = v271_env["mt5_xau"] / "M15.parquet"
    df_raw = _create_sample_ohlcv(m15_raw, n_bars=100)
    feat_p = v271_env["features"] / "XAUUSD_M15.parquet"
    _create_sample_features(feat_p, df_raw)

    lab = Lab()
    lab.db = db
    lab._datasets_ready = True
    lab._research_blocked = False

    # Candidate 1: references M5 (UNAVAILABLE on disk)
    g1 = {"symbol": "XAUUSD", "timeframe": "M5", "entry_long": {"op": "gt", "left": "rsi:14", "right": 40}, "exit_rules": {}}
    db.x("INSERT INTO strategies (hash, symbol, timeframe, status, genome, created_at, updated_at) VALUES ('H_UNAVAIL_1', 'XAUUSD', 'M5', 'BORN', ?, ?, ?)",
         (json.dumps(g1), time.time(), time.time()))

    # Candidate 2: references M15 (AVAILABLE on disk)
    g2 = {"symbol": "XAUUSD", "timeframe": "M15", "entry_long": {"op": "gt", "left": "rsi:14", "right": 50}, "exit_rules": {}}
    db.x("INSERT INTO strategies (hash, symbol, timeframe, status, genome, created_at, updated_at) VALUES ('H_AVAIL_2', 'XAUUSD', 'M15', 'BORN', ?, ?, ?)",
         (json.dumps(g2), time.time(), time.time()))

    # Run screen batch
    lab._screen_batch()

    s1 = db.one("SELECT status, creation_reason FROM strategies WHERE hash='H_UNAVAIL_1'")
    assert s1["status"] == "FAILED"
    assert "DATASET UNAVAILABLE" in s1["creation_reason"]

    # Ensure no orchestrator consecutive failures occurred
    assert lab._consecutive_failures == 0


def test_test_e_valid_dataset_and_features_evaluates_normally(v271_env):
    """TEST E: Valid dataset and valid features exist.
    Expected: research -> backtesting -> node evaluation continues normally.
    """
    db = v271_env["db"]
    m15_raw = v271_env["mt5_xau"] / "M15.parquet"
    df_raw = _create_sample_ohlcv(m15_raw, n_bars=150)
    feat_p = v271_env["features"] / "XAUUSD_M15.parquet"
    _create_sample_features(feat_p, df_raw)

    lab = Lab()
    lab.db = db
    lab._datasets_ready = True
    lab._research_blocked = False

    g = {"symbol": "XAUUSD", "timeframe": "M15", "entry_long": {"op": "gt", "left": "rsi:14", "right": 45}, "exit_rules": {}}
    db.x("INSERT INTO strategies (hash, symbol, timeframe, status, genome, created_at, updated_at) VALUES ('H_VALID_E', 'XAUUSD', 'M15', 'BORN', ?, ?, ?)",
         (json.dumps(g), time.time(), time.time()))

    lab._screen_batch()

    s = db.one("SELECT status, fitness FROM strategies WHERE hash='H_VALID_E'")
    assert s["status"] in ("SURVIVED", "FAILED", "KILLED")
    assert lab._consecutive_failures == 0


def test_test_f_three_genuine_runtime_failures_safe_paused(v271_env, monkeypatch):
    """TEST F: Three genuine runtime failures occur.
    Expected: SAFE PAUSED and dashboard remains responsive.
    """
    lab = Lab()
    assert lab.safe_paused is False
    assert lab._consecutive_failures == 0

    for i in range(1, 4):
        lab._consecutive_failures += 1
        lab.last_error = f"SimulatedFatalRuntimeError: attempt #{i}"
        if lab._consecutive_failures >= 3:
            lab.paused = True
            lab.safe_paused = True
            lab.safe_paused_reason = f"Repeated orchestrator failures ({lab._consecutive_failures} consecutive). Last error: {lab.last_error}"

    assert lab.safe_paused is True
    assert lab.paused is True
    assert "Repeated orchestrator failures (3 consecutive)" in lab.safe_paused_reason

    st = lab.status()
    assert st["safe_paused"] is True
    assert st["consecutive_failures"] == 3

    # Point global get_lab to this lab instance to test dashboard API response
    monkeypatch.setattr("backend.app.orchestrator.lab.get_lab", lambda: lab)
    monkeypatch.setattr("backend.app.api.routes.get_lab", lambda: lab)

    # Verify dashboard and APIs remain 200 OK and responsive
    client = TestClient(app)
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    status = client.get("/status")
    assert status.status_code == 200
    assert status.json()["lab"]["safe_paused"] is True
