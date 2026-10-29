"""Deterministic diagnostics tests. Use explicit isolated DATA_ROOT and TEMP."""
import concurrent.futures
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

@pytest.fixture
def profiler(monkeypatch):
    from app import speed
    p=speed.Profiler(capacity=100);p._runtime={'application_version':'test','commit_sha':None,'pid':os.getpid()}
    monkeypatch.setattr(speed,'PROFILER',p)
    yield p
    p.shutdown(persist=False)


def test_real_monotonic_nested_timing_and_wall_jump(profiler,monkeypatch):
    from app import speed
    with speed.span('generation.outer',run_id='run.1'):
        with speed.span('candidate.inner'):
            time.sleep(.002)
            monkeypatch.setattr(speed.time,'time',lambda:-90000.)
        time.sleep(.002)
    rows={r['stage']:r for r in profiler.snapshot()['stages']}
    outer=rows['generation.outer'];inner=rows['candidate.inner']
    assert outer['inclusive_seconds']>=inner['inclusive_seconds']>=.001
    assert abs(outer['self_seconds']+inner['inclusive_seconds']-outer['inclusive_seconds'])<1e-5
    events=profiler.snapshot()['events'];assert events[0]['parent_span_id']==events[1]['span_id']
    assert all(e['run_id']=='run.1' for e in events)
    assert not profiler.active


def test_off_path_preserves_return_errors_rng_and_empty_metrics(profiler):
    import random
    from app.speed import profiled
    profiler.configure('off')
    @profiled('candidate.test')
    def run(rng):return [rng.random() for _ in range(100)]
    assert run(random.Random(42))==list_rng(42)
    assert not profiler.stages
    @profiled('candidate.failed')
    def failed():raise ValueError('business error')
    with pytest.raises(ValueError,match='business error'):failed()
    assert not profiler.stages

def list_rng(seed):
    import random
    r=random.Random(seed);return [r.random() for _ in range(100)]


def test_capacity_drops_and_resize_are_exact(profiler):
    for i in range(1000):profiler.event('generation','progress',fields={'persisted_count':i})
    snap=profiler.snapshot();assert len(snap['events'])==100
    assert snap['retention']['dropped_events']==900
    assert [e['seq'] for e in snap['events']]==list(range(901,1001))
    profiler.configure('light',capacity=100)
    assert profiler.snapshot()['retention']['dropped_events']==901


def test_concurrent_runs_have_correct_context_worker_and_parent(profiler):
    from app import speed
    barrier=__import__('threading').Barrier(2)
    def job(rid):
        with speed.span('backtest.run',run_id=rid):
            barrier.wait()
            with speed.span('db.commit'):time.sleep(.001)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(job,['run.1','run.2']))
    snap=profiler.snapshot();assert {e['run_id'] for e in snap['events']}=={'run.1','run.2'}
    assert len({e['thread_id'] for e in snap['events']})==2
    for e in snap['events']:
        if e['parent_span_id']:
            parent=next(x for x in snap['events'] if x['span_id']==e['parent_span_id'])
            assert parent['thread_id']==e['thread_id'] and parent['run_id']==e['run_id']
    assert len(snap['workers'])==2


def test_no_credentials_sql_genomes_account_details_or_raw_errors(profiler):
    secret='ghp_ThisIsASensitiveToken123'
    profiler.event('db','query',fields={'sql':secret,'password':secret,'genome':{'secret':secret},'account':secret,'error_type':secret,'node_id':7},run_id=secret)
    output=json.dumps(profiler.snapshot());assert secret not in output
    event=profiler.snapshot()['events'][0];assert event['fields']=={'node_id':7} and event['run_id'] is None


def test_instrumentation_failures_cannot_change_business_result(profiler,monkeypatch):
    from app import speed,performance
    def broken(*a,**kw):raise OSError('instrument broken')
    monkeypatch.setattr(profiler,'span',broken);monkeypatch.setattr(profiler,'count',broken)
    @speed.profiled('generation.real')
    def business():
        with performance.measure('candidate.validation'):performance.count('candidates.persisted')
        return 42
    assert business()==42
    with speed.span('failure'):
        with pytest.raises(ValueError):raise ValueError('still business failure')


def test_copy_snapshot_not_affected_by_event_queries_or_filters(profiler,monkeypatch):
    from app.speed_api import router
    monkeypatch.setattr(profiler,'start_sampler',lambda:None)
    app=FastAPI();app.include_router(router)
    for i in range(120):profiler.event('generation','insert',fields={'node_id':i})
    with TestClient(app) as client:
        page=client.get('/api/speed-log/events?after=100&limit=1').json();assert len(page['events'])==1
        all=client.get('/api/speed-log/snapshot?limit=1&search=not-present&subsystem=hidden').json()
        assert len(all['events'])==100 and all['retention']['dropped_events']==20
        assert all['snapshot_id'] and all['runtime'] and all['recording']
        assert client.post('/api/speed-log/recording',json={'profile':'detailed','seconds':301}).status_code==422


def test_percentiles_only_when_enough_measured_calls(profiler):
    from app.speed import span
    for _ in range(4):
        with span('db.commit'):pass
    assert profiler.snapshot()['stages'][0]['p95_seconds'] is None
    with span('db.commit'):pass
    assert profiler.snapshot()['stages'][0]['p95_seconds'] is not None


def test_detailed_expiry_returns_to_light_without_altering_workers(profiler):
    profiler.configure('detailed',seconds=1);profiler.deadline=time.perf_counter()-.01
    assert profiler.enabled() and profiler.profile=='light'
    assert any(e['type']=='detailed_expired' for e in profiler.events)


def test_hot_sampling_does_not_extrapolate(profiler):
    from app.speed import span
    profiler.configure('light',sample_every=20)
    for _ in range(101):
        with span('db.query',hot=True):pass
    stage=next(v for v in profiler.snapshot()['stages'] if v['stage']=='db.query')
    assert stage['count']==6 and profiler.seen['db.query']==101


def test_shutdown_retains_cancellation_and_bounded_temp_flush(profiler,tmp_path,monkeypatch):
    from app import speed
    monkeypatch.setattr(speed.tempfile,'gettempdir',lambda:str(tmp_path))
    profiler.event('deeptest','phase','WARN',fields={'state':'CANCELLED'})
    profiler.shutdown()
    files=list(tmp_path.rglob('speed-*.json'));assert len(files)==1
    snapshot=json.loads(files[0].read_text());assert not snapshot['recording']['active']
    assert [e['type'] for e in snapshot['events']][-1]=='shutdown'
    assert snapshot['events'][0]['fields']['state']=='CANCELLED'


def test_worker_report_is_consumed_not_research_metadata(profiler):
    from app.speed import collect_result
    report={'stages':[{'stage':'backtest.compute','pid':98765,'count':1,'inclusive_seconds':2.,'self_seconds':1.,'max_seconds':2.,'failures':0,'sample_values':[2.]}], 'events':[{'subsystem':'worker','type':'started','severity':'INFO','pid':98765,'thread_id':123,'run_id':'run.1','fields':{'queue_seconds':.2}}],'counters':{'backtest.processed_bars':5000}}
    result={'ok':True,'metrics':{'trades':4000},'_speed_diagnostics':report}
    assert collect_result(result)=={'ok':True,'metrics':{'trades':4000}}
    snap=profiler.snapshot();assert snap['stages'][0]['pid']==98765
    assert snap['events'][0]['pid']==98765 and snap['events'][0]['run_id']=='run.1'
    assert snap['counters']['backtest.processed_bars']==5000


def test_real_database_commits_preserved_and_profiled(profiler,tmp_path):
    from app.db.database import Database
    db=Database(str(tmp_path/'test.db'))
    db.x('CREATE TABLE diagnostic_fixture (n INTEGER)');db.x('INSERT INTO diagnostic_fixture VALUES (42)')
    assert db.one('SELECT n FROM diagnostic_fixture')['n']==42
    stages={r['stage'] for r in profiler.snapshot()['stages']}
    assert {'db.query','db.write_sql','db.commit','db.lock_wait'}<=stages
    db.close()


def test_asgi_response_size_is_real_stream_and_does_not_buffer(profiler):
    from app.speed_api import SpeedMiddleware
    from fastapi.responses import StreamingResponse
    app=FastAPI();app.add_middleware(SpeedMiddleware)
    @app.get('/fixture')
    def response():return StreamingResponse(iter([b'ab',b'cde']))
    with TestClient(app) as client:assert client.get('/fixture').content==b'abcde'
    api=[e for e in profiler.snapshot()['events'] if e['type']=='response']
    assert api[-1]['fields']['bytes']==5 and api[-1]['fields']['status_code']==200


def test_cardinality_limits_are_bounded(profiler):
    from app.speed import span
    for i in range(400):
        with span('stages.'+str(i)):pass
        profiler.count('counter.'+str(i))
    assert len(profiler.stages)<=256 and len(profiler.counters)<=128 and len(profiler.seen)<=256
    assert profiler.snapshot()['retention']['dropped_metric_keys']>0

def test_real_unlimited_backtest_results_identical_off_light_detailed(profiler,tmp_path,monkeypatch):
    import copy
    import app.config as C
    from test_v10_2_processing import bars,request,actual,metrics_without_observation
    cfg=copy.deepcopy(C.get_config());cfg.resources.deep_test_workers=1
    monkeypatch.setattr(C,'_config',cfg)
    frame=bars();results=[]
    for mode in ('off','light','detailed'):
        profiler.configure(mode)
        result=actual(request(frame,max_trades=None),frame,tmp_path/mode)
        assert result.ok and len(result.trades)>6000
        results.append(result)
    # NaN stop-loss/take-profit sentinel values deliberately compare unequal in Python.
    # Canonical JSON retains every field and treats matching NaN sentinels identically.
    assert json.dumps(results[0].trades,sort_keys=True)==json.dumps(results[1].trades,sort_keys=True)==json.dumps(results[2].trades,sort_keys=True)
    assert metrics_without_observation(results[0].metrics)==metrics_without_observation(results[1].metrics)==metrics_without_observation(results[2].metrics)

def test_real_resource_sampler_reports_measured_delta_not_worker_capacity(profiler):
    profiler.interval=.5;profiler.start_sampler();deadline=time.monotonic()+4
    while (not profiler.resources or profiler.resources['processes'][0]['cpu_percent_one_core_100'] is None) and time.monotonic()<deadline:time.sleep(.02)
    snapshot=profiler.snapshot();assert snapshot['resources'],snapshot['resource_availability']
    resources=snapshot['resources'];assert resources['rss_bytes']>0
    assert resources['processes'][0]['pid']==os.getpid()
    assert resources['processes'][0]['cpu_percent_one_core_100'] is not None
    assert resources['per_core_cpu_percent'] and resources['cpu_delta_interval_seconds']>0
    json.dumps(snapshot,allow_nan=False)


def test_legacy_timing_failure_is_fail_open(profiler):
    from app import performance
    class Broken:
        def record(self,*args):raise OSError('trace unavailable')
        def count(self,*args):raise OSError('trace unavailable')
    token=performance._CURRENT.set(Broken())
    try:
        with performance.measure('candidate.validation'):performance.count('candidates.attempted')
    finally:performance._CURRENT.reset(token)

def test_record_failure_does_not_leak_context_or_active_operation(profiler,monkeypatch):
    from app import speed
    def broken(*a,**kw):raise OSError('record failure')
    monkeypatch.setattr(profiler,'_record',broken)
    with speed.span('generation.outer',run_id='run.1'):
        with speed.span('candidate.inner'):pass
    assert speed._STACK.get()==() and speed._RUN.get() is None and not profiler.active
    assert profiler.failure_count==2
