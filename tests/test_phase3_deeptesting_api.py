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
                   "spread_mode": "no_spread",
                   "tick_size": 0.1, "tick_value_account": 1.0,
                   "volume_min": 0.01, "volume_max": 100, "volume_step": 0.01}})
    assert r.status_code == 200, r.text
    run = r.json()["runs"][0]
    assert run["status"] == "DONE", run
    assert run["fingerprint"] and run["engine_version"] == "3.1.0"
    assert run["config"]["spread_mode"] == "no_spread"
    detail = client.get(f"/api/v1/instruments/XAUUSD/deeptesting/runs/{run['id']}").json()
    assert detail["assumptions"]["spread_mode"] == "no_spread"
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
    own_history = [item for item in listing["scenarios"] if item["id"] == sid or item.get("parent_scenario_id") == sid]
    assert len(own_history) == 2  # this scenario's edit creates a new history row

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


def test_offset_precedence_pips_individual_beats_global_never_stacked():
    from app.deeptesting_service import resolve_offsets
    spec = {"exits": {"stop_loss": {"distance": {"value": 2, "unit": "absolute"}},
                      "take_profit": {"ratio": 2.0}},
            "management": {"stepped_trailing": {}}}
    cfg = {"global_tp_plus_pips": 10, "global_sl_plus_pips": 30, "global_sl_minus_pips": 5,
           "node_offsets": {"7": {"tp_minus_pips": 15}}}
    out = resolve_offsets(cfg, 7, spec, pip_size=0.1)
    # individual TP pair wins: 0 - 15 = -15 pips -> -1.5 price (closer)
    assert out["tp"] == {"offset_pips": -15.0, "offset_price": -1.5, "source": "individual_node"}
    # global SL pair: +30 away -5 closer = +25 pips -> +2.5 price
    assert out["sl"] == {"offset_pips": 25.0, "offset_price": 2.5, "source": "global_deeptesting"}
    assert out["warnings"] == []
    out = resolve_offsets({}, 7, spec, pip_size=0.1)
    assert out["tp"]["source"] == "strategy" and out["tp"]["offset_price"] == 0.0
    # no TP declared: reported NOT applied, never invented
    out = resolve_offsets({"global_tp_plus_pips": 15}, 7, {"exits": {"stop_loss": {}}}, pip_size=0.1)
    assert out["tp"]["offset_price"] == 0.0 and "NOT applied" in out["warnings"][0]
    # no pip size: conversion refused explicitly
    out = resolve_offsets({"global_tp_plus_pips": 15}, 7, spec, pip_size=None)
    assert out["tp"]["offset_price"] == 0.0 and "NOT applied" in out["warnings"][0]
    # scenario handoff: single scenario retrievable by persistent ID
    # (covered by router smoke in test_scenario_lifecycle_versions)


def test_partial_testing_uses_all_available_data(client, seeded_store):
    spec = cross_spec()
    spec["identity"]["name"] = "DT partial node"
    spec["identity"]["tags"] = ["dt-partial-1"]
    created = client.post("/api/v1/instruments/XAUUSD/nodes", json={
        "name": "DT partial node", "origin": "CUSTOM", "spec_json": json.dumps(spec)})
    node_id = created.json()["id"]
    first, last = seeded_store[0]["ts"], seeded_store[-1]["ts"]
    # request starts a year before history exists: partial, but still runs everything
    r = client.post("/api/v1/instruments/XAUUSD/deeptesting/runs", json={
        "node_ids": [node_id], "config": {"mode": "initial",
         "range": {"start": first - 365 * 86400, "end": last},
         "initial_capital": 10000, "risk_mode": "percent", "risk_value": 1.0,
         "max_active_trades": 1, "tick_size": 0.1, "tick_value_account": 1.0}})
    run = r.json()["runs"][0]
    assert run["status"] == "DONE", run
    assert run["coverage_status"] == "PARTIAL_TESTED"
    assert run["metrics"]["candles_tested"] == len(seeded_store)
    assert run["metrics"]["actual_range"] == {"start": first, "end": last}
    assert run["requested_range"]["start"] == first - 365 * 86400

    # sufficient coverage -> TESTED (not partial)
    r = client.post("/api/v1/instruments/XAUUSD/deeptesting/runs", json={
        "node_ids": [node_id], "config": {"mode": "initial",
         "range": {"start": first, "end": last},
         "initial_capital": 10000, "risk_mode": "percent", "risk_value": 1.0,
         "max_active_trades": 1, "tick_size": 0.1, "tick_value_account": 1.0}})
    assert r.json()["runs"][0]["coverage_status"] == "TESTED"


@pytest.fixture()
def seeded_m1_h1(tmp_path, monkeypatch):
    """Two days of H1 + M1: day-1 extremes PDH=110/PDL=90 (in M1), day-2 H1
    bar touches 110 with M1 momentum UP into its close -> NODE 1 SHORT."""
    from test_node1_strategy import T, C, aux_m1, node_candles
    db = tmp_path / "market_data.db"
    conn = md.connect_store(db)
    h1, cutoff = node_candles(3600, "high")
    m1 = aux_m1("up", cutoff)
    with conn:
        for tf, rows in (("H1", h1), ("M1", m1)):
            md.merge_candles(conn, dataset_id="test", instrument="XAUUSD", timeframe=tf,
                             rows=[{**r, "ts_iso": md.ts_iso_of(r["ts"]), "tick_volume": 100,
                                    "spread": 0} for r in rows], policy="import")
    conn.close()
    monkeypatch.setattr(svc, "store_db_path", lambda: db)
    return h1


def test_node1_deeptesting_end_to_end(client, seeded_m1_h1):
    # broker metadata passed per-request (never stored, so other tests'
    # NEEDS_METADATA behaviour is untouched); 300 pips resolves via pip=0.1
    broker_meta = {"XAUUSD": {"pip_size": 0.1, "point_size": 0.01,
                              "tick_size": 0.01, "contract_size": 100}}
    # the officially seeded NODE 1 (creating an identical spec is refused
    # as SPEC_DUPLICATE by the canonical pipeline - by design)
    items = client.get("/api/v1/instruments/XAUUSD/nodes?limit=500").json()["items"]
    node_id = next(n["id"] for n in items
                   if n["name"].startswith("NODE 1 — Previous Day"))
    start, end = seeded_m1_h1[0]["ts"], seeded_m1_h1[-1]["ts"]
    r = client.post("/api/v1/instruments/XAUUSD/deeptesting/runs", json={
        "node_ids": [node_id],
        "config": {"mode": "initial", "range": {"start": start, "end": end},
                   "initial_capital": 200000, "risk_mode": "percent", "risk_value": 1.0,
                   "max_active_trades": 1,
                   "tick_size": 0.1, "tick_value_account": 1.0,
                   "broker_meta": broker_meta,
                   "volume_min": 0.01, "volume_max": 100, "volume_step": 0.01}})
    assert r.status_code == 200, r.text
    run = r.json()["runs"][0]
    assert run["status"] == "DONE", run
    trades = client.get(
        f"/api/v1/instruments/XAUUSD/deeptesting/runs/{run['id']}/trades").json()
    assert trades["count"] == 1
    assert trades["trades"][0]["side"] == "SHORT"  # PDH touch + M1 momentum UP
    detail = client.get(f"/api/v1/instruments/XAUUSD/deeptesting/runs/{run['id']}").json()
    assert detail["assumptions"]["data_requirements"]["m1_momentum"] is True
    assert "M1 candles" in detail["assumptions"]["reference_data_basis"]


def test_till_data_range_uses_all_available(client, seeded_store):
    """TILL DATA: no explicit range -> earliest..latest eligible candle,
    TESTED (not PARTIAL), requested==actual persisted (Part 8)."""
    spec = cross_spec()
    spec["identity"]["name"] = "DT till-data node"
    spec["identity"]["tags"] = ["dt-till-1"]
    created = client.post("/api/v1/instruments/XAUUSD/nodes", json={
        "name": "DT till-data node", "origin": "CUSTOM", "spec_json": json.dumps(spec)})
    node_id = created.json()["id"]
    r = client.post("/api/v1/instruments/XAUUSD/deeptesting/runs", json={
        "node_ids": [node_id],
        "config": {"mode": "initial", "range": {},
                   "initial_capital": 10000, "risk_mode": "percent", "risk_value": 1.0,
                   "max_active_trades": 1, "tick_size": 0.1, "tick_value_account": 1.0,
                   "volume_min": 0.01, "volume_max": 100, "volume_step": 0.01}})
    run = r.json()["runs"][0]
    assert run["status"] == "DONE", run
    assert run["coverage_status"] == "TESTED"
    assert run["requested_range"] == {"start": seeded_store[0]["ts"], "end": seeded_store[-1]["ts"]}
    m = run["metrics"]
    assert m["candles_tested"] == len(seeded_store)
    assert m["directional"]["LONG"]["total_trades"] == 1  # BUY/SELL persisted
    # server-side paginated trades: page 2 of 1 trade is empty, count stable
    page = client.get(f"/api/v1/instruments/XAUUSD/deeptesting/runs/{run['id']}/trades"
                      "?page=0&page_size=25&sort=net_pnl&dir=desc").json()
    assert page["count"] == 1 and page["pages"] == 1 and page["trades"][0]["id"] >= 1
    assert page["trades"][0]["weekday"] in ("Monday", "Tuesday", "Wednesday",
                                            "Thursday", "Friday", "UNASSIGNED")
