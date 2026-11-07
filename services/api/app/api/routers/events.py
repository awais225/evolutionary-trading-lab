from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.deps import get_registry_session, SYMBOL_RE
from app.schemas import EventCreate, EventListOut, EventOut
from hero1_eventbus import get_event_publisher
from hero1_storage.enums import EventCategory, EventSeverity
from hero1_storage.repositories import EventRepository, events_to_dicts

router = APIRouter(prefix="/api/v1/events", tags=["events"])


def _symbol_filter(value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip().upper()
    if not SYMBOL_RE.match(v):
        raise HTTPException(422, f"malformed instrument filter: {value!r}")
    return v


@router.get("", response_model=EventListOut)
def list_events(
    instrument: str | None = Query(default=None),
    category: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    subsystem: str | None = Query(default=None, max_length=48),
    since_id: int | None = Query(default=None, ge=0),
    search: str | None = Query(default=None, max_length=120,
                               description="matches message and payload (operation ids, timeframes, errors)"),
    offset: int = Query(default=0, ge=0),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    sort: str = Query(default="ts", pattern="^(ts|severity|category|subsystem|instrument|message|id)$"),
    limit: int = Query(default=100, ge=1, le=500),
    registry: Session = Depends(get_registry_session),
) -> EventListOut:
    cat = category.upper() if category else None
    sev = severity.upper() if severity else None
    if cat and cat not in {c.value for c in EventCategory}:
        raise HTTPException(422, f"unknown category: {category!r}")
    if sev and sev not in {s.value for s in EventSeverity}:
        raise HTTPException(422, f"unknown severity: {severity!r}")
    repo = EventRepository(registry)
    search_value = (search.strip() or None) if search else None
    rows = repo.list(instrument=_symbol_filter(instrument), category=cat, severity=sev,
                     subsystem=subsystem, since_id=since_id, limit=limit,
                     search=search_value, offset=offset, desc=(order == "desc"),
                     sort=sort)
    total = repo.count(instrument=_symbol_filter(instrument), category=cat, severity=sev,
                       subsystem=subsystem, search=search_value)
    events = events_to_dicts(rows)
    return EventListOut(limit=limit, count=len(events), total=total,
                        events=[EventOut(**e) for e in events])


@router.get("/categories", response_model=dict)
def list_categories() -> dict:
    return {
        "categories": [c.value for c in EventCategory],
        "severities": [s.value for s in EventSeverity],
    }


@router.post("", response_model=EventOut, status_code=201)
def create_event(body: EventCreate, registry: Session = Depends(get_registry_session)) -> EventOut:
    symbol = _symbol_filter(body.instrument)
    publisher = get_event_publisher()
    event = publisher.publish(
        category=body.category.value, severity=body.severity.value,
        message=body.message, instrument=symbol, subsystem=body.subsystem,
        payload=body.payload, node_id=body.node_id,
    )
    return EventOut(**event)
