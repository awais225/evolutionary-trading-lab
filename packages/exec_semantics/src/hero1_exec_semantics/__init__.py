"""HERO1 shared execution semantics - ONE definition of what a compiled
strategy DOES at runtime, consumed identically by DeepTesting (Phase 3)
and MT5 Live Testing (Phase 5). Engines may differ in data arrival and
fill modelling; they may never reinterpret rules.

Phase 2 provides the pure reference evaluator for dynamic stop
management (`stop_management`): activation, stepped/fixed trailing,
break-even composition, never-backwards clamping, tick rounding and
explicit refusal of not-yet-defined semantics. Indicator mathematics and
bar/tick simulation arrive in Phase 3 and must call, not re-implement,
this module."""
from .stop_management import (
    PriceContext, RuleEffect, SemanticsError, StopEvaluation,
    evaluate_stop_management, resolve_distance,
)

__version__ = "0.2.0"

__all__ = ["PriceContext", "RuleEffect", "SemanticsError", "StopEvaluation",
           "evaluate_stop_management", "resolve_distance", "__version__"]
