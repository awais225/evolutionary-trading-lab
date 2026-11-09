import json
import pytest
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from test_phase3_deeptesting_api import seeded_store
from test_phase3_engine import cross_spec


def test_deeptest_to_monte_carlo_persist_reopen_and_export(client, seeded_store, monkeypatch):
    spec=cross_spec(); spec['identity']['name']='MC API node'; spec['identity']['tags']=['mc-api-acceptance']
    created=client.post('/api/v1/instruments/XAUUSD/nodes',json={'name':'MC API node','origin':'CUSTOM','spec_json':json.dumps(spec)})
    assert created.status_code in (200,201),created.text
    node_id=created.json()['id']; start,end=seeded_store[0]['ts'],seeded_store[-1]['ts']
    dt=client.post('/api/v1/instruments/XAUUSD/deeptesting/runs',json={'node_ids':[node_id],'config':{'mode':'initial','range':{'start':start,'end':end},'initial_capital':10000,'risk_mode':'percent','risk_value':1,'risk_basis':'initial_capital','max_active_trades':1,'spread_mode':'no_spread','tick_size':0.1,'tick_value_account':1,'volume_min':0.01,'volume_max':100,'volume_step':0.01}})
    assert dt.status_code==200,dt.text
    run=dt.json()['runs'][0]; assert run['status']=='DONE'
    body={'node_ids':[node_id],'methods':['RESHUFFLE','BLOCK_BOOTSTRAP'],'config':{'simulations':10,'random_points':20,'displayed_curves':2,'initial_capital':10000,'risk_pct':1,'block_size':3,'seed':1234}}
    mc=client.post('/api/v1/instruments/XAUUSD/monte-carlo/runs',json=body)
    assert mc.status_code==200,mc.text
    assert len(mc.json()['runs'])==2 and all(r.get('task_id') for r in mc.json()['runs'])
    all_trades=client.get(f"/api/v1/instruments/XAUUSD/deeptesting/runs/{run['id']}/trades").json()['trades']
    selected_trades=all_trades[::2]
    scenario=client.post('/api/v1/instruments/XAUUSD/deeptesting/scenarios',json={
        'name':'Scenario filtered ledger','config':{'node_ids':[node_id],'source_run_id':run['id'],
        'node_version':run['node_version'],'day_selections':{'Monday':False},
        'session_selections':{'Monday|ASIA':False},'filtered_trade_ids':[t['id'] for t in selected_trades],
        'filtered_metrics':{'trades':999999,'net':999999}}})
    assert scenario.status_code==200,scenario.text
    scenario_id=scenario.json()['id']
    saved=client.get('/api/v1/instruments/XAUUSD/deeptesting/scenarios').json()['scenarios']
    child=next(s for s in saved if s['id']==scenario_id)
    assert child['node_id']==node_id and child['source_run_id']==run['id']
    assert len(child['trades'])==len(selected_trades) and child['stats']['trades']==len(selected_trades)
    assert child['stats']['net_profit']==sum(t.get('net_pnl',0) for t in selected_trades)
    assert child['stats']['net_profit'] != 999999  # server derives stats from the source ledger
    scenario_mc=client.post('/api/v1/instruments/XAUUSD/monte-carlo/runs',json={
        'node_ids':[node_id],'scenario_ids':{str(node_id):scenario_id},'methods':['RESHUFFLE'],
        'config':{'simulations':10,'random_points':20,'displayed_curves':2,'initial_capital':10000,'risk_pct':1,'block_size':3,'seed':987}})
    assert scenario_mc.status_code==200 and scenario_mc.json()['runs'][0]['scenario_id']==scenario_id
    item=scenario_mc.json()['runs'][0]
    for _ in range(100):
        task=client.get(f"/api/v1/tasks/{item['task_id']}").json()
        if task['status'] in ('COMPLETED','FAILED','CANCELLED'): break
        time.sleep(.02)
    assert task['status']=='COMPLETED',task
    detail=client.get(f"/api/v1/instruments/XAUUSD/monte-carlo/runs/{item['id']}").json()
    assert detail['scenario_id']==scenario_id and detail['deeptest_run_id']==run['id']
    assert detail['result']['summary']['trade_count']==len(selected_trades)
    for item in mc.json()['runs']:
        for _ in range(100):
            task=client.get(f"/api/v1/tasks/{item['task_id']}").json()
            if task['status'] in ('COMPLETED','FAILED','CANCELLED'): break
            time.sleep(.02)
        assert task['status']=='COMPLETED', (task.get('error'), client.get(f"/api/v1/instruments/XAUUSD/monte-carlo/runs/{item['id']}").json().get('error'))
        reopened=client.get(f"/api/v1/instruments/XAUUSD/monte-carlo/runs/{item['id']}")
        assert reopened.status_code==200 and reopened.json()['result']['summary']['seed']==item['seed']
        assert reopened.json()['result']['summary']['seed'] != 0
        exported=client.get(f"/api/v1/instruments/XAUUSD/monte-carlo/runs/{item['id']}/export?format=zip")
        assert exported.status_code==200 and exported.content[:2]==b'PK'
        import io,zipfile
        archive=zipfile.ZipFile(io.BytesIO(exported.content)); files=set(archive.namelist())
        assert {'manifest.json','configuration.json','original_deeptesting_reference.json','strategy_identity.json','simulation_results.csv','simulation_results.json','aggregate_metrics.json','percentile_distributions.json','equity_curves.json','robustness_score.json','eligibility.json','execution_stress.json','warnings.json','evidence_readiness.json'} <= files
        manifest=json.loads(archive.read('manifest.json')); exported_run=json.loads(archive.read('original_deeptesting_reference.json'))
        assert manifest['deeptest_run_id']==run['id']
        assert exported_run['run_id']==manifest['deeptest_run_id']
        assert manifest['fingerprint']==run['fingerprint']
        assert json.loads(archive.read('strategy_identity.json'))['fingerprint']==run['fingerprint']
        assert json.loads(archive.read('configuration.json'))==reopened.json()['config']
        assert manifest['seed_strategy']==reopened.json()['result']['summary']['seed_strategy']
        assert json.loads(archive.read('aggregate_metrics.json'))==reopened.json()['result']['summary']
        exported_sims=json.loads(archive.read('simulation_results.json'))
        assert exported_sims==reopened.json()['result']['per_simulation']
        assert len({m['simulation_seed'] for m in exported_sims})==len(exported_sims)
        assert [m['simulation_index'] for m in exported_sims]==list(range(1,len(exported_sims)+1))
        import csv
        csv_rows=list(csv.DictReader(io.StringIO(archive.read('simulation_results.csv').decode())))
        assert len(csv_rows)==len(exported_sims)
        assert [int(row['simulation_seed']) for row in csv_rows]==[m['simulation_seed'] for m in exported_sims]
        assert json.loads(archive.read('percentile_distributions.json'))['simulation_distributions']==reopened.json()['result']['summary']['simulation_distributions']
        exported_curves=json.loads(archive.read('equity_curves.json'))
        assert exported_curves['percentiles']==reopened.json()['result']['equity_percentiles']
        assert exported_curves['representative']==reopened.json()['result']['representative_curves']
        assert json.loads(archive.read('robustness_score.json'))==reopened.json()['result']['robustness']
        assert json.loads(archive.read('eligibility.json'))==reopened.json()['result']['eligibility']
        txt_export=client.get(f"/api/v1/instruments/XAUUSD/monte-carlo/runs/{item['id']}/export?format=txt")
        assert txt_export.status_code==200 and f"run={item['id']}" in txt_export.text
        assert f"DeepTesting run={run['id']}" in txt_export.text
    rerun=client.post('/api/v1/instruments/XAUUSD/monte-carlo/runs',json={'node_ids':[node_id],'methods':['RESHUFFLE'],'config':body['config']}).json()['runs'][0]
    assert rerun['id']!=mc.json()['runs'][0]['id'] and rerun['task_id']!=mc.json()['runs'][0]['task_id']
    for _ in range(200):
        repeated_task=client.get(f"/api/v1/tasks/{rerun['task_id']}").json()
        if repeated_task['status'] in ('COMPLETED','FAILED','CANCELLED'): break
        time.sleep(.01)
    assert repeated_task['status']=='COMPLETED'
    repeated_result=client.get(f"/api/v1/instruments/XAUUSD/monte-carlo/runs/{rerun['id']}").json()['result']
    original_result=client.get(f"/api/v1/instruments/XAUUSD/monte-carlo/runs/{mc.json()['runs'][0]['id']}").json()['result']
    assert repeated_result['per_simulation']==original_result['per_simulation']
    hist=client.get('/api/v1/instruments/XAUUSD/monte-carlo/runs').json()['runs']
    assert len([r for r in hist if r['node_id']==node_id])>=2

    # Exercise actual simultaneous API requests while holding the real worker
    # dispatch target so both requests overlap in the active-task window.
    from app.api.routers import monte_carlo as mc_router
    real_worker=mc_router._mc_worker
    gate=threading.Event()
    monkeypatch.setattr(mc_router,"_mc_worker",lambda *args,**kwargs: gate.wait(2))
    def request(seed,idem):
        return client.post('/api/v1/instruments/XAUUSD/monte-carlo/runs',json={"node_ids":[node_id],"methods":["RESHUFFLE"],"idempotency_key":idem,"config":{"simulations":100,"random_points":5,"displayed_curves":1,"initial_capital":10000,"risk_pct":1,"seed":seed}}).json()['runs'][0]
    with ThreadPoolExecutor(max_workers=4) as pool:
        same=list(pool.map(lambda _:request(771,"same-active-request"),range(2)))
        different=list(pool.map(lambda seed:request(seed,"different-config"),[772,773]))
    gate.set()
    assert same[0]['task_id']==same[1]['task_id'] and same[0]['id']==same[1]['id']
    assert different[0]['task_id']!=different[1]['task_id'] and different[0]['id']!=different[1]['id']

    # Real two-node execution after restoring the production worker.
    monkeypatch.setattr(mc_router,"_mc_worker",real_worker)
    spec2=cross_spec(); spec2['identity']['name']='MC Concurrent Node B'; spec2['identity']['tags']=['mc-concurrent-b']
    created2=client.post('/api/v1/instruments/XAUUSD/nodes',json={'name':'MC Concurrent Node B','origin':'CUSTOM','spec_json':json.dumps(spec2)})
    assert created2.status_code in (200,201),created2.text
    node_b=created2.json()['id']
    dt2=client.post('/api/v1/instruments/XAUUSD/deeptesting/runs',json={'node_ids':[node_b],'config':{'mode':'initial','range':{'start':start,'end':end},'initial_capital':10000,'risk_mode':'percent','risk_value':1,'risk_basis':'initial_capital','max_active_trades':1,'spread_mode':'no_spread','tick_size':0.1,'tick_value_account':1,'volume_min':0.01,'volume_max':100,'volume_step':0.01}})
    assert dt2.status_code==200 and dt2.json()['runs'][0]['status']=='DONE'
    # Gate the real simulator only at persisted batch callbacks for deterministic overlap.
    real_simulate=mc_router.simulate
    first_batch={}; first_release={}; second_batch={}; second_release={}; ev_lock=threading.Lock()
    def get_event(mapping,key):
        with ev_lock: return mapping.setdefault(key,threading.Event())
    def controlled_simulate(trades,config,method,**kwargs):
        task_id=config['task_id']; real_callback=kwargs['on_batch']
        def controlled_callback(done,metrics,paths):
            real_callback(done,metrics,paths)
            if done==10:
                get_event(first_batch,task_id).set(); get_event(first_release,task_id).wait(20)
            if done==20:
                get_event(second_batch,task_id).set(); get_event(second_release,task_id).wait(20)
        kwargs['on_batch']=controlled_callback
        return real_simulate(trades,config,method,**kwargs)
    monkeypatch.setattr(mc_router,'simulate',controlled_simulate)
    long_cfg={'simulations':5000,'random_points':2,'displayed_curves':1,'initial_capital':10000,'risk_pct':1,'seed':904,'batch_size':10}
    run_a=client.post('/api/v1/instruments/XAUUSD/monte-carlo/runs',json={'node_ids':[node_id],'methods':['RESHUFFLE'],'config':long_cfg}).json()['runs'][0]
    run_b=client.post('/api/v1/instruments/XAUUSD/monte-carlo/runs',json={'node_ids':[node_b],'methods':['RESHUFFLE'],'config':long_cfg}).json()['runs'][0]
    assert run_a['task_id']!=run_b['task_id'] and run_a['id']!=run_b['id']
    # Wait for both workers to persist batch one; no progress is manufactured.
    assert get_event(first_batch,run_a['task_id']).wait(20)
    assert get_event(first_batch,run_b['task_id']).wait(20)
    task_a=client.get(f"/api/v1/tasks/{run_a['task_id']}").json()
    task_b=client.get(f"/api/v1/tasks/{run_b['task_id']}").json()
    assert task_a['completed_units']==10 and task_b['completed_units']==10
    assert client.post(f"/api/v1/instruments/XAUUSD/monte-carlo/tasks/{run_a['task_id']}/cancel").json()['cancel_requested']
    get_event(first_release,run_a['task_id']).set()
    for _ in range(300):
        task_a=client.get(f"/api/v1/tasks/{run_a['task_id']}").json()
        if task_a['status']=='CANCELLED': break
        time.sleep(.01)
    assert task_a['status']=='CANCELLED' and task_a['completed_units']==10,task_a
    # Node B is held at its own first persisted batch: cancellation of A did not touch it.
    task_b=client.get(f"/api/v1/tasks/{run_b['task_id']}").json()
    assert task_b['status']=='RUNNING' and task_b['completed_units']==10
    get_event(first_release,run_b['task_id']).set()
    assert get_event(second_batch,run_b['task_id']).wait(20)
    task_b=client.get(f"/api/v1/tasks/{run_b['task_id']}").json()
    assert task_b['completed_units']==20 and task_b['status']=='RUNNING'
    get_event(second_release,run_b['task_id']).set()
    for _ in range(2000):
        task_b=client.get(f"/api/v1/tasks/{run_b['task_id']}").json()
        if task_b['status'] in ('COMPLETED','FAILED','CANCELLED'): break
        time.sleep(.01)
    assert task_b['status']=='COMPLETED',task_b
    part_a=client.get(f"/api/v1/instruments/XAUUSD/monte-carlo/runs/{run_a['id']}").json()
    final_b=client.get(f"/api/v1/instruments/XAUUSD/monte-carlo/runs/{run_b['id']}").json()
    assert part_a['status']=='CANCELLED' and part_a['result']['partial'] is True and part_a['result']['summary']['simulations_completed']>0
    assert part_a['result']['summary']['mean_return_pct'] is not None
    assert final_b['status']=='DONE' and final_b['result']['summary']['simulations_completed']==5000

    # A genuine worker exception must be persisted and visible on both task and run.
    def explode(*_args,**_kwargs): raise RuntimeError('acceptance worker fault')
    monkeypatch.setattr(mc_router,'simulate',explode)
    failed=client.post('/api/v1/instruments/XAUUSD/monte-carlo/runs',json={'node_ids':[node_id],'methods':['RESHUFFLE'],'config':{'simulations':10,'random_points':2,'displayed_curves':1,'initial_capital':10000,'risk_pct':1,'seed':905}}).json()['runs'][0]
    for _ in range(300):
        failed_task=client.get(f"/api/v1/tasks/{failed['task_id']}").json()
        if failed_task['status'] in ('FAILED','COMPLETED','CANCELLED'): break
        time.sleep(.01)
    failed_run=client.get(f"/api/v1/instruments/XAUUSD/monte-carlo/runs/{failed['id']}").json()
    assert failed_task['status']=='FAILED' and 'acceptance worker fault' in failed_task['error']
    assert failed_run['status']=='FAILED' and 'acceptance worker fault' in failed_run['error']
    # A completed study stores PF/deviation aggregates against its source-run baseline.
    assert original_result['summary']['deep_baseline_return_pct']==pytest.approx(run['metrics']['return_on_initial_capital_pct'])
    assert 'profit_factor_mean' in original_result['summary']
    assert 'profit_factor_worst' in original_result['summary']
    assert original_result['summary']['mean_deviation_from_baseline_pct'] is not None
    assert original_result['summary']['worst_deviation_from_baseline_pct'] is not None
    deleted=client.delete(f"/api/v1/instruments/XAUUSD/monte-carlo/runs/{failed['id']}")
    assert deleted.status_code==200 and deleted.json()['deleted'] is True
    assert client.get(f"/api/v1/instruments/XAUUSD/deeptesting/runs/{run['id']}").status_code==200
    assert any(n['id']==node_id for n in client.get('/api/v1/instruments/XAUUSD/nodes').json()['items'])
    assert client.get(f"/api/v1/instruments/XAUUSD/monte-carlo/runs/{rerun['id']}").status_code==200
