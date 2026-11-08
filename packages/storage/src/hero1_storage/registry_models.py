"""Global registry models: instruments, application settings, global events."""
from __future__ import annotations

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .enums import EventCategory, EventSeverity
from .timeutil import utcnow


class Instrument(Base):
    __tablename__ = "instruments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    asset_class: Mapped[str] = mapped_column(String(32), nullable=False)  # metal | fx | crypto
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    default_timeframe: Mapped[str] = mapped_column(String(4), nullable=False, default="M15")
    digits: Mapped[int | None] = mapped_column(Integer, nullable=True)          # filled from broker snapshot later
    contract_size: Mapped[float | None] = mapped_column(Float, nullable=True)   # never assumed; from broker specs
    created_at: Mapped[float] = mapped_column(Float, nullable=False, default=utcnow)
    updated_at: Mapped[float] = mapped_column(Float, nullable=False, default=utcnow, onupdate=utcnow)


class ApplicationSetting(Base):
    __tablename__ = "application_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)  # JSON-encoded value
    updated_at: Mapped[float] = mapped_column(Float, nullable=False, default=utcnow, onupdate=utcnow)
    updated_by: Mapped[str] = mapped_column(String(60), nullable=False, default="user")


class GlobalEvent(Base):
    """Durable, append-only event log backing the global LIVE FEED.

    Instrument events carry instrument_id (FK instruments.symbol); system-wide
    events leave it NULL. Replay is simply ``WHERE id > :since ORDER BY id``.
    """

    __tablename__ = "global_events"
    __table_args__ = (
        Index("ix_global_events_instrument_id", "instrument_id", "id"),
        Index("ix_global_events_category_id", "category", "id"),
        Index("ix_global_events_severity_id", "severity", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[float] = mapped_column(Float, nullable=False, default=utcnow)
    category: Mapped[str] = mapped_column(String(24), nullable=False, default=EventCategory.SYSTEM.value)
    severity: Mapped[str] = mapped_column(String(12), nullable=False, default=EventSeverity.INFO.value)
    instrument_id: Mapped[str | None] = mapped_column(
        String(16), ForeignKey("instruments.symbol", ondelete="SET NULL"), nullable=True
    )
    subsystem: Mapped[str] = mapped_column(String(48), nullable=False, default="system")
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    node_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class DashboardTask(Base):
    """One authoritative task-progress record shared by the global progress
    bars, the Live Feed infographics, the DeepTesting node-by-node progress
    and completion acknowledgement (HERO1 task-progress architecture).

    A task is visible while it is active, and afterwards until a user opens
    the owning tab and the completion is acknowledged. Acknowledgement is
    recorded here (server-side) so a page refresh can neither resurrect an
    acknowledged bar nor lose a live one."""

    __tablename__ = "dashboard_tasks"
    __table_args__ = (Index("ix_dt_category_status", "category", "status"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    category: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    # DEEPTESTING | DATA_FETCHING | NODE_GENERATION | VALIDATION
    instrument_id: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    label: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="QUEUED")
    # QUEUED | RUNNING | COMPLETED | FAILED | CANCELLED | BLOCKED
    total_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_unit: Mapped[str | None] = mapped_column(String(160), nullable=True)
    current_unit_progress: Mapped[float | None] = mapped_column(Float, nullable=True)
    summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, nullable=False, default=utcnow)
    started_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[float] = mapped_column(Float, nullable=False, default=utcnow)
    finished_at: Mapped[float | None] = mapped_column(Float, nullable=True)
