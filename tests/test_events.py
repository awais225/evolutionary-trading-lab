def test_event_create_list_and_replay(client):
    created = client.post("/api/v1/events", json={
        "category": "NODE", "severity": "INFO", "instrument": "XAUUSD",
        "subsystem": "tests", "message": "synthetic test event",
    })
    assert created.status_code == 201
    eid = created.json()["id"]

    listed = client.get("/api/v1/events", params={"instrument": "XAUUSD", "category": "NODE", "limit": 10})
    assert listed.status_code == 200
    ids = [e["id"] for e in listed.json()["events"]]
    assert eid in ids

    replay = client.get("/api/v1/events", params={"since_id": eid - 1, "limit": 5})
    assert any(e["id"] == eid for e in replay.json()["events"])


def test_event_filter_validation(client):
    assert client.get("/api/v1/events", params={"category": "BOGUS"}).status_code == 422
    assert client.get("/api/v1/events", params={"severity": "BOGUS"}).status_code == 422
    assert client.get("/api/v1/events", params={"instrument": "bad!sym"}).status_code == 422


def test_event_categories_endpoint(client):
    r = client.get("/api/v1/events/categories")
    assert r.status_code == 200
    assert set(r.json()["categories"]) >= {"SYSTEM", "RESEARCH", "NODE", "MT5", "LIVE_TRADING"}


def test_websocket_replay_and_live_delivery(client):
    with client.websocket_connect("/ws/v1/events?since_id=0&limit=1") as ws:
        replay = ws.receive_json()
        assert replay["type"] == "replay"
        client.post("/api/v1/events", json={"category": "SYSTEM", "subsystem": "ws-test",
                                            "message": "ws live delivery probe"})
        msg = ws.receive_json()
        assert msg["type"] == "event"
        assert msg["event"]["message"] == "ws live delivery probe"
