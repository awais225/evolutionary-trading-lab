"""Per-instrument models. One independent SQLite file per instrument.

Every table carries ``instrument_id`` (the owning symbol) even though the file
itself is instrument-scoped. This is deliberate defense-in-depth: it keeps the
Alembic migrations, seed tooling, and a future PostgreSQL consolidation honest,
and makes the isolation testable.
"""
from __future__ import annotations

from sqlalchemy import (
    Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .enums import ApprovalState, EventCategory, EventSeverity, NodeOrigin, NodeStatus
from .timeutil import utcnow


class Node(Base):
    __tablename__ = "nodes"
    __table_args__ = (
        UniqueConstraint("instrument_id", "spec_hash", "version", name="uq_nodes_spec"),
        Index("ix_nodes_instr_origin", "instrument_id", "origin"),
        Index("ix_nodes_instr_status", "instrument_id", "status"),
        Index("ix_nodes_instr_approval", "instrument_id", "approval_state"),
        Index("ix_nodes_instr_parent", "instrument_id", "parent_node_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    origin: Mapped[str] = mapped_column(String(16), nullable=False, default=NodeOrigin.CUSTOM.value)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=NodeStatus.DRAFT.value)
    approval_state: Mapped[str] = mapped_column(String(12), nullable=False, default=ApprovalState.NONE.value)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    parent_node_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("nodes.id", ondelete="SET NULL"), nullable=True
    )
    spec_json: Mapped[str | None] = mapped_column(Text, nullable=True)   # validated spec; NULL until Phase 2 compiler lands
    spec_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[str] = mapped_column(String(60), nullable=False, default="user")
    created_at: Mapped[float] = mapped_column(Float, nullable=False, default=utcnow)
    updated_at: Mapped[float] = mapped_column(Float, nullable=False, default=utcnow, onupdate=utcnow)


class NodeLineage(Base):
    __tablename__ = "node_lineage"
    __table_args__ = (Index("ix_lineage_child", "instrument_id", "child_node_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    child_node_id: Mapped[int] = mapped_column(Integer, ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False)
    parent_node_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("nodes.id", ondelete="SET NULL"), nullable=True
    )
    relation: Mapped[str] = mapped_column(String(40), nullable=False, default="derived_from")  # derived_from | fine_tuned_from | imported
    created_at: Mapped[float] = mapped_column(Float, nullable=False, default=utcnow)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    __table_args__ = (Index("ix_bt_node", "instrument_id", "node_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    node_id: Mapped[int] = mapped_column(Integer, ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False)
    stage: Mapped[str] = mapped_column(String(16), nullable=False, default="detail")  # screen|detail|oos|walkforward|stress|montecarlo
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="QUEUED")  # QUEUED|RUNNING|DONE|FAILED|CANCELLED
    dataset_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    params_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, nullable=False, default=utcnow)
    finished_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class DeepTestScenario(Base):
    __tablename__ = "deeptest_scenarios"
    __table_args__ = (Index("ix_dt_node_status", "instrument_id", "node_id", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    node_id: Mapped[int] = mapped_column(Integer, ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="QUEUED")  # QUEUED|RUNNING|DONE|FAILED|CANCELLED
    config_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, nullable=False, default=utcnow)
    finished_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class DeepTestTrade(Base):
    __tablename__ = "deeptest_trades"
    __table_args__ = (Index("ix_dtt_scenario", "instrument_id", "scenario_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    scenario_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("deeptest_scenarios.id", ondelete="CASCADE"), nullable=False
    )
    node_id: Mapped[int] = mapped_column(Integer, ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False)
    side: Mapped[str] = mapped_column(String(5), nullable=False)  # BUY|SELL
    volume: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    entry_ts: Mapped[float] = mapped_column(Float, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_ts: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)


class ResearchTask(Base):
    __tablename__ = "research_tasks"
    __table_args__ = (Index("ix_rt_instr_status", "instrument_id", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    origin: Mapped[str] = mapped_column(String(16), nullable=False, default=NodeOrigin.WWW.value)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")  # PENDING|RUNNING|PAUSED|DONE|FAILED|STOPPED
    budget_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, nullable=False, default=utcnow)
    finished_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class ResearchSource(Base):
    __tablename__ = "research_sources"
    __table_args__ = (UniqueConstraint("instrument_id", "task_id", "url", name="uq_source_task_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    task_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("research_tasks.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")  # PENDING|DONE|FAILED|SKIPPED
    fetched_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class ResearchFinding(Base):
    __tablename__ = "research_findings"
    __table_args__ = (Index("ix_rf_task", "instrument_id", "task_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    task_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("research_tasks.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    evidence_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    usable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[float] = mapped_column(Float, nullable=False, default=utcnow)


class LiveSession(Base):
    __tablename__ = "live_sessions"
    __table_args__ = (Index("ix_ls_instr_status", "instrument_id", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="INACTIVE")  # INACTIVE|ACTIVE|STOPPED|INTERRUPTED
    started_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    stopped_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class LiveConfig(Base):
    __tablename__ = "live_configs"
    __table_args__ = (UniqueConstraint("instrument_id", "node_id", name="uq_live_config_node"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    node_id: Mapped[int] = mapped_column(Integer, ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False)
    risk_pct: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    max_trades: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False, default=utcnow)


class LiveTrade(Base):
    __tablename__ = "live_trades"
    __table_args__ = (Index("ix_lt_session", "instrument_id", "session_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    session_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("live_sessions.id", ondelete="SET NULL"), nullable=True
    )
    node_id: Mapped[int] = mapped_column(Integer, ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False)
    side: Mapped[str] = mapped_column(String(5), nullable=False)
    lots: Mapped[float] = mapped_column(Float, nullable=False, default=0.01)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    sl: Mapped[float | None] = mapped_column(Float, nullable=True)
    tp: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN")  # OPEN|CLOSED|UNKNOWN
    open_ts: Mapped[float] = mapped_column(Float, nullable=False, default=utcnow)
    close_ts: Mapped[float | None] = mapped_column(Float, nullable=True)


class Approval(Base):
    """Live-trading approval ledger. Separate from Node.status on purpose."""

    __tablename__ = "approvals"
    __table_args__ = (Index("ix_appr_node_state", "instrument_id", "node_id", "state"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    node_id: Mapped[int] = mapped_column(Integer, ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False)
    scope: Mapped[str] = mapped_column(String(24), nullable=False, default="LIVE_TRADE")  # LIVE_TRADE | DEMO_FORWARD
    state: Mapped[str] = mapped_column(String(12), nullable=False, default=ApprovalState.PENDING.value)
    decided_by: Mapped[str | None] = mapped_column(String(60), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_at: Mapped[float] = mapped_column(Float, nullable=False, default=utcnow)
    decided_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class ActivityEvent(Base):
    """Instrument-scoped audit trail (nodes, tests, live). Mirrors GlobalEvent shape."""

    __tablename__ = "activity_events"
    __table_args__ = (Index("ix_ae_instr_id", "instrument_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    ts: Mapped[float] = mapped_column(Float, nullable=False, default=utcnow)
    category: Mapped[str] = mapped_column(String(24), nullable=False, default=EventCategory.SYSTEM.value)
    severity: Mapped[str] = mapped_column(String(12), nullable=False, default=EventSeverity.INFO.value)
    subsystem: Mapped[str] = mapped_column(String(48), nullable=False, default="system")
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    node_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("nodes.id", ondelete="SET NULL"), nullable=True
    )
