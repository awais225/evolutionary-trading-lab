"""Typed expression tree for strategy conditions.

A condition is structured DATA: logical combinators (and/or/not) over
comparisons. Comparisons operate on typed operands (candles, forming candles,
indicators, constants). There is no string-parsed or executable expression
anywhere in this module — by design.

Indexing semantics (lookahead-safe):
  * ``candle`` operands reference COMPLETED candles only. ``offset=0`` is the
    most recent completed (signal) candle, ``offset=1`` the previous one, etc.
  * ``forming_candle`` reads the unclosed candle and is ONLY legal when the
    execution config explicitly opts in (``candle="forming"`` +
    ``permit_forming_candle=true``). It is never silently treated as completed.
  * ``indicator`` operands carry their own ``offset`` in completed candles.
"""
from __future__ import annotations

from enum import Enum
from typing import Annotated, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from .indicators import IndicatorName, validate_indicator_params

MAX_TREE_NODES = 64          # safety bound — counted across logic nodes + operands
MAX_TREE_DEPTH = 8           # safety bound
MAX_OPERAND_OFFSET = 500     # candles back — beyond this the data window is unknowable


class CandleField(str, Enum):
    OPEN = "open"
    HIGH = "high"
    LOW = "low"
    CLOSE = "close"
    VOLUME = "volume"


class ComparisonOp(str, Enum):
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    EQ = "eq"
    NEQ = "neq"
    CROSS_ABOVE = "cross_above"
    CROSS_BELOW = "cross_below"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CandleOperand(_Strict):
    kind: Literal["candle"] = "candle"
    field: CandleField
    offset: int = Field(default=0, ge=0, le=MAX_OPERAND_OFFSET,
                        description="0 = latest completed candle, 1 = previous, 2 = two back …")


class FormingCandleOperand(_Strict):
    kind: Literal["forming_candle"] = "forming_candle"
    field: CandleField


class IndicatorOperand(_Strict):
    kind: Literal["indicator"] = "indicator"
    name: IndicatorName
    params: dict = Field(default_factory=dict)
    field: Optional[str] = Field(default=None, description="Multi-output selector, e.g. MACD histogram / BB upper")
    offset: int = Field(default=0, ge=0, le=MAX_OPERAND_OFFSET)

    def _check_params(self) -> list[str]:
        return validate_indicator_params(self.name, self.params, self.field)


class ConstantOperand(_Strict):
    kind: Literal["constant"] = "constant"
    value: float


Operand = Union[CandleOperand, FormingCandleOperand, IndicatorOperand, ConstantOperand]


class LogicAnd(_Strict):
    type: Literal["and"] = "and"
    children: List["LogicNode"] = Field(min_length=1, max_length=16)


class LogicOr(_Strict):
    type: Literal["or"] = "or"
    children: List["LogicNode"] = Field(min_length=1, max_length=16)


class LogicNot(_Strict):
    type: Literal["not"] = "not"
    child: "LogicNode"


class Comparison(_Strict):
    type: Literal["compare"] = "compare"
    op: ComparisonOp
    left: Operand = Field(discriminator="kind")
    right: Operand = Field(discriminator="kind")


LogicNode = Annotated[Union[LogicAnd, LogicOr, LogicNot, Comparison],
                     Field(discriminator="type")]

LogicAnd.model_rebuild()
LogicOr.model_rebuild()
LogicNot.model_rebuild()
Comparison.model_rebuild()


def walk(node: LogicNode, depth: int = 1):
    """Yield (node, depth) for every logic node, then (operand, depth+1)."""
    yield node, depth
    if isinstance(node, (LogicAnd, LogicOr)):
        for child in node.children:
            yield from walk(child, depth + 1)
    elif isinstance(node, LogicNot):
        yield from walk(node.child, depth + 1)
    elif isinstance(node, Comparison):
        yield (node.left, depth + 1)
        yield (node.right, depth + 1)


def tree_depth(node: LogicNode, depth: int = 1) -> int:
    if isinstance(node, (LogicAnd, LogicOr)):
        return max(tree_depth(c, depth + 1) for c in node.children)
    if isinstance(node, LogicNot):
        return tree_depth(node.child, depth + 1)
    return depth


def tree_size(node: LogicNode) -> int:
    return sum(1 for _ in walk(node))
