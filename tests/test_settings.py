def test_settings_roundtrip(client):
    r = client.get("/api/v1/settings")
    assert r.status_code == 200
    default = r.json()["settings"]["research_max_sources"]

    r2 = client.put("/api/v1/settings", json={"settings": {"research_max_sources": 55}})
    assert r2.status_code == 200
    assert r2.json()["settings"]["research_max_sources"] == 55

    r3 = client.get("/api/v1/settings")
    assert r3.json()["settings"]["research_max_sources"] == 55

    client.put("/api/v1/settings", json={"settings": {"research_max_sources": default}})


def test_settings_validation(client):
    assert client.put("/api/v1/settings", json={"settings": {"unknown_key": 1}}).status_code == 422
    assert client.put("/api/v1/settings", json={"settings": {"log_level": "CHATTY"}}).status_code == 422
    assert client.put("/api/v1/settings", json={"settings": {"research_max_sources": 0}}).status_code == 422
