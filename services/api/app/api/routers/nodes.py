from __future__ import annotations

import json
import re
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import spec_service
from app.deps import get_instrument_context, get_instrument_session
from app.schemas import (
    CompileResultOut, NewVersionRequest, NodeCreate, NodeDeleteOut, NodeDeleteRequest,
    NodeDependenciesOut, NodeDuplicateRequest, NodeGenerateOut, NodeGenerateRequest,
    NodeListOut, NodeOut, NodePatch,
    NodeVersionListOut, SpecCompileRequest, SpecValidationRequest, ValidationReportOut,
    ValidationIssueOut,
)
from hero1_eventbus import get_event_publisher
from hero1_storage.enums import ApprovalState, NodeStatus
from hero1_storage.repositories import NodeRepository
from hero1_strategy_spec import SpecCompilationError, SUPPORTED_TIMEFRAMES

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

_NAME_DUPLICATE_DETAIL = {
    "code": "NAME_DUPLICATE",
    "message": "a node with this name already exists for this instrument. Names are unique per instrument.",
}

_C_NAME_RE = re.compile(r"^[Cc](\d+)$")


def _alloc_sequential_names(existing_names, count: int):
    """First available sequential C-numbers (gap reuse), in order."""
    used = set()
    for n in existing_names:
        m = _C_NAME_RE.match(str(n).strip())
        if m:
            used.add(int(m.group(1)))
    out, k = [], 1
    while len(out) < count:
        if k not in used:
            out.append(f"C{k}")
            used.add(k)
        k += 1
    return out


def _integrity_detail(exc: IntegrityError):
    msg = str(getattr(exc, "orig", exc))
    if "nodes.name" in msg or "uq_nodes_instr_name" in msg:
        return _NAME_DUPLICATE_DETAIL
    if "spec_hash" in msg or "uq_nodes_spec" in msg:
        return _DUPLICATE_SPEC_DETAIL
    return {"code": "NODE_CREATE_CONFLICT",
            "message": "conflict with an existing record (unique constraint)."}


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
               starred: bool | None = Query(default=None),
               timeframe: str | None = Query(default=None,
                   description="Comma-separated timeframes (e.g. 'M1,M15'); unknown values are rejected"),
               limit: int = Query(default=200, ge=1, le=500),
               offset: int = Query(default=0, ge=0),
               session: Session = Depends(get_instrument_session)) -> NodeListOut:
    tfs: Optional[List[str]] = None
    if timeframe:
        wanted = [t.strip().upper() for t in timeframe.split(",") if t.strip()]
        bad = [t for t in wanted if t not in SUPPORTED_TIMEFRAMES]
        if bad:
            raise HTTPException(422, detail={"code": "TIMEFRAME_UNSUPPORTED",
                                             "message": f"unsupported timeframe(s) {bad}; supported: {list(SUPPORTED_TIMEFRAMES)}"})
        if len(set(wanted)) != len(wanted):
            raise HTTPException(422, detail={"code": "TIMEFRAME_DUPLICATE",
                                             "message": "duplicate timeframes in the filter"})
        tfs = wanted
    repo = NodeRepository(session, instrument)
    items = repo.list(origin=origin, status=status, starred=starred,
                      timeframes=tfs, limit=limit, offset=offset)
    filtered_total = repo.count(starred=starred, timeframes=tfs)
    return NodeListOut(instrument=instrument, total=repo.count(),
                       starred_total=repo.count(starred=True),
                       filtered_total=filtered_total,
                       timeframe_counts=repo.timeframe_counts(starred=starred),
                       unassigned_timeframe_total=repo.count_no_timeframe(starred=starred),
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


@router.post("/generate", response_model=NodeGenerateOut, status_code=201)
def generate_nodes(body: NodeGenerateRequest,
                   instrument: str = Depends(get_instrument_context),
                   session: Session = Depends(get_instrument_session)) -> NodeGenerateOut:
    """Create ONE INDEPENDENT NODE PER selected timeframe from one strategy definition.

    Each node: own id, own instrument+timeframe, own spec (timeframe-swapped),
    own semantic fingerprint (timeframe IS fingerprinted), own lineage root,
    DATA availability explicitly NOT claimed (no market-data store ships yet).

    Naming: a single timeframe honours an explicit name (409 on conflict);
    multiple timeframes allocate the first available sequential C-names
    atomically. All-or-nothing: any conflict aborts the whole batch.
    """
    repo = NodeRepository(session, instrument)

    ident = body.spec.get("identity") if isinstance(body.spec, dict) else None
    if not isinstance(ident, dict):
        raise HTTPException(422, detail={"code": "SPEC_INVALID",
                                         "message": "spec.identity is missing or invalid"})

    prepared = []
    for tf in body.timeframes:
        cloned = json.loads(json.dumps(body.spec))
        cloned["identity"] = {**cloned["identity"], "timeframe": tf}
        parsed, err = spec_service.parse_spec_json(json.dumps(cloned))
        if err:
            raise HTTPException(422, err)
        _, result = _compile_or_422(parsed, instrument, body.broker_meta)
        prepared.append({"timeframe": tf,
                         "spec_json": result.canonical_json,
                         "spec_hash": result.fingerprint})

    tf_count = len(prepared)
    explicit_single = tf_count == 1 and body.name
    if explicit_single and repo.name_exists(body.name):
        raise HTTPException(409, _NAME_DUPLICATE_DETAIL)

    def _items(names):
        return [{"origin": "CUSTOM", "name": names[i],
                 "description": body.description,
                 "spec_json": p["spec_json"], "spec_hash": p["spec_hash"],
                 "created_by": body.created_by, "timeframe": p["timeframe"]}
                for i, p in enumerate(prepared)]

    names = [body.name.strip()] if explicit_single else _alloc_sequential_names(repo.existing_names(), tf_count)
    try:
        created = repo.create_many(_items(names))
    except IntegrityError as exc:
        session.rollback()
        if explicit_single:
            raise HTTPException(409, _integrity_detail(exc)) from None
        # One retry with a fresh allocation — the race guard is uq_nodes_instr_name.
        try:
            names = _alloc_sequential_names(repo.existing_names(), tf_count)
            created = repo.create_many(_items(names))
        except IntegrityError as exc2:
            session.rollback()
            raise HTTPException(409, _integrity_detail(exc2)) from None

    for node in created:
        _publish("INFO", f"node generated [{instrument}] #{node.id} {node.name} ({node.timeframe})",
                 instrument, node_id=node.id,
                 payload={"origin": node.origin, "node_id": node.id,
                          "version": node.version, "timeframe": node.timeframe,
                          "spec_hash": node.spec_hash})
    return NodeGenerateOut(instrument=instrument,
                           requested=[p["timeframe"] for p in prepared],
                           count=len(created),
                           created=[NodeOut.model_validate(n, from_attributes=True) for n in created])


# -------------------------------------------------------------- write side
@router.post("", response_model=NodeOut, status_code=201)
def create_node(body: NodeCreate,
                instrument: str = Depends(get_instrument_context),
                session: Session = Depends(get_instrument_session)) -> NodeOut:
    repo = NodeRepository(session, instrument)

    # parent (if any) must exist in the SAME instrument database — no cross-instrument lineage
    if body.parent_node_id is not None and repo.get(body.parent_node_id) is None:
        raise HTTPException(422, f"parent node {body.parent_node_id} not found for instrument {instrument}")

    if repo.name_exists(body.name):
        raise HTTPException(409, _NAME_DUPLICATE_DETAIL)

    spec_json: Optional[str] = None
    spec_hash: Optional[str] = None
    timeframe: Optional[str] = None
    if body.spec_json is not None:
        parsed, err = spec_service.parse_spec_json(body.spec_json)
        if err:
            raise HTTPException(422, err)
        _, result = _compile_or_422(parsed, instrument, body.broker_meta)
        spec_json, spec_hash = result.canonical_json, result.fingerprint
        ident = parsed.get("identity") if isinstance(parsed, dict) else None
        tf = ident.get("timeframe") if isinstance(ident, dict) else None
        timeframe = str(tf).upper() if tf else None

    try:
        node = repo.create(
            origin=body.origin.value, name=body.name, description=body.description,
            spec_json=spec_json, spec_hash=spec_hash,
            parent_node_id=body.parent_node_id, created_by=body.created_by,
            timeframe=timeframe,
        )
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, _integrity_detail(exc)) from None
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
    if body.name:
        if repo.name_exists(body.name):
            raise HTTPException(409, _NAME_DUPLICATE_DETAIL)
        child_name = body.name
    else:
        # names are unique per instrument — a new version cannot reuse its parent's
        # display name; it gets the first available sequential C-name (renameable).
        child_name = _alloc_sequential_names(repo.existing_names(), 1)[0]
    version = spec_service.next_version(session, instrument, parent)
    ident = body.spec.get("identity") if isinstance(body.spec, dict) else None
    tf = ident.get("timeframe") if isinstance(ident, dict) else None
    timeframe = str(tf).upper() if tf else None
    try:
        node = repo.create(
            origin=parent.origin,
            name=child_name,
            description=body.description if body.description is not None else parent.description,
            spec_json=result.canonical_json, spec_hash=result.fingerprint,
            parent_node_id=parent.id, created_by=body.created_by, version=version,
            timeframe=timeframe,
        )
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, _integrity_detail(exc)) from None
    _publish("INFO",
             f"node version created [{instrument}] #{node.id} v{node.version} (parent #{parent.id})",
             instrument, node_id=node.id,
             payload={"parent_node_id": parent.id, "version": node.version,
                      "spec_hash": result.fingerprint, "fingerprint_prefix": result.fingerprint[:12]})
    return NodeOut.model_validate(node, from_attributes=True)


# ------------------------------------------------- deletion (ISSUE-054)
def _dependency_summary(repo: NodeRepository, node_id: int) -> dict:
    descendants = repo.descendant_ids(node_id)
    counts = repo.dependent_record_counts(node_id)
    dependent_total = sum(counts.values())
    blocked = None
    if dependent_total:
        blocked = ("node has dependent records that are never cascade-deleted "
                   f"({', '.join(f'{k}={v}' for k, v in counts.items() if v)})")
    return {"node_id": node_id, "descendants": descendants,
            "descendant_count": len(descendants), "dependent_records": counts,
            "dependent_total": dependent_total,
            "deletable": dependent_total == 0, "blocked_reason": blocked}


def _delete_one(repo: NodeRepository, node_id: int, include_descendants: bool,
                instrument: str):
    """Delete one node (optionally its whole descendant subtree).

    Returns (deleted_ids, failure|None). Guards run BEFORE any row is
    touched: descendants require the explicit opt-in, and ANY dependent
    record (backtests, deeptest, live, approvals) anywhere in the subtree
    blocks the whole operation - those records are never cascade-deleted.
    """
    node = repo.get(node_id)
    if node is None:
        return [], {"node_id": node_id, "code": "NODE_NOT_FOUND",
                    "message": f"node {node_id} not found for instrument {instrument}"}
    descendants = repo.descendant_ids(node_id)
    if descendants and not include_descendants:
        return [], {"node_id": node_id, "code": "NODE_HAS_DESCENDANTS",
                    "message": (f"node #{node_id} has {len(descendants)} descendant(s) "
                                f"{descendants[:10]}; pass include_descendants=true to "
                                "delete the subtree explicitly")}
    targets = list(reversed(descendants)) + [node_id]  # deepest first
    for tid in targets:
        counts = repo.dependent_record_counts(tid)
        if any(counts.values()):
            return [], {"node_id": node_id, "code": "NODE_HAS_DEPENDENT_RECORDS",
                        "message": (f"node #{tid} has dependent records that are never "
                                    f"cascade-deleted: "
                                    f"{ {k: v for k, v in counts.items() if v} }")}
    snapshot = []
    for tid in targets:
        n = repo.get(tid)
        if n is not None:
            snapshot.append({"id": n.id, "name": n.name, "version": n.version,
                             "parent_node_id": n.parent_node_id,
                             "spec_hash": n.spec_hash, "timeframe": n.timeframe})
    deleted = repo.delete_nodes(targets)
    _publish("WARNING",
             f"node deleted [{instrument}] #{node_id}"
             + (f" + {len(deleted) - 1} descendant(s)" if len(deleted) > 1 else ""),
             instrument, node_id=node_id,
             payload={"action": "NODE_DELETED", "deleted": snapshot,
                      "include_descendants": include_descendants})
    return deleted, None


@router.get("/{node_id}/dependencies", response_model=NodeDependenciesOut)
def node_dependencies(node_id: int, instrument: str = Depends(get_instrument_context),
                      session: Session = Depends(get_instrument_session)) -> NodeDependenciesOut:
    repo = NodeRepository(session, instrument)
    if repo.get(node_id) is None:
        raise HTTPException(404, f"node {node_id} not found for instrument {instrument}")
    return NodeDependenciesOut(**_dependency_summary(repo, node_id))


@router.delete("/{node_id}", response_model=NodeDeleteOut)
def delete_node(node_id: int, include_descendants: bool = False,
                instrument: str = Depends(get_instrument_context),
                session: Session = Depends(get_instrument_session)) -> NodeDeleteOut:
    repo = NodeRepository(session, instrument)
    deleted, failure = _delete_one(repo, node_id, include_descendants, instrument)
    if failure is not None:
        code = failure["code"]
        raise HTTPException(404 if code == "NODE_NOT_FOUND" else 409,
                            detail={"code": code, "message": failure["message"]})
    return NodeDeleteOut(deleted=deleted, failed=[])


@router.post("/delete", response_model=NodeDeleteOut)
def delete_nodes_bulk(body: NodeDeleteRequest,
                      instrument: str = Depends(get_instrument_context),
                      session: Session = Depends(get_instrument_session)) -> NodeDeleteOut:
    """Bulk delete: each node (with its subtree, if opted in) is atomic;
    per-node failures are reported, never hidden, and never block others."""
    repo = NodeRepository(session, instrument)
    deleted: List[int] = []
    failed = []
    seen: set = set()
    for node_id in body.node_ids:
        if node_id in seen:
            continue
        seen.add(node_id)
        d, failure = _delete_one(repo, node_id, body.include_descendants, instrument)
        if failure is not None:
            failed.append(failure)
        else:
            deleted.extend(d)
    return NodeDeleteOut(deleted=deleted, failed=failed)


@router.post("/{node_id}/duplicate", response_model=NodeOut, status_code=201)
def duplicate_node(node_id: int, body: NodeDuplicateRequest,
                   instrument: str = Depends(get_instrument_context),
                   session: Session = Depends(get_instrument_session)) -> NodeOut:
    """Duplicate & Edit: create a NEW independent node from an edited draft
    of the source node's spec. The source is NEVER modified. The new node
    records the source as parent (lineage 'derived_from'), gets its own id
    and a server-recomputed fingerprint; an unchanged spec hits the existing
    (instrument, spec_hash, version=1) duplicate rule -> 409 SPEC_DUPLICATE.
    """
    repo = NodeRepository(session, instrument)
    source = repo.get(node_id)
    if source is None:
        raise HTTPException(404, f"node {node_id} not found for instrument {instrument}")
    spec = body.spec
    if spec is None:
        if not source.spec_json:
            raise HTTPException(422, detail={
                "code": "NO_SPEC_TO_DUPLICATE",
                "message": "source node has no strategy spec; Duplicate & Edit applies to spec-backed nodes"})
        spec = json.loads(source.spec_json)
    _, result = _compile_or_422(spec, instrument, body.broker_meta)
    if body.name:
        if repo.name_exists(body.name):
            raise HTTPException(409, _NAME_DUPLICATE_DETAIL)
        name = body.name.strip()
    else:
        name = _alloc_sequential_names(repo.existing_names(), 1)[0]
    ident = spec.get("identity") if isinstance(spec, dict) else None
    tf = ident.get("timeframe") if isinstance(ident, dict) else None
    timeframe = str(tf).upper() if tf else None
    try:
        node = repo.create(
            origin=source.origin, name=name,
            description=(body.description if body.description is not None
                         else source.description),
            spec_json=result.canonical_json, spec_hash=result.fingerprint,
            parent_node_id=source.id, created_by="user", version=1,
            timeframe=timeframe,
        )
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, _integrity_detail(exc)) from None
    _publish("INFO",
             f"node duplicated [{instrument}] #{source.id} -> #{node.id} ({node.name})",
             instrument, node_id=node.id,
             payload={"action": "NODE_DUPLICATED", "source_node_id": source.id,
                      "new_node_id": node.id, "spec_hash": result.fingerprint,
                      "timeframe": timeframe})
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
    if fields.get("name"):
        repo = NodeRepository(session, instrument)
        if repo.name_exists(fields["name"], exclude_id=node_id):
            raise HTTPException(409, _NAME_DUPLICATE_DETAIL)
    try:
        node = NodeRepository(session, instrument).update(node_id, **fields)
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, _integrity_detail(exc)) from None
    if node is None:
        raise HTTPException(404, f"node {node_id} not found for instrument {instrument}")
    _publish("INFO", f"node metadata updated [{instrument}] #{node.id}: {sorted(fields)}",
             instrument, node_id=node.id, payload={"updated_fields": sorted(fields)})
    return NodeOut.model_validate(node, from_attributes=True)
