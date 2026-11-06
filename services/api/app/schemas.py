from __future__ import annotations

import json
import re
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from hero1_storage.enums import ApprovalState, EventCategory, EventSeverity, NodeOrigin, NodeStatus

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
    spec_hash: Optional[str] = None
    parent_node_id: Optional[int] = None
    created_by: str = Field(default="user", max_length=60)

    _check_spec = field_validator("spec_json")(_validate_spec_json)

    @field_validator("spec_hash")
    @classmethod
    def _check_hash(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _SPEC_HASH_RE.match(v):
            raise ValueError("spec_hash must be a 64-char lowercase hex string (sha256)")
        return v


class NodePatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=160)
    description: Optional[str] = Field(default=None, max_length=4000)
    status: Optional[NodeStatus] = None
    approval_state: Optional[ApprovalState] = None
    spec_json: Optional[str] = None

    _check_spec = field_validator("spec_json")(_validate_spec_json)


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
    created_by: str
    created_at: float
    updated_at: float

    model_config = {"from_attributes": True}


class NodeListOut(BaseModel):
    instrument: str
    total: int
    items: List[NodeOut]


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


class ErrorOut(BaseModel):
    error: dict
