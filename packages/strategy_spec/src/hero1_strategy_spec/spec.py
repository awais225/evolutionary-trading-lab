"""Top-level strategy specification model (schema_version 1.0.0)."""
from __future__ import annotations

from enum import Enum
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .expressions import LogicNode
from .management import TradeManagement
from .risk import RiskConfig, StopLoss, TakeProfit

SCHEMA_VERSION = "1.1.0"
SUPPORTED_SCHEMA_VERSIONS = {"1.0.0", "1.1.0"}
SUPPORTED_TIMEFRAMES = ("M1", "M5", "M15", "M30", "H1", "H4", "D1")
KNOWN_INSTRUMENTS = ("XAUUSD", "EURUSD", "BTCUSD")
MAX_METADATA_ENTRIES = 32
MAX_TAGS = 16


class Direction(str, Enum):
    """Declared side(s) the strategy trades. Contract for the DeepTesting and
    Live Testing stages (never silently reversed):

    LONG   - the strategy is evaluated for LONG opportunities only.
    SHORT  - the strategy is evaluated for SHORT opportunities only.
    BOTH   - the strategy is evaluated for both sides; direction-sensitive
             semantics must be mirrored symmetrically by the consumer (never
             as a silent duplicate of one side's reading).

    Direction is part of the semantic fingerprint (renaming the node does not
    change it; changing LONG<->SHORT / ->BOTH forks the strategy identity).
    A strategy whose entry logic is inherently one-sided must declare that
    direction; BOTH is the default for new blank strategies.
    """
    LONG = "LONG"
    SHORT = "SHORT"
    BOTH = "BOTH"


class Origin(str, Enum):
    CUSTOM = "CUSTOM"
    WWW = "WWW"
    INTERACTIVE = "INTERACTIVE"
    FINE_TUNED = "FINE_TUNED"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Identity(_Strict):
    name: str = Field(min_length=1, max_length=160)
    description: Optional[str] = Field(default=None, max_length=4000)
    instrument: str = Field(min_length=3, max_length=12)
    timeframe: str = Field(description="One of M1,M5,M15,M30,H1,H4,D1 (validated centrally)")
    direction: Direction
    origin: Origin
    tags: List[str] = Field(default_factory=list, max_length=MAX_TAGS)
    metadata: Dict[str, str | int | float | bool] = Field(default_factory=dict)


class ExitRule(_Strict):
    """One prioritized explicit exit rule. When several rules' conditions
    hold at the same evaluation, the LOWEST priority value wins and the
    rest are not evaluated for that bar. Priorities must be unique within
    a strategy (validation enforces this; ties are never silently broken).
    The legacy single `explicit_exit` behaves as an implicit rule with
    priority 0 evaluated before all numbered rules."""
    name: str = Field(min_length=1, max_length=80)
    priority: int = Field(ge=1, le=1000)
    condition: LogicNode
    action: Literal["close_position"] = "close_position"


class Exits(_Strict):
    """Declared exit behaviour. At least one mechanism must be set
    (enforced in the validation engine): stop_loss, take_profit,
    explicit_exit, exit_rules, max_holding_candles, time_exit_utc.
    Never invented."""
    stop_loss: Optional[StopLoss] = None
    take_profit: Optional[TakeProfit] = None
    explicit_exit: Optional[LogicNode] = None
    exit_rules: List[ExitRule] = Field(default_factory=list, max_length=16)
    max_holding_candles: Optional[int] = Field(default=None, ge=1, le=100_000)
    time_exit_utc: Optional[str] = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$",
                                         description="Optional time-based exit, UTC HH:MM")


class ExecutionConfig(_Strict):
    entry_timing: Literal["next_bar_open", "signal_close"] = "next_bar_open"
    candle: Literal["completed", "forming"] = "completed"
    permit_forming_candle: bool = False
    order_type: Literal["market", "pending_stop"] = "market"
    position_handling: Literal["single", "pyramid"] = "single"


class StrategySpec(_Strict):
    schema_version: str = Field(description="Spec schema version; supported: 1.0.0")
    identity: Identity
    entry: LogicNode = Field(description="Entry condition expression tree")
    exits: Exits
    management: Optional[TradeManagement] = Field(
        default=None,
        description="Optional dynamic stop management (break-even / trailing). "
                    "Semantically absent when null - old specs keep their fingerprints.")
    risk: RiskConfig = Field(description="Sizing + direction overrides; exits are authoritative for exit behaviour")
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
