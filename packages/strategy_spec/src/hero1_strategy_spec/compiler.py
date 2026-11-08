"""Deterministic compiler: StrategySpec -> canonical IR + SHA-256 fingerprint.

Guarantees:
  * identical specs produce identical fingerprints (key-order independent),
  * different meaningful specs produce different fingerprints,
  * fingerprint EXCLUDES identity.name/identity.description (presentation) but
    INCLUDES identity.timeframe and identity.instrument (semantics) — changing a
    timeframe forks the strategy; renaming it does not,
  * output records schema_version and compiler_version,
  * nothing from the spec is ever executed, imported or eval'd.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional, Tuple

from pydantic import BaseModel, ConfigDict

from .spec import SCHEMA_VERSION, StrategySpec
from .validation import ReportStatus, ValidationReport, validate_spec

COMPILER_VERSION = "1.2.0"
IR_VERSION = "1.1.0"


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


# Identity fields that are PRESENTATION, not semantics. They are stored in the
# spec (canonical_json) and shown in the UI but are excluded from the semantic
# fingerprint — renaming a node must never fork a "different strategy".
# Timeframe and instrument ARE part of identity and ARE fingerprinted.
PRESENTATION_IDENTITY_FIELDS = ("name", "description")


def semantic_canonical(data: Any) -> str:
    """Canonical JSON source for the fingerprint: the full spec minus display fields.

    Backward-compatibility rule (schema 1.1.0): sections introduced after
    1.0.0 are stripped while semantically ABSENT - `management: null` and
    `exits.exit_rules: []` - so a pre-existing spec keeps the exact
    fingerprint it had under compiler 1.1.0. The moment either section
    carries content, it is fingerprinted like everything else."""
    payload = data if isinstance(data, dict) else data.model_dump(mode="json")
    if payload.get("management") is None:
        payload = {k: v for k, v in payload.items() if k != "management"}
    exits = payload.get("exits")
    if isinstance(exits, dict) and not exits.get("exit_rules"):
        exits = {k: v for k, v in exits.items() if k != "exit_rules"}
        payload = {**payload, "exits": exits}
    ident = payload.get("identity")
    if isinstance(ident, dict):
        ident = {k: v for k, v in ident.items() if k not in PRESENTATION_IDENTITY_FIELDS}
        payload = {**payload, "identity": ident}
    return canonical_json(payload)


def semantic_fingerprint(spec: "StrategySpec") -> Tuple[str, str]:
    """(fingerprint, semantic canonical JSON) for an already-validated spec."""
    canon = semantic_canonical(spec.model_dump(mode="json"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest(), canon


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
    # Storage keeps the FULL canonical spec (incl. identity.name — it is needed to
    # round-trip the editor). The FINGERPRINT excludes presentation fields.
    canon = canonical_json(spec.model_dump(mode="json"))
    fingerprint, semantic_canon = semantic_fingerprint(spec)

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
        "management": (spec.management.model_dump(mode="json", exclude_none=True)
                       if spec.management is not None else None),
        # Ordered evaluation contract for explicit exits: the legacy single
        # explicit_exit is an implicit priority-0 rule; numbered rules follow
        # in strictly ascending priority. SL/TP and time-based exits are
        # price/time events evaluated by the engines before rule conditions
        # on the same bar (see exec semantics); nothing is reordered here.
        "ordered_exit_rules": (
            ([{"name": "explicit_exit", "priority": 0, "action": "close_position",
               "condition": spec.exits.explicit_exit.model_dump(mode="json")}]
             if spec.exits.explicit_exit is not None else [])
            + [{"name": r.name, "priority": r.priority, "action": r.action,
                "condition": r.condition.model_dump(mode="json")}
               for r in sorted(spec.exits.exit_rules, key=lambda r: r.priority)]),
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
