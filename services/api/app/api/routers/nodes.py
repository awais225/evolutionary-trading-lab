from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.deps import get_instrument_context, get_instrument_session
from app.schemas import NodeCreate, NodeListOut, NodeOut, NodePatch
from hero1_eventbus import get_event_publisher
from hero1_storage.enums import ApprovalState, NodeStatus
from hero1_storage.repositories import NodeRepository

router = APIRouter(prefix="/api/v1/instruments/{instrument}/nodes", tags=["nodes"])


@router.get("", response_model=NodeListOut)
def list_nodes(
    instrument: str = Depends(get_instrument_context),
    origin: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_instrument_session),
) -> NodeListOut:
    repo = NodeRepository(session, instrument)
    items = repo.list(origin=origin, status=status, limit=limit, offset=offset)
    return NodeListOut(
        instrument=instrument,
        total=repo.count(),
        items=[NodeOut.model_validate(n, from_attributes=True) for n in items],
    )


@router.post("", response_model=NodeOut, status_code=201)
def create_node(
    body: NodeCreate,
    instrument: str = Depends(get_instrument_context),
    session: Session = Depends(get_instrument_session),
) -> NodeOut:
    repo = NodeRepository(session, instrument)
    try:
        node = repo.create(
            origin=body.origin.value, name=body.name, description=body.description,
            spec_json=body.spec_json, spec_hash=body.spec_hash,
            parent_node_id=body.parent_node_id, created_by=body.created_by,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    get_event_publisher().publish(
        category="NODE", severity="INFO", subsystem="nodes",
        message=f"node created [{instrument}] #{node.id}: {node.name}",
        instrument=instrument, node_id=node.id,
        payload={"origin": node.origin, "node_id": node.id},
    )
    return NodeOut.model_validate(node, from_attributes=True)


@router.get("/{node_id}", response_model=NodeOut)
def get_node(node_id: int, instrument: str = Depends(get_instrument_context),
             session: Session = Depends(get_instrument_session)) -> NodeOut:
    node = NodeRepository(session, instrument).get(node_id)
    if node is None:
        raise HTTPException(404, f"node {node_id} not found for instrument {instrument}")
    return NodeOut.model_validate(node, from_attributes=True)


@router.patch("/{node_id}", response_model=NodeOut)
def patch_node(body: NodePatch, node_id: int,
               instrument: str = Depends(get_instrument_context),
               session: Session = Depends(get_instrument_session)) -> NodeOut:
    fields = body.model_dump(exclude_unset=True, exclude_none=True)
    if "approval_state" in fields and fields["approval_state"] is not None:
        fields["approval_state"] = ApprovalState(fields["approval_state"]).value
    if "status" in fields and fields["status"] is not None:
        fields["status"] = NodeStatus(fields["status"]).value
    node = NodeRepository(session, instrument).update(node_id, **fields)
    if node is None:
        raise HTTPException(404, f"node {node_id} not found for instrument {instrument}")
    get_event_publisher().publish(
        category="NODE", severity="INFO", subsystem="nodes",
        message=f"node updated [{instrument}] #{node.id}: {sorted(fields)}",
        instrument=instrument, node_id=node.id,
        payload={"updated_fields": sorted(fields)},
    )
    return NodeOut.model_validate(node, from_attributes=True)
