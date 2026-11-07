"""Phase 2 — server-side strategy spec pipeline: validate, compile, version.

The server is the sole authority on fingerprints. A client-supplied spec is
data, never code: it is validated and compiled declaratively — nothing is
executed, imported, or eval'd.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from hero1_storage import instrument_models as im
from hero1_strategy_spec import (
    SpecCompilationError, ValidationReport, canonical_json, compile_spec, validate_spec,
)
from hero1_strategy_spec.validation import ReportStatus


def _resolve_broker_meta(instrument: str, broker_meta: Optional[dict]) -> Optional[dict]:
    """Broker metadata precedence: explicit request body -> stored MT5 snapshot
    (DATA/MARKET_METADATA) -> None (validation then reports NEEDS_METADATA)."""
    if broker_meta:
        return broker_meta
    try:
        from app import metadata_service
        return metadata_service.snapshot_broker_meta(instrument)
    except Exception:  # metadata store must never break validation wholesale
        return None


def spec_to_report(data: Any, instrument: str, broker_meta: Optional[dict]) -> ValidationReport:
    return validate_spec(data, expected_instrument=instrument,
                         broker_meta=_resolve_broker_meta(instrument, broker_meta))


def report_payload(report: ValidationReport) -> dict:
    return json.loads(report.model_dump_json())


def spec_blocking_response(report: ValidationReport) -> dict:
    status = report.status.value
    code = "SPEC_NEEDS_METADATA" if status == ReportStatus.NEEDS_METADATA.value else "SPEC_INVALID"
    return {
        "detail": {
            "code": code,
            "message": f"specification is {status.lower()} and cannot be saved/compiled",
            "status": status,
            "issues": report_payload(report)["issues"],
        }
    }


def compile_or_raise(data: Any, instrument: str, broker_meta: Optional[dict]):
    broker_meta = _resolve_broker_meta(instrument, broker_meta)
    report = validate_spec(data, expected_instrument=instrument, broker_meta=broker_meta)
    if report.status in (ReportStatus.INVALID, ReportStatus.NEEDS_METADATA):
        raise SpecCompilationError(report)
    return report, compile_spec(data, expected_instrument=instrument,
                                broker_meta=broker_meta, report=report)


def canonical_spec_json(spec_dict: Any) -> str:
    """Normalized JSON stored in nodes.spec_json (canonical, stable)."""
    from hero1_strategy_spec import StrategySpec
    return canonical_json(StrategySpec.model_validate(spec_dict).model_dump(mode="json"))


def lineage_component(session: Session, instrument: str, node: im.Node) -> List[im.Node]:
    """All nodes in the same versioning lineage as `node` (up to root, then down)."""
    root = node
    hops = 0
    while root.parent_node_id is not None and hops < 1000:
        parent = session.execute(
            select(im.Node).where(im.Node.instrument_id == instrument,
                                  im.Node.id == root.parent_node_id)
        ).scalar_one_or_none()
        if parent is None:
            break
        root = parent
        hops += 1

    component = [root]
    seen = {root.id}
    frontier = [root]
    while frontier:
        nxt = []
        for current in frontier:
            children = session.execute(
                select(im.Node).where(im.Node.instrument_id == instrument,
                                      im.Node.parent_node_id == current.id)
            ).scalars().all()
            for child in children:
                if child.id not in seen:
                    seen.add(child.id)
                    component.append(child)
                    nxt.append(child)
        frontier = nxt
    return component


def next_version(session: Session, instrument: str, parent: im.Node) -> int:
    return max(n.version for n in lineage_component(session, instrument, parent)) + 1


def parse_spec_json(raw: str) -> Tuple[Optional[Any], Optional[str]]:
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as exc:
        return None, f"spec_json is not valid JSON: {exc}"
