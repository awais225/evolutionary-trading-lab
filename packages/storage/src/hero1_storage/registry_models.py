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
