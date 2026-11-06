"""Repositories. Instrument-scoped repositories CANNOT be built without a
validated instrument context, and every query is filtered by instrument_id."""
from __future__ import annotations

import json
from typing import Dict, Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import registry_models as rm
from . import instrument_models as im
from .enums import NodeOrigin, NodeStatus
from .timeutil import utcnow


# ---------------------------------------------------------------- registry
class InstrumentRepository:
    def __init__(self, session: Session):
        self.s = session

    def list(self, enabled_only: bool = False) -> List[rm.Instrument]:
        stmt = select(rm.Instrument).order_by(rm.Instrument.symbol)
        if enabled_only:
            stmt = stmt.where(rm.Instrument.enabled.is_(True))
        return list(self.s.execute(stmt).scalars())

    def get(self, symbol: str) -> Optional[rm.Instrument]:
        return self.s.execute(
            select(rm.Instrument).where(rm.Instrument.symbol == symbol.upper())
        ).scalar_one_or_none()

    def require(self, symbol: str) -> rm.Instrument:
        inst = self.get(symbol)
        if inst is None or not inst.enabled:
            raise KeyError(symbol)
        return inst


class SettingsRepository:
    def __init__(self, session: Session):
        self.s = session

    def get_all(self) -> dict:
        rows = self.s.execute(select(rm.ApplicationSetting)).scalars().all()
        out = {}
        for r in rows:
            try:
                out[r.key] = json.loads(r.value)
            except json.JSONDecodeError:
                out[r.key] = r.value
        return out

    def get(self, key: str, default=None):
        row = self.s.get(rm.ApplicationSetting, key)
        if row is None:
            return default
        try:
            return json.loads(row.value)
        except json.JSONDecodeError:
            return row.value

    def set(self, key: str, value, updated_by: str = "user") -> None:
        payload = json.dumps(value, separators=(",", ":"))
        row = self.s.get(rm.ApplicationSetting, key)
        if row is None:
            self.s.add(rm.ApplicationSetting(key=key, value=payload, updated_by=updated_by))
        else:
            row.value = payload
            row.updated_by = updated_by
            row.updated_at = utcnow()
        self.s.commit()

    def set_many(self, values: dict, updated_by: str = "user") -> None:
        for key, value in values.items():
            payload = json.dumps(value, separators=(",", ":"))
            row = self.s.get(rm.ApplicationSetting, key)
            if row is None:
                self.s.add(rm.ApplicationSetting(key=key, value=payload, updated_by=updated_by))
            else:
                row.value = payload
                row.updated_by = updated_by
                row.updated_at = utcnow()
        self.s.commit()


class EventRepository:
    """Registry-side durable event store. Replay = id > since_id."""

    def __init__(self, session: Session):
        self.s = session

    def append(self, *, ts: float, category: str, severity: str, instrument_id: Optional[str],
               subsystem: str, message: str, payload: Optional[str], node_id: Optional[int]) -> rm.GlobalEvent:
        ev = rm.GlobalEvent(
            ts=ts, category=category, severity=severity,
            instrument_id=instrument_id, subsystem=subsystem,
            message=message[:500], payload=payload, node_id=node_id,
        )
        self.s.add(ev)
        self.s.commit()
        self.s.refresh(ev)
        return ev

    def list(self, *, instrument: Optional[str] = None, category: Optional[str] = None,
             severity: Optional[str] = None, subsystem: Optional[str] = None,
             since_id: Optional[int] = None, limit: int = 100) -> List[rm.GlobalEvent]:
        stmt = select(rm.GlobalEvent).order_by(rm.GlobalEvent.id)
        if instrument:
            stmt = stmt.where(rm.GlobalEvent.instrument_id == instrument.upper())
        if category:
            stmt = stmt.where(rm.GlobalEvent.category == category.upper())
        if severity:
            stmt = stmt.where(rm.GlobalEvent.severity == severity.upper())
        if subsystem:
            stmt = stmt.where(rm.GlobalEvent.subsystem == subsystem)
        if since_id is not None:
            stmt = stmt.where(rm.GlobalEvent.id > int(since_id))
        stmt = stmt.limit(max(1, min(int(limit), 500)))
        return list(self.s.execute(stmt).scalars())


# ---------------------------------------------------------- instrument
class NodeRepository:
    """Hard-scoped to one instrument. Full stop."""

    def __init__(self, session: Session, instrument: str):
        self.s = session
        self.instrument = instrument.upper()

    def _scoped(self, stmt):
        return stmt.where(im.Node.instrument_id == self.instrument)

    def create(self, *, origin: str, name: str, description: Optional[str] = None,
               spec_json: Optional[str] = None, spec_hash: Optional[str] = None,
               parent_node_id: Optional[int] = None, created_by: str = "user",
               version: int = 1, timeframe: Optional[str] = None) -> im.Node:
        if origin not in {o.value for o in NodeOrigin}:
            raise ValueError(f"invalid origin: {origin!r}")
        node = im.Node(
            instrument_id=self.instrument, origin=origin, name=name.strip(),
            description=description, spec_json=spec_json, spec_hash=spec_hash,
            parent_node_id=parent_node_id, created_by=created_by, version=version,
            timeframe=timeframe,
        )
        self.s.add(node)
        self.s.flush()
        if parent_node_id is not None:
            self.s.add(im.NodeLineage(
                instrument_id=self.instrument, child_node_id=node.id,
                parent_node_id=parent_node_id, relation="derived_from",
            ))
        self.s.commit()
        self.s.refresh(node)
        return node

    def create_many(self, items: List[dict]) -> List[im.Node]:
        """Atomic bulk create (multi-timeframe generation). ALL rows or NONE.

        Every item carries the same caller-resolved fields as repo.create plus an
        already-allocated unique ``name`` and ``timeframe``. Callers own name
        allocation; the uq_nodes_instr_name index remains the race guard.
        """
        nodes = []
        for it in items:
            if it["origin"] not in {o.value for o in NodeOrigin}:
                raise ValueError(f"invalid origin: {it['origin']!r}")
            nodes.append(im.Node(
                instrument_id=self.instrument, origin=it["origin"], name=it["name"].strip(),
                description=it.get("description"), spec_json=it.get("spec_json"),
                spec_hash=it.get("spec_hash"), parent_node_id=it.get("parent_node_id"),
                created_by=it.get("created_by", "user"), version=it.get("version", 1),
                timeframe=it.get("timeframe"),
            ))
        self.s.add_all(nodes)
        self.s.flush()          # row ids assigned; unique indexes enforced here
        self.s.commit()
        for n in nodes:
            self.s.refresh(n)
        return nodes

    def name_exists(self, name: str, *, exclude_id: Optional[int] = None) -> bool:
        stmt = select(im.Node.id).where(
            im.Node.instrument_id == self.instrument, im.Node.name == name.strip())
        if exclude_id is not None:
            stmt = stmt.where(im.Node.id != exclude_id)
        return self.s.execute(stmt).first() is not None

    def existing_names(self) -> List[str]:
        return list(self.s.execute(
            select(im.Node.name).where(im.Node.instrument_id == self.instrument)).scalars())

    def get(self, node_id: int) -> Optional[im.Node]:
        return self.s.execute(
            self._scoped(select(im.Node)).where(im.Node.id == node_id)
        ).scalar_one_or_none()

    def list(self, *, origin: Optional[str] = None, status: Optional[str] = None,
             starred: Optional[bool] = None, timeframes: Optional[List[str]] = None,
             limit: int = 200, offset: int = 0) -> List[im.Node]:
        stmt = self._scoped(select(im.Node)).order_by(im.Node.id.desc())
        if origin:
            stmt = stmt.where(im.Node.origin == origin.upper())
        if status:
            stmt = stmt.where(im.Node.status == status.upper())
        if starred is not None:
            stmt = stmt.where(im.Node.starred.is_(bool(starred)))
        if timeframes is not None:
            wanted = [t.upper() for t in timeframes]
            stmt = stmt.where(im.Node.timeframe.in_(wanted))
        stmt = stmt.offset(max(0, offset)).limit(max(1, min(limit, 500)))
        return list(self.s.execute(stmt).scalars())

    def count(self, *, starred: Optional[bool] = None,
              timeframes: Optional[List[str]] = None) -> int:
        from sqlalchemy import func
        stmt = select(func.count()).select_from(im.Node).where(
            im.Node.instrument_id == self.instrument)
        if starred is not None:
            stmt = stmt.where(im.Node.starred.is_(bool(starred)))
        if timeframes is not None:
            wanted = [t.upper() for t in timeframes]
            stmt = stmt.where(im.Node.timeframe.in_(wanted))
        return int(self.s.execute(stmt).scalar_one())

    def timeframe_counts(self, *, starred: Optional[bool] = None) -> Dict[str, int]:
        from sqlalchemy import func
        stmt = select(im.Node.timeframe, func.count()).where(
            im.Node.instrument_id == self.instrument, im.Node.timeframe.isnot(None))
        if starred is not None:
            stmt = stmt.where(im.Node.starred.is_(bool(starred)))
        return {str(tf): int(c) for tf, c in self.s.execute(stmt.group_by(im.Node.timeframe)).all()}

    def count_no_timeframe(self, *, starred: Optional[bool] = None) -> int:
        from sqlalchemy import func
        stmt = select(func.count()).select_from(im.Node).where(
            im.Node.instrument_id == self.instrument, im.Node.timeframe.is_(None))
        if starred is not None:
            stmt = stmt.where(im.Node.starred.is_(bool(starred)))
        return int(self.s.execute(stmt).scalar_one())

    def update(self, node_id: int, **fields) -> Optional[im.Node]:
        node = self.get(node_id)
        if node is None:
            return None
        allowed = {"name", "description", "status", "approval_state", "spec_json", "spec_hash", "version", "starred"}
        for key, value in fields.items():
            if key in allowed and value is not None:
                setattr(node, key, value)
        node.updated_at = utcnow()
        self.s.commit()
        self.s.refresh(node)
        return node


class ActivityEventRepository:
    """Instrument-side audit log."""

    def __init__(self, session: Session, instrument: str):
        self.s = session
        self.instrument = instrument.upper()

    def append(self, *, category: str, severity: str, subsystem: str,
               message: str, payload: Optional[str] = None, node_id: Optional[int] = None) -> im.ActivityEvent:
        ev = im.ActivityEvent(
            instrument_id=self.instrument, ts=utcnow(), category=category,
            severity=severity, subsystem=subsystem, message=message[:500],
            payload=payload, node_id=node_id,
        )
        self.s.add(ev)
        self.s.commit()
        self.s.refresh(ev)
        return ev


def events_to_dicts(rows: Iterable[rm.GlobalEvent]) -> List[dict]:
    out = []
    for r in rows:
        try:
            payload = json.loads(r.payload) if r.payload else None
        except json.JSONDecodeError:
            payload = None
        out.append({
            "id": r.id, "ts": r.ts, "category": r.category, "severity": r.severity,
            "instrument": r.instrument_id, "subsystem": r.subsystem,
            "message": r.message, "payload": payload, "node_id": r.node_id,
        })
    return out
