"""
V4.2 — Controlled MT5 DEMO order execution foundation.

This module is the single execution path for manual demo orders. It consumes the
existing bridge infrastructure (`app.mt5.factory.get_bridge()` /
`MT5RealBridge`) — it does NOT create a second MT5 client.

Design rules (V4.2 spec):

* DEMO ONLY.  An order is sent only when the connected account can be
  *positively* identified as a demo account (MT5 ``account_info().trade_mode``
  == ACCOUNT_TRADE_MODE_DEMO).  Anything else — simulator bridge, missing
  package, disconnected terminal, missing account, real/live account,
  undeterminable trade mode — BLOCKS execution (never guess).
* Validate before sending.  Connection/account/demo/symbol/volume/price/SL/TP
  and symbol trading constraints are checked locally first.
* No fake success.  A success status is only reported when MT5 itself returns
  a DONE/PLACED retcode AND the resulting position/order is verified back from
  the terminal.  ``source`` is always reported so nothing synthetic can be
  presented as a real broker fill.
* SL/TP are real order parameters.  After execution the position/order is read
  back from MT5 and the *actual* broker SL/TP are reported (a broker adjustment
  is surfaced, not hidden).
* Explicit user action + confirmation token.  Nothing here runs on page load,
  on refresh, or from background loops.
* No automatic retry.  A timeout/connection error is reported as UNKNOWN with
  an explicit warning: the order may have reached the broker — verify positions
  before retrying.  Duplicate submissions are rejected (in-flight gate +
  client order id) instead of silently placing a second order.
* Research isolation (V4.0/V4.1 unchanged).  A manual test order never touches
  node status/generation/statistics, and legacy nodes can never be selected as
  trading candidates (``assert_tradeable_strategy``).
"""
from __future__ import annotations

import json
import logging
import math
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("mt5.execution")

# The phrase the UI must send (typed confirmation) for an order to be sent.
PLACE_CONFIRMATION = "PLACE_DEMO_ORDER"

# MT5 documented constants — used when the MetaTrader5 package is unavailable so
# that request building stays testable off-Windows. Values are the official
# MetaTrader5 python API values; when the package IS importable its own
# constants win (see `_c` below).
_CONST_FALLBACK = {
    "TRADE_ACTION_DEAL": 1,
    "TRADE_ACTION_MODIFY": 2,
    "TRADE_ACTION_REMOVE": 3,
    "TRADE_ACTION_CLOSE_BY": 10,
    "ORDER_TYPE_BUY": 0,
    "ORDER_TYPE_SELL": 1,
    "ORDER_TIME_GTC": 0,
    "ORDER_FILLING_FOK": 0,
    "ORDER_FILLING_IOC": 1,
    "ORDER_FILLING_RETURN": 2,
    "ORDER_FILLING_BOC": 3,
    "ACCOUNT_TRADE_MODE_DEMO": 0,
    "ACCOUNT_TRADE_MODE_CONTEST": 1,
    "ACCOUNT_TRADE_MODE_REAL": 2,
    "SYMBOL_TRADE_MODE_DISABLED": 0,
    "SYMBOL_TRADE_MODE_LONGONLY": 1,
    "SYMBOL_TRADE_MODE_SHORTONLY": 2,
    "SYMBOL_TRADE_MODE_CLOSEONLY": 3,
    "SYMBOL_TRADE_MODE_FULL": 4,
    "TRADE_RETCODE_REQUOTE": 10004,
    "TRADE_RETCODE_REJECT": 10006,
    "TRADE_RETCODE_CANCEL": 10007,
    "TRADE_RETCODE_PLACED": 10008,
    "TRADE_RETCODE_DONE": 10009,
    "TRADE_RETCODE_DONE_PARTIAL": 10010,
    "TRADE_RETCODE_ERROR": 10011,
    "TRADE_RETCODE_TIMEOUT": 10012,
    "TRADE_RETCODE_INVALID": 10013,
    "TRADE_RETCODE_INVALID_VOLUME": 10014,
    "TRADE_RETCODE_INVALID_PRICE": 10015,
    "TRADE_RETCODE_INVALID_STOPS": 10016,
    "TRADE_RETCODE_TRADE_DISABLED": 10017,
    "TRADE_RETCODE_MARKET_CLOSED": 10018,
    "TRADE_RETCODE_NO_MONEY": 10019,
    "TRADE_RETCODE_PRICE_CHANGED": 10020,
    "TRADE_RETCODE_PRICE_OFF": 10021,
    "TRADE_RETCODE_INVALID_EXPIRATION": 10022,
    "TRADE_RETCODE_ORDER_CHANGED": 10023,
    "TRADE_RETCODE_TOO_MANY_REQUESTS": 10024,
    "TRADE_RETCODE_NO_CHANGES": 10025,
    "TRADE_RETCODE_SERVER_DISABLES_AT": 10026,
    "TRADE_RETCODE_CLIENT_DISABLES_AT": 10027,
    "TRADE_RETCODE_LOCKED": 10028,
    "TRADE_RETCODE_FROZEN": 10029,
    "TRADE_RETCODE_INVALID_FILL": 10030,
    "TRADE_RETCODE_CONNECTION": 10031,
    "TRADE_RETCODE_ONLY_REAL": 10032,
    "TRADE_RETCODE_LIMIT_ORDERS": 10033,
    "TRADE_RETCODE_LIMIT_VOLUME": 10034,
    "TRADE_RETCODE_INVALID_ORDER": 10035,
    "TRADE_RETCODE_POSITION_CLOSED": 10036,
    "TRADE_RETCODE_INVALID_CLOSE_VOLUME": 10038,
    "TRADE_RETCODE_CLOSE_ORDER_EXIST": 10039,
    "TRADE_RETCODE_LIMIT_POSITIONS": 10040,
    "TRADE_RETCODE_REJECT_CANCEL": 10041,
    "TRADE_RETCODE_LONG_ONLY": 10042,
    "TRADE_RETCODE_SHORT_ONLY": 10043,
    "TRADE_RETCODE_CLOSE_ONLY": 10044,
    "TRADE_RETCODE_FIFO_CLOSE": 10045,
    "TRADE_RETCODE_HEDGE_PROHIBITED": 10046,
}


def mt5_module():
    """Return the MetaTrader5 module if importable, else None (never raises)."""
    try:
        from .mt5_real import mt5 as _mt5  # type: ignore
        return _mt5
    except Exception:  # pragma: no cover
        return None


def _c(name: str) -> int:
    """Constant lookup: the package's own value when available, else official."""
    mod = mt5_module()
    val = getattr(mod, name, None) if mod is not None else None
    return int(val) if isinstance(val, int) else int(_CONST_FALLBACK[name])


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class MT5ExecutionError(Exception):
    """Structured execution failure. Never rendered as '[object Object]'."""

    def __init__(self, code: str, message: str, stage: str = "EXECUTION",
                 details: Optional[Dict[str, Any]] = None, http_status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage
        self.details = details or {}
        self.http_status = http_status

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "message": self.message, "stage": self.stage,
                "details": self.details, "ok": False}


# ---------------------------------------------------------------------------
# Demo account safety guard (spec §1)
# ---------------------------------------------------------------------------
def demo_account_guard(bridge=None) -> Dict[str, Any]:
    """Positively identify the connected MT5 account as a DEMO account.

    Returns a report; `demo_verified=True` only for a real MT5 bridge whose
    account_info() reports ACCOUNT_TRADE_MODE_DEMO. Any doubt -> verified False
    with a human-readable `blocked_reason` and a machine `blocked_code`.
    """
    if bridge is None:
        from .factory import get_bridge
        bridge = get_bridge()

    src = getattr(bridge, "source", "UNKNOWN")
    rep: Dict[str, Any] = {
        "bridge": getattr(bridge, "name", "unknown"),
        "bridge_source": src,
        "is_simulated": bool(getattr(bridge, "is_simulated", src == "SIMULATOR")),
        "connected": bool(getattr(bridge, "_connected", False)),
        "demo_verified": False,
        "blocked_code": None,
        "blocked_reason": None,
        "account": None,
        "trade_allowed": None,
        "checked_ts": time.time(),
    }

    try:
        from .mt5_real import MT5_PACKAGE_AVAILABLE
    except Exception:  # pragma: no cover
        MT5_PACKAGE_AVAILABLE = False
    rep["mt5_package_installed"] = bool(MT5_PACKAGE_AVAILABLE)

    if src != "MT5":
        rep["blocked_code"] = "MT5_UNAVAILABLE"
        rep["blocked_reason"] = (
            f"Order execution blocked: the active market bridge is '{src}', not a real "
            f"MetaTrader 5 terminal. Demo order execution requires a connected MT5 "
            f"terminal (Windows host) with the MetaTrader5 package installed."
        )
        return rep

    if not rep["connected"]:
        rep["blocked_code"] = "MT5_NOT_CONNECTED"
        rep["blocked_reason"] = ("Order execution blocked: the MT5 terminal is not "
                                 "connected. Reconnect the terminal and try again.")
        return rep

    acct = None
    try:
        acct = bridge.account_info()
    except Exception as e:
        rep["blocked_code"] = "ACCOUNT_UNAVAILABLE"
        rep["blocked_reason"] = f"Order execution blocked: account_info() failed ({e})."
        return rep

    raw_trade_mode = None
    try:
        mod = mt5_module()
        raw = mod.account_info() if mod is not None else None
        raw_trade_mode = getattr(raw, "trade_mode", None) if raw is not None else None
    except Exception:
        raw_trade_mode = None

    if acct is None:
        rep["blocked_code"] = "ACCOUNT_UNAVAILABLE"
        rep["blocked_reason"] = ("Order execution blocked: no MT5 account is logged in "
                                 "(account_info() returned nothing).")
        return rep

    acct_info = {
        "login": getattr(acct, "login", 0),
        "server": getattr(acct, "server", ""),
        "company": getattr(getattr(bridge, "broker_name", ""), "strip", lambda: "")() or "",
        "currency": getattr(acct, "currency", "USD"),
        "balance": float(getattr(acct, "balance", 0.0) or 0.0),
        "equity": float(getattr(acct, "equity", 0.0) or 0.0),
        "margin_free": float(getattr(acct, "margin_free", 0.0) or 0.0),
        "leverage": int(getattr(acct, "leverage", 0) or 0),
        "trade_mode": raw_trade_mode,
        "trade_mode_name": _trade_mode_name(raw_trade_mode),
        "is_demo_flag": bool(getattr(acct, "is_demo", False)),
    }
    rep["account"] = acct_info

    terminal_trade_allowed = None
    try:
        mod = mt5_module()
        ti = mod.terminal_info() if mod is not None else None
        if ti is not None:
            terminal_trade_allowed = bool(getattr(ti, "trade_allowed", True))
            rep["trade_allowed"] = terminal_trade_allowed
            rep["terminal"] = {"name": str(getattr(ti, "name", "") or ""),
                               "company": str(getattr(ti, "company", "") or ""),
                               "connected": bool(getattr(ti, "connected", True))}
    except Exception:
        pass

    # Positive identification: trade mode must be explicitly DEMO.
    demo_const = _c("ACCOUNT_TRADE_MODE_DEMO")
    if raw_trade_mode is None:
        rep["blocked_code"] = "ACCOUNT_MODE_UNKNOWN"
        rep["blocked_reason"] = (
            "Order execution blocked: the account trade mode could not be read from MT5, "
            "so the account cannot be positively identified as a demo account.")
        return rep
    if int(raw_trade_mode) != demo_const:
        rep["blocked_code"] = "NON_DEMO_ACCOUNT"
        rep["blocked_reason"] = (
            f"Order execution blocked: account {acct_info['login']} on server "
            f"'{acct_info['server']}' is a {acct_info['trade_mode_name']} account, not a "
            f"DEMO account. V4.2 executes demo orders only.")
        return rep
    if not acct_info["is_demo_flag"]:
        # trade_mode says demo but the bridge's own flag disagrees -> do not guess.
        rep["blocked_code"] = "ACCOUNT_MODE_UNKNOWN"
        rep["blocked_reason"] = ("Order execution blocked: conflicting demo identification from "
                                 "MT5 (trade_mode=demo but is_demo flag is false).")
        return rep
    if terminal_trade_allowed is False:
        rep["blocked_code"] = "ALGO_TRADING_DISABLED"
        rep["blocked_reason"] = ("Order execution blocked: Algo Trading is disabled in the MT5 "
                                 "terminal. Enable the 'Algo Trading' button (and allow trading "
                                 "for the account) and retry.")
        return rep

    rep["demo_verified"] = True
    return rep


def _http_for(code: Optional[str]) -> int:
    return {
        "MT5_UNAVAILABLE": 503, "MT5_NOT_CONNECTED": 503, "ACCOUNT_UNAVAILABLE": 503,
        "NON_DEMO_ACCOUNT": 403, "ACCOUNT_MODE_UNKNOWN": 403, "ALGO_TRADING_DISABLED": 409,
    }.get(code or "", 400)


def _trade_mode_name(mode: Optional[int]) -> str:
    if mode is None:
        return "UNKNOWN"
    m = int(mode)
    if m == _c("ACCOUNT_TRADE_MODE_DEMO"):
        return "DEMO"
    if m == _c("ACCOUNT_TRADE_MODE_CONTEST"):
        return "CONTEST"
    if m == _c("ACCOUNT_TRADE_MODE_REAL"):
        return "REAL/LIVE"
    return f"UNKNOWN({m})"


# ---------------------------------------------------------------------------
# Order validation (spec §7)
# ---------------------------------------------------------------------------
def _finite(x) -> bool:
    try:
        return math.isfinite(float(x))
    except Exception:
        return False


def _chk(checks: List[Dict], cid: str, label: str, ok: bool, detail: str = "",
         code: Optional[str] = None, blocking: bool = True):
    checks.append({"id": cid, "label": label, "ok": bool(ok), "detail": detail,
                   "code": None if ok else code, "blocking": bool(blocking)})
    return bool(ok)


def validate_order_request(bridge=None, *, symbol: str = "", side: str = "",
                           volume: Any = None, sl: Any = None, tp: Any = None,
                           price: Any = None, strategy_id: Optional[int] = None,
                           magic: Optional[int] = None,
                           require_live: bool = True) -> Dict[str, Any]:
    """Local validation of a requested demo order.

    Returns a report with per-check rows (shown 1:1 in the UI) plus normalized
    values. `placement_allowed` is True only when no blocking check failed —
    the same report is used for the UI preview and as the pre-send gate.
    """
    from .factory import get_bridge
    bridge = bridge or get_bridge()
    guard = demo_account_guard(bridge)

    checks: List[Dict] = []
    errors: List[Dict] = []

    side_n = str(side or "").strip().lower()
    symbol_n = str(symbol or "").strip().upper()
    vol = float(volume) if _finite(volume) else None
    sl_f = float(sl) if _finite(sl) else None
    tp_f = float(tp) if _finite(tp) else None
    px = float(price) if _finite(price) else None

    # --- connection / account / demo (spec §7 first four bullets, §1) ---
    _chk(checks, "mt5_package", "MetaTrader5 package installed",
         bool(guard.get("mt5_package_installed")), "", "MT5_UNAVAILABLE")
    _chk(checks, "mt5_bridge", "Real MT5 bridge active (not simulator)",
         guard.get("bridge_source") == "MT5",
         f"active bridge: {guard.get('bridge')} / {guard.get('bridge_source')}",
         "MT5_UNAVAILABLE")
    _chk(checks, "mt5_connected", "MT5 terminal connected", guard.get("connected"),
         "", "MT5_NOT_CONNECTED")
    acct = guard.get("account") or {}
    _chk(checks, "account_available", "Account available",
         bool(guard.get("account")),
         (f"login {acct.get('login')} @ {acct.get('server')} ({acct.get('currency')})"
          if guard.get("account") else ""), "ACCOUNT_UNAVAILABLE")
    _chk(checks, "demo_account", "DEMO account positively verified",
         guard.get("demo_verified"),
         f"trade_mode: {acct.get('trade_mode_name')}" if guard.get("account") else "",
         guard.get("blocked_code") or "NON_DEMO_ACCOUNT")
    _chk(checks, "algo_trading", "Terminal allows algo trading",
         guard.get("trade_allowed") is not False, "", "ALGO_TRADING_DISABLED")

    # --- symbol ---
    sinfo = None
    quote = None
    try:
        sinfo = bridge.symbol_info(symbol_n) if symbol_n else None
    except Exception as e:
        log.warning("symbol_info(%s) failed: %s", symbol_n, e)
    sym_ok = _chk(checks, "symbol_exists", "Symbol exists", sinfo is not None,
                  f"symbol: {symbol_n or '(empty)'}", "INVALID_SYMBOL")
    if sym_ok:
        # tradable?
        tmode = getattr(sinfo, "trade_mode_raw", None)
        allowed_flag = getattr(sinfo, "trade_allowed", None)
        fully = tmode is None or int(tmode) == _c("SYMBOL_TRADE_MODE_FULL")
        _chk(checks, "symbol_tradable", "Symbol is tradable",
             bool(fully and allowed_flag is not False),
             f"trade_mode: {getattr(sinfo, 'trade_mode', '?')}", "SYMBOL_NOT_TRADABLE")
        if side_n == "buy" and tmode is not None and int(tmode) == _c("SYMBOL_TRADE_MODE_SHORTONLY"):
            _chk(checks, "symbol_side", "Symbol allows this direction", False,
                 "symbol is SHORT-ONLY", "SYMBOL_SIDE_NOT_ALLOWED")
        elif side_n == "sell" and tmode is not None and int(tmode) == _c("SYMBOL_TRADE_MODE_LONGONLY"):
            _chk(checks, "symbol_side", "Symbol allows this direction", False,
                 "symbol is LONG-ONLY", "SYMBOL_SIDE_NOT_ALLOWED")
        else:
            _chk(checks, "symbol_side", "Symbol allows this direction", True, "")

        try:
            tick = bridge.latest_tick(symbol_n)
        except Exception:
            tick = None
        if tick is not None:
            quote = {"bid": float(tick.bid), "ask": float(tick.ask),
                     "ts": float(tick.ts), "source": getattr(tick, "source", ""),
                     "spread_points": round((float(tick.ask) - float(tick.bid)) / (sinfo.point or 0.01), 1)}
            from ..config import get_config
            stale_s = float(getattr(get_config().mt5, "feed_stale_s", 120) or 120)
            age = time.time() - float(tick.ts)
            _chk(checks, "quote_fresh", "Live quote available and fresh",
                 float(tick.bid) > 0 and float(tick.ask) > 0 and age <= stale_s,
                 f"bid {tick.bid} / ask {tick.ask} ({age:.0f}s old)",
                 "MARKET_CLOSED_OR_STALE")
        else:
            _chk(checks, "quote_fresh", "Live quote available and fresh", False,
                 "no tick from the terminal", "MARKET_CLOSED_OR_STALE")

    # --- side / volume / price / SL / TP ---
    _chk(checks, "side_valid", "BUY/SELL is valid", side_n in ("buy", "sell"),
         f"side: {side or '(empty)'}", "INVALID_SIDE")
    vol_ok = _chk(checks, "volume_numeric", "Volume is a valid number",
                  vol is not None and vol > 0, f"volume: {volume}", "INVALID_VOLUME")
    if vol_ok and sym_ok:
        vmin = getattr(sinfo, "volume_min", None)
        vmax = getattr(sinfo, "volume_max", None)
        vstep = getattr(sinfo, "volume_step", None)
        if vmin is not None and vol < float(vmin):
            _chk(checks, "volume_min", "Volume >= symbol minimum", False,
                 f"minimum is {vmin} lots (requested {vol})", "VOLUME_BELOW_MIN")
        else:
            _chk(checks, "volume_min", "Volume >= symbol minimum", True,
                 f"minimum {vmin} lots" if vmin is not None else "symbol minimum unknown")
        if vmax is not None and vol > float(vmax):
            _chk(checks, "volume_max", "Volume <= symbol maximum", False,
                 f"maximum is {vmax} lots (requested {vol})", "VOLUME_ABOVE_MAX")
        else:
            _chk(checks, "volume_max", "Volume <= symbol maximum", True,
                 f"maximum {vmax} lots" if vmax is not None else "symbol maximum unknown")
        step_ok = True
        detail = "symbol step unknown"
        if vstep is not None and float(vstep) > 0:
            steps = vol / float(vstep)
            step_ok = abs(steps - round(steps)) < 1e-6
            detail = f"step is {vstep} lots"
            if not step_ok:
                detail = f"volume {vol} is not a multiple of the symbol step {vstep}"
            code = None if step_ok else "VOLUME_STEP"
            _chk(checks, "volume_step", "Volume respects symbol step", step_ok, detail, code)
        else:
            _chk(checks, "volume_step", "Volume respects symbol step", True, detail)
    else:
        for cid, label in (("volume_min", "Volume >= symbol minimum"),
                           ("volume_max", "Volume <= symbol maximum"),
                           ("volume_step", "Volume respects symbol step")):
            _chk(checks, cid, label, vol_ok, "not evaluated", None if vol_ok else "INVALID_VOLUME")

    market_ok = bool(quote and quote.get("bid", 0) > 0 and quote.get("ask", 0) > 0)
    _chk(checks, "price_valid", "Entry / market price is valid",
         (px is None or px > 0) and market_ok,
         (f"market price: {'/'.join(str(quote[k]) for k in ('bid', 'ask')) if quote else 'unavailable'}"
          + (f" (requested {px})" if px is not None else "")),
         "INVALID_PRICE")
    _chk(checks, "sl_valid", "Stop loss is valid", sl_f is None or sl_f > 0,
         f"SL: {sl_f if sl_f is not None else '(none)'}", "INVALID_SL")
    _chk(checks, "tp_valid", "Take profit is valid", tp_f is None or tp_f > 0,
         f"TP: {tp_f if tp_f is not None else '(none)'}", "INVALID_TP")

    ref_price = None
    if market_ok and side_n in ("buy", "sell"):
        ref_price = float(quote["ask"] if side_n == "buy" else quote["bid"])

    if ref_price is not None and (sl_f is not None or tp_f is not None):
        if side_n == "buy":
            bad = (sl_f is not None and sl_f >= ref_price) or (tp_f is not None and tp_f <= ref_price)
            expect = "SL < entry < TP"
        else:
            bad = (sl_f is not None and sl_f <= ref_price) or (tp_f is not None and tp_f >= ref_price)
            expect = "TP < entry < SL"
        _chk(checks, "sl_tp_direction", f"SL/TP direction is correct ({expect})",
             not bad, f"entry {ref_price}", "SL_TP_DIRECTION")

        stops = getattr(sinfo, "trade_stops_level", None) if sinfo is not None else None
        point = float(getattr(sinfo, "point", 0.0) or 0.0) if sinfo is not None else 0.0
        if stops is not None and point > 0 and int(stops) > 0:
            min_dist = float(stops) * point
            probs = []
            if sl_f is not None and abs(ref_price - sl_f) < min_dist:
                probs.append(f"SL distance {abs(ref_price - sl_f):.5f} < broker minimum {min_dist:.5f}")
            if tp_f is not None and abs(tp_f - ref_price) < min_dist:
                probs.append(f"TP distance {abs(tp_f - ref_price):.5f} < broker minimum {min_dist:.5f}")
            _chk(checks, "stops_level", "SL/TP respect the symbol stops level",
                 not probs, f"minimum distance {min_dist:.5f} ({int(stops)} points); " +
                 ("; ".join(probs) if probs else "ok"), "SL_TP_TOO_CLOSE")
    else:
        _chk(checks, "sl_tp_direction", "SL/TP direction is correct", True,
             "not evaluated (no SL/TP or no quote)")
        _chk(checks, "stops_level", "SL/TP respect the symbol stops level", True,
             "not evaluated")

    if vol_ok and acct:
        free = float(acct.get("margin_free", 0.0) or 0.0)
        _chk(checks, "margin", "Account has free margin", free > 0 or not require_live,
             f"free margin: {free}", "INSUFFICIENT_MARGIN")
    else:
        _chk(checks, "margin", "Account has free margin", bool(acct),
             f"free margin: {(acct or {}).get('margin_free', 'n/a')}", "INSUFFICIENT_MARGIN")

    # --- strategy / node linkage (spec §15) ---
    node = None
    if strategy_id is not None:
        node = assert_tradeable_strategy(int(strategy_id))
        _chk(checks, "node_scope", "Selected node is a USER_RESEARCH node", node["ok"],
             node["detail"], node["code"])

    # --- blocking failures ---
    blocking = [c for c in checks if not c["ok"] and c["blocking"] and require_live]
    for c in checks:
        if not c["ok"] and c["blocking"]:
            errors.append({"field": c["id"], "code": c["code"] or "VALIDATION_FAILED",
                           "message": f"{c['label']}" + (f" — {c['detail']}" if c["detail"] else "")})

    normalized = {"symbol": symbol_n, "side": side_n, "volume": vol, "sl": sl_f, "tp": tp_f,
                  "price": px, "strategy_id": strategy_id,
                  "magic": int(magic) if magic is not None else _magic_for(strategy_id)}
    return {
        "ok": not blocking,
        "placement_allowed": not blocking,
        "require_live": require_live,
        "checks": checks,
        "errors": errors,
        "normalized": normalized,
        "quote": quote,
        "symbol_info": ({"symbol": sinfo.symbol, "digits": sinfo.digits, "point": sinfo.point,
                         "volume_min": getattr(sinfo, "volume_min", None),
                         "volume_max": getattr(sinfo, "volume_max", None),
                         "volume_step": getattr(sinfo, "volume_step", None),
                         "stops_level": getattr(sinfo, "trade_stops_level", None),
                         "filling_modes": getattr(sinfo, "filling_modes", None),
                         "source": getattr(sinfo, "source", "")} if sinfo is not None else None),
        "account_safety": {k: guard.get(k) for k in
                           ("demo_verified", "blocked_code", "blocked_reason", "account",
                            "bridge_source", "mt5_package_installed", "connected", "trade_allowed")},
        "node": node,
    }


def assert_tradeable_strategy(strategy_id: int) -> Dict[str, Any]:
    """A manually selected node must be a normal USER_RESEARCH node (spec §15).

    Uses the existing V4.0 data_source classification — legacy infrastructure
    rows can never become trading candidates. Read-only: no status is touched.
    """
    from ..db.database import get_db
    row = get_db().one("SELECT id, status, data_source, run_id FROM strategies WHERE id=?",
                       (int(strategy_id),))
    if row is None:
        return {"ok": False, "code": "NODE_NOT_FOUND",
                "detail": f"node {strategy_id} does not exist", "node": None}
    ds = (row.get("data_source") or "USER_RESEARCH")
    if ds == "LEGACY_TEST":
        return {"ok": False, "code": "LEGACY_NODE_NOT_TRADEABLE",
                "detail": (f"node {strategy_id} is a LEGACY_TEST infrastructure record and is "
                           f"outside normal research/trading selection (V4.0 isolation)"),
                "node": None}
    return {"ok": True, "code": None, "detail": f"node {strategy_id} ({ds}, status {row['status']})",
            "node": {"id": int(row["id"]), "status": row["status"], "data_source": ds,
                     "run_id": row.get("run_id")}}


def _magic_for(strategy_id: Optional[int]) -> int:
    # 777000 range = manual V4.2 demo orders; strategy-linked orders get +node id.
    # V4.3 live-testing orders use an explicit magic from the 778000 range so the
    # authoritative MT5 position list can be filtered per execution layer.
    base = 777000
    return base + (int(strategy_id) % 900 if strategy_id is not None else 0)


LIVE_TEST_MAGIC_BASE = 778000


def live_test_magic(strategy_id: Optional[int]) -> int:
    """Magic range reserved for V4.3 live-testing orders."""
    return LIVE_TEST_MAGIC_BASE + (int(strategy_id) % 900 if strategy_id is not None else 0)


def pick_filling(symbol_info) -> int:
    """Choose the order filling mode from the symbol's supported set."""
    modes = getattr(symbol_info, "filling_modes", None) or []
    try:
        modes = [int(m) for m in modes]
    except Exception:
        modes = []
    for name in ("ORDER_FILLING_IOC", "ORDER_FILLING_FOK", "ORDER_FILLING_RETURN"):
        c = _c(name)
        if c in modes:
            return c
    return _c("ORDER_FILLING_IOC")


def build_market_order_request(payload: Dict[str, Any], *, filling: int,
                               deviation: int = 20, order_type: Optional[int] = None) -> Dict[str, Any]:
    """Build the MT5 request dict for a market order with real SL/TP."""
    side = payload["side"]
    otype = order_type if order_type is not None else (
        _c("ORDER_TYPE_BUY") if side == "buy" else _c("ORDER_TYPE_SELL"))
    req: Dict[str, Any] = {
        "action": _c("TRADE_ACTION_DEAL"),
        "symbol": payload["symbol"],
        "volume": float(payload["volume"]),
        "type": otype,
        "price": float(payload["price"]),
        "deviation": int(deviation),
        "magic": int(payload.get("magic") or 777000),
        "comment": str(payload.get("comment") or "evolab-demo-manual")[:31],
        "type_time": _c("ORDER_TIME_GTC"),
        "type_filling": int(filling),
    }
    if payload.get("sl") is not None:
        req["sl"] = float(payload["sl"])
    if payload.get("tp") is not None:
        req["tp"] = float(payload["tp"])
    return req


# ---------------------------------------------------------------------------
# MT5 result interpretation (spec §8, §12, §14)
# ---------------------------------------------------------------------------
def interpret_retcode(retcode: Optional[int], comment: str = "") -> Dict[str, Any]:
    """Turn an MT5 retcode into category + human-readable message.

    Categories: EXECUTED | PENDING | REJECTED | UNKNOWN | TIMEOUT.
    `safe_to_retry` is False for anything where the order may have reached the
    broker (timeout / connection / unknown) — the operator must verify first.
    """
    r = retcode
    c = (comment or "").strip()
    base = {"retcode": r, "broker_comment": c, "safe_to_retry": False}
    m = {
        _c("TRADE_RETCODE_DONE"): ("EXECUTED", "Order executed by the broker (retcode 10009 DONE)."),
        _c("TRADE_RETCODE_DONE_PARTIAL"): ("EXECUTED", "Order partially executed by the broker (retcode 10010 DONE_PARTIAL)."),
        _c("TRADE_RETCODE_PLACED"): ("PENDING", "Order accepted and placed as a pending order (retcode 10008 PLACED)."),
        _c("TRADE_RETCODE_REQUOTE"): ("REJECTED", "Broker requoted the price (retcode 10004 REQUOTE). Review the current quote and resubmit manually."),
        _c("TRADE_RETCODE_REJECT"): ("REJECTED", "Broker rejected the request (retcode 10006 REJECT)."),
        _c("TRADE_RETCODE_CANCEL"): ("REJECTED", "Request cancelled by the broker (retcode 10007 CANCEL)."),
        _c("TRADE_RETCODE_ERROR"): ("REJECTED", "MT5 reported a request error (retcode 10011 ERROR)."),
        _c("TRADE_RETCODE_TIMEOUT"): ("TIMEOUT", "MT5 request timed out (retcode 10012 TIMEOUT). The order MAY have reached the broker — check open positions before retrying."),
        _c("TRADE_RETCODE_INVALID"): ("REJECTED", "Invalid request (retcode 10013 INVALID)."),
        _c("TRADE_RETCODE_INVALID_VOLUME"): ("REJECTED", "Invalid volume (retcode 10014 INVALID_VOLUME)."),
        _c("TRADE_RETCODE_INVALID_PRICE"): ("REJECTED", "Invalid price (retcode 10015 INVALID_PRICE)."),
        _c("TRADE_RETCODE_INVALID_STOPS"): ("REJECTED", "Invalid stops: SL/TP too close to the price or on the wrong side (retcode 10016 INVALID_STOPS)."),
        _c("TRADE_RETCODE_TRADE_DISABLED"): ("REJECTED", "Trading is disabled for this account (retcode 10017 TRADE_DISABLED)."),
        _c("TRADE_RETCODE_MARKET_CLOSED"): ("REJECTED", "Market is closed for this symbol (retcode 10018 MARKET_CLOSED)."),
        _c("TRADE_RETCODE_NO_MONEY"): ("REJECTED", "Insufficient margin/funds (retcode 10019 NO_MONEY)."),
        _c("TRADE_RETCODE_PRICE_CHANGED"): ("REJECTED", "Price changed before execution (retcode 10020 PRICE_CHANGED)."),
        _c("TRADE_RETCODE_PRICE_OFF"): ("REJECTED", "No quotes to process the request (retcode 10021 PRICE_OFF)."),
        _c("TRADE_RETCODE_INVALID_EXPIRATION"): ("REJECTED", "Invalid order expiration (retcode 10022)."),
        _c("TRADE_RETCODE_ORDER_CHANGED"): ("REJECTED", "Order state changed (retcode 10023)."),
        _c("TRADE_RETCODE_TOO_MANY_REQUESTS"): ("REJECTED", "Too many requests — broker throttled the client (retcode 10024)."),
        _c("TRADE_RETCODE_NO_CHANGES"): ("REJECTED", "No changes in the request (retcode 10025)."),
        _c("TRADE_RETCODE_SERVER_DISABLES_AT"): ("REJECTED", "AutoTrading is disabled on the server side (retcode 10026)."),
        _c("TRADE_RETCODE_CLIENT_DISABLES_AT"): ("REJECTED", "AutoTrading is disabled in the terminal — enable the 'Algo Trading' button (retcode 10027 CLIENT_DISABLES_AT)."),
        _c("TRADE_RETCODE_LOCKED"): ("REJECTED", "Request locked by the broker (retcode 10028 LOCKED)."),
        _c("TRADE_RETCODE_FROZEN"): ("REJECTED", "Order or position is frozen (retcode 10029 FROZEN)."),
        _c("TRADE_RETCODE_INVALID_FILL"): ("REJECTED", "Filling mode not supported for this symbol (retcode 10030 INVALID_FILL)."),
        _c("TRADE_RETCODE_CONNECTION"): ("TIMEOUT", "No connection to the trade server (retcode 10031 CONNECTION). The order MAY have reached the broker — check open positions before retrying."),
        _c("TRADE_RETCODE_ONLY_REAL"): ("REJECTED", "Operation allowed on real accounts only (retcode 10032 ONLY_REAL)."),
        _c("TRADE_RETCODE_LIMIT_ORDERS"): ("REJECTED", "Order limit reached for this account/symbol (retcode 10033)."),
        _c("TRADE_RETCODE_LIMIT_VOLUME"): ("REJECTED", "Volume limit reached for this symbol (retcode 10034)."),
        _c("TRADE_RETCODE_INVALID_ORDER"): ("REJECTED", "Invalid or prohibited order type (retcode 10035)."),
        _c("TRADE_RETCODE_POSITION_CLOSED"): ("REJECTED", "Position with the specified id is already closed (retcode 10036)."),
        _c("TRADE_RETCODE_INVALID_CLOSE_VOLUME"): ("REJECTED", "Invalid close volume (retcode 10038)."),
        _c("TRADE_RETCODE_CLOSE_ORDER_EXIST"): ("REJECTED", "A close order already exists for this position (retcode 10039)."),
        _c("TRADE_RETCODE_LIMIT_POSITIONS"): ("REJECTED", "Open-position limit reached (retcode 10040)."),
        _c("TRADE_RETCODE_REJECT_CANCEL"): ("REJECTED", "Order activation rejected, cancelled (retcode 10041)."),
        _c("TRADE_RETCODE_LONG_ONLY"): ("REJECTED", "Only long positions are allowed for this symbol (retcode 10042)."),
        _c("TRADE_RETCODE_SHORT_ONLY"): ("REJECTED", "Only short positions are allowed for this symbol (retcode 10043)."),
        _c("TRADE_RETCODE_CLOSE_ONLY"): ("REJECTED", "Only position closing is allowed (retcode 10044)."),
        _c("TRADE_RETCODE_FIFO_CLOSE"): ("REJECTED", "Position must be closed by FIFO rule first (retcode 10045)."),
        _c("TRADE_RETCODE_HEDGE_PROHIBITED"): ("REJECTED", "Hedging is prohibited for this account (retcode 10046)."),
    }
    if r in m:
        cat, msg = m[r]
    elif r is None:
        cat, msg = "UNKNOWN", "MT5 returned no result object — the request outcome is UNKNOWN."
    else:
        cat, msg = "UNKNOWN", f"Unrecognised MT5 retcode {r} — see the server log for the raw response."
    if c and c.lower() not in msg.lower():
        msg = f"{msg} Broker comment: {c}"
    out = {**base, "category": cat, "message": msg,
           "safe_to_retry": cat in ("REJECTED",)}
    if cat in ("TIMEOUT", "UNKNOWN"):
        out["warning"] = ("Do NOT resubmit automatically. Verify open positions/orders in MT5 "
                          "first — the request may have been executed at the broker.")
    return out


# ---------------------------------------------------------------------------
# Operation gate (duplicate protection, spec §11)
# ---------------------------------------------------------------------------
class _OpGate:
    """Single-flight gate + progress state for manual order submission.

    `_token` is a non-reentrant lock used ONLY as the in-flight token, while
    `_lock` guards the state dict — so state updates (stage/finish/fail) can
    never deadlock against the token (V4.1 lesson: no non-reentrant lock is
    held across nested acquisitions).
    """

    def __init__(self):
        self._token = threading.Lock()
        self._lock = threading.RLock()
        self._state: Dict[str, Any] = {"in_flight": False, "stage": None}

    def try_begin(self, meta: Dict[str, Any]) -> bool:
        if not self._token.acquire(blocking=False):
            return False
        with self._lock:
            self._state = {"in_flight": True, "started_ts": time.time(), "stage": "QUEUED",
                           "last_result": self._state.get("last_result"), **meta}
        return True

    def stage(self, stage: str, **extra):
        with self._lock:
            self._state.update({"stage": stage, **extra})

    def _release(self):
        try:
            self._token.release()
        except RuntimeError:
            pass          # already released (defensive: never raise on cleanup)

    def finish(self, result: Dict[str, Any]):
        with self._lock:
            self._state.update({"in_flight": False, "stage": "DONE",
                                "finished_ts": time.time(), "last_result": result})
        self._release()

    def fail(self, error: "MT5ExecutionError"):
        with self._lock:
            self._state.update({"in_flight": False, "stage": "ERROR",
                                "finished_ts": time.time(),
                                "last_error": error.to_dict()})
        self._release()

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._state)


_gate = _OpGate()


def operation_status() -> Dict[str, Any]:
    st = _gate.status()
    return {"in_flight": bool(st.get("in_flight")),
            "stage": st.get("stage"),
            "started_ts": st.get("started_ts"),
            "finished_ts": st.get("finished_ts"),
            "client_order_id": st.get("client_order_id"),
            "symbol": st.get("symbol"), "side": st.get("side"), "volume": st.get("volume"),
            "last_result": st.get("last_result"),
            "last_error": st.get("last_error")}


# ---------------------------------------------------------------------------
# Execution (spec §8, §9, §10, §11, §14)
# ---------------------------------------------------------------------------
def place_demo_order(payload: Dict[str, Any], bridge=None) -> Dict[str, Any]:
    """Place ONE demo market order with real SL/TP, after all gates pass.

    Never retries. Returns a structured result; raises MT5ExecutionError with
    `stage` naming the exact failed stage otherwise.
    """
    from .factory import get_bridge
    bridge = bridge or get_bridge()

    confirm = str(payload.get("confirm") or "")
    if confirm != PLACE_CONFIRMATION:
        raise MT5ExecutionError(
            "CONFIRMATION_REQUIRED",
            f"Explicit confirmation required: send confirm='{PLACE_CONFIRMATION}' after the "
            f"operator has confirmed symbol, side, volume, price, SL, TP and the demo account.",
            stage="CONFIRMATION", http_status=409)

    client_order_id = str(payload.get("client_order_id") or uuid.uuid4().hex)
    symbol = str(payload.get("symbol") or "").strip().upper()
    side = str(payload.get("side") or "").strip().lower()
    volume = payload.get("volume")

    db = _db()
    existing = _find_client_order(db, client_order_id)
    if existing:
        raise MT5ExecutionError(
            "DUPLICATE_ORDER",
            (f"client_order_id {client_order_id} was already submitted "
             f"(status {existing.get('status')}, ticket {existing.get('order_ticket')}). "
             f"A new order was NOT placed."),
            stage="DUPLICATE_GATE", http_status=409,
            details={"existing": {k: existing.get(k) for k in
                                  ("status", "retcode", "order_ticket", "result_message", "ts")}})

    if not _gate.try_begin({"client_order_id": client_order_id, "symbol": symbol,
                            "side": side, "volume": volume}):
        cur = operation_status()
        raise MT5ExecutionError(
            "ORDER_IN_FLIGHT",
            (f"An order submission is already in progress (stage {cur.get('stage')}, "
             f"{cur.get('symbol')} {cur.get('side')}). Duplicate submission blocked — wait for "
             f"the MT5 result."),
            stage="DUPLICATE_GATE", http_status=409)

    t0 = time.time()
    try:
        _gate.stage("ACCOUNT_SAFETY")
        guard = demo_account_guard(bridge)
        if not guard.get("demo_verified"):
            code = guard.get("blocked_code") or "NON_DEMO_ACCOUNT"
            raise MT5ExecutionError(code,
                                    guard.get("blocked_reason") or "account is not a verified demo account",
                                    stage="ACCOUNT_SAFETY", details={"account_safety": guard},
                                    http_status=_http_for(code))

        _gate.stage("VALIDATION")
        report = validate_order_request(
            bridge, symbol=symbol, side=side, volume=volume, sl=payload.get("sl"),
            tp=payload.get("tp"), price=payload.get("price"),
            strategy_id=payload.get("strategy_id"), magic=payload.get("magic"),
            require_live=True)
        if not report["placement_allowed"]:
            raise MT5ExecutionError(
                "VALIDATION_FAILED",
                "Order blocked by local validation: " +
                "; ".join(f"{e['message']}" for e in report["errors"][:4]),
                stage="VALIDATION", details={"errors": report["errors"], "checks": report["checks"]},
                http_status=422)

        sinfo = bridge.symbol_info(symbol)
        quote = report["quote"] or {}
        if side == "buy":
            exec_price = float(quote.get("ask") or 0.0)
        else:
            exec_price = float(quote.get("bid") or 0.0)
        if exec_price <= 0:
            raise MT5ExecutionError("INVALID_PRICE", "No usable market price from MT5.",
                                    stage="VALIDATION", http_status=422)

        norm = report["normalized"]
        filling = pick_filling(sinfo)
        request = build_market_order_request(
            {"symbol": symbol, "side": side, "volume": norm["volume"], "price": exec_price,
             "sl": norm["sl"], "tp": norm["tp"], "magic": norm["magic"],
             "comment": payload.get("comment") or "evolab-demo-manual"},
            filling=filling, deviation=int(payload.get("deviation") or 20))

        _gate.stage("SENDING", request=request)
        log.info("[V4.2] sending DEMO order: %s", json.dumps(
            {k: v for k, v in request.items()}, default=str))
        raw = bridge.send_market_order(request)
        raw_result = raw.get("raw") if isinstance(raw, dict) else None
        if raw_result is None and isinstance(raw, dict) and raw.get("unsupported"):
            raise MT5ExecutionError("MT5_UNAVAILABLE",
                                    "The active bridge does not support order execution "
                                    "(no real MT5 terminal).",
                                    stage="SENDING", details={"raw": raw}, http_status=503)
        if raw_result is None and isinstance(raw, dict) and raw.get("exception"):
            raise MT5ExecutionError("MT5_EXCEPTION",
                                    f"mt5.order_send raised: {raw.get('exception')}",
                                    stage="SENDING", details={"raw": raw}, http_status=502)

        _gate.stage("INTERPRETING")
        interp = interpret_retcode(raw.get("retcode") if isinstance(raw, dict) else None,
                                   (raw_result or {}).get("comment", "") if isinstance(raw_result, dict) else "")
        ticket = (raw_result or {}).get("order") if isinstance(raw_result, dict) else None
        deal = (raw_result or {}).get("deal") if isinstance(raw_result, dict) else None

        _gate.stage("VERIFYING")
        verification = verify_execution(bridge, ticket=ticket, symbol=symbol,
                                        sl=norm["sl"], tp=norm["tp"],
                                        magic=norm["magic"], done=(interp["category"] == "EXECUTED"))

        status = _final_status(interp, verification)
        result = {
            "ok": interp["category"] in ("EXECUTED", "PENDING"),
            "status": status,
            "label": {"POSITION_OPEN": "ORDER EXECUTED",
                      "PENDING_ORDER": "ORDER ACCEPTED / PENDING",
                      "EXECUTED_UNCONFIRMED": "EXECUTED BUT NOT CONFIRMED BY THE TERMINAL",
                      "TIMEOUT_UNKNOWN": "RESULT UNKNOWN — TIMEOUT",
                      "REJECTED": "ORDER REJECTED",
                      "UNKNOWN": "RESULT UNKNOWN"}[status],
            "client_order_id": client_order_id,
            "broker": {"retcode": interp["retcode"], "comment": interp["broker_comment"],
                       "message": interp["message"], "category": interp["category"],
                       "warning": interp.get("warning"),
                       "safe_to_retry": interp["safe_to_retry"]},
            "order": {"ticket": ticket, "deal_ticket": deal,
                      "position_ticket": verification.get("position_ticket"),
                      "pending_ticket": verification.get("pending_ticket")},
            "request": request,
            "execution": {"symbol": symbol, "side": side, "volume": norm["volume"],
                          "requested_volume": norm["volume"],
                          "executed_volume": verification.get("exec_volume"),
                          "requested_price": exec_price,
                          "exec_price": verification.get("exec_price"),
                          "sl_requested": norm["sl"], "tp_requested": norm["tp"],
                          "sl_broker": verification.get("broker_sl"),
                          "tp_broker": verification.get("broker_tp"),
                          "sl_tp_verified": verification.get("sl_tp_verified"),
                          "time": verification.get("time")},
            "account": guard.get("account"),
            "source": getattr(bridge, "source", "MT5"),
            "duration_ms": round((time.time() - t0) * 1000.0, 1),
            "verification": verification,
        }
        _record(db, result, strategy_id=payload.get("strategy_id"), t0=t0)
        _gate.finish(result)
        log.info("[V4.2] demo order result: %s", json.dumps(
            {k: result[k] for k in ("status", "ok")}, default=str))
        return result

    except MT5ExecutionError as e:
        _gate.fail(e)
        try:
            _record_failure(db, client_order_id=client_order_id, symbol=symbol, side=side,
                            volume=volume, payload=payload, error=e, t0=t0, gate=_gate)
        except Exception as rec_err:  # never mask the original failure
            log.error("[V4.2] failed to persist order failure: %s", rec_err)
        log.warning("[V4.2] demo order blocked/failed at stage %s: %s (%s)",
                    e.stage, e.message, e.code)
        raise
    except Exception as e:  # unexpected -> surfaced, never swallowed
        err = MT5ExecutionError("UNEXPECTED_ERROR", f"Unexpected execution error: {e}",
                                stage="UNKNOWN", http_status=500)
        _gate.fail(err)
        log.exception("[V4.2] unexpected execution error")
        raise err


def _final_status(interp: Dict[str, Any], verification: Dict[str, Any]) -> str:
    if interp["category"] == "EXECUTED":
        if verification.get("position_found"):
            return "POSITION_OPEN"
        if verification.get("pending_found"):
            return "PENDING_ORDER"
        return "EXECUTED_UNCONFIRMED"
    if interp["category"] == "PENDING":
        return "PENDING_ORDER"
    if interp["category"] == "TIMEOUT":
        return "TIMEOUT_UNKNOWN"
    if interp["category"] == "REJECTED":
        return "REJECTED"
    return "UNKNOWN"


def verify_execution(bridge, *, ticket: Optional[int], symbol: str,
                     sl: Optional[float], tp: Optional[float],
                     magic: Optional[int], done: bool) -> Dict[str, Any]:
    """Read the result back from MT5 (spec §9/§14): position, pending order, SL/TP."""
    out: Dict[str, Any] = {"position_found": False, "pending_found": False,
                           "position_ticket": None, "pending_ticket": None,
                           "exec_price": None, "exec_volume": None,
                           "broker_sl": None, "broker_tp": None, "sl_tp_verified": None,
                           "time": None, "note": ""}
    try:
        pos = None
        if ticket and hasattr(bridge, "positions_get"):
            pos = _first(bridge.positions_get(ticket=int(ticket)))
        if pos is None and hasattr(bridge, "positions_get"):
            pool = _rows(bridge.positions_get(symbol=symbol))
            for p in pool:
                if ticket and int(p.get("ticket", -1)) == int(ticket):
                    pos = p
                    break
                if magic and int(p.get("magic", 0)) == int(magic) and p.get("symbol") == symbol:
                    pos = p
                    break
        if pos:
            out.update({"position_found": True,
                        "position_ticket": pos.get("ticket"),
                        "exec_price": pos.get("price_open"),
                        "exec_volume": pos.get("volume"),
                        "broker_sl": pos.get("sl"), "broker_tp": pos.get("tp"),
                        "time": pos.get("time")})
        elif ticket and hasattr(bridge, "orders_get"):
            po = _first(bridge.orders_get(ticket=int(ticket)))
            if po is None:
                pool = _rows(bridge.orders_get(symbol=symbol))
                for o in pool:
                    if ticket and int(o.get("ticket", -1)) == int(ticket):
                        po = o
                        break
                    if magic and int(o.get("magic", 0)) == int(magic) and o.get("symbol") == symbol:
                        po = o
                        break
            if po:
                out.update({"pending_found": True, "pending_ticket": po.get("ticket"),
                            "exec_price": po.get("price_open") or po.get("price"),
                            "exec_volume": po.get("volume_current") or po.get("volume_initial"),
                            "broker_sl": po.get("sl"), "broker_tp": po.get("tp"),
                            "time": po.get("time_setup")})
        if out["position_found"] or out["pending_found"]:
            out["sl_tp_verified"] = _sl_tp_match(sl, tp, out["broker_sl"], out["broker_tp"])
            if not out["sl_tp_verified"]:
                out["note"] = (f"broker SL/TP differ from the request "
                               f"(requested SL {sl} / TP {tp}, broker SL {out['broker_sl']} / "
                               f"TP {out['broker_tp']})")
        elif done:
            out["note"] = ("retcode DONE but no position/order with this ticket was found in the "
                           "terminal (it may have been closed immediately or the ticket changed)")
    except Exception as e:
        out["note"] = f"verification error: {e}"
        log.warning("[V4.2] verification error: %s", e)
    return out


def _rows(rows) -> List[Dict]:
    """bridges return lists; test doubles may return a single dict or None."""
    if rows is None:
        return []
    if isinstance(rows, dict):
        return [rows]
    try:
        return [r for r in rows if isinstance(r, dict)]
    except TypeError:
        return []


def _first(rows) -> Optional[Dict]:
    got = _rows(rows)
    return got[0] if got else None


def _sl_tp_match(sl, tp, bsl, btp) -> bool:
    def eq(a, b):
        if a is None and (b in (None, 0, 0.0)):
            return True
        if a is None or b is None:
            return False
        return abs(float(a) - float(b)) < 1e-6
    return eq(sl, bsl) and eq(tp, btp)


# ---------------------------------------------------------------------------
# Position close (test hygiene for the on-host verification script)
# ---------------------------------------------------------------------------
def close_demo_position(ticket: int, bridge=None, comment: str = "evolab-demo-close") -> Dict[str, Any]:
    """Close an MT5 demo position by ticket. Demo-guarded; used to clean up
    verification trades. Not part of any automated loop (V4.2 has none)."""
    from .factory import get_bridge
    bridge = bridge or get_bridge()
    guard = demo_account_guard(bridge)
    if not guard.get("demo_verified"):
        code = guard.get("blocked_code") or "NON_DEMO_ACCOUNT"
        raise MT5ExecutionError(code, guard.get("blocked_reason") or "not a verified demo account",
                                stage="ACCOUNT_SAFETY", http_status=_http_for(code))
    if not hasattr(bridge, "close_position"):
        raise MT5ExecutionError("MT5_UNAVAILABLE", "bridge does not support position closing",
                                stage="SENDING", http_status=503)
    return bridge.close_position(int(ticket), comment=comment)


# ---------------------------------------------------------------------------
# Persistence (audit log of manual demo orders)
# ---------------------------------------------------------------------------
def _db():
    from ..db.database import get_db
    return get_db()


def _find_client_order(db, client_order_id: str) -> Optional[Dict[str, Any]]:
    try:
        return db.get_manual_mt5_order(client_order_id)
    except Exception as e:  # pragma: no cover
        log.warning("client order lookup failed: %s", e)
        return None


def _record(db, result: Dict[str, Any], strategy_id: Optional[int], t0: float) -> None:
    row = {
        "client_order_id": result["client_order_id"],
        "ts": t0,
        "symbol": result["execution"]["symbol"],
        "side": result["execution"]["side"],
        "volume": result["execution"]["volume"],
        "requested_price": result["execution"]["requested_price"],
        "sl": result["execution"]["sl_requested"],
        "tp": result["execution"]["tp_requested"],
        "strategy_id": strategy_id,
        "status": result["status"],
        "retcode": result["broker"]["retcode"],
        "order_ticket": result["order"]["ticket"],
        "deal_ticket": result["order"]["deal_ticket"],
        "position_ticket": result["order"]["position_ticket"],
        "exec_price": result["execution"]["exec_price"],
        "broker_sl": result["execution"]["sl_broker"],
        "broker_tp": result["execution"]["tp_broker"],
        "sl_tp_verified": result["execution"]["sl_tp_verified"],
        "message": result["broker"]["message"],
        "error_code": None if result["ok"] else result["status"],
        "account_login": (result.get("account") or {}).get("login"),
        "account_server": (result.get("account") or {}).get("server"),
        "duration_ms": result.get("duration_ms"),
        "raw_json": json.dumps(result, default=str)[:20000],
    }
    try:
        db.record_manual_mt5_order(row)
    except Exception as e:
        log.error("failed to persist manual MT5 order: %s", e)


def _record_failure(db, *, client_order_id: str, symbol: str, side: str, volume: Any,
                    payload: Dict[str, Any], error: MT5ExecutionError, t0: float,
                    gate: _OpGate) -> None:
    """Persist a blocked/failed attempt so the audit trail shows what happened."""
    st = gate.to_dict()
    row = {
        "client_order_id": client_order_id, "ts": t0, "symbol": symbol, "side": side,
        "volume": float(volume) if _finite(volume) else None,
        "requested_price": payload.get("price"),
        "sl": float(payload["sl"]) if _finite(payload.get("sl")) else None,
        "tp": float(payload["tp"]) if _finite(payload.get("tp")) else None,
        "strategy_id": payload.get("strategy_id"),
        "status": "BLOCKED" if error.stage in ("CONFIRMATION", "DUPLICATE_GATE",
                                               "ACCOUNT_SAFETY", "VALIDATION") else "ERROR",
        "retcode": None, "order_ticket": None, "deal_ticket": None, "position_ticket": None,
        "exec_price": None, "broker_sl": None, "broker_tp": None, "sl_tp_verified": None,
        "message": error.message, "error_code": error.code,
        "account_login": None, "account_server": None,
        "duration_ms": round((time.time() - t0) * 1000.0, 1),
        "raw_json": json.dumps({"error": error.to_dict(), "stage_state": {
            k: st.get(k) for k in ("stage", "symbol", "side", "client_order_id")},
            "request": st.get("request")}, default=str)[:20000],
    }
    db.record_manual_mt5_order(row)


def recent_orders(limit: int = 20) -> List[Dict[str, Any]]:
    try:
        return _db().get_manual_mt5_orders(limit=limit)
    except Exception as e:  # pragma: no cover
        log.warning("history lookup failed: %s", e)
        return []


def execution_state(bridge=None) -> Dict[str, Any]:
    """Everything the manual demo order panel needs (account identity, block
    reason, symbol defaults, in-flight state, recent results)."""
    from .factory import get_bridge
    from ..config import get_config
    bridge = bridge or get_bridge()
    guard = demo_account_guard(bridge)
    cfg = get_config()
    symbol = cfg.data.symbol
    sinfo = None
    quote = None
    try:
        sinfo = bridge.symbol_info(symbol)
        tick = bridge.latest_tick(symbol)
        if tick is not None:
            quote = {"bid": float(tick.bid), "ask": float(tick.ask), "ts": float(tick.ts)}
    except Exception as e:
        log.debug("state symbol info failed: %s", e)
    try:
        from ..risk.controls import get_risk_manager
        kill_switch = get_risk_manager().kill_switch_engaged()
    except Exception:
        kill_switch = None
    return {
        "bridge": {"name": getattr(bridge, "name", "unknown"),
                   "source": getattr(bridge, "source", "UNKNOWN"),
                   "is_simulated": bool(getattr(bridge, "is_simulated", False)),
                   "connected": bool(getattr(bridge, "_connected", False))},
        "account_safety": guard,
        "execution_allowed": bool(guard.get("demo_verified")),
        "blocked_code": guard.get("blocked_code"),
        "blocked_reason": guard.get("blocked_reason"),
        "default_symbol": symbol,
        "symbol": ({"symbol": sinfo.symbol, "digits": sinfo.digits, "point": sinfo.point,
                    "volume_min": getattr(sinfo, "volume_min", None),
                    "volume_max": getattr(sinfo, "volume_max", None),
                    "volume_step": getattr(sinfo, "volume_step", None),
                    "stops_level": getattr(sinfo, "trade_stops_level", None),
                    "filling_modes": getattr(sinfo, "filling_modes", None),
                    "source": getattr(sinfo, "source", "")} if sinfo is not None else None),
        "quote": quote,
        "rolling": getattr(bridge, "symbol_names", lambda: [])() if hasattr(bridge, "symbol_names") else [],
        "kill_switch_engaged": kill_switch,
        "operation": operation_status(),
        "recent_orders": recent_orders(20),
        "confirmation_phrase": PLACE_CONFIRMATION,
        "note": ("V4.2 manual demo execution only: no automatic trading, no background loops, "
                 "research nodes are never modified by a manual test order."),
    }
