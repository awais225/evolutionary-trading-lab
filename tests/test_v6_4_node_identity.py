"""V6.4 #3 — ONE canonical node-identity contract across every surface.

The lab's real study contains BOTH a node with database id 6658 (research #5871,
QUALIFIED, +6.71% return) AND a different node whose study-local number is 6658
(database id 7445, RETIRED). Pages that used the bare number "6658" pointed at
different nodes: the Overview showed "#6658", the detail page renamed it
"Research Node #5,871", and a search in the Live Testing qualified table found
neither.

Contract (V6.4): the CANONICAL identity is the database primary key
("Node #<id>" — the key every API URL, worker and navigation target uses);
research_node_num is the explicitly-labeled secondary ("research #<num>").
Generation and label are descriptive, never identity. A QUALIFIED badge attaches
to the exact canonical node, never to a different node that merely carries a
related number.
"""
from __future__ import annotations

import json
import time

import pytest


def _add_node(db, node_id, research_num, *, status, fitness=0.5, gen=1):
    db.x("""INSERT OR REPLACE INTO strategies
            (id, hash, parent_id, generation, symbol, timeframe, direction, status,
             genome, complexity, fitness, created_at, updated_at, origin, run_id,
             data_source, research_node_num)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
         (node_id, f"ident-{node_id}", None, gen, "XAUUSD", "M15", "short", status,
          json.dumps({"symbol": "XAUUSD", "timeframe": "M15", "direction": "short",
                      "entry_short": {"type": "compare", "left": "close", "cmp": ">",
                                      "right": 0}}),
          3, fitness, time.time(), time.time(), "research",
          "RUN-IDENTITY-TEST", "USER_RESEARCH", research_num))


@pytest.fixture()
def db(tmp_path):
    from app.db.database import Database
    d = Database(str(tmp_path / "identity.db"))
    d.get_shortlist()            # materialise lazily-created tables
    d.x("DELETE FROM strategies")
    d.x("DELETE FROM live_test_configs")
    # the REAL study's collision, reproduced exactly: the number 6658 exists in
    # BOTH namespaces and belongs to two different nodes
    _add_node(d, 6658, 5871, status="QUALIFIED", fitness=0.74294, gen=7)
    _add_node(d, 7445, 6658, status="RETIRED", fitness=0.2, gen=9)
    return d


@pytest.fixture()
def api(monkeypatch, db):
    import app.api.routes as routes
    import app.db.database as database_mod
    import app.live_testing.engine as eng_mod
    from fastapi.testclient import TestClient
    from app.main import app

    monkeypatch.setattr(database_mod, "get_db", lambda: db)
    monkeypatch.setattr(routes, "get_db", lambda: db)
    monkeypatch.setattr(eng_mod, "get_db", lambda: db)
    client = TestClient(app)
    yield client


def _rows(api, filter_="all", search=None):
    params = {"filter": filter_, "limit": 50}
    if search:
        params["search"] = search          # URL-encoded by the client (# is a fragment!)
    d = api.get("/api/nodes", params=params).json()
    assert d["ok"] is True, d
    return {r["node_id"]: r for r in d["nodes"]}, d


# --------------------------------------------------------------------------- #
# the contract on the payloads
# --------------------------------------------------------------------------- #
def test_01_canonical_identity_is_the_database_id_research_is_the_labeled_secondary(api):
    rows, _ = _rows(api)
    a, b = rows[6658], rows[7445]
    assert a["node_label"] == "Node #6658"          # canonical: the database id
    assert a["research_label"] == "research #5871"  # explicitly-labeled secondary
    assert b["node_label"] == "Node #7445"
    assert b["research_label"] == "research #6658"  # the collision, labeled
    # never a bare ambiguous number as identity
    assert "6658" != b["node_label"]
    # the identity block states the contract
    for r in (a, b):
        assert r["identity"]["canonical"] == "database row id (node_id / strategy_id)"
        assert r["identity"]["canonical_label"] == r["node_label"]
        assert r["identity"]["research_label"] == r["research_label"]
        assert r["identity"]["generation"] == r["generation"]  # gen is descriptive


def test_02_the_overview_row_and_the_detail_page_name_the_same_node(api):
    """The Overview's row opens exactly the node its label names."""
    rows, _ = _rows(api)
    r = rows[6658]
    # whatever the Overview renders (node_label) must embed the canonical id
    assert str(r["node_id"]) in r["node_label"]
    # and the canonical id is the id the detail API serves
    detail = api.get(f"/api/strategies/{r['node_id']}").json()
    assert str(detail["strategy"]["id"]) == str(r["node_id"])
    assert detail["strategy"]["research_node_num"] == r["research_node_num"]


def test_03_search_disambiguates_the_two_namespaces(api):
    """"6658" finds BOTH nodes and says which namespace matched."""
    by_id, _ = _rows(api, search="6658")
    assert set(by_id) == {6658, 7445}
    assert by_id[6658]["node_label"] == "Node #6658"
    assert by_id[7445]["research_label"] == "research #6658"
    # "5871" finds only the node whose research number is 5871
    by_num, _ = _rows(api, search="5871")
    assert set(by_num) == {6658}
    # the canonical node's label is unique to its own id
    by_label, _ = _rows(api, search="node #7445")
    assert set(by_label) == {7445}


def test_04_qualified_applies_to_the_exact_canonical_node_only(api):
    """The QUALIFIED badge belongs to id 6658; the research-6658 node is NOT it."""
    rows, _ = _rows(api, filter_="qualified")
    assert 6658 in rows, "the canonical qualified node must be in the qualified set"
    assert rows[6658]["qualification"] == "QUALIFIED"
    assert rows[6658]["bucket"] == "qualified"
    assert 7445 not in rows, "a different node carrying the related number must not appear"
    failed, _ = _rows(api, filter_="failed")
    assert 7445 in failed and failed[7445]["bucket"] == "failed"


def test_05_live_testing_index_lists_and_opens_the_canonical_node(api):
    """The Live Testing qualified table (the qualified-node index) shows the
    canonical row under one label, and the frontend opens exactly node_id."""
    rows, _ = _rows(api, filter_="qualified")
    assert 6658 in rows
    assert rows[6658]["node_label"] == "Node #6658"
    assert rows[6658]["research_label"] == "research #5871"
    # the same id the Overview row opens
    assert rows[6658]["node_id"] == rows[6658]["strategy_id"]
    # the Live Testing table navigates by the canonical id (never the research #)
    import re
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "frontend" / "src" / "components" / "LiveNodeIndex.jsx"
    text = src.read_text(encoding="utf-8")
    assert "onOpenNode(r.node_id)" in text
    assert "research #" in text and "Node #" in text


def test_06_generation_and_label_are_never_identity(api):
    rows, _ = _rows(api)
    a, b = rows[6658], rows[7445]
    assert a["generation"] == 7 and b["generation"] == 9
    assert a["identity"]["generation"] == 7
    # two nodes can share a generation; only node_id is unique
    assert a["node_id"] != b["node_id"]
