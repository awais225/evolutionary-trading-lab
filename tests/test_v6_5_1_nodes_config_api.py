"""V6.5.1 §3/§5/§8/§11 — per-node configuration API: validation, persistence,
isolation.  The unified Nodes table saves through this endpoint; every edited
setting (risk mode / risk value / max active trades / SL / TP offsets) must be
validated, persisted, readable back, and confined to the node it was saved for.
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


def _node_ids(client, n=2):
    r = client.get("/api/nodes", params={"filter": "all", "limit": 0})
    ids = [int(x["node_id"]) for x in (r.json().get("nodes") or []) if x.get("node_id")]
    if len(ids) < n:
        pytest.skip("not enough nodes in this DATA snapshot")
    return ids[:n]


def test_01_risk_mode_validation_refuses_bad_values_with_clear_codes(client):
    (sid,) = _node_ids(client, 1)
    # start from a clean risk-mode state so "AMOUNT with no amount" is judged
    # against an empty config (the endpoint validates the EFFECTIVE state)
    assert client.post(f"/api/live-testing/nodes/{sid}/config",
                       json={"risk_mode": None, "risk_amount": None,
                             "risk_capital_basis": None}).status_code == 200
    for payload, code in (
        ({"risk_mode": "LOTS"}, "RISK_MODE_INVALID"),
        ({"risk_mode": "AMOUNT"}, "RISK_AMOUNT_INVALID"),
        ({"risk_mode": "AMOUNT", "risk_amount": -3}, "RISK_AMOUNT_INVALID"),
        ({"risk_mode": "AMOUNT", "risk_amount": 0}, "RISK_AMOUNT_INVALID"),
        ({"risk_capital_basis": "TELEPHONE"}, "RISK_CAPITAL_BASIS_INVALID"),
        ({"risk_pct": -1}, "RISK_PCT_INVALID"),
        ({"risk_pct": 999}, "RISK_PCT_ABOVE_MAXIMUM"),
        ({"max_active_trades": 0}, "INVALID_MAX_ACTIVE_TRADES"),
        ({"max_active_trades": 2.5}, "INVALID_MAX_ACTIVE_TRADES"),
        ({"sl_offset_pips": "xx"}, "INVALID_OFFSET"),
    ):
        r = client.post(f"/api/live-testing/nodes/{sid}/config", json=payload)
        assert r.status_code == 422, (payload, r.status_code)
        assert r.json()["detail"].get("code") == code, (payload, r.json())


def test_02_risk_mode_accepts_and_persists_valid_values(client):
    (sid,) = _node_ids(client, 1)
    r = client.post(f"/api/live-testing/nodes/{sid}/config",
                    json={"risk_mode": "AMOUNT", "risk_amount": 12.5})
    assert r.status_code == 200, r.text
    cfg = r.json()["config"]
    assert cfg["risk_mode"] == "AMOUNT" and cfg["risk_amount"] == 12.5
    r = client.get("/api/nodes", params={"filter": "all", "limit": 0})
    row = [x for x in r.json()["nodes"] if int(x["node_id"]) == sid][0]
    assert row["risk_mode"]["mode"] == "AMOUNT"
    assert row["risk_mode"]["risk_amount"] == 12.5
    # switch back to Mode A with an explicit capital basis
    r = client.post(f"/api/live-testing/nodes/{sid}/config",
                    json={"risk_mode": "PERCENT", "risk_pct": 0.75,
                          "risk_capital_basis": "BALANCE"})
    assert r.status_code == 200
    cfg = r.json()["config"]
    assert cfg["risk_mode"] == "PERCENT" and cfg["risk_pct"] == 0.75
    assert cfg["risk_capital_basis"] == "BALANCE"


def test_03_mode_switch_to_amount_reuses_the_stored_amount(client):
    (sid,) = _node_ids(client, 1)
    assert client.post(f"/api/live-testing/nodes/{sid}/config",
                       json={"risk_amount": 30}).status_code == 200
    # switching the mode alone must work because the stored amount is validated
    r = client.post(f"/api/live-testing/nodes/{sid}/config", json={"risk_mode": "AMOUNT"})
    assert r.status_code == 200, r.text
    assert r.json()["config"]["risk_mode"] == "AMOUNT"
    # ... but explicitly clearing the amount while in AMOUNT mode is refused
    r = client.post(f"/api/live-testing/nodes/{sid}/config",
                    json={"risk_mode": "AMOUNT", "risk_amount": None})
    assert r.status_code == 422


def test_04_one_nodes_settings_never_change_another_node(client):
    sid_a, sid_b = _node_ids(client, 2)
    assert client.post(f"/api/live-testing/nodes/{sid_a}/config",
                       json={"risk_mode": "AMOUNT", "risk_amount": 5,
                             "max_active_trades": 3,
                             "sl_offset_pips": 2, "tp_offset_pips": -1}).status_code == 200
    assert client.post(f"/api/live-testing/nodes/{sid_b}/config",
                       json={"risk_pct": 0.25}).status_code == 200
    r = client.get("/api/nodes", params={"filter": "all", "limit": 0})
    rows = {int(x["node_id"]): x for x in r.json()["nodes"]}
    a, b = rows[sid_a], rows[sid_b]
    assert a["risk_mode"]["mode"] == "AMOUNT" and a["risk_mode"]["risk_amount"] == 5
    assert a["max_active_trades"] == 3
    assert a["sl_offset_pips"] == 2 and a["tp_offset_pips"] == -1
    # node B must not have received any of node A's settings
    assert b["risk_mode"]["mode"] == "PERCENT"
    assert b["risk_mode"]["risk_amount"] != 5
    assert b["max_active_trades"] != 3
    assert b["sl_offset_pips"] != 2 and b["tp_offset_pips"] != -1


def test_05_offsets_reach_the_row_payload_and_survive_reconfig(client):
    (sid,) = _node_ids(client, 1)
    assert client.post(f"/api/live-testing/nodes/{sid}/config",
                       json={"sl_offset_pips": 5, "tp_offset_pips": -2}).status_code == 200
    r = client.get("/api/nodes", params={"filter": "all", "limit": 0})
    row = [x for x in r.json()["nodes"] if int(x["node_id"]) == sid][0]
    assert row["sl_offset_pips"] == 5 and row["tp_offset_pips"] == -2
    assert row["offsets_active"] is True
    # saving an unrelated field must not disturb the offsets (merged config)
    assert client.post(f"/api/live-testing/nodes/{sid}/config",
                       json={"risk_pct": 0.5}).status_code == 200
    r = client.get("/api/nodes", params={"filter": "all", "limit": 0})
    row = [x for x in r.json()["nodes"] if int(x["node_id"]) == sid][0]
    assert row["sl_offset_pips"] == 5 and row["tp_offset_pips"] == -2
