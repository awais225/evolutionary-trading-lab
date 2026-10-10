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
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .bridge import (MarketBridge, Bar, Tick, SymbolInfo, AccountInfo,
                     OrderResult, TIMEFRAME_MINUTES)
from .config import get_saved_terminal_path, save_mt5_config, load_mt5_config
from .discovery import discover_terminals, validate_terminal_path, normalize_terminal_path
from .order_semantics import (ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN,
                              allowed_filling_by_bitmask, effective_deviation,
                              filling_candidates, filling_name, interpret_check_result,
                              last_error_name, resolve_filling, retcode_name,
                              symbol_filling_bitmask)

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

# ---------------------------------------------------------------------------
# V5.3 §2.1 — ONE session lock for every operation that can create or destroy
# the MetaTrader5 IPC session (initialize / shutdown) and for the order path
# itself.  Several features legitimately re-initialize the terminal (account
# discovery probes every installed terminal, the account selector switches
# terminal, the connection monitor reconnects).  Without this lock a probe that
# shuts the terminal down can land *between* `order_check` and `order_send`,
# and `order_send` then answers `None` — indistinguishable from a broker
# refusal unless the session state is captured.  The lock makes that
# interleaving impossible; it never retries an order.
# ---------------------------------------------------------------------------
SESSION_LOCK = threading.RLock()
_SESSION: Dict[str, Any] = {
    "initialized": False,      # the MetaTrader5 package has a live IPC session
    "path": "",                # terminal executable the session was created with
    "login": 0, "server": "",  # account the session was created with
    "generation": 0,           # bumped on every initialize/shutdown
    "last_initialize_ts": 0.0,
    "last_shutdown_ts": 0.0,
    "last_shutdown_by": "",
}


def session_snapshot() -> Dict[str, Any]:
    """The IPC session as the binding sees it right now (never guessed)."""
    snap: Dict[str, Any] = dict(_SESSION)
    snap["package_importable"] = bool(MT5_PACKAGE_AVAILABLE)
    snap["python"] = sys.executable
    snap["python_version"] = sys.version.split()[0]
    snap["package_version"] = (getattr(mt5, "__version__", None)
                              if MT5_PACKAGE_AVAILABLE else None)
    snap["terminal_info_available"] = None
    snap["terminal_info_supported"] = None
    snap["terminal_connected"] = None
    snap["terminal_trade_allowed"] = None
    snap["account_login"] = None
    if MT5_PACKAGE_AVAILABLE:
        getter = getattr(mt5, "terminal_info", None)
        snap["terminal_info_supported"] = callable(getter)
        ti = None
        if callable(getter):
            try:
                ti = getter()
            except Exception as e:                                # pragma: no cover
                ti = None
                snap["terminal_info_error"] = f"{type(e).__name__}: {e}"
                snap["terminal_info_supported"] = False
        snap["terminal_info_available"] = (ti is not None) if callable(getter) else None
        if ti is not None:
            snap["terminal_connected"] = getattr(ti, "connected", None)
            snap["terminal_trade_allowed"] = getattr(ti, "trade_allowed", None)
            snap["terminal_path"] = getattr(ti, "path", None)
            snap["terminal_build"] = getattr(ti, "build", None)
        try:
            ai = mt5.account_info()
            snap["account_login"] = getattr(ai, "login", None) if ai is not None else None
        except Exception:                                          # pragma: no cover
            pass
        try:
            snap["last_error"] = mt5.last_error()
        except Exception:                                          # pragma: no cover
            snap["last_error"] = None
    return snap


def _session_mark(*, initialized: bool, path: str = "", login: int = 0, server: str = "",
                  by: str = "") -> None:
    with SESSION_LOCK:
        _SESSION["initialized"] = bool(initialized)
        if path:
            _SESSION["path"] = path
        _SESSION["login"] = int(login or 0) if login else _SESSION["login"]
        if server:
            _SESSION["server"] = server
        _SESSION["generation"] = int(_SESSION.get("generation") or 0) + 1
        if initialized:
            _SESSION["last_initialize_ts"] = time.time()
        else:
            _SESSION["last_shutdown_ts"] = time.time()
            _SESSION["last_shutdown_by"] = by or "unknown"


def restore_session_from_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Re-create the IPC session described by ``snapshot`` (V5.3 §2.1).

    Used after a read-only probe (account discovery, terminal switch) shut the
    terminal down: the *trading* session must be back before the session lock is
    released, or the next `order_send` would be issued against a dead session and
    answer `None`. Takes no lock of its own — callers hold ``SESSION_LOCK``.
    """
    out: Dict[str, Any] = {"restored": False, "reason": "", "path": snapshot.get("path") or "",
                           "login": snapshot.get("login") or 0,
                           "server": snapshot.get("server") or ""}
    if not MT5_PACKAGE_AVAILABLE:
        out["reason"] = "MetaTrader5 package not importable"
        return out
    kwargs: Dict[str, Any] = {"timeout": 60000}
    if out["login"]:
        # the password is NEVER stored in a snapshot; a same-terminal re-initialize
        # uses the terminal's existing session (login only)
        kwargs.update({"login": int(out["login"]), "server": out["server"]})
    try:
        ok = (mt5.initialize(path=str(out["path"]), **kwargs) if out["path"]
              else mt5.initialize(**kwargs))
    except Exception as e:                                        # pragma: no cover
        ok = False
        out["reason"] = f"initialize raised: {type(e).__name__}: {e}"
    if not ok:
        try:
            out["reason"] = out["reason"] or f"mt5.initialize failed: {mt5.last_error()}"
        except Exception:                                          # pragma: no cover
            out["reason"] = out["reason"] or "mt5.initialize failed"
        return out
    _session_mark(initialized=True, path=out["path"], login=int(out["login"] or 0),
                  server=out["server"], by="restore_session_from_snapshot")
    out["restored"] = True
    try:
        ai = mt5.account_info()
        out["account_login"] = getattr(ai, "login", None) if ai is not None else None
        out["account_ok"] = (out["account_login"] is not None)
    except Exception:                                              # pragma: no cover
        out["account_ok"] = None
    log.info("[V5.3] restored the live MT5 session after a probe (path=%s login=%s)",
             out["path"], out["login"])
    return out


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
        # V5.2 §1 — a saved *folder* must not be handed to initialize(): it fails
        # with -10003 "Process create failed". Resolve it to the executable first.
        if target_path:
            target_path, _path_note = normalize_terminal_path(target_path)
            self._path = target_path
            if _path_note:
                log.info("terminal path normalized: %s (%s)", target_path, _path_note)

        kwargs = {"timeout": self._timeout}
        if self._login:
            kwargs.update({"login": int(self._login), "password": self._password,
                           "server": self._server})

        ok = False
        used_path = ""
        # V5.3 §2.1 — the whole initialize sequence is serialized against the order
        # path and against every other session user (account probes, account switch,
        # monitor reconnect): an initialize/shutdown can never land mid-order.
        with SESSION_LOCK:
            # Try target path first if available
            if target_path:
                try:
                    ok = mt5.initialize(path=str(target_path), **kwargs)
                    if ok:
                        used_path = str(target_path)
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
                                used_path = str(t_path)
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
        _session_mark(initialized=True, path=used_path or self._path,
                      login=int(self._login or 0), server=self._server, by="connect")
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
        # 1. tear down whatever we were using before switching (recorded)
        self.disconnect(by="switch_to_path")
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
        _session_mark(initialized=True, path=requested, login=int(login or 0),
                      server=str(server or ""), by="switch_to_path")
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

    def disconnect(self, by: str = "bridge.disconnect") -> None:
        # V5.3 §2.1 — a shutdown may only happen under the session lock, and it is
        # recorded: "order_send answered None" must be attributable to whoever tore
        # the session down, with a timestamp.
        with SESSION_LOCK:
            if self._connected and MT5_PACKAGE_AVAILABLE:
                try:
                    mt5.shutdown()
                except Exception:
                    pass
                _session_mark(initialized=False, by=by)
        self._connected = False
        self._terminal_build = None
        self._terminal_company = ""
        self._terminal_path = ""
        self._terminal_name = ""

    def session_state(self) -> Dict[str, Any]:
        """V5.3 §2.1 — this bridge's IPC session, as the binding reports it."""
        snap = session_snapshot()
        snap["bridge_connected"] = bool(self._connected)
        snap["bridge_path"] = self._path or snap.get("path")
        return snap

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
                          filling_mode_raw=(int(getattr(si, "filling_mode"))
                                            if getattr(si, "filling_mode", None) is not None
                                            else None),
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
        """`mt5.order_check` on the *same* request — DIAGNOSTIC-ONLY (V6).

        The terminal answers whether the order would be accepted (margin,
        volume step, stop distance, filling mode, market state). This verdict is
        recorded with the result for the panel/forensics, but it NEVER gates the
        send: an unevaluable check (None) is not a broker refusal, and only the
        real `mt5.order_send()` answer decides the order's fate. Nothing is
        interpreted locally: the retcode and comment come straight from the
        terminal.

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
        verdict = interpret_check_result(raw, mt5)
        log.info("[V5.2] mt5.order_check retcode=%s (%s) margin=%s comment=%r -> %s",
                 verdict["retcode"], verdict["retcode_name"], verdict["margin"],
                 verdict["comment"], "PASS" if verdict["ok"] else "REFUSED")
        return {"ok": bool(verdict["ok"]), "retcode": verdict["retcode"], "raw": raw,
                "verdict": verdict}

    def _ensure_order_session(self) -> Dict[str, Any]:
        """V5.3 §2.1 — is there a *live* IPC session for the order we are about to send?

        `order_check` is answered by the terminal client and can pass while the
        Python binding has no live session at all; `order_send` then returns
        `None`. Several features re-initialize the terminal (account probes, the
        account selector, the connection monitor), so before sending we state
        whether a session exists, and — only if it does not — re-establish the
        SAME terminal/account once. This recovers a session; it never re-sends an
        order (nothing has been sent yet at this point).
        """
        out: Dict[str, Any] = {"checked": True, "recovered": False, "ok": True,
                               "session_before": session_snapshot()}
        if not MT5_PACKAGE_AVAILABLE:
            out["ok"] = False
            out["reason"] = "the MetaTrader5 package is not importable in this interpreter"
            return out
        if out["session_before"].get("terminal_info_available"):
            return out
        if out["session_before"].get("terminal_info_supported") is not True:
            # This binding cannot answer the question: report it and do NOT block —
            # an unknown must never refuse an order (the send is still instrumented,
            # so a silent None is captured with its last_error either way).
            out["reason"] = ("this MetaTrader5 build exposes no terminal_info(), so the IPC "
                             "session state cannot be read — proceeding and recording the "
                             "outcome")
            out["unknown"] = True
            return out
        # No session: the terminal was shut down or never initialized here.
        out["recovered_attempt"] = self.connect()
        out["session_after"] = session_snapshot()
        out["recovered"] = bool(out["session_after"].get("terminal_info_available"))
        out["ok"] = out["recovered"]
        if not out["ok"]:
            out["reason"] = ("no live MetaTrader5 session: mt5.terminal_info() is None and "
                             "re-initializing the terminal failed "
                             f"(mt5.last_error={out['session_after'].get('last_error')})")
            log.error("[V5.3] order session unavailable: %s", out["reason"])
        else:
            self._connected = True
            log.warning("[V5.3] order session was gone (shutdown by %s at %s) - re-initialized "
                        "before sending; no order had been sent",
                        out["session_before"].get("last_shutdown_by"),
                        out["session_before"].get("last_shutdown_ts"))
        return out

    def verify_sent_order(self, request: Dict, raw_result: Optional[Dict] = None) -> Dict[str, Any]:
        """V5.3 §2.6 — the ACTUAL terminal state after a send (never inferred).

        Positions and orders are read from the terminal and filtered by this
        account's symbol + magic, plus the tickets the broker returned. This is
        the only thing that may be described as "the order exists".
        """
        symbol = str((request or {}).get("symbol") or "")
        magic = int((request or {}).get("magic") or 0)
        want_ticket = None
        want_deal = None
        if isinstance(raw_result, dict):
            for key in ("order", "request_id"):
                try:
                    val = int(raw_result.get(key) or 0)
                except (TypeError, ValueError):
                    val = 0
                if val and want_ticket is None:
                    want_ticket = val
            try:
                want_deal = int(raw_result.get("deal") or 0) or None
            except (TypeError, ValueError):
                want_deal = None
        rep: Dict[str, Any] = {"queried": True, "symbol": symbol, "magic": magic,
                               "by_ticket": want_ticket, "by_deal": want_deal,
                               "positions": [], "orders": [], "verified": False,
                               "errors": []}
        try:
            rows = mt5.positions_get(symbol=symbol) if (MT5_PACKAGE_AVAILABLE and symbol) else None
            for r in (rows or []):
                d = _pos_to_dict(r)
                if int(d.get("magic") or 0) in (magic, 0) or (want_ticket and
                                                              int(d.get("ticket") or 0) == want_ticket):
                    rep["positions"].append(d)
        except Exception as e:                                        # pragma: no cover
            rep["errors"].append(f"positions_get: {type(e).__name__}: {e}")
        try:
            rows = mt5.orders_get(symbol=symbol) if (MT5_PACKAGE_AVAILABLE and symbol) else None
            for r in (rows or []):
                d = _order_to_dict(r)
                if int(d.get("magic") or 0) in (magic, 0) or (want_ticket and
                                                              int(d.get("ticket") or 0) == want_ticket):
                    rep["orders"].append(d)
        except Exception as e:                                        # pragma: no cover
            rep["errors"].append(f"orders_get: {type(e).__name__}: {e}")
        rep["verified"] = bool(rep["positions"] or rep["orders"])
        rep["verdict"] = ("an MT5 position/order for this symbol+magic exists in the terminal"
                          if rep["verified"] else
                          "NO position and NO order for this symbol+magic exists in the terminal")
        return rep

    def send_market_order(self, request: Dict) -> Dict:
        """Send a fully-built MT5 order request (market order with real SL/TP).

        V6 (MT5 handoff) — `order_check` runs for DIAGNOSTICS ONLY and can never
        stop the send; the order goes out through the proven filling-mode
        fallback chain (real `mt5.order_send()` calls, 10029/10030 advance the
        chain) and the ACTUAL broker outcome is what gets reported.

        V5.3 §2.1 — the whole sequence (session check -> order_check -> order_send
        -> post-send verification) runs inside the session lock, so a shutdown/
        initialize from anywhere else cannot land in the middle of it; and the
        binding-side outcome is recorded verbatim: `ORDER_SEND_RETURNED_NONE`,
        `ORDER_SEND_EXCEPTION`, `ORDER_SEND_UNUSABLE_RESULT` or
        `ORDER_SEND_RETURNED_RESULT` with `mt5.last_error()` BEFORE and AFTER the
        call. Nothing is retried, nothing is invented.

        Returns {"ok", "retcode", "raw": <result as dict>, "unsupported",
        "exception", "check": <order_check result>, "forensics", "session",
        "verification"}. This is the ONLY place an order leaves the application.
        """
        if not self._connected or not MT5_PACKAGE_AVAILABLE:
            return {"ok": False, "unsupported": True, "retcode": None, "called": False,
                    "error": "MT5 terminal not connected",
                    "session": session_snapshot(),
                    "diagnostic": self._execution_diagnostic(request, called=False,
                                                             phase="BRIDGE_NOT_CONNECTED")}
        with SESSION_LOCK:
            # V5.2 §1/§20 — the filling mode is derived from the symbol's own
            # metadata and confirmed by the terminal's read-only order_check before
            # anything is sent. Never hard-coded, never assumed from the request.
            request, filling_resolution = self._resolve_request_filling(dict(request))
            session = self._ensure_order_session()
            if not session.get("ok"):
                return {"ok": False, "retcode": None, "raw": None, "called": False,
                        "unsupported": False, "session": {**(session or {}), "snapshot": session_snapshot()},
                        "request": dict(request), "filling_resolution": filling_resolution,
                        "error": session.get("reason"),
                        "exception_type": "NoSession",
                        "diagnostic": self._execution_diagnostic(
                            request, called=False, phase="ORDER_SESSION_UNAVAILABLE",
                            filling=filling_resolution)}
            check = self.check_market_order(request)
            # V6 (MT5 handoff) — order_check is DIAGNOSTIC-ONLY. It must never
            # gate the proven order_send path: an unevaluable check (None) is NOT
            # a broker refusal, and even an explicit preflight refusal is only an
            # opinion — the broker's own order_send answer is the truth. Nothing
            # below may return early because of `check`.
            if not check.get("unsupported") and not check.get("ok"):
                log.info("order_check preflight did not pass (retcode=%s) — recorded as a "
                         "diagnostic; the order is STILL SENT and the broker decides",
                         check.get("retcode"))
            check = dict(check)
            check["role"] = ("diagnostic-only (V6: order_check never gates order_send)")
            check["gate"] = False
            # ------------------------------------------------------------------
            # V6 (MT5 handoff) — THE calls: the filling-mode fallback chain, each
            # mode a REAL mt5.order_send(). Only the filling retcodes 10029/10030
            # advance the chain; None/exception/any other retcode stops it and is
            # reported verbatim. Nothing is retried blindly.
            # ------------------------------------------------------------------
            chain = self._send_with_filling_fallback(
                dict(request), _fallback_sequence(request.get("type_filling")))
            request = chain["request"]
            result = chain["result"] if chain["result"] is not None else chain["last_res"]
            raised = chain["raised"]
            tb_tail = chain["tb_tail"]
            elapsed_ms = chain["elapsed_ms"]
            last_error_before = chain["last_error_before"]
            last_error_after = chain["last_error_after"]
            attempts = chain["attempts"]
            call_count = max(1, len(attempts))
            result_repr = None
            if result is not None:
                try:
                    result_repr = repr(result)[:2000]
                except Exception as e:                                # pragma: no cover
                    result_repr = f"<repr failed: {type(e).__name__}: {e}>"
            forensics: Dict[str, Any] = {
                "order_send_called": True, "call_count": call_count,
                "order_send_attempts": attempts,
                "order_send_returned": result is not None,
                "order_send_result_type": type(result).__name__ if result is not None else None,
                "order_send_result_repr": result_repr,
                "mt5_last_error_before": last_error_before,
                "mt5_last_error_after": last_error_after,
                "exception_type": (type(raised).__name__ if raised is not None else None),
                "exception_message": (str(raised) if raised is not None else None),
                "traceback_tail": tb_tail,
                "elapsed_ms": elapsed_ms,
                "request": dict(request),
                "type_filling": request.get("type_filling"),
                "type_filling_name": filling_name(request.get("type_filling")),
                "outcome": ("ORDER_SEND_EXCEPTION" if raised is not None else
                            "ORDER_SEND_RETURNED_NONE" if result is None else
                            "ORDER_SEND_RETURNED_RESULT"),
            }
            if raised is not None:
                return {"ok": False, "retcode": None, "raw": None, "called": True, "call_count": call_count,
                        "exception": str(raised), "exception_type": type(raised).__name__,
                        "last_error": last_error_after, "forensics": forensics, "session": {**(session or {}), "snapshot": session_snapshot()},
                        "request": dict(request), "check": check,
                        "filling_resolution": filling_resolution,
                        "diagnostic": self._execution_diagnostic(
                            request, called=True, phase="ORDER_SEND_EXCEPTION",
                            filling=filling_resolution, check=check,
                            exception={"type": type(raised).__name__, "message": str(raised),
                                       "traceback_tail": tb_tail},
                            forensics=forensics)}
            if result is None:
                # §2.1 — the one case that must never be summarised as "UNKNOWN": the
                # call produced no result object at all. The binding's own last_error()
                # after the call travels with it.
                log.error("order_send returned None (last_error_before=%s last_error_after=%s)",
                          last_error_before, last_error_after)
                return {"ok": False, "retcode": None, "raw": None, "called": True, "call_count": call_count,
                        "last_error": last_error_after,
                        "exception": (f"order_send returned None "
                                      f"(mt5.last_error={last_error_after})"),
                        "exception_type": "NoResult", "forensics": forensics, "session": {**(session or {}), "snapshot": session_snapshot()},
                        "filling_resolution": filling_resolution,
                        "request": dict(request), "check": check,
                        "diagnostic": self._execution_diagnostic(
                            request, called=True, phase="ORDER_SEND_RETURNED_NONE", check=check,
                            last_error=last_error_after, filling=filling_resolution,
                            forensics=forensics)}
            raw = _result_to_dict(result)
            if not isinstance(raw.get("retcode"), int) or isinstance(raw.get("retcode"), bool):
                # V5.3 §1 — the terminal ANSWERED, but with an object this binding
                # cannot read (no usable retcode). That is not a broker result.
                err = last_error_after
                log.error("order_send returned an unreadable result (retcode missing, keys=%s, "
                          "last_error=%s)", sorted(raw.keys()), err)
                return {"ok": False, "retcode": None, "raw": raw, "called": True, "call_count": call_count,
                        "last_error": err, "exception_type": "UnusableResult",
                        "exception": ("order_send answered with an object that carries no usable "
                                      f"retcode (keys={sorted(raw.keys())})"),
                        "forensics": forensics, "session": {**(session or {}), "snapshot": session_snapshot()},
                        "filling_resolution": filling_resolution,
                        "request": dict(request), "check": check,
                        "diagnostic": self._execution_diagnostic(
                            request, called=True, phase="ORDER_SEND_UNUSABLE_RESULT", check=check,
                            last_error=err, filling=filling_resolution, forensics=forensics)}
            log.info("[V4.2] mt5.order_send retcode=%s order=%s deal=%s price=%s vol=%s sl=%s tp=%s comment=%r",
                     raw.get("retcode"), raw.get("order"), raw.get("deal"), raw.get("price"),
                     raw.get("volume"), raw.get("sl"), raw.get("tp"), raw.get("comment"))
            # §2.6 — the terminal is queried for the ACTUAL state. A retcode is not
            # proof; an existing position/order for this symbol+magic is.
            verification = self.verify_sent_order(request, raw)
            forensics["post_send"] = verification
            return {"ok": raw.get("retcode") == getattr(mt5, "TRADE_RETCODE_DONE", 10009),
                    "retcode": raw.get("retcode"), "raw": raw,
                    "check": check, "filling_resolution": filling_resolution,
                    "request": dict(request), "called": True, "call_count": call_count,
                    "last_error": last_error_after, "forensics": forensics, "session": {**(session or {}), "snapshot": session_snapshot()},
                    "verification": verification,
                    "diagnostic": self._execution_diagnostic(request, called=True,
                                                             phase="ORDER_SEND_RETURNED_RESULT",
                                                             check=check,
                                                             filling=filling_resolution,
                                                             forensics=forensics)}

    def _resolve_request_filling(self, request: Dict) -> Tuple[Dict, Dict]:
        """Make ``request["type_filling"]`` one this terminal accepts for this symbol.

        Read-only: the candidates come from ``symbol_info().filling_mode`` (the
        SYMBOL_FILLING_MODE bitmask, which does *not* share values with
        ENUM_ORDER_TYPE_FILLING) and each one is confirmed with ``mt5.order_check``.
        Nothing is sent here.
        """
        resolution: Dict = {"evaluated": True, "applied": False,
                            "requested": request.get("type_filling"),
                            "requested_name": filling_name(request.get("type_filling")),
                            "resolution": "not evaluated"}
        try:
            sinfo = self.symbol_info(str(request.get("symbol") or ""))
        except Exception as e:                                  # pragma: no cover - defensive
            sinfo = None
            resolution["error"] = f"symbol_info failed: {type(e).__name__}: {e}"
        if sinfo is None:
            resolution["evaluated"] = False
            resolution["resolution"] = ("symbol metadata unavailable — the request keeps the "
                                        "filling mode it was built with")
            return request, resolution
        cands = filling_candidates(sinfo, mt5)
        bitmask = symbol_filling_bitmask(sinfo)
        requested = request.get("type_filling")
        resolution.update({"symbol_filling_mode": bitmask,
                           "symbol_filling_mode_name": ("no FOK/IOC bit (0) — Return policy"
                                                        if bitmask == 0 else f"bitmask {bitmask}"),
                           "candidates": cands,
                           "first_candidate": cands[0]["name"] if cands else None})
        consistent = (requested is not None
                      and cands and int(requested) == int(cands[0]["value"])
                      and allowed_filling_by_bitmask(sinfo, requested))
        if consistent:
            resolution.update({"filling": int(requested),
                               "name": filling_name(requested),
                               "why": "already consistent with symbol_info().filling_mode",
                               "resolution": ("the request already uses the filling mode "
                                              "derived from symbol_info().filling_mode")})
            return request, resolution
        resolved = resolve_filling(sinfo, mt5, request, candidates=cands)
        request["type_filling"] = int(resolved["filling"])
        resolution.update({"applied": True, "filling": resolved["filling"],
                           "name": resolved["name"], "why": resolved.get("why"),
                           "resolution": resolved["resolution"],
                           "probes": resolved.get("probes")})
        log.info("[V5.2] filling mode resolved for %s: %s (%s)",
                 request.get("symbol"), resolved["name"], resolved["resolution"])
        return request, resolution

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
                              exception: Optional[Dict] = None,
                              filling: Optional[Dict] = None,
                              forensics: Optional[Dict] = None) -> Dict:
        """Everything needed to explain an order_send outcome without guessing.

        Read-only: re-reads the terminal/account/symbol state and returns it
        beside the request that was (or was not) sent. It never sends anything and
        never retries — the caller decides, the operator verifies in MT5.
        """
        diag: Dict = {
            "phase": phase,
            "order_send_called": bool(called),
            "call_count": int((forensics or {}).get("call_count") or (1 if called else 0)),
            "request": dict(request or {}),
            "last_error": last_error,
            "last_error_before": (forensics or {}).get("mt5_last_error_before"),
            "last_error_after": ((forensics or {}).get("mt5_last_error_after")
                                 if forensics else last_error),
            "exception": exception,
            # V5.3 §2.1 — the forensic record of the actual binding call
            "forensics": forensics,
            "session": session_snapshot(),
            "check": ({"ok": check.get("ok"), "retcode": check.get("retcode"),
                       "retcode_name": (check.get("verdict") or {}).get("retcode_name")
                                       or retcode_name(check.get("retcode")),
                       "unsupported": check.get("unsupported"),
                       "comment": ((check.get("raw") or {}).get("comment")
                                   if isinstance(check.get("raw"), dict) else None),
                       "rule": (check.get("verdict") or {}).get("rule"),
                       "margin": (check.get("verdict") or {}).get("margin")}
                      if isinstance(check, dict) else None),
            "last_error_name": last_error_name(last_error) if last_error else None,
            # V5.3 §1 — the read-only facts that name the usual causes; all of them
            # come straight from the terminal, none is inferred.
            "terminal_trade_allowed": None,
            "terminal_tradeapi_disabled": None,
            "account_trade_expert": None,
            "symbol_trade_mode": None,
        }
        if isinstance(filling, dict):
            diag["filling_resolution"] = filling
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
            diag["terminal_trade_allowed"] = diag["terminal"]["trade_allowed"]
            diag["terminal_tradeapi_disabled"] = diag["terminal"]["tradeapi_disabled"]
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
            diag["account_trade_expert"] = diag["account"]["trade_expert"]
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
                "filling_mode": getattr(si, "filling_mode", None),
                "fill_modes": _filling_modes(si),
            }
            diag["symbol_trade_mode"] = diag["symbol"]["trade_mode"]
            req_fill = (request or {}).get("type_filling")
            diag["fill_mode_used"] = req_fill
            diag["fill_mode_used_name"] = filling_name(req_fill)
            bitmask = symbol_filling_bitmask(si)
            diag["symbol_filling_bitmask"] = bitmask
            diag["fill_mode_supported"] = (allowed_filling_by_bitmask(si, req_fill)
                                           if req_fill is not None else None)
            diag["fill_mode_candidates"] = [c["name"] for c in filling_candidates(si, mt5)]
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

    def deal_history(self, position: Optional[int] = None,
                     date_from: Optional[int] = None,
                     date_to: Optional[int] = None) -> List[Dict]:
        """READ-ONLY deal history (V6.5 §6.3). Never places or modifies anything.

        ``position`` returns that position's deals (its entry AND exit deals);
        a date range returns the account's history in that window.  Returns []
        when the terminal is not connected or the query fails — callers treat
        'no data' as 'unknown', never as proof of anything.
        """
        if not self._connected or not MT5_PACKAGE_AVAILABLE:
            return []
        try:
            if position is not None:
                rows = mt5.history_deals_get(position=int(position))
            elif date_from is not None or date_to is not None:
                df = datetime.fromtimestamp(int(date_from), tz=timezone.utc) if date_from else None
                dt = datetime.fromtimestamp(int(date_to), tz=timezone.utc) if date_to else None
                rows = mt5.history_deals_get(df, dt)
            else:
                rows = mt5.history_deals_get()
        except Exception as e:
            log.warning("deal_history failed: %s", e)
            return []
        return [_deal_to_dict(d) for d in (rows or [])]

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

    # ---------- execution: the proven MT5 handoff order path (V6) ----------
    def _send_with_filling_fallback(self, request: Dict, mode_sequence) -> Dict:
        """THE one place ``mt5.order_send`` is called (MT5 handoff, V6).

        Walks ``mode_sequence`` — literally IOC -> FOK -> RETURN for
        ``place_order`` (and the metadata-resolved mode first for the older
        instrumented entry points). Only the documented filling-related broker
        retcodes advance to the next mode:

            10029  TRADE_RETCODE_INVALID_ORDER  ("not this way")
            10030  TRADE_RETCODE_INVALID_FILL   ("not this way")

        Success is ``retcode == 10009`` and nothing else. Any other broker
        answer stops the chain immediately and is returned verbatim ("do not
        retry arbitrary broker errors"). A ``None`` answer or an exception also
        stops the chain and is reported honestly — an unknown outcome is never
        blindly resent (that is how duplicate orders are born).
        """
        attempts: List[Dict] = []
        t0 = time.time()
        last_error_before = self._last_error_safe()
        result = None
        last_res = None
        raised: Optional[BaseException] = None
        tb_tail: Optional[List[str]] = None
        used_req = dict(request)
        for fill_name, fill_mode in mode_sequence:
            req = dict(request)
            req["type_filling"] = int(fill_mode)
            used_req = req
            try:
                res = mt5.order_send(req)
            except BaseException as e:            # noqa: BLE001 - reported, not swallowed
                raised = e
                tb_tail = traceback.format_exc().strip().splitlines()[-8:]
                log.error("order_send raised (%s): %s: %s", fill_name, type(e).__name__, e)
                attempts.append({"filling": fill_name, "filling_mode": int(fill_mode),
                                 "outcome": "ORDER_SEND_EXCEPTION",
                                 "exception_type": type(e).__name__, "exception": str(e)})
                break
            err_after = self._last_error_safe()
            if res is None:
                attempts.append({"filling": fill_name, "filling_mode": int(fill_mode),
                                 "outcome": "ORDER_SEND_RETURNED_NONE", "retcode": None,
                                 "last_error": err_after})
                log.warning("order_send returned None (filling %s, mt5.last_error=%s)",
                            fill_name, err_after)
                break
            raw = _result_to_dict(res)
            rc = raw.get("retcode")
            attempts.append({"filling": fill_name, "filling_mode": int(fill_mode),
                             "outcome": "ORDER_SEND_RETURNED_RESULT", "retcode": rc,
                             "comment": raw.get("comment"), "order": raw.get("order"),
                             "deal": raw.get("deal"), "price": raw.get("price"),
                             "volume": raw.get("volume")})
            last_res = res
            if rc == getattr(mt5, "TRADE_RETCODE_DONE", 10009):
                result = res
                break
            if rc in (getattr(mt5, "TRADE_RETCODE_INVALID_ORDER", 10029),
                      getattr(mt5, "TRADE_RETCODE_INVALID_FILL", 10030)):
                log.warning("filling mode %s rejected (retcode %s) — trying the next mode",
                            fill_name, rc)
                continue
            log.info("broker answered retcode=%s comment=%r (filling %s) — not a filling "
                     "rejection, stopping the chain", rc, raw.get("comment"), fill_name)
            break
        return {"result": result, "last_res": last_res, "raised": raised,
                "tb_tail": tb_tail, "attempts": attempts, "request": used_req,
                "last_error_before": last_error_before,
                "last_error_after": self._last_error_safe(),
                "elapsed_ms": round((time.time() - t0) * 1000.0, 1)}

    def place_order(self, symbol: str, side: str, lots: float,
                    sl_price: float | None = None, tp_price: float | None = None,
                    comment: str = "", position_ticket: int | None = None) -> OrderResult:
        """Send a real market DEAL to MT5 (the MT5 handoff's proven path).

        symbol_select -> live tick -> DEAL request (SL/TP keys OMITTED when
        absent — never ``sl: 0.0``) -> filling fallback IOC -> FOK -> RETURN,
        each mode a REAL ``mt5.order_send()`` call. Success is retcode 10009.
        Nothing is ever fabricated: a None answer is reported as None.
        """
        if not self._connected or not MT5_PACKAGE_AVAILABLE:
            return OrderResult(ok=False, comment="MT5 not connected",
                               rejected_by="BRIDGE", source=self.source, retcode=-1)
        t0 = time.time()
        # handoff rule: symbol_select BEFORE any symbol query or order call
        try:
            mt5.symbol_select(symbol, True)
        except Exception as e:                    # pragma: no cover - terminal dependent
            log.warning("symbol_select(%s) raised: %s", symbol, e)
        tick = self.latest_tick(symbol)
        if tick is None:
            return OrderResult(ok=False, comment="no live tick from MT5",
                               rejected_by="BRIDGE", source=self.source, retcode=-2,
                               order_send_called=False, call_count=0)
        side_l = str(side).lower()
        order_type = (getattr(mt5, "ORDER_TYPE_BUY", 0) if side_l == "buy"
                      else getattr(mt5, "ORDER_TYPE_SELL", 1))
        price = tick.ask if side_l == "buy" else tick.bid
        base_request: Dict[str, Any] = {
            "action": getattr(mt5, "TRADE_ACTION_DEAL", 1),
            "symbol": symbol,
            "volume": float(lots),
            "type": order_type,
            "price": price,
            "deviation": 20,
            "magic": 777001,
            "comment": str(comment or "evolab")[:31],
            "type_time": getattr(mt5, "ORDER_TIME_GTC", 0),
        }
        if sl_price is not None and sl_price > 0:
            base_request["sl"] = float(sl_price)
        if tp_price is not None and tp_price > 0:
            base_request["tp"] = float(tp_price)
        if position_ticket is not None:
            base_request["position"] = int(position_ticket)

        chain = self._send_with_filling_fallback(base_request, _filling_chain())
        attempts = chain["attempts"]
        used = chain["request"]
        exec_ts = time.time()
        common = {"source": self.source, "requested_price": price,
                  "order_send_called": bool(attempts), "call_count": len(attempts),
                  "attempts": attempts, "last_error": chain["last_error_after"]}
        if chain["result"] is not None:
            raw = _result_to_dict(chain["result"])
            ep = float(raw.get("price") or price)
            rc = raw.get("retcode")
            try:
                rc = int(rc) if rc is not None and not isinstance(rc, bool) else None
            except (TypeError, ValueError):
                rc = None
            # V6.4 — 10009 (DONE) is success ONLY with the result confirming it.
            # Any other readable retcode is a broker answer and travels verbatim;
            # a result object with no readable retcode is still not success.
            done = rc == getattr(mt5, "TRADE_RETCODE_DONE", 10009)
            return OrderResult(
                ok=done, order_id=raw.get("order") or raw.get("deal"),
                deal_id=raw.get("deal"), exec_price=ep, exec_ts=exec_ts,
                slippage_points=(abs(ep - price) / 0.01) if ep else 0.0,
                delay_ms=(exec_ts - t0) * 1000.0, retcode=rc,
                comment=str(raw.get("comment") or ("DONE" if done else "FAILED")),
                filling=filling_name(used.get("type_filling")), **common)
        if chain["raised"] is not None:
            return OrderResult(ok=False, retcode=None,
                               comment=f"order_send exception: {chain['raised']}",
                               rejected_by="BRIDGE", exec_ts=exec_ts,
                               delay_ms=(exec_ts - t0) * 1000.0,
                               filling=filling_name(used.get("type_filling")), **common)
        last = chain["last_res"]
        if last is None:
            return OrderResult(ok=False, retcode=None, comment="order_send returned None",
                               rejected_by="BRIDGE", exec_ts=exec_ts,
                               delay_ms=(exec_ts - t0) * 1000.0,
                               filling=filling_name(used.get("type_filling")), **common)
        raw = _result_to_dict(last)
        return OrderResult(ok=False, retcode=raw.get("retcode"),
                           comment=str(raw.get("comment") or "FAILED"),
                           order_id=raw.get("order"), deal_id=raw.get("deal"),
                           exec_ts=exec_ts,
                           delay_ms=(exec_ts - t0) * 1000.0,
                           filling=filling_name(used.get("type_filling")), **common)

    def real_market_order(self, symbol: str, side: str, lots: float) -> OrderResult:
        """Send a REAL market order to MT5. Only callable through the risk layer.

        V6: rides the SAME proven filling-fallback chain as ``place_order`` —
        every send is a real ``mt5.order_send()`` call.
        """
        return self.place_order(symbol, side, lots, comment="evolab")

def _filling_chain():
    """The MT5 handoff's literal fallback order: IOC -> FOK -> RETURN."""
    return [("IOC", getattr(mt5, "ORDER_FILLING_IOC", 1)),
            ("FOK", getattr(mt5, "ORDER_FILLING_FOK", 0)),
            ("RETURN", getattr(mt5, "ORDER_FILLING_RETURN", 2))]


def _fallback_sequence(preferred):
    """Chain order for send_market_order: the metadata-resolved mode first,
    then the remaining modes in the handoff order IOC -> FOK -> RETURN."""
    chain = _filling_chain()
    if preferred is None:
        return chain
    try:
        pref = int(preferred)
    except (TypeError, ValueError):
        return chain
    return ([(n, v) for (n, v) in chain if int(v) == pref]
            + [(n, v) for (n, v) in chain if int(v) != pref])


def _filling_modes(si) -> List[int]:
    """``type_filling`` values this symbol accepts, from its own metadata.

    V5.2 §1/§20 — the bitmask in ``symbol_info().filling_mode`` is
    ``ENUM_SYMBOL_FILLING_MODE`` (SYMBOL_FILLING_FOK=1, SYMBOL_FILLING_IOC=2),
    which does **not** share values with ``ENUM_ORDER_TYPE_FILLING``
    (ORDER_FILLING_FOK=0, IOC=1, RETURN=2). The previous fallback mapped a zero
    bitmask to IOC, which is exactly what made a healthy IC Markets XAUUSD
    terminal refuse the order. A zero bitmask means "no FOK/IOC restriction":
    market orders then use the Return fill policy.
    """
    try:
        mask = int(getattr(si, "filling_mode", 0) or 0)
    except Exception:
        mask = 0
    out: List[int] = []
    # market orders prefer IOC, then FOK; RETURN is the no-restriction policy
    if mask & 2:
        out.append(ORDER_FILLING_IOC)
    if mask & 1:
        out.append(ORDER_FILLING_FOK)
    if not out:
        out = [ORDER_FILLING_RETURN]
    return out


#: The fields a real MT5 order_send result carries. Read DIRECTLY from the
#: result object (V6.4, MT5_BRIDGE_DETAILS.txt): the MetaTrader5 binding has
#: returned namedtuples, dicts, and objects with __slots__/properties across
#: builds — enumerating ``_fields``/``__dict__`` silently produced an empty
#: payload for any of the latter, and an executed trade (retcode 10009 with
#: deal/order tickets) was then labelled UNKNOWN. One known field list, read
#: one attribute at a time, plus whatever extra attributes the object exposes.
_RESULT_FIELDS = ("retcode", "deal", "order", "volume", "price", "bid", "ask",
                  "comment", "request_id", "retcode_external",
                  # extensions some builds/bindings add
                  "sl", "tp", "position_id", "external_id")


def _result_to_dict(result) -> Dict:
    """Serialise an MT5 order_send result into a plain dict — never lose the facts.

    V6.4: reads the actual result fields directly (retcode, deal, order, volume,
    price, comment, request_id, and any result extensions), whatever the object
    shape (namedtuple, dict-like, ``__slots__``, properties, or a foreign
    object). A readable retcode is preserved even when it is a numpy-style int
    or a stringified number; a readable object is NEVER reported as unreadable.
    Raw diagnostics (``_repr``/``_type``/``_dict_keys``) are kept alongside for
    odd types so nothing the binding returned is thrown away.
    """
    out: Dict = {}
    if result is None:
        return out
    # 1. dict-like results (some wrappers/mock bindings return plain dicts)
    if isinstance(result, dict):
        out.update({k: v for k, v in result.items()})
    else:
        # 2. the known MT5 result fields, read DIRECTLY via getattr
        for f in _RESULT_FIELDS:
            try:
                v = getattr(result, f, None)
            except Exception:
                v = None
            if v is not None:
                out[f] = v
        # 3. anything else the object exposes (namedtuple _fields, __dict__,
        #    __slots__, property names) — extension fields must survive too
        extra: list = []
        try:
            extra.extend(list(getattr(result, "_fields", None) or ()))
        except Exception:
            pass
        try:
            extra.extend(list(getattr(result, "__dict__", {}) or ()))
        except Exception:
            pass
        try:
            extra.extend(list(getattr(result, "__slots__", None) or ()))
        except Exception:
            pass
        for f in extra:
            if f in out or f.startswith("_"):
                continue
            try:
                v = getattr(result, f, None)
            except Exception:
                v = None
            if v is not None and not callable(v):
                out[f] = v
    # 4. retcode normalisation: accept numpy-style ints and numeric strings —
    #    a readable retcode is the difference between DONE and a false UNKNOWN.
    rc = out.get("retcode")
    if rc is not None and not isinstance(rc, bool):
        try:
            out["retcode"] = int(rc)
        except (TypeError, ValueError):
            pass
    # 5. raw diagnostics for odd types — kept, never parsed away
    if not out:
        try:
            out["_repr"] = repr(result)[:2000]
        except Exception as e:
            out["_repr"] = f"<repr failed: {type(e).__name__}: {e}>"
        try:
            out["_type"] = type(result).__name__
        except Exception:
            pass
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


def _deal_to_dict(d) -> Dict:
    """V6.5 §6.3 — one history deal as a plain dict.

    order ticket, deal ticket and position ticket are KEPT DISTINCT (they are
    not interchangeable); ``entry`` and ``reason`` keep the broker's own codes
    so exit classification is evidence-based, never guessed.
    """
    return {"ticket": int(getattr(d, "ticket", 0) or 0),
            "order": int(getattr(d, "order", 0) or 0),
            "position": int(getattr(d, "position", 0) or 0),
            "position_id": int(getattr(d, "position_id", 0) or 0),
            "time": int(getattr(d, "time", 0) or 0),
            "type": int(getattr(d, "type", -1)),
            "entry": int(getattr(d, "entry", -1)),
            "magic": int(getattr(d, "magic", 0) or 0),
            "volume": float(getattr(d, "volume", 0.0) or 0.0),
            "price": float(getattr(d, "price", 0.0) or 0.0),
            "commission": float(getattr(d, "commission", 0.0) or 0.0),
            "swap": float(getattr(d, "swap", 0.0) or 0.0),
            "profit": float(getattr(d, "profit", 0.0) or 0.0),
            "fee": float(getattr(d, "fee", 0.0) or 0.0),
            "reason": int(getattr(d, "reason", -1)),
            "symbol": str(getattr(d, "symbol", "") or ""),
            "comment": str(getattr(d, "comment", "") or "")}


def _find(rows: List[Dict], ticket: int) -> Optional[Dict]:
    for r in rows or []:
        if int(r.get("ticket", -1)) == int(ticket):
            return r
    return None
