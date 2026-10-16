"""V9.3 deterministic contracts: isolated SQLite/fake executor; no broker orders."""
import copy,datetime as dt,json,os,time
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import pytest
from app.deeptest import daily_schedule as D, filter_rules as F

def ts(text):return dt.datetime.fromisoformat(text).replace(tzinfo=dt.timezone.utc).timestamp()
def matrix(value=False,zone='UTC'):return D.normalize({'timezone':zone,'days':{d:{s:value for s in D.MAJORS} for d in D.DAYS}})

@pytest.mark.parametrize('hour,category',[(0,'asia'),(6,'asia'),(7,'overlaps'),(8,'london'),(11,'london'),(12,'overlaps'),(15,'overlaps'),(16,'newyork'),(20,'newyork'),(21,None),(23,None)])
def test_exclusive_legacy_boundaries(hour,category):assert D.category(ts(f'2026-07-06T{hour:02}:00:00'))==category

@pytest.mark.parametrize('bad',[{}, {'days':{}},{'days':{d:{s:1 for s in D.MAJORS} for d in D.DAYS}},{**matrix(),'timezone':'not/a/zone'},{**matrix(),'version':2},{**matrix(),'boundaries':'invented'}])
def test_matrix_strict_validation(bad):
    with pytest.raises((ValueError,KeyError)):D.normalize(bad)

def test_matrix_all_28_cells_independent_and_master_derivation():
    m=matrix();assert all(v is False for v in m['global_sessions'].values())
    for d in D.DAYS:
        for s in D.MAJORS:
            n=copy.deepcopy(m);n['days'][d][s]=True;n=D.normalize(n)
            assert sum(sum(row.values()) for row in n['days'].values())==1
            assert n['day_masters'][d] is None and n['global_sessions'][s] is None
    assert all(v is True for v in matrix(True)['global_sessions'].values())

@pytest.mark.parametrize('zone,start', [('UTC','2026-07-05T00:00:00'),('America/New_York','2026-03-07T00:00:00'),('America/New_York','2026-10-31T00:00:00'),('Asia/Karachi','2026-07-05T00:00:00')])
def test_full_week_historical_live_matrix_parity_dst_and_weekends(zone,start):
    from app.live_testing.schedule import evaluate,bar_mask
    m=matrix(True,zone);m['days']['sun']['overlaps']=False;m['days']['mon']['asia']=False
    m=D.normalize(m);rules=F.normalize({'daily_schedule':m})
    stamps=np.arange(ts(start),ts(start)+9*86400,3600,dtype=float)
    cfg={'days':[0], 'sessions':['asia'],'filter_rules':rules}
    expected=[D.allows(t,m) for t in stamps]
    assert bar_mask(cfg,ts=stamps,dow=np.zeros(len(stamps))).tolist()==expected
    assert [evaluate(cfg,now=float(t))['allowed'] for t in stamps]==expected
    assert [F.reason({'entry_ts':float(t)},rules) is None for t in stamps]==expected

def test_matrix_does_not_bypass_enabled_or_other_filters():
    from app.live_testing.schedule import evaluate,bar_mask
    t=ts('2026-07-06T10:00:00');r=F.normalize({'daily_schedule':matrix(True),'exclude_days':['mon']})
    assert not evaluate({'filter_rules':r},now=t)['allowed']
    assert not evaluate({'enabled':False,'filter_rules':F.normalize({'daily_schedule':matrix(True)})},now=t)['allowed']
    assert not bar_mask({'enabled':False,'filter_rules':r},ts=np.array([t]),dow=np.array([0]))[0]

def test_unique_eligible_closed_trade_population_and_zero_loss():
    from app.deeptest.analytics import summarize
    from app.deeptest.inspect_calendar import aggregate
    t=ts('2026-07-06T13:00:00')
    base={'run_id':'one','trade_no':1,'entry_ts':t,'exit_ts':t+1,'net_pnl':10,'status':'CLOSED'}
    rows=[{**base,'status':'OPEN'},base,dict(base),{**base,'trade_no':2,'net_pnl':-5},{'net_pnl':0,'entry_ts':t,'exit_ts':t+2},{'net_pnl':0,'entry_ts':t,'exit_ts':t+3},{'net_pnl':float('nan')}]
    s=summarize(rows,initial_capital=100)['summary'];assert s['trades']==4 and s['win_rate']==.25 and s['profit_factor']==2 and s['net_return_pct']==.05
    assert s['max_drawdown_pct']==.05
    a=aggregate(rows,100);assert a['total']['metrics']==s and a['original_trades']==4
    zero=summarize([base],initial_capital=100)['summary'];assert zero['profit_factor'] is None and zero['win_rate']==1

def test_shared_canonical_rank_sources_missing_partial_and_ties():
    from app.stats.canonical import attach,rank
    def row(n,v,status='COMPLETED'):
        return attach({'node_id':n,'metrics':{'win_rate':v},'metrics_source':'deeptest_latest_valid','generation_metrics':{'win_rate':.4},'deeptest':{'preferred_status':status,'preferred_run_id':'real-run','coverage_status':status if status=='PARTIAL' else 'COMPLETE','dataset_id':'stored','dataset_fingerprint':'fp','actual_start':1,'actual_end':2}})
    rows=[row(9,.8),row(2,.8),row(1,.99,'PARTIAL'),row(3,None),row(4,float('nan'))]
    assert [r['node_id'] for r in rank(rows)]==[2,9,1,3,4]
    assert [r['node_id'] for r in rank(rows,False)]==[2,9,1,3,4]
    h=rows[0]['historical_statistics'];assert h['provenance']['dataset_fingerprint']=='fp' and h['provenance']['scenario_id']=='real-run'
    assert attach(copy.deepcopy(rows[0]),'generation')['metrics']['win_rate']==.4
    missing=attach({'node_id':5,'metrics':{'win_rate':.5},'generation_metrics':{'win_rate':.5},'metrics_source':'generation'},'deeptest')
    assert missing['historical_statistics']['status']=='UNAVAILABLE' and missing['metrics']=={}

@pytest.fixture
def db(tmp_path):
    from app.db.database import Database
    d=Database(str(tmp_path/'v93.db'));yield d;d.close()

def seed(db):
    return db.insert_strategy({'hash':'v93-real-unit-node','symbol':'XAUUSD','timeframe':'M1','genome':{'symbol':'XAUUSD','timeframe':'M1'},'status':'SURVIVED'})

def test_assignment_durable_snapshot_cross_connection_outdated_and_safety(db):
    from app.db.database import Database
    from app.deeptest import filter_scenarios as S
    n=seed(db);db.set_live_test_config(n,{'risk_pct':2,'is_active':False,'enabled':False});before=db.get_live_test_config(n)
    m=matrix();m['days']['sun']['london']=True
    a=S.save(db,{'name':'Sunday London','scope':'NODE','node_id':n,'rules':{'daily_schedule':m,'exclude_by_day':{'fri':['asia']}}})
    S.assign(db,n,a['id'],'live');reopened=Database(db.path)
    try:
        assert S.assignment(reopened,n)['rules']['daily_schedule']['days']==m['days']
        newrules=F.normalize({'daily_schedule':matrix(True)})
        db.x('UPDATE filter_scenarios SET rules=? WHERE id=?',(json.dumps(newrules),a['id']))
        b=S.assignment(reopened,n);assert b['outdated'] and b['rules']['daily_schedule']['days']==m['days']
        assert db.get_live_test_config(n)==before
        S.assign(db,n,None,'live');assert S.assignment(reopened,n) is None
    finally:reopened.close()

def fixture_run(db,monkeypatch):
    from app.deeptest import scenarios as S,store
    store.ensure_schema(db);n=seed(db);state={'fp':'data-one'};submitted=[]
    monkeypatch.setattr(S,'preflight',lambda *a,**k:{'ok':True,'needs_confirmation':False,'period':{'duration_key':'3M','start_ts':1.,'end_ts':20.},'coverage':{'coverage_status':'COMPLETE','dataset_fingerprint':state['fp'],'confirmation_fingerprint':'confirmation','effective_population':{'persisted_range_first':2.,'persisted_range_last':18.}},'settings':k['settings']})
    monkeypatch.setattr(S._executor,'submit',lambda *a:submitted.append(a))
    return S,n,state,submitted

def test_concurrent_repeat_same_scenario_db_claim_no_orders(db,monkeypatch):
    S,n,state,submitted=fixture_run(db,monkeypatch);payload={'node_id':n,'duration_key':'3M'}
    with ThreadPoolExecutor(max_workers=8) as pool:out=list(pool.map(lambda _:S.start_run(db,payload),range(24)))
    ids={r.get('run_id') or r['scenario']['run_id'] for r in out}
    assert len(ids)==1 and len(submitted)==1 and db.one('SELECT COUNT(*) c FROM deeptest_claims')['c']==1
    assert db.one('SELECT COUNT(*) c FROM deeptest_scenarios')['c']==1

def test_material_new_data_and_settings_distinct_cosmetic_release_not(db,monkeypatch):
    import app.versions as versions
    S,n,state,submitted=fixture_run(db,monkeypatch);p={'node_id':n,'duration_key':'3M','idempotency_key':'ui-one'}
    a=S.start_run(db,p)['run_id'];monkeypatch.setattr(versions,'PRODUCT_RELEASE','COSMETIC ONLY')
    assert S.start_run(db,{**p,'idempotency_key':'different-ui'})['scenario']['run_id']==a
    b=S.start_run(db,{**p,'node_settings':{'max_trades':12}})['run_id'];assert b!=a
    state['fp']='new-data';assert S.start_run(db,p)['run_id'] not in (a,b)
    assert len(submitted)==3

def test_failed_requires_explicit_retry_preserves_history_and_force_refused(db,monkeypatch):
    from app.deeptest import store
    S,n,state,submitted=fixture_run(db,monkeypatch);p={'node_id':n,'duration_key':'3M'}
    a=S.start_run(db,p)['run_id'];store.update_scenario(db,a,status='FAILED',error='explicit test interruption')
    assert S.start_run(db,p)['retry_required']
    b=S.start_run(db,{**p,'retry_incomplete':True})['run_id'];assert b!=a
    assert store.get_scenario(db,a)['status']=='FAILED'
    assert not S.start_run(db,{**p,'force_rerun':True})['ok']

def test_invalid_completed_result_is_not_reused_in_production(db,monkeypatch):
    from app.deeptest import store
    S,n,state,submitted=fixture_run(db,monkeypatch);p={'node_id':n,'duration_key':'3M'}
    a=S.start_run(db,p)['run_id'];store.update_scenario(db,a,status='COMPLETED')
    assert S.start_run(db,p)['retry_required']
    store.update_scenario(db,a,analytics={'summary':{'trades':0,'win_rate':None}})
    assert S.start_run(db,p)['scenario']['run_id']==a

def test_material_fingerprint_numeric_and_names():
    from app.deeptest.scenarios import settings_fingerprint as fp
    assert fp({'risk':2,'name':'A','notes':'old'})==fp({'risk':2.,'name':'B','notes':'new'})
    assert fp({'risk':2})!=fp({'risk':3})
    assert fp({'daily_schedule':matrix()})!=fp({'daily_schedule':matrix(True)})

def test_bulk_durable_queue_dedup_skip_failure_cancel_and_cross_process_owner(db,monkeypatch):
    from app.deeptest import bulk as B,scenarios as SC,store as S
    S.ensure_schema(db);n=seed(db);calls=[]
    def start(db,p):
        calls.append(p)
        if p['node_id']==n+1:return {'ok':False,'needs_confirmation':True,'error':'partial history needs explicit confirmation'}
        S.insert_scenario(db,{'run_id':'unit-bulk','node_id':n,'status':'COMPLETED','analytics':{'summary':{'trades':0}}})
        return {'ok':True,'run_id':'unit-bulk'}
    monkeypatch.setattr(SC,'start_run',start)
    # Synchronous orchestration seam; no historical/live engines are invoked.
    monkeypatch.setattr(B._POOL,'submit',lambda fn,*args:fn(*args))
    out=B.create(db,{'node_ids':[n,n,n+1],'node_settings_by_id':{str(n):{'max_trades':3}}})
    assert out['total']==2 and out['finished']==2 and out['status']=='COMPLETED_WITH_ISSUES'
    assert calls[0]['node_settings']=={'max_trades':3} and out['results'][1]['needs_confirmation']
    # A separate process reading a genuinely live owner must not interrupt it.
    import subprocess
    proc=subprocess.Popen(['sleep','15'])
    try:
        db.x("UPDATE deeptest_batches SET status='RUNNING',owner_pid=? WHERE id=?",(proc.pid,out['id']))
        assert B.get(db,out['id'])['status']=='RUNNING'
    finally:proc.terminate();proc.wait()
    assert B.get(db,out['id'])['status']=='INTERRUPTED'
    B.cancel(db,out['id']);assert B.get(db,out['id'])['cancel_requested']==1

def test_bulk_persists_unexpected_orchestration_failure(db,monkeypatch):
    from app.deeptest import bulk as B
    monkeypatch.setattr(B._POOL,'submit',lambda fn,*args:fn(*args))
    # Deliberately corrupt saved payload after create's validation.
    original=B.get
    monkeypatch.setattr(B,'get',lambda *a:(_ for _ in ()).throw(RuntimeError('test lost read')))
    B.schema(db);db.x('INSERT INTO deeptest_batches(id,status,payload,results,updated_at) VALUES (?,?,?,?,?)',('broken','QUEUED','{}','[]',time.time()))
    B._run(db,'broken');assert db.one("SELECT status FROM deeptest_batches WHERE id='broken'")['status']=='FAILED'

def test_api_schedule_and_live_selection_are_read_only_for_trading(db,monkeypatch):
    from app.api import routes as R
    from app.deeptest.contracts import ScheduleRequest,SelectionRequest
    from fastapi import HTTPException
    n=seed(db);monkeypatch.setattr(R,'get_db',lambda:db);before=db.get_live_test_config(n)
    saved=R.daily_schedule_put(n,ScheduleRequest(target='live',name='Complete matrix',rules={'daily_schedule':matrix(True)},expected_scenario_id=''))
    assert saved['matrix_active'] and saved['assignment']['name']=='Complete matrix'
    assert saved['rules']['daily_schedule']['day_masters']['sun'] is True
    with pytest.raises(HTTPException) as err:R.daily_schedule_put(n,ScheduleRequest(target='live',rules={'daily_schedule':matrix()},expected_scenario_id='stale'))
    assert err.value.status_code==409
    with pytest.raises(HTTPException) as err:R.daily_schedule_put(n,ScheduleRequest(rules={'daily_schedule':{}}))
    assert err.value.status_code==422
    result=R.live_selection_validate(SelectionRequest(node_ids=[n,n,99999999]))
    assert result['selected']==2 and result['started']==0 and result['orders_submitted']==0
    assert not result['nodes'][1]['eligible'] and db.get_live_test_config(n)==before

def test_cross_process_identical_requests_reuse_one_live_owner(db,tmp_path):
    """Real OS file lock + SQLite UNIQUE claim, not just Python thread locking."""
    import subprocess,sys
    from app.deeptest import store as S
    S.ensure_schema(db);n=seed(db)
    code=r'''
import sys,json,time
from pathlib import Path
from app.db.database import Database
from app.deeptest import scenarios as S
path,node,out,release=sys.argv[1:];db=Database(path)
S.preflight=lambda *a,**k:{'ok':True,'needs_confirmation':False,'period':{'duration_key':'3M','start_ts':1.,'end_ts':20.},'coverage':{'coverage_status':'COMPLETE','dataset_fingerprint':'process-test','confirmation_fingerprint':'confirmed','effective_population':{'persisted_range_first':2.,'persisted_range_last':18.}},'settings':k['settings']}
S._executor.submit=lambda *a:None
result=S.start_run(db,{'node_id':int(node),'duration_key':'3M'})
Path(out).write_text(json.dumps(result));deadline=time.monotonic()+30
while not Path(release).exists() and time.monotonic()<deadline:time.sleep(.02)
db.close()
'''
    release=tmp_path/'release';files=[tmp_path/'result1.json',tmp_path/'result2.json']
    env={**os.environ,'PYTHONPATH':str(__import__('pathlib').Path(__file__).resolve().parents[1]/'backend')}
    ps=[subprocess.Popen([sys.executable,'-c',code,db.path,str(n),str(f),str(release)],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE) for f in files]
    try:
        deadline=time.monotonic()+20
        while not all(f.exists() for f in files) and time.monotonic()<deadline:time.sleep(.02)
        assert all(f.exists() for f in files),'concurrent requests did not finish'
        results=[json.loads(f.read_text()) for f in files]
        assert all(r['ok'] for r in results),results
        assert len({r.get('run_id') or r['scenario']['run_id'] for r in results})==1
        assert db.one('SELECT COUNT(*) c FROM deeptest_scenarios')['c']==1
        assert db.one('SELECT COUNT(*) c FROM deeptest_claims')['c']==1
    finally:
        release.touch()
        for p in ps:
            _,err=p.communicate(timeout=35)
            assert p.returncode==0,err.decode()[-1000:]

def test_default_projection_and_detail_choose_identical_partial_source(db):
    from app.deeptest import store as S,scenarios as SC
    from app.stats.deeptest_projection import build_index,project_node,effective_for
    S.ensure_schema(db);n=seed(db)
    for rid,status,created in [('complete','COMPLETED',10),('partial','PARTIAL',20)]:
        S.insert_scenario(db,{'run_id':rid,'node_id':n,'status':status,'created_at':created,'coverage_status':'COMPLETE' if status=='COMPLETED' else 'PARTIAL','analytics':{'summary':{'trades':2,'win_rate':.5}},'actual_start':1,'actual_end':2,'dataset_fingerprint':'fp'})
    index=build_index(db);a=effective_for(index,n,{});b=project_node(db,n,{})
    assert a['deeptest']['preferred_run_id']==b['deeptest']['preferred_run_id']=='partial'
    assert a['metrics']==b['metrics']
    S.set_pref(db,n,'complete',explicit=True)
    assert effective_for(build_index(db),n,{})['deeptest']['preferred_run_id']==project_node(db,n,{})['deeptest']['preferred_run_id']=='complete'

def test_generation_does_not_claim_current_operational_dataset_dates():
    from app.stats.canonical import attach
    row=attach({'node_id':17,'metrics_source':'generation','metrics':{'win_rate':.5},'generation_metrics':{'win_rate':.5},'backtest_coverage':{'start':'2026-01-01T00:00:00Z','end':'2026-02-01T00:00:00Z','research_backtest':{'dataset_id':'old-gen-dataset'}},'generation_record':{'backtest_id':14,'provenance':{'created_at':1}}})
    p=row['historical_statistics']['provenance'];assert p['start_ts'] is None and p['end_ts'] is None and p['dataset_id']=='old-gen-dataset' and p['generation_record']['backtest_id']==14

def test_invalid_win_rate_is_unavailable_not_clamped():
    from app.stats.canonical import attach
    r=attach({'node_id':1,'metrics_source':'generation','metrics':{'win_rate':1.5},'generation_metrics':{'win_rate':1.5}})
    assert r['metrics']['win_rate'] is None and r['generation_metrics']['win_rate']==1.5 and r['historical_statistics']['warnings']

def test_live_cosmetic_configuration_does_not_change_historical_signature(db,monkeypatch):
    S,n,state,submitted=fixture_run(db,monkeypatch);p={'node_id':n,'duration_key':'3M','node_settings':{'sl_offset_pips':2,'tp_offset_pips':-3}}
    a=S.start_run(db,p)['run_id']
    db.set_live_test_config(n,{'risk_pct':3,'is_active':False,'sl_offset_pips':50,'tp_offset_pips':60})
    assert S.start_run(db,p)['scenario']['run_id']==a


def test_deep_schedule_preview_never_substitutes_live_assignment_or_history(db,monkeypatch):
    from app.deeptest import filter_scenarios as S
    from app.api import routes as R
    from app.live_testing import schedule
    from app.deeptest import store
    store.ensure_schema(db)
    n=seed(db)
    db.set_live_test_config(n,{'enabled':False,'is_active':False,'risk_pct':2})
    before=db.get_live_test_config(n)
    live=S.save(db,{'name':'Live blocked','scope':'NODE','node_id':n,'rules':{'daily_schedule':matrix(False)}})
    deep=S.save(db,{'name':'Deep allowed','scope':'NODE','node_id':n,'rules':{'daily_schedule':matrix(True)}})
    S.assign(db,n,live['id'],'live');S.assign(db,n,deep['id'],'deep')
    evaluate=schedule.evaluate
    monkeypatch.setattr(schedule,'evaluate',lambda cfg,**kw:evaluate(cfg,now=ts('2026-07-06T10:00:00'),**kw))
    monkeypatch.setattr(R,'_live_engine',lambda:pytest.fail('Deep preview must not read Live engine trade/cooldown/assignment state'))
    out=R._deeptest_schedule_contract(db,n,{})
    assert out['allowed'] is True and out['evaluation']['allowed'] is True
    assert 'assigned Deep' in out['config_source'] and out['trades_today']==0
    S.assign(db,n,None,'deep')
    out=R._deeptest_schedule_contract(db,n,{})
    assert out['allowed'] is True and out['config_source'].startswith('historical defaults')
    assert db.get_live_test_config(n)==before and S.assignment(db,n,'live')['id']==live['id']
