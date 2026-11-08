"""Phase 3 API: DeepTesting end-to-end on a seeded canonical store."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/api"))

from hero1_market_data import store as md  # noqa: E402
from app import market_data_service as svc  # noqa: E402
from test_phase3_engine import CLOSES, candles_from_closes, cross_spec  # noqa: E402


@pytest.fixture()
def seeded_store(tmp_path, monkeypatch):
    db = tmp_path / "market_data.db"
    conn = md.connect_store(db)
    rows = candles_from_closes(CLOSES)
    with conn:
        md.merge_candles(conn, dataset_id="test", instrument="XAUUSD", timeframe="H1",
                         rows=[{**r, "ts_iso": md.ts_iso_of(r["ts"]), "tick_volume": 100, "spread": 0} for r in rows],
                         policy="import")
    conn.close()
    monkeypatch.setattr(svc, "store_db_path", lambda: db)
    return rows


def test_deeptesting_run_end_to_end(client, seeded_store):
    spec = cross_spec()
    spec["identity"]["name"] = "DT acceptance node"
    spec["identity"]["tags"] = ["dt-acceptance-1"]
    created = client.post("/api/v1/instruments/XAUUSD/nodes", json={
        "name": "DT acceptance node", "origin": "CUSTOM", "spec_json": json.dumps(spec)})
    assert created.status_code in (200, 201), created.text
    node_id = created.json()["id"]

    start, end = seeded_store[0]["ts"], seeded_store[-1]["ts"]
    r = client.post("/api/v1/instruments/XAUUSD/deeptesting/runs", json={
        "node_ids": [node_id],
        "config": {"mode": "initial", "range": {"start": start, "end": end},
                   "initial_capital": 10000, "risk_mode": "percent", "risk_value": 1.0,
                   "risk_basis": "initial_capital", "max_active_trades": 1,
                   "tick_size": 0.1, "tick_value_account": 1.0,
                   "volume_min": 0.01, "volume_max": 100, "volume_step": 0.01}})
    assert r.status_code == 200, r.text
    run = r.json()["runs"][0]
    assert run["status"] == "DONE", run
    assert run["fingerprint"] and run["engine_version"] == "3.0.0"
    assert run["metrics"]["total_trades"] == 1
    assert run["metrics"]["net_profit"] == 200.0

    detail = client.get(f"/api/v1/instruments/XAUUSD/deeptesting/runs/{run['id']}").json()
    assert detail["data_quality"]["readiness"] in ("READY", "READY_WITH_WARNINGS")
    assert detail["config"]["initial_capital"] == 10000
    assert detail["assumptions"]["max_active_trades"] == 1

    trades = client.get(f"/api/v1/instruments/XAUUSD/deeptesting/runs/{run['id']}/trades").json()
    assert trades["count"] == 1
    t = trades["trades"][0]
    assert t["entry_price"] == 94.0 and t["volume"] == 5.0 and t["planned_risk"] == 100.0
    assert t["exit_reason"] == "TAKE_PROFIT"

    # rerun creates a NEW record with identical metrics (reproducibility)
    again = client.post(f"/api/v1/instruments/XAUUSD/deeptesting/runs/{run['id']}/rerun").json()
    assert again["id"] != run["id"] and again["metrics"]["net_profit"] == 200.0


def test_blocked_data_never_tests(client, seeded_store):
    spec = cross_spec()
    spec["identity"]["name"] = "DT blocked node"
    spec["identity"]["tags"] = ["dt-blocked-1"]
    created = client.post("/api/v1/instruments/XAUUSD/nodes", json={
        "name": "DT blocked node", "origin": "CUSTOM", "spec_json": json.dumps(spec)})
    node_id = created.json()["id"]
    # range far outside stored data -> coverage BLOCKED -> FAILED, never tested
    r = client.post("/api/v1/instruments/XAUUSD/deeptesting/runs", json={
        "node_ids": [node_id], "config": {"mode": "initial",
         "range": {"start": 946684800, "end": 946771200},
         "initial_capital": 10000, "tick_size": 0.1, "tick_value_account": 1.0}})
    assert r.status_code == 200
    run = r.json()["runs"][0]
    assert run["status"] == "FAILED" and "DATA_BLOCKED" in (run["error"] or "")
    trades = client.get(f"/api/v1/instruments/XAUUSD/deeptesting/runs/{run['id']}/trades").json()
    assert trades["count"] == 0


def test_scenario_lifecycle_versions(client):
    r = client.post("/api/v1/instruments/XAUUSD/deeptesting/scenarios", json={
        "name": "London only", "folder": "sessions", "config": {"sessions": ["london"]}})
    assert r.status_code == 200
    sid = r.json()["id"]
    edited = client.put(f"/api/v1/instruments/XAUUSD/deeptesting/scenarios/{sid}", json={
        "name": "London only v2", "config": {"sessions": ["london", "newyork"]}})
    assert edited.json()["version"] == 2 and edited.json()["parent_scenario_id"] == sid
    listing = client.get("/api/v1/instruments/XAUUSD/deeptesting/scenarios").json()
    assert len(listing["scenarios"]) == 2  # edit never overwrites history

    gen = client.post("/api/v1/instruments/XAUUSD/deeptesting/scenarios/generate", json={
        "base_config": {"mode": "initial"},
        "dimensions": {"tp_offset_pct": [-10, 0, 10], "sl_offset_pct": [0, 5]},
        "max_count": 10})
    assert gen.json()["count"] == 6
    boom = client.post("/api/v1/instruments/XAUUSD/deeptesting/scenarios/generate", json={
        "base_config": {}, "dimensions": {"tp_offset_pct": list(range(50)),
                                          "sl_offset_pct": list(range(50))},
        "max_count": 100})
    assert boom.status_code == 422 and "SCENARIO_EXPLOSION" in boom.text
