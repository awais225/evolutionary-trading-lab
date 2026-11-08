"""Hero1 strategy specification DSL — Phase 2.

Strategies are structured DATA (typed, versioned, hashable specs) — never
executable Python. Every origin (CUSTOM / WWW / INTERACTIVE / FINE_TUNED)
passes through the same validation and deterministic compilation pipeline.
"""
from .expressions import (
    CandleOperand, Comparison, ComparisonOp, ConstantOperand, FormingCandleOperand,
    IndicatorOperand, LogicAnd, LogicNot, LogicNode, LogicOr, MomentumOperand,
    ReferenceLevel, ReferenceOperand,
)
from .spec import (
    KNOWN_INSTRUMENTS, SUPPORTED_SCHEMA_VERSIONS, SUPPORTED_TIMEFRAMES,
    SCHEMA_VERSION, Direction, Identity, Origin, StrategySpec, Exits, ExitRule, ExecutionConfig,
)
from .management import (
    AppliesTo, TrailingStart, BreakEvenRule, SteppedTrailingRule,
    FixedTrailingRule, IndicatorTrailingRule, TradeManagement,
)
from .risk import Distance, DistanceUnit, PositionSizing, RiskConfig, StopLoss, TakeProfit, DirectionRisk
from .indicators import IndicatorName, indicator_warmup
from .validation import (
    Issue, ReportStatus, Severity, ValidationReport, validate_spec,
)
from .compiler import (
    COMPILER_VERSION, IR_VERSION, PRESENTATION_IDENTITY_FIELDS, CompileResult,
    SpecCompilationError, canonical_json, compile_spec, semantic_canonical, semantic_fingerprint,
)
from .examples import bullish_engulfing, ema_sma_stepped_trailing, example_spec, example_specs, EXAMPLE_TIMEFRAMES

__version__ = "1.2.0"
__phase__ = 2

__all__ = [
    "SCHEMA_VERSION", "COMPILER_VERSION", "IR_VERSION", "SUPPORTED_TIMEFRAMES",
    "KNOWN_INSTRUMENTS", "StrategySpec", "Identity", "Exits", "ExitRule", "ExecutionConfig",
    "AppliesTo", "TrailingStart", "BreakEvenRule", "SteppedTrailingRule",
    "FixedTrailingRule", "IndicatorTrailingRule", "TradeManagement",
    "Direction", "Origin", "LogicNode", "LogicAnd", "LogicOr", "LogicNot", "Comparison",
    "ComparisonOp", "CandleOperand", "FormingCandleOperand", "IndicatorOperand",
    "ConstantOperand", "ReferenceOperand", "ReferenceLevel", "MomentumOperand",
    "IndicatorName", "indicator_warmup",
    "Distance", "ZeroableDistance", "DistanceUnit", "PositionSizing", "RiskConfig", "StopLoss", "TakeProfit", "DirectionRisk",
    "Issue", "ReportStatus", "Severity", "ValidationReport", "validate_spec",
    "CompileResult", "SpecCompilationError", "canonical_json", "compile_spec",
    "PRESENTATION_IDENTITY_FIELDS", "semantic_canonical", "semantic_fingerprint",
    "bullish_engulfing", "ema_sma_stepped_trailing", "example_spec", "example_specs", "EXAMPLE_TIMEFRAMES",
]
