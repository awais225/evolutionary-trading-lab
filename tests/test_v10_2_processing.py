"""V10.2 correctness ONLY. Synthetic deterministic bars are labelled fixtures,
never evidence of real-data throughput. Run in a staged TEMP source tree.
No generation campaign, broker orders or authoritative database is used.
"""
import copy
import csv
import importlib.util
import io
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def database(tmp_path, monkeypatch):
    from app.db import database as D
    d=D.Database(str(tmp_path/'regression.db'))
    monkeypatch.setattr(D,'_db',d)
    yield d
    from app.deeptest.scenarios import _executor
    from app.deeptest.parallel import shutdown
    _executor.shutdown();shutdown()
    d.close()


@pytest.fixture
def configuration(monkeypatch):
    import app.config as C
    cfg=copy.deepcopy(C.get_config())
    cfg.resources.deep_test_workers=1
    cfg.resources.cpu_limit_enabled=False
    cfg.resources.cpu_workers_max=2
    cfg.resources.memory_limit_enabled=False
    monkeypatch.setattr(C,'_config',cfg)
    return cfg


def bars(count=6200, step=60):
    # Pure regression fixture, not sampled or fabricated broker history.
    ts=1783339200. + np.arange(count,dtype=float)*step
    dates=pd.to_datetime(ts,unit='s',utc=True)
    return pd.DataFrame(dict(ts=ts,open=1.2,high=1.201,low=1.199,close=1.2,
                             bid=1.19995,ask=1.20005,spread=10.,tick_volume=100.,volume=100.,
                             session='london',dow=dates.dayofweek,dom=dates.day,hour=dates.hour,minute=dates.minute))


def genome(timeframe='M1',hold=1):
    return dict(symbol='EURUSD',timeframe=timeframe,direction='long',
                entry_long=dict(type='compare',left='close',cmp='>',right=0),
                exit=dict(atr_spec='atr:14',sl_atr_mult=0.,tp_atr_mult=0.,max_hold_bars=hold))


def request(frame, *, max_trades=None, active=0, timeframe='M1'):
    from app.backtest.engine import BacktestRequest
    return BacktestRequest(genome(timeframe),dataset_id='SYNTHETIC-REGRESSION-'+timeframe,
                           stage='oos',window=(0,len(frame)),max_trades=max_trades,
                           initial_balance=1e8,risk_mode='AMOUNT',risk_amount=100.,max_active_trades=active)


def actual(req, frame, path, **kwargs):
    from app.deeptest.runtime import scratch_backtest
    return scratch_backtest(req,frame,path,lambda *args:None,**kwargs)


def metrics_without_observation(metrics):
    return {k:v for k,v in metrics.items() if k not in ('runtime_ms','bars_processed','eligible_bars',
             'actual_start_ts','actual_end_ts','completed_trades','trade_limit','execution_policy','worker_execution')}


@pytest.mark.parametrize('ceiling',[None,0])
def test_real_engine_more_than_3000_no_thinning_or_stop(tmp_path,configuration,ceiling):
    frame=bars();res=actual(request(frame,max_trades=ceiling),frame,tmp_path)
    assert res.ok,res.error
    assert len(res.trades)>6000 and res.metrics['trades']==len(res.trades)
    assert res.metrics['bars_processed']==len(frame)
    assert res.trades[-1]['exit_ts']==frame.ts.iloc[-1]
    assert res.trades[3000]['entry_ts']>res.trades[2999]['entry_ts']
    assert res.trades[0]['entry_ts']>frame.ts.iloc[0]  # real ATR warmup, never trades before ready


@pytest.mark.parametrize('exit_case',['hold','sl_tp','trailing','short'])
@pytest.mark.parametrize('active',[0,1,2,7])
def test_exact_risk_exit_and_heap_equivalence_to_frozen_engine(tmp_path,configuration,active,exit_case):
    from app.backtest.engine import Backtester
    from app.features.engine import FeatureEngine
    from app.features import library
    spec=importlib.util.spec_from_file_location('app.backtest._v102_reference',Path(__file__).parent/'fixtures/v102_backtest_reference.py')
    reference=importlib.util.module_from_spec(spec);sys.modules[spec.name]=reference;spec.loader.exec_module(reference)
    frame=bars(1100);req=request(frame,max_trades=100000,active=active);req.genome['exit']['max_hold_bars']=7
    if exit_case in ('sl_tp','trailing'):req.genome['exit'].update(sl_atr_mult=.3,tp_atr_mult=.3)
    if exit_case=='trailing':req.genome['exit']['trailing']={'activation_mult':0.,'atr_mult':.2}
    if exit_case=='short':
        req.genome['direction']='short';req.genome['entry_short']=req.genome.pop('entry_long')
    class Features(FeatureEngine):
        def _load_disk_cache(self,*args,**kwargs):pass
        def _compute_spec(self,df,ident,spec):
            fn,args=library.parse_spec(spec);out=fn(df,*args)
            for name,array in out.items():self._put((ident,name),np.asarray(array,dtype=float))
            return out
    data=SimpleNamespace(get_frame=lambda _:frame);features=Features(data_engine=data)
    old=reference.Backtester.__new__(reference.Backtester);old._data=data;old._feat=features
    new=Backtester(data_engine=data,feature_engine=features,configuration=configuration)
    a=old.run(req);b=new.run(req)
    assert a.ok and b.ok
    pd.testing.assert_frame_equal(pd.DataFrame(a.trades),pd.DataFrame(b.trades),check_exact=True)
    assert a.equity_curve==b.equity_curve
    assert metrics_without_observation(a.metrics)==metrics_without_observation(b.metrics)


def test_unlimited_matches_sufficient_explicit_bound(tmp_path,configuration):
    frame=bars();a=actual(request(frame),frame,tmp_path);b=actual(request(frame,max_trades=100000),frame,tmp_path)
    pd.testing.assert_frame_equal(pd.DataFrame(a.trades),pd.DataFrame(b.trades),check_exact=True)
    assert a.equity_curve==b.equity_curve


def test_complete_persistence_http_pagination_summary_export(tmp_path,database,configuration):
    from app.deeptest import store as S,scenarios as SC,analytics as A
    from app.api import routes as R
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    frame=bars();res=actual(request(frame),frame,tmp_path);assert res.ok
    trades=SC._normalize_trades(res.trades,{})
    S.insert_scenario(database,dict(run_id='v102-complete',node_id=1,status='RUNNING',metrics=res.metrics))
    S.insert_trades(database,'v102-complete',trades)
    summary=A.summarize(trades,initial_capital=1e8)
    S.update_scenario(database,'v102-complete',status='COMPLETED',analytics=summary)
    app=FastAPI();app.include_router(R.router)
    with TestClient(app) as client:
        total=len(trades)
        detail=client.get('/api/deeptest/run/v102-complete').json()
        assert detail['scenario']['metrics']['trades']==detail['scenario']['analytics']['summary']['trades']==total
        after=client.get('/api/deeptest/run/v102-complete/trades?offset=3000&limit=500').json()
        assert after['total']==total and after['trades'][0]['trade_no']==3001
        complete=[]
        for offset in range(0,total,500):
            page=client.get(f'/api/deeptest/run/v102-complete/trades?offset={offset}&limit=500').json()
            assert page['total']==total;complete+=page['trades']
        assert len(complete)==total and [x['trade_no'] for x in complete]==list(range(1,total+1))
        exported=client.get('/api/deeptest/run/v102-complete/export.csv')
        assert exported.status_code==200 and int(exported.headers['X-Trade-Count'])==total
        csv_rows=list(csv.DictReader(io.StringIO(exported.text)))
        assert len(csv_rows)==total and int(csv_rows[3000]['trade_no'])==3001
        assert int(csv_rows[-1]['trade_no'])==total
        assert S.list_trades(database,'v102-complete',limit=1)['total']==total


def test_only_complete_results_exportable(database):
    from app.api.routes import deeptest_run_export
    from fastapi import HTTPException
    from app.deeptest import store as S
    S.insert_scenario(database,dict(run_id='incomplete',node_id=1,status='CANCELLED'))
    with pytest.raises(HTTPException) as exc:deeptest_run_export('incomplete')
    assert exc.value.status_code==409


@pytest.mark.parametrize('step,timeframe',[(60,'M1'),(300,'M5'),(3600,'H1')])
def test_exact_window_timeframe_and_warmup(tmp_path,configuration,step,timeframe):
    frame=bars(1100,step);req=request(frame,timeframe=timeframe);req.window=(100,900)
    result=actual(req,frame,tmp_path)
    assert result.ok and result.metrics['window']==[100,900]
    assert result.metrics['bars_processed']==800
    assert result.metrics['actual_start_ts']==frame.ts.iloc[100]
    assert result.metrics['actual_end_ts']==frame.ts.iloc[899]
    assert all(frame.ts.iloc[100]<t['entry_ts']<=t['exit_ts']<=frame.ts.iloc[899] for t in result.trades)


def test_actual_engine_cancellation_not_success(tmp_path,configuration):
    frame=bars();result=actual(request(frame),frame,tmp_path,cancelled=lambda:True)
    assert not result.ok and 'cancel' in result.error


def test_persistence_cancellation_preserves_partial_evidence(database):
    from app.deeptest import store as S
    from concurrent.futures import CancelledError
    values=[{'net_pnl':1.,'entry_ts':1.,'exit_ts':2.}]*4;calls=[]
    def cancel():calls.append(1);return len(calls)>2
    with pytest.raises(CancelledError):S.insert_trades(database,'cancel-fixture',values,cancelled=cancel)
    assert S.list_trades(database,'cancel-fixture')['total']==2


def test_schema_cache_no_ddl_commits_and_external_schema_invalidation(database):
    from app.deeptest import store as S
    S.ensure_schema(database)
    assert 'signature' in [r['name'] for r in database.q('PRAGMA table_info(deeptest_scenarios)')]
    traced=[];database.conn.set_trace_callback(traced.append)
    for _ in range(4):S.list_trades(database,'none')
    assert not any(x.upper().startswith(('CREATE','ALTER','COMMIT','BEGIN')) for x in traced)
    database.x('DROP INDEX idx_dt_trades_run');S.ensure_schema(database)
    assert database.one("SELECT 1 present FROM sqlite_master WHERE name='idx_dt_trades_run'")


def test_revision_skip_does_not_change_generic_metadata(database):
    database.set_meta('unrelated','same');database.set_meta('unrelated','same')
    assert database.set_observed_node_revision('r1') is True
    before=database.conn.total_changes
    traced=[];database.conn.set_trace_callback(traced.append)
    assert database.set_observed_node_revision('r1') is False
    assert database.conn.total_changes==before
    assert not any(x.upper().startswith(('COMMIT','INSERT','BEGIN')) for x in traced)
    assert database.set_observed_node_revision('r2') is True
    assert database.get_meta('node_data_revision')=='r2'


def test_scalar_duplicate_predicate_matches_full_row(database):
    database.insert_strategy(dict(hash='fixture-hash',genome=genome(),symbol='EURUSD',timeframe='M1',run_id='FIXTURE',data_source='USER_RESEARCH'))
    for value in ('fixture-hash','absent'):
        assert database.strategy_hash_exists(value)==bool(database.find_by_hash(value))


def test_one_population_snapshot_per_computation(database,monkeypatch):
    from app.evolution.engine import EvolutionEngine
    from app.research import population_state as P
    original=P.snapshot;calls=[]
    def observed(*args,**kwargs):calls.append(1);return original(*args,**kwargs)
    monkeypatch.setattr(P,'snapshot',observed)
    engine=EvolutionEngine(database,run_id='FIXTURE')
    state=engine.get_node_generation_state()
    assert len(calls)==1
    assert state['node_accounting']['TOTAL CREATED']==state['population_snapshot']['current_nodes']


def test_deep_version_prevents_reusing_old_capped_results():
    from app.deeptest.scenarios import _material_engine_version
    assert _material_engine_version().endswith(':data-bounded-v102')


def test_process_result_matches_actual_serial_engine(tmp_path,database,configuration):
    from app.deeptest.parallel import execute,worker_limit
    from app.deeptest.runtime import Session
    configuration.resources.deep_test_workers=2
    if worker_limit()<2:pytest.skip('OS/resource CPU capacity is below two; no forced workers')
    frame=bars();req=request(frame)
    serial=actual(req,frame,tmp_path)
    with Session('dt-v102-process-fixture') as session:
        process=execute(req,frame,session,lambda:False,lambda *args:None)
    assert process.ok and serial.ok
    pd.testing.assert_frame_equal(pd.DataFrame(process.trades),pd.DataFrame(serial.trades),check_exact=True)
    assert process.equity_curve==serial.equity_curve
    assert metrics_without_observation(process.metrics)==metrics_without_observation(serial.metrics)
    assert process.metrics['worker_execution']['worker_pid'] != __import__('os').getpid()


def test_defaults_serial_and_invalid_worker_configuration(configuration):
    from app.deeptest.parallel import worker_limit
    assert worker_limit()==1
    for value in (0,-1,65,1.5,True):
        configuration.resources.deep_test_workers=value
        with pytest.raises(ValueError):worker_limit()


def test_bounded_controller_queue_and_true_concurrency(configuration):
    from app.deeptest.parallel import ScenarioExecutor,worker_limit
    configuration.resources.deep_test_workers=2
    if worker_limit()<2:pytest.skip('OS/resource CPU capacity is below two')
    gate=threading.Event();entered=threading.Barrier(3);executor=ScenarioExecutor()
    def job():entered.wait(timeout=5);gate.wait(timeout=5);return 1
    futures=[executor.submit(job) for _ in range(2)]
    entered.wait(timeout=5)
    queued=[executor.submit(lambda:2) for _ in range(2)]
    with pytest.raises(RuntimeError,match='queue full'):executor.submit(lambda:3)
    gate.set();assert [f.result(timeout=5) for f in futures+queued]==[1,1,2,2]
    executor.shutdown()


def test_ui_uses_complete_count_and_accessible_export():
    root=Path(__file__).parents[1]/'frontend/src'
    source=(root/'components/DeepScenarioPanel.jsx').read_text()
    assert 'while(all.length<(tr.total??all.length))' in source
    assert 'export.csv' in source and 'all.length !== tr.total' in source
    assert 'summary.trades' in source
    deep=(root/'pages/DeepBacktest.jsx').read_text()
    assert 'max_trades: null' in deep and 'All eligible bars' in deep

@pytest.mark.parametrize('timeframe,step,count',[('M1',60,6200),('M5',300,950)])
def test_complete_scenario_pipeline_real_store_engine_db_api(tmp_path,database,configuration,timeframe,step,count):
    """Actual start/preflight/executor/engine/storage path; only input bars synthetic.
    No mocked strategy, BacktestResult, data-reader, status or persistence method.
    """
    from app.data.master import get_master
    from app.mt5.factory import get_bridge
    from app.deeptest import scenarios as SC,store as S
    frame=bars(count,step)
    store=get_master('EURUSD',timeframe,get_bridge().profile())
    store.append(frame,source_info={'source':'DETERMINISTIC_SYNTHETIC_REGRESSION_ONLY'})
    ident=database.insert_strategy(dict(hash='full-path-'+timeframe,genome=genome(timeframe),
                  symbol='EURUSD',timeframe=timeframe,status='BORN',run_id='SYNTHETIC_FIXTURE',data_source='USER_RESEARCH'))
    payload=dict(node_id=ident,requested_start=float(frame.ts.iloc[0]),requested_end=float(frame.ts.iloc[-1])+step,
                 settings={'total_capital':1e8,'risk_pct':1.,'max_active_trades_per_node':0},
                 node_settings={'risk_mode':'AMOUNT','risk_amount':100.,'max_trades':3000})
    pf=SC.preflight(database,node_id=ident,requested_start=payload['requested_start'],requested_end=payload['requested_end'],
                     settings=payload['settings'],node_settings=payload['node_settings'])
    assert pf['ok'],pf
    if pf['needs_confirmation']:
        SC.record_decision(database,confirmation_fingerprint=pf['coverage']['confirmation_fingerprint'],decision='YES_PROCEED',node_id=ident)
    response=SC.start_run(database,payload);assert response['ok'],response
    run=response['run_id'];deadline=time.monotonic()+45
    while time.monotonic()<deadline:
        scenario=S.get_scenario(database,run)
        if scenario['status'] in SC.TERMINAL:break
        time.sleep(.02)
    assert scenario['status'] in ('COMPLETED','PARTIAL'),scenario
    assert scenario['timeframe']==timeframe
    assert scenario['bars_used']==count
    assert scenario['metrics']['bars_processed']==count
    assert scenario['actual_start']==frame.ts.iloc[0] and scenario['actual_end']==frame.ts.iloc[-1]
    saved=S.list_trades(database,run,limit=500,offset=3000 if count>3000 else 0)
    assert saved['total']==scenario['metrics']['trades']==scenario['analytics']['summary']['trades']
    if count>3000:
        assert saved['total']>6000 and saved['trades'][0]['trade_no']==3001
    else:assert saved['total']<count
    from app.api import routes as R
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app=FastAPI();app.include_router(R.router)
    with TestClient(app) as client:
        actual=client.get(f'/api/deeptest/run/{run}').json()['scenario']
        total=saved['total']
        assert actual['metrics']['trades']==actual['analytics']['summary']['trades']==total
        offset=3000 if total>3000 else 0
        page=client.get(f'/api/deeptest/run/{run}/trades?offset={offset}&limit=500').json()
        assert page['total']==total and page['trades'][0]['trade_no']==offset+1
        exported=client.get(f'/api/deeptest/run/{run}/export.csv')
        rows=list(csv.DictReader(io.StringIO(exported.text)))
        assert int(exported.headers['X-Trade-Count'])==len(rows)==total
        assert rows[-1]['symbol']=='EURUSD' and int(rows[-1]['trade_no'])==total
    assert SC.start_run(database,{**payload,'timeframe':'H4'})['ok'] is False


def test_bounded_optin_instrumentation_does_not_log_payload(database,configuration,monkeypatch):
    import os,tempfile
    from app.performance import capture
    root=Path(tempfile.gettempdir())/'EvolutionaryTradingLab/V10.2/traces'
    before=set(root.glob('*.json')) if root.exists() else set()
    monkeypatch.setenv('LAB_V102_TRACE','1')
    with capture('regression') as trace:
        database.set_observed_node_revision('regression-stamp')
        for _ in range(150):database.one('SELECT 1 AS value')
        trace.count('regression.completed')
    added=set(root.glob('*.json'))-before;assert len(added)==1
    report=json.loads(added.pop().read_text())
    assert report['counters']['regression.completed']==1
    assert len(report['phases']['db.query']['samples_seconds'])<=128
    assert report['phases']['db.commit']['count']>=1
    assert 'regression-stamp' not in json.dumps(report)
    assert 'genome' not in report and 'password' not in report


def test_actual_bulk_parallel_processes_complete_independent_nodes(database,configuration):
    from app.data.master import get_master
    from app.mt5.factory import get_bridge
    from app.deeptest import bulk as B,store as S
    from app.deeptest.parallel import worker_limit
    configuration.resources.deep_test_workers=2
    if worker_limit()<2:pytest.skip('OS/resource CPU capacity is below two')
    frame=bars(6200)
    store=get_master('EURUSD','M1',get_bridge().profile())
    store.append(frame,source_info={'source':'SYNTHETIC_REGRESSION_ONLY'})
    ids=[database.insert_strategy(dict(hash='parallel-fixture-'+str(i),genome=genome(),symbol='EURUSD',timeframe='M1',
          run_id='SYNTHETIC_FIXTURE',data_source='USER_RESEARCH')) for i in range(2)]
    job=B.create(database,dict(node_ids=ids,duration_key='',requested_start=float(frame.ts.iloc[0]),
                 requested_end=float(frame.ts.iloc[-1])+60,auto_partial=True,
                 settings={'total_capital':1e8,'risk_pct':1.,'max_active_trades_per_node':0},
                 node_settings={'risk_mode':'AMOUNT','risk_amount':100.}))
    deadline=time.monotonic()+60
    while time.monotonic()<deadline:
        job=B.get(database,job['id'])
        if job['status'] in ('COMPLETED','COMPLETED_WITH_ISSUES','CANCELLED','FAILED','INTERRUPTED'):break
        time.sleep(.03)
    assert job['status'] in ('COMPLETED','COMPLETED_WITH_ISSUES'),job
    assert job['failure']==0 and job['success']==2
    assert [r['node_id'] for r in job['results']]==ids
    for outcome in job['results']:
        run=S.get_scenario(database,outcome['run_id'])
        assert run['metrics']['trades']>6000 and run['bars_used']==6200
        assert S.list_trades(database,outcome['run_id'],offset=3000)['trades'][0]['trade_no']==3001
        assert run['metrics']['worker_execution']['worker_pid']!=__import__('os').getpid()
        assert run['metrics']['worker_execution']['worker_cpu_seconds']>0


def test_rng_genome_and_entropy_call_expressions_unchanged():
    import ast
    from app.evolution import engine
    before=json.loads((Path(__file__).parent/'fixtures/v102_evolution_calls.json').read_text())
    found=[]
    for n in ast.walk(ast.parse(Path(engine.__file__).read_text())):
        if isinstance(n,ast.Call):
            name=ast.unparse(n.func)
            if name.startswith(('self._rng.','random.','gops.')) or name in ('self._tournament_parent','time.time','os.getpid'):
                found.append((n.lineno,n.col_offset,ast.dump(n,include_attributes=False)))
    assert [item[2] for item in sorted(found)]==before['calls']


def test_process_cancellation_cannot_return_success_after_worker_begins(tmp_path,configuration):
    from app.deeptest.parallel import execute,worker_limit
    configuration.resources.deep_test_workers=2
    if worker_limit()<2:pytest.skip('OS/resource CPU capacity is below two')
    frame=bars(50000)
    session=SimpleNamespace(path=tmp_path,update=lambda *a,**k:None)
    # The actual child writes this atomic marker from real engine progress.
    # Cancellation is deliberately possible at compute/aggregation boundary too.
    cancelled=lambda:(tmp_path/'worker-progress.json').exists()
    result=execute(request(frame),frame,session,cancelled,lambda *a:None)
    assert cancelled() and not result.ok and 'cancel' in result.error.lower()


def test_memory_admission_fails_explicitly_not_by_truncating(configuration,monkeypatch):
    from app.deeptest.parallel import reserve_memory
    import psutil
    monkeypatch.setattr(psutil,'virtual_memory',lambda:SimpleNamespace(total=8*1024**3,available=1))
    with pytest.raises(MemoryError,match='memory admission'):
        with reserve_memory(bars(30)):pytest.fail('Insufficient-memory computation admitted')


def test_unreadable_persisted_partition_never_becomes_complete(database,configuration,monkeypatch):
    from app.data.master import get_master
    from app.mt5.factory import get_bridge
    from app.deeptest import scenarios as SC,store as S
    frame=bars(400)
    symbol='V102SYNTHETIC';g=genome();g['symbol']=symbol
    store=get_master(symbol,'M1',get_bridge().profile())
    store.append(frame,source_info={'source':'SYNTHETIC_REGRESSION_ONLY'})
    ident=database.insert_strategy(dict(hash='unreadable-fixture',genome=g,symbol=symbol,timeframe='M1',
              run_id='SYNTHETIC_FIXTURE',data_source='USER_RESEARCH'))
    payload=dict(node_id=ident,requested_start=float(frame.ts.iloc[0]),requested_end=float(frame.ts.iloc[-1])+60)
    pf=SC.preflight(database,**payload);assert pf['ok'],pf
    if pf['needs_confirmation']:
        SC.record_decision(database,confirmation_fingerprint=pf['coverage']['confirmation_fingerprint'],decision='YES_PROCEED',node_id=ident)
    started=threading.Event();release=threading.Event();execute=SC._execute
    def gated(run):
        started.set();assert release.wait(10);execute(run)
    monkeypatch.setattr(SC,'_execute',gated)
    response=SC.start_run(database,payload);assert response['ok'],response
    assert started.wait(5)
    partition=next(store.raw_dir.glob('*.parquet'));original=partition.read_bytes()
    try:
        partition.write_bytes(b'DELIBERATELY_INVALID_SYNTHETIC_TEST_PARTITION')
        release.set();deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            row=S.get_scenario(database,response['run_id'])
            if row['status'] in SC.TERMINAL:break
            time.sleep(.02)
        assert row['status']=='FAILED',row
        assert 'Incomplete persisted history snapshot' in row['error']
        assert S.list_trades(database,response['run_id'])['total']==0
    finally:
        release.set();partition.write_bytes(original)


def test_success_cancel_race_is_sql_atomic_across_connections(database):
    from app.db.database import Database
    from app.deeptest import store as S
    from concurrent.futures import ThreadPoolExecutor
    other=Database(database.path)
    try:
        for i in range(20):
            run='terminal-race-'+str(i)
            S.insert_scenario(database,dict(run_id=run,node_id=1,status='RUNNING'))
            barrier=threading.Barrier(2)
            def complete():barrier.wait();return S.finish_if_running(database,run,'COMPLETED')
            def cancel():barrier.wait();return S.cancel_if_active(other,run)
            with ThreadPoolExecutor(max_workers=2) as pool:
                a=pool.submit(complete);b=pool.submit(cancel);outcomes=[a.result(),b.result()]
            assert sum(outcomes)==1
            row=S.get_scenario(database,run)
            if row['status']=='CANCELLED':
                S.update_scenario(other,run,status='RUNNING')
                assert S.get_scenario(database,run)['status']=='CANCELLED'
                assert not S.finish_if_running(other,run,'COMPLETED')
            else:assert row['status']=='COMPLETED' and not S.cancel_if_active(other,run)
    finally:other.close()


def test_shutdown_blocks_new_admissions_and_cancels_queued_batches(database,configuration,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from app.deeptest import bulk as B,scenarios as SC
    from app.deeptest.parallel import ScenarioExecutor
    pool=ThreadPoolExecutor(max_workers=1);controller=ScenarioExecutor()
    monkeypatch.setattr(B,'_POOL',pool);monkeypatch.setattr(SC,'_executor',controller)
    active=threading.Event();release=threading.Event();executed=[]
    def held(db,ident):
        executed.append(ident);active.set();assert release.wait(10)
        db.x("UPDATE deeptest_batches SET status='CANCELLED' WHERE id=?",(ident,))
        B._ACTIVE.discard(ident)
    monkeypatch.setattr(B,'_prepare',held)
    jobs=[B.create(database,dict(node_ids=[i+1])) for i in range(3)]
    assert active.wait(5)
    stopped=[]
    def stop():B.shutdown();stopped.append(True)
    thread=threading.Thread(target=stop);thread.start()
    deadline=time.monotonic()+5
    while not controller.closing and time.monotonic()<deadline:time.sleep(.01)
    try:
        assert controller.closing
        with pytest.raises(RuntimeError,match='shutting down'):controller.submit(lambda:None)
    finally:release.set();thread.join(10)
    assert not thread.is_alive() and stopped
    assert len(executed)==1
    assert all(B.get(database,j['id'])['status']=='CANCELLED' for j in jobs)


def test_shutdown_stops_live_before_waiting_on_historical_work():
    import ast,inspect
    from app.main import shutdown
    tree=ast.parse(inspect.getsource(shutdown));calls=[]
    for node in ast.walk(tree):
        if isinstance(node,ast.Call):calls.append((node.lineno,ast.unparse(node.func)))
    line=lambda name:min(n for n,fn in calls if fn==name)
    live=min(n for n,fn in calls if fn.endswith('get_live_testing_engine().stop'))
    assert live<line('shutdown_deep_batches')<line('deep_scenarios.shutdown')<line('shutdown_deep_processes')


def test_http_cancel_can_interrupt_real_per_trade_persistence(database,monkeypatch):
    from concurrent.futures import CancelledError
    from app.deeptest import store as S,scenarios as SC
    run='cancel-during-persistence'
    S.insert_scenario(database,dict(run_id=run,node_id=1,status='RUNNING'))
    committed=threading.Event();release=threading.Event();original=database.x
    writes=[];failures=[];responses=[]
    def pause_after_commit(sql,args=()):
        result=original(sql,args)
        if 'INSERT INTO deeptest_trades' in sql:
            writes.append(1)
            if len(writes)==1:committed.set();assert release.wait(10)
        return result
    monkeypatch.setattr(database,'x',pause_after_commit)
    def persist():
        try:S.insert_trades(database,run,[{'net_pnl':1.}]*30,cancelled=lambda:run in SC._CANCELS)
        except BaseException as exc:failures.append(exc)
    writer=threading.Thread(target=persist);writer.start();assert committed.wait(5)
    canceller=threading.Thread(target=lambda:responses.append(SC.cancel_run(database,run)));canceller.start()
    deadline=time.monotonic()+5
    try:
        while run not in SC._CANCELS and time.monotonic()<deadline:time.sleep(.01)
        assert run in SC._CANCELS,'Schema/writer lock prevented cancellation from signalling'
    finally:
        release.set();writer.join(10);canceller.join(10);SC._CANCELS.discard(run)
    assert not writer.is_alive() and not canceller.is_alive()
    assert responses[0]['ok'] and len(failures)==1 and isinstance(failures[0],CancelledError)
    assert len(writes)==1 and S.list_trades(database,run)['total']==1
    assert S.get_scenario(database,run)['status']=='CANCELLED'


def test_backtest_positional_contract_preserved():
    from dataclasses import fields
    from app.backtest.engine import BacktestRequest
    spec=importlib.util.spec_from_file_location('app.backtest._v102_positional_reference',Path(__file__).parent/'fixtures/v102_backtest_reference.py')
    reference=importlib.util.module_from_spec(spec);sys.modules[spec.name]=reference;spec.loader.exec_module(reference)
    assert [f.name for f in fields(BacktestRequest)][:-1]==[f.name for f in fields(reference.BacktestRequest)]
    assert fields(BacktestRequest)[-1].name=='execution_snapshot'
