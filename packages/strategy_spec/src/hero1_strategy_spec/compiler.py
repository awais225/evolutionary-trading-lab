"""Deterministic compiler: StrategySpec -> canonical IR + SHA-256 fingerprint.

Guarantees:
  * identical specs produce identical fingerprints (key-order independent),
  * different meaningful specs produce different fingerprints,
  * output records schema_version and compiler_version,
  * nothing from the spec is ever executed, imported or eval'd.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict

from .spec import SCHEMA_VERSION, StrategySpec
from .validation import ReportStatus, ValidationReport, validate_spec

COMPILER_VERSION = "1.0.0"
IR_VERSION = "1.0.0"


class SpecCompilationError(Exception):
    def __init__(self, report: ValidationReport):
        self.report = report
        status = report.status.value
        blocking = [i for i in report.issues
                    if i.severity.value in ("INVALID", "NEEDS_METADATA")]
        super().__init__(
            f"spec cannot be compiled (status={status}): " +
            "; ".join(f"{i.code}@{i.path}: {i.message}" for i in blocking[:5]))


class CompileResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fingerprint: str
    canonical_json: str
    schema_version: str
    compiler_version: str
    ir_version: str
    ir: Dict[str, Any]
    required_warmup_candles: int
    warnings: list[str]


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False)


def compile_spec(data: Any, *, expected_instrument: Optional[str] = None,
                 broker_meta: Optional[Dict[str, dict]] = None,
                 report: Optional[ValidationReport] = None) -> CompileResult:
    """Compile a spec dict (or StrategySpec) into a normalized IR.

    Refuses to compile INVALID or NEEDS_METADATA specs — broker metadata must
    be supplied by the caller; nothing is guessed.
    """
    if report is None:
        report = validate_spec(data, expected_instrument=expected_instrument,
                               broker_meta=broker_meta)
    if report.status in (ReportStatus.INVALID, ReportStatus.NEEDS_METADATA):
        raise SpecCompilationError(report)

    spec = data if isinstance(data, StrategySpec) else StrategySpec.model_validate(data)
    canonical_source = spec.model_dump(mode="json")
    canon = canonical_json(canonical_source)
    fingerprint = hashlib.sha256(canon.encode("utf-8")).hexdigest()

    warmup = report.required_warmup_candles if report.required_warmup_candles is not None else 1
    warnings = [f"{i.code}@{i.path}: {i.message}" for i in report.issues if i.severity.value == "WARNING"]

    ir = {
        "ir_version": IR_VERSION,
        "schema_version": spec.schema_version,
        "compiler_version": COMPILER_VERSION,
        "fingerprint": fingerprint,
        "required_warmup_candles": warmup,
        "strategy": {
            "name": spec.identity.name,
            "instrument": spec.identity.instrument.upper(),
            "timeframe": spec.identity.timeframe,
            "direction": spec.identity.direction.value,
            "origin": spec.identity.origin.value,
            "tags": list(spec.identity.tags),
        },
        "entry": spec.entry.model_dump(mode="json"),
        "exits": spec.exits.model_dump(mode="json", exclude_none=True),
        "risk": spec.risk.model_dump(mode="json", exclude_none=True),
        "execution": spec.execution.model_dump(mode="json"),
    }

    return CompileResult(
        fingerprint=fingerprint,
        canonical_json=canon,
        schema_version=spec.schema_version,
        compiler_version=COMPILER_VERSION,
        ir_version=IR_VERSION,
        ir=ir,
        required_warmup_candles=warmup,
        warnings=warnings,
    )
