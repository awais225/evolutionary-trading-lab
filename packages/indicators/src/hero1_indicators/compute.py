"""Causal indicator mathematics for DeepTesting (Phase 3).

Every series is computed strictly causally: the value at index i depends
only on candles[0..i]. Warmup positions are None - never back-filled,
never approximated. Formulas are the standard textbook/Wilder forms:

* SMA: arithmetic mean of the last `period` source values.
* EMA: seeded with the SMA of the first `period` values, k = 2/(period+1).
* RSI / ATR / ADX: Wilder smoothing.
* MACD: EMA(fast) - EMA(slow); signal = EMA of MACD; histogram = diff.
* BBANDS: SMA basis +/- stddev * population standard deviation.
* ROC: (src / src[-period] - 1) * 100.
* STOCH: %K over k_period extremes, slowed by SMA(slowing); %D = SMA(k, d_period).
* VWAP: daily-anchored cumulative typical-price volume average (UTC day).

Sources: close (default), open/high/low per the spec's `source` param;
ATR/STOCH/ADX always use OHLC. Volume defaults to 1.0 when a candle has
no volume so VWAP degrades to a typical-price average rather than dying.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence

Candle = Dict[str, float]


def _src(candles: Sequence[Candle], source: str) -> List[float]:
    key = source if source in ("open", "high", "low", "close") else "close"
    return [float(c[key]) for c in candles]


def sma(values: Sequence[float], period: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    acc = 0.0
    for i, v in enumerate(values):
        acc += v
        if i >= period:
            acc -= values[i - period]
        if i >= period - 1:
            out[i] = acc / period
    return out


def ema(values: Sequence[float], period: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    if len(values) < period:
        return out
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    k = 2.0 / (period + 1)
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def _wilder(values: Sequence[float], period: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    if len(values) <= period:
        return out
    prev = sum(values[1:period + 1]) / period
    out[period] = prev
    for i in range(period + 1, len(values)):
        prev = (prev * (period - 1) + values[i]) / period
        out[i] = prev
    return out


def rsi(closes: Sequence[float], period: int) -> List[Optional[float]]:
    gains = [0.0] * len(closes)
    losses = [0.0] * len(closes)
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains[i] = max(d, 0.0)
        losses[i] = max(-d, 0.0)
    avg_g = _wilder(gains, period)
    avg_l = _wilder(losses, period)
    out: List[Optional[float]] = [None] * len(closes)
    for i in range(len(closes)):
        if avg_g[i] is None or avg_l[i] is None:
            continue
        out[i] = 100.0 if avg_l[i] == 0 else 100.0 - 100.0 / (1.0 + avg_g[i] / avg_l[i])
    return out


def true_ranges(candles: Sequence[Candle]) -> List[float]:
    tr = [0.0] * len(candles)
    for i, c in enumerate(candles):
        if i == 0:
            tr[i] = float(c["high"]) - float(c["low"])
        else:
            pc = float(candles[i - 1]["close"])
            tr[i] = max(float(c["high"]) - float(c["low"]),
                        abs(float(c["high"]) - pc), abs(float(c["low"]) - pc))
    return tr


def atr(candles: Sequence[Candle], period: int) -> List[Optional[float]]:
    return _wilder(true_ranges(candles), period)


def macd(closes: Sequence[float], fast: int, slow: int, signal: int) -> Dict[str, List[Optional[float]]]:
    ef, es = ema(closes, fast), ema(closes, slow)
    line: List[Optional[float]] = [None if (a is None or b is None) else a - b
                                   for a, b in zip(ef, es)]
    compact = [v for v in line if v is not None]
    sig_compact = ema(compact, signal)
    sig: List[Optional[float]] = [None] * len(closes)
    j = 0
    for i, v in enumerate(line):
        if v is not None:
            sig[i] = sig_compact[j]
            j += 1
    hist = [None if (a is None or b is None) else a - b for a, b in zip(line, sig)]
    return {"macd": line, "signal": sig, "histogram": hist}


def bbands(values: Sequence[float], period: int, stddev: float) -> Dict[str, List[Optional[float]]]:
    basis = sma(values, period)
    upper: List[Optional[float]] = [None] * len(values)
    lower: List[Optional[float]] = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1:i + 1]
        mean = basis[i]
        var = sum((v - mean) ** 2 for v in window) / period
        sd = math.sqrt(var)
        upper[i] = mean + stddev * sd
        lower[i] = mean - stddev * sd
    return {"upper": upper, "basis": basis, "lower": lower}


def roc(closes: Sequence[float], period: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(closes)
    for i in range(period, len(closes)):
        if closes[i - period] != 0:
            out[i] = (closes[i] / closes[i - period] - 1.0) * 100.0
    return out


def stoch(candles: Sequence[Candle], k_period: int, d_period: int, slowing: int) -> Dict[str, List[Optional[float]]]:
    raw: List[Optional[float]] = [None] * len(candles)
    for i in range(k_period - 1, len(candles)):
        window = candles[i - k_period + 1:i + 1]
        hh = max(float(c["high"]) for c in window)
        ll = min(float(c["low"]) for c in window)
        raw[i] = 50.0 if hh == ll else (float(candles[i]["close"]) - ll) / (hh - ll) * 100.0
    compact = [v for v in raw if v is not None]
    k_compact = sma(compact, slowing)
    d_compact = sma([v for v in k_compact if v is not None], d_period)
    k: List[Optional[float]] = [None] * len(candles)
    d: List[Optional[float]] = [None] * len(candles)
    idx = [i for i, v in enumerate(raw) if v is not None]
    for pos, i in enumerate(idx):
        k[i] = k_compact[pos]
    k_idx = [i for i, v in enumerate(k) if v is not None]
    for pos, i in enumerate(k_idx):
        if pos < len(d_compact):
            d[i] = d_compact[pos]
    return {"k": k, "d": d}


def adx(candles: Sequence[Candle], period: int) -> List[Optional[float]]:
    n = len(candles)
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    for i in range(1, n):
        up = float(candles[i]["high"]) - float(candles[i - 1]["high"])
        down = float(candles[i - 1]["low"]) - float(candles[i]["low"])
        plus_dm[i] = up if (up > down and up > 0) else 0.0
        minus_dm[i] = down if (down > up and down > 0) else 0.0
    tr_s = _wilder(true_ranges(candles), period)
    p_s = _wilder(plus_dm, period)
    m_s = _wilder(minus_dm, period)
    dx: List[Optional[float]] = [None] * n
    for i in range(n):
        if tr_s[i] in (None, 0) or p_s[i] is None or m_s[i] is None:
            continue
        pdi = 100.0 * p_s[i] / tr_s[i]
        mdi = 100.0 * m_s[i] / tr_s[i]
        dx[i] = 0.0 if (pdi + mdi) == 0 else 100.0 * abs(pdi - mdi) / (pdi + mdi)
    compact = [v for v in dx if v is not None]
    smoothed = _wilder([0.0] + compact, period)  # align: _wilder indexes from 1
    out: List[Optional[float]] = [None] * n
    idx = [i for i, v in enumerate(dx) if v is not None]
    for pos, i in enumerate(idx):
        if pos + 1 < len(smoothed):
            out[i] = smoothed[pos + 1]
    return out


def vwap(candles: Sequence[Candle]) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(candles)
    cum_pv = 0.0
    cum_v = 0.0
    current_day: Optional[int] = None
    import datetime as _dt
    for i, c in enumerate(candles):
        day = _dt.datetime.utcfromtimestamp(int(c["ts"])).date().toordinal()
        if day != current_day:
            current_day, cum_pv, cum_v = day, 0.0, 0.0
        typical = (float(c["high"]) + float(c["low"]) + float(c["close"])) / 3.0
        vol = float(c.get("volume") or 1.0)
        cum_pv += typical * vol
        cum_v += vol
        out[i] = cum_pv / cum_v if cum_v else typical
    return out


def compute_indicator(name: str, params: dict, candles: Sequence[Candle]) -> Dict[str, List[Optional[float]]]:
    """Return {field: series} for one indicator definition. Raises ValueError
    for unknown names so the engine can fail explicitly, never guess."""
    name = str(name).upper()
    source = str(params.get("source") or "close")
    closes = _src(candles, "close")
    if name == "SMA":
        return {"value": sma(_src(candles, source), int(params["period"]))}
    if name == "EMA":
        return {"value": ema(_src(candles, source), int(params["period"]))}
    if name == "RSI":
        return {"value": rsi(_src(candles, source), int(params.get("period", 14)))}
    if name == "ATR":
        return {"value": atr(candles, int(params.get("period", 14)))}
    if name == "MACD":
        return macd(_src(candles, source), int(params.get("fast", 12)),
                    int(params.get("slow", 26)), int(params.get("signal", 9)))
    if name == "BBANDS":
        return bbands(_src(candles, source), int(params.get("period", 20)),
                      float(params.get("stddev", 2.0)))
    if name == "ROC":
        return {"value": roc(_src(candles, source), int(params.get("period", 12)))}
    if name == "STOCH":
        return stoch(candles, int(params.get("k_period", 14)),
                     int(params.get("d_period", 3)), int(params.get("slowing", 3)))
    if name == "ADX":
        return {"adx": adx(candles, int(params.get("period", 14)))}
    if name == "VWAP":
        return {"value": vwap(candles)}
    raise ValueError(f"indicator {name!r} has no Phase 3 mathematics - refusing to approximate it")


__all__ = ["compute_indicator", "sma", "ema", "rsi", "atr", "macd", "bbands",
           "roc", "stoch", "adx", "vwap", "true_ranges"]
