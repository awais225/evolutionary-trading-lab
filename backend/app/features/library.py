"""
Feature library — deterministic indicator/feature computation (numpy).

Every feature is addressed by a spec string:  name[:p1[:p2...]]
Examples:  ema:20   rsi:14   bb:20:2.0   macd:12:26:9   regime:trending
           session:london   prev_day_high   relvol:20

A spec may produce several named arrays (e.g. bb -> bb_upper/bb_lower/
bb_mid/bb_width). All arrays are float64, aligned to the dataset index,
with NaN where undefined (backtester treats NaN as False in conditions).

Architecture note: a learned regime classifier can later replace the
rule-based regime:* features — same spec interface, register a new
function in FEATURE_FUNCS.
"""
from __future__ import annotations

from typing import Callable, Dict

import numpy as np
import pandas as pd


# ---------------- primitives ----------------

def _sma(x: np.ndarray, n: int) -> np.ndarray:
    c = pd.Series(x)
    return c.rolling(n, min_periods=n).mean().to_numpy()


def _ema(x: np.ndarray, n: int) -> np.ndarray:
    c = pd.Series(x)
    return c.ewm(span=n, adjust=False, min_periods=n).mean().to_numpy()


def _std(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).rolling(n, min_periods=n).std().to_numpy()


def _shift(x: np.ndarray, n: int = 1) -> np.ndarray:
    out = np.full_like(x, np.nan, dtype=np.float64)
    if n > 0:
        out[n:] = x[:-n]
    elif n < 0:
        out[:n] = x[-n:]
    else:
        out[:] = x
    return out


def _rma(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean().to_numpy()


# ---------------- feature functions ----------------
# signature: fn(df) -> dict of arrays

def f_price(df: pd.DataFrame) -> Dict[str, np.ndarray]:
    o, h, l, c = (df[k].to_numpy(np.float64) for k in ("open", "high", "low", "close"))
    rng = np.where(h - l > 0, h - l, np.nan)
    body = c - o
    return {
        "price": c,
        "open": o, "high": h, "low": l, "close": c,
        "returns:1": np.where(_shift(c) != 0, c / _shift(c) - 1.0, np.nan),
        "returns:5": np.where(_shift(c, 5) != 0, c / _shift(c, 5) - 1.0, np.nan),
        "candle_body": body,
        "candle_body_pct": body / np.where(c > 0, c, np.nan),
        "upper_wick": h - np.maximum(o, c),
        "lower_wick": np.minimum(o, c) - l,
        "candle_range": h - l,
        "candle_range_atr": (h - l) / np.where(np.isnan(rng), np.nan, rng),  # ~1
        "prev_high": _shift(h), "prev_low": _shift(l),
        "breakout_dist_high": c - pd.Series(h).rolling(20, min_periods=20).max().shift(1).to_numpy(),
        "breakout_dist_low": pd.Series(l).rolling(20, min_periods=20).min().shift(1).to_numpy() - c,
    }


def f_sma(df: pd.DataFrame, n: int = 20) -> Dict[str, np.ndarray]:
    c = df["close"].to_numpy(np.float64)
    return {f"sma:{n}": _sma(c, n)}


def f_ema(df: pd.DataFrame, n: int = 20) -> Dict[str, np.ndarray]:
    c = df["close"].to_numpy(np.float64)
    return {f"ema:{n}": _ema(c, n)}


def f_rsi(df: pd.DataFrame, n: int = 14) -> Dict[str, np.ndarray]:
    c = df["close"].to_numpy(np.float64)
    d = np.diff(c, prepend=c[0])
    up = np.clip(d, 0, None)
    dn = np.clip(-d, 0, None)
    rs = _rma(up, n) / np.where(_rma(dn, n) == 0, 1e-12, _rma(dn, n))
    return {f"rsi:{n}": 100.0 - 100.0 / (1.0 + rs)}


def f_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, sig: int = 9):
    c = df["close"].to_numpy(np.float64)
    line = _ema(c, fast) - _ema(c, slow)
    signal = _ema(line, sig)
    return {f"macd:{fast}:{slow}:{sig}": line,
            f"macd_signal:{fast}:{slow}:{sig}": signal,
            f"macd_hist:{fast}:{slow}:{sig}": line - signal}


def f_roc(df: pd.DataFrame, n: int = 12) -> Dict[str, np.ndarray]:
    c = df["close"].to_numpy(np.float64)
    prev = _shift(c, n)
    return {f"roc:{n}": np.where(prev != 0, (c - prev) / prev * 100.0, np.nan)}


def f_momentum(df: pd.DataFrame, n: int = 10) -> Dict[str, np.ndarray]:
    c = df["close"].to_numpy(np.float64)
    return {f"momentum:{n}": c - _shift(c, n)}


def f_stoch(df: pd.DataFrame, n: int = 14, smooth: int = 3):
    h = pd.Series(df["high"].to_numpy(np.float64)).rolling(n, min_periods=n).max()
    l = pd.Series(df["low"].to_numpy(np.float64)).rolling(n, min_periods=n).min()
    c = df["close"].to_numpy(np.float64)
    k = 100.0 * (c - l.to_numpy()) / np.where((h - l).to_numpy() == 0, np.nan, (h - l).to_numpy())
    k = pd.Series(k).rolling(smooth, min_periods=smooth).mean().to_numpy()
    d = _sma(k, smooth)
    return {f"stoch_k:{n}:{smooth}": k, f"stoch_d:{n}:{smooth}": d}


def f_cci(df: pd.DataFrame, n: int = 20) -> Dict[str, np.ndarray]:
    tp = (df["high"] + df["low"] + df["close"]).to_numpy(np.float64) / 3.0
    ma = _sma(tp, n)
    md = _sma(np.abs(tp - ma), n)
    return {f"cci:{n}": (tp - ma) / np.where(0.015 * md == 0, np.nan, 0.015 * md)}


def f_adx(df: pd.DataFrame, n: int = 14):
    h, l, c = (df[k].to_numpy(np.float64) for k in ("high", "low", "close"))
    up = np.diff(h, prepend=h[0])
    dn = -np.diff(l, prepend=l[0])
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = np.maximum.reduce([h - l, np.abs(h - _shift(c)), np.abs(l - _shift(c))])
    atr = _rma(tr, n)
    pdi = 100 * _rma(plus_dm, n) / np.where(atr == 0, np.nan, atr)
    mdi = 100 * _rma(minus_dm, n) / np.where(atr == 0, np.nan, atr)
    dx = 100 * np.abs(pdi - mdi) / np.where(pdi + mdi == 0, np.nan, pdi + mdi)
    return {f"adx:{n}": _rma(dx, n), f"di_plus:{n}": pdi, f"di_minus:{n}": mdi}


def f_atr(df: pd.DataFrame, n: int = 14) -> Dict[str, np.ndarray]:
    h, l, c = (df[k].to_numpy(np.float64) for k in ("high", "low", "close"))
    tr = np.maximum.reduce([h - l, np.abs(h - _shift(c)), np.abs(l - _shift(c))])
    atr = _rma(tr, n)
    return {f"atr:{n}": atr, f"atr_pct:{n}": 100.0 * atr / np.where(c == 0, np.nan, c)}


def f_bb(df: pd.DataFrame, n: int = 20, k: float = 2.0):
    c = df["close"].to_numpy(np.float64)
    mid = _sma(c, n)
    sd = _std(c, n)
    up, lo = mid + k * sd, mid - k * sd
    width = (up - lo) / np.where(mid == 0, np.nan, mid)
    pctb = (c - lo) / np.where(up - lo == 0, np.nan, up - lo)
    return {f"bb_upper:{n}:{k}": up, f"bb_lower:{n}:{k}": lo, f"bb_mid:{n}:{k}": mid,
            f"bb_width:{n}:{k}": width, f"bb_pctb:{n}:{k}": pctb}


def f_volatility(df: pd.DataFrame, n: int = 20):
    c = df["close"].to_numpy(np.float64)
    r = np.where(_shift(c) != 0, np.log(c / _shift(c)), np.nan)
    vol = _std(r, n) * 100.0
    rng = df["high"].to_numpy(np.float64) - df["low"].to_numpy(np.float64)
    rng_ma = _sma(rng, n)
    rng_ma_slow = _sma(rng, n * 3)
    return {f"rolling_vol:{n}": vol,
            f"range_expansion:{n}": rng_ma / np.where(rng_ma_slow == 0, np.nan, rng_ma_slow)}


def f_volume(df: pd.DataFrame, n: int = 20):
    v = df["tick_volume"].to_numpy(np.float64)
    ma = _sma(v, n)
    return {"tick_volume": v, f"volume_ma:{n}": ma,
            f"relvol:{n}": v / np.where(ma == 0, np.nan, ma)}


def f_vwap(df: pd.DataFrame) -> Dict[str, np.ndarray]:
    tp = (df["high"] + df["low"] + df["close"]).to_numpy(np.float64) / 3.0
    v = df["tick_volume"].to_numpy(np.float64)
    day = (df["ts"].to_numpy() // 86400).astype(np.int64)
    cum_tpv = pd.Series(tp * v).groupby(day).cumsum().to_numpy()
    cum_v = pd.Series(v).groupby(day).cumsum().to_numpy()
    vwap = cum_tpv / np.where(cum_v == 0, np.nan, cum_v)
    c = df["close"].to_numpy(np.float64)
    return {"vwap": vwap, "vwap_dist": (c - vwap) / np.where(vwap == 0, np.nan, vwap) * 100.0}


def f_prev_day(df: pd.DataFrame):
    h, l = df["high"].to_numpy(np.float64), df["low"].to_numpy(np.float64)
    c = df["close"].to_numpy(np.float64)
    day = (df["ts"].to_numpy() // 86400).astype(np.int64)
    dh = pd.Series(h).groupby(day).transform("max").to_numpy()
    dl = pd.Series(l).groupby(day).transform("min").to_numpy()
    # previous trading day's high/low, vectorized via day-level agg + shift
    day_hi = pd.Series(h).groupby(day).max().sort_index()
    day_lo = pd.Series(l).groupby(day).min().sort_index()
    prev_hi_map = day_hi.shift(1)
    prev_lo_map = day_lo.shift(1)
    prev_h = pd.Series(day).map(prev_hi_map).to_numpy(np.float64)
    prev_l = pd.Series(day).map(prev_lo_map).to_numpy(np.float64)
    prev_rng = prev_h - prev_l
    return {"prev_day_high": prev_h, "prev_day_low": prev_l, "prev_day_range": prev_rng,
            "day_high": dh, "day_low": dl, "session_range": dh - dl,
            "break_prev_day_high": (c > prev_h).astype(np.float64),
            "break_prev_day_low": (c < prev_l).astype(np.float64)}


def f_time(df: pd.DataFrame) -> Dict[str, np.ndarray]:
    hour = df["hour"].to_numpy(np.float64)
    session = df["session"].astype(str).to_numpy()
    lon = (session == "london").astype(np.float64)
    ny = (session == "newyork").astype(np.float64)
    asia = (session == "asia").astype(np.float64)
    overlap = (((hour >= 12) & (hour < 16))).astype(np.float64)  # London/NY overlap UTC 12-16
    return {"hour": hour, "minute": df["minute"].to_numpy(np.float64),
            "dow": df["dow"].to_numpy(np.float64), "dom": df["dom"].to_numpy(np.float64),
            "session_london": lon, "session_newyork": ny, "session_asia": asia,
            "session_london_ny_overlap": overlap}


def f_regime(df: pd.DataFrame) -> Dict[str, np.ndarray]:
    """Rule-based market regime features (learned classifier can replace later)."""
    c = df["close"].to_numpy(np.float64)
    ema_f, ema_s = _ema(c, 20), _ema(c, 50)
    adx = f_adx(df, 14)[f"adx:14"]
    atr = f_atr(df, 14)[f"atr:14"]
    atr_slow = _sma(atr, 100)
    r = np.where(_shift(c) != 0, np.log(c / _shift(c)), np.nan)
    vol20 = _std(r, 20)
    vol100 = _std(r, 100)
    bbw = f_bb(df, 20, 2.0)[f"bb_width:20:2.0"]
    bbw_pct = pd.Series(bbw).rolling(400, min_periods=100).rank(pct=True).to_numpy()

    trending = ((adx > 25) & (np.abs(ema_f - ema_s) / np.where(c == 0, np.nan, c) > 0.0015)).astype(np.float64)
    ranging = ((adx < 20)).astype(np.float64)
    high_vol = (vol20 > vol100 * 1.25).astype(np.float64)
    low_vol = (vol20 < vol100 * 0.75).astype(np.float64)
    expansion = (atr > atr_slow * 1.2).astype(np.float64)
    compression = (atr < atr_slow * 0.8).astype(np.float64)
    hh = pd.Series(df["high"].to_numpy(np.float64)).rolling(20, min_periods=20).max().shift(1).to_numpy()
    ll = pd.Series(df["low"].to_numpy(np.float64)).rolling(20, min_periods=20).min().shift(1).to_numpy()
    breakout = ((c > hh) | (c < ll)).astype(np.float64)
    mom = (np.abs(_ema(np.where(np.isnan(r), 0, r), 10)) > _std(r, 50) * 0.35).astype(np.float64)

    return {"regime:trending": trending, "regime:ranging": ranging,
            "regime:high_volatility": high_vol, "regime:low_volatility": low_vol,
            "regime:expansion": expansion, "regime:compression": compression,
            "regime:breakout": breakout, "regime:momentum": mom,
            "regime_bbw_pctile": bbw_pct}


def f_sessions(df: pd.DataFrame) -> Dict[str, np.ndarray]:
    session = df["session"].astype(str).to_numpy()
    return {"session:london": (session == "london").astype(np.float64),
            "session:newyork": (session == "newyork").astype(np.float64),
            "session:asia": (session == "asia").astype(np.float64),
            "session:london_ny_overlap": (((df["hour"].to_numpy() >= 12) &
                                            (df["hour"].to_numpy() < 16))).astype(np.float64)}


# ---------------- registry ----------------
# spec-name -> (function, n_params, defaults)
FEATURE_FUNCS: Dict[str, tuple] = {
    "price": (f_price, 0, []),
    "sma": (f_sma, 1, [20]),
    "ema": (f_ema, 1, [20]),
    "rsi": (f_rsi, 1, [14]),
    "macd": (f_macd, 3, [12, 26, 9]),
    "roc": (f_roc, 1, [12]),
    "momentum": (f_momentum, 1, [10]),
    "stoch": (f_stoch, 2, [14, 3]),
    "cci": (f_cci, 1, [20]),
    "adx": (f_adx, 1, [14]),
    "atr": (f_atr, 1, [14]),
    "bb": (f_bb, 2, [20, 2.0]),
    "volatility": (f_volatility, 1, [20]),
    "volume": (f_volume, 1, [20]),
    "vwap": (f_vwap, 0, []),
    "prev_day": (f_prev_day, 0, []),
    "time": (f_time, 0, []),
    "regime": (f_regime, 0, []),
    "sessions": (f_sessions, 0, []),
}

# Feature families a genome may reference as "primary indicators"
PRIMARY_FAMILIES = ["sma", "ema", "rsi", "macd", "roc", "momentum", "stoch",
                    "cci", "adx", "atr", "bb", "volatility", "volume", "vwap",
                    "prev_day", "regime"]


def parse_spec(spec: str):
    parts = spec.split(":")
    name = parts[0]
    if name not in FEATURE_FUNCS:
        raise ValueError(f"unknown feature spec: {spec}")
    fn, n_params, defaults = FEATURE_FUNCS[name]
    args = []
    for i in range(n_params):
        if i + 1 < len(parts):
            v = parts[i + 1]
            args.append(float(v) if ("." in v) else int(v))
        else:
            args.append(defaults[i])
    return fn, args


def feature_family(spec: str) -> str:
    """Family of an output feature name (e.g. 'rsi:14' -> 'rsi')."""
    base = spec.split(":")[0]
    aliases = {
        # adx family
        "di_plus": "adx", "di_minus": "adx",
        # macd family
        "macd_signal": "macd", "macd_hist": "macd",
        # stoch
        "stoch_k": "stoch", "stoch_d": "stoch",
        # bollinger
        "bb_upper": "bb", "bb_lower": "bb", "bb_mid": "bb",
        "bb_width": "bb", "bb_pctb": "bb",
        # volatility
        "rolling_vol": "volatility", "range_expansion": "volatility",
        # volume
        "volume_ma": "volume", "relvol": "volume", "tick_volume": "volume",
        # atr
        "atr_pct": "atr",
        # price
        "returns": "price", "open": "price", "high": "price", "low": "price",
        "close": "price", "prev_high": "price", "prev_low": "price",
        "candle_body": "price", "candle_body_pct": "price", "upper_wick": "price",
        "lower_wick": "price", "candle_range": "price", "candle_range_atr": "price",
        "breakout_dist_high": "price", "breakout_dist_low": "price",
        # vwap
        "vwap_dist": "vwap",
        # prev_day / structure
        "prev_day_high": "prev_day", "prev_day_low": "prev_day",
        "prev_day_range": "prev_day", "day_high": "prev_day", "day_low": "prev_day",
        "session_range": "prev_day", "break_prev_day_high": "prev_day",
        "break_prev_day_low": "prev_day",
        # time
        "hour": "time", "minute": "time", "dow": "time", "dom": "time",
        "session_london": "time", "session_newyork": "time", "session_asia": "time",
        "session_london_ny_overlap": "time",
        # regime
        "regime_bbw_pctile": "regime",
    }
    return aliases.get(base, base)


def spec_primary_column(spec: str) -> str:
    """Return the primary column name produced by a generator spec."""
    mapping = {
        "price": "close",
        "stoch:14:3": "stoch_k:14:3",
        "bb:20:2.0": "bb_upper:20:2.0",
        "volatility:20": "rolling_vol:20",
        "volume:20": "volume_ma:20",
        "prev_day": "prev_day_high",
        "time": "hour",
        "regime": "regime:trending",
        "sessions": "session_london",
        "vwap": "vwap",
    }
    if spec in mapping:
        return mapping[spec]
    if ":" in spec:
        base, *rest = spec.split(":")
        if base == "stoch":
            return f"stoch_k:{':'.join(rest)}"
        if base == "bb":
            return f"bb_upper:{':'.join(rest)}"
        if base == "volatility":
            return f"rolling_vol:{':'.join(rest)}"
        if base == "volume":
            return f"volume_ma:{':'.join(rest)}"
    return spec


def is_spec_cached(spec: str, cached_columns: set) -> bool:
    """Return True if spec or its primary generated column exists in cached_columns."""
    if spec in cached_columns:
        return True
    if spec == "price" and ("close" in cached_columns or "price" in cached_columns):
        return True
    if spec == "close" and ("price" in cached_columns or "close" in cached_columns):
        return True
    primary = spec_primary_column(spec)
    return primary in cached_columns

