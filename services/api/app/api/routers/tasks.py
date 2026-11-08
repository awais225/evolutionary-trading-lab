"""Dashboard task progress endpoints (global bars / Live Feed / DeepTesting).

All three UI surfaces read the SAME `dashboard_tasks` records; nothing is
calculated independently in the frontend.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import task_service
from app.deps import get_registry_session

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


class AcknowledgeBody(BaseModel):
    category: str


@router.get("")
def list_tasks(session: Session = Depends(get_registry_session)):
    task_service.sync_fetch_tasks(session)
    return task_service.list_visible(session)


@router.get("/{task_id}")
def get_task(task_id: str, session: Session = Depends(get_registry_session)):
    from hero1_storage.registry_models import DashboardTask
    t = session.get(DashboardTask, task_id)
    if t is None:
        raise HTTPException(404, f"task {task_id!r} not found")
    return task_service.task_dict(t)


@router.post("/acknowledge")
def acknowledge(body: AcknowledgeBody,
                session: Session = Depends(get_registry_session)):
    try:
        n = task_service.acknowledge_category(session, body.category)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"acknowledged": n, "category": body.category}
