"""Candidate-only synthetic regression evidence, NOT native Windows certification."""
import asyncio
import copy
import json
import sqlite3
import subprocess
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from pathlib import Path
import pytest
from test_v10_2_processing import database, configuration
from test_v10_5_integrity import saved
from v1052_read_fixture import seed


@pytest.fixture(scope='module')
def large(tmp_path_factory):
    from app.db.database import Database
    db=Database(str(tmp_path_factory.mktemp('v1052-large')/'synthetic.db'))
    ids=seed(db,nodes=64,scenarios_per_node=4,trades_per_scenario=128,population=4000)
    yield db,ids
    db.close()


def test_cold_and_warm_large_listing_never_reads_trades_or_calculates(large,monkeypatch):
    from app.deeptest import listing as L,metric_service as M
    db,ids=large;sql=[]
    def forbidden(*a,**kw):raise AssertionError('A listing must not calculate financial metrics')
    monkeypatch.setattr(M,'calculate',forbidden)
    db.conn.set_trace_callback(sql.append)
    try:
        for _ in range(2):
            result=L.node_summaries(db)
            assert len(result)==64 and sum(r['scenario_count'] for r in result)==256
            assert sum(len(r['scenarios']) for r in result)==256
            assert all(r['preferred']['analytics']['summary']=={} for r in result)
            assert all(r['preferred']['metric_provenance']['availability']=='UNAVAILABLE' for r in result)
        assert not any('deeptest_trades' in q.lower() for q in sql)
        assert not any('select * from deeptest_scenarios' in q.lower() for q in sql)
    finally:db.conn.set_trace_callback(None)


def test_detail_warms_valid_summary_without_redefining_metrics(database):
    from app.deeptest import listing as L,metric_service as M,store as S
    rid,nid=saved(database)
    assert L.node_summaries(database)[0]['preferred']['metrics']=={}
    detail=M.enrich(database,S.get_scenario(database,rid))
    result=L.node_summaries(database)[0]['preferred']
    assert result['metrics']==detail['analytics']['summary']
    assert result['analytics']['summary']['max_drawdown']==270
    assert result['metric_provenance']['trade_snapshot']==detail['metric_provenance']['trade_snapshot']
    assert result['metric_provenance']['availability']=='VERIFIED'
    assert result['status']=='PARTIAL' and result['coverage_status']=='PARTIAL'
    # Caller mutation cannot poison either cache or a later listing response.
    result['metrics']['net_profit']=0
    assert L.node_summaries(database)[0]['preferred']['metrics']['net_profit']==-70
    assert S.get_scenario(database,rid)['analytics']['summary']['max_drawdown_pct']==3.99415


def test_listing_and_index_share_summary_cache_and_preferences(database):
    from app.deeptest import listing as L,metric_service as M,store as S
    from app.stats.deeptest_projection import build_index,effective_for
    older,nid=saved(database,'older');newer,_=saved(database,'newer',nid)
    S.update_scenario(database,older,created_at=1);S.update_scenario(database,newer,created_at=2)
    S.set_pref(database,nid,older,explicit=True)
    detail=M.enrich(database,S.get_scenario(database,older))
    assert L.node_summaries(database)[0]['preferred']['run_id']==older
    listed=L.scenarios(database,node_id=nid)
    assert next(r for r in listed if r['preferred_run_id']==r['run_id'])['run_id']==older
    index=build_index(database,summary_only=True)
    assert index[nid]['preferred']['analytics']['summary']==detail['analytics']['summary']
    assert effective_for(index,nid,{})['metrics']['net_profit']==-70
    # Unchanged durable explicit preference survives a new connection, but
    # a process/connection-local summary proof does not pretend to survive it.
    from app.db.database import Database
    other=Database(database.path)
    try:
        result=L.node_summaries(other)[0]
        assert result['preferred']['run_id']==older
        assert result['preferred']['metric_provenance']['availability']=='UNAVAILABLE'
    finally:other.close()
    S.update_scenario(database,older,status='FAILED')
    assert L.node_summaries(database)[0]['preferred']['run_id']==newer
    assert S.get_pref(database,nid)['preferred_run_id']==older


@pytest.mark.parametrize('mutation',[
    "UPDATE deeptest_trades SET net_pnl=net_pnl+1",
    "DELETE FROM deeptest_trades WHERE trade_no=1",
    "UPDATE deeptest_scenarios SET status='COMPLETED'",
    "UPDATE deeptest_scenarios SET settings='{}'",
    "CREATE TABLE unrelated_schema_change(id INTEGER)",
])
def test_metric_summary_invalidates_after_mutation(database,mutation):
    from app.deeptest import listing as L,metric_service as M,store as S
    rid,_=saved(database);M.enrich(database,S.get_scenario(database,rid))
    assert L.scenarios(database)[0]['metric_provenance']['availability']=='VERIFIED'
    database.x(mutation)
    result=L.scenarios(database)[0]
    assert result['metric_provenance']['availability']=='UNAVAILABLE' and result['analytics']['summary']=={}


def test_partial_missing_and_unknown_capital_remain_na(database):
    from app.deeptest import listing as L,metric_service as M,store as S
    rid,_=saved(database,cap=0);database.x('DELETE FROM deeptest_trades')
    cold=L.scenarios(database)[0]
    assert cold['status']=='PARTIAL' and cold['metrics']=={}
    detail=M.enrich(database,S.get_scenario(database,rid))
    warm=L.scenarios(database)[0]
    assert warm['metrics']['max_drawdown_pct'] is None
    assert warm['metric_provenance']['integrity']=='MISSING_TRADE_RECORDS'
    assert detail['metric_provenance']['integrity']=='MISSING_TRADE_RECORDS'
    assert S.get_scenario(database,rid)['analytics']['summary']['trades']==3


def test_full_trade_access_and_export_are_not_capped(database,monkeypatch):
    from app.api import routes as R
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.deeptest import store as S
    ids=seed(database,nodes=1,scenarios_per_node=1,trades_per_scenario=3501)
    app=FastAPI();app.include_router(R.router)
    with TestClient(app) as client:
        listed=client.get('/api/deeptest/scenarios').json()
        assert listed['count']==1 and listed['scenarios'][0]['metric_provenance']['availability']=='UNAVAILABLE'
        detail=client.get('/api/deeptest/run/'+ids[0]).json()['scenario']
        assert detail['analytics']['summary']['trades']==3501
        page=client.get('/api/deeptest/run/'+ids[0]+'/trades?offset=3000&limit=500').json()
        assert page['total']==3501 and page['trades'][0]['trade_no']==3001
        csv=client.get('/api/deeptest/run/'+ids[0]+'/export.csv')
        assert csv.status_code==200 and csv.headers['X-Trade-Count']=='3501'
        assert len(csv.text.splitlines())==3502
        assert S.list_trades(database,ids[0],offset=3500,limit=100)['trades'][0]['trade_no']==3501


def test_population_reuses_grouped_facts_across_scopes_and_readers(database):
    from app.research.population_state import snapshot
    seed(database,nodes=1,scenarios_per_node=0,population=2000)
    sql=[];database.conn.set_trace_callback(sql.append)
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            values=list(pool.map(lambda i:snapshot(database,run_id='FIXTURE' if i%2 else None,target=3000+i,include_excluded=bool(i%2)),range(24)))
        assert all(v['current_nodes']==2000 for v in values)
        assert len([q for q in sql if 'GROUP BY s.run_id' in q])==1
        values[0]['status_counts']['QUALIFIED']=0
        assert snapshot(database)['status_counts']['QUALIFIED']==2000
    finally:database.conn.set_trace_callback(None)


@pytest.mark.parametrize('mutation',[
    "UPDATE strategies SET status='FAILED'",
    "UPDATE strategies SET generation=8",
    "UPDATE strategies SET run_id='OTHER'",
    "UPDATE strategies SET data_source='LEGACY_TEST'",
    "UPDATE strategies SET status='EXCLUDED'",
    "DELETE FROM strategies",
])
def test_population_invalidates_relevant_mutations(database,mutation):
    from app.research.population_state import snapshot
    seed(database,nodes=1,scenarios_per_node=0)
    before=snapshot(database);database.x(mutation);after=snapshot(database)
    assert before['revision']!=after['revision']
    if 'DELETE' in mutation or "LEGACY_TEST" in mutation or "EXCLUDED" in mutation:assert after['current_nodes']==0
    if "FAILED" in mutation:assert after['dead_nodes']==1
    if 'generation=8' in mutation:assert after['generation_number']==8
    if "OTHER" in mutation:assert snapshot(database,run_id='FIXTURE')['current_nodes']==0


def test_insert_and_external_commit_invalidate_population_and_metric_cache(database):
    from app.research.population_state import snapshot
    from app.deeptest import listing as L,metric_service as M,store as S
    rid,_=saved(database);M.enrich(database,S.get_scenario(database,rid));before=snapshot(database)
    with sqlite3.connect(database.path) as external:
        external.execute("UPDATE strategies SET status='FAILED'")
        external.execute('UPDATE deeptest_trades SET net_pnl=-500 WHERE trade_no=1')
    assert snapshot(database)['dead_nodes']==1
    assert L.scenarios(database)[0]['metric_provenance']['availability']=='UNAVAILABLE'
    database.insert_strategy(dict(hash='new',genome={},symbol='EURUSD',timeframe='M1',status='BORN',run_id='FIXTURE'))
    assert snapshot(database)['current_nodes']==before['current_nodes']+1


def test_rollback_does_not_publish_uncommitted_metric_or_population_proofs(database):
    from app.research.population_state import snapshot
    from app.deeptest import listing as L,metric_service as M,store as S
    rid,_=saved(database);assert snapshot(database)['qualified_nodes']==1
    database.conn.execute('BEGIN')
    database.conn.execute("UPDATE strategies SET status='FAILED'")
    database.conn.execute('UPDATE deeptest_trades SET net_pnl=-500 WHERE trade_no=1')
    assert snapshot(database)['dead_nodes']==1
    uncommitted=M.enrich(database,S.get_scenario(database,rid))
    assert uncommitted['metrics']['net_profit']==-450
    database.conn.rollback()
    assert snapshot(database)['qualified_nodes']==1
    assert L.scenarios(database)[0]['metric_provenance']['availability']=='UNAVAILABLE'
    assert M.enrich(database,S.get_scenario(database,rid))['metrics']['net_profit']==-70


def test_header_and_trades_are_one_snapshot_during_external_write(database,monkeypatch):
    from app.deeptest import metric_service as M,store as S
    rid,_=saved(database)
    original=database.q_rows;changed=False
    def rows(sql,args=()):
        nonlocal changed
        if 'SELECT * FROM deeptest_trades' in sql and not changed:
            changed=True
            with sqlite3.connect(database.path) as external:
                external.execute('UPDATE deeptest_scenarios SET settings=?',(json.dumps({'total_capital':400000,'account_currency':'EUR'}),))
                external.execute('UPDATE deeptest_trades SET net_pnl=-220 WHERE trade_no=1')
        return original(sql,args)
    monkeypatch.setattr(database,'q_rows',rows)
    first=M.enrich(database,S.get_scenario(database,rid))
    assert first['metrics']['initial_capital']==200000 and first['metrics']['net_profit']==-70
    second=M.enrich(database,S.get_scenario(database,rid))
    assert second['metrics']['initial_capital']==400000 and second['metrics']['net_profit']==-170


def test_dictionary_conversion_runs_outside_q_lock(database,monkeypatch):
    from app.db import database as D
    entered=threading.Event();release=threading.Event();real=dict
    def convert(row):
        if row.keys()==['n'] and row['n']==1:
            assert not database._lock._is_owned()
            entered.set();assert release.wait(5)
        return real(row)
    monkeypatch.setattr(D,'dict',convert,raising=False)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(database.q,'SELECT 1 n')
        try:
            assert entered.wait(3)
            assert pool.submit(database.one,'SELECT 2 n').result(timeout=1)=={'n':2}
        finally:release.set()
        assert first.result()==[{'n':1}]


def test_opt_in_diagnostics_bounded_private_and_fault_isolated(database,monkeypatch):
    from app import read_diagnostics as T
    with T.capture() as trace:
        database.q('SELECT ? private_value',('credential-fixture-must-not-appear',))
    data=trace.snapshot()
    assert {'db.lock_wait','db.lock_hold','db.sql_execute','db.fetch','db.materialize'}<=data.keys()
    assert 'credential-fixture' not in json.dumps(data)
    for i in range(300):
        with T.capture(trace):
            with T.phase('fixed'):pass
    assert len(trace.snapshot()['fixed']['samples_seconds'])==128
    for i in range(100):trace.record(str(i),1)
    assert len(trace.snapshot())==64
    monkeypatch.setattr(trace,'record',lambda *a:(_ for _ in ()).throw(RuntimeError('diagnostic only')))
    with T.capture(trace):assert database.one('SELECT 4 n')=={'n':4}


def test_metric_cache_bounds_and_oversize_rejection(database,monkeypatch):
    from app.deeptest import metric_service as M
    with M._LOCK:M._CACHE.pop(database,None)
    for i in range(600):M._cached(database,('fixture',i),lambda i=i:{'n':i})
    assert len(M._CACHE[database])==512
    assert sum(v[1] for v in M._CACHE[database].values())<=M.MAX_BYTES
    monkeypatch.setattr(M,'MAX_BYTES',32)
    M._cached(database,'oversize',lambda:{'value':'x'*100})
    assert 'oversize' not in M._CACHE[database]


@pytest.fixture
def routes(database,configuration,monkeypatch):
    from app.orchestrator import lab as L
    from app.evolution import engine as E
    monkeypatch.setattr(L,'_lab',None);monkeypatch.setattr(E,'_evo_engine',None)
    from app.api import routes as R
    from fastapi import FastAPI
    app=FastAPI();app.include_router(R.router)
    return app


def test_status_status_row_workflow_and_listing_concurrently(database,routes):
    from fastapi.testclient import TestClient
    from app.read_diagnostics import capture,Timings
    seed(database,nodes=24,scenarios_per_node=4,trades_per_scenario=128,population=3000)
    urls=['/api/status','/api/system/status_row','/api/workflow/task_state','/api/workflow/milestones','/api/deeptest/nodes','/api/deeptest/scenarios']
    trace=Timings()
    with TestClient(routes) as client,ThreadPoolExecutor(max_workers=6) as pool:
        def get(url):
            with capture(trace):
                start=time.perf_counter();response=client.get(url)
                return url,response,time.perf_counter()-start
        results=list(pool.map(get,urls*3))
    for url,response,elapsed in results:
        assert response.status_code==200,(url,response.text[:200])
        assert elapsed<15
    assert 'status.total' in trace.snapshot() and 'status_row.total' in trace.snapshot()
    assert 'deeptest.metric_calculation' not in trace.snapshot()


def test_client_deadline_does_not_cancel_sync_worker_or_block_status(database,routes,monkeypatch):
    import httpx
    from app.deeptest import metric_service as M
    rid,_=saved(database);entered=threading.Event();release=threading.Event();finished=threading.Event();real=M.calculate
    def slow(*a,**kw):
        entered.set()
        try:
            assert release.wait(5)
            return real(*a,**kw)
        finally:finished.set()
    monkeypatch.setattr(M,'calculate',slow)
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=routes),base_url='http://synthetic') as client:
            try:
                request=asyncio.create_task(client.get('/api/deeptest/run/'+rid))
                assert await asyncio.to_thread(entered.wait,3)
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(request,timeout=.05)
                assert entered.is_set() and not finished.is_set()
                # Worker is still alive; status must not wait for its analytics.
                response=await asyncio.wait_for(client.get('/api/system/status_row'),timeout=5)
                assert response.status_code==200 and not finished.is_set()
            finally:release.set()
            assert await asyncio.to_thread(finished.wait,3)
    asyncio.run(exercise())


def test_financial_metric_parity_with_exact_v1051_calculator(database):
    from app.deeptest import metric_service as M,store as S
    from app.deeptest import metric_service
    repo=Path(__file__).resolve().parents[1]
    source=subprocess.check_output(['git','show','94dcc68336ee60097dc07ee2e6112cbc42113ea1:backend/app/deeptest/metric_service.py'],cwd=repo,text=True)
    # Trusted pinned baseline source, isolated namespace; only calculate() is
    # called on synthetic rows. No baseline application import or DB bootstrap.
    baseline=types.ModuleType('app.deeptest._pinned_v1051_metric')
    exec(compile(source,'pinned-v1051-metric-service','exec'),baseline.__dict__)
    rid,_=saved(database);sc=S.get_scenario(database,rid)
    def normalize(value):
        value=copy.deepcopy(value)
        value.get('provenance',{}).pop('calculated_at',None)
        return value
    cases=[]
    for cap in (0,None,100,200000):
        for pnls in ([],[0,0],[1,2],[-1,-2],[2,-1,0,-3],[-20000,1]):
            header=copy.deepcopy(sc);header['settings']={'total_capital':cap};header['metrics']={};header['analytics']={}
            trades=[dict(id=i+1,trade_no=i+1,net_pnl=p,entry_ts=1783339200+i,exit_ts=1783339300+i) for i,p in enumerate(pnls)]
            assert normalize(M.calculate(header,trades))==normalize(baseline.calculate(header,trades))
            cases.append((cap,pnls))
    assert len(cases)==24


def test_connection_replacement_invalidates_read_proofs(database):
    from app.deeptest import listing as L,metric_service as M,store as S
    from app.research.population_state import snapshot
    rid,_=saved(database);M.enrich(database,S.get_scenario(database,rid));before=snapshot(database)
    old=database.conn
    with database._lock:
        replacement=sqlite3.connect(database.path,check_same_thread=False)
        replacement.row_factory=sqlite3.Row
        database._conn=replacement
    old.close()
    assert L.scenarios(database)[0]['metric_provenance']['availability']=='UNAVAILABLE'
    assert snapshot(database)['status_counts']==before['status_counts']


def test_population_cache_bound_and_no_reuse_of_oversized_groups(database,monkeypatch):
    from app.research import population_state as P
    seed(database,nodes=1,scenarios_per_node=0)
    monkeypatch.setattr(P,'_MAX_BYTES',1)
    P.snapshot(database)
    assert database not in P._CACHE


def test_read_snapshot_nested_scope_and_exception_release(database):
    from app.research.population_state import snapshot
    seed(database,nodes=1,scenarios_per_node=0)
    with pytest.raises(RuntimeError):
        with database.read_snapshot():
            with database.read_snapshot():
                assert snapshot(database)['current_nodes']==1
                raise RuntimeError('synthetic read interrupted')
    assert not database.conn.in_transaction
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(snapshot,database).result(timeout=2)['current_nodes']==1


def test_external_commit_between_token_components_cannot_mix_cached_data(database,monkeypatch):
    from app.deeptest import listing as L,metric_service as M,store as S
    rid,_=saved(database);M.enrich(database,S.get_scenario(database,rid))
    real=database.one;changed=False
    def one(sql,args=()):
        nonlocal changed
        result=real(sql,args)
        if sql=='PRAGMA data_version' and not changed:
            changed=True
            with sqlite3.connect(database.path) as external:
                external.execute('UPDATE deeptest_trades SET net_pnl=-220 WHERE trade_no=1')
        return result
    monkeypatch.setattr(database,'one',one)
    # This request is linearized before the external commit by its anchored
    # read snapshot, so its old metrics are internally consistent, not stale
    # data for a newer snapshot. The next request must invalidate that proof.
    first=L.scenarios(database)[0]
    assert first['metric_provenance']['availability']=='VERIFIED' and first['metrics']['net_profit']==-70
    assert L.scenarios(database)[0]['metric_provenance']['availability']=='UNAVAILABLE'
    assert M.enrich(database,S.get_scenario(database,rid))['metrics']['net_profit']==-170


def test_incomplete_trade_population_is_not_promoted_to_listing_authority(database):
    from app.deeptest import metric_service as M,store as S,listing as L
    rid,_=saved(database,status='COMPLETED')
    database.x('DELETE FROM deeptest_trades WHERE trade_no=1')
    detail=M.enrich(database,S.get_scenario(database,rid))
    # Detailed metric definition and available-row evidence are unchanged.
    assert detail['metrics']['trades']==2 and detail['metrics']['net_profit']==50
    assert detail['metric_provenance']['trade_count_matches_stored'] is False
    row=L.scenarios(database)[0]
    assert row['metrics']['net_profit'] is None and row['metrics']['trades'] is None
    assert row['metric_provenance']['availability']=='UNAVAILABLE'
    assert row['metric_provenance']['integrity']=='INCOMPLETE_TRADE_RECORDS'
    assert 'Inspect' in row['metric_provenance']['reason']
    assert M.enrich(database,S.get_scenario(database,rid))['metrics']['net_profit']==50


def test_get_db_native_probe_obeys_the_existing_connection_gate(database,monkeypatch):
    from app.db import database as D
    real=database.conn;entered=threading.Event()
    class CheckedConnection:
        def execute(self,sql,*a,**kw):
            assert database._lock._is_owned()
            entered.set()
            return real.execute(sql,*a,**kw)
        def __getattr__(self,name):return getattr(real,name)
    monkeypatch.setattr(database,'_conn',CheckedConnection())
    with ThreadPoolExecutor(max_workers=1) as pool:
        with database._lock:
            future=pool.submit(D.get_db)
            assert not entered.wait(.05),'The native SELECT 1 probe bypassed the database gate'
        assert future.result(timeout=2) is database
        assert entered.is_set()


def test_raw_and_dictionary_reads_both_retain_existing_total_timing(database):
    from app import performance as P,read_diagnostics as T
    trace=T.Timings();token=P._CURRENT.set(trace)
    try:
        assert database.q_rows('SELECT 1 n')[0]['n']==1
        assert database.q('SELECT 2 n')==[{'n':2}]
    finally:P._CURRENT.reset(token)
    # Exactly one legacy total span per public read, including raw reads;
    # no apparent SPEEDLOG improvement caused by dropping instrumentation.
    assert trace.snapshot()['db.read_total']['count']==2


def test_stored_hash_version_counts_and_dataset_are_not_a_freshness_proof(database):
    from app.deeptest import listing as L,metric_service as M,store as S
    rid,_=saved(database)
    original=M.enrich(database,S.get_scenario(database,rid))
    # Even a stored current-method, once-correct complete summary/hash becomes
    # stale after an in-place edit. Count, row IDs, run status and candle-source
    # fingerprint remain unchanged. No migration/new proof columns are used.
    S.update_scenario(database,rid,analytics=original['analytics'],
                      provenance=original['metric_provenance'],dataset_fingerprint='same-candle-source')
    before=database.one('SELECT COUNT(*) n,MAX(id) last FROM deeptest_trades WHERE run_id=?',(rid,))
    database.x('UPDATE deeptest_trades SET net_pnl=net_pnl+13 WHERE run_id=? AND trade_no=1',(rid,))
    assert database.one('SELECT COUNT(*) n,MAX(id) last FROM deeptest_trades WHERE run_id=?',(rid,))==before
    raw=S.get_scenario(database,rid)
    assert raw['analytics']['provenance']['metric_version']==M.VERSION
    sql=[];database.conn.set_trace_callback(sql.append)
    try:
        listed=L.scenarios(database)[0]
        assert listed['metric_provenance']['validation_state']=='HISTORICAL_UNVERIFIED'
        assert listed['metrics']=={}
        assert 'No validation is queued automatically' in listed['metric_provenance']['validation_reason']
        assert not any('deeptest_trades' in q.lower() for q in sql)
    finally:database.conn.set_trace_callback(None)
    current=M.enrich(database,raw)
    assert current['metrics']['net_profit']==original['metrics']['net_profit']+13
    assert current['metric_provenance']['trade_snapshot']!=original['metric_provenance']['trade_snapshot']
    assert L.scenarios(database)[0]['metric_provenance']['validation_state']=='AUTHORITATIVE'


def test_genuine_na_only_after_calculation_and_financial_values_unchanged(database):
    from app.deeptest import listing as L,metric_service as M,store as S
    rid,_=saved(database,cap=0)
    cold=L.scenarios(database)[0]
    assert cold['metric_provenance']['validation_state']=='HISTORICAL_UNVERIFIED'
    assert cold['metric_provenance']['field_states']=={}
    detail=M.enrich(database,S.get_scenario(database,rid))
    proof=detail['metric_provenance']
    assert proof['validation_state']=='AUTHORITATIVE'
    assert proof['field_states']['net_return_pct']['state']=='NOT_APPLICABLE'
    assert proof['field_states']['max_drawdown_pct']['state']=='NOT_APPLICABLE'
    assert detail['metrics']['initial_capital']==0 and detail['metrics']['account_currency']=='EUR'
    assert detail['metrics']['net_profit']==-70 and detail['metrics']['trades']==3
    listed=L.scenarios(database)[0]
    assert listed['metrics']==detail['analytics']['summary']
    assert listed['metric_provenance']['field_states']==proof['field_states']
    assert detail['status']=='PARTIAL' and detail['coverage_status']=='PARTIAL'


def test_missing_evidence_is_unavailable_not_mathematical_na(database):
    from app.deeptest import listing as L,metric_service as M,store as S
    rid,_=saved(database);database.x('DELETE FROM deeptest_trades')
    detail=M.enrich(database,S.get_scenario(database,rid))
    assert detail['metric_provenance']['validation_state']=='HISTORICAL_UNAVAILABLE'
    listed=L.scenarios(database)[0]
    assert listed['metric_provenance']['validation_state']=='HISTORICAL_UNAVAILABLE'
    assert listed['metric_provenance']['field_states']=={}
    assert listed['metrics']['initial_capital']==200000 and listed['metrics']['account_currency']=='EUR'
    assert listed['metrics']['net_profit'] is None and listed['metrics']['trades'] is None


def test_incomplete_listing_and_available_row_detail_stay_distinct(database):
    from app.deeptest import listing as L,metric_service as M,store as S
    rid,_=saved(database);database.x('DELETE FROM deeptest_trades WHERE trade_no=1')
    detail=M.enrich(database,S.get_scenario(database,rid))
    assert detail['metric_provenance']['validation_state']=='AUTHORITATIVE'
    assert detail['metric_provenance']['available_trade_rows']==2
    assert detail['metric_provenance']['trade_count_matches_stored'] is False
    listed=L.scenarios(database)[0]
    assert listed['metric_provenance']['validation_state']=='HISTORICAL_UNAVAILABLE'
    assert listed['metrics']['net_profit'] is None
    assert detail['metrics']['net_profit']==50 and detail['metrics']['trades']==2
    assert detail['coverage_status']=='PARTIAL'


def test_projection_does_not_label_unverified_history_as_genuine_na(database):
    from app.deeptest import listing as L,metric_service as M,store as S
    from app.stats.deeptest_projection import effective_for
    rid,nid=saved(database)
    projected=effective_for(L.index(database),nid,{})
    assert 'unverified' in projected['metrics_source_label']
    assert 'N/A' not in projected['metrics_source_label']
    M.enrich(database,S.get_scenario(database,rid))
    assert effective_for(L.index(database),nid,{})['metrics']['net_profit']==-70


def test_validation_metadata_never_mutates_calculated_financial_evidence(database):
    from app.deeptest import metric_service as M,store as S
    rid,_=saved(database)
    value=M.calculate(S.get_scenario(database,rid),S.list_trades(database,rid,limit=100)['trades'])
    before=copy.deepcopy(value)
    for listing in (False,True):
        proof=M.validation_provenance(value,listing=listing)
        assert proof['validation_state']=='AUTHORITATIVE'
        proof['field_states'].clear()
        assert value==before
