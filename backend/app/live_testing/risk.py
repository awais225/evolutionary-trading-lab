"""V4.3 risk-based position sizing (spec §7 - §10).

Everything here is pure calculation over *real* broker data: the risk amount
comes from the live MT5 account equity, the stop distance comes from the
node's own exit model (ATR multiples), and the volume conversion uses the
symbol's actual contract specification (tick size / tick value / volume
min-max-step). No pip approximation, no hard-coded balance, and - critically -
no silent clamping: a value outside the configured bounds raises a block so the
operator sees the problem instead of a quietly mangled trade size.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional

from ..config import get_config


class RiskBlock(RuntimeError):
    """Raised when a trade must not be executed for risk/safety reasons."""

    def __init__(self, code: str, message: str, detail: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# V6.5.1 §8 — the two explicit risk modes.
#
#   Mode A  PERCENT  — risk a percentage of an explicitly named capital basis
#                      (EQUITY = live MT5 account equity (default, the historical
#                      behaviour), BALANCE = account balance). The allowed loss
#                      is capital * pct / 100.
#   Mode B  AMOUNT   — risk a constant monetary amount if the stop is hit.
#
# The effective risk amount is always converted to volume through the broker's
# own contract specification (compute_volume). When these fields are unset the
# behaviour is EXACTLY the historical PERCENT/EQUITY path.
# --------------------------------------------------------------------------- #
RISK_MODE_PERCENT = "PERCENT"
RISK_MODE_AMOUNT = "AMOUNT"
RISK_MODES = (RISK_MODE_PERCENT, RISK_MODE_AMOUNT)
RISK_MODE_LABELS = {RISK_MODE_PERCENT: "Percentage of capital",
                    RISK_MODE_AMOUNT: "Constant monetary risk"}

CAPITAL_BASIS_EQUITY = "EQUITY"
CAPITAL_BASIS_BALANCE = "BALANCE"
CAPITAL_BASES = (CAPITAL_BASIS_EQUITY, CAPITAL_BASIS_BALANCE)
CAPITAL_BASIS_LABELS = {CAPITAL_BASIS_EQUITY: "account equity",
                        CAPITAL_BASIS_BALANCE: "account balance"}


def resolve_risk_mode(cfg: Any) -> Dict[str, Any]:
    """Normalize a node's risk-mode configuration (unset = PERCENT / EQUITY).

    Returns ``{"ok", "risk_mode", "capital_basis", "risk_amount", "code",
    "message"}``. An unparseable value is reported (ok=False), never coerced to a
    different mode.
    """
    cfg = cfg or {}
    raw_mode = cfg.get("risk_mode")
    raw_basis = cfg.get("risk_capital_basis")
    raw_amount = cfg.get("risk_amount")
    mode = str(raw_mode).strip().upper() if raw_mode not in (None, "") else RISK_MODE_PERCENT
    basis = str(raw_basis).strip().upper() if raw_basis not in (None, "") else CAPITAL_BASIS_EQUITY
    amount = _f(raw_amount) if raw_amount not in (None, "") else None
    out: Dict[str, Any] = {"ok": True, "risk_mode": mode, "capital_basis": basis,
                           "risk_amount": amount, "code": None, "message": None}
    if mode not in RISK_MODES:
        out.update({"ok": False, "code": "RISK_MODE_INVALID",
                    "message": f"risk_mode must be one of {list(RISK_MODES)} (got {raw_mode!r})"})
        return out
    if basis not in CAPITAL_BASES:
        out.update({"ok": False, "code": "RISK_CAPITAL_BASIS_INVALID",
                    "message": (f"risk_capital_basis must be one of {list(CAPITAL_BASES)} "
                                f"(got {raw_basis!r})")})
        return out
    if mode == RISK_MODE_AMOUNT:
        if amount is None or amount <= 0:
            out.update({"ok": False, "code": "RISK_AMOUNT_INVALID",
                        "message": ("constant monetary risk needs a positive risk_amount "
                                    f"(got {raw_amount!r})")})
            return out
    return out


def risk_limits() -> Dict[str, Any]:
    cfg = get_config().live_testing
    return {
        "risk_pct_default": float(cfg.risk_pct_default),
        "risk_pct_max": float(cfg.risk_pct_max),
        # V6.5.1 §8 — Mode B ceiling + the vocabulary the UI validates against
        "risk_amount_max": float(getattr(cfg, "risk_amount_max", 100000.0)),
        "risk_modes": list(RISK_MODES),
        "capital_bases": list(CAPITAL_BASES),
        "max_active_trades": int(cfg.max_active_trades),
        "tick_interval_s": float(cfg.tick_interval_s),
        "max_data_age_s": int(cfg.max_data_age_s),
        "require_sl": bool(cfg.require_sl),
    }


def resolve_per_node_limit(node: Any) -> Dict[str, Any]:
    """V6.5 §5 — the effective per-node max-active-trades limit.

    An explicit node override (``live_test_configs.max_positions``) wins; a node
    without one INHERITS ``config.live_testing.max_active_trades_per_node_default``
    (default 1).  The result always names its source so the UI can distinguish
    `Default` from an explicit override (§5.2).
    """
    cfg = (node or {}).get("config") or {}
    override = _f(cfg.get("max_positions"))
    default = int(getattr(get_config().live_testing,
                          "max_active_trades_per_node_default", 1) or 1)
    if override is not None and override >= 1:
        return {"effective": int(override), "source": "override", "default": default}
    return {"effective": int(default), "source": "default", "default": default}


def resolve_risk_pct(global_pct: Any, override_pct: Any) -> Dict[str, Any]:
    """Per-node override wins over the global default (spec §8)."""
    override = _f(override_pct)
    global_v = _f(global_pct)
    if global_v is None:
        global_v = float(get_config().live_testing.risk_pct_default)
    if override is not None:
        return {"risk_pct": override, "source": "node_override", "global_pct": global_v}
    return {"risk_pct": global_v, "source": "global_default", "global_pct": global_v}


def _spec_get(spec: Any, name: str) -> Optional[float]:
    if spec is None:
        return None
    if isinstance(spec, dict):
        return _f(spec.get(name))
    return _f(getattr(spec, name, None))


def compute_volume(*, symbol: str, side: str, entry: Any, sl: Any, risk_amount: Any,
                   spec: Any) -> Dict[str, Any]:
    """Volume from entry/SL distance and the broker's contract specification.

    ``volume = risk_amount / (stop_distance / tick_size * tick_value)``
    i.e. the money lost if the stop is hit, converted through the broker's own
    tick value instead of an assumed pip value.
    """
    sym = (symbol or "").upper()
    entry_f = _f(entry)
    sl_f = _f(sl)
    risk_f = _f(risk_amount)
    if entry_f is None or entry_f <= 0:
        raise RiskBlock("INVALID_ENTRY_PRICE", f"Entry price unavailable for {sym}: cannot size the trade")
    if sl_f is None or sl_f <= 0:
        raise RiskBlock("SL_MISSING", "Stop loss required for risk-based sizing (none resolved)")
    dist = abs(entry_f - sl_f)
    if dist <= 0:
        raise RiskBlock("SL_DISTANCE_ZERO", "Stop loss distance is zero: refusing to size the trade")
    if risk_f is None or risk_f <= 0:
        raise RiskBlock("RISK_AMOUNT_INVALID", "Risk amount is not positive")

    tick_size = _spec_get(spec, "trade_tick_size")
    tick_value = _spec_get(spec, "trade_tick_value")
    vmin = _spec_get(spec, "volume_min")
    vmax = _spec_get(spec, "volume_max")
    vstep = _spec_get(spec, "volume_step")
    digits = _spec_get(spec, "digits")
    contract = _spec_get(spec, "trade_contract_size")

    base = {"symbol": sym, "side": (side or "").upper(), "entry": entry_f, "sl": sl_f,
            "stop_distance": round(dist, 6), "risk_amount": round(risk_f, 2),
            "tick_size": tick_size, "tick_value": tick_value,
            "volume_min": vmin, "volume_max": vmax, "volume_step": vstep,
            "contract_size": contract,
            "spec_source": "broker_symbol_info"}

    if tick_size is None or tick_value is None:
        raise RiskBlock("INVALID_SYMBOL_DATA",
                        f"Broker specifications for {sym} are incomplete "
                        "(tick size / tick value unavailable)", base)
    if tick_size <= 0 or tick_value <= 0 or vstep is None or vmin is None or vmax is None:
        raise RiskBlock("INVALID_SYMBOL_DATA",
                        f"Broker volume specification for {sym} is invalid or incomplete", base)
    if vstep <= 0 or vmin <= 0 or vmax < vmin:
        raise RiskBlock("INVALID_SYMBOL_DATA",
                        f"Broker volume limits for {sym} are inconsistent", base)

    risk_per_lot = (dist / tick_size) * tick_value
    if risk_per_lot <= 0:
        raise RiskBlock("INVALID_SYMBOL_DATA",
                        f"Computed risk per lot for {sym} is not positive", base)

    raw = risk_f / risk_per_lot
    steps = math.floor(raw / vstep + 1e-9)
    # round to the broker's volume precision (derived from the step itself)
    if vstep >= 1:
        ndec = 0
    else:
        ndec = min(8, max(0, int(math.ceil(-math.log10(vstep) - 1e-9))))
    vol = round(steps * vstep, ndec)

    out = dict(base)
    out.update({"risk_per_lot": round(risk_per_lot, 4), "raw_volume": round(raw, 8),
                "volume": round(vol, 4) if vol else 0.0})
    if vol < vmin - 1e-12:
        raise RiskBlock("VOLUME_BELOW_MINIMUM",
                        f"Risk {risk_f:.2f} needs {raw:.4f} lots below the broker minimum "
                        f"{vmin} lots for {sym}: reduce the stop distance or accept the "
                        "minimum lot manually", out)
    if vol > vmax + 1e-12:
        raise RiskBlock("VOLUME_ABOVE_MAXIMUM",
                        f"Required volume {vol} exceeds the broker maximum {vmax} lots for {sym}", out)
    out["actual_risk"] = round(vol * risk_per_lot, 2)
    if digits is not None:
        out["digits"] = int(digits)
    return out


def risk_for_volume(*, symbol: str, side: str, entry: Any, sl: Any, volume: Any,
                    spec: Any) -> Dict[str, Any]:
    """Money at risk for an already-chosen volume (the reverse of compute_volume).

    V4.8: used by the read-only order preview so the dashboard can convert
    lot size -> risk with the *same* broker tick math (tick size / tick value
    from the symbol specification) instead of duplicating it in the browser.
    Pure calculation: it never touches the bridge, the database or an order.
    """
    sym = (symbol or "").upper()
    entry_f = _f(entry)
    sl_f = _f(sl)
    vol_f = _f(volume)
    if entry_f is None or entry_f <= 0:
        raise RiskBlock("INVALID_ENTRY_PRICE", f"Entry price unavailable for {sym}: cannot size the trade")
    if sl_f is None or sl_f <= 0:
        raise RiskBlock("SL_MISSING", "Stop loss required for risk sizing (none resolved)")
    dist = abs(entry_f - sl_f)
    if dist <= 0:
        raise RiskBlock("SL_DISTANCE_ZERO", "Stop loss distance is zero: refusing to size the trade")
    if vol_f is None or vol_f <= 0:
        raise RiskBlock("VOLUME_INVALID", "Lot size is not positive")

    tick_size = _spec_get(spec, "trade_tick_size")
    tick_value = _spec_get(spec, "trade_tick_value")
    vmin = _spec_get(spec, "volume_min")
    vstep = _spec_get(spec, "volume_step")
    digits = _spec_get(spec, "digits")
    base = {"symbol": sym, "side": (side or "").upper(), "entry": entry_f, "sl": sl_f,
            "stop_distance": round(dist, 6), "volume": vol_f,
            "tick_size": tick_size, "tick_value": tick_value,
            "volume_min": vmin, "volume_step": vstep, "spec_source": "broker_symbol_info"}
    if tick_size is None or tick_value is None or tick_size <= 0 or tick_value <= 0:
        raise RiskBlock("INVALID_SYMBOL_DATA",
                        f"Broker specifications for {sym} are incomplete "
                        "(tick size / tick value unavailable)", base)
    risk_per_lot = (dist / tick_size) * tick_value
    out = dict(base)
    out.update({"risk_per_lot": round(risk_per_lot, 4),
                "actual_risk": round(vol_f * risk_per_lot, 2)})
    if vmin is not None and vol_f < vmin - 1e-12:
        out["below_minimum"] = True
        out["minimum_risk"] = round(vmin * risk_per_lot, 2)
    if digits is not None:
        out["digits"] = int(digits)
    return out


def compute_risk(*, node_id: int, strategy_id: Optional[int], symbol: str, side: str,
                 entry: Any, sl: Any, equity: Any, global_pct: Any, override_pct: Any,
                 spec: Any, risk_mode: Any = None, risk_amount: Any = None,
                 capital_basis: Any = None, balance: Any = None) -> Dict[str, Any]:
    """Validate the risk configuration, size the trade and return the full trace.

    V6.5.1 §8 — two explicit modes (default = the historical behaviour):

    * ``PERCENT`` (Mode A): allowed loss = capital * pct / 100, where capital is
      the configured basis — ``EQUITY`` (default; live MT5 equity, required) or
      ``BALANCE`` (live MT5 balance, required). The basis is named in the trace.
    * ``AMOUNT``  (Mode B): allowed loss = the configured constant monetary
      ``risk_amount`` (account currency), independent of capital.

    Both modes convert money -> volume through the broker's own tick size /
    tick value / volume step (``compute_volume``), round the volume DOWN to the
    step, and block (never clamp) when the result is invalid or out of bounds.
    Documented limitations: worst-case loss assumes the stop fills at its price
    (no gap/slippage/swap/commission modelling here) and no account-currency
    conversion is applied to the instrument's tick value.
    """
    limits = risk_limits()
    equity_f = _f(equity)
    balance_f = _f(balance)

    mode_cfg = resolve_risk_mode({"risk_mode": risk_mode, "risk_amount": risk_amount,
                                  "risk_capital_basis": capital_basis})
    if not mode_cfg["ok"]:
        raise RiskBlock(mode_cfg["code"], mode_cfg["message"],
                        {"risk_mode": risk_mode, "capital_basis": capital_basis,
                         "risk_amount": risk_amount})
    mode = mode_cfg["risk_mode"]
    basis = mode_cfg["capital_basis"]

    resolved = resolve_risk_pct(global_pct, override_pct)
    pct = resolved["risk_pct"]

    if mode == RISK_MODE_AMOUNT:
        # Mode B — constant monetary risk. The pct settings are NOT consulted
        # (they configure a different mode) but remain visible in the trace.
        risk_amount_f = mode_cfg["risk_amount"]
        if risk_amount_f is None or risk_amount_f <= 0:
            raise RiskBlock("RISK_AMOUNT_INVALID",
                            "Constant monetary risk needs a positive risk_amount")
        if risk_amount_f > limits["risk_amount_max"]:
            raise RiskBlock("RISK_AMOUNT_ABOVE_MAXIMUM",
                            f"Risk amount {risk_amount_f:g} exceeds the configured maximum "
                            f"{limits['risk_amount_max']:g}: trade blocked (no silent clamping)",
                            {"risk_amount": risk_amount_f,
                             "risk_amount_max": limits["risk_amount_max"]})
        risk_amount_final = risk_amount_f
        capital_used = equity_f if basis == CAPITAL_BASIS_EQUITY else balance_f
    else:
        # Mode A — percentage of the explicitly named capital basis.
        if pct is None or pct <= 0:
            raise RiskBlock("RISK_PCT_INVALID", f"Risk per trade must be greater than 0 (got {pct})")
        if pct > limits["risk_pct_max"]:
            raise RiskBlock("RISK_PCT_ABOVE_MAXIMUM",
                            f"Risk {pct:g}% exceeds the configured maximum "
                            f"{limits['risk_pct_max']:g}%: trade blocked (no silent clamping)",
                            {"risk_pct": pct, "risk_pct_max": limits["risk_pct_max"]})
        if basis == CAPITAL_BASIS_BALANCE:
            if balance_f is None or balance_f <= 0:
                raise RiskBlock("ACCOUNT_BALANCE_UNAVAILABLE",
                                "Account balance is unavailable from MT5: refusing to size the trade")
            capital_used = balance_f
        else:
            if equity_f is None or equity_f <= 0:
                raise RiskBlock("ACCOUNT_EQUITY_UNAVAILABLE",
                                "Account equity is unavailable from MT5: refusing to size the trade")
            capital_used = equity_f
        risk_amount_final = capital_used * pct / 100.0

    if limits["require_sl"]:
        sl_f = _f(sl)
        if sl_f is None or sl_f <= 0:
            raise RiskBlock("SL_MISSING", "Stop loss required: the trade has no SL to size against")

    sizing = compute_volume(symbol=symbol, side=side, entry=entry, sl=sl,
                            risk_amount=risk_amount_final, spec=spec)
    trace = {
        "node_id": node_id, "strategy_id": strategy_id, "symbol": sizing["symbol"],
        "side": sizing["side"], "equity": round(equity_f, 2) if equity_f is not None else None,
        "risk_mode": mode, "capital_basis": basis,
        "capital": round(capital_used, 2) if capital_used is not None else None,
        "risk_pct": pct if mode == RISK_MODE_PERCENT else None,
        "risk_pct_configured": pct,
        "risk_pct_source": resolved["source"], "global_risk_pct": resolved["global_pct"],
        "risk_pct_max": limits["risk_pct_max"],
        "risk_amount": round(risk_amount_final, 2),
        "risk_amount_max": limits["risk_amount_max"],
        "volume": sizing["volume"], "sizing": sizing,
    }
    return trace


def validate_risk_settings(risk_pct: Any, node_id: Optional[int] = None,
                           *, risk_mode: Any = None, risk_amount: Any = None,
                           capital_basis: Any = None) -> Dict[str, Any]:
    """Config-time validation (used by the config API) - read-only, no side effects.

    V6.5.1 §8 — also validates the risk-mode fields when they are present. A
    payload that omits them keeps the historical risk_pct-only validation.
    """
    limits = risk_limits()
    if any(v not in (None, "") for v in (risk_mode, risk_amount, capital_basis)):
        mode_cfg = resolve_risk_mode({"risk_mode": risk_mode, "risk_amount": risk_amount,
                                      "risk_capital_basis": capital_basis})
        if not mode_cfg["ok"]:
            return {"ok": False, "code": mode_cfg["code"], "message": mode_cfg["message"],
                    "limits": limits}
        if mode_cfg["risk_mode"] == RISK_MODE_AMOUNT:
            amt = mode_cfg["risk_amount"]
            if amt is not None and amt > limits["risk_amount_max"]:
                return {"ok": False, "code": "RISK_AMOUNT_ABOVE_MAXIMUM",
                        "message": (f"Risk amount {amt:g} is above the configured maximum "
                                    f"{limits['risk_amount_max']:g} - not saved"),
                        "limits": limits}
    pct = _f(risk_pct)
    if pct is None:
        return {"ok": True, "risk_pct": None, "limits": limits}
    if pct <= 0:
        return {"ok": False, "code": "RISK_PCT_INVALID",
                "message": "Risk per trade must be greater than 0", "limits": limits}
    if pct > limits["risk_pct_max"]:
        return {"ok": False, "code": "RISK_PCT_ABOVE_MAXIMUM",
                "message": f"Risk {pct:g}% is above the configured maximum "
                           f"{limits['risk_pct_max']:g}% - not saved",
                "limits": limits}
    return {"ok": True, "risk_pct": pct, "limits": limits}
