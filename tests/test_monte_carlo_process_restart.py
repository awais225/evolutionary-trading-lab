"""Process-boundary recovery: deliberately kill a running API process."""
import json, os, socket, subprocess, sys, time, urllib.request
from pathlib import Path

import pytest
from hero1_storage.init import initialize_databases
from hero1_market_data import store as md
from test_phase3_engine import CLOSES, candles_from_closes, cross_spec


def _free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); return sock.getsockname()[1]


def _request(base,path,method='GET',payload=None,timeout=3):
    data=None if payload is None else json.dumps(payload).encode()
    req=urllib.request.Request(base+path,data=data,headers={'Content-Type':'application/json'},method=method)
    with urllib.request.urlopen(req,timeout=timeout) as response:
        return json.loads(response.read())


def _spawn(root,port):
    env=os.environ.copy(); repo=Path(__file__).resolve().parents[1]
    env['HERO1_DATA_ROOT']=str(root); env['HERO1_REPO_ROOT']=str(repo)
    paths=['services/api','packages/storage/src','packages/eventbus/src','packages/ai_orchestrator/src','packages/mt5_adapter/src','packages/market_data/src','packages/exec_semantics/src','packages/strategy_spec/src','packages/indicators/src','packages/research/src']
    env['PYTHONPATH']=os.pathsep.join(str(repo/p) for p in paths)+os.pathsep+env.get('PYTHONPATH','')
    log_file=(Path(root).parent/'hero1-api-test.log').open('ab')
    proc=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port',str(port)],cwd=repo,env=env,stdout=log_file,stderr=subprocess.STDOUT)
    proc._test_log_file=log_file
    return proc


def _wait_health(process,base,timeout=35):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if process.poll() is not None: raise AssertionError(f"HERO1 process exited during startup: {process.returncode}")
        try:
            if _request(base,'/health').get('status')=='ok': return
        except Exception: pass
        time.sleep(.05)
    raise AssertionError('HERO1 process did not become healthy')


def test_real_process_kill_restart_preserves_partial_and_allows_new_run(tmp_path):
    root=tmp_path/'hero-data'; initialize_databases(root,create=True,instruments=('XAUUSD',))
    store_path=root/'MARKET_DATA'/'market_data.db'; store_path.parent.mkdir(parents=True,exist_ok=True)
    conn=md.connect_store(store_path); rows=candles_from_closes(CLOSES)
    with conn:
        md.merge_candles(conn,dataset_id='process-restart-test',instrument='XAUUSD',timeframe='H1',rows=[{**r,'ts_iso':md.ts_iso_of(r['ts']),'tick_volume':100,'spread':0} for r in rows],policy='import')
    conn.close()
    port=_free_port(); base=f'http://127.0.0.1:{port}'; proc=_spawn(root,port)
    try:
        _wait_health(proc,base)
        spec=cross_spec();spec['identity']['name']='Process restart Monte Carlo node';spec['identity']['tags']=['process-restart-mc']
        node=_request(base,'/api/v1/instruments/XAUUSD/nodes','POST',{'name':spec['identity']['name'],'origin':'CUSTOM','spec_json':json.dumps(spec)})['id']
        dt=_request(base,'/api/v1/instruments/XAUUSD/deeptesting/runs','POST',{'node_ids':[node],'config':{'mode':'initial','range':{'start':rows[0]['ts'],'end':rows[-1]['ts']},'initial_capital':10000,'risk_mode':'percent','risk_value':1,'risk_basis':'initial_capital','max_active_trades':1,'spread_mode':'no_spread','tick_size':0.1,'tick_value_account':1,'volume_min':0.01,'volume_max':100,'volume_step':0.01}})['runs'][0]
        assert dt['status']=='DONE',dt
        started=_request(base,'/api/v1/instruments/XAUUSD/monte-carlo/runs','POST',{'node_ids':[node],'methods':['RESHUFFLE'],'config':{'simulations':5000,'random_points':2,'displayed_curves':1,'initial_capital':10000,'risk_pct':1,'seed':885,'batch_size':25}})['runs'][0]
        task_id=started['task_id']; run_id=started['id']
        deadline=time.monotonic()+30; progress=None
        while time.monotonic()<deadline:
            task=_request(base,f'/api/v1/tasks/{task_id}')
            if task['status']=='RUNNING' and task['completed_units']>0:
                detail=_request(base,f'/api/v1/instruments/XAUUSD/monte-carlo/runs/{run_id}')
                if detail['result'] and detail['result']['summary']['simulations_completed']>0:
                    progress=task['completed_units']; break
            if task['status'] in ('FAILED','COMPLETED','CANCELLED'): break
            time.sleep(.05)
        assert progress and progress<5000,task
        proc.kill();proc.wait(timeout=10)  # abrupt OS process death; no graceful worker completion
        proc=_spawn(root,port);_wait_health(proc,base)
        recovered=_request(base,f'/api/v1/tasks/{task_id}')
        assert recovered['status']=='FAILED'
        assert 'Interrupted by application restart' in recovered['error']
        detail=_request(base,f'/api/v1/instruments/XAUUSD/monte-carlo/runs/{run_id}')
        assert detail['status']=='FAILED' and detail['result']['partial'] is True
        done=detail['result']['summary']['simulations_completed']
        assert 0<done<5000 and len(detail['result']['per_simulation'])==done
        new=_request(base,'/api/v1/instruments/XAUUSD/monte-carlo/runs','POST',{'node_ids':[node],'methods':['RESHUFFLE'],'config':{'simulations':10,'random_points':2,'displayed_curves':1,'initial_capital':10000,'risk_pct':1,'seed':886,'batch_size':5}})['runs'][0]
        assert new['id']!=run_id and new['task_id']!=task_id
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            new_task=_request(base,f'/api/v1/tasks/{new["task_id"]}')
            if new_task['status'] in ('COMPLETED','FAILED','CANCELLED'): break
            time.sleep(.05)
        assert new_task['status']=='COMPLETED',new_task
        # Interrupted record is still independently reopenable and unchanged.
        after=_request(base,f'/api/v1/instruments/XAUUSD/monte-carlo/runs/{run_id}')
        assert after['status']=='FAILED' and after['result']['summary']['simulations_completed']==done
        # Exercise the dedicated orderly shutdown endpoint in this disposable child process.
        stopped=_request(base,'/api/v1/system/shutdown','POST',{'confirm':'SHUTDOWN HERO1','timeout_seconds':10})
        assert stopped['shutdown_requested'] and stopped['worker_count']==0
        proc.wait(timeout=10)
        assert proc.returncode in (0,-15)
        shutdown_log=(Path(root).parent/'hero1-api-test.log').read_text(errors='replace')
        assert 'hero1-api shutting down' in shutdown_log,shutdown_log[-3000:]
    finally:
        if proc.poll() is None: proc.kill()
        proc.wait(timeout=10)
