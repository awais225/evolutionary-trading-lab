"""V6.5 §4/§5/§6 — API contracts: limits validation, per-node isolation,
active-trades/sync surfaces, trade filters.  Uses the real app via TestClient
against the scratch DATA root; no terminal, no real orders.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_config_disk_writes(monkeypatch):
    """Settings POSTs must never write the repo's tracked CONFIG/ files."""
    import app.api.routes as _routes

    def _fake_update_config(section, updates):
        cfg = _routes.get_config()
        obj = getattr(cfg, section)
        for k, v in updates.items():
            setattr(obj, k, v)
        return cfg

    monkeypatch.setattr(_routes, "update_config", _fake_update_config)


@pytest.fixture(scope="module")
def any_node(client):
    r = client.get("/api/live-testing/nodes-table", params={"limit": 5})
    rows = r.json().get("nodes") or []
    if not rows:
        pytest.skip("no live-testable nodes in this DATA snapshot")
    return rows[0]["node_id"]


def test_01_settings_reject_invalid_limits(client):
    for bad in (0, -1, 101, "abc", 1.5):
        r = client.post("/api/live-testing/settings",
                        json={"max_active_trades": bad})
        assert r.status_code == 422, bad
        r = client.post("/api/live-testing/settings",
                        json={"max_active_trades_per_node_default": bad})
        assert r.status_code == 422, bad


def test_02_settings_accept_valid_limits_and_preserve_others(client):
    r = client.post("/api/live-testing/settings",
                    json={"max_active_trades": 10,
                          "max_active_trades_per_node_default": 1})
    assert r.status_code == 200
    body = r.json()["live_testing"]
    assert body["max_active_trades"] == 10
    assert body["max_active_trades_per_node_default"] == 1
    # existing settings untouched
    assert "risk_pct_default" in body and "max_data_age_s" in body


def test_03_node_limit_override_is_validated_and_isolated(client, any_node):
    r = client.post(f"/api/live-testing/nodes/{any_node}/config",
                    json={"max_active_trades": 0})
    assert r.status_code == 422
    r = client.post(f"/api/live-testing/nodes/{any_node}/config",
                    json={"max_active_trades": 2})
    assert r.status_code == 200
    cfg = r.json()["config"]
    assert cfg["max_positions"] == 2
    # Default (null) resets to inherit
    r = client.post(f"/api/live-testing/nodes/{any_node}/config",
                    json={"max_active_trades": None})
    assert r.status_code == 200
    assert r.json()["config"]["max_positions"] is None


def test_04_node_offsets_are_validated(client, any_node):
    r = client.post(f"/api/live-testing/nodes/{any_node}/config",
                    json={"sl_offset_pips": "xx", "tp_offset_pips": 0})
    assert r.status_code == 422
    r = client.post(f"/api/live-testing/nodes/{any_node}/config",
                    json={"sl_offset_pips": 5, "tp_offset_pips": -5})
    assert r.status_code == 200
    cfg = r.json()["config"]
    assert cfg["sl_offset_pips"] == 5 and cfg["tp_offset_pips"] == -5


def test_05_nodes_table_reports_effective_limits_and_offsets(client, any_node):
    client.post(f"/api/live-testing/nodes/{any_node}/config",
                json={"max_active_trades": 2, "sl_offset_pips": 3, "tp_offset_pips": 0})
    r = client.get("/api/live-testing/nodes-table", params={"limit": 200})
    row = next((n for n in r.json()["nodes"] if n["node_id"] == any_node), None)
    assert row is not None
    assert row["max_active_trades_source"] == "CUSTOM"
    assert row["max_active_trades_effective"] == 2
    assert row["sl_offset_pips"] == 3 and row["offsets_active"] is True
    client.post(f"/api/live-testing/nodes/{any_node}/config", json={"max_active_trades": None})
    r = client.get("/api/live-testing/nodes-table", params={"limit": 200})
    row = next(n for n in r.json()["nodes"] if n["node_id"] == any_node)
    assert row["max_active_trades_source"] == "DEFAULT"
    assert row["max_active_trades_effective"] == 1        # the inherited default


def test_06_active_trades_and_sync_surfaces(client):
    r = client.get("/api/live-testing/active-trades")
    body = r.json()
    assert r.status_code == 200 and body["ok"] is True
    assert isinstance(body["active_trades"], list)
    for t in body["active_trades"]:
        assert {"ticket", "node_id", "side", "floating_pnl", "sl", "tp"} <= set(t)
        assert "estimate_note" in t                     # estimates always labelled
    r = client.get("/api/live-testing/sync")
    body = r.json()
    assert r.status_code == 200 and body["ok"] is True
    assert body["certainty"] in ("NEVER_SYNCED", "SUFFICIENT", "INSUFFICIENT")
    assert "reservations" in body and "activity" in body


def test_07_trades_endpoint_filters_and_detail(client, any_node):
    r = client.get("/api/live-testing/trades",
                   params={"node_id": any_node, "symbol": "XAUUSD", "side": "BUY",
                           "date_from": 0, "date_to": 2 ** 40})
    assert r.status_code == 200
    body = r.json()
    assert "counting_conventions" in body
    assert all(int(t["strategy_id"]) == any_node for t in body["trades"] if t.get("strategy_id"))
    if body["trades"]:
        rid = body["trades"][0]["id"]
        d = client.get(f"/api/live-testing/trades/{rid}").json()
        assert d["ok"] is True and "trade" in d and "events" in d


def test_08_reconcile_reports_sync_state(client):
    r = client.post("/api/live-testing/reconcile")
    body = r.json()
    assert r.status_code == 200 and body["ok"] is True
    assert "sync" in body and "reconcile" in body
