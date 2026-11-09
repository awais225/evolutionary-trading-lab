"""Shared, authoritative dashboard task progress (Section 13).

One record per task in the registry DB (`dashboard_tasks`) drives the
global progress bars, the Live Feed infographics, the DeepTesting
node-by-node progress list and completion acknowledgement. Nothing here
invents progress: units are completed only when the owning subsystem
reports the work done, percentages derive from completed/total units,
and a failed/blocked task keeps its real status and reason.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Optional

from sqlalchemy.orm import Session

from hero1_storage.registry_models import DashboardTask

CATEGORIES = ("DEEPTESTING", "DATA_FETCHING", "NODE_GENERATION", "VALIDATION")
TERMINAL = ("COMPLETED", "FAILED", "CANCELLED", "BLOCKED")
ACTIVE = ("QUEUED", "RUNNING", "PAUSED")


def _now() -> float:
    return time.time()


def task_dict(t: DashboardTask) -> dict:
    total = int(t.total_units or 0)
    completed = int(t.completed_units or 0)
    pct = round(100.0 * completed / total, 1) if total > 0 else (
        100.0 if t.status == "COMPLETED" else 0.0)
    return {
        "id": t.id, "category": t.category, "instrument": t.instrument_id,
        "label": t.label, "status": t.status,
        "total_units": total, "completed_units": completed,
        "current_unit": t.current_unit,
        "current_unit_progress": t.current_unit_progress,
        "percentage": pct, "summary": t.summary, "error": t.error,
        "acknowledged": bool(t.acknowledged),
        "payload": json.loads(t.payload_json) if t.payload_json else None,
        "created_at": t.created_at, "started_at": t.started_at,
        "updated_at": t.updated_at, "finished_at": t.finished_at,
    }


def create_task(session: Session, *, category: str, instrument: Optional[str],
                label: str, total_units: int, payload: Optional[dict] = None,
                status: str = "RUNNING") -> DashboardTask:
    if category not in CATEGORIES:
        raise ValueError(f"unknown task category {category!r}")
    t = DashboardTask(id=uuid.uuid4().hex, category=category,
                      instrument_id=(instrument or None), label=label[:160],
                      status=status, total_units=int(total_units),
                      completed_units=0,
                      payload_json=json.dumps(payload) if payload is not None else None,
                      created_at=_now(), started_at=_now(), updated_at=_now())
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


def update_task(session: Session, task_id: str, *, commit: bool = True,
                **fields: Any) -> Optional[DashboardTask]:
    t = session.get(DashboardTask, task_id)
    if t is None:
        return None
    # Progress never moves backwards (retries create a new task instead).
    if "completed_units" in fields and fields["completed_units"] is not None:
        fields["completed_units"] = max(int(t.completed_units or 0),
                                        int(fields["completed_units"]))
    if "payload" in fields:
        fields["payload_json"] = json.dumps(fields.pop("payload"))
    for key, value in fields.items():
        if hasattr(t, key):
            setattr(t, key, value)
    t.updated_at = _now()
    if commit:
        session.commit()
        session.refresh(t)
    return t


def finish_task(session: Session, task_id: str, status: str, *,
                error: Optional[str] = None, summary: Optional[str] = None,
                completed_units: Optional[int] = None) -> Optional[DashboardTask]:
    if status not in TERMINAL:
        raise ValueError(f"finish status must be terminal, got {status!r}")
    t = session.get(DashboardTask, task_id)
    if t is None:
        return None
    if t.status in TERMINAL:
        return t  # duplicate completion events are ignored, never doubled
    t.status = status
    t.error = error
    if summary is not None:
        t.summary = summary[:500]
    if status == "COMPLETED":
        t.completed_units = int(t.total_units or 0)
    elif completed_units is not None:
        t.completed_units = max(int(t.completed_units or 0), int(completed_units))
    t.current_unit_progress = None
    t.finished_at = _now()
    t.updated_at = _now()
    session.commit()
    session.refresh(t)
    return t


def list_visible(session: Session) -> dict:
    """Active tasks plus terminal tasks not yet acknowledged, and the
    latest task per category (acknowledged or not) for the infographics."""
    rows = (session.query(DashboardTask)
            .order_by(DashboardTask.created_at.desc()).limit(200).all())
    visible = [t for t in rows
               if t.status in ACTIVE or not t.acknowledged]
    latest: dict = {}
    for t in rows:
        latest.setdefault(t.category, t)
    return {"tasks": [task_dict(t) for t in visible],
            "latest": {c: task_dict(t) for c, t in latest.items()}}


def acknowledge_category(session: Session, category: str) -> int:
    """Opening the owning tab acknowledges ONLY that category's terminal
    tasks. Active tasks are never dismissed by viewing them."""
    if category not in CATEGORIES:
        raise ValueError(f"unknown task category {category!r}")
    rows = (session.query(DashboardTask)
            .filter(DashboardTask.category == category,
                    DashboardTask.acknowledged.is_(False)).all())
    n = 0
    for t in rows:
        if t.status in TERMINAL:
            t.acknowledged = True
            n += 1
    session.commit()
    return n


def acknowledge_task(session: Session, task_id: str) -> bool:
    """Acknowledge one terminal task without affecting sibling notifications.

    Completed/cancelled bars use this after their five-second display period;
    failed/blocked bars use it only when the user closes the error details.
    Active tasks are deliberately never dismissible.
    """
    t = session.get(DashboardTask, task_id)
    if t is None or t.status in ACTIVE:
        return False
    if t.status not in TERMINAL or t.acknowledged:
        return False
    t.acknowledged = True
    t.updated_at = _now()
    session.commit()
    return True


def sync_fetch_tasks(session: Session) -> None:
    """Reconcile DATA_FETCHING tasks with the market-data operation
    registry (the fetch subsystem's own source of truth)."""
    from app import market_data_service

    rows = (session.query(DashboardTask)
            .filter(DashboardTask.category == "DATA_FETCHING",
                    DashboardTask.status.in_(ACTIVE)).all())
    for t in rows:
        payload = json.loads(t.payload_json) if t.payload_json else {}
        op_id = payload.get("operation_id")
        if not op_id:
            continue
        rec = market_data_service.operation_status(op_id)
        if rec is None:
            continue  # operation registry reset (server restart): keep last state
        state = rec.get("state")
        report = rec.get("report") or {}
        tfs = rec.get("timeframes") or payload.get("timeframes") or []
        done = 0
        candles = 0
        if report:
            datasets = report.get("timeframes") or report.get("datasets") or {}
            if isinstance(datasets, dict):
                items = datasets.values()
            else:
                items = datasets
            for d in items:
                if isinstance(d, dict):
                    if d.get("status") in ("AVAILABLE", "UP_TO_DATE"):
                        done += 1
                    candles += int(d.get("candles") or d.get("rows") or 0)
        payload = {**payload, "timeframes": tfs, "candles_fetched": candles,
                   "stage": rec.get("stage")}
        if state in ("COMPLETED", "PARTIAL"):
            finish_task(session, t.id, "COMPLETED",
                        summary=f"{done}/{len(tfs)} timeframes · {candles} candles",
                        completed_units=done)
            t2 = session.get(DashboardTask, t.id)
            if t2 is not None:
                t2.payload_json = json.dumps(payload)
                session.commit()
        elif state == "FAILED":
            err = rec.get("error") or {}
            finish_task(session, t.id, "FAILED",
                        error=err.get("message") or "fetch failed",
                        completed_units=done)
        else:
            update_task(session, t.id, status="RUNNING",
                        current_unit=rec.get("stage") or "FETCHING",
                        completed_units=done, payload=payload)
