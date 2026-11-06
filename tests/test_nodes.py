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


def test_starred_toggle_persists_and_filters(client):
    a = client.post("/api/v1/instruments/XAUUSD/nodes",
                    json={"name": "star-persist-a", "origin": "CUSTOM"}).json()
    b = client.post("/api/v1/instruments/XAUUSD/nodes",
                    json={"name": "star-persist-b", "origin": "CUSTOM"}).json()

    # star A only
    r = client.patch(f"/api/v1/instruments/XAUUSD/nodes/{a['id']}", json={"starred": True})
    assert r.status_code == 200 and r.json()["starred"] is True

    starred = client.get("/api/v1/instruments/XAUUSD/nodes?starred=true").json()
    ids = {n["id"] for n in starred["items"]}
    assert a["id"] in ids and b["id"] not in ids
    assert all(n["starred"] for n in starred["items"])
    assert starred["starred_total"] >= 1
    assert starred["filtered_total"] == starred["starred_total"]
    assert starred["total"] >= starred["starred_total"]

    everything = client.get("/api/v1/instruments/XAUUSD/nodes").json()
    assert everything["starred_total"] >= 1
    assert everything["filtered_total"] == everything["total"]

    # starring is research metadata: semantics and identity must not move
    assert r.json()["spec_hash"] == a["spec_hash"]
    assert r.json()["version"] == a["version"] == 1
    assert r.json()["created_by"] == a["created_by"]
    assert r.json()["created_at"] == a["created_at"]

    # unstar again — same row, same semantics
    r2 = client.patch(f"/api/v1/instruments/XAUUSD/nodes/{a['id']}", json={"starred": False})
    assert r2.status_code == 200 and r2.json()["starred"] is False
    starred2 = client.get("/api/v1/instruments/XAUUSD/nodes?starred=true").json()
    assert a["id"] not in {n["id"] for n in starred2["items"]}


def test_starred_filter_scoping_and_counts(client):
    made = [client.post("/api/v1/instruments/EURUSD/nodes",
                        json={"name": f"scope-{i}", "origin": "CUSTOM"}).json()
            for i in range(3)]
    for n in made[:2]:
        client.patch(f"/api/v1/instruments/EURUSD/nodes/{n['id']}", json={"starred": True})

    res = client.get("/api/v1/instruments/EURUSD/nodes?starred=true&limit=1").json()
    assert len(res["items"]) == 1            # limit applies to the filtered set
    assert res["filtered_total"] == 2        # NOT capped by limit
    assert res["starred_total"] == 2
    names = {n["name"] for n in
             client.get("/api/v1/instruments/EURUSD/nodes?starred=true").json()["items"]}
    assert names == {"scope-0", "scope-1"}

    res_unstarred = client.get("/api/v1/instruments/EURUSD/nodes?starred=false").json()
    assert all(n["starred"] is False for n in res_unstarred["items"])
