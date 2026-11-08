"""Official seed nodes (idempotent).

NODE 1 — Previous Day High Low Momentum Reversal (XAUUSD) is created once
through the canonical persistence mechanism: the spec is produced by the
real schema builder, stored as canonical JSON with the server-side
semantic fingerprint, and versioned/lineaged like any user node.

Idempotency rules (never violate):
* a node with the seed fingerprint already exists -> nothing to do;
* a node with the seed NAME exists but a different fingerprint -> the
  user edited their NODE 1: never overwrite, never duplicate;
* otherwise create version 1 exactly once.
"""
from __future__ import annotations

import json
from typing import List

from sqlalchemy.orm import Session

from hero1_storage import instrument_models as im
from hero1_storage.repositories import NodeRepository
from hero1_strategy_spec import StrategySpec, semantic_fingerprint
from hero1_strategy_spec.examples import prev_day_high_low_momentum_reversal

from . import spec_service

SEED_INSTRUMENT = "XAUUSD"


def node1_spec() -> dict:
    return prev_day_high_low_momentum_reversal(SEED_INSTRUMENT, "H1")


def ensure_seed_nodes(session: Session, instrument: str) -> List[int]:
    if instrument.upper() != SEED_INSTRUMENT:
        return []
    spec = node1_spec()
    canonical = spec_service.canonical_spec_json(spec)
    fingerprint, _ = semantic_fingerprint(StrategySpec.model_validate(spec))
    existing = session.query(im.Node).filter_by(
        instrument_id=SEED_INSTRUMENT).all()
    if any(n.spec_hash == fingerprint for n in existing):
        return []
    name = spec["identity"]["name"]
    if any(n.name == name for n in existing):
        return []  # user-edited NODE 1 - preserve it exactly
    repo = NodeRepository(session, SEED_INSTRUMENT)
    node = repo.create(
        origin=spec["identity"]["origin"], name=name,
        description=spec["identity"].get("description"),
        spec_json=canonical, spec_hash=fingerprint,
        parent_node_id=None, created_by="system-seed", timeframe="H1")
    session.commit()
    return [node.id]


def node1_export_json() -> str:
    """The canonical NODE 1 spec JSON, importable through the dashboard."""
    return json.dumps(node1_spec(), indent=2)
