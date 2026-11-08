"""NODE 1 seeding (Part 2.7): idempotent, canonical, user-edit safe."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/api"))

from hero1_strategy_spec import StrategySpec, semantic_fingerprint  # noqa: E402
from hero1_strategy_spec.examples import prev_day_high_low_momentum_reversal  # noqa: E402

NAME = "NODE 1 — Previous Day High Low Momentum Reversal"


def _node1s(client):
    items = client.get("/api/v1/instruments/XAUUSD/nodes?limit=500").json()["items"]
    return [n for n in items if n["name"] == NAME]


def test_node1_seeded_once_by_startup(client):
    found = _node1s(client)
    assert len(found) == 1, "NODE 1 must be seeded exactly once at startup"
    node = found[0]
    spec = prev_day_high_low_momentum_reversal("XAUUSD", "H1")
    fingerprint, _ = semantic_fingerprint(StrategySpec.model_validate(spec))
    assert node["spec_hash"] == fingerprint
    assert node["timeframe"] == "H1" and node["version"] == 1


def test_ensure_seed_nodes_idempotent_and_edit_safe(client):
    from hero1_storage.engine import instrument_session
    from app.seed_nodes import ensure_seed_nodes
    session = instrument_session("XAUUSD")
    try:
        assert ensure_seed_nodes(session, "XAUUSD") == []  # already seeded
        # simulate a user edit: fingerprint changes -> still no duplicate
        from hero1_storage import instrument_models as im
        row = session.query(im.Node).filter_by(name=NAME).one()
        original = row.spec_hash
        row.spec_hash = "user-edited-fingerprint"
        session.commit()
        assert ensure_seed_nodes(session, "XAUUSD") == []
        assert session.query(im.Node).filter_by(name=NAME).count() == 1
        row.spec_hash = original  # restore: shared session test database
        session.commit()
    finally:
        session.close()
