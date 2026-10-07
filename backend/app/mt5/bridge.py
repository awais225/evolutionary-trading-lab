"""Abstract market-data/execution bridge contract."""
from __future__ import annotations

import abc
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional


TIMEFRAME_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60,
                     "H4": 240, "D1": 1440}


@dataclass
class Bar:
    ts: float            # unix seconds, bar open time (UTC)
    open: float
    high: float
    low: float
    close: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    spread: Optional[float] = None   # in points
    tick_volume: int = 0
    source: str = "SIMULATOR"        # "MT5" | "SIMULATOR"

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class Tick:
    ts: float
    bid: float
    ask: float
    last: Optional[float] = None
    volume: int = 0
    source: str = "SIMULATOR"

    @property
    def spread_points(self) -> float:
        return (self.ask - self.bid)


@dataclass
class SymbolInfo:
    symbol: str
    digits: int = 2
    point: float = 0.01
    trade_contract_size: float = 100.0
    spread_points: float = 2.5
    trade_mode: str = "full"
    source: str = "SIMULATOR"
    visible: bool = True
    # V4.2 — trading constraints used by the order validator (None = unknown;
    # the validator reports "unknown" rather than guessing when a value is None)
    trade_mode_raw: Optional[int] = None       # SYMBOL_TRADE_MODE_* constant
    trade_allowed: Optional[bool] = None       # symbol_info.trade_allowed
    volume_min: Optional[float] = None
    volume_max: Optional[float] = None
    volume_step: Optional[float] = None
    trade_stops_level: Optional[int] = None    # broker minimum SL/TP distance (points)
    freeze_level: Optional[int] = None
    filling_modes: Optional[List[int]] = None  # supported ORDER_FILLING_* modes
    # V5.2 §1 — the raw ENUM_SYMBOL_FILLING_MODE bitmask (FOK=1, IOC=2) as the
    # terminal reported it; 0/None means "no FOK/IOC restriction". Kept beside
    # the decoded ORDER_FILLING_* list so diagnostics can show both.
    filling_mode_raw: Optional[int] = None
    # V4.3 - broker specs used for risk-based position sizing (None = unknown,
    # in which case the risk calculation refuses to guess)
    trade_tick_size: Optional[float] = None    # price change for one tick
    trade_tick_value: Optional[float] = None   # money per tick per 1.0 lot
    currency_profit: Optional[str] = None


@dataclass
class AccountInfo:
    login: int = 0
    server: str = ""
    balance: float = 0.0
    equity: float = 0.0
    currency: str = "USD"
    is_demo: bool = True
    source: str = "SIMULATOR"
    # V4.2 — used for positive demo identification (raw MT5 trade mode)
    margin_free: float = 0.0
    leverage: int = 0
    name: str = ""


@dataclass
class OrderResult:
    ok: bool
    order_id: Optional[int] = None
    exec_price: Optional[float] = None
    exec_ts: Optional[float] = None
    requested_price: Optional[float] = None
    slippage_points: Optional[float] = None
    delay_ms: Optional[float] = None
    retcode: Optional[int] = None
    comment: str = ""
    rejected_by: Optional[str] = None   # e.g. "RISK_MANAGER"
    source: str = "SIMULATOR"


class MarketBridge(abc.ABC):
    """Contract every bridge must fulfil."""

    name: str = "abstract"
    source: str = "UNKNOWN"

    # feed-health bookkeeping (filled by implementations; used by the
    # connection monitor, spec §15-19)
    last_tick_ts: float = 0.0        # epoch of the most recent tick received
    last_request_ts: float = 0.0     # epoch of the last successful data call
    last_request_ms: float = 0.0     # latency of that call

    @property
    def is_simulated(self) -> bool:
        return self.source == "SIMULATOR"

    def profile(self) -> Dict[str, str]:
        """Broker/server identity used in dataset identity (spec §6).

        Broker A XAUUSD and Broker B XAUUSD are DIFFERENT datasets.
        """
        return {"source": self.source,
                "broker": getattr(self, "broker_name", self.source),
                "server": getattr(self, "server_name", ""),
                "account": str(getattr(self, "account_id", "") or "")}

    def profile_key(self) -> str:
        pr = self.profile()
        return f"{pr['source']}|{pr['broker']}|{pr['server']}"

    @abc.abstractmethod
    def available(self) -> bool: ...

    @abc.abstractmethod
    def connect(self) -> bool: ...

    @abc.abstractmethod
    def disconnect(self) -> None: ...

    @abc.abstractmethod
    def status(self) -> Dict: ...

    @abc.abstractmethod
    def symbol_info(self, symbol: str) -> Optional[SymbolInfo]: ...

    @abc.abstractmethod
    def account_info(self) -> Optional[AccountInfo]: ...

    @abc.abstractmethod
    def copy_rates(self, symbol: str, timeframe: str, n_bars: int) -> List[Bar]:
        """Most recent n_bars closed bars."""

    @abc.abstractmethod
    def copy_rates_range(self, symbol: str, timeframe: str,
                         start_ts: float, end_ts: float) -> List[Bar]: ...

    @abc.abstractmethod
    def latest_tick(self, symbol: str) -> Optional[Tick]: ...

    def tick_stream(self, symbol: str):
        """Yield ticks; default implementation polls latest_tick."""
        import time as _t
        last = 0.0
        while True:
            t = self.latest_tick(symbol)
            if t is not None and t.ts != last:
                last = t.ts
                yield t
            _t.sleep(0.1)

    # ---- order simulation / (eventually) controlled real execution ----
    @abc.abstractmethod
    def simulate_market_order(self, symbol: str, side: str, lots: float,
                              price_hint: Optional[float] = None) -> OrderResult:
        """Simulated fill against the live feed (paper trading)."""

    def real_market_order(self, symbol: str, side: str, lots: float) -> OrderResult:
        """Real execution. Disabled unless the bridge explicitly supports it
        AND the user has activated real trading (risk layer double-checks)."""
        return OrderResult(ok=False, comment="real execution not supported by this bridge",
                           rejected_by="BRIDGE", source=self.source)

    # ---- V4.2 execution primitives -------------------------------------
    # Only MT5RealBridge implements these against a real terminal. The base
    # implementations report "unsupported" so no bridge can ever pretend to
    # have executed an order.
    def send_market_order(self, request: Dict) -> Dict:
        """Send a fully-built MT5 order request. Returns {unsupported: True}."""
        return {"unsupported": True, "ok": False, "retcode": None,
                "error": f"order execution is not supported by bridge '{self.name}'"}

    def positions_get(self, ticket: Optional[int] = None, symbol: Optional[str] = None) -> List[Dict]:
        """Open positions from the terminal (verification of executed orders)."""
        return []

    def orders_get(self, ticket: Optional[int] = None, symbol: Optional[str] = None) -> List[Dict]:
        """Pending orders from the terminal."""
        return []

    def close_position(self, ticket: int, comment: str = "") -> Dict:
        return {"ok": False, "error": f"position closing is not supported by bridge '{self.name}'"}
