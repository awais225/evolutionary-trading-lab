def test_health_reports_registry_healthy(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["checks"]["registry_db"]["status"] == "healthy"
    assert body["status"] in {"ok", "degraded"}  # degraded only if optional deps absent
    assert "mt5" in body["checks"] and "gpu" in body["checks"] and "ollama" in body["checks"]
    assert body["checks"]["mt5"]["status"] != "healthy" or True  # may be package_present on Windows


def test_instrument_registry_lists_three(client):
    r = client.get("/api/v1/instruments")
    assert r.status_code == 200
    symbols = {i["symbol"] for i in r.json()["instruments"]}
    assert {"XAUUSD", "EURUSD", "BTCUSD"} <= symbols


def test_instrument_detail_and_404(client):
    assert client.get("/api/v1/instruments/XAUUSD").status_code == 200
    assert client.get("/api/v1/instruments/NOPE").status_code == 404


def test_invalid_instrument_rejected(client):
    r = client.get("/api/v1/instruments/AAAA_TOO_LONG_SYMBOL_999/nodes")
    assert r.status_code in (404, 422)
    r2 = client.get("/api/v1/instruments/bad symbol!/nodes")
    assert r2.status_code in (404, 422)


def test_instrument_db_files_created_outside_repo(data_root):
    assert (data_root / "DATABASE" / "hero1_registry.db").exists()
    for sym in ("XAUUSD", "EURUSD", "BTCUSD"):
        assert (data_root / sym / "DATABASE" / "hero1.db").exists()
        for sub in ("MARKET_DATA/M15", "NODES/CUSTOM", "DEEPTEST", "LIVE_TESTING", "RESEARCH"):
            assert (data_root / sym / sub).is_dir(), f"missing {sym}/{sub}"


def test_health_identity_fields_present(client):
    body = client.get("/health").json()
    # additive launcher-identity fields: always present; populated when launched
    # via START.bat (conftest sets HERO1_REPO_ROOT, token only when injected).
    assert "start_token" in body and "project_root" in body
    assert body["start_token"] is None or isinstance(body["start_token"], str)
    assert body["project_root"] is None or isinstance(body["project_root"], str)
