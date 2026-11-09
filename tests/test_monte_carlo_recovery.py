import asyncio
import json
import uuid
import pytest
from hero1_market_data import store as md
from app import market_data_service as svc
from test_phase3_engine import CLOSES, candles_from_closes, cross_spec

@pytest.fixture()
def seeded_store(tmp_path,monkeypatch):
    db=tmp_path/'market_data.db'; conn=md.connect_store(db); rows=candles_from_closes(CLOSES)
    with conn:
        md.merge_candles(conn,dataset_id='test',instrument='XAUUSD',timeframe='H1',rows=[{**r,'ts_iso':md.ts_iso_of(r['ts']),'tick_volume':100,'spread':0} for r in rows],policy='import')
    conn.close(); monkeypatch.setattr(svc,'store_db_path',lambda:db)
    return rows
from app.main import create_app
from app import task_service
from hero1_storage.engine import instrument_session, registry_session
from hero1_storage import instrument_models as im
from hero1_storage.registry_models import DashboardTask
from app.monte_carlo_service import simulate


def test_startup_marks_persisted_work_interrupted_and_keeps_partial_results(client, seeded_store):
    spec=cross_spec(); spec['identity']['name']='MC recovery node'; spec['identity']['tags']=['mc-recovery-test']
    created=client.post('/api/v1/instruments/XAUUSD/nodes',json={'name':'MC recovery node','origin':'CUSTOM','spec_json':json.dumps(spec)})
    node_id=created.json()['id']; start,end=seeded_store[0]['ts'],seeded_store[-1]['ts']
    dt=client.post('/api/v1/instruments/XAUUSD/deeptesting/runs',json={'node_ids':[node_id],'config':{'mode':'initial','range':{'start':start,'end':end},'initial_capital':10000,'risk_mode':'percent','risk_value':1,'risk_basis':'initial_capital','max_active_trades':1,'spread_mode':'no_spread','tick_size':0.1,'tick_value_account':1,'volume_min':0.01,'volume_max':100,'volume_step':0.01}})
    deep=dt.json()['runs'][0]; assert deep['status']=='DONE'
    from hero1_storage.engine import registry_session as open_registry
    registry=open_registry(); inst=instrument_session('XAUUSD')
    interrupted_ids=[]
    try:
        for state in ('RUNNING','QUEUED'):
            mcid=str(uuid.uuid4())
            task=task_service.create_task(registry,category='MONTE_CARLO',instrument='XAUUSD',label=f'recovery {state}',total_units=10,status=state,payload={"monte_carlo_run_id":mcid,"node_id":node_id,"deeptest_run_id":deep['id']})
            row=im.MonteCarloRun(id=mcid,instrument_id='XAUUSD',node_id=node_id,deeptest_run_id=deep['id'],node_version=1,fingerprint=deep['fingerprint'],method='RESHUFFLE',seed=431,status='RUNNING',config_json=json.dumps({"simulations":10,"task_id":task.id}),created_at=1)
            if state=='RUNNING':
                trade_rows=inst.query(im.DeepTestRunTrade).filter_by(run_id=deep['id']).all()
                trades=[{**json.loads(t.detail_json or '{}'),"id":t.id} for t in trade_rows]
                # Complete one authentic simulation, then stop at the next batch boundary.
                stop={"value":False}
                def save_batch(done,metrics,paths):
                    if done>=1: stop["value"]=True
                partial=simulate(trades,{"simulations":10,"random_points":2,"displayed_curves":1,"initial_capital":10000,"risk_pct":1,"seed":431,"batch_size":1},"RESHUFFLE",on_batch=save_batch,cancel_check=lambda:stop["value"])
                row.result_json=json.dumps({**partial,"partial":True,"summary":{**partial['summary'],"partial":True}})
                task_service.update_task(registry,task.id,completed_units=1,summary='1/10 persisted')
            inst.add(row);inst.commit();interrupted_ids.append((task.id,mcid,state))
        registry.commit()
    finally:
        inst.close();registry.close()

    async def restart_once():
        app=create_app()
        async with app.router.lifespan_context(app):
            pass
    asyncio.run(restart_once())
    registry=open_registry(); inst=instrument_session('XAUUSD')
    try:
        for task_id,mcid,old_state in interrupted_ids:
            t=registry.get(DashboardTask,task_id); r=inst.get(im.MonteCarloRun,mcid)
            assert t.status=='FAILED' and 'Interrupted by application restart' in t.error
            assert r.status=='FAILED' and 'unsafe to resume' in r.error
            if old_state=='RUNNING':
                saved=json.loads(r.result_json)
                assert saved['summary']['simulations_completed']==1
                assert saved['partial'] is True
                exported=client.get(f'/api/v1/instruments/XAUUSD/monte-carlo/runs/{mcid}/export?format=zip')
                assert exported.status_code==200
                import io,zipfile
                archive=zipfile.ZipFile(io.BytesIO(exported.content)); manifest=json.loads(archive.read('manifest.json'))
                assert manifest['partial'] is True and manifest['completed_simulations']==1
                assert json.loads(archive.read('simulation_results.json'))==saved['per_simulation']
                assert len(list(__import__('csv').DictReader(io.StringIO(archive.read('simulation_results.csv').decode()))))==1
            else:
                assert r.result_json is None
    finally:
        inst.close();registry.close()

    # The failed task's active-only dedupe key has been released; a fresh API run is independent.
    fresh=client.post('/api/v1/instruments/XAUUSD/monte-carlo/runs',json={'node_ids':[node_id],'methods':['RESHUFFLE'],'config':{'simulations':10,'random_points':2,'displayed_curves':1,'initial_capital':10000,'risk_pct':1,'seed':431}}).json()['runs'][0]
    assert fresh['task_id'] and fresh['id'] not in {x[1] for x in interrupted_ids}
    assert fresh['task_id'] not in {x[0] for x in interrupted_ids}
