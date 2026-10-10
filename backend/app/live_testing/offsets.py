"""V6.5 §9 — experimental per-node SL/TP offsets (pure calculation).

Semantics (§9.2), applied to the strategy's ALREADY-COMPUTED default levels:

* ``offset = 0`` — the strategy's own level is used EXACTLY as computed;
* BUY:  positive SL offset moves SL farther BELOW the default (away from
  entry); positive TP offset moves TP DOWN toward entry (inward);
* SELL: positive SL offset moves SL farther ABOVE the default (away from
  entry); positive TP offset moves TP UP toward entry (inward);
* negative offsets are supported with the documented REVERSE meaning
  (SL inward, TP outward) — validated the same way, never silently ambiguous.

The result is normalized to the broker's tick size/precision and validated for
placement side (SL on the loss side, TP on the profit side) and the broker's
minimum stop distance.  An invalid adjusted level is RETURNED as a refusal —
the caller must not submit it.  Nothing here touches signals, sizing inputs
other than the resulting levels, or order construction.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from ..backtest.symbol_specs import (
    pips_to_price, round_to_tick, stops_distance_check,
)


def apply_offsets(*, side: str, entry: float,
                  sl: Optional[float], tp: Optional[float],
                  sl_offset_pips: Any = 0.0, tp_offset_pips: Any = 0.0,
                  specs: Any, stops_level_points: Any = None,
                  freeze_level_points: Any = None) -> Dict[str, Any]:
    """Return ``{"ok": True, "sl": .., "tp": .., "meta": {...}}`` or
    ``{"ok": False, "code": .., "reason": .., "meta": {...}}``."""
    side_u = (side or "").upper()
    try:
        sl_off = float(sl_offset_pips) if sl_offset_pips not in (None, "") else 0.0
        tp_off = float(tp_offset_pips) if tp_offset_pips not in (None, "") else 0.0
    except (TypeError, ValueError):
        return {"ok": False, "code": "OFFSET_INVALID",
                "reason": "SL/TP offset values must be numeric (pips)", "meta": {}}
    meta: Dict[str, Any] = {
        "sl_offset_pips": sl_off, "tp_offset_pips": tp_off,
        "sl_default": sl, "tp_default": tp,
        "sl_effective": sl, "tp_effective": tp,
        "offsets_applied": bool(sl_off or tp_off),
    }
    if not meta["offsets_applied"]:
        return {"ok": True, "sl": sl, "tp": tp, "meta": meta}
    if side_u not in ("BUY", "SELL"):
        return {"ok": False, "code": "OFFSET_INVALID",
                "reason": "offsets require a BUY or SELL direction", "meta": meta}
    pip = getattr(specs, "pip_size", None)
    if callable(pip):                       # tolerate a method-style accessor
        pip = pip()
    tick = getattr(specs, "tick_size", None) or pip
    digits = getattr(specs, "digits", None)
    if not pip:
        return {"ok": False, "code": "OFFSET_NO_PIP_CONVENTION",
                "reason": ("the instrument's pip convention is not established "
                           "(digits/point unknown) - refusing to apply offsets"),
                "meta": meta}
    meta["pip_size"] = pip

    sl_adj, tp_adj = sl, tp
    if sl_off and sl is not None:
        away = 1.0 if side_u == "SELL" else -1.0          # farther from entry
        sl_adj = float(sl) + away * pips_to_price(sl_off, pip)
    if tp_off and tp is not None:
        toward = 1.0 if side_u == "SELL" else -1.0        # toward entry
        # BUY: target is above entry -> inward is DOWN (-); SELL: below -> UP (+)
        tp_adj = float(tp) + toward * pips_to_price(tp_off, pip)
    sl_adj = round_to_tick(sl_adj, tick, digits) if sl_adj is not None else None
    tp_adj = round_to_tick(tp_adj, tick, digits) if tp_adj is not None else None

    # placement sanity (§9.3.5): SL on the loss side, TP on the profit side
    if sl_adj is not None:
        if (side_u == "BUY" and sl_adj >= float(entry)) or \
           (side_u == "SELL" and sl_adj <= float(entry)):
            meta.update({"sl_effective": sl_adj})
            return {"ok": False, "code": "OFFSET_LEVEL_INVALID",
                    "reason": (f"SL offset {sl_off:g} pips produced an invalid stop level "
                               f"{sl_adj} for {side_u} at {entry} - refusing to submit"),
                    "meta": meta}
    if tp_adj is not None:
        if (side_u == "BUY" and tp_adj <= float(entry)) or \
           (side_u == "SELL" and tp_adj >= float(entry)):
            meta.update({"tp_effective": tp_adj})
            return {"ok": False, "code": "OFFSET_LEVEL_INVALID",
                    "reason": (f"TP offset {tp_off:g} pips produced an invalid target level "
                               f"{tp_adj} for {side_u} at {entry} - refusing to submit"),
                    "meta": meta}

    # broker minimum stop distance (§9.3.4) — validated read-only, client-side
    if sl_adj is not None:
        point = getattr(specs, "point", None)
        stops = (stops_level_points if stops_level_points is not None
                 else getattr(specs, "stops_level_points", 0))
        freeze = (freeze_level_points if freeze_level_points is not None
                  else getattr(specs, "freeze_level_points", 0))
        chk = stops_distance_check(abs(float(sl_adj) - float(entry)), point, stops, freeze)
        if chk.get("checked") and not chk.get("ok"):
            meta.update({"sl_effective": sl_adj})
            return {"ok": False, "code": "OFFSET_STOPS_LEVEL",
                    "reason": (f"offset-adjusted SL violates broker distance rules: "
                               f"{chk.get('reason')}"),
                    "meta": meta}
    meta.update({"sl_effective": sl_adj, "tp_effective": tp_adj})
    return {"ok": True, "sl": sl_adj, "tp": tp_adj, "meta": meta}
