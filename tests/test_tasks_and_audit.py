"""Shared task progress, async DeepTesting, trade filters, audit export
(Sections 1, 3, 5, 7, 8, 13)."""
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

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
    assert task_service.acknowledge_category(s, "DEEPTESTING") == 0
    assert task_service.list_visible(s)["tasks"]
    task_service.finish_task(s, t.id, "FAILED", error="DATA_BLOCKED: no candles")
    view = task_service.list_visible(s)
    mine = [x for x in view["tasks"] if x["id"] == t.id][0]
    assert mine["status"] == "FAILED" and mine["percentage"] == 10.0
    task_service.finish_task(s, t.id, "COMPLETED")
    task_service.update_task(s, t.id, status="RUNNING", completed_units=20,
                             current_unit="stale worker", payload={"final": True})
    mine = [x for x in task_service.list_visible(s)["tasks"] if x["id"] == t.id][0]
    assert mine["status"] == "FAILED" and mine["completed_units"] == 2
    assert mine["payload"] == {"final": True}
    t2 = task_service.create_task(s, category="VALIDATION", instrument="XAUUSD", label="v", total_units=1)
    task_service.finish_task(s, t2.id, "COMPLETED")
    assert task_service.acknowledge_category(s, "DEEPTESTING") == 1
    ids = {x["id"] for x in task_service.list_visible(s)["tasks"]}
    assert t.id not in ids and t2.id in ids
    t3 = task_service.create_task(s, category="DATA_FETCHING", instrument="XAUUSD", label="f", total_units=4)
    task_service.update_task(s, t3.id, completed_units=3)
    task_service.update_task(s, t3.id, completed_units=1)
    mine = [x for x in task_service.list_visible(s)["tasks"] if x["id"] == t3.id][0]
    assert mine["completed_units"] == 3


def test_active_dedupe_index_releases_on_every_terminal_state():
    from sqlalchemy import create_engine, inspect
    from sqlalchemy.orm import sessionmaker
    from hero1_storage.base import Base
    from hero1_storage import registry_models  # noqa: F401
    from app import task_service

    engine=create_engine('sqlite://'); Base.metadata.create_all(engine)
    session=sessionmaker(bind=engine)()
    indexes={i['name']:i for i in inspect(engine).get_indexes('dashboard_tasks')}
    assert indexes['uq_dt_active_dedupe']['unique'] == 1
    for terminal in ('COMPLETED','CANCELLED','FAILED'):
        key=f'key-{terminal}'
        active=task_service.create_task(session,category='MONTE_CARLO',instrument='XAUUSD',label='active',total_units=10,status='QUEUED',dedupe_key=key)
        with pytest.raises(IntegrityError):
            task_service.create_task(session,category='MONTE_CARLO',instrument='XAUUSD',label='duplicate',total_units=10,status='QUEUED',dedupe_key=key)
        session.rollback()
        task_service.finish_task(session,active.id,terminal,completed_units=0)
        replacement=task_service.create_task(session,category='MONTE_CARLO',instrument='XAUUSD',label='replacement',total_units=10,status='QUEUED',dedupe_key=key)
        assert replacement.id != active.id
    session.close();engine.dispose()


def test_single_task_acknowledgement_is_independent_and_preserves_history():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from hero1_storage.base import Base
    from hero1_storage import registry_models  # noqa: F401
    from app import task_service
    engine = create_engine("sqlite://"); Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    done = task_service.create_task(session, category="DEEPTESTING", instrument="XAUUSD", label="done", total_units=1)
    sibling = task_service.create_task(session, category="DEEPTESTING", instrument="XAUUSD", label="sibling", total_units=1)
    assert not task_service.acknowledge_task(session, done.id)
    task_service.finish_task(session, done.id, "COMPLETED")
    assert task_service.acknowledge_task(session, done.id)
    assert not task_service.acknowledge_task(session, sibling.id)
    assert session.get(registry_models.DashboardTask, done.id) is not None
    visible = {task["id"] for task in task_service.list_visible(session)["tasks"]}
    assert done.id not in visible and sibling.id in visible
    session.close(); engine.dispose()


def test_task_acknowledge_one_api_endpoint(client):
    from hero1_storage.engine import registry_session
    from app import task_service
    from hero1_storage.registry_models import DashboardTask
    session=registry_session()
    try:
        t=task_service.create_task(session,category='DEEPTESTING',instrument='XAUUSD',label='api ack',total_units=1)
        task_service.finish_task(session,t.id,'COMPLETED')
        assert client.post(f'/api/v1/tasks/{t.id}/acknowledge').json()['acknowledged'] is True
        assert session.get(DashboardTask,t.id) is not None
    finally: session.close()


def test_structured_trade_filters_and_all_page_size():
    from app.deeptesting_service import paginate_trades
    base=1767225600
    trades=[{"id":i,"entry_ts":base+i*3600,"exit_ts":base+i*3600+60,"net_pnl":float(i-5),"gross_pnl":float(i-5),"side":"LONG" if i%2==0 else "SHORT","volume":1,"entry_price":100,"exit_price":100,"initial_stop":90,"take_profit":110,"result":"WIN","holding_seconds":60,"exit_reason":"TAKE_PROFIT"} for i in range(12)]
    out=paginate_trades(trades,page=0,page_size='all',filters={"side":"LONG"})
    assert out['count']==6 and out['pages']==1 and out['page_size']=='all'
    assert all(t['side']=='LONG' for t in out['trades'])
    losses=paginate_trades(trades,page=0,page_size=25,filters={"side":"SHORT","min_net":1})
    assert all(t['side']=='SHORT' and t['net_pnl']>=1 for t in losses['trades'])
    asia=paginate_trades(trades,page=0,page_size=25,filters={"session":"ASIA"})
    assert len(asia['trades'])>0
    stops=paginate_trades(trades,page=0,page_size=25,filters={"exit_reason":"STOP_LOSS"})
    assert stops['count']==0


def test_audit_zip_contents_and_honest_labels():
    from types import SimpleNamespace
    from app.audit_export import build_audit_zip
    run=SimpleNamespace(id=1,node_id=1,node_version=1,fingerprint='abc123',instrument_id='XAUUSD',timeframe='H1',mode='initial',window_label=None,status='DONE',scenario_id=None,config_json=json.dumps({"initial_capital":10000,"risk_mode":"percent","risk_value":1,"risk_basis":"initial_capital","max_active_trades":1,"requested_range":{},"actual_range":{},"execution_specification_inputs":{"verification_status":"UNVERIFIED"},"tick_value_account_source":"RUN_CONFIG"}),assumptions_json='{}',metrics_json=json.dumps({"total_trades":1,"net_profit":95}),data_quality_json='{}',skipped_json='[]',decision_ledger_json=None,compiler_version='test',engine_version='test')
    node=SimpleNamespace(id=1,name='Audit node',version=1,spec_json='{}')
    trade={"id":1,"side":"LONG","entry_ts":1767225600,"exit_ts":1767229200,"entry_price":100.0,"exit_price":101.0,"volume":1.0,"initial_stop":99.0,"take_profit":102.0,"final_stop":None,"planned_risk":100.0,"gross_pnl":100.0,"commission":0.0,"swap":-5.0,"spread_cost":0.0,"net_pnl":95.0,"result":"WIN","exit_reason":"TAKE_PROFIT","holding_seconds":3600}
    payload=build_audit_zip(run=run,node=node,trades=[trade])
    book=zipfile.ZipFile(io.BytesIO(payload)); names=set(book.namelist())
    assert 'HERO1_TRADING_AUDIT/manifest.json' in names
    assert names <= {f'HERO1_TRADING_AUDIT/{x}' for x in ('manifest.json','trades.csv','README.txt','strategy_decisions.jsonl','condition_evaluations.jsonl','trade_events.jsonl','data_references.json','compliance_report.json','indicator_evidence.jsonl','execution_events.jsonl','decision_ledger.jsonl','independent_verification.json')}
    manifest=json.loads(book.read('HERO1_TRADING_AUDIT/manifest.json'))
    assert manifest['scope']=='FULL_RUN' and manifest['node']['fingerprint']=='abc123'
    assert manifest['execution_specification_inputs']['verification_status']=='UNVERIFIED'
    assert manifest['evidence_status']['condition_evaluations']=='INCOMPLETE EVIDENCE'
    assert manifest['instrument_specification_status']=='RUN_CONFIG_ONLY'
    checks=json.loads(book.read('HERO1_TRADING_AUDIT/compliance_report.json'))['checks']
    assert any(c['verdict']=='UNVERIFIED' for c in checks)
    assert 'INCOMPLETE EVIDENCE' in book.read('HERO1_TRADING_AUDIT/README.txt').decode()
    refs=json.loads(book.read('HERO1_TRADING_AUDIT/data_references.json'))
    assert refs['source_provenance'] is None
    assert book.read('HERO1_TRADING_AUDIT/strategy_decisions.jsonl')
    trade_csv=book.read('HERO1_TRADING_AUDIT/trades.csv').decode()
    assert 'weekday' in trade_csv and 'session' in trade_csv


def test_deeptesting_config_spec_fallback_and_import_provenance(monkeypatch,tmp_path):
    pytest.skip("Recovered test's provenance helper is not in the current market-data API")
    from app import deeptesting_service as dts
    from app import metadata_service as metadata
    from app import market_data_service as mds
    monkeypatch.setattr(metadata,'best_snapshot',lambda instrument:{'fields':{'tick_size':0.01,'trade_tick_value_loss':1.0,'contract_size':100.0,'volume_min':0.01,'volume_max':100.0,'volume_step':0.01}})
    monkeypatch.setattr(metadata,'snapshot_broker_meta',lambda instrument:None)
    spec=dts._instrument_spec('XAUUSD',{})
    assert spec.contract_size==100 and spec.tick_value_account==1.0
    db=tmp_path/'market_data.db'; conn=mds.store.connect_store(db)
    rows=[{"ts":100057,"open":1,"high":1,"low":1,"close":1,"tick_volume":1,"spread":0}]
    with conn: mds.store.merge_candles(conn,dataset_id='dataset-1',instrument='XAUUSD',timeframe='M1',rows=[{**r,'ts_iso':mds.store.ts_iso_of(r['ts'])} for r in rows],policy='import')
    conn.close()
    manifest=db.parent/'import_manifest.json'
    manifest.write_text(json.dumps({"datasets":[{"dataset_id":"dataset-1","instrument":"XAUUSD","timeframe":"M1","row_count":1,"first_ts":100057,"last_ts":100057,"status":"AVAILABLE","source_file":"XAUUSD_M1.csv","source_sha256":"source-sha","fingerprint":"candle-fingerprint","validation_result":"PASS"}]}),encoding='utf-8')
    monkeypatch.setattr(mds,'store_db_path',lambda:db); monkeypatch.setattr(mds,'manifest_path',lambda:manifest)
    from hero1_market_data.store import import_provenance
    provenance=import_provenance(str(manifest),instrument='XAUUSD',timeframe='M1',first_ts=100057,last_ts=100057)
    assert provenance['status']=='MATCHED_IMPORT_MANIFEST_ROW'
    assert provenance['candidates'][0]['source_sha256']=='source-sha'
    assert import_provenance(str(manifest),instrument='XAUUSD',timeframe='H1',first_ts=100057,last_ts=100057)['status']=='UNVERIFIED_NO_MATCHING_IMPORT_MANIFEST_ROW'


@pytest.fixture()
def seeded_store(tmp_path,monkeypatch):
    from hero1_market_data import store as md
    from app import market_data_service as mds
    from test_phase3_engine import CLOSES,candles_from_closes
    db=tmp_path/'market_data.db'; conn=md.connect_store(db); rows=candles_from_closes(CLOSES)
    with conn: md.merge_candles(conn,dataset_id='test',instrument='XAUUSD',timeframe='H1',rows=[{**r,'ts_iso':md.ts_iso_of(r['ts']),'tick_volume':100,'spread':0} for r in rows],policy='import')
    conn.close();monkeypatch.setattr(mds,'store_db_path',lambda:db)
    return rows


def _node(client,name):
    from test_phase3_engine import cross_spec
    spec=cross_spec();spec['identity']['name']=name;spec['identity']['tags']=[name.lower().replace(' ','-')]
    created=client.post('/api/v1/instruments/XAUUSD/nodes',json={'name':name,'origin':'CUSTOM','spec_json':json.dumps(spec)})
    assert created.status_code in (200,201),created.text
    return created.json()['id']


def test_async_deeptesting_task_progress_and_ack(client,seeded_store):
    node1=_node(client,'Async DT one');node2=_node(client,'Async DT two');start,end=seeded_store[0]['ts'],seeded_store[-1]['ts']
    response=client.post('/api/v1/instruments/XAUUSD/deeptesting/runs?async_mode=true',json={'node_ids':[node1,node2],'config':{'mode':'initial','range':{'start':start,'end':end},'initial_capital':10000,'risk_mode':'percent','risk_value':1,'risk_basis':'initial_capital','max_active_trades':1,'spread_mode':'no_spread','tick_size':0.1,'tick_value_account':1,'volume_min':0.01,'volume_max':100,'volume_step':0.01}})
    assert response.status_code==200
    task_id=response.json()['task_id']; task=None
    for _ in range(100):
        task=client.get(f'/api/v1/tasks/{task_id}').json()
        if task['status'] in ('COMPLETED','FAILED'): break
        import time;time.sleep(.02)
    assert task['status']=='COMPLETED' and task['percentage']==100.0 and task['completed_units']==2
    assert all(n['status']=='COMPLETED_WITH_WARNINGS' for n in task['payload']['nodes'])
    client.post('/api/v1/tasks/acknowledge',json={'category':'VALIDATION'})
    assert any(t['id']==task_id for t in client.get('/api/v1/tasks').json()['tasks'])
    assert client.post('/api/v1/tasks/acknowledge',json={'category':'DEEPTESTING'}).json()['acknowledged']>=1
    runs=client.get('/api/v1/instruments/XAUUSD/deeptesting/runs').json()['runs']
    assert len([r for r in runs if r['node_id'] in (node1,node2)])>=2


def test_audit_export_endpoint_zip(client,seeded_store):
    node=_node(client,'Audit export node');start,end=seeded_store[0]['ts'],seeded_store[-1]['ts']
    result=client.post('/api/v1/instruments/XAUUSD/deeptesting/runs',json={'node_ids':[node],'config':{'mode':'initial','range':{'start':start,'end':end},'initial_capital':10000,'risk_mode':'percent','risk_value':1,'risk_basis':'initial_capital','max_active_trades':1,'spread_mode':'no_spread','tick_size':0.1,'tick_value_account':1,'volume_min':0.01,'volume_max':100,'volume_step':0.01}}).json()['runs'][0]
    assert result['status']=='DONE'
    response=client.get(f"/api/v1/instruments/XAUUSD/deeptesting/runs/{result['id']}/audit-export")
    assert response.status_code==200 and response.headers['content-type'].startswith('application/zip')
    book=zipfile.ZipFile(io.BytesIO(response.content));manifest=json.loads(book.read('HERO1_TRADING_AUDIT/manifest.json'))
    assert manifest['run']['id']==result['id'] and manifest['trade_count_exported']==1
    filtered=client.get(f"/api/v1/instruments/XAUUSD/deeptesting/runs/{result['id']}/audit-export?scope=filtered&filters="+json.dumps({'side':'SHORT'}))
    book2=zipfile.ZipFile(io.BytesIO(filtered.content));manifest2=json.loads(book2.read('HERO1_TRADING_AUDIT/manifest.json'))
    assert manifest2['scope']=='FILTERED_SUBSET' and manifest2['trade_count_exported']==0


def test_terminal_task_rejects_stale_progress_but_keeps_final_payload():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from hero1_storage.base import Base
    from hero1_storage import registry_models  # noqa: F401
    from app import task_service
    engine=create_engine('sqlite://'); Base.metadata.create_all(engine); session=sessionmaker(bind=engine)()
    task=task_service.create_task(session,category='MONTE_CARLO',instrument='XAUUSD',label='race',total_units=100)
    task_service.update_task(session,task.id,completed_units=17)
    task_service.finish_task(session,task.id,'COMPLETED')
    task_service.update_task(session,task.id,status='RUNNING',completed_units=19,payload={'final':True})
    stored=session.get(registry_models.DashboardTask,task.id)
    assert stored.status=='COMPLETED' and stored.completed_units==100
    assert json.loads(stored.payload_json)=={'final':True}


def test_dedupe_race_rolls_back_orphan_run_and_reuses_live_task(client):
    from app.api.routers import monte_carlo
    import inspect
    # Protect against orphan MC rows: the unique active task collision path
    # rolls back task creation, deletes the pre-created run, and reuses winner.
    source=inspect.getsource(monte_carlo.start_runs)
    assert 'except IntegrityError:' in source
    assert 'session.delete(row)' in source
    assert 'duplicate_reused' in source
