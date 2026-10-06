"""
SIMULATOR bridge — synthetic market data for development.

!! This is NOT MT5 data. Every Bar/Tick/record produced here is tagged
   source="SIMULATOR" so the UI and DB can never present it as real.

It generates a plausible XAUUSD-like price series with:
  * volatility clustering (GARCH-ish)
  * intraday session structure (Asia quiet, London/NY active + overlap)
  * occasional trend regimes and range regimes
  * bid/ask with time-varying spread, tick volume correlated with activity
  * a live tick stream (random walk around the last bar close)
"""
from __future__ import annotations

import hashlib
import math
import random
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np

from .bridge import (MarketBridge, Bar, Tick, SymbolInfo, AccountInfo,
                     OrderResult, TIMEFRAME_MINUTES)

SESSION_HOURS_UTC = {
    "asia": (0, 7),
    "london": (7, 13),
    "newyork": (13, 21),
}


def session_of(hour_utc: int) -> str:
    for name, (a, b) in SESSION_HOURS_UTC.items():
        if a <= hour_utc < b:
            return name
    return "off"


def session_activity(hour_utc: int) -> float:
    """Relative market activity multiplier by UTC hour."""
    s = session_of(hour_utc)
    base = {"asia": 0.5, "london": 1.0, "newyork": 1.0, "off": 0.25}[s]
    if hour_utc in (12, 13):   # London/NY overlap
        base *= 1.35
    return base


class SimulatorBridge(MarketBridge):
    name = "simulator"
    source = "SIMULATOR"
    broker_name = "LAB_SIMULATOR"
    server_name = "SIM"
    account_id = "0"

    def __init__(self, seed: int = 42, base_price: Dict[str, float] | None = None):
        self._seed = seed
        self._rng = np.random.default_rng(seed)
        self._connected = False
        self._lock = threading.RLock()
        self._base = base_price or {"XAUUSD": 2350.0, "BTCUSD": 62000.0,
                                    "NAS100": 18500.0, "EURUSD": 1.085}
        # live state per symbol
        self._live: Dict[str, Dict] = {}
        self._hist_cache: Dict[tuple, np.ndarray] = {}
        self._series_cache: Dict[tuple, np.ndarray] = {}
        self._forming: Dict[tuple, Dict] = {}

    # ---------- lifecycle ----------
    def available(self) -> bool:
        return True

    def connect(self) -> bool:
        self._connected = True
        return True

    def disconnect(self) -> None:
        self._connected = False

    def status(self) -> Dict:
        return {
            "bridge": "SIMULATOR",
            "connected": self._connected,
            "mode": "simulated",
            "warning": "SYNTHETIC DATA — not connected to MetaTrader 5. "
                       "Install/run the MT5 terminal on Windows and set mt5.mode=real "
                       "(or auto) to use genuine market data.",
            "seed": self._seed,
        }

    def symbol_info(self, symbol: str) -> Optional[SymbolInfo]:
        digits = 5 if symbol == "EURUSD" else (1 if symbol == "BTCUSD" else 2)
        point = 10 ** (-digits)
        cs = {"XAUUSD": 100.0, "BTCUSD": 1.0, "NAS100": 1.0, "EURUSD": 100000.0}.get(symbol, 100.0)
        return SymbolInfo(symbol=symbol, digits=digits, point=point,
                          trade_contract_size=cs, spread_points=18.0, source=self.source)

    def account_info(self) -> Optional[AccountInfo]:
        return AccountInfo(login=999999, server="SIMULATOR-DEMO", balance=10000.0,
                           equity=10000.0, is_demo=True, source=self.source)

    # ---------- historical data ----------
    # Live queries are served from a day-anchored series so that bars ADVANCE
    # with real time (stable timestamps within a day) and a forming bar tracks
    # the live random walk. Datasets are slices of the same series.
    _SERIES_LOOKBACK = 80_000

    def _series(self, symbol: str, timeframe: str, upto_ts: float,
                min_bars: int = 0) -> np.ndarray:
        """Day-anchored deterministic series covering [upto - lookback, end of today]."""
        step = TIMEFRAME_MINUTES[timeframe] * 60
        day_end = (int(upto_ts // 86400) + 1) * 86400
        n_today = int(86400 // step) + 2
        n_total = max(self._SERIES_LOOKBACK, min_bars) + n_today * 2
        key = (symbol, timeframe, day_end, n_total)
        with self._lock:
            arr = self._series_cache.get(key)
        if arr is None:
            arr = self._generate_history(symbol, timeframe, day_end, n_total)
            arr = self._anchor_level(symbol, arr, upto_ts)
            with self._lock:
                self._series_cache[key] = arr
                while len(self._series_cache) > 6:
                    self._series_cache.pop(next(iter(self._series_cache)))
        return arr

    # ---- level anchoring: all timeframes of a symbol share the same price
    # ---- level at today's UTC midnight (cross-timeframe consistency) ----
    def _day_ref_price(self, symbol: str, day_idx: int) -> float:
        base = self._base.get(symbol, 100.0)
        cache_key = ("ref", symbol)
        with self._lock:
            cached = self._hist_cache.get(cache_key)
            if cached is not None and cached[0] == day_idx:
                return float(cached[1])
        h = int(hashlib.sha256(symbol.encode()).hexdigest()[:8], 16)
        rng = np.random.default_rng(h)
        epoch_day = 18000  # ~2019-04; deterministic meander since then
        n = max(1, day_idx - epoch_day)
        ref = base * math.exp(float(rng.normal(0.0002, 0.006, n).sum()))
        with self._lock:
            self._hist_cache[cache_key] = (day_idx, ref)
        return ref

    def _anchor_level(self, symbol: str, arr: np.ndarray, upto_ts: float) -> np.ndarray:
        day_start = int(upto_ts // 86400) * 86400
        ref = self._day_ref_price(symbol, day_start // 86400)
        idx = int(np.searchsorted(arr[:, 0], day_start))
        anchor = float(arr[max(0, idx - 1), 4]) if len(arr) else ref
        if anchor <= 0:
            return arr
        factor = ref / anchor
        out = arr.copy()
        for col in (1, 2, 3, 4, 5, 6):
            out[:, col] *= factor
        return out

    def _generate_history(self, symbol: str, timeframe: str, end_ts: float,
                          n_bars: int) -> np.ndarray:
        """Deterministic synthetic OHLCV+b/a/s history ending at end_ts."""
        key = (symbol, timeframe, int(end_ts // 86400), n_bars)
        with self._lock:
            if key in self._hist_cache:
                return self._hist_cache[key]
        rng = np.random.default_rng(abs(hash(key)) % (2 ** 31))
        tf_min = TIMEFRAME_MINUTES[timeframe]
        step = tf_min * 60
        start = end_ts - n_bars * step
        ts = start + np.arange(n_bars) * step

        hours = ((ts // 3600) % 24).astype(int)
        dow = (((ts // 86400) + 3) % 7).astype(int)  # 0=Mon (epoch day0=Thu)
        act = np.array([session_activity(h) for h in hours])
        weekend = (dow >= 5)

        base = self._base.get(symbol, 100.0)
        # per-bar LOG-RETURN volatility: clustered + session-scaled
        vol_unit = 0.00055 * math.sqrt(tf_min / 15.0)
        cluster = np.ones(n_bars)
        c = 1.0
        for i in range(n_bars):
            c = 0.94 * c + 0.06 * (0.6 + rng.exponential(0.6))
            cluster[i] = c
        sigma = vol_unit * act * np.clip(cluster, 0.35, 3.2)

        # regime drift: slowly switching trend/range states (log-return units)
        drift = np.zeros(n_bars)
        state, remaining = 0.0, 0
        for i in range(n_bars):
            if remaining <= 0:
                state = rng.choice([-1.0, 0.0, 1.0], p=[0.22, 0.5, 0.28])
                remaining = int(rng.integers(200, 1600))
            remaining -= 1
            drift[i] = state * vol_unit * 0.30 * act[i]

        rets = rng.normal(0.0, 1.0, n_bars) * sigma + drift
        rets[weekend] = 0.0
        close = base * np.exp(np.cumsum(rets))

        open_ = np.empty(n_bars); open_[0] = base; open_[1:] = close[:-1]
        spread_hi = np.maximum(open_, close)
        spread_lo = np.minimum(open_, close)
        wick = np.abs(close) * sigma * rng.uniform(0.15, 1.4, n_bars)
        high = spread_hi + wick
        low = spread_lo - wick

        point = 0.01 if symbol not in ("BTCUSD",) else 0.1
        spread_pts = np.clip(rng.normal(18.0, 5.0, n_bars) * (0.7 + 0.6 * act), 5.0, 60.0)
        spread_abs = spread_pts * point
        bid = close - spread_abs / 2
        ask = close + spread_abs / 2
        tickvol = np.clip(rng.poisson(90 * act * np.clip(cluster, 0.4, 2.2)), 1, None).astype(int)
        tickvol[weekend] = 0

        arr = np.column_stack([ts, open_, high, low, close, bid, ask,
                               spread_pts, tickvol]).astype(np.float64)
        with self._lock:
            if len(self._hist_cache) > 24:
                self._hist_cache.clear()
            self._hist_cache[key] = arr
        return arr

    def _to_bars(self, arr: np.ndarray) -> List[Bar]:
        return [Bar(ts=r[0], open=r[1], high=r[2], low=r[3], close=r[4],
                    bid=r[5], ask=r[6], spread=r[7], tick_volume=int(r[8]),
                    source=self.source) for r in arr]

    def copy_rates(self, symbol: str, timeframe: str, n_bars: int) -> List[Bar]:
        """Last n_bars bars: closed bars + the current FORMING bar (live)."""
        step = TIMEFRAME_MINUTES[timeframe] * 60
        now = time.time()
        cur_start = now - (now % step)
        arr = self._series(symbol, timeframe, now, min_bars=n_bars + 2)
        closed = arr[arr[:, 0] <= cur_start - step]
        closed = closed[-(max(1, n_bars) - 1):]
        forming = self._forming_bar(symbol, timeframe, cur_start, closed, step)
        out = self._to_bars(closed)
        out.append(forming)
        return out

    def copy_rates_range(self, symbol: str, timeframe: str,
                         start_ts: float, end_ts: float) -> List[Bar]:
        step = TIMEFRAME_MINUTES[timeframe] * 60
        n = max(1, int((end_ts - start_ts) // step))
        arr = self._series(symbol, timeframe, end_ts, min_bars=n + 2)
        sel = arr[(arr[:, 0] >= start_ts - step) & (arr[:, 0] <= end_ts)]
        return self._to_bars(sel)

    def _forming_bar(self, symbol: str, timeframe: str, cur_start: float,
                     closed: np.ndarray, step: int) -> Bar:
        """Build/update the live forming bar; anchors the random walk to the
        last closed bar so the paper engine sees continuous prices."""
        key = (symbol, timeframe)
        prev_close = float(closed[-1][4]) if len(closed) else self._base.get(symbol, 100.0)
        with self._lock:
            st = self._forming.get(key)
            if st is None or st["start"] != cur_start:
                # new bar: snap the live walk to the series FIRST (continuity)
                live = self._live.setdefault(symbol, {"price": prev_close,
                                                      "ts": time.time(),
                                                      "vol": max(prev_close * 0.00012, 0.02)})
                live["price"] = prev_close
                st = {"start": cur_start, "open": prev_close, "high": prev_close,
                      "low": prev_close, "vol": 0}
                self._forming[key] = st
        tick = self.latest_tick(symbol)          # walk now continues from prev_close
        with self._lock:
            st = self._forming[key]
            px = float(tick.last or tick.bid)
            st["high"] = max(st["high"], px)
            st["low"] = min(st["low"], px)
            st["vol"] += max(int(tick.volume or 1), 1)
            spread_pts = max((tick.ask - tick.bid) / 0.01, 0.5)
            return Bar(ts=cur_start, open=st["open"], high=st["high"], low=st["low"],
                       close=px, bid=tick.bid, ask=tick.ask, spread=spread_pts,
                       tick_volume=st["vol"], source=self.source)

    # ---------- live ticks ----------
    def _live_state(self, symbol: str) -> Dict:
        with self._lock:
            if symbol not in self._live:
                # seed from the anchored series (never via copy_rates ->
                # that path calls latest_tick and would recurse)
                try:
                    arr = self._series(symbol, "M1", time.time(), min_bars=200)
                    last_close = float(arr[-1][4])
                except Exception:
                    last_close = self._base.get(symbol, 100.0)
                self._live[symbol] = {
                    "price": last_close, "ts": time.time(),
                    "vol": max(last_close * 0.00012, 0.02),
                }
            return self._live[symbol]

    def latest_tick(self, symbol: str) -> Optional[Tick]:
        st = self._live_state(symbol)
        with self._lock:
            now = time.time()
            hour = datetime.now(timezone.utc).hour
            act = session_activity(hour)
            if now - st["ts"] < 0.25:
                price = st["price"]
            else:
                drift = random.gauss(0, st["vol"] * (0.4 + act))
                price = max(st["price"] + drift, 1.0)
                st["price"] = price
                st["ts"] = now
            spread = max(0.05, random.gauss(0.18 * (0.8 + 0.5 * act), 0.04))
            self.last_tick_ts = now
            self.last_request_ts = now
            return Tick(ts=now, bid=price - spread / 2, ask=price + spread / 2,
                        last=price, volume=random.randint(1, 40), source=self.source)

    # ---------- paper order simulation ----------
    def simulate_market_order(self, symbol: str, side: str, lots: float,
                              price_hint: Optional[float] = None) -> OrderResult:
        t0 = time.time()
        tick = self.latest_tick(symbol)
        if tick is None:
            return OrderResult(ok=False, comment="no tick", source=self.source)
        delay_ms = max(20.0, random.gauss(110, 40))
        time.sleep(min(delay_ms / 1000.0, 0.05))  # partial real delay; rest recorded
        base = tick.ask if side == "buy" else tick.bid
        slip_pts = abs(random.gauss(0.8, 0.6))
        slip = slip_pts * 0.01
        exec_price = base + slip if side == "buy" else base - slip
        exec_ts = t0 + delay_ms / 1000.0
        return OrderResult(ok=True, order_id=random.randint(10 ** 8, 10 ** 9),
                           exec_price=round(exec_price, 2), exec_ts=exec_ts,
                           requested_price=base, slippage_points=slip_pts,
                           delay_ms=delay_ms, retcode=10009, comment="simulated fill",
                           source=self.source)
