"""Broker-aware sizing utilities. NEVER assume a universal pip/point/tick.

The digits rule (proven in the legacy lab, retained verbatim):
    pip_size = 10 * point  when digits in (3, 5)   # fractional-pip FX feeds
    pip_size = 1  * point  when digits in (2, 4)   # whole-pip feeds (XAUUSD=2 -> pip == point)
Anything else (or missing inputs) -> None. None is a REFUSAL signal: callers
must not guess; validation reports NEEDS_METADATA instead.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Optional, Tuple


def pip_size_from_digits(digits: Optional[int], point: Optional[float]) -> Optional[float]:
    try:
        if point is None or digits is None:
            return None
        point_f = float(point)
        digits_i = int(digits)
    except (TypeError, ValueError):
        return None
    if point_f <= 0 or digits_i not in (2, 3, 4, 5):
        return None
    return round(10.0 * point_f, 12) if digits_i in (3, 5) else round(point_f, 12)


def points_per_pip_from_digits(digits: Optional[int]) -> Optional[float]:
    try:
        d = int(digits)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if d not in (2, 3, 4, 5):
        return None
    return 10.0 if d in (3, 5) else 1.0


def point_from_digits(digits: Optional[int]) -> Optional[float]:
    try:
        d = int(digits)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if d < 0 or d > 10:
        return None
    return 10.0 ** -d


def round_price_to_tick(price: Optional[float], tick_size: Optional[float],
                        digits: Optional[int] = None) -> Optional[float]:
    """Round a price to the broker's tick size (Decimal math; float in/out)."""
    if price is None:
        return None
    try:
        p = Decimal(str(price))
        if not p.is_finite():
            return None
        if tick_size is not None:
            t = Decimal(str(tick_size))
            if t.is_finite() and t > 0:
                ticks = (p / t).to_integral_value(rounding=ROUND_HALF_UP)
                out = ticks * t
                return float(out)
        if digits is not None:
            q = Decimal(1).scaleb(-int(digits))
            return float(p.quantize(q, rounding=ROUND_HALF_UP))
        return float(p)
    except (InvalidOperation, ValueError, TypeError, ArithmeticError):
        return None


def normalize_volume(volume: Optional[float], min_volume: Optional[float],
                     max_volume: Optional[float], step: Optional[float]) -> Tuple[Optional[float], Dict[str, object]]:
    """Normalize lot volume to the broker's [min, max] and step.

    Returns (normalized, report). normalized is None when inputs are missing or
    unsatisfiable (e.g. step <= 0) - never an invented lot size.
    """
    try:
        v = float(volume)  # type: ignore[arg-type]
        mn = float(min_volume) if min_volume is not None else None
        mx = float(max_volume) if max_volume is not None else None
        st = float(step) if step is not None else None
    except (TypeError, ValueError):
        return None, {"adjusted": False, "reason": "volume or constraint is not numeric"}
    if v <= 0:
        return None, {"adjusted": False, "reason": "volume must be positive"}
    if st is not None and st <= 0:
        return None, {"adjusted": False, "reason": "volume_step must be positive - refusing to guess"}
    report: Dict[str, object] = {"adjusted": False, "reasons": []}
    reasons = report["reasons"]
    if mn is not None and mn > 0 and v < mn:
        v = mn
        reasons.append(f"raised to min_volume {mn}")  # type: ignore[union-attr]
    if mx is not None and mx > 0 and v > mx:
        v = mx
        reasons.append(f"capped to max_volume {mx}")  # type: ignore[union-attr]
    if st is not None and st > 0:
        steps = round(v / st)
        snapped = steps * st
        if abs(snapped - v) > 1e-12:
            reasons.append(f"snapped to volume_step {st}")  # type: ignore[union-attr]
        v = snapped
    if v <= 0:
        return None, {**report, "reason": "normalisation produced a non-positive volume"}
    report["adjusted"] = bool(reasons)
    # final re-clamp after snapping
    if mn is not None and mn > 0 and v < mn:
        v = mn
        report["adjusted"] = True
    if mx is not None and mx > 0 and v > mx:
        v = mx
        report["adjusted"] = True
    return float(round(v, 10)), report


def stops_distance_check(price_distance: Optional[float], point: Optional[float],
                         stops_level_points: Optional[int],
                         freeze_level_points: Optional[int]) -> Dict[str, object]:
    """Client-side stops/freeze minimum distance (points * point). Read-only."""
    if price_distance is None or point is None or point <= 0:
        return {"ok": None, "reason": "distance or point missing - cannot establish the broker minimum"}
    lvl = max(int(stops_level_points or 0), int(freeze_level_points or 0))
    min_dist = lvl * float(point)
    ok = float(price_distance) >= min_dist
    return {"ok": ok, "min_distance": min_dist, "stops_level_points": stops_level_points,
            "freeze_level_points": freeze_level_points,
            "reason": None if ok else f"distance {price_distance} < broker minimum {min_dist} ({lvl} points)"}


__all__ = [
    "pip_size_from_digits", "points_per_pip_from_digits", "point_from_digits",
    "round_price_to_tick", "normalize_volume", "stops_distance_check",
]
