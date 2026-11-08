"""Risk, distance and position-sizing configuration models.

Distance units are always EXPLICIT. Nothing in this module assumes a universal
pip size: pips/ticks/points resolve only against caller-supplied broker
metadata (see hero1_strategy_spec.validation). When metadata is missing the
validator reports NEEDS_METADATA — never a guessed default.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DistanceUnit(str, Enum):
    ABSOLUTE = "absolute"        # raw price distance (e.g. 3.5 USD)
    POINTS = "points"            # broker points (needs point_size)
    TICKS = "ticks"              # broker ticks (needs tick_size)
    PIPS = "pips"                # broker pips (needs pip_size)
    ATR_MULTIPLE = "atr_multiple"  # multiple of ATR


class Distance(_Strict):
    unit: DistanceUnit
    value: float = Field(gt=0)
    atr_period: Optional[int] = Field(default=None, ge=1, le=200,
                                      description="Required when unit = atr_multiple")

    @model_validator(mode="after")
    def _atr_period_rules(self):
        if self.unit is DistanceUnit.ATR_MULTIPLE and self.atr_period is None:
            raise ValueError("distance with unit 'atr_multiple' requires 'atr_period'")
        if self.unit is not DistanceUnit.ATR_MULTIPLE and self.atr_period is not None:
            raise ValueError("'atr_period' is only meaningful with unit 'atr_multiple'")
        return self


class ZeroableDistance(Distance):
    """Distance that may be exactly zero - e.g. a break-even rule locking
    0 profit. Still explicit about its unit; still never negative, never
    ambiguous."""

    value: float = Field(ge=0)


class StopLoss(_Strict):
    anchor: Literal["entry_price", "signal_low", "signal_high", "atr"] = "entry_price"
    distance: Distance


class TakeProfit(_Strict):
    """Exactly one of distance or ratio (risk:reward) must be declared."""
    distance: Optional[Distance] = None
    ratio: Optional[float] = Field(default=None, gt=0, le=100,
                                   description="risk:reward, e.g. 2.0 == 1:2")

    @model_validator(mode="after")
    def _exactly_one(self):
        if (self.distance is None) == (self.ratio is None):
            raise ValueError("take_profit requires exactly one of 'distance' or 'ratio'")
        return self


class PositionSizing(_Strict):
    mode: Literal["fixed_risk", "fixed_volume"]
    risk_pct: Optional[float] = Field(default=None, gt=0, le=100)
    risk_amount: Optional[float] = Field(default=None, gt=0, description="Account currency per trade")
    volume: Optional[float] = Field(default=None, gt=0, le=10_000, description="Lots")

    @model_validator(mode="after")
    def _mode_requirements(self):
        if self.mode == "fixed_risk":
            if self.risk_pct is None and self.risk_amount is None:
                raise ValueError("fixed_risk sizing requires 'risk_pct' or 'risk_amount'")
        elif self.mode == "fixed_volume":
            if self.volume is None:
                raise ValueError("fixed_volume sizing requires 'volume' (lots)")
        return self


class DirectionRisk(_Strict):
    """Optional per-direction overrides (e.g. tighter SL for shorts)."""
    stop_loss: Optional[StopLoss] = None
    take_profit: Optional[TakeProfit] = None
    risk_pct: Optional[float] = Field(default=None, gt=0, le=100)


class RiskConfig(_Strict):
    """Position sizing and direction-specific overrides.

    Exit behavior (stop-loss, take-profit, explicit exit, max holding,
    time exit) is declared on the `exits` section — it is authoritative and
    never duplicated here. Direction overrides may tighten exit distance per
    side for BOTH-direction strategies.
    """
    sizing: PositionSizing
    long: Optional[DirectionRisk] = None
    short: Optional[DirectionRisk] = None
