"""
REAL MetaTrader 5 bridge.

Uses the official `MetaTrader5` Python package (Windows-only, requires a
running MT5 terminal). NOTHING here is faked: if the package is missing or
the terminal cannot initialize, `available()` returns False and `status()`
explains exactly why — the lab then reports MT5 as NOT CONNECTED and the
SimulatorBridge can be used instead (clearly labelled).

Real order sending (`real_market_order`) is implemented but is gated twice:
  1. RiskManager.real_execution_allowed() must be True (explicit user
     activation from the dashboard; never automatic).
  2. The caller (paper/execution layer) must pass a risk-checked request.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .bridge import (MarketBridge, Bar, Tick, SymbolInfo, AccountInfo,
                     OrderResult, TIMEFRAME_MINUTES)
from .config import get_saved_terminal_path, save_mt5_config, load_mt5_config
from .discovery import discover_terminals, validate_terminal_path

log = logging.getLogger("mt5.real")

try:
    import MetaTrader5 as mt5  # type: ignore
    MT5_PACKAGE_AVAILABLE = True
    MT5_IMPORT_ERROR = ""
except Exception as e:  # pragma: no cover - platform dependent
    mt5 = None  # type: ignore
    MT5_PACKAGE_AVAILABLE = False
    MT5_IMPORT_ERROR = str(e)

_TF_MAP = {}


def _tf_const(timeframe: str):
    if not MT5_PACKAGE_AVAILABLE:
        return None
    if not _TF_MAP:
        _TF_MAP.update({
            "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15, "M30": mt5.TIMEFRAME_M30,
            "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4, "D1": mt5.TIMEFRAME_D1,
        })
    return _TF_MAP.get(timeframe)


class MT5RealBridge(MarketBridge):
    name = "mt5_real"
    source = "MT5"
    broker_name = ""
    server_name = ""
    account_id = ""

    def __init__(self, login: int = 0, password: str = "", server: str = "",
                 path: str = "", timeout_ms: int = 60000):
        self._login = login
        self._password = password
        self._server = server
        self._path = path
        self._timeout = timeout_ms
        self._connected = False
        self._last_error = ""
        self._terminal_build: Optional[int] = None
        self._terminal_version: Optional[str] = None
        self._terminal_company: str = ""
        self._terminal_path: str = ""
        self._terminal_name: str = ""

    # ---------- lifecycle ----------
    def available(self) -> bool:
        return MT5_PACKAGE_AVAILABLE

    def connect(self) -> bool:
        if not MT5_PACKAGE_AVAILABLE:
            self._last_error = (f"MetaTrader5 python package not available "
                                f"({MT5_IMPORT_ERROR}). pip install MetaTrader5 on Windows.")
            return False

        # Resolution priority:
        # 1. Configured/passed path
        # 2. Previously saved path in CONFIG/mt5_config.json
        # 3. Automatic discovery (mt5.initialize() without path)
        # 4. Common discovered terminal locations
        target_path = self._path
        if not target_path:
            saved_p = get_saved_terminal_path()
            if saved_p and Path(saved_p).exists():
                target_path = saved_p

        kwargs = {"timeout": self._timeout}
        if self._login:
            kwargs.update({"login": int(self._login), "password": self._password,
                           "server": self._server})

        ok = False
        # Try target path first if available
        if target_path:
            try:
                ok = mt5.initialize(path=str(target_path), **kwargs)
                if ok:
                    log.info("Initialized MT5 with specified path: %s", target_path)
            except Exception as e:
                log.warning("MT5 initialize with path %s failed: %s", target_path, e)

        # If still not connected, try standard auto-discovery
        if not ok:
            try:
                ok = mt5.initialize(**kwargs)
                if ok:
                    log.info("Initialized MT5 via automatic discovery.")
            except Exception as e:
                self._last_error = f"initialize exception: {e}"

        # If still not connected, try discovered terminals
        if not ok:
            terminals = discover_terminals()
            for term in terminals:
                t_path = term.get("path")
                if t_path and Path(t_path).exists():
                    try:
                        ok = mt5.initialize(path=str(t_path), **kwargs)
                        if ok:
                            log.info("Initialized MT5 using discovered terminal: %s", t_path)
                            break
                    except Exception:
                        continue

        if not ok:
            try:
                self._last_error = f"mt5.initialize failed: {mt5.last_error()}"
            except Exception:
                pass
            self._connected = False
            return False

        self._connected = True
        self._refresh_identity()
        log.info("MT5 terminal connected: %s (Build %s, %s)",
                 self._terminal_name, self._terminal_build, self._terminal_company)
        return True

    def _refresh_identity(self) -> None:
        """Fill dynamic terminal info, build number, broker/server/account identity."""
        if not MT5_PACKAGE_AVAILABLE or not self._connected:
            return
        try:
            ver = mt5.version()
            if ver:
                # ver is typically tuple e.g. (500, 5060, '12 Sep 2026')
                self._terminal_build = ver[1] if len(ver) > 1 else ver[0]
                self._terminal_version = str(ver)
        except Exception as e:
            log.debug("mt5.version() error: %s", e)

        try:
            ti = mt5.terminal_info()
            if ti is not None:
                self._terminal_name = str(getattr(ti, "name", "") or "MetaTrader 5")
                self._terminal_company = str(getattr(ti, "company", "") or "")
                self._terminal_path = str(getattr(ti, "path", "") or "")
                self.broker_name = self._terminal_company or "MT5"
                # Save last connected info to persistent config
                cfg = load_mt5_config()
                cfg["last_connected_terminal"] = self._terminal_name
                cfg["last_connected_company"] = self._terminal_company
                cfg["last_connected_build"] = self._terminal_build
                if self._terminal_path and not cfg.get("terminal_path"):
                    cfg["terminal_path"] = self._terminal_path
                save_mt5_config(cfg)
        except Exception as e:
            log.debug("terminal_info error: %s", e)
            self.broker_name = self.broker_name or "MT5"

        try:
            ai = mt5.account_info()
            if ai is not None:
                self.server_name = str(ai.server or "")
                self.account_id = str(ai.login or "")
        except Exception:
            pass

    def disconnect(self) -> None:
        if self._connected and MT5_PACKAGE_AVAILABLE:
            try:
                mt5.shutdown()
            except Exception:
                pass
        self._connected = False
        self._terminal_build = None
        self._terminal_company = ""
        self._terminal_path = ""
        self._terminal_name = ""

    def status(self) -> Dict:
        pkg_ver = getattr(mt5, "__version__", "not installed") if MT5_PACKAGE_AVAILABLE else None
        st = {
            "bridge": "MT5_REAL",
            "package_installed": MT5_PACKAGE_AVAILABLE,
            "package_version": pkg_ver,
            "connected": self._connected,
            "mode": "real",
            "last_error": self._last_error,
            "terminal_build": self._terminal_build,
            "terminal_company": self._terminal_company,
            "terminal_path": self._terminal_path,
            "terminal_name": self._terminal_name,
        }
        if self._connected and MT5_PACKAGE_AVAILABLE:
            try:
                ai = mt5.account_info()
                ti = mt5.terminal_info()
                st.update({
                    "account": ai.login if ai else None,
                    "server": ai.server if ai else None,
                    "demo": bool(ai.trade_mode == 0) if ai else None,
                    "terminal": ti.name if ti else self._terminal_name,
                    "terminal_connected": bool(ti.connected) if ti else None,
                })
            except Exception as e:
                st["status_error"] = str(e)
        return st

    # ---------- info ----------
    def symbol_info(self, symbol: str) -> Optional[SymbolInfo]:
        if not self._connected:
            return None
        try:
            mt5.symbol_select(symbol, True)
            si = mt5.symbol_info(symbol)
        except Exception as e:
            log.warning("symbol_info failed: %s", e)
            return None
        if si is None:
            return None
        return SymbolInfo(symbol=symbol, digits=si.digits, point=si.point,
                          trade_contract_size=si.trade_contract_size,
                          spread_points=float(si.spread),
                          trade_mode=str(si.trade_mode), source=self.source,
                          visible=bool(si.visible))

    def account_info(self) -> Optional[AccountInfo]:
        if not self._connected:
            return None
        try:
            ai = mt5.account_info()
        except Exception:
            return None
        if ai is None:
            return None
        return AccountInfo(login=ai.login, server=ai.server, balance=ai.balance,
                           equity=ai.equity, currency=ai.currency,
                           is_demo=bool(ai.trade_mode == 0), source=self.source)

    # ---------- historical ----------
    def _rows_to_bars(self, rows) -> List[Bar]:
        if rows is None:
            return []
        try:
            if len(rows) == 0:
                return []
        except (TypeError, ValueError):
            return []

        bars: List[Bar] = []
        # Fast path for numpy structured arrays (standard MT5 python package output)
        if hasattr(rows, "dtype") and getattr(rows.dtype, "names", None):
            names = set(rows.dtype.names)
            try:
                times = rows["time"].astype(float)
                opens = rows["open"].astype(float)
                highs = rows["high"].astype(float)
                lows = rows["low"].astype(float)
                closes = rows["close"].astype(float)
                spreads = rows["spread"].astype(float) if "spread" in names else None
                volumes = rows["tick_volume"].astype(int) if "tick_volume" in names else (
                    rows["volume"].astype(int) if "volume" in names else None
                )

                n = len(rows)
                for i in range(n):
                    c = float(closes[i])
                    sp = float(spreads[i]) if spreads is not None else 0.0
                    pt_sp = sp * 0.01
                    vol = int(volumes[i]) if volumes is not None else 0
                    bars.append(Bar(
                        ts=float(times[i]), open=float(opens[i]), high=float(highs[i]),
                        low=float(lows[i]), close=c,
                        bid=c - pt_sp / 2.0, ask=c + pt_sp / 2.0,
                        spread=sp, tick_volume=vol,
                        source=self.source
                    ))
                return bars
            except Exception as e:
                log.debug("structured array conversion failed, falling back to iterative: %s", e)

        # Fallback iterative conversion supporting records, dicts, and namedtuples
        for r in rows:
            try:
                if isinstance(r, dict):
                    t = r.get("time") or r.get("ts")
                    o = float(r.get("open", 0.0))
                    h = float(r.get("high", 0.0))
                    l = float(r.get("low", 0.0))
                    c = float(r.get("close", 0.0))
                    sp = float(r.get("spread", 0.0) or 0.0)
                    vol = int(r.get("tick_volume", 0) or r.get("volume", 0) or 0)
                elif hasattr(r, "dtype") and getattr(r.dtype, "names", None):
                    t = r["time"] if "time" in r.dtype.names else r[0]
                    o = float(r["open"])
                    h = float(r["high"])
                    l = float(r["low"])
                    c = float(r["close"])
                    sp = float(r["spread"]) if "spread" in r.dtype.names else 0.0
                    vol = int(r["tick_volume"]) if "tick_volume" in r.dtype.names else (
                        int(r["volume"]) if "volume" in r.dtype.names else 0
                    )
                else:
                    t = getattr(r, "time", None) or getattr(r, "ts", 0.0)
                    o = float(getattr(r, "open", 0.0))
                    h = float(getattr(r, "high", 0.0))
                    l = float(getattr(r, "low", 0.0))
                    c = float(getattr(r, "close", 0.0))
                    sp = float(getattr(r, "spread", 0.0) or 0.0)
                    vol = int(getattr(r, "tick_volume", 0) or getattr(r, "volume", 0) or 0)

                if hasattr(t, "timestamp"):
                    t = t.timestamp()
                t = float(t)
                pt_sp = sp * 0.01
                bars.append(Bar(
                    ts=t, open=o, high=h, low=l, close=c,
                    bid=c - pt_sp / 2.0, ask=c + pt_sp / 2.0,
                    spread=sp, tick_volume=vol,
                    source=self.source
                ))
            except Exception as ex:
                log.debug("error converting bar row: %s", ex)
                continue
        return bars

    def copy_rates(self, symbol: str, timeframe: str, n_bars: int) -> List[Bar]:
        if not self._connected or not MT5_PACKAGE_AVAILABLE:
            return []
        t0 = time.time()
        try:
            mt5.symbol_select(symbol, True)
            rows = mt5.copy_rates_from_pos(symbol, _tf_const(timeframe), 0, n_bars)
        except Exception as e:
            log.warning("copy_rates failed for %s %s: %s", symbol, timeframe, e)
            return []

        n_rows = len(rows) if rows is not None and hasattr(rows, "__len__") else 0
        is_empty = rows is None or n_rows == 0
        log.info("MT5 DATA: symbol=%s timeframe=%s type=%s rows=%d empty=%s",
                 symbol, timeframe, type(rows).__name__, n_rows, is_empty)

        if is_empty:
            return []

        bars = self._rows_to_bars(rows)
        if len(bars) > 0:
            self.last_request_ts = time.time()
            self.last_request_ms = (time.time() - t0) * 1000.0
        return bars

    def copy_rates_range(self, symbol: str, timeframe: str,
                         start_ts: float, end_ts: float) -> List[Bar]:
        if not self._connected or not MT5_PACKAGE_AVAILABLE:
            return []
        t0 = time.time()
        try:
            mt5.symbol_select(symbol, True)
            s = datetime.fromtimestamp(start_ts, tz=timezone.utc)
            e = datetime.fromtimestamp(end_ts, tz=timezone.utc)
            rows = mt5.copy_rates_range(symbol, _tf_const(timeframe), s, e)
        except Exception as ex:
            log.warning("copy_rates_range failed for %s %s: %s", symbol, timeframe, ex)
            return []

        n_rows = len(rows) if rows is not None and hasattr(rows, "__len__") else 0
        is_empty = rows is None or n_rows == 0
        log.info("MT5 DATA: symbol=%s timeframe=%s type=%s rows=%d empty=%s",
                 symbol, timeframe, type(rows).__name__, n_rows, is_empty)

        if is_empty:
            return []

        bars = self._rows_to_bars(rows)
        if len(bars) > 0:
            self.last_request_ts = time.time()
            self.last_request_ms = (time.time() - t0) * 1000.0
        return bars

    def latest_tick(self, symbol: str) -> Optional[Tick]:
        if not self._connected:
            return None
        try:
            t = mt5.symbol_info_tick(symbol)
        except Exception:
            return None
        if t is None:
            return None
        ts = float(t.time)
        if ts < 10 ** 9:  # some builds return ms
            ts /= 1000.0
        self.last_tick_ts = max(self.last_tick_ts, time.time())
        return Tick(ts=ts, bid=float(t.bid), ask=float(t.ask),
                    last=float(getattr(t, "last", 0) or 0),
                    volume=int(getattr(t, "volume", 0) or 0), source=self.source)

    # ---------- execution ----------
    def simulate_market_order(self, symbol: str, side: str, lots: float,
                              price_hint: Optional[float] = None) -> OrderResult:
        """Paper fill computed from the REAL live MT5 quote (no order sent)."""
        t0 = time.time()
        tick = self.latest_tick(symbol)
        if tick is None:
            return OrderResult(ok=False, comment="no live tick from MT5",
                               source=self.source)
        price = tick.ask if side == "buy" else tick.bid
        delay_ms = (time.time() - t0) * 1000.0 + 90.0  # quote latency + assumed broker latency
        return OrderResult(ok=True, order_id=None, exec_price=price, exec_ts=time.time(),
                           requested_price=price, slippage_points=0.0, delay_ms=delay_ms,
                           comment="paper fill on real MT5 quote", source="MT5_DEMO_QUOTE")

    def real_market_order(self, symbol: str, side: str, lots: float) -> OrderResult:
        """Send a REAL market order to MT5. Only callable through the risk layer."""
        if not self._connected or not MT5_PACKAGE_AVAILABLE:
            return OrderResult(ok=False, comment="MT5 not connected",
                               rejected_by="BRIDGE", source=self.source)
        t0 = time.time()
        tick = self.latest_tick(symbol)
        if tick is None:
            return OrderResult(ok=False, comment="no tick", rejected_by="BRIDGE",
                               source=self.source)
        order_type = mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL
        price = tick.ask if side == "buy" else tick.bid
        request = {
            "action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": float(lots),
            "type": order_type, "price": price, "deviation": 20,
            "magic": 777001, "comment": "evolab", "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        try:
            result = mt5.order_send(request)
        except Exception as e:
            return OrderResult(ok=False, comment=f"order_send exception: {e}",
                               rejected_by="BRIDGE", source=self.source)
        exec_ts = time.time()
        if result is None:
            return OrderResult(ok=False, comment="order_send returned None",
                               rejected_by="BRIDGE", source=self.source)
        ok = result.retcode == mt5.TRADE_RETCODE_DONE
        ep = float(result.price or 0)
        return OrderResult(ok=ok, order_id=getattr(result, "order", None),
                           exec_price=ep, exec_ts=exec_ts, requested_price=price,
                           slippage_points=(abs(ep - price) / 0.01) if ep else None,
                           delay_ms=(exec_ts - t0) * 1000.0, retcode=result.retcode,
                           comment=getattr(result, "comment", ""), source=self.source)
