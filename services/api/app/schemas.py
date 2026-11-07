from __future__ import annotations

import json
import re
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from hero1_storage.enums import ApprovalState, EventCategory, EventSeverity, NodeOrigin, NodeStatus
from hero1_strategy_spec import SUPPORTED_TIMEFRAMES

_SPEC_HASH_RE = re.compile(r"^[a-f0-9]{64}$")


def _validate_spec_json(v: Optional[str]) -> Optional[str]:
    if v is None:
        return v
    try:
        json.loads(v)
    except json.JSONDecodeError as exc:
        raise ValueError(f"spec_json is not valid JSON: {exc}") from exc
    return v


class InstrumentOut(BaseModel):
    symbol: str
    display_name: str
    asset_class: str
    enabled: bool
    default_timeframe: str
    digits: Optional[int] = None
    contract_size: Optional[float] = None


class InstrumentListOut(BaseModel):
    instruments: List[InstrumentOut]


class NodeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    origin: NodeOrigin
    description: Optional[str] = Field(default=None, max_length=4000)
    spec_json: Optional[str] = None
    spec_hash: Optional[str] = Field(
        default=None,
        description="DEPRECATED compat field: format-checked only. The server ALWAYS "
                    "recomputes the canonical fingerprint; a client-supplied hash is never persisted.")
    parent_node_id: Optional[int] = None
    created_by: str = Field(default="user", max_length=60)
    broker_meta: Optional[dict] = Field(
        default=None,
        description="Optional per-instrument broker metadata used ONLY to validate the spec "
                    "(e.g. {'XAUUSD': {'pip_size': 0.1}}). Never persisted; never trusted blindly.")

    _check_spec = field_validator("spec_json")(_validate_spec_json)

    @field_validator("spec_hash")
    @classmethod
    def _check_hash(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _SPEC_HASH_RE.match(v):
            raise ValueError("spec_hash must be a 64-char lowercase hex string (sha256)")
        return v


class NodePatch(BaseModel):
    """Metadata-only edits. Strategy semantics (spec_json) are IMMUTABLE for a
    saved node — extra keys are rejected with 422. To change a strategy, call
    POST /api/v1/instruments/{instrument}/nodes/{node_id}/versions instead."""

    model_config = {"extra": "forbid"}

    name: Optional[str] = Field(default=None, min_length=1, max_length=160)
    description: Optional[str] = Field(default=None, max_length=4000)
    status: Optional[NodeStatus] = None
    approval_state: Optional[ApprovalState] = None
    # Favorite toggle — research metadata only. Cannot alter spec_json, spec_hash,
    # version or lineage, so it cannot change strategy semantics or validation.
    starred: Optional[bool] = None


class NodeOut(BaseModel):
    id: int
    instrument_id: str
    origin: str
    name: str
    description: Optional[str]
    status: str
    approval_state: str
    version: int
    parent_node_id: Optional[int]
    spec_hash: Optional[str]
    spec_json: Optional[str]
    created_by: str
    created_at: float
    updated_at: float
    starred: bool = False
    # Node identity (assigned from the spec at creation). NULL = draft / legacy-unparseable.
    timeframe: Optional[str] = None

    model_config = {"from_attributes": True}


class NodeListOut(BaseModel):
    instrument: str
    total: int                    # UNFILTERED instrument total (stable contract)
    starred_total: int = 0        # nodes currently starred (unfiltered)
    filtered_total: int = 0       # rows matching the ACTIVE filter (== total when none)
    items: List[NodeOut]
    # Per-timeframe node counts (within the ACTIVE starred scope, ignoring the timeframe filter).
    timeframe_counts: dict = Field(default_factory=dict)
    unassigned_timeframe_total: int = 0


class EventCreate(BaseModel):
    category: EventCategory
    severity: EventSeverity = EventSeverity.INFO
    instrument: Optional[str] = None
    subsystem: str = Field(default="api", max_length=48)
    message: str = Field(min_length=1, max_length=500)
    payload: Optional[dict] = None
    node_id: Optional[int] = None


class EventOut(BaseModel):
    id: int
    ts: float
    category: str
    severity: str
    instrument: Optional[str]
    subsystem: str
    message: str
    payload: Optional[Any]
    node_id: Optional[int]


class EventListOut(BaseModel):
    limit: int
    count: int
    total: int = 0
    events: List[EventOut]


_KNOWN_SETTINGS = {
    "log_level": lambda v: str(v).upper() in {"DEBUG", "INFO", "WARNING", "ERROR"},
    "research_max_sources": lambda v: isinstance(v, int) and 1 <= v <= 500,
    "research_max_minutes": lambda v: isinstance(v, int) and 1 <= v <= 600,
    "research_max_tokens": lambda v: isinstance(v, int) and 1_000 <= v <= 2_000_000,
    "research_require_evidence": lambda v: isinstance(v, bool),
}
_DEFAULT_SETTINGS = {
    "log_level": "INFO",
    "research_max_sources": 40,
    "research_max_minutes": 30,
    "research_max_tokens": 200_000,
    "research_require_evidence": True,
}


class SettingsPut(BaseModel):
    settings: dict

    @field_validator("settings")
    @classmethod
    def _validate(cls, values: dict) -> dict:
        unknown = sorted(set(values) - set(_KNOWN_SETTINGS))
        if unknown:
            raise ValueError(f"unknown setting keys: {unknown}; known: {sorted(_KNOWN_SETTINGS)}")
        for key, value in values.items():
            if not _KNOWN_SETTINGS[key](value):
                raise ValueError(f"invalid value for {key!r}: {value!r}")
        return values


class SettingsOut(BaseModel):
    settings: dict
    environment: dict


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    checks: dict
    # Additive launcher-identity fields. Both are None unless the process was
    # started by START.bat/run_backend.cmd (localhost only; no secrets). Let the
    # launcher prove that the answering process is THIS repository and this launch.
    start_token: str | None = None
    project_root: str | None = None


class ErrorOut(BaseModel):
    error: dict


# --------------------------------------------------------------- Phase 2
class SpecValidationRequest(BaseModel):
    spec: dict
    broker_meta: Optional[dict] = Field(
        default=None,
        description="Per-instrument broker metadata, e.g. {'XAUUSD': {'pip_size': 0.1, 'tick_size': 0.01}}. "
                    "Never invented by the server; missing metadata => NEEDS_METADATA.")


class ValidationIssueOut(BaseModel):
    code: str
    path: str
    message: str
    severity: str
    remediation: Optional[str] = None


class ValidationReportOut(BaseModel):
    instrument: str
    status: str
    issues: List[ValidationIssueOut]
    required_warmup_candles: Optional[int] = None
    schema_version: Optional[str] = None
    summary: dict


class SpecCompileRequest(BaseModel):
    spec: dict
    broker_meta: Optional[dict] = None


class CompileResultOut(BaseModel):
    fingerprint: str
    canonical_json: str
    schema_version: str
    compiler_version: str
    ir_version: str
    ir: dict
    required_warmup_candles: int
    warnings: List[str]


class NewVersionRequest(BaseModel):
    spec: dict
    name: Optional[str] = Field(default=None, min_length=1, max_length=160)
    description: Optional[str] = Field(default=None, max_length=4000)
    created_by: str = Field(default="user", max_length=60)
    broker_meta: Optional[dict] = None


class NodeVersionListOut(BaseModel):
    instrument: str
    lineage_root_id: Optional[int]
    items: List[NodeOut]


class NodeGenerateRequest(BaseModel):
    """Generate ONE NODE PER selected timeframe from a single strategy definition.

    Names: when len(timeframes) == 1 an optional explicit ``name`` is honoured
    (conflict ⇒ 409 NAME_DUPLICATE). When multiple timeframes are selected the
    server allocates the first available sequential C-names, in the order of the
    timeframes list, atomically — never duplicates.
    """
    spec: dict
    timeframes: List[str] = Field(min_length=1, max_length=len(SUPPORTED_TIMEFRAMES))
    name: Optional[str] = Field(default=None, min_length=1, max_length=160)
    description: Optional[str] = Field(default=None, max_length=4000)
    broker_meta: Optional[dict] = None
    created_by: str = Field(default="user", max_length=60)

    @field_validator("timeframes")
    @classmethod
    def _check_timeframes(cls, v: List[str]) -> List[str]:
        seen, out = set(), []
        for tf in v:
            t = str(tf).upper()
            if t not in SUPPORTED_TIMEFRAMES:
                raise ValueError(f"unsupported timeframe {tf!r}; supported: {list(SUPPORTED_TIMEFRAMES)}")
            if t in seen:
                raise ValueError(f"duplicate timeframe {t!r}")
            seen.add(t)
            out.append(t)
        return out


class NodeGenerateOut(BaseModel):
    instrument: str
    requested: List[str]
    count: int
    created: List[NodeOut]
