"""Shared enumerations. All stored as plain strings (portable, Alembic-friendly)."""
from __future__ import annotations

import enum


class StrEnum(str, enum.Enum):
    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.value


class NodeOrigin(StrEnum):
    CUSTOM = "CUSTOM"
    WWW = "WWW"
    INTERACTIVE = "INTERACTIVE"
    FINE_TUNED = "FINE_TUNED"


class NodeStatus(StrEnum):
    """Working lifecycle. Approval for live trading is a SEPARATE concern (ApprovalState)."""
    DRAFT = "DRAFT"
    VALIDATING = "VALIDATING"
    QUALIFIED = "QUALIFIED"
    REJECTED = "REJECTED"
    RETIRED = "RETIRED"


class ApprovalState(StrEnum):
    """Live-trading approval. Independent of NodeStatus on purpose:
    a QUALIFIED node is eligible for testing; only an APPROVED approval record
    plus a sufficiently tested version may activate live execution."""
    NONE = "NONE"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class EventCategory(StrEnum):
    SYSTEM = "SYSTEM"
    RESEARCH = "RESEARCH"
    NODE = "NODE"
    BACKTEST = "BACKTEST"
    DEEPTEST = "DEEPTEST"
    FINE_TUNING = "FINE_TUNING"
    MT5 = "MT5"
    LIVE_TRADING = "LIVE_TRADING"
    ERROR = "ERROR"
    WARNING = "WARNING"


class EventSeverity(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    ERROR = "ERROR"
