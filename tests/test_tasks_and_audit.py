"""Shared task progress, async DeepTesting, trade filters, audit export
(Sections 1, 3, 5, 7, 8, 13)."""
import io
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in ("packages/strategy_spec/src", "packages/exec_semantics/src", "packages/indicators/src"):
    sys.path.insert(0, str(ROOT / p))
sys.path.insert(0, str(ROOT / "services/api"))


def test_task_lifecycle_and_acknowledgement_rules():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from hero1_storage.base import Base
    from hero1_storage import registry_models  # noqa: F401
    from app import task_service

    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    t = task_service.create_task(s, category="DEEPTESTING", instrument="XAUUSD",
                                 label="DeepTesting 20 node(s)", total_units=20,
                                 payload={"nodes": []})
    assert t.status == "RUNNING"
    task_service.update_task(s, t.id, completed_units=2, current_unit="Node 3")
    view = task_service.list_visible(s)
    mine = [x for x in view["tasks"] if x["id"] == t.id][0]
    assert mine["percentage"] == 10.0 and mine["completed_units"] == 2
    # acknowledging while ACTIVE dismisses nothing
    assert task_service.acknowledge_category(s, "DEEPTESTING") == 0
    assert task_service.list_visible(s)["tasks"]
    # failed task keeps its real status/reason, never forced to 100%
    task_service.finish_task(s, t.id, "FAILED", error="DATA_BLOCKED: no candles")
    view = task_service.list_visible(s)
    mine = [x for x in view["tasks"] if x["id"] == t.id][0]
    assert mine["status"] == "FAILED" and mine["percentage"] == 10.0
    # duplicate completion is ignored
    task_service.finish_task(s, t.id, "COMPLETED")
    mine = [x for x in task_service.list_visible(s)["tasks"] if x["id"] == t.id][0]
    assert mine["status"] == "FAILED"
    # opening the tab acknowledges only terminal tasks of that category
    t2 = task_service.create_task(s, category="VALIDATION", instrument="XAUUSD",
                                  label="v", total_units=1)
    task_service.finish_task(s, t2.id, "COMPLETED")
    assert task_service.acknowledge_category(s, "DEEPTESTING") == 1
    ids = {x["id"] for x in task_service.list_visible(s)["tasks"]}
    assert t.id not in ids and t2.id in ids  # other category untouched
    # progress never moves backwards
    t3 = task_service.create_task(s, category="DATA_FETCHING", instrument="XAUUSD",
                                  label="f", total_units=4)
    task_service.update_task(s, t3.id, completed_units=3)
    task_service.update_task(s, t3.id, completed_units=1)
    mine = [x for x in task_service.list_visible(s)["tasks"] if x["id"] == t3.id][0]
    assert mine["completed_units"] == 3


def test_structured_trade_filters_and_all_page_size():
    from app.deeptesting_service import paginate_trades
    base = 1767225600  # Thu 2026-01-01 00:00 UTC
    trades = [{"id": i, "entry_ts": base + i * 3600, "exit_ts": base + i * 3600 + 60,
               "net_pnl": float(i - 5), "gross_pnl": float(i - 5),
               "side": "LONG" if i % 2 == 0 else "SHORT", "volume": 1,
               "entry_price": 100, "exit_price": 100, "initial_stop": 90,
               "take_profit": 110, "result": "WIN", "holding_seconds": i * 100,
               "exit_reason": "TAKE_PROFIT"} for i in range(12)]
    out = paginate_trades(trades, page=0, page_size="all", filters={"side": "LONG"})
    assert out["count"] == 6 and out["pages"] == 1 and out["page_size"] == "all"
    out = paginate_trades(trades, page=0, page_size=25, filters={"outcome": "LOSS"})
    assert out["count"] == 5  # net -5..-1
    out = paginate_trades(trades, page=0, page_size=25,
                          filters={"side": "SHORT", "min_net": 0})
    assert all(t["side"] == "SHORT" and t["net_pnl"] >= 0 for t in out["trades"])
    out = paginate_trades(trades, page=0, page_size=25, filters={"session": "ASIA"})
    assert all(t["session"] == "ASIA" for t in out["trades"]) and out["count"] > 0
    out = paginate_trades(trades, page=0, page_size=25, filters={"exit_reason": "STOP_LOSS"})
    assert out["count"] == 0


def test_audit_zip_contents_and_honest_labels():
    from app.audit_export import build_audit_zip

    class FakeRun:
        id = 7; node_id = 3; node_version = 2; instrument_id = "XAUUSD"
        timeframe = "H1"; mode = "initial"; window_label = None; status = "DONE"
        scenario_id = None; fingerprint = "abc123"; compiler_version = "1"
        engine_version = "3.0.0"; config_json = json.dumps({
            "initial_capital": 200000, "account_currency": "USD", "risk_mode": "percent",
            "risk_value": 1.0, "max_active_trades": 1,
            "requested_range": {"start": 1, "end": 2}, "actual_range": {"start": 1, "end": 2},
            "coverage_status": "TESTED", "data_snapshot": {"instrument": "XAUUSD",
                "source_provenance": {"status": "MATCHED_IMPORT_MANIFEST_ROW",
                                      "candidates": [{"source_sha256": "sha-from-test",
                                                      "fingerprint": "dataset-fingerprint"}]}},
            "execution_specification_inputs": {"verification_status": "UNVERIFIED_NO_MT5_SNAPSHOT; explicit run inputs used",
                                                "tick_size": 0.01, "tick_value_account": 1.0,
                                                "tick_value_account_source": "run_config"}})
        assumptions_json = json.dumps({"spread_price": 0.0})
        metrics_json = json.dumps({"total_trades": 1})
        data_quality_json = json.dumps({"readiness": "READY"})
        skipped_json = json.dumps([])

    class FakeNode:
        id = 3; name = "N1"; spec_json = json.dumps({"identity": {"name": "N1"}})

    trades = [{"id": 1, "side": "LONG", "entry_ts": 1767225600, "exit_ts": 1767229200,
               "entry_price": 100.0, "exit_price": 101.0, "volume": 1.0,
               "initial_stop": 99.0, "take_profit": 102.0, "final_stop": 99.0,
               "planned_risk": 100.0, "gross_pnl": 100.0, "commission": 0.0,
               "swap": -5.0, "spread_cost": 40.0, "net_pnl": 95.0, "result": "WIN",
               "exit_reason": "TAKE_PROFIT", "holding_seconds": 3600}]
    data = build_audit_zip(run=FakeRun(), node=FakeNode(), trades=trades)
    z = zipfile.ZipFile(io.BytesIO(data))
    names = {n.split("/", 1)[1] for n in z.namelist()}
    assert {"manifest.json", "trades.csv", "trade_events.jsonl",
            "strategy_decisions.jsonl", "condition_evaluations.jsonl",
            "indicator_evidence.jsonl", "execution_events.jsonl",
            "compliance_report.json", "data_references.json", "README.txt"} <= names
    manifest = json.loads(z.read("HERO1_TRADING_AUDIT/manifest.json"))
    assert manifest["scope"] == "FULL_RUN" and manifest["node"]["fingerprint"] == "abc123"
    assert manifest["execution_specification_inputs"]["verification_status"].startswith("UNVERIFIED")
    assert manifest["execution_specification_inputs"]["tick_value_account_source"] == "run_config"
    assert manifest["evidence_status"]["condition_evaluations"].startswith("INCOMPLETE")
    decisions = z.read("HERO1_TRADING_AUDIT/strategy_decisions.jsonl").decode()
    assert "INCOMPLETE EVIDENCE" in decisions and "TRADE_EXECUTED" in decisions
    comp = json.loads(z.read("HERO1_TRADING_AUDIT/compliance_report.json"))
    assert comp["summary_by_verdict"]["UNVERIFIED"] >= 3  # missing logs are not PASSes
    verdicts = {c["check"]: c["verdict"] for c in comp["checks"]}
    assert verdicts["entry_before_exit_chronology"] == "PASS"
    assert verdicts["net_pnl_reconciles_gross_commission_signed_swap"] == "PASS"
    net_check = next(c for c in comp["checks"] if c["check"] == "net_pnl_reconciles_gross_commission_signed_swap")
    assert "not deducted again" in net_check["formula"]
    refs = json.loads(z.read("HERO1_TRADING_AUDIT/data_references.json"))
    assert refs["source_provenance"]["candidates"][0]["source_sha256"] == "sha-from-test"
    assert verdicts["entry_conditions_followed"] == "UNVERIFIED"
    csv_text = z.read("HERO1_TRADING_AUDIT/trades.csv").decode()
    assert "Thursday" in csv_text and "ASIA" in csv_text  # stored classification


def test_deeptest_config_spec_fallback_and_import_provenance(tmp_path, monkeypatch):
    from app import deeptesting_service as dt

    monkeypatch.setattr(dt.metadata_service, "best_snapshot", lambda _instrument: None)
    monkeypatch.setattr(dt.metadata_service, "snapshot_broker_meta", lambda _instrument: {})
    spec = dt._instrument_spec("XAUUSD", {
        "tick_size": 0.01, "tick_value_account": 1.0, "contract_size": 100.0,
        "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01,
    })
    assert spec.contract_size == 100.0
    assert spec.tick_value_account == 1.0

    manifest_path = tmp_path / "import_manifest.json"
    manifest_path.write_text(json.dumps({"datasets": [{
        "dataset_id": "dataset-1", "instrument": "XAUUSD", "timeframe": "M1",
        "row_count": 100057, "first_ts": "2026-07-07 18:41:00",
        "last_ts": "2026-10-16 12:31:00", "status": "AVAILABLE",
        "source_file": "XAUUSD_M1.csv", "source_sha256": "source-sha",
        "fingerprint": "candle-fingerprint", "validation_result": "PASS",
    }]}), encoding="utf-8")
    monkeypatch.setattr(dt.market_data_service, "manifest_path", lambda: manifest_path)
    provenance = dt._dataset_provenance("XAUUSD", "M1", {
        "first_ts": 1783449660, "last_ts": 1792153860, "candles": 100057,
    })
    assert provenance["status"] == "MATCHED_IMPORT_MANIFEST_ROW"
    assert provenance["candidates"][0]["source_sha256"] == "source-sha"
    assert dt._dataset_provenance("XAUUSD", "H1", {
        "first_ts": 1783449660, "last_ts": 1792153860, "candles": 100057,
    })["status"] == "UNVERIFIED_NO_MATCHING_IMPORT_MANIFEST_ROW"


# ---------------------------------------------------------------------------
# API level: async DeepTesting progress + audit export download
# ---------------------------------------------------------------------------
import pytest  # noqa: E402

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
                         rows=[{**r, "ts_iso": md.ts_iso_of(r["ts"]), "tick_volume": 100,
                                "spread": 0} for r in rows], policy="import")
    conn.close()
    monkeypatch.setattr(svc, "store_db_path", lambda: db)
    return rows


def _node(client, name):
    spec = cross_spec()
    spec["identity"]["name"] = name
    spec["identity"]["tags"] = [name.lower().replace(" ", "-")]
    created = client.post("/api/v1/instruments/XAUUSD/nodes", json={
        "name": name, "origin": "CUSTOM", "spec_json": json.dumps(spec)})
    assert created.status_code in (200, 201), created.text
    return created.json()["id"]


_CONFIG = {"mode": "initial", "range": {},
           "initial_capital": 10000, "risk_mode": "percent", "risk_value": 1.0,
           "risk_basis": "initial_capital", "max_active_trades": 1,
           "tick_size": 0.1, "tick_value_account": 1.0,
           "volume_min": 0.01, "volume_max": 100, "volume_step": 0.01}


def test_async_deeptesting_task_progress_and_ack(client, seeded_store):
    import time as _time
    n1 = _node(client, "Async DT one")
    n2 = _node(client, "Async DT two")
    r = client.post("/api/v1/instruments/XAUUSD/deeptesting/runs?async_mode=true",
                    json={"node_ids": [n1, n2], "config": dict(_CONFIG)})
    assert r.status_code == 200, r.text
    task_id = r.json()["task_id"]
    deadline = _time.time() + 30
    task = None
    while _time.time() < deadline:
        view = client.get("/api/v1/tasks").json()
        mine = [t for t in view["tasks"] if t["id"] == task_id]
        if mine and mine[0]["status"] in ("COMPLETED", "FAILED"):
            task = mine[0]
            break
        _time.sleep(0.1)
    assert task is not None, "task never finished"
    assert task["status"] == "COMPLETED", task
    assert task["percentage"] == 100.0 and task["completed_units"] == 2
    nodes = task["payload"]["nodes"]
    assert [n["status"] for n in nodes] == ["COMPLETED_WITH_WARNINGS", "COMPLETED_WITH_WARNINGS"]
    assert all(n["warnings"] for n in nodes)
    assert all(n["trades"] == 1 for n in nodes)  # separate runs, never mixed
    assert len({n["run_id"] for n in nodes}) == 2
    # completed bar stays visible until the DeepTesting tab acknowledges it
    client.post("/api/v1/tasks/acknowledge", json={"category": "VALIDATION"})
    assert any(t["id"] == task_id for t in client.get("/api/v1/tasks").json()["tasks"]), \
        "acknowledging another category must not dismiss the DeepTesting bar"
    assert client.post("/api/v1/tasks/acknowledge",
                       json={"category": "DEEPTESTING"}).json()["acknowledged"] >= 1
    assert not any(t["id"] == task_id for t in client.get("/api/v1/tasks").json()["tasks"])
    # the runs themselves are really persisted
    runs = client.get("/api/v1/instruments/XAUUSD/deeptesting/runs").json()["runs"]
    assert len([x for x in runs if x["node_id"] in (n1, n2)]) >= 2


def test_audit_export_endpoint_zip(client, seeded_store):
    n1 = _node(client, "Audit export node")
    r = client.post("/api/v1/instruments/XAUUSD/deeptesting/runs",
                    json={"node_ids": [n1], "config": dict(_CONFIG)})
    run = r.json()["runs"][0]
    assert run["status"] == "DONE", run
    z = client.get(f"/api/v1/instruments/XAUUSD/deeptesting/runs/{run['id']}/audit-export")
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
    book = zipfile.ZipFile(io.BytesIO(z.content))
    manifest = json.loads(book.read("HERO1_TRADING_AUDIT/manifest.json"))
    assert manifest["run"]["id"] == run["id"] and manifest["trade_count_exported"] == 1
    # filtered scope is clearly distinguished
    z2 = client.get(f"/api/v1/instruments/XAUUSD/deeptesting/runs/{run['id']}/audit-export"
                    "?scope=filtered&filters=" + json.dumps({"side": "SHORT"}))
    book2 = zipfile.ZipFile(io.BytesIO(z2.content))
    manifest2 = json.loads(book2.read("HERO1_TRADING_AUDIT/manifest.json"))
    assert manifest2["scope"] == "FILTERED_SUBSET" and manifest2["trade_count_exported"] == 0
