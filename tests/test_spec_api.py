"""API integration for the Phase-2 spec pipeline (synthetic DATA only)."""
from __future__ import annotations

import itertools
import json

from hero1_strategy_spec import bullish_engulfing

_COUNTER = itertools.count(1)

XAU_META = {"XAUUSD": {"pip_size": 0.1, "point_size": 0.01, "tick_size": 0.01, "tick_value": 1.0}}
EUR_META = {"EURUSD": {"pip_size": 0.0001, "point_size": 0.00001, "tick_size": 0.00001, "tick_value": 1.0}}


def _fresh_spec(tf="M15", name="Bullish Engulfing (example)"):
    spec = bullish_engulfing("XAUUSD", tf)
    spec = json.loads(json.dumps(spec))
    # Fingerprints exclude DISPLAY identity since compiler 1.1.0 — mint a unique
    # SEMANTIC difference (tag) per test instead of relying on the name.
    spec["identity"]["name"] = name
    spec["identity"]["tags"] = [*spec["identity"].get("tags", []), f"test-{next(_COUNTER)}"]
    return spec


def _create_spec_node(client, spec, meta=None, name="engulfing-test"):
    spec = json.loads(json.dumps(spec))
    counter = next(_COUNTER)
    spec["identity"]["name"] = f"{spec['identity']['name']} — {name} #{counter}"
    spec["identity"]["tags"] = [*spec["identity"].get("tags", []), f"unique-{counter}-{name}"]
    # names are unique per instrument since uq_nodes_instr_name — mint a unique name too
    body = {"name": f"{name} #{next(_COUNTER)}", "origin": "CUSTOM",
            "spec_json": json.dumps(spec)}
    if meta is not None:
        body["broker_meta"] = meta
    return client.post("/api/v1/instruments/XAUUSD/nodes", json=body)


def test_validate_endpoint_without_persistence(client):
    spec = bullish_engulfing("XAUUSD", "M15")
    r = client.post("/api/v1/instruments/XAUUSD/nodes/validate",
                    json={"spec": spec, "broker_meta": XAU_META})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "VALID" and body["issues"] == []
    assert body["instrument"] == "XAUUSD"
    # nothing persisted:
    listing = client.get("/api/v1/instruments/XAUUSD/nodes").json()
    assert all(n["name"] != "engulfing-test" for n in listing["items"])


def test_validate_endpoint_reports_needs_metadata(client):
    spec = bullish_engulfing("XAUUSD", "M15")
    r = client.post("/api/v1/instruments/XAUUSD/nodes/validate", json={"spec": spec})
    assert r.status_code == 200
    assert r.json()["status"] == "NEEDS_METADATA"
    assert any(i["code"] == "RISK_METADATA_REQUIRED" for i in r.json()["issues"])


def test_compile_endpoint_valid(client):
    spec = bullish_engulfing("XAUUSD", "M15")
    r = client.post("/api/v1/instruments/XAUUSD/nodes/compile",
                    json={"spec": spec, "broker_meta": XAU_META})
    assert r.status_code == 200
    body = r.json()
    assert len(body["fingerprint"]) == 64
    assert body["schema_version"] == "1.0.0" and body["compiler_version"] == "1.1.0"
    assert body["ir"]["strategy"]["instrument"] == "XAUUSD"
    assert body["ir"]["execution"]["entry_timing"] == "next_bar_open"


def test_compile_endpoint_rejects_invalid(client):
    spec = bullish_engulfing("XAUUSD", "M15")
    spec["identity"]["timeframe"] = "M3"
    r = client.post("/api/v1/instruments/XAUUSD/nodes/compile",
                    json={"spec": spec, "broker_meta": XAU_META})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["code"] == "SPEC_INVALID"
    assert any(i["code"] == "TIMEFRAME_INVALID" for i in detail["issues"])


def test_create_node_with_spec_server_hashes(client):
    spec = bullish_engulfing("XAUUSD", "M15")
    r = _create_spec_node(client, spec, XAU_META)
    assert r.status_code == 201, r.text
    node = r.json()
    assert node["spec_hash"] and len(node["spec_hash"]) == 64
    assert node["spec_json"]
    stored = json.loads(node["spec_json"])
    assert stored["identity"]["instrument"] == "XAUUSD"
    # canonical form is stable:
    assert stored == json.loads(json.dumps(json.loads(node["spec_json"])))


def test_create_node_with_spec_needs_metadata_rejected(client):
    spec = bullish_engulfing("XAUUSD", "M15")
    r = _create_spec_node(client, spec)          # no meta → NEEDS_METADATA
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "SPEC_NEEDS_METADATA"


def test_client_supplied_hash_is_never_authoritative(client):
    spec = bullish_engulfing("XAUUSD", "M15")
    spec = json.loads(json.dumps(spec)); spec["identity"]["tags"] = [*spec["identity"].get("tags", []), f"hash-test-{next(_COUNTER)}"]
    body = {"name": f"hash-test #{next(_COUNTER)}", "origin": "CUSTOM",
            "spec_json": json.dumps(spec),
            "spec_hash": "f" * 64,                # client claim — must be ignored
            "broker_meta": XAU_META}
    r = client.post("/api/v1/instruments/XAUUSD/nodes", json=body)
    assert r.status_code == 201
    assert r.json()["spec_hash"] != "f" * 64


def test_immutable_versions_and_lineage(client):
    spec_v1 = bullish_engulfing("XAUUSD", "M15")
    r = _create_spec_node(client, _fresh_spec("M15", "v-lineage"), XAU_META, name="versioned")
    assert r.status_code == 201
    v1 = r.json()

    got_v1 = r.json()
    spec_v2 = json.loads(got_v1["spec_json"])
    spec_v2["exits"]["stop_loss"]["distance"]["value"] = 12    # semantic change
    r2 = client.post(f"/api/v1/instruments/XAUUSD/nodes/{v1['id']}/versions",
                     json={"spec": spec_v2, "broker_meta": XAU_META, "created_by": "tester"})
    assert r2.status_code == 201, r2.text
    v2 = r2.json()
    assert v2["version"] == v1["version"] + 1 == 2
    assert v2["parent_node_id"] == v1["id"]
    assert v2["spec_hash"] != v1["spec_hash"]

    # parent untouched — immutable history
    got = client.get(f"/api/v1/instruments/XAUUSD/nodes/{v1['id']}").json()
    assert got["spec_json"] == v1["spec_json"]
    assert got["spec_hash"] == v1["spec_hash"]
    assert got["version"] == 1

    lineage = client.get(f"/api/v1/instruments/XAUUSD/nodes/{v1['id']}/lineage").json()
    assert [n["version"] for n in lineage["items"]] == [1, 2]
    assert lineage["lineage_root_id"] == v1["id"]

    # third version from v2 increments within the same lineage
    spec_v3 = json.loads(json.dumps(spec_v2))
    spec_v3["risk"]["sizing"]["risk_pct"] = 0.75
    r3 = client.post(f"/api/v1/instruments/XAUUSD/nodes/{v2['id']}/versions",
                     json={"spec": spec_v3, "broker_meta": XAU_META})
    assert r3.status_code == 201
    assert r3.json()["version"] == 3
    assert r3.json()["parent_node_id"] == v2["id"]


def test_spec_json_patch_rejected_with_guidance(client):
    spec = bullish_engulfing("XAUUSD", "M15")
    r = _create_spec_node(client, spec, XAU_META, name="immutable-me")
    node = r.json()
    r2 = client.patch(f"/api/v1/instruments/XAUUSD/nodes/{node['id']}",
                      json={"spec_json": json.dumps(spec)})
    assert r2.status_code == 422   # extra=forbid; spec changes must use /versions


def test_cross_instrument_version_rejected(client):
    spec = bullish_engulfing("XAUUSD", "M15")
    r = _create_spec_node(client, spec, XAU_META, name="xau-only")
    node = r.json()
    eur_spec = bullish_engulfing("EURUSD", "M15")
    r2 = client.post(f"/api/v1/instruments/EURUSD/nodes/{node['id']}/versions",
                     json={"spec": eur_spec, "broker_meta": EUR_META})
    assert r2.status_code == 404    # XAUUSD node id is not visible inside EURUSD


def test_create_child_with_invalid_parent_rejected(client):
    spec = bullish_engulfing("XAUUSD", "M15")
    body = {"name": "orphan", "origin": "CUSTOM", "parent_node_id": 999999}
    r = client.post("/api/v1/instruments/XAUUSD/nodes", json=body)
    assert r.status_code == 422


def test_get_node_returns_full_spec(client):
    spec = bullish_engulfing("XAUUSD", "M15")
    r = _create_spec_node(client, spec, XAU_META, name="read-back")
    got = client.get(f"/api/v1/instruments/XAUUSD/nodes/{r.json()['id']}").json()
    assert got["spec_json"] is not None
    parsed = json.loads(got["spec_json"])
    assert parsed["schema_version"] == "1.0.0"
    assert parsed["entry"]["type"] == "and"


def test_existing_crud_still_works(client):
    r = client.post("/api/v1/instruments/XAUUSD/nodes",
                    json={"name": "plain-draft", "origin": "WWW"})
    assert r.status_code == 201
    assert r.json()["spec_json"] is None and r.json()["version"] == 1


def test_duplicate_identical_spec_at_same_version_is_409(client):
    spec = _fresh_spec("M15", "dup-check")
    r1 = _create_spec_node(client, spec, XAU_META, name="dup-one")
    assert r1.status_code == 201, r1.text
    # identical (instrument, spec_hash, version=1) — DB constraint mapped to a clean 409, not a 500
    body = {"name": "dup-two", "origin": "CUSTOM",
            "spec_json": json.dumps(json.loads(r1.json()["spec_json"])),
            "broker_meta": XAU_META}
    r2 = client.post("/api/v1/instruments/XAUUSD/nodes", json=body)
    assert r2.status_code == 409
    assert r2.json()["detail"]["code"] == "SPEC_DUPLICATE"
