"""Centralized multi-layer specification validator.

Layers: structural → semantic (expression trees) → instrument → timeframe →
indicator → risk → execution → safety. Every finding is a structured ``Issue``
(code, path, message, severity, remediation). The report exposes an overall
status:

  INVALID          — at least one blocking problem
  NEEDS_METADATA   — acceptable only after required broker metadata exists
  WARNING          — accepted with a known limitation
  VALID            — all checks passed

Nothing is invented: missing metadata yields NEEDS_METADATA, never a guess.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict

from .expressions import (
    MAX_TREE_DEPTH, MAX_TREE_NODES, CandleOperand, Comparison, ComparisonOp,
    FormingCandleOperand, IndicatorOperand, LogicNode,
    ConstantOperand, tree_depth, tree_size, walk,
)
from .indicators import IndicatorName, indicator_warmup, validate_indicator_params
from .risk import DistanceUnit
from .spec import (
    KNOWN_INSTRUMENTS, MAX_METADATA_ENTRIES, SUPPORTED_SCHEMA_VERSIONS,
    SUPPORTED_TIMEFRAMES, StrategySpec,
)


class Severity(str, Enum):
    INVALID = "INVALID"
    WARNING = "WARNING"
    NEEDS_METADATA = "NEEDS_METADATA"


class ReportStatus(str, Enum):
    VALID = "VALID"
    WARNING = "WARNING"
    NEEDS_METADATA = "NEEDS_METADATA"
    INVALID = "INVALID"


class Issue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    path: str
    message: str
    severity: Severity
    remediation: Optional[str] = None


class ValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instrument: str
    status: ReportStatus
    issues: List[Issue]
    required_warmup_candles: Optional[int] = None
    schema_version: Optional[str] = None
    summary: Dict[str, int] = {}

    @property
    def ok(self) -> bool:
        return self.status in (ReportStatus.VALID, ReportStatus.WARNING)


def _err(code: str, path: str, message: str, remediation: Optional[str] = None) -> Issue:
    return Issue(code=code, path=path, message=message, severity=Severity.INVALID, remediation=remediation)


def _warn(code: str, path: str, message: str, remediation: Optional[str] = None) -> Issue:
    return Issue(code=code, path=path, message=message, severity=Severity.WARNING, remediation=remediation)


def _meta(code: str, path: str, message: str, remediation: Optional[str] = None) -> Issue:
    return Issue(code=code, path=path, message=message, severity=Severity.NEEDS_METADATA, remediation=remediation)


def _structural_issues(exc: Exception) -> List[Issue]:
    issues: List[Issue] = []
    errors = getattr(exc, "errors", None)
    if callable(errors):
        for e in exc.errors():  # type: ignore[union-attr]
            loc = ".".join(str(p) for p in e.get("loc", ())) or "$"
            issues.append(_err("E_STRUCTURAL", loc, str(e.get("msg", e))))
    else:
        issues.append(_err("E_STRUCTURAL", "$", str(exc)))
    return issues


_COMPARABLE = (CandleOperand, IndicatorOperand, FormingCandleOperand, ConstantOperand)


def _check_operand(op: Any, path: str, spec: StrategySpec, issues: List[Issue], warmups: List[int]) -> None:
    if isinstance(op, IndicatorOperand):
        for problem in validate_indicator_params(op.name, op.params, op.field):
            code = "E_INDICATOR_UNSUPPORTED" if "unknown parameter" in problem else "E_INDICATOR_PARAMS"
            issues.append(_err(code, path, problem,
                               "Supported indicators: SMA, EMA, RSI, MACD, BBANDS, ATR, VWAP, ROC, STOCH, ADX"))
        warmups.append(indicator_warmup(op.name, op.params) + op.offset + 2)
    elif isinstance(op, CandleOperand):
        if op.offset > 500:
            issues.append(_err("E_CANDLE_OFFSET", path, f"candle offset {op.offset} exceeds 500"))
    elif isinstance(op, FormingCandleOperand):
        if not (spec.execution.candle == "forming" and spec.execution.permit_forming_candle):
            issues.append(_err(
                "E_FORMING_CANDLE_FORBIDDEN", path,
                "forming_candle used without explicit permission "
                "(execution.candle='forming' and execution.permit_forming_candle=true)",
                "Set execution.candle='forming' and execution.permit_forming_candle=true, or use completed candles",
            ))


def _check_comparison(comp: Comparison, path: str, spec: StrategySpec, issues: List[Issue], warmups: List[int]) -> None:
    left, right = comp.left, comp.right
    _check_operand(left, f"{path}.left", spec, issues, warmups)
    _check_operand(right, f"{path}.right", spec, issues, warmups)

    if isinstance(left, ConstantOperand) and isinstance(right, ConstantOperand):
        issues.append(_warn("CONSTANT_COMPARISON", path,
                            "constant-vs-constant comparison is static and carries no market information"))

    if comp.op in (ComparisonOp.CROSS_ABOVE, ComparisonOp.CROSS_BELOW):
        directional = (CandleOperand, FormingCandleOperand, IndicatorOperand)
        if not (isinstance(left, directional) and isinstance(right, directional)):
            issues.append(_err(
                "E_CROSS_OPERAND", path,
                "cross_above / cross_below require both operands to be candles or indicators "
                "(they compare value transitions between the previous and the current completed candle)",
            ))


def _check_tree(node: LogicNode, spec: StrategySpec, issues: List[Issue], warmups: List[int]) -> None:
    depth = tree_depth(node)
    size = tree_size(node)
    if depth > MAX_TREE_DEPTH:
        issues.append(_err("E_SAFETY_DEPTH", "entry", f"expression depth {depth} exceeds {MAX_TREE_DEPTH}"))
    if size > MAX_TREE_NODES:
        issues.append(_err("E_SAFETY_SIZE", "entry", f"expression size {size} exceeds {MAX_TREE_NODES}"))
    for child, child_depth in walk(node):
        if isinstance(child, Comparison):
            _check_comparison(child, "entry", spec, issues, warmups)


def _check_risk(spec: StrategySpec, issues: List[Issue], broker_meta: Optional[Dict[str, dict]]) -> None:
    meta = (broker_meta or {}).get(spec.identity.instrument.upper())

    def distance_issue(path: str, unit: DistanceUnit) -> None:
        need = {DistanceUnit.POINTS: ("point_size", "point"), DistanceUnit.TICKS: ("tick_size", "tick"),
                DistanceUnit.PIPS: ("pip_size", "pip")}.get(unit)
        if need is None:
            return  # absolute and atr_multiple need no broker meta
        key, label = need
        if meta is None or meta.get(key) is None:
            issues.append(_meta(
                "RISK_METADATA_REQUIRED", path,
                f"distance unit '{unit.value}' cannot be resolved without broker metadata ({key}) for "
                f"{spec.identity.instrument}; refusing to guess a {label} size",
                f"Supply broker_meta={{\"{spec.identity.instrument}\": {{{key!s}: …}}}} from the instrument "
                "registry / MT5 symbol info (Phase 5) — never a hardcoded universal value",
            ))

    ev, rv = spec.exits, spec.risk
    if ev.stop_loss is not None:
        distance_issue("exits.stop_loss.distance", ev.stop_loss.distance.unit)
    if ev.take_profit is not None and ev.take_profit.distance is not None:
        distance_issue("exits.take_profit.distance", ev.take_profit.distance.unit)
    for side in ("long", "short"):
        override = getattr(rv, side)
        if override is not None and override.stop_loss is not None:
            distance_issue(f"risk.{side}.stop_loss.distance", override.stop_loss.distance.unit)
        if override is not None and override.take_profit is not None and override.take_profit.distance is not None:
            distance_issue(f"risk.{side}.take_profit.distance", override.take_profit.distance.unit)

    # exit behaviour is mandatory — never invented
    if not any([ev.stop_loss, ev.take_profit, ev.explicit_exit,
                ev.max_holding_candles, ev.time_exit_utc]):
        issues.append(_err("EXIT_REQUIRED", "exits",
                           "strategy declares no exit behaviour (stop_loss / take_profit / explicit_exit / "
                           "max_holding_candles / time_exit_utc). Refusing to invent one.",
                           "Declare at least one exit mechanism explicitly"))

    # sizing sanity beyond pydantic ranges
    sizing = rv.sizing
    if sizing.mode == "fixed_risk" and sizing.risk_pct is not None and sizing.risk_pct > 25:
        issues.append(_warn("RISK_HIGH", "risk.sizing.risk_pct",
                            f"risk_pct={sizing.risk_pct}% per trade is unusually high; accepted but flagged"))


def _check_execution(spec: StrategySpec, issues: List[Issue]) -> None:
    ex = spec.execution
    if ex.candle == "forming" and not ex.permit_forming_candle:
        issues.append(_err("E_FORMING_CANDLE_FORBIDDEN", "execution.candle",
                           "candle='forming' requires permit_forming_candle=true",
                           "Set permit_forming_candle=true explicitly, or use candle='completed'"))
    if ex.candle == "completed" and ex.permit_forming_candle:
        issues.append(_warn("EXEC_FORMING_PERMIT_UNUSED", "execution.permit_forming_candle",
                            "permit_forming_candle=true has no effect with candle='completed'"))
    if ex.entry_timing == "signal_close" and ex.candle == "forming":
        issues.append(_err("E_EXEC_TIMING", "execution.entry_timing",
                           "entry_timing='signal_close' is contradictory with candle='forming'"))


def _check_identity(spec: StrategySpec, expected_instrument: Optional[str], issues: List[Issue]) -> None:
    inst = spec.identity.instrument.upper()
    if expected_instrument is not None and inst != expected_instrument.upper():
        issues.append(_err("INSTRUMENT_MISMATCH", "identity.instrument",
                           f"spec instrument {inst} does not match the owning instrument context "
                           f"{expected_instrument.upper()}; refusing silent cross-instrument mix"))
    if inst not in KNOWN_INSTRUMENTS:
        issues.append(_warn("INSTRUMENT_UNKNOWN", "identity.instrument",
                            f"instrument {inst} is not in the seeded registry set {list(KNOWN_INSTRUMENTS)}"))
    if spec.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        issues.append(_err("SCHEMA_VERSION_UNSUPPORTED", "schema_version",
                           f"schema_version {spec.schema_version!r} is not supported; expected one of {sorted(SUPPORTED_SCHEMA_VERSIONS)}"))
    if spec.identity.timeframe not in SUPPORTED_TIMEFRAMES:
        issues.append(_err("TIMEFRAME_INVALID", "identity.timeframe",
                           f"timeframe {spec.identity.timeframe!r} is not supported; expected one of {list(SUPPORTED_TIMEFRAMES)}"))
    if len(spec.identity.metadata) > MAX_METADATA_ENTRIES:
        issues.append(_err("E_METADATA_TOO_LARGE", "identity.metadata",
                           f"metadata exceeds {MAX_METADATA_ENTRIES} entries"))
    # tags hygiene
    for i, tag in enumerate(spec.identity.tags):
        if not tag.strip() or len(tag) > 40:
            issues.append(_err("E_TAG_INVALID", f"identity.tags[{i}]", "tags must be non-empty and ≤ 40 chars"))


def validate_spec(data: Any, *, expected_instrument: Optional[str] = None,
                  broker_meta: Optional[Dict[str, dict]] = None) -> ValidationReport:
    """Validate a spec (dict or StrategySpec). Never raises for bad input —
    returns a structured report instead."""
    issues: List[Issue] = []
    instrument_hint = expected_instrument or (
        data.get("identity", {}).get("instrument") if isinstance(data, dict) else None) or "UNKNOWN"

    # Layer 1 — structural
    try:
        spec = data if isinstance(data, StrategySpec) else StrategySpec.model_validate(data)
    except Exception as exc:  # pydantic ValidationError (structural layer)
        issues.extend(_structural_issues(exc))
        return ValidationReport(
            instrument=str(instrument_hint).upper(), status=ReportStatus.INVALID, issues=issues,
            summary={"invalid": len(issues), "warning": 0, "needs_metadata": 0})

    inst = spec.identity.instrument.upper()
    broker_meta_norm = {k.upper(): v for k, v in (broker_meta or {}).items()} or None

    # Layers 3,4 (identity/instrument/timeframe/schema)
    _check_identity(spec, expected_instrument, issues)
    # Layers 2,5,8 (expression tree / indicators / safety)
    warmups: List[int] = []
    _check_tree(spec.entry, spec, issues, warmups)
    # Layer 6 — risk (incl. metadata / exit presence)
    _check_risk(spec, issues, broker_meta_norm)
    # Layer 7 — execution
    _check_execution(spec, issues)

    warmup = max(warmups) if warmups else 1
    counts = {
        "invalid": sum(1 for i in issues if i.severity is Severity.INVALID),
        "warning": sum(1 for i in issues if i.severity is Severity.WARNING),
        "needs_metadata": sum(1 for i in issues if i.severity is Severity.NEEDS_METADATA),
    }
    if counts["invalid"]:
        status = ReportStatus.INVALID
    elif counts["needs_metadata"]:
        status = ReportStatus.NEEDS_METADATA
    elif counts["warning"]:
        status = ReportStatus.WARNING
    else:
        status = ReportStatus.VALID

    return ValidationReport(instrument=inst, status=status, issues=issues,
                            required_warmup_candles=warmup,
                            schema_version=spec.schema_version, summary=counts)
