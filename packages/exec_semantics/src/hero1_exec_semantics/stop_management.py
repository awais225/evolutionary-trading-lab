"""Reference evaluation semantics for dynamic stop management (Phase 2).

ONE definition shared by DeepTesting and Live Testing: given the same
position state and the same price context, every engine MUST produce the
same next stop. Engines differ in data arrival and fill modelling, never
in this arithmetic. Pure functions - no I/O, no clocks, no MT5.

Contract (the ambiguities the brief required to be eliminated):
* Reference price: the position's CLOSING-side price - LONG positions are
  evaluated on BID, SHORT on ASK (a long is closed by selling at bid).
  `best_favourable` is the best such price observed since entry; engines
  own tracking it (tick for live, bar-extreme for backtests - documented
  per engine), this module only consumes it.
* Activation: a rule is ARMED once favourable excursion
  (LONG: best - entry; SHORT: entry - best) reaches its activation
  distance. start='immediate' arms at entry (activation is null by schema).
* Composition: when several rules produce candidates, the MOST PROTECTIVE
  wins (LONG: highest stop; SHORT: lowest). No silent precedence.
* Stepped ladder: steps = floor((excursion - activation) / step); the stop
  target is entry +/- (initial_locked + steps * advance). Excursion that
  jumps several steps lands on the final earned step in ONE evaluation.
  max_trail_distance additionally caps how far behind `best` the stop may
  lag (target = max(target, best - max_trail) for LONG).
* Never backwards / never loosens: the result is clamped against the
  current stop (LONG: result >= current; SHORT: result <= current).
* Idempotent: re-evaluating with the produced stop as `current_stop`
  returns the same stop with changed=False.
* Rounding: candidates round TOWARD the position (LONG: floor to the tick
  grid, SHORT: ceil) so rounding can only tighten, then the never-backward
  clamp applies. Without tick_size in context, no rounding is applied.
* Invalid candidate: a candidate at or beyond the reference price is
  clamped one tick inside the market and flagged CLAMPED_INSIDE_MARKET
  (the engine then treats the stop as immediately marketable). Without a
  tick size it cannot be clamped safely and raises SemanticsError.
* Units: every Distance resolves through PriceContext (pip/point/tick
  sizes from the broker symbol snapshot, atr from the engine's ATR
  series). Missing context raises SemanticsError - never a guess.
* indicator_trailing is DEFINITION-ONLY in Phase 2 (indicator math is
  Phase 3): evaluating a spec containing it raises
  SemanticsError(INDICATOR_TRAILING_NOT_EVALUABLE_PHASE2). Explicit
  refusal, never an approximation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional

from hero1_strategy_spec.management import TradeManagement
from hero1_strategy_spec.risk import Distance, DistanceUnit


class SemanticsError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PriceContext:
    pip_size: Optional[float] = None
    point_size: Optional[float] = None
    tick_size: Optional[float] = None
    atr_value: Optional[float] = None


@dataclass(frozen=True)
class RuleEffect:
    rule: str                 # break_even | stepped_trailing | fixed_trailing
    active: bool
    candidate: Optional[float]
    steps_completed: int = 0
    excursion: float = 0.0


@dataclass(frozen=True)
class StopEvaluation:
    new_stop: float
    previous_stop: Optional[float]
    changed: bool
    clamped_inside_market: bool
    rules: List[RuleEffect] = field(default_factory=list)


def resolve_distance(distance: Distance, ctx: PriceContext, what: str = "distance") -> float:
    unit = distance.unit
    if unit is DistanceUnit.ABSOLUTE:
        return float(distance.value)
    needs = {DistanceUnit.PIPS: ("pip_size", ctx.pip_size),
             DistanceUnit.POINTS: ("point_size", ctx.point_size),
             DistanceUnit.TICKS: ("tick_size", ctx.tick_size),
             DistanceUnit.ATR_MULTIPLE: ("atr_value", ctx.atr_value)}
    key, value = needs[unit]
    if value is None or value <= 0:
        raise SemanticsError(
            "MISSING_PRICE_CONTEXT",
            f"{what}: unit '{unit.value}' needs {key} from broker/engine metadata; "
            "refusing to guess a pip/point/tick size")
    return float(distance.value) * float(value)


def _applies(applies_to: str, side: str) -> bool:
    return applies_to in ("BOTH", side)


def _round_toward_position(price: float, side: str, tick: Optional[float]) -> float:
    if not tick:
        return price
    steps = price / tick
    steps = math.floor(steps + 1e-9) if side == "LONG" else math.ceil(steps - 1e-9)
    return round(steps * tick, 10)


def evaluate_stop_management(*, side: str, entry_price: float,
                             current_stop: Optional[float],
                             best_favourable: float,
                             management: TradeManagement,
                             ctx: PriceContext) -> StopEvaluation:
    """Compute the next stop. Stateless: call with the same inputs, get the
    same outputs (idempotence falls out of the monotonic clamp)."""
    if side not in ("LONG", "SHORT"):
        raise SemanticsError("BAD_SIDE", f"side must be LONG or SHORT, got {side!r}")
    if management.indicator_trailing is not None and _applies(
            management.indicator_trailing.applies_to.value, side):
        raise SemanticsError(
            "INDICATOR_TRAILING_NOT_EVALUABLE_PHASE2",
            "indicator-based trailing is defined in the spec but its evaluation "
            "semantics arrive with the Phase 3 indicator engine; refusing to "
            "approximate it in DeepTesting or Live Testing")
    sign = 1.0 if side == "LONG" else -1.0
    excursion = sign * (best_favourable - entry_price)
    effects: List[RuleEffect] = []
    candidates: List[float] = []

    def arm(rule, label: str) -> tuple[bool, float]:
        if rule.start.value == "immediate":
            return True, 0.0
        act = resolve_distance(rule.activation, ctx, f"{label}.activation")
        return excursion >= act, act

    if management.break_even is not None and _applies(management.break_even.applies_to.value, side):
        rule, label = management.break_even, "break_even"
        act = resolve_distance(rule.activation, ctx, "break_even.activation")
        locked = resolve_distance(rule.locked_profit, ctx, "break_even.locked_profit")
        active = excursion >= act
        cand = entry_price + sign * locked if active else None
        effects.append(RuleEffect(label, active, cand, 0, excursion))
        if cand is not None:
            candidates.append(cand)

    if management.stepped_trailing is not None and _applies(management.stepped_trailing.applies_to.value, side):
        rule, label = management.stepped_trailing, "stepped_trailing"
        active, act = arm(rule, label)
        cand, steps = None, 0
        if active:
            step = resolve_distance(rule.step, ctx, "stepped_trailing.step")
            advance = resolve_distance(rule.advance_per_step, ctx, "stepped_trailing.advance_per_step")
            locked0 = resolve_distance(rule.initial_locked_profit, ctx, "stepped_trailing.initial_locked_profit")
            steps = max(0, math.floor((excursion - act) / step + 1e-12)) if step > 0 else 0
            cand = entry_price + sign * (locked0 + steps * advance)
            if rule.max_trail_distance is not None:
                max_trail = resolve_distance(rule.max_trail_distance, ctx, "stepped_trailing.max_trail_distance")
                floor_price = best_favourable - sign * max_trail
                cand = max(cand, floor_price) if side == "LONG" else min(cand, floor_price)
        effects.append(RuleEffect(label, active, cand, steps, excursion))
        if cand is not None:
            candidates.append(cand)

    if management.fixed_trailing is not None and _applies(management.fixed_trailing.applies_to.value, side):
        rule, label = management.fixed_trailing, "fixed_trailing"
        active, _act = arm(rule, label)
        cand = None
        if active:
            trail = resolve_distance(rule.trail_distance, ctx, "fixed_trailing.trail_distance")
            cand = best_favourable - sign * trail
        effects.append(RuleEffect(label, active, cand, 0, excursion))
        if cand is not None:
            candidates.append(cand)

    if not candidates:
        keep = current_stop if current_stop is not None else 0.0
        return StopEvaluation(keep, current_stop, False, False, effects)

    candidate = max(candidates) if side == "LONG" else min(candidates)
    clamped = False
    inside = best_favourable - sign * candidate  # >0: stop strictly inside market
    if inside <= 0:
        if not ctx.tick_size:
            raise SemanticsError(
                "INVALID_CANDIDATE_NO_TICK",
                "management produced a stop at/beyond the reference price and no "
                "tick_size is available to clamp it inside the market")
        candidate = best_favourable - sign * ctx.tick_size
        clamped = True
    candidate = _round_toward_position(candidate, side, ctx.tick_size)
    new_stop = candidate
    if current_stop is not None:
        new_stop = max(current_stop, candidate) if side == "LONG" else min(current_stop, candidate)
    return StopEvaluation(new_stop, current_stop,
                          current_stop is None or new_stop != current_stop,
                          clamped, effects)


__all__ = ["PriceContext", "RuleEffect", "StopEvaluation", "SemanticsError",
           "evaluate_stop_management", "resolve_distance"]
