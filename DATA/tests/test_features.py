"""Feature library tests: ranges, alignment, no lookahead, caching."""
from __future__ import annotations

import numpy as np
import pytest

from app.data.engine import get_data_engine
from app.features import library
from app.features.engine import get_feature_engine


@pytest.fixture(scope="module")
def df(lab_env):
    return get_data_engine().get_frame(lab_env["dataset_id"])


def test_rsi_range(df):
    rsi = library.f_rsi(df, 14)["rsi:14"]
    fin = rsi[np.isfinite(rsi)]
    assert fin.size > 0
    assert fin.min() >= 0 and fin.max() <= 100


def test_adx_range(df):
    out = library.f_adx(df, 14)
    adx = out["adx:14"]
    fin = adx[np.isfinite(adx)]
    assert fin.min() >= 0 and fin.max() <= 100
    assert np.all(np.isfinite(out["di_plus:14"][np.isfinite(out["di_plus:14"])]) )


def test_ema_sma_converge_on_constant():
    import pandas as pd
    flat = pd.DataFrame({"close": np.full(300, 100.0), "high": 100.0,
                         "low": 100.0, "open": 100.0})
    assert abs(library.f_ema(flat, 20)["ema:20"][-1] - 100.0) < 1e-9
    assert abs(library.f_sma(flat, 20)["sma:20"][-1] - 100.0) < 1e-9


def test_no_lookahead_prev_day(df):
    out = library.f_prev_day(df)
    ts = df["ts"].to_numpy()
    day = (ts // 86400).astype(np.int64)
    ph, pl = out["prev_day_high"], out["prev_day_low"]
    h, l = df["high"].to_numpy(), df["low"].to_numpy()
    # previous-day high must equal max of high strictly on the previous day
    checked = 0
    for i in range(len(df) - 1):
        if day[i] != day[i - 1] and day[i] > day.min() and np.isfinite(ph[i]):
            prev_mask = day == day[i] - 1
            if prev_mask.any():
                assert abs(ph[i] - h[prev_mask].max()) < 1e-9
                assert abs(pl[i] - l[prev_mask].min()) < 1e-9
                checked += 1
        if checked > 5:
            break
    assert checked > 0
    # prev-day values must be constant within the same day (no intraday peek)
    for i in range(1, len(df)):
        if day[i] == day[i - 1] and np.isfinite(ph[i]) and np.isfinite(ph[i - 1]):
            assert ph[i] == ph[i - 1]


def test_no_lookahead_breakout(df):
    out = library.f_price(df)
    bd = out["breakout_dist_high"]
    h = df["high"].to_numpy()
    ts = df["ts"].to_numpy()
    # breakout distance uses rolling(20).max().shift(1): strictly past bars only
    import pandas as pd
    expected = pd.Series(h).rolling(20, min_periods=20).max().shift(1).to_numpy()
    fin = np.isfinite(bd) & np.isfinite(expected)
    assert np.allclose(bd[fin], df["close"].to_numpy()[fin] - expected[fin], atol=1e-9)


def test_sessions_and_time(df):
    t = library.f_time(df)
    assert set(np.unique(t["session_london"])) <= {0.0, 1.0}
    assert set(np.unique(t["session_newyork"])) <= {0.0, 1.0}
    assert set(np.unique(t["session_asia"])) <= {0.0, 1.0}
    assert np.sum(t["session_london"]) > 0


def test_regime_binary(df):
    r = library.f_regime(df)
    for k in ("regime:trending", "regime:ranging", "regime:breakout"):
        assert set(np.unique(r[k])) <= {0.0, 1.0}


def test_feature_engine_caches(lab_env):
    fe = get_feature_engine()
    dsid = lab_env["dataset_id"]
    a1 = fe.get(dsid, "rsi:14")
    a2 = fe.get(dsid, "rsi:14")
    assert a1 is a2          # identical object => cache hit
    stats = fe.cache_stats()
    assert stats["entries"] > 0
