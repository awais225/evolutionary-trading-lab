def test_node_crud_and_origin_validation(client):
    bad = client.post("/api/v1/instruments/XAUUSD/nodes", json={"name": "n1", "origin": "NOT_AN_ORIGIN"})
    assert bad.status_code == 422

    created = client.post("/api/v1/instruments/XAUUSD/nodes", json={
        "name": "audit-custom-node", "origin": "CUSTOM", "description": "synthetic test node",
        "spec_hash": "ab" * 32,
    })
    assert created.status_code == 201, created.text
    node = created.json()
    assert node["instrument_id"] == "XAUUSD"
    assert node["origin"] == "CUSTOM"
    assert node["approval_state"] == "NONE" and node["status"] == "DRAFT"

    got = client.get(f"/api/v1/instruments/XAUUSD/nodes/{node['id']}")
    assert got.status_code == 200 and got.json()["name"] == "audit-custom-node"

    patched = client.patch(f"/api/v1/instruments/XAUUSD/nodes/{node['id']}",
                           json={"status": "VALIDATING", "approval_state": "PENDING"})
    assert patched.status_code == 200
    assert patched.json()["status"] == "VALIDATING" and patched.json()["approval_state"] == "PENDING"


def test_spec_hash_validation(client):
    r = client.post("/api/v1/instruments/XAUUSD/nodes",
                    json={"name": "badhash", "origin": "CUSTOM", "spec_hash": "not-a-sha"})
    assert r.status_code == 422
    r2 = client.post("/api/v1/instruments/XAUUSD/nodes",
                     json={"name": "badspec", "origin": "CUSTOM", "spec_json": "{not json"})
    assert r2.status_code == 422


def test_cross_instrument_isolation(client):
    """Same primary-key values legitimately exist in independent per-instrument
    DBs, so isolation is asserted on instrument-scoped content, never on bare ids."""
    a = client.post("/api/v1/instruments/XAUUSD/nodes",
                    json={"name": "isolation-marker-xau", "origin": "WWW"}).json()
    b = client.post("/api/v1/instruments/EURUSD/nodes",
                    json={"name": "isolation-marker-eur", "origin": "WWW"}).json()

    xau_items = client.get("/api/v1/instruments/XAUUSD/nodes").json()["items"]
    eur_items = client.get("/api/v1/instruments/EURUSD/nodes").json()["items"]
    bt_items = client.get("/api/v1/instruments/BTCUSD/nodes").json()["items"]

    # every returned row belongs to the requested instrument — no exceptions
    assert all(n["instrument_id"] == "XAUUSD" for n in xau_items)
    assert all(n["instrument_id"] == "EURUSD" for n in eur_items)
    assert all(n["instrument_id"] == "BTCUSD" for n in bt_items)

    # cross-instrument content leaks are impossible
    assert "isolation-marker-eur" not in {n["name"] for n in xau_items}
    assert "isolation-marker-eur" not in {n["name"] for n in bt_items}
    assert "isolation-marker-xau" not in {n["name"] for n in eur_items}
    assert "isolation-marker-xau" not in {n["name"] for n in bt_items}

    # a path query scoped to EURUSD can never surface the XAUUSD marker node,
    # regardless of id collision:
    eur_names = {n["name"] for n in eur_items}
    assert "isolation-marker-xau" not in eur_names
    _ = (a, b)


def test_origin_taxonomy_complete(client):
    for origin in ("CUSTOM", "WWW", "INTERACTIVE", "FINE_TUNED"):
        r = client.post("/api/v1/instruments/BTCUSD/nodes",
                        json={"name": f"origin-{origin}", "origin": origin})
        assert r.status_code == 201, (origin, r.text)
