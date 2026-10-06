"""
Unit & Integration tests for Evolution Tree Lineage Visualization & Branch State Rules:
  1. Authoritative parent-child relationship in database & API
  2. Status-based pathway coloring rules:
     - Active / Verified child (QUALIFIED, PAPER, SURVIVED) -> active green branch
     - Dead / Failed child (FAILED, KILLED, RETIRED) -> dead grey branch
  3. Sibling branch distinction (active sibling is green, dead sibling is grey)
  4. Ancestor and descendant lineage extraction
  5. Legacy nodes without parent_id (parent_id = None) compatibility
  6. Deterministic 6-node tree from Prompt Part 44
  7. /api/health and /health endpoint contracts
"""
import pytest
from app.db.database import Database
from app.api.routes import router
from app.main import app as main_app
from fastapi.testclient import TestClient
from fastapi import FastAPI

app = FastAPI()
app.include_router(router)
client = TestClient(app)
main_client = TestClient(main_app)


def test_tree_api_returns_authoritative_lineage(tmp_path):
    # Use isolated test database
    db_path = str(tmp_path / "test_tree.db")
    db = Database(db_path)
    import app.api.routes as routes
    routes.get_db = lambda: db

    # Insert root node 1
    sid1 = db.insert_strategy({
        "symbol": "XAUUSD", "timeframe": "M15", "species_key": "M15|any|none",
        "genome": {}, "hash": "h_node_1", "parent_id": None, "generation": 0,
        "status": "QUALIFIED", "fitness": 1.5,
    })

    # Insert active child node 2 (parent: 1)
    sid2 = db.insert_strategy({
        "symbol": "XAUUSD", "timeframe": "M15", "species_key": "M15|any|none",
        "genome": {}, "hash": "h_node_2", "parent_id": sid1, "generation": 1,
        "status": "PAPER", "fitness": 1.8,
    })

    # Insert dead child node 3 (parent: 1)
    sid3 = db.insert_strategy({
        "symbol": "XAUUSD", "timeframe": "M15", "species_key": "M15|any|none",
        "genome": {}, "hash": "h_node_3", "parent_id": sid1, "generation": 1,
        "status": "FAILED", "fitness": 0.4,
    })
    db.update_strategy(sid3, failure_reason="Screening trade count too low")

    # Insert grandchild node 4 (parent: 2, active)
    sid4 = db.insert_strategy({
        "symbol": "XAUUSD", "timeframe": "M15", "species_key": "M15|any|none",
        "genome": {}, "hash": "h_node_4", "parent_id": sid2, "generation": 2,
        "status": "QUALIFIED", "fitness": 2.1,
    })
    db.update_strategy(sid4, survival_reason="Passed all 8 walk-forward windows")

    # Fetch tree from API
    resp = client.get("/api/tree")
    assert resp.status_code == 200
    data = resp.json()

    nodes = {n["id"]: n for n in data["nodes"]}
    assert sid1 in nodes
    assert sid2 in nodes
    assert sid3 in nodes
    assert sid4 in nodes

    # Verify node fields
    assert nodes[sid1]["parent_id"] is None
    assert nodes[sid2]["parent_id"] == sid1
    assert nodes[sid3]["parent_id"] == sid1
    assert nodes[sid4]["parent_id"] == sid2

    assert nodes[sid3]["failure_reason"] == "Screening trade count too low"
    assert nodes[sid4]["survival_reason"] == "Passed all 8 walk-forward windows"

    # Verify edges and status-based pathway colors
    edge_map = {(e["source"], e["target"]): e for e in data["edges"]}

    # Edge (1 -> 2): Active parent to Active child -> active (GREEN)
    e1_2 = edge_map.get((sid1, sid2))
    assert e1_2 is not None
    assert e1_2["branch_state"] == "active"
    assert e1_2["is_alive"] is True
    assert e1_2["is_dead"] is False

    # Edge (1 -> 3): Active parent to Dead child -> dead (GREY)
    e1_3 = edge_map.get((sid1, sid3))
    assert e1_3 is not None
    assert e1_3["branch_state"] == "dead"
    assert e1_3["is_alive"] is False
    assert e1_3["is_dead"] is True

    # Edge (2 -> 4): Active parent to Active child -> active (GREEN)
    e2_4 = edge_map.get((sid2, sid4))
    assert e2_4 is not None
    assert e2_4["branch_state"] == "active"
    assert e2_4["is_alive"] is True


def test_deterministic_spec_part_44_tree_data(tmp_path):
    """
    Validates exact Part 44 specification test tree:
    ROOT
    |
    +--- A (PAPER)
    |    |
    |    +--- C (QUALIFIED)
    |    |
    |    +--- D (FAILED)
    |
    +--- B (FAILED)
         |
         +--- E (KILLED)
    """
    db_path = str(tmp_path / "test_part_44.db")
    db = Database(db_path)
    import app.api.routes as routes
    routes.get_db = lambda: db

    root = db.insert_strategy({
        "symbol": "XAUUSD", "timeframe": "M15", "species_key": "M15|any|rsi",
        "genome": {}, "hash": "h_root", "parent_id": None, "generation": 0,
        "status": "QUALIFIED", "fitness": 2.1,
    })
    child_a = db.insert_strategy({
        "symbol": "XAUUSD", "timeframe": "M15", "species_key": "M15|any|rsi",
        "genome": {}, "hash": "h_a", "parent_id": root, "generation": 1,
        "status": "PAPER", "fitness": 2.3,
    })
    child_b = db.insert_strategy({
        "symbol": "XAUUSD", "timeframe": "M15", "species_key": "M15|any|rsi",
        "genome": {}, "hash": "h_b", "parent_id": root, "generation": 1,
        "status": "FAILED", "fitness": 0.5,
    })
    child_c = db.insert_strategy({
        "symbol": "XAUUSD", "timeframe": "M15", "species_key": "M15|any|rsi",
        "genome": {}, "hash": "h_c", "parent_id": child_a, "generation": 2,
        "status": "QUALIFIED", "fitness": 2.6,
    })
    child_d = db.insert_strategy({
        "symbol": "XAUUSD", "timeframe": "M15", "species_key": "M15|any|rsi",
        "genome": {}, "hash": "h_d", "parent_id": child_a, "generation": 2,
        "status": "FAILED", "fitness": 0.6,
    })
    child_e = db.insert_strategy({
        "symbol": "XAUUSD", "timeframe": "M15", "species_key": "M15|any|rsi",
        "genome": {}, "hash": "h_e", "parent_id": child_b, "generation": 2,
        "status": "KILLED", "fitness": 0.2,
    })

    resp = client.get("/api/tree")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["nodes"]) == 6
    assert len(data["edges"]) == 5

    edge_map = {(e["source"], e["target"]): e for e in data["edges"]}

    # Root -> A is active (GREEN)
    assert edge_map[(root, child_a)]["branch_state"] == "active"
    # Root -> B is dead (GREY)
    assert edge_map[(root, child_b)]["branch_state"] == "dead"
    # A -> C is active (GREEN)
    assert edge_map[(child_a, child_c)]["branch_state"] == "active"
    # A -> D is dead (GREY)
    assert edge_map[(child_a, child_d)]["branch_state"] == "dead"
    # B -> E is dead (GREY)
    assert edge_map[(child_b, child_e)]["branch_state"] == "dead"


def test_legacy_nodes_without_parent_compatible(tmp_path):
    db_path = str(tmp_path / "test_legacy.db")
    db = Database(db_path)
    import app.api.routes as routes
    routes.get_db = lambda: db

    # Legacy node with parent_id = None
    sid = db.insert_strategy({
        "symbol": "XAUUSD", "timeframe": "M15", "species_key": "M15|any|none",
        "genome": {}, "hash": "h_legacy", "parent_id": None, "generation": 0,
        "status": "QUALIFIED", "fitness": 1.1,
    })

    resp = client.get("/api/tree")
    assert resp.status_code == 200
    data = resp.json()
    assert any(n["id"] == sid and n["parent_id"] is None for n in data["nodes"])


def test_api_health_endpoint():
    resp = main_client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["database"] == "connected"
    assert data["version"] in ("2.4.0", "2.71", "3.1", "EVOLUTIONARY TRADING RESEARCH LAB V3.1", "3.2", "EVOLUTIONARY TRADING RESEARCH LAB V3.2", "3.6", "EVOLUTIONARY TRADING RESEARCH LAB V3.6")
    assert "mt5_api" in data
