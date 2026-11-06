"""Hero1 strategy specification DSL — Phase 2.

Strategies are structured DATA (typed, versioned, hashable specs) — never
executable Python. Every origin (CUSTOM / WWW / INTERACTIVE / FINE_TUNED)
passes through the same validation and deterministic compilation pipeline.
"""
from .expressions import (
    CandleOperand, Comparison, ComparisonOp, ConstantOperand, FormingCandleOperand,
    IndicatorOperand, LogicAnd, LogicNot, LogicNode, LogicOr,
)
from .spec import (
    KNOWN_INSTRUMENTS, SUPPORTED_SCHEMA_VERSIONS, SUPPORTED_TIMEFRAMES,
    SCHEMA_VERSION, Direction, Identity, Origin, StrategySpec, Exits, ExecutionConfig,
)
from .risk import Distance, DistanceUnit, PositionSizing, RiskConfig, StopLoss, TakeProfit, DirectionRisk
from .indicators import IndicatorName, indicator_warmup
from .validation import (
    Issue, ReportStatus, Severity, ValidationReport, validate_spec,
)
from .compiler import COMPILER_VERSION, IR_VERSION, CompileResult, SpecCompilationError, canonical_json, compile_spec
from .examples import bullish_engulfing, example_spec, example_specs, EXAMPLE_TIMEFRAMES

__version__ = "1.1.0"
__phase__ = 2

__all__ = [
    "SCHEMA_VERSION", "COMPILER_VERSION", "IR_VERSION", "SUPPORTED_TIMEFRAMES",
    "KNOWN_INSTRUMENTS", "StrategySpec", "Identity", "Exits", "ExecutionConfig",
    "Direction", "Origin", "LogicNode", "LogicAnd", "LogicOr", "LogicNot", "Comparison",
    "ComparisonOp", "CandleOperand", "FormingCandleOperand", "IndicatorOperand",
    "ConstantOperand", "IndicatorName", "indicator_warmup",
    "Distance", "DistanceUnit", "PositionSizing", "RiskConfig", "StopLoss", "TakeProfit", "DirectionRisk",
    "Issue", "ReportStatus", "Severity", "ValidationReport", "validate_spec",
    "CompileResult", "SpecCompilationError", "canonical_json", "compile_spec",
    "bullish_engulfing", "example_spec", "example_specs", "EXAMPLE_TIMEFRAMES",
]
