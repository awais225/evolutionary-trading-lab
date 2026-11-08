"""Dynamic trade-management rules (Phase 2 completion).

These models define HOW protective stops evolve after entry. They are
pure specification - deterministic, engine-agnostic, and shared verbatim
between the future DeepTesting and Live Testing engines. The semantics
(contract) are implemented by the pure reference evaluator in
`hero1_exec_semantics.stop_management`; engines must reproduce it.

Units: every distance is a `risk.Distance` with an explicit unit
(absolute / points / ticks / pips / atr_multiple). Nothing here assumes a
universal pip size; values resolve only against broker metadata.

Design rules (unambiguous on purpose):
  * A management rule NEVER moves a stop backwards and never loosens an
    existing protective stop (LONG: candidate SL may only rise; SHORT:
    may only fall).
  * Rules are evaluated from the BEST FAVOURABLE price of the position's
    reference side (LONG: bid, SHORT: ask - the price the position could
    actually be closed at), never from last/candle-mid heuristics.
  * Evaluation is idempotent: re-evaluating with unchanged inputs yields
    the same stop; one evaluation applies at most the adjustments the
    excursion has earned (step jumps collapse to the final earned step).
  * A stepped rule earns floor((excursion - activation) / step) steps.
  * The 30-pip numbers in examples are EXAMPLES, never defaults baked
    into execution: every distance is user-configured per strategy.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .expressions import IndicatorOperand
from .risk import Distance, DistanceUnit, ZeroableDistance


class AppliesTo(str, Enum):
    """Direction scope of a management rule. Mirrors spec.Direction
    (LONG / SHORT / BOTH) without a circular import; validation checks it
    against the strategy's declared direction."""

    LONG = "LONG"
    SHORT = "SHORT"
    BOTH = "BOTH"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TrailingStart(str, Enum):
    AFTER_ACTIVATION = "after_activation"  # trail only once activation is reached
    IMMEDIATE = "immediate"                # trail from entry (activation must be 0-absent)


class BreakEvenRule(_Strict):
    """Move the stop to entry +/- locked profit once activation is reached."""
    activation: Distance = Field(description="Favourable distance from entry that arms break-even")
    locked_profit: Distance = Field(
        description="Profit locked in by the moved stop (0 in the rule's unit = exact "
                    "entry price). For LONG the stop becomes entry + locked; SHORT: entry - locked.")
    applies_to: AppliesTo = AppliesTo.BOTH


class SteppedTrailingRule(_Strict):
    """Stepped trailing stop (the general form of the '30-pip ladder').

    Once the favourable excursion reaches `activation`, the stop is
    placed at entry + `initial_locked_profit` (or exactly entry when
    `first_step_to_breakeven` is set - which is precisely
    initial_locked_profit == 0 made explicit; both must agree). Every
    further COMPLETE `step` of excursion advances the stop by
    `advance_per_step`. The stop never moves backwards, and an optional
    `max_trail_distance` caps how far behind the best favourable price
    the stop may lag... expressed as the minimum favourable excursion
    distance kept between best price and stop once fully trailing.
    """
    activation: Optional[Distance] = Field(
        default=None,
        description="Favourable distance arming the rule. REQUIRED for "
                    "start='after_activation'; MUST be null for start='immediate' "
                    "(trailing from entry) - the pair is never ambiguous.")
    initial_locked_profit: ZeroableDistance = Field(
        description="Locked profit at the moment trailing activates")
    step: Distance = Field(description="Excursion interval that earns one advancement")
    advance_per_step: Distance = Field(description="Stop advancement earned per completed step")
    first_step_to_breakeven: bool = Field(
        default=False,
        description="Explicit statement that the first earned position is entry price "
                    "(breakeven). Must agree with initial_locked_profit == 0.")
    start: TrailingStart = TrailingStart.AFTER_ACTIVATION
    max_trail_distance: Optional[Distance] = Field(
        default=None,
        description="Optional cap: the stop trails at most this distance behind the "
                    "best favourable price once active")
    applies_to: AppliesTo = AppliesTo.BOTH

    @model_validator(mode="after")
    def _start_activation_agreement(self):
        if self.start is TrailingStart.AFTER_ACTIVATION and self.activation is None:
            raise ValueError("start='after_activation' requires an 'activation' distance")
        if self.start is TrailingStart.IMMEDIATE and self.activation is not None:
            raise ValueError("start='immediate' (trail from entry) requires activation=null; "
                             "a positive activation contradicts immediate trailing")
        return self

    @model_validator(mode="after")
    def _breakeven_agreement(self):
        locks_zero = float(self.initial_locked_profit.value) == 0.0
        if locks_zero != self.first_step_to_breakeven:
            raise ValueError(
                "first_step_to_breakeven and initial_locked_profit must agree: "
                "initial_locked_profit.value == 0 IS the breakeven first step - set "
                "first_step_to_breakeven=true, or lock a positive "
                "initial_locked_profit with first_step_to_breakeven=false "
                "(no silent precedence between the two declarations)")
        return self


class FixedTrailingRule(_Strict):
    """Classic fixed-distance trailing: once active, the stop follows the
    best favourable price at a constant `trail_distance` behind it."""
    activation: Optional[Distance] = Field(
        default=None,
        description="Favourable distance arming the rule. REQUIRED for "
                    "start='after_activation'; MUST be null for start='immediate'.")
    trail_distance: Distance
    start: TrailingStart = TrailingStart.AFTER_ACTIVATION
    applies_to: AppliesTo = AppliesTo.BOTH


    @model_validator(mode="after")
    def _start_activation_agreement(self):
        if self.start is TrailingStart.AFTER_ACTIVATION and self.activation is None:
            raise ValueError("start='after_activation' requires an 'activation' distance")
        if self.start is TrailingStart.IMMEDIATE and self.activation is not None:
            raise ValueError("start='immediate' (trail from entry) requires activation=null; "
                             "a positive activation contradicts immediate trailing")
        return self


class IndicatorTrailingRule(_Strict):
    """Stop follows an indicator value (e.g. ATR-band / EMA) offset by a distance.

    Specification only in Phase 2: engines evaluate the indicator on
    completed candles; the reference evaluator refuses it explicitly
    rather than guessing indicator mathematics (Phase 3 owns indicator
    computation, shared by both engines).
    """
    indicator: IndicatorOperand
    offset: Optional[Distance] = Field(
        default=None,
        description="Optional distance kept between indicator value and stop; null = none")
    activation: Optional[Distance] = Field(
        default=None, description="Optional favourable excursion required before following starts")
    applies_to: AppliesTo = AppliesTo.BOTH


class TradeManagement(_Strict):
    """Post-entry stop management. All members are optional; combining
    break-even with a trailing rule is legal - evaluation composes them
    (the most protective resulting stop wins). Multiple trailing rules of
    different kinds likewise compose to the most protective stop."""
    break_even: Optional[BreakEvenRule] = None
    stepped_trailing: Optional[SteppedTrailingRule] = None
    fixed_trailing: Optional[FixedTrailingRule] = None
    indicator_trailing: Optional[IndicatorTrailingRule] = None


__all__ = ["AppliesTo", "TrailingStart", "BreakEvenRule", "SteppedTrailingRule",
           "FixedTrailingRule", "IndicatorTrailingRule", "TradeManagement"]
