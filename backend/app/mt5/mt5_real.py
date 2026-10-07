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
import traceback
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

    def switch_to_path(self, path: str, *, login: int = 0, password: str = "",
                       server: str = "") -> Dict:
        """V5.1a-next §B — move this bridge onto a specific terminal/account.

        Shutdown the current session, initialize the requested terminal and probe
        what it is logged into.  Read-only: nothing is ever sent to the broker.
        The returned report names DEMO/REAL/CONTEST per the *probed* account, so
        the UI can show an account selector without guessing.
        """
        requested = str(path or "").strip()
        report: Dict = {"requested_path": requested, "connected": False,
                        "login": None, "server": None, "trade_mode": None,
                        "trade_mode_name": "UNKNOWN", "account_kind": "UNKNOWN",
                        "demo": None, "terminal": None, "company": None,
                        "build": None, "path": "", "last_error": self._last_error}
        if not requested:
            report["last_error"] = "no terminal path supplied"
            return report
        if not MT5_PACKAGE_AVAILABLE:
            report["last_error"] = (f"MetaTrader5 python package not available "
                                    f"({MT5_IMPORT_ERROR}). pip install MetaTrader5 on Windows.")
            return report
        # 1. tear down whatever we were using before switching
        self.disconnect()
        # 2. initialize exactly the requested terminal (never auto-discovery here:
        #    the operator asked for *this* account)
        self._path = requested
        if login:
            self._login, self._password, self._server = int(login), password, server
        try:
            ok = mt5.initialize(path=requested, timeout=self._timeout,
                                **({"login": int(login), "password": password, "server": server}
                                   if login else {}))
        except Exception as e:
            ok = False
            self._last_error = f"initialize exception: {e}"
        report["path"] = requested
        if not ok:
            try:
                report["last_error"] = f"mt5.initialize failed: {mt5.last_error()}"
                self._last_error = report["last_error"]
            except Exception:
                pass
            self._connected = False
            return report
        self._connected = True
        self._refresh_identity()
        try:
            ti = mt5.terminal_info()
            if ti is not None:
                report["terminal"] = str(getattr(ti, "name", "") or "")
                report["company"] = str(getattr(ti, "company", "") or "")
            ai = mt5.account_info()
            if ai is not None:
                mode = int(getattr(ai, "trade_mode", -1))
                from .accounts import _trade_mode_name
                report.update({"login": int(getattr(ai, "login", 0) or 0) or None,
                               "server": str(getattr(ai, "server", "") or ""),
                               "trade_mode": mode,
                               "trade_mode_name": _trade_mode_name(mode),
                               "account_kind": _trade_mode_name(mode),
                               "demo": mode == 0})
            else:
                report["last_error"] = ("terminal initialized but no account is logged in "
                                        "(account_info() returned None)")
        except Exception as e:  # pragma: no cover - terminal specific
            report["last_error"] = f"probe failed: {e}"
        try:
            ver = mt5.version()
            if ver and len(ver) > 1:
                report["build"] = int(ver[1])
        except Exception:
            pass
        report["connected"] = True
        return report

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
                          visible=bool(si.visible),
                          trade_mode_raw=int(getattr(si, "trade_mode", 0) or 0),
                          trade_allowed=bool(getattr(si, "trade_allowed", True)),
                          volume_min=float(getattr(si, "volume_min", 0.0) or 0.0),
                          volume_max=float(getattr(si, "volume_max", 0.0) or 0.0),
                          volume_step=float(getattr(si, "volume_step", 0.0) or 0.0),
                          trade_stops_level=int(getattr(si, "trade_stops_level", 0) or 0),
                          freeze_level=int(getattr(si, "freeze_level", 0) or 0),
                          filling_modes=_filling_modes(si),
                          trade_tick_size=float(getattr(si, "trade_tick_size", 0.0) or 0.0) or None,
                          trade_tick_value=float(getattr(si, "trade_tick_value", 0.0) or 0.0) or None,
                          currency_profit=str(getattr(si, "currency_profit", "") or "") or None)

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
                           is_demo=bool(ai.trade_mode == 0), source=self.source,
                           margin_free=float(getattr(ai, "margin_free", 0.0) or 0.0),
                           leverage=int(getattr(ai, "leverage", 0) or 0),
                           name=str(getattr(ai, "name", "") or ""))

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

    # ---------- V4.2 execution primitives ----------
    def check_market_order(self, request: Dict) -> Dict:
        """V5.1a §15 — `mt5.order_check` on the *same* request before it is sent.

        The terminal answers whether the order would be accepted (margin,
        volume step, stop distance, filling mode, market state) and returns its
        own retcode. A non-DONE check is a refusal with the broker's reason —
        the order is then never sent. Nothing is interpreted locally: the
        retcode and comment come straight from the terminal.

        Returns {"ok", "retcode", "raw", "unsupported", "error"}.
        """
        if not self._connected or not MT5_PACKAGE_AVAILABLE:
            return {"ok": False, "unsupported": True, "retcode": None,
                    "error": "MT5 terminal not connected"}
        order_check = getattr(mt5, "order_check", None)
        if order_check is None:                      # older/variant builds
            return {"ok": True, "unsupported": True, "retcode": None,
                    "error": "this MetaTrader5 build exposes no order_check"}
        try:
            checked = order_check(dict(request))
        except Exception as e:  # pragma: no cover - terminal dependent
            log.error("order_check raised: %s", e)
            return {"ok": False, "exception": str(e), "retcode": None, "raw": None}
        if checked is None:
            err = None
            try:
                err = mt5.last_error()
            except Exception:
                pass
            return {"ok": False, "retcode": None, "raw": None,
                    "exception": f"order_check returned None (mt5.last_error={err})"}
        raw = _result_to_dict(checked)
        done = getattr(mt5, "TRADE_RETCODE_DONE", 10009)
        ok = raw.get("retcode") == done
        log.info("[V5.1a] mt5.order_check retcode=%s margin=%s comment=%r",
                 raw.get("retcode"), raw.get("margin"), raw.get("comment"))
        return {"ok": bool(ok), "retcode": raw.get("retcode"), "raw": raw}

    def send_market_order(self, request: Dict) -> Dict:
        """Send a fully-built MT5 order request (market order with real SL/TP).

        V5.1a §15 — the request is first put through the terminal's own
        `order_check`. When that check refuses, nothing is sent and the refusal
        is returned with the terminal's retcode and comment.

        Returns {"ok", "retcode", "raw": <result as dict>, "unsupported",
        "exception", "check": <order_check result>}. This is the ONLY place an
        order leaves the application. Interpretation of the retcode is done by
        app.mt5.execution.
        """
        if not self._connected or not MT5_PACKAGE_AVAILABLE:
            return {"ok": False, "unsupported": True, "retcode": None, "called": False,
                    "error": "MT5 terminal not connected",
                    "diagnostic": self._execution_diagnostic(request, called=False,
                                                             phase="BRIDGE_NOT_CONNECTED")}
        check = self.check_market_order(request)
        if not check.get("unsupported") and not check.get("ok"):
            raw_check = check.get("raw") or {}
            return {"ok": False, "retcode": check.get("retcode"), "raw": raw_check,
                    "check": check, "request": dict(request), "called": False,
                    "refused_by": "mt5.order_check",
                    "error": (raw_check.get("comment")
                              or f"order_check refused the order (retcode {check.get('retcode')})"),
                    "diagnostic": self._execution_diagnostic(request, called=False,
                                                             phase="ORDER_CHECK_REFUSED",
                                                             check=check)}
        try:
            result = mt5.order_send(dict(request))
        except Exception as e:  # pragma: no cover - terminal dependent
            log.error("order_send raised: %s", e)
            return {"ok": False, "exception": str(e), "exception_type": type(e).__name__,
                    "retcode": None, "raw": None, "called": True, "call_count": 1,
                    "last_error": self._last_error_safe(),
                    "request": dict(request), "check": check,
                    "diagnostic": self._execution_diagnostic(
                        request, called=True, phase="ORDER_SEND_RAISED", check=check,
                        exception={"type": type(e).__name__, "message": str(e),
                                   "traceback_tail": traceback.format_exc().strip().splitlines()[-6:]})}
        if result is None:
            # The one case the operator must never see as a generic "UNKNOWN": the
            # terminal accepted the call but produced no result object at all.
            err = self._last_error_safe()
            log.error("order_send returned None (last_error=%s)", err)
            return {"ok": False, "retcode": None, "raw": None, "called": True, "call_count": 1,
                    "last_error": err,
                    "exception": f"order_send returned None (mt5.last_error={err})",
                    "exception_type": "NoResult",
                    "request": dict(request), "check": check,
                    "diagnostic": self._execution_diagnostic(
                        request, called=True, phase="ORDER_SEND_NO_RESULT", check=check,
                        last_error=err)}
        raw = _result_to_dict(result)
        log.info("[V4.2] mt5.order_send retcode=%s order=%s deal=%s price=%s vol=%s sl=%s tp=%s comment=%r",
                 raw.get("retcode"), raw.get("order"), raw.get("deal"), raw.get("price"),
                 raw.get("volume"), raw.get("sl"), raw.get("tp"), raw.get("comment"))
        return {"ok": bool(raw.get("retcode")), "retcode": raw.get("retcode"), "raw": raw,
                "check": check, "request": dict(request), "called": True, "call_count": 1,
                "last_error": self._last_error_safe(),
                "diagnostic": self._execution_diagnostic(request, called=True,
                                                         phase="ORDER_SEND_RETURNED_RESULT",
                                                         check=check)}

    # ---------- execution diagnostics (V5.1a-next §A) ----------
    @staticmethod
    def _last_error_safe():
        """mt5.last_error() as a list, or None when it cannot be read."""
        try:
            err = mt5.last_error()
            if err is None:
                return None
            try:
                return list(err)
            except TypeError:
                return [str(err)]
        except Exception:
            return None

    def _execution_diagnostic(self, request: Dict, *, called: bool, phase: str,
                              check: Optional[Dict] = None,
                              last_error: Any = None,
                              exception: Optional[Dict] = None) -> Dict:
        """Everything needed to explain an order_send outcome without guessing.

        Read-only: re-reads the terminal/account/symbol state and returns it
        beside the request that was (or was not) sent. It never sends anything and
        never retries — the caller decides, the operator verifies in MT5.
        """
        diag: Dict = {
            "phase": phase,
            "order_send_called": bool(called),
            "call_count": 1 if called else 0,
            "request": dict(request or {}),
            "last_error": last_error,
            "exception": exception,
            "check": ({"ok": check.get("ok"), "retcode": check.get("retcode"),
                       "unsupported": check.get("unsupported"),
                       "comment": ((check.get("raw") or {}).get("comment")
                                   if isinstance(check.get("raw"), dict) else None)}
                      if isinstance(check, dict) else None),
        }
        # terminal identity + flags
        try:
            ti = mt5.terminal_info() if MT5_PACKAGE_AVAILABLE else None
        except Exception as e:
            ti = None
            diag["terminal_info_error"] = f"{type(e).__name__}: {e}"
        if ti is not None:
            diag["terminal"] = {
                "name": getattr(ti, "name", None), "company": getattr(ti, "company", None),
                "path": getattr(ti, "path", None), "build": getattr(ti, "build", None),
                "connected": getattr(ti, "connected", None),
                "trade_allowed": getattr(ti, "trade_allowed", None),
                "tradeapi_disabled": getattr(ti, "tradeapi_disabled", None),
                "dlls_allowed": getattr(ti, "dlls_allowed", None),
            }
        diag["bridge"] = {"name": self.name, "source": self.source,
                          "connected": bool(self._connected),
                          "package_importable": bool(MT5_PACKAGE_AVAILABLE),
                          "package_error": MT5_IMPORT_ERROR or None,
                          "login": self._login or getattr(self, "account_id", "") or None}
        # account (safe fields only)
        try:
            acct = mt5.account_info() if MT5_PACKAGE_AVAILABLE else None
        except Exception:
            acct = None
        if acct is not None:
            diag["account"] = {
                "login": getattr(acct, "login", None), "server": getattr(acct, "server", None),
                "trade_mode": getattr(acct, "trade_mode", None),
                "trade_mode_name": {0: "DEMO", 1: "CONTEST", 2: "REAL"}.get(
                    getattr(acct, "trade_mode", None), "unknown"),
                "currency": getattr(acct, "currency", None),
                "balance": getattr(acct, "balance", None),
                "equity": getattr(acct, "equity", None),
                "margin_free": getattr(acct, "margin_free", None),
                "leverage": getattr(acct, "leverage", None),
                "trade_allowed": getattr(acct, "trade_allowed", None),
                "trade_expert": getattr(acct, "trade_expert", None),
            }
        # symbol state (the request's own symbol)
        symbol = str((request or {}).get("symbol") or "")
        try:
            mt5.symbol_select(symbol, True)
            si = mt5.symbol_info(symbol) if MT5_PACKAGE_AVAILABLE else None
        except Exception as e:
            si = None
            diag["symbol_error"] = f"{type(e).__name__}: {e}"
        if si is not None:
            diag["symbol"] = {
                "symbol": symbol, "visible": getattr(si, "visible", None),
                "digits": getattr(si, "digits", None), "point": getattr(si, "point", None),
                "trade_tick_size": getattr(si, "trade_tick_size", None),
                "trade_tick_value": getattr(si, "trade_tick_value", None),
                "volume_min": getattr(si, "volume_min", None),
                "volume_max": getattr(si, "volume_max", None),
                "volume_step": getattr(si, "volume_step", None),
                "trade_stops_level": getattr(si, "trade_stops_level", None),
                "freeze_level": getattr(si, "freeze_level", None),
                "trade_mode": getattr(si, "trade_mode", None),
                "trade_mode_name": {0: "DISABLED", 1: "LONG_ONLY", 2: "SHORT_ONLY",
                                    3: "CLOSE_ONLY", 4: "FULL"}.get(
                    getattr(si, "trade_mode", None), "unknown"),
                "trade_allowed": getattr(si, "trade_allowed", None),
                "fill_modes": _filling_modes(si),
            }
            req_fill = (request or {}).get("type_filling")
            diag["fill_mode_used"] = req_fill
            diag["fill_mode_supported"] = (req_fill in _filling_modes(si)) if req_fill is not None else None
        else:
            diag["symbol"] = {"symbol": symbol, "available": False}
        return diag

    def positions_get(self, ticket: Optional[int] = None, symbol: Optional[str] = None) -> List[Dict]:
        """Open positions from the terminal (used to verify an executed order)."""
        if not self._connected or not MT5_PACKAGE_AVAILABLE:
            return []
        try:
            if ticket is not None:
                rows = mt5.positions_get(ticket=int(ticket))
            elif symbol:
                rows = mt5.positions_get(symbol=symbol)
            else:
                rows = mt5.positions_get()
        except Exception as e:
            log.warning("positions_get failed: %s", e)
            return []
        return [_pos_to_dict(p) for p in (rows or [])]

    def orders_get(self, ticket: Optional[int] = None, symbol: Optional[str] = None) -> List[Dict]:
        """Pending orders from the terminal."""
        if not self._connected or not MT5_PACKAGE_AVAILABLE:
            return []
        try:
            if ticket is not None:
                rows = mt5.orders_get(ticket=int(ticket))
            elif symbol:
                rows = mt5.orders_get(symbol=symbol)
            else:
                rows = mt5.orders_get()
        except Exception as e:
            log.warning("orders_get failed: %s", e)
            return []
        return [_order_to_dict(o) for o in (rows or [])]

    def close_position(self, ticket: int, comment: str = "evolab-demo-close") -> Dict:
        """Close a position by ticket (spec V4.2 test hygiene). Demo-guarded by callers."""
        if not self._connected or not MT5_PACKAGE_AVAILABLE:
            return {"ok": False, "error": "MT5 terminal not connected"}
        pos = _find(self.positions_get(ticket=int(ticket)), ticket)
        if pos is None:
            return {"ok": False, "error": f"position {ticket} not found"}
        tick = self.latest_tick(pos["symbol"])
        if tick is None:
            return {"ok": False, "error": "no tick for closing"}
        side_buy = str(pos.get("type", 0)) in ("0", "buy") or pos.get("type") == 0
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos["symbol"],
            "volume": float(pos["volume"]),
            "type": mt5.ORDER_TYPE_SELL if side_buy else mt5.ORDER_TYPE_BUY,
            "position": int(ticket),
            "price": float(tick.bid if side_buy else tick.ask),
            "deviation": 20,
            "magic": int(pos.get("magic") or 777000),
            "comment": comment[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": int(getattr(mt5, "ORDER_FILLING_IOC", 1)),
        }
        res = self.send_market_order(request)
        raw = res.get("raw") or {}
        return {"ok": res.get("retcode") == mt5.TRADE_RETCODE_DONE, "retcode": res.get("retcode"),
                "request": request, "raw": raw,
                "error": res.get("error") or res.get("exception")}

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
            return OrderResult(ok=False,
                               comment=(f"order_send exception {type(e).__name__}: {e} "
                                        f"| mt5.last_error={self._last_error_safe()}"),
                               rejected_by="BRIDGE", source=self.source)
        exec_ts = time.time()
        if result is None:
            # never a bare "returned None": the terminal's own last_error is the
            # diagnostic, and the outcome is uncertain (the order may exist).
            return OrderResult(ok=False,
                               comment=(f"order_send returned no result object "
                                        f"(mt5.last_error={self._last_error_safe()}) — "
                                        f"verify open positions/orders in MT5"),
                               rejected_by="BRIDGE", source=self.source)
        ok = result.retcode == mt5.TRADE_RETCODE_DONE
        ep = float(result.price or 0)
        return OrderResult(ok=ok, order_id=getattr(result, "order", None),
                           exec_price=ep, exec_ts=exec_ts, requested_price=price,
                           slippage_points=(abs(ep - price) / 0.01) if ep else None,
                           delay_ms=(exec_ts - t0) * 1000.0, retcode=result.retcode,
                           comment=getattr(result, "comment", ""), source=self.source)

def _filling_modes(si) -> List[int]:
    """Supported ORDER_FILLING_* modes from symbol_info.filling_mode bitmask."""
    out: List[int] = []
    try:
        mask = int(getattr(si, "filling_mode", 0) or 0)
    except Exception:
        return out
    for name, bit in (("ORDER_FILLING_FOK", 1), ("ORDER_FILLING_IOC", 2)):
        if mask & bit:
            out.append(int(getattr(mt5, name, 0 if name.endswith("FOK") else 1)))
    if not out:
        # Some builds report 0 -> fall back to the commonly supported modes.
        out = [int(getattr(mt5, "ORDER_FILLING_RETURN", 2)),
               int(getattr(mt5, "ORDER_FILLING_IOC", 1))]
    return out


def _result_to_dict(result) -> Dict:
    """Serialise an MT5 order_send result (namedtuple) into a plain dict."""
    out: Dict = {}
    try:
        fields = getattr(result, "_fields", None) or getattr(result, "__dict__", {})
        for f in fields:
            out[f] = getattr(result, f, None)
    except Exception:
        pass
    if not out:
        try:
            out = {"_repr": repr(result)}
        except Exception:
            out = {}
    return out


def _pos_to_dict(p) -> Dict:
    return {"ticket": int(getattr(p, "ticket", 0) or 0),
            "symbol": str(getattr(p, "symbol", "") or ""),
            "type": int(getattr(p, "type", -1)),
            "volume": float(getattr(p, "volume", 0.0) or 0.0),
            "price_open": float(getattr(p, "price_open", 0.0) or 0.0),
            "price_current": float(getattr(p, "price_current", 0.0) or 0.0),
            "sl": float(getattr(p, "sl", 0.0) or 0.0),
            "tp": float(getattr(p, "tp", 0.0) or 0.0),
            "profit": float(getattr(p, "profit", 0.0) or 0.0),
            "magic": int(getattr(p, "magic", 0) or 0),
            "time": int(getattr(p, "time", 0) or 0),
            "comment": str(getattr(p, "comment", "") or "")}


def _order_to_dict(o) -> Dict:
    return {"ticket": int(getattr(o, "ticket", 0) or 0),
            "symbol": str(getattr(o, "symbol", "") or ""),
            "type": int(getattr(o, "type", -1)),
            "volume_initial": float(getattr(o, "volume_initial", 0.0) or 0.0),
            "volume_current": float(getattr(o, "volume_current", 0.0) or 0.0),
            "price_open": float(getattr(o, "price_open", 0.0) or 0.0),
            "sl": float(getattr(o, "sl", 0.0) or 0.0),
            "tp": float(getattr(o, "tp", 0.0) or 0.0),
            "magic": int(getattr(o, "magic", 0) or 0),
            "state": int(getattr(o, "state", -1)),
            "time_setup": int(getattr(o, "time_setup", 0) or 0),
            "comment": str(getattr(o, "comment", "") or "")}


def _find(rows: List[Dict], ticket: int) -> Optional[Dict]:
    for r in rows or []:
        if int(r.get("ticket", -1)) == int(ticket):
            return r
    return None
