from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import spec_service
from app.deps import get_instrument_context, get_instrument_session
from app.schemas import (
    CompileResultOut, NewVersionRequest, NodeCreate, NodeListOut, NodeOut, NodePatch,
    NodeVersionListOut, SpecCompileRequest, SpecValidationRequest, ValidationReportOut,
    ValidationIssueOut,
)
from hero1_eventbus import get_event_publisher
from hero1_storage.enums import ApprovalState, NodeStatus
from hero1_storage.repositories import NodeRepository
from hero1_strategy_spec import SpecCompilationError

router = APIRouter(prefix="/api/v1/instruments/{instrument}/nodes", tags=["nodes"])


# ---------------------------------------------------------------- helpers
def _publish(severity: str, message: str, instrument: str, *,
             node_id: Optional[int] = None, payload: Optional[dict] = None) -> None:
    get_event_publisher().publish(
        category="NODE", severity=severity, subsystem="strategy_spec",
        message=message[:500], instrument=instrument, node_id=node_id,
        payload=payload or {},
    )


_SEVERITY_FOR_STATUS = {"VALID": "INFO", "WARNING": "WARNING",
                        "NEEDS_METADATA": "WARNING", "INVALID": "ERROR"}

# nodes table carries UNIQUE(instrument_id, spec_hash, version) — the server never silently
# duplicates an identical strategy at the same version, it reports it.
_DUPLICATE_SPEC_DETAIL = {
    "code": "SPEC_DUPLICATE",
    "message": ("an identical specification already exists at this version for this instrument. "
                "Inspect the existing node or create the next version instead."),
}


def _report_out(report) -> ValidationReportOut:
    payload = json.loads(report.model_dump_json())
    return ValidationReportOut(**payload)


def _compile_or_422(spec: dict, instrument: str, broker_meta: Optional[dict]):
    try:
        return spec_service.compile_or_raise(spec, instrument, broker_meta)
    except SpecCompilationError as exc:
        report = exc.report
        out = _report_out(report)
        _publish(_SEVERITY_FOR_STATUS.get(report.status.value, "WARNING"),
                 f"spec rejected [{report.status.value}] [{instrument}]",
                 instrument,
                 payload={"status": report.status.value,
                          "codes": [i.code for i in report.issues]})
        raise HTTPException(422, spec_service.spec_blocking_response(report)["detail"]) from exc


# --------------------------------------------------------------- read side
@router.get("", response_model=NodeListOut)
def list_nodes(instrument: str = Depends(get_instrument_context),
               origin: str | None = Query(default=None),
               status: str | None = Query(default=None),
               limit: int = Query(default=200, ge=1, le=500),
               offset: int = Query(default=0, ge=0),
               session: Session = Depends(get_instrument_session)) -> NodeListOut:
    repo = NodeRepository(session, instrument)
    items = repo.list(origin=origin, status=status, limit=limit, offset=offset)
    return NodeListOut(instrument=instrument, total=repo.count(),
                       items=[NodeOut.model_validate(n, from_attributes=True) for n in items])


@router.post("/validate", response_model=ValidationReportOut)
def validate_spec_endpoint(body: SpecValidationRequest,
                           instrument: str = Depends(get_instrument_context)) -> ValidationReportOut:
    """Validate a spec WITHOUT persisting it. Server-side authority."""
    report = spec_service.spec_to_report(body.spec, instrument, body.broker_meta)
    status = report.status.value
    _publish(_SEVERITY_FOR_STATUS.get(status, "WARNING"),
             f"spec validated [{status}] [{instrument}]", instrument,
             payload={"status": status, "warmup": report.required_warmup_candles,
                      "codes": [i.code for i in report.issues]})
    return _report_out(report)


@router.post("/compile", response_model=CompileResultOut)
def compile_spec_endpoint(body: SpecCompileRequest,
                          instrument: str = Depends(get_instrument_context)) -> CompileResultOut:
    """Validate + compile a spec to IR. Nothing is executed — declarative only."""
    _, result = _compile_or_422(body.spec, instrument, body.broker_meta)
    _publish("INFO", f"spec compiled [{result.fingerprint[:12]}…] [{instrument}]", instrument,
             payload={"fingerprint": result.fingerprint, "warmup": result.required_warmup_candles,
                      "compiler_version": result.compiler_version})
    return CompileResultOut(**result.model_dump())


@router.get("/{node_id}", response_model=NodeOut)
def get_node(node_id: int, instrument: str = Depends(get_instrument_context),
             session: Session = Depends(get_instrument_session)) -> NodeOut:
    node = NodeRepository(session, instrument).get(node_id)
    if node is None:
        raise HTTPException(404, f"node {node_id} not found for instrument {instrument}")
    return NodeOut.model_validate(node, from_attributes=True)


@router.get("/{node_id}/lineage", response_model=NodeVersionListOut)
def node_lineage(node_id: int, instrument: str = Depends(get_instrument_context),
                 session: Session = Depends(get_instrument_session)) -> NodeVersionListOut:
    repo = NodeRepository(session, instrument)
    node = repo.get(node_id)
    if node is None:
        raise HTTPException(404, f"node {node_id} not found for instrument {instrument}")
    component = spec_service.lineage_component(session, instrument, node)
    root = min((n for n in component if n.parent_node_id is None), key=lambda n: n.id, default=None)
    ordered = sorted(component, key=lambda n: (n.version, n.id))
    return NodeVersionListOut(
        instrument=instrument,
        lineage_root_id=root.id if root else None,
        items=[NodeOut.model_validate(n, from_attributes=True) for n in ordered],
    )


# -------------------------------------------------------------- write side
@router.post("", response_model=NodeOut, status_code=201)
def create_node(body: NodeCreate,
                instrument: str = Depends(get_instrument_context),
                session: Session = Depends(get_instrument_session)) -> NodeOut:
    repo = NodeRepository(session, instrument)

    # parent (if any) must exist in the SAME instrument database — no cross-instrument lineage
    if body.parent_node_id is not None and repo.get(body.parent_node_id) is None:
        raise HTTPException(422, f"parent node {body.parent_node_id} not found for instrument {instrument}")

    spec_json: Optional[str] = None
    spec_hash: Optional[str] = None
    if body.spec_json is not None:
        parsed, err = spec_service.parse_spec_json(body.spec_json)
        if err:
            raise HTTPException(422, err)
        _, result = _compile_or_422(parsed, instrument, body.broker_meta)
        spec_json, spec_hash = result.canonical_json, result.fingerprint

    try:
        node = repo.create(
            origin=body.origin.value, name=body.name, description=body.description,
            spec_json=spec_json, spec_hash=spec_hash,
            parent_node_id=body.parent_node_id, created_by=body.created_by,
        )
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, _DUPLICATE_SPEC_DETAIL) from None
    _publish("INFO",
             f"node created [{instrument}] #{node.id}: {node.name}"
             + (f" (spec {spec_hash[:12]}…)" if spec_hash else " (no spec — draft)"),
             instrument, node_id=node.id,
             payload={"origin": node.origin, "node_id": node.id, "version": node.version,
                      "spec_hash": spec_hash})
    return NodeOut.model_validate(node, from_attributes=True)


@router.post("/{node_id}/versions", response_model=NodeOut, status_code=201)
def create_version(node_id: int, body: NewVersionRequest,
                   instrument: str = Depends(get_instrument_context),
                   session: Session = Depends(get_instrument_session)) -> NodeOut:
    """Immutable semantics: a changed spec creates a NEW version linked to its parent.
    The parent row is never modified."""
    repo = NodeRepository(session, instrument)
    parent = repo.get(node_id)
    if parent is None:
        raise HTTPException(404, f"node {node_id} not found for instrument {instrument}")

    _, result = _compile_or_422(body.spec, instrument, body.broker_meta)
    version = spec_service.next_version(session, instrument, parent)
    try:
        node = repo.create(
            origin=parent.origin,
            name=body.name or parent.name,
            description=body.description if body.description is not None else parent.description,
            spec_json=result.canonical_json, spec_hash=result.fingerprint,
            parent_node_id=parent.id, created_by=body.created_by, version=version,
        )
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, _DUPLICATE_SPEC_DETAIL) from None
    _publish("INFO",
             f"node version created [{instrument}] #{node.id} v{node.version} (parent #{parent.id})",
             instrument, node_id=node.id,
             payload={"parent_node_id": parent.id, "version": node.version,
                      "spec_hash": result.fingerprint, "fingerprint_prefix": result.fingerprint[:12]})
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
    _publish("INFO", f"node metadata updated [{instrument}] #{node.id}: {sorted(fields)}",
             instrument, node_id=node.id, payload={"updated_fields": sorted(fields)})
    return NodeOut.model_validate(node, from_attributes=True)
