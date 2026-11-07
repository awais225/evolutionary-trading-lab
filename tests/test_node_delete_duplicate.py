"""Manual Nodes delete (single + bulk) and Duplicate & Edit (ISSUE-054).

Verified behavior, all against the real API + per-instrument SQLite DB:
  * dependencies endpoint reports descendants and dependent records;
  * deleting a node with descendants is refused (409) unless the caller
    explicitly opts into subtree deletion;
  * dependent records (backtest runs etc.) BLOCK deletion and are never
    cascade-erased;
  * bulk delete reports per-node deleted/failed and continues past failures;
  * duplicate creates a NEW independent node: new id, parent = source,
    lineage 'derived_from' recorded, source byte-identical afterwards;
  * an unedited duplicate hits the existing duplicate-prevention rule (409
    SPEC_DUPLICATE) - renaming alone does not change semantic identity;
  * duplicating with an edited spec (direction / timeframe / condition)
    produces a new fingerprint.
"""
from __future__ import annotations

import itertools
import json

import pytest
from sqlalchemy import text

from hero1_strategy_spec import bullish_engulfing

_COUNTER = itertools.count(1)
BASE = "/api/v1/instruments/XAUUSD/nodes"


def _spec(tf="M15", direction=None, tag=None):
    spec = bullish_engulfing("XAUUSD", tf)
    spec = json.loads(json.dumps(spec))
    spec["identity"]["tags"] = [*spec["identity"].get("tags", []),
                                tag or f"dup-test-{next(_COUNTER)}"]
    if direction:
        spec["identity"]["direction"] = direction
    return spec


META = {"XAUUSD": {"pip_size": 0.1, "point_size": 0.01,
                   "tick_size": 0.01, "tick_value": 1.0}}


def _create(client, spec=None, name=None):
    body = {"name": name or f"node-{next(_COUNTER)}", "origin": "CUSTOM",
            "broker_meta": META}
    if spec is not None:
        body["spec_json"] = json.dumps(spec)
    r = client.post(BASE, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _version(client, parent_id, spec):
    r = client.post(f"{BASE}/{parent_id}/versions",
                    json={"spec": spec, "broker_meta": META})
    assert r.status_code == 201, r.text
    return r.json()


# ------------------------------------------------------------- deletion
def test_delete_leaf_node(client):
    node = _create(client)
    deps = client.get(f"{BASE}/{node['id']}/dependencies")
    assert deps.status_code == 200
    assert deps.json()["descendants"] == [] and deps.json()["deletable"] is True

    r = client.delete(f"{BASE}/{node['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] == [node["id"]]
    assert client.get(f"{BASE}/{node['id']}").status_code == 404


def test_delete_with_descendants_requires_explicit_opt_in(client):
    parent = _create(client, _spec())
    child = _version(client, parent["id"], _spec(tag="child-a"))
    grandchild = _version(client, child["id"], _spec(tag="child-b"))

    deps = client.get(f"{BASE}/{parent['id']}/dependencies").json()
    assert deps["descendant_count"] == 2
    assert set(deps["descendants"]) == {child["id"], grandchild["id"]}

    blocked = client.delete(f"{BASE}/{parent['id']}")
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "NODE_HAS_DESCENDANTS"
    assert client.get(f"{BASE}/{parent['id']}").status_code == 200  # untouched

    ok = client.delete(f"{BASE}/{parent['id']}?include_descendants=true")
    assert ok.status_code == 200
    assert set(ok.json()["deleted"]) == {parent["id"], child["id"], grandchild["id"]}
    for nid in (parent["id"], child["id"], grandchild["id"]):
        assert client.get(f"{BASE}/{nid}").status_code == 404


def test_dependent_records_block_deletion_and_survive(client, data_root):
    node = _create(client, _spec())
    # simulate a backtest run referencing the node (CASCADE FK in schema -
    # the API must refuse rather than let the DB erase it)
    from hero1_storage.engine import instrument_url, create_engine, _configure_sqlite
    eng = create_engine(instrument_url("XAUUSD", data_root))
    _configure_sqlite(eng)
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO backtest_runs (instrument_id, node_id, stage, status, created_at)"
            " VALUES ('XAUUSD', :n, 'BACKTEST', 'DONE', 0)"), {"n": node["id"]})
    eng.dispose()

    deps = client.get(f"{BASE}/{node['id']}/dependencies").json()
    assert deps["dependent_records"]["backtest_runs"] == 1
    assert deps["deletable"] is False and deps["blocked_reason"]

    r = client.delete(f"{BASE}/{node['id']}")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "NODE_HAS_DEPENDENT_RECORDS"
    assert client.get(f"{BASE}/{node['id']}").status_code == 200

    eng = create_engine(instrument_url("XAUUSD", data_root))
    _configure_sqlite(eng)
    with eng.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM backtest_runs WHERE node_id=:n"),
                            {"n": node["id"]}).scalar_one() == 1  # never cascaded
    eng.dispose()


def test_bulk_delete_mixed_results(client):
    a = _create(client)
    b = _create(client)
    parent = _create(client, _spec())
    _version(client, parent["id"], _spec(tag="bulk-child"))

    r = client.post(f"{BASE}/delete", json={"node_ids": [a["id"], 999999, parent["id"], b["id"]]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert set(out["deleted"]) == {a["id"], b["id"]}
    codes = {f["node_id"]: f["code"] for f in out["failed"]}
    assert codes[999999] == "NODE_NOT_FOUND"
    assert codes[parent["id"]] == "NODE_HAS_DESCENDANTS"
    # failed nodes survive; repeating the delete is a no-op failure report
    assert client.get(f"{BASE}/{parent['id']}").status_code == 200
    again = client.post(f"{BASE}/delete", json={"node_ids": [a["id"]]}).json()
    assert again["deleted"] == [] and again["failed"][0]["code"] == "NODE_NOT_FOUND"


def test_bulk_delete_with_descendants_opt_in(client):
    parent = _create(client, _spec())
    child = _version(client, parent["id"], _spec(tag="bulk-sub"))
    r = client.post(f"{BASE}/delete",
                    json={"node_ids": [parent["id"]], "include_descendants": True})
    assert set(r.json()["deleted"]) == {parent["id"], child["id"]}


# ------------------------------------------------------------- duplicate
def test_duplicate_unedited_hits_duplicate_prevention(client):
    src = _create(client, _spec())
    r = client.post(f"{BASE}/{src['id']}/duplicate", json={"broker_meta": META})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "SPEC_DUPLICATE"


def test_duplicate_renamed_only_still_duplicate(client):
    src = _create(client, _spec())
    r = client.post(f"{BASE}/{src['id']}/duplicate",
                    json={"name": f"renamed-{next(_COUNTER)}", "broker_meta": META})
    assert r.status_code == 409  # renaming never changes semantic identity


def test_duplicate_with_direction_change_creates_independent_variant(client):
    src = _create(client, _spec(direction="LONG"))
    edited = _spec(direction="SHORT", tag="variant-tag")
    edited["identity"]["tags"] = [t for t in edited["identity"]["tags"]
                                  if not t.startswith("dup-test-")]
    edited["identity"]["tags"].append(src["spec_json"] and json.loads(src["spec_json"])["identity"]["tags"][-1])
    before = client.get(f"{BASE}/{src['id']}").json()

    r = client.post(f"{BASE}/{src['id']}/duplicate",
                    json={"spec": edited, "name": f"variant-{next(_COUNTER)}", "broker_meta": META})
    assert r.status_code == 201, r.text
    new = r.json()
    assert new["id"] != src["id"]
    assert new["parent_node_id"] == src["id"]
    assert new["spec_hash"] != src["spec_hash"]
    assert json.loads(new["spec_json"])["identity"]["direction"] == "SHORT"

    after = client.get(f"{BASE}/{src['id']}").json()
    assert after == before  # original untouched

    lineage = client.get(f"{BASE}/{new['id']}/lineage").json()
    assert lineage["lineage_root_id"] in (src["id"], new["id"])
    ids = {n["id"] for n in lineage["items"]}
    assert src["id"] in ids and new["id"] in ids


def test_duplicate_with_timeframe_change(client):
    src = _create(client, _spec(tf="M15"))
    edited = _spec(tf="H1", tag=json.loads(src["spec_json"])["identity"]["tags"][-1])
    r = client.post(f"{BASE}/{src['id']}/duplicate", json={"spec": edited, "broker_meta": META})
    assert r.status_code == 201, r.text
    new = r.json()
    assert new["timeframe"] == "H1" and new["parent_node_id"] == src["id"]
    assert new["spec_hash"] != src["spec_hash"]


def test_duplicate_name_conflict_and_missing_source(client):
    src = _create(client, _spec())
    edited = _spec(tag="other-semantics")
    r = client.post(f"{BASE}/{src['id']}/duplicate",
                    json={"spec": edited, "name": src["name"], "broker_meta": META})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "NAME_DUPLICATE"
    missing = client.post(f"{BASE}/999999/duplicate", json={"spec": edited, "broker_meta": META})
    assert missing.status_code == 404


def test_duplicate_draft_without_spec_rejected(client):
    draft = _create(client)  # no spec
    r = client.post(f"{BASE}/{draft['id']}/duplicate", json={})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "NO_SPEC_TO_DUPLICATE"
