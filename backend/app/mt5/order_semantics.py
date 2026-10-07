"""V5.2 — MT5 request/response semantics: filling modes and `order_check` results.

Two facts about the MetaTrader5 Python API are easy to get wrong, and getting them
wrong makes a *healthy* Windows terminal look broken:

1. **Filling modes are two different enumerations.**

   ``ENUM_ORDER_TYPE_FILLING`` (what goes into ``request["type_filling"]``)::

       ORDER_FILLING_FOK    = 0
       ORDER_FILLING_IOC    = 1
       ORDER_FILLING_RETURN = 2

   ``ENUM_SYMBOL_FILLING_MODE`` (the *bitmask* in ``symbol_info().filling_mode``)::

       SYMBOL_FILLING_FOK = 1
       SYMBOL_FILLING_IOC = 2

   The values do **not** line up, and a bitmask of ``0`` means the symbol has no
   FOK/IOC restriction — for market-execution symbols the terminal then only
   accepts ``ORDER_FILLING_RETURN``. Sending ``type_filling = 1`` (IOC) to such a
   symbol is the classic cause of "order_send returned None" / retcode 10030.

2. **``order_check`` reports success as ``retcode = 0``** on many builds when the
   request passes, together with a *computed* margin and ``comment='Done'``. MQL5's
   own reference output for a passing ``MqlTradeCheckResult`` prints
   ``Retcode: OK (0) … Margin: 128.66 … Comment: Done``. Treating "retcode !=
   10009" as a refusal therefore rejects perfectly valid orders. A genuine refusal
   carries an error retcode (10013/10014/10016/10019/10030 …) and an error comment.

So: the filling mode is *derived* from the symbol's own metadata (never hard-coded),
the terminal is asked (read-only, via ``order_check``) when the metadata is
ambiguous, and the check result is interpreted with the documented rules. Sending
an order is never retried here — these helpers only decide *whether* the single
send may happen.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger("mt5.order_semantics")

# --------------------------------------------------------------------------- #
# the two filling enumerations
# --------------------------------------------------------------------------- #
#: ENUM_ORDER_TYPE_FILLING — the value used in request["type_filling"]
ORDER_FILLING_FOK = 0
ORDER_FILLING_IOC = 1
ORDER_FILLING_RETURN = 2

ORDER_FILLING_NAMES: Dict[int, str] = {
    ORDER_FILLING_FOK: "ORDER_FILLING_FOK",
    ORDER_FILLING_IOC: "ORDER_FILLING_IOC",
    ORDER_FILLING_RETURN: "ORDER_FILLING_RETURN",
}

#: ENUM_SYMBOL_FILLING_MODE — the *bitmask* in symbol_info().filling_mode
SYMBOL_FILLING_FOK = 1
SYMBOL_FILLING_IOC = 2

#: when the symbol's bitmask carries neither bit, market orders must use RETURN
NO_RESTRICTION_FILLING = ORDER_FILLING_RETURN


def filling_name(value: Optional[int]) -> str:
    if value is None:
        return "UNKNOWN"
    return ORDER_FILLING_NAMES.get(int(value), f"UNKNOWN({value})")


def symbol_filling_bitmask(symbol_info: Any) -> Optional[int]:
    """The raw ENUM_SYMBOL_FILLING_MODE bitmask of a symbol (None when unreadable).

    Reads the terminal's own ``filling_mode``; the project's ``SymbolInfo`` keeps the
    same number under ``filling_mode_raw`` beside the decoded list, so both shapes
    are accepted.
    """
    for attr in ("filling_mode", "filling_mode_raw"):
        try:
            raw = getattr(symbol_info, attr, None)
        except Exception:
            continue
        if raw is None:
            continue
        try:
            return int(raw)
        except Exception:
            continue
    return None


def decoded_filling_modes(symbol_info: Any) -> Optional[List[int]]:
    """``symbol_info().filling_modes`` when the bridge already decoded the bitmask.

    The project's own ``SymbolInfo`` carries this list (built by
    ``mt5_real._filling_modes``); when it is present and valid it is used as-is,
    otherwise the raw bitmask is decoded here.
    """
    mods = getattr(symbol_info, "filling_modes", None)
    if mods is None or isinstance(mods, (str, bytes)):
        return None
    try:
        vals = [int(m) for m in mods]
    except Exception:
        return None
    vals = [v for v in vals if v in ORDER_FILLING_NAMES]
    return vals or None


def filling_candidates(symbol_info: Any, mt5: Any = None, *,
                       spread_points: Optional[float] = None) -> List[Dict[str, Any]]:
    """Ordered ``type_filling`` candidates for this symbol, from its own metadata.

    Each candidate is ``{"value", "name", "why"}``. The list is never empty: the
    last resort is RETURN (the terminal rejects an unsupported mode with retcode
    10030, which :func:`resolve_filling` turns into the next probe).
    """
    bitmask = symbol_filling_bitmask(symbol_info)
    out: List[Dict[str, Any]] = []

    def add(value: int, why: str) -> None:
        if all(c["value"] != value for c in out):
            out.append({"value": value, "name": filling_name(value), "why": why})

    decoded = decoded_filling_modes(symbol_info)
    if decoded is not None:
        for v in decoded:
            add(v, "decoded from symbol_info().filling_mode by the bridge")
        if ORDER_FILLING_RETURN not in decoded and bitmask not in (None, 0):
            add(ORDER_FILLING_RETURN,
                "kept as a probe candidate: the terminal's own order_check decides "
                "whether this symbol accepts it")
        return out

    if bitmask is None:
        add(NO_RESTRICTION_FILLING,
            "symbol_info().filling_mode is unavailable — RETURN is the mode the "
            "terminal accepts for market execution when no restriction is reported")
    elif bitmask == 0:
        add(NO_RESTRICTION_FILLING,
            "symbol_info().filling_mode bitmask is 0 (no SYMBOL_FILLING_FOK/IOC bit) — "
            "market orders on this symbol use the Return fill policy")
    else:
        # market orders are executed at the market, so IOC leads (then FOK), and
        # each candidate is only ever *probed* through the terminal's own check
        if bitmask & SYMBOL_FILLING_IOC:
            add(ORDER_FILLING_IOC, "SYMBOL_FILLING_IOC bit set in filling_mode")
        if bitmask & SYMBOL_FILLING_FOK:
            add(ORDER_FILLING_FOK, "SYMBOL_FILLING_FOK bit set in filling_mode")
        # A symbol advertising FOK/IOC may still refuse both in market execution;
        # RETURN stays available as a *probe* candidate (never as a blind choice —
        # the terminal answers before anything is sent).
        add(NO_RESTRICTION_FILLING,
            "kept as a probe candidate: the terminal's own order_check decides "
            "whether this symbol accepts it")
    return out


def pick_filling(symbol_info: Any, mt5: Any = None) -> int:
    """The first (metadata-derived) candidate. Kept as the API used by callers."""
    cands = filling_candidates(symbol_info, mt5)
    return int(cands[0]["value"])


def allowed_filling_by_bitmask(symbol_info: Any, value: int) -> bool:
    """Is ``type_filling = value`` consistent with the symbol's metadata?"""
    if value is None:
        return False
    v = int(value)
    bitmask = symbol_filling_bitmask(symbol_info)
    if bitmask is None:
        # no raw bitmask on this object; fall back to the bridge's decoded list
        decoded = decoded_filling_modes(symbol_info)
        return True if decoded is None else v in decoded
    if bitmask == 0:
        # no FOK/IOC bit -> the Return fill policy is the only one this symbol takes
        return v == ORDER_FILLING_RETURN
    if v == ORDER_FILLING_FOK:
        return bool(bitmask & SYMBOL_FILLING_FOK)
    if v == ORDER_FILLING_IOC:
        return bool(bitmask & SYMBOL_FILLING_IOC)
    return v == ORDER_FILLING_RETURN and v in (decoded_filling_modes(symbol_info) or [])


# --------------------------------------------------------------------------- #
# order_check interpretation (MQL5 semantics, documented)
# --------------------------------------------------------------------------- #
#: MqlTradeCheckResult.retcode values that mean "the request is acceptable".
#: 0  — undocumented-but-standard "OK" on passing checks (margin is computed and
#:      the comment is 'Done'; see the module docstring);
#: 10009 TRADE_RETCODE_DONE and 10008 TRADE_RETCODE_PLACED — the explicit codes.
CHECK_PASS_RETCODES: Tuple[int, ...] = (0, 10008, 10009)

#: MQL5 trade-server return codes worth naming in a refusal
TRADE_RETCODE_NAMES: Dict[int, str] = {
    0: "TRADE_RETCODE_OK/UNDEFINED (0)",
    1: "TRADE_RETCODE_ERROR (1)",
    2: "TRADE_RETCODE_TIMEOUT (2)",
    3: "TRADE_RETCODE_INVALID (3)",
    4: "TRADE_RETCODE_INVALID_VOLUME (4)",
    10004: "TRADE_RETCODE_REQUOTE (10004)",
    10006: "TRADE_RETCODE_REJECT (10006)",
    10007: "TRADE_RETCODE_CANCEL (10007)",
    10008: "TRADE_RETCODE_PLACED (10008)",
    10009: "TRADE_RETCODE_DONE (10009)",
    10010: "TRADE_RETCODE_DONE_PARTIAL (10010)",
    10011: "TRADE_RETCODE_ERROR (10011)",
    10012: "TRADE_RETCODE_TIMEOUT (10012)",
    10013: "TRADE_RETCODE_INVALID (10013)",
    10014: "TRADE_RETCODE_INVALID_VOLUME (10014)",
    10015: "TRADE_RETCODE_INVALID_PRICE (10015)",
    10016: "TRADE_RETCODE_INVALID_STOPS (10016)",
    10017: "TRADE_RETCODE_TRADE_DISABLED (10017)",
    10018: "TRADE_RETCODE_MARKET_CLOSED (10018)",
    10019: "TRADE_RETCODE_NO_MONEY (10019)",
    10020: "TRADE_RETCODE_PRICE_CHANGED (10020)",
    10021: "TRADE_RETCODE_PRICE_OFF (10021)",
    10022: "TRADE_RETCODE_INVALID_EXPIRATION (10022)",
    10023: "TRADE_RETCODE_ORDER_CHANGED (10023)",
    10024: "TRADE_RETCODE_TOO_MANY_REQUESTS (10024)",
    10025: "TRADE_RETCODE_NO_CHANGES (10025)",
    10026: "TRADE_RETCODE_SERVER_DISABLES_AT (10026)",
    10027: "TRADE_RETCODE_CLIENT_DISABLES_AT (10027 — enable 'Algo Trading' in the terminal)",
    10028: "TRADE_RETCODE_LOCKED (10028)",
    10029: "TRADE_RETCODE_FROZEN (10029)",
    10030: "TRADE_RETCODE_INVALID_FILL (10030 — filling mode not supported by the symbol)",
    10031: "TRADE_RETCODE_CONNECTION (10031)",
    10032: "TRADE_RETCODE_ONLY_REAL (10032)",
    10033: "TRADE_RETCODE_LIMIT_ORDERS (10033)",
    10034: "TRADE_RETCODE_LIMIT_VOLUME (10034)",
    10035: "TRADE_RETCODE_INVALID_ORDER (10035)",
    10036: "TRADE_RETCODE_POSITION_CLOSED (10036)",
    10038: "TRADE_RETCODE_INVALID_CLOSE_VOLUME (10038)",
    10039: "TRADE_RETCODE_CLOSE_ORDER_EXIST (10039)",
    10040: "TRADE_RETCODE_LIMIT_POSITIONS (10040)",
    10041: "TRADE_RETCODE_REJECT_CANCEL (10041)",
    10042: "TRADE_RETCODE_LONG_ONLY (10042)",
    10043: "TRADE_RETCODE_SHORT_ONLY (10043)",
    10044: "TRADE_RETCODE_CLOSE_ONLY (10044)",
    10045: "TRADE_RETCODE_FIFO_CLOSE (10045)",
    10046: "TRADE_RETCODE_HEDGE_PROHIBITED (10046)",
}

#: mt5.last_error() codes (negative) seen around order_send
LAST_ERROR_NAMES: Dict[int, str] = {
    -1: "RES_E_FAIL (-1) generic failure",
    -2: "RES_E_INVALID_PARAMS (-2) invalid parameters",
    -3: "RES_E_NO_MEMORY (-3)",
    -4: "RES_E_NOT_FOUND (-4)",
    -5: "RES_E_ARRAY_WRONG_RANGE (-5)",
    -6: "RES_E_ARRAY_INVALID_PARAMETER (-6)",
    -7: "RES_E_NO_HISTORY_DATA (-7)",
    -10000: "IPC: no connection to the terminal (-10000)",
    -10001: "IPC: wrong terminal (-10001)",
    -10002: "IPC: send failed (-10002)",
    -10003: "IPC: initialize failed / process create failed (-10003)",
    -10004: "IPC: no IPC connection (-10004) — the terminal is not reachable from this "
            "interpreter (wrong architecture, terminal closed, or not started by this session)",
    -10005: "IPC: timeout (-10005)",
    -10006: "IPC: invalid handle (-10006)",
    -10007: "IPC: unknown command (-10007)",
    -10008: "IPC: not found (-10008)",
    -10009: "IPC: no rights (-10009)",
    -10010: "no MetaTrader 5 terminal found (-10010)",
    -10011: "terminal not initialized (-10011)",
    -10012: "not initialized by this interpreter (-10012)",
}


def last_error_name(last_error: Any) -> str:
    """Human name for ``mt5.last_error()`` (which returns ``(code, description)``)."""
    if last_error is None:
        return "mt5.last_error() unavailable"
    code = None
    desc = ""
    try:
        if isinstance(last_error, (list, tuple)) and len(last_error) >= 1:
            code = int(last_error[0])
            desc = str(last_error[1]) if len(last_error) > 1 else ""
        else:
            code = int(last_error)
    except Exception:
        return str(last_error)
    name = LAST_ERROR_NAMES.get(code, f"code {code}")
    return f"{name}" + (f" — {desc}" if desc and desc.lower() not in name.lower() else "")


def retcode_name(retcode: Optional[int]) -> str:
    if retcode is None:
        return "NO_RESULT"
    return TRADE_RETCODE_NAMES.get(int(retcode), f"retcode {retcode}")


def interpret_check_result(raw: Any, mt5: Any = None) -> Dict[str, Any]:
    """Decide whether an ``MqlTradeCheckResult`` means "this request is acceptable".

    Rule (documented, not guessed):

    * ``retcode`` in :data:`CHECK_PASS_RETCODES` **and** no error-looking comment
      → the request passed the terminal's own validation;
    * anything else → refused, named with its MQL5 constant.

    ``margin``/``margin_free``/``balance`` are reported either way: a passing check
    computes them (the operator's own Windows run showed ``margin=8.17``), and an
    error code never needs them to be trusted.
    """
    d = _as_dict(raw)
    retcode = d.get("retcode")
    comment = str(d.get("comment") or "")
    try:
        rc = None if retcode is None else int(retcode)
    except Exception:
        rc = None
    # A pass code that comes with an "unsupported/denied/invalid" comment is NOT a
    # pass: the comment is the terminal telling us what it objected to.
    comment_refuses = any(word in comment.lower() for word in
                          ("unsupported", "invalid", "denied", "not allowed", "disabled",
                           "no money", "not enough", "closed", "reject"))
    ok = rc in CHECK_PASS_RETCODES and not comment_refuses
    if ok and rc == 0:
        rule = ("retcode 0 with comment 'Done' is a PASSING MqlTradeCheckResult "
                "(MQL5 reference output: 'Retcode: OK (0) … Comment: Done'); the "
                "computed margin confirms the request was evaluated")
    elif ok:
        rule = f"retcode {rc} ({retcode_name(rc)}) is an explicit pass code"
    elif rc is None:
        rule = "the check produced no retcode (no result object)"
    elif comment_refuses:
        rule = f"comment {comment!r} refuses the request (code {rc}, {retcode_name(rc)})"
    else:
        rule = f"retcode {rc} ({retcode_name(rc)}) is not a pass code"
    out = {
        "ok": bool(ok),
        "retcode": rc,
        "retcode_name": retcode_name(rc),
        "comment": comment,
        "rule": rule,
        "margin": d.get("margin"),
        "margin_free": d.get("margin_free"),
        "balance": d.get("balance"),
        "equity": d.get("equity"),
        "profit": d.get("profit"),
        "margin_level": d.get("margin_level"),
        "raw": d,
    }
    if not ok:
        out["error_name"] = retcode_name(rc) if rc not in (None, 0) else "CHECK_REFUSED"
    return out


# --------------------------------------------------------------------------- #
# dynamic resolution against the live terminal (read-only probes)
# --------------------------------------------------------------------------- #
def resolve_filling(symbol_info: Any, mt5: Any, request: Dict[str, Any], *,
                    check_fn: Optional[Callable[[Dict[str, Any]], Any]] = None,
                    candidates: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Find the ``type_filling`` this terminal accepts **for this request**.

    The candidates come from the symbol's metadata; each one is put through the
    terminal's own ``order_check`` (read-only — nothing is sent) until one passes.
    When none passes, the *first* metadata candidate is kept so the failure is
    reported with the terminal's own reason for it.

    Returns ``{"filling", "name", "resolution", "probes": [...], "ok"}``.
    """
    cands = candidates or filling_candidates(symbol_info, mt5)
    probes: List[Dict[str, Any]] = []
    requested = request.get("type_filling") if isinstance(request, dict) else None

    def _check(req: Dict[str, Any]):
        fn = check_fn or getattr(mt5, "order_check", None)
        if fn is None:
            return None
        try:
            return fn(dict(req))
        except Exception as e:                      # pragma: no cover - terminal side
            log.warning("order_check raised while resolving the filling mode: %s", e)
            return None

    # 1. if the caller's own mode is already a metadata candidate, try it first
    ordered = list(cands)
    if requested is not None and all(int(c["value"]) != int(requested) for c in ordered):
        ordered.insert(0, {"value": int(requested), "name": filling_name(int(requested)),
                           "why": "the value already present in the request"})

    for cand in ordered:
        req = dict(request or {})
        req["type_filling"] = int(cand["value"])
        checked = _check(req)
        if checked is None:
            probes.append({**cand, "checked": False,
                           "note": "order_check unavailable in this build — metadata order used"})
            continue
        verdict = interpret_check_result(checked, mt5)
        probes.append({**cand, "checked": True, "ok": verdict["ok"],
                       "retcode": verdict["retcode"], "retcode_name": verdict["retcode_name"],
                       "comment": verdict["comment"], "rule": verdict["rule"],
                       "margin": verdict["margin"]})
        if verdict["ok"]:
            return {"ok": True, "filling": int(cand["value"]), "name": cand["name"],
                    "why": cand["why"], "resolution": "accepted by the terminal's own order_check",
                    "probes": probes, "check": verdict}

    first = cands[0]
    return {"ok": False, "filling": int(first["value"]), "name": first["name"],
            "why": first["why"],
            "resolution": ("none of the metadata-derived filling modes was accepted by the "
                           "terminal — the order must not be sent"),
            "probes": probes, "check": None}


# --------------------------------------------------------------------------- #
# deviation (also a points-scale value the broker decides)
# --------------------------------------------------------------------------- #
def effective_deviation(symbol_info: Any, base: int = 20, *,
                        spread_points: Optional[float] = None) -> Dict[str, Any]:
    """Slippage tolerance in **points**, widened to the symbol's own spread.

    ``deviation`` is in points, so the same number means different money on a
    2-digit XAUUSD symbol (0.01/point) than on a 5-digit FX pair. The terminal's
    own spread is the only sane floor: allow at least 3× the current spread, keep
    the operator's value when it is larger, and cap it so a spread spike cannot
    turn into a wild price tolerance.
    """
    sp = spread_points
    if sp is None:
        try:
            sp = float(getattr(symbol_info, "spread_points", None))
        except Exception:
            sp = None
    floor = 0
    if sp and sp > 0:
        floor = int(min(200, max(10, round(float(sp) * 3))))
    value = int(max(int(base or 0), floor))
    return {"value": value, "base": int(base or 0), "spread_points": sp,
            "floor_from_spread": floor,
            "rule": ("deviation is in points; at least 3× the symbol's own spread "
                     "(capped at 200 points) and never below the operator's value")}


def _as_dict(result: Any) -> Dict[str, Any]:
    """Mql* results are namedtuples; also accept dicts and plain objects."""
    if result is None:
        return {}
    if isinstance(result, dict):
        return dict(result)
    for attr in ("_asdict",):
        fn = getattr(result, attr, None)
        if callable(fn):
            try:
                return dict(fn())
            except Exception:
                pass
    fields = getattr(result, "_fields", None)
    if fields:
        return {f: getattr(result, f, None) for f in fields}
    try:
        return {k: v for k, v in vars(result).items() if not k.startswith("_")}
    except Exception:
        return {"value": result}
