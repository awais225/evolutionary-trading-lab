"""V9.5 isolated contracts. No broker orders; actual SQLite, owned processes and HTTP."""
import json,time,threading,sqlite3,hashlib
from pathlib import Path
from types import SimpleNamespace
import pytest

@pytest.fixture
def db(tmp_path):
    from app.db.database import Database
    import app.evolution.engine as E
    old=E._evo_engine
    d=Database(str(tmp_path/'state.db'))
    yield d
    E._evo_engine=old
    d.close()

def seed(db,run='RUN-A',status='BORN',generation=0,source='USER_RESEARCH'):
    import uuid
    return db.insert_strategy({'hash':uuid.uuid4().hex,'run_id':run,'status':status,'generation':generation,'data_source':source,'symbol':'XAUUSD','timeframe':'M1','genome':{'symbol':'XAUUSD','timeframe':'M1'}})

def test_one_sql_snapshot_run_lifetime_statuses_and_new_run(db):
    from app.research.population_state import snapshot
    for status in ['BORN','QUALIFIED','FAILED','VALIDATING','BACKTEST_ERROR']:seed(db,status=status)
    seed(db,run='OLD',status='QUALIFIED',generation=9);seed(db,run='OLD',source='LEGACY_TEST');seed(db,status='EXCLUDED')
    a=snapshot(db,'RUN-A',10000)
    assert a['current_nodes']==5 and a['lifetime_total']==8 and a['lifetime_research']==6 and a['excluded_total']==2
    assert a['alive_nodes']+a['dead_nodes']+a['other_nodes']==a['current_nodes']
    assert a['qualified_nodes']==1 and a['generation_number']==0
    fresh=snapshot(db,'NEW',10000);assert fresh['current_nodes']==fresh['progress_pct']==fresh['generation_number']==0
    seed(db,run='NEW');assert snapshot(db,'NEW')['revision']!=fresh['revision']

def test_counts_above_ten_thousand_and_progress_2563(db):
    from app.research.population_state import snapshot
    now=time.time()
    db.xm("INSERT INTO strategies(hash,genome,status,run_id,data_source,generation,created_at,updated_at,symbol,timeframe) VALUES (?,?,?,?,?,?,?,?,?,?)",[(str(i),"{}","BORN","CURRENT" if i<2563 else "OLD","USER_RESEARCH",0,now,now,"XAUUSD","M1") for i in range(10809)])
    s=snapshot(db,'CURRENT',10000);assert s['current_nodes']==2563 and s['progress_pct']==25.63 and s['remaining_nodes']==7437 and s['lifetime_total']==10809
    assert snapshot(db,None)['current_nodes']==10809

def test_active_query_excludes_legacy_even_search_and_star(db,monkeypatch):
    from app.api import routes as R
    from app.deeptest import store
    store.ensure_schema(db)
    good=seed(db,status='QUALIFIED');bad=seed(db,status='QUALIFIED',source='LEGACY_TEST');excluded=seed(db,status='EXCLUDED')
    monkeypatch.setattr(R,'get_db',lambda:db)
    for wanted in ('all','qualified','excluded'):
        r=R.nodes_index(limit=0,filter=wanted)
        assert all(n['node_id'] not in (bad,excluded) for n in r['nodes'])
    r=R.nodes_index(limit=0,filter='all',search=str(bad));assert all(n['node_id']!=bad for n in r['nodes'])

def _stall():
    while True:time.sleep(1)

def test_stalled_owned_validation_call_cancel_is_bounded_and_idempotent():
    from app.orchestrator.cancellable import call,Cancelled
    stop=threading.Event();errors=[]
    def work():
        try:call(_stall,(),stop,timeout=30)
        except Cancelled as exc:errors.append(str(exc))
    thread=threading.Thread(target=work);thread.start();time.sleep(.15);t=time.monotonic();stop.set();stop.set();thread.join(timeout=2)
    assert not thread.is_alive() and time.monotonic()-t<2 and errors

def test_stalled_dependency_timeout():
    from app.orchestrator.cancellable import call
    with pytest.raises(TimeoutError):call(_stall,(),threading.Event(),timeout=.15)

def test_actual_lab_validation_stop_requeues_without_qualification(db,monkeypatch):
    from app.orchestrator import lab as L
    from app.evolution.engine import EvolutionEngine
    from app.orchestrator import pipeline_state as PS,stages
    from app.orchestrator import cancellable
    n=seed(db,status="SURVIVED");db.x("INSERT INTO backtests(strategy_id,stage,metrics,fitness,created_at,stale,dataset_id) VALUES (?,?,?,?,?,0,?)",(n,"detail","{}",1,time.time(),"fixture"))
    lab=L.Lab.__new__(L.Lab);lab.db=db;lab.evo=EvolutionEngine(db,run_id='RUN-A');lab._stop=threading.Event();lab._lock=threading.RLock();lab._pool=None;lab._pool_workers=0;lab.running=True;lab.paused=False;lab.safe_paused=False;lab.execution_state='RUNNING';lab.current_operation=None;lab.last_progress_at=time.time();lab.counters={'validated':0}
    monkeypatch.setattr(L,'full_validation',lambda *args:_stall())
    monkeypatch.setattr(stages,'get_stage_manager',lambda:SimpleNamespace(current_stage='VALIDATION',transition=lambda *a:None))
    monkeypatch.setattr(lab,'_publish_state',lambda *a:None);monkeypatch.setattr(lab.evo,'emit_stop_diagnostic',lambda *a,**k:None)
    lab._thread=threading.Thread(target=lab._validation_batch);lab._thread.start()
    deadline=time.monotonic()+3
    while lab.current_operation is None and time.monotonic()<deadline:time.sleep(.01)
    assert lab.current_operation and lab.current_operation.startswith('VALIDATION')
    t=time.monotonic();a=lab.stop();b=lab.stop()
    assert a['state']==b['state']=='STOPPED' and time.monotonic()-t<3
    assert db.get_strategy(n)['status']=='SURVIVED' and db.one('SELECT COUNT(*) n FROM validations')['n']==0

@pytest.mark.parametrize('pnls,length,amount', [([1,-2,-3,4,-10],2,10),([-1,-2,-3],3,6),([-4],1,4),([-1,0,-2],1,2),([-0.105,-0.105],2,.21),([1,2],0,0)])
def test_loss_sequences_actual_order_and_currency(pnls,length,amount):
    from app.deeptest.loss_sequences import calculate
    rows=[{'run_id':'one','trade_no':i,'exit_ts':i+1,'net_pnl':v} for i,v in enumerate(pnls)]
    out=calculate(list(reversed(rows)));assert out['max_consecutive_losing_trades']==length and out['max_consecutive_loss_currency']==amount

@pytest.mark.parametrize('rows',[[{'net_pnl':-2}],[{'exit_ts':1,'net_pnl':None}],[{'exit_ts':1,'net_pnl':float('nan')}],[]])
def test_missing_loss_records_not_invented(rows):
    from app.deeptest.loss_sequences import calculate
    out=calculate(rows);assert out['max_consecutive_loss_currency'] is None and out['loss_sequence_reason']

def test_partial_trade_population_and_duplicates():
    from app.deeptest.loss_sequences import calculate
    t={'run_id':'one','trade_no':1,'exit_ts':1,'net_pnl':-3}
    assert calculate([t,t])['max_consecutive_losing_trades']==1
    assert calculate([t],expected_count=2)['max_consecutive_loss_currency'] is None

@pytest.fixture
def bulk_fixture(db,monkeypatch):
    from app.deeptest import bulk as B,scenarios as SC,store as S
    S.ensure_schema(db);calls=[];decisions=[]
    def pre(db,**p):
        n=p['node_id'];kind={1:'COMPLETE',2:'PARTIAL',3:'NONE'}[n]
        return {'ok':kind!='NONE','needs_confirmation':kind=='PARTIAL','coverage':{'coverage_status':kind,'confirmation_fingerprint':str(n),'actual':{'bars':0 if n==3 else 400,'first_ts':10,'last_ts':20}},'period':{'start_ts':1,'end_ts':30},'error':'No usable history' if n==3 else None}
    def start(db,p):
        calls.append(p);rid='run-'+str(p['node_id']);S.insert_scenario(db,{'run_id':rid,'node_id':p['node_id'],'status':'COMPLETED','analytics':{'summary':{'trades':1}}});return {'ok':True,'run_id':rid}
    monkeypatch.setattr(SC,'preflight',pre);monkeypatch.setattr(SC,'start_run',start);monkeypatch.setattr(SC,'record_decision',lambda *a,**k:decisions.append(k));monkeypatch.setattr(B._POOL,'submit',lambda fn,*a:fn(*a))
    return B,calls,decisions

@pytest.mark.parametrize('decision,ran',[('partial',[1,2]),('full_only',[1]),('cancel',[])])
def test_mixed_batch_one_explicit_decision(db,bulk_fixture,decision,ran):
    B,calls,decisions=bulk_fixture
    job=B.create(db,{'node_ids':[1,2,3,2],'duration_key':'3M'})
    assert job['status']=='AWAITING_COVERAGE_DECISION' and not calls
    assert (job['preflight']['full'],job['preflight']['partial'],job['preflight']['unavailable'])==(1,1,1)
    B.coverage_decision(db,job['id'],decision)
    assert [p['node_id'] for p in calls]==ran
    assert len(decisions)==int(decision=='partial')
    if decision!='cancel':assert all(r.get('coverage') and r.get('requested_period') for r in B.get(db,job['id'])['results'])

def test_full_batch_runs_without_dialog_and_empty_requires_consent(db,bulk_fixture):
    B,calls,_=bulk_fixture
    j=B.create(db,{'node_ids':[1]});assert j['status']=='COMPLETED' and len(calls)==1
    j=B.create(db,{'node_ids':[3]});assert j['status']=='AWAITING_COVERAGE_DECISION'
    B.coverage_decision(db,j['id'],'partial');assert len(calls)==1

def test_generation_window_real_utc_inputs_cache_and_revision(db,monkeypatch):
    import numpy as np,pandas as pd
    from app.research import generation_window as G
    from app.data import history as H,master as M,engine as E
    import app.backtest.engine as BT
    end=1792141200.;stamps=np.arange(end-5*86400,end,60.);frame=pd.DataFrame({'ts':stamps,'open':1.,'high':2.,'low':.5,'close':1.5,'tick_volume':2.,'spread':3.})
    class Store:
        fingerprint='revision1';reads=0
        def read_range(self,start_ts=None,end_ts=None,columns=None):
            self.reads+=1;f=frame[(frame.ts>=start_ts)&(frame.ts<=end_ts)] if start_ts is not None else frame
            return f[columns] if columns else f
    store=Store();engine=SimpleNamespace(_materialize_dataset_view=lambda s:'exact',get_frame=lambda d:frame)
    monkeypatch.setattr(H,'canonical_profile',lambda *a:{'source':'USER_HISTORY'});monkeypatch.setattr(M,'get_master',lambda *a:store);monkeypatch.setattr(E,'get_data_engine',lambda:engine)
    G._CACHE.clear();cfg=G.settings(db);assert cfg['days']==3 and cfg['enabled']
    g={'symbol':'XAUUSD','timeframe':'M1'}
    a=G.prepare(g,cfg,now=end);reads=store.reads;b=G.prepare(g,cfg,now=end)
    assert a['requested_start']==end-3*86400 and a['requested_end']==end and a['available_bars']==4320
    assert b['view_cache_hit'] and store.reads==reads
    calls=[];monkeypatch.setattr(BT,'run_backtest',lambda req:(calls.append(req) or SimpleNamespace(ok=True,error=None,metrics={'bars':4320})))
    G.run(g,a);assert calls[0].window==(2880,7200) and calls[0].seed_salt=='generation_window'
    G.save_settings(db,{'days':2});c=G.prepare(g,G.settings(db),now=end);assert c['requested_start']==end-2*86400
    store.fingerprint='revision2';assert G.prepare(g,cfg,now=end)['cache_key']!=a['cache_key']

def test_cached_window_views_share_then_invalidate_on_frame_replacement():
    import pandas as pd,numpy as np
    from app.data.engine import DataEngine
    de=DataEngine();frame=pd.DataFrame({'ts':np.arange(500),'close':np.arange(500.)})
    a=de.generation_window_view('data-v1',100,400,frame);b=de.generation_window_view('data-v1',100,400,frame)
    assert a is b and len(a)==300
    assert de.generation_window_view('data-v2',100,400,frame) is not a
    assert de.generation_window_view('data-v1',100,400,frame.copy()) is not a

def test_offline_reset_backup_preserves_market_and_watermarks(tmp_path):
    from app.research_run.release_reset import execute,market_manifest
    root=tmp_path/'DATA';(root/'DATABASE').mkdir(parents=True);(root/'HISTORICAL').mkdir();(root/'HISTORICAL/original.csv').write_text('exact original bytes')
    (root/'RESEARCH/strategies').mkdir(parents=True);(root/'RESEARCH/strategies/node.json').write_text('{"id":99}')
    c=sqlite3.connect(root/"DATABASE/lab_state.db");c.executescript('CREATE TABLE strategies(id INTEGER PRIMARY KEY AUTOINCREMENT,status TEXT);INSERT INTO strategies VALUES(99,"QUALIFIED");CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);CREATE TABLE datasets(id TEXT,bars INT);INSERT INTO datasets VALUES("market",4131);')
    c.execute('INSERT INTO meta VALUES (?,?)',('generation_window_settings',json.dumps({'days':5})));c.commit()
    before=market_manifest(root);out=execute(c,root,tmp_path/'backup','RESET_ALL_NODE_RESEARCH_V9_5','a'*40)
    assert out['state']=='VERIFIED' and out['market_unchanged'] and market_manifest(root)==before
    assert c.execute('SELECT count(*) FROM strategies').fetchone()[0]==0
    assert c.execute('SELECT seq FROM sqlite_sequence WHERE name="strategies"').fetchone()[0]==99
    assert c.execute('SELECT bars FROM datasets').fetchone()[0]==4131
    assert not (root/'RESEARCH/strategies/node.json').exists() and Path(out['backup'],'node-state.zip').is_file()
    from app.research_run.release_reset import restore
    recovered=restore(c,root,out['backup'],'RESTORE_NODE_RESEARCH_V9_5')
    assert recovered['restored_nodes']==1 and market_manifest(root)==before
    assert (root/'RESEARCH/strategies/node.json').read_text()=='{"id":99}'
    assert c.execute('SELECT id FROM strategies').fetchone()[0]==99
    c.close()

def test_unknown_node_table_refuses_reset(tmp_path):
    from app.research_run.release_reset import inventory
    c=sqlite3.connect(':memory:');c.execute('CREATE TABLE unknown_owner(node_id INT)')
    with pytest.raises(RuntimeError,match='Unclassified'):inventory(c,tmp_path)


def test_calendar_malformed_and_ambiguous_loss_metrics_are_unavailable():
    from app.deeptest.inspect_calendar import aggregate
    from app.deeptest.loss_sequences import calculate
    bad=[{'run_id':'x','trade_no':1,'exit_ts':1,'entry_ts':1,'net_pnl':'not numeric'}]
    assert aggregate(bad,10000)['total']['metrics']['max_consecutive_loss_currency'] is None
    assert calculate([{'exit_ts':1,'trade_no':'bad','net_pnl':-2}])['max_consecutive_losing_trades'] is None
    assert calculate([{'exit_ts':1,'net_pnl':-2},{'exit_ts':1,'net_pnl':-3}])['loss_sequence_reason']=='Ambiguous same-timestamp trade order'


def _stall_payload(payload):
    return _stall()


def test_stalled_backtest_pool_stop_reaps_owned_workers(monkeypatch):
    from app.orchestrator import lab as L
    from concurrent.futures import ProcessPoolExecutor
    import multiprocessing as mp
    lab=L.Lab.__new__(L.Lab);lab._lock=threading.RLock();lab._stop=threading.Event();lab._pool=ProcessPoolExecutor(max_workers=1,mp_context=mp.get_context('fork'));lab._pool_workers=1;lab.current_operation=None;lab.last_progress_at=time.time();lab.execution_state='RUNNING';lab.running=True;lab.paused=False
    lab.evo=SimpleNamespace(emit_stop_diagnostic=lambda *a,**k:None)
    rm=SimpleNamespace(effective_workers=lambda:1,check_memory_budget=lambda **k:True,update_job_counts=lambda **k:None,record_task_completion=lambda *a,**k:None)
    monkeypatch.setattr(L,'get_resource_manager',lambda:rm);monkeypatch.setattr(L,'_worker_bt',_stall_payload);monkeypatch.setattr(lab,'_ensure_pool',lambda:None);monkeypatch.setattr(lab,'_publish_state',lambda *a:None)
    results=[];pool=lab._pool
    lab._thread=threading.Thread(target=lambda:results.extend(lab._run_batch([{}],timeout=30)));lab._thread.start()
    deadline=time.monotonic()+3
    while not (getattr(pool,'_processes',None) or {}) and time.monotonic()<deadline:time.sleep(.01)
    owned=list(pool._processes.values());assert owned
    t=time.monotonic();out=lab.stop()
    assert out['state']=='STOPPED' and time.monotonic()-t<3 and results==[None]
    assert not any(p.is_alive() for p in owned)


def test_duplicate_start_precedes_any_target_or_run_mutation():
    from app.orchestrator.lab import Lab
    lab=Lab.__new__(Lab);lab._lock=threading.RLock();lab._thread=SimpleNamespace(is_alive=lambda:True);lab.execution_state='RUNNING'
    assert not lab.start('continuous',target=999,run_type='new')['ok']


def test_unverified_resume_does_not_change_target(db,monkeypatch):
    from app.orchestrator.lab import Lab
    from app.evolution.engine import EvolutionEngine
    from app.orchestrator import pipeline_state as P
    db.set_meta('total_node_target','5')
    lab=Lab.__new__(Lab);lab._lock=threading.RLock();lab._thread=None;lab.db=db;lab.evo=EvolutionEngine(db);lab.execution_state='STOPPED'
    monkeypatch.setattr(P,'get_pipeline_state_manager',lambda:SimpleNamespace(run_id='NO-SUCH-RUN'))
    out=lab.start(target=999,run_type='resume')
    assert not out['ok'] and db.get_meta('total_node_target')=='5'


def test_persisted_fresh_uuid_and_collision_cannot_reset_frontier(db,monkeypatch,tmp_path):
    from app.orchestrator.pipeline_state import PipelineStateManager
    import app.orchestrator.pipeline_state as P
    monkeypatch.setattr(P,'get_db',lambda:db)
    manager=PipelineStateManager.__new__(PipelineStateManager);manager._lock=threading.RLock();manager.experiment_id='UNIT';manager.node_total=10000;manager.stage_statuses={'EVOLUTION':'COMPLETED'}
    monkeypatch.setattr(manager,'_persist',lambda:None);monkeypatch.setattr(manager,'_save_active_run',lambda:None)
    first=manager.new_run();seed(db,run=first)
    second=manager.new_run();assert first!=second and manager.generation==manager.node_completed==0
    from app.research.population_state import snapshot
    assert snapshot(db,second)['current_nodes']==0 and snapshot(db,second)['lifetime_total']==1
    with pytest.raises(ValueError):manager.new_run(first,target=99)
    assert manager.run_id==second and manager.node_total==10000


@pytest.mark.parametrize('saved',[{}, {'days':'bad'}, {'days':float('nan')}, {'days':True}, {'days':3,'enabled':'yes'}])
def test_invalid_generation_preferences_fall_back_to_three_days(db,saved):
    from app.research.generation_window import settings
    db.set_meta('generation_window_settings',json.dumps(saved));out=settings(db)
    assert out['days']==3 and out['enabled'] is True


def test_projection_holds_one_persisted_sqlite_version_across_external_commit(db):
    from app.research.population_state import snapshot
    with db.read_snapshot():
        first=snapshot(db)
        other=sqlite3.connect(db.path)
        now=time.time();other.execute("INSERT INTO strategies(hash,symbol,timeframe,genome,run_id,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",('external','XAUUSD','M1','{}','RUN-A','BORN',now,now));other.commit();other.close()
        second=snapshot(db)
        assert first['current_nodes']==second['current_nodes']==0
        assert first['registry_version']==second['registry_version']
    assert snapshot(db)['current_nodes']==1


def _stall_args(*args):return _stall()

def _minimal_lab(db,monkeypatch):
    from app.orchestrator import lab as L
    from app.evolution.engine import EvolutionEngine
    lab=L.Lab.__new__(L.Lab);lab.db=db;lab.evo=EvolutionEngine(db,run_id='RUN-A')
    lab._stop=threading.Event();lab._lock=threading.RLock();lab._pool=None;lab._pool_workers=0
    lab.running=True;lab.paused=False;lab.execution_state='RUNNING';lab.current_operation=None;lab.last_progress_at=time.time()
    monkeypatch.setattr(lab,'_publish_state',lambda *a:None);monkeypatch.setattr(lab.evo,'emit_stop_diagnostic',lambda *a,**k:None)
    return lab

def test_real_feature_stage_stall_stop_discards_computation(db,monkeypatch):
    import pandas as pd
    from app.orchestrator import lab as L,workers,milestones
    from app.features import tasks
    lab=_minimal_lab(db,monkeypatch);cancelled=[]
    feat=SimpleNamespace(_lock=threading.RLock(),_cache={})
    task=SimpleNamespace(start=lambda **k:None,start_feature=lambda *a:None,heartbeat=lambda **k:None,cancel=lambda reason:cancelled.append(reason))
    manager=SimpleNamespace(create_or_attach=lambda *a,**k:(task,True),cancel_all_active=lambda *a:None)
    monkeypatch.setattr(tasks,'get_feature_task_manager',lambda:manager)
    monkeypatch.setattr(milestones,'get_milestone_manager',lambda:SimpleNamespace())
    monkeypatch.setattr(L,'get_feature_engine',lambda:feat)
    monkeypatch.setattr(L,'get_data_engine',lambda:SimpleNamespace(get_frame=lambda _:pd.DataFrame({'close':[1.,2.]})))
    monkeypatch.setattr(workers,'feature_worker',_stall_args)
    lab._thread=threading.Thread(target=lab._precompute_dataset_features,args=('fixture','M1',['close']))
    lab._thread.start();deadline=time.monotonic()+3
    while lab.current_operation is None and time.monotonic()<deadline:time.sleep(.01)
    assert lab.current_operation and lab.current_operation.startswith('FEATURES')
    began=time.monotonic();out=lab.stop()
    assert out['state']=='STOPPED' and time.monotonic()-began<3 and cancelled
    assert feat._cache=={}

def test_real_research_rule_stall_stop_prevents_hypothesis_writes(db,monkeypatch):
    from app.ai_researcher import analyzer as A
    lab=_minimal_lab(db,monkeypatch);n=seed(db,status='QUALIFIED');db.x('UPDATE strategies SET fitness=1 WHERE id=?',(n,))
    monkeypatch.setattr(A,'get_db',lambda:db)
    monkeypatch.setattr(A,'get_config',lambda:SimpleNamespace(ai=SimpleNamespace(researcher_enabled=True,max_hypotheses_per_cycle=2)))
    monkeypatch.setattr(A,'rule_analyze',_stall_args)
    lab._thread=threading.Thread(target=lab._research_pass);lab._thread.start()
    deadline=time.monotonic()+3
    while lab.current_operation is None and time.monotonic()<deadline:time.sleep(.01)
    assert lab.current_operation=='RESEARCH analysis'
    began=time.monotonic();out=lab.stop()
    assert out['state']=='STOPPED' and time.monotonic()-began<3
    assert db.one('SELECT COUNT(*) n FROM hypotheses')['n']==0

def test_accounting_artifacts_are_current_run_not_other_runs(db):
    from app.evolution.engine import EvolutionEngine
    a=seed(db,run='RUN-A',status='QUALIFIED');b=seed(db,run='RUN-B',status='QUALIFIED');legacy=seed(db,source='LEGACY_TEST',status='QUALIFIED')
    for n in (a,b,legacy):db.x("INSERT INTO backtests(strategy_id,stage,created_at,dataset_id,metrics) VALUES (?,'detail',?,'fixture','{}')",(n,time.time()))
    out=EvolutionEngine(db,run_id='RUN-A').node_accounting(exclude_legacy=True)
    assert out['TOTAL CREATED']==out['TOTAL BACKTESTED']==out['TOTAL EVALUATED']==out['TOTAL QUALIFIED']==1

def test_qualified_default_excludes_legacy_explicit_diagnostics_do_not(db):
    a=seed(db,status='QUALIFIED');b=seed(db,status='QUALIFIED',source='LEGACY_TEST')
    assert {r['id'] for r in db.get_qualified_strategies()}=={a}
    assert {r['id'] for r in db.get_qualified_strategies(exclude_legacy=False)}=={a,b}


def test_owned_worker_telemetry_measures_active_task_not_pool_capacity():
    from app.orchestrator.cancellable import call,Cancelled,active_tasks
    stop=threading.Event()
    def work():
        try:call(_stall,(),stop,timeout=30)
        except Cancelled:pass
    t=threading.Thread(target=work);t.start();deadline=time.monotonic()+2
    while not active_tasks() and time.monotonic()<deadline:time.sleep(.01)
    measured=active_tasks();assert len(measured)==1 and measured[0]['task']=='_stall' and measured[0]['pid']>0
    stop.set();t.join(timeout=2);assert not t.is_alive() and active_tasks()==[]

def test_start_api_requires_explicit_resume_not_implicit_history(monkeypatch):
    from app.api import routes as R
    calls=[]
    monkeypatch.setattr(R,'get_lab',lambda:SimpleNamespace(start=lambda *a,**k:calls.append(k) or {'ok':True}))
    R.lab_start({});R.lab_start({'run_type':'resume'})
    assert [x['run_type'] for x in calls]==['new','resume']


def test_uninterruptible_owned_pool_is_truthful_stopping_not_false_stopped(db,monkeypatch):
    lab=_minimal_lab(db,monkeypatch);lab._thread=None;lab.current_operation='BACKTEST fixture'
    child=SimpleNamespace(pid=4242,is_alive=lambda:True,terminate=lambda:None,kill=lambda:None,join=lambda **k:None)
    lab._pool=SimpleNamespace(_processes={4242:child},shutdown=lambda **k:None)
    out=lab.stop()
    assert out['state']=='STOPPING' and out['blocked_operation']=='BACKTEST fixture'
    assert lab._blocked_owned_workers()[0]['pid']==4242
    assert not lab.start(target=999,run_type='new')['ok']


def test_empty_pipeline_uses_saved_target_and_truthful_stopped_state(db,monkeypatch,tmp_path):
    from app.orchestrator import pipeline_state as P
    db.set_meta('total_node_target','10000')
    monkeypatch.setattr(P,'get_db',lambda:db)
    monkeypatch.setattr(P.P,'METADATA_DIR',tmp_path)
    p=P.PipelineStateManager()
    assert p.run_id is None and p.status=='STOPPED' and p.node_total==10000

def test_bare_ready_field_cannot_identify_unrelated_application(monkeypatch):
    import importlib.util
    spec=importlib.util.spec_from_file_location('check_v95',Path(__file__).resolve().parents[1]/'backend/tools/check_port.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(module,'_json',lambda *a,**k:(200,{'service':'unrelated','ready':False}))
    assert module.identify_lab(8787)=='other'


def test_same_historical_alias_rule_is_completed_before_first_snapshot_and_reopen(db):
    from app.db.database import Database,RESEARCH_ALIAS_SQL
    from app.research.population_state import snapshot
    db.x("INSERT OR REPLACE INTO sqlite_sequence(name,seq) VALUES ('strategies',787)")
    a=seed(db);b=seed(db);c=seed(db,run='RUN-B')
    db.x(RESEARCH_ALIAS_SQL)
    assert [(r['id'],r['research_node_num']) for r in db.q('SELECT id,research_node_num FROM strategies ORDER BY id')]==[(a,1),(b,2),(c,1)]
    before=snapshot(db);path=db.path;db.close();reopened=Database(path)
    try:
        after=snapshot(reopened)
        assert before['registry_version']==after['registry_version'] and before['revision']==after['revision']
    finally:reopened.close()


def test_prior_consent_never_reclassifies_partial_coverage(db,monkeypatch):
    from app.deeptest import scenarios as SC,store as S
    from app.data import history as H
    S.ensure_schema(db);n=seed(db,status='QUALIFIED')
    monkeypatch.setattr(H,'canonical_profile',lambda *a:None)
    monkeypatch.setattr(SC,'check_period',lambda *a,**k:{'coverage_status':'PARTIAL','confirmation_fingerprint':'fixture-v1','actual':{'bars':600,'first_ts':200,'last_ts':600},'requested':{'start_ts':100,'end_ts':700}})
    before=SC.preflight(db,node_id=n,requested_start=100,requested_end=700)
    SC.record_decision(db,confirmation_fingerprint='fixture-v1',decision='YES_PROCEED',node_id=n)
    after=SC.preflight(db,node_id=n,requested_start=100,requested_end=700)
    assert before['coverage_classification']==after['coverage_classification']=='PARTIAL'
    assert after['needs_confirmation'] and not after['confirmation_required'] and after['may_proceed']
    monkeypatch.setattr(SC,'check_period',lambda *a,**k:{'coverage_status':'PARTIAL','confirmation_fingerprint':'fixture-v2','actual':{'bars':600}})
    changed=SC.preflight(db,node_id=n,requested_start=100,requested_end=800)
    assert changed['needs_confirmation'] and changed['confirmation_required'] and not changed['may_proceed']


def test_duplicate_pending_batch_is_one_job(db,bulk_fixture):
    B,calls,decisions=bulk_fixture
    a=B.create(db,{'node_ids':[1,2,3],'requested_end':1000})
    b=B.create(db,{'node_ids':[3,2,1],'requested_end':1000})
    assert a['id']==b['id'] and not calls
    assert db.one('SELECT COUNT(*) n FROM deeptest_batches')['n']==1


def test_full_only_uses_coverage_not_remembered_confirmation(db,bulk_fixture,monkeypatch):
    B,calls,decisions=bulk_fixture
    from app.deeptest import scenarios as SC
    original=SC.preflight
    def confirmed(db,**args):
        p=original(db,**args)
        if args['node_id']==2:p.update(needs_confirmation=False,confirmation_valid_for_this_fingerprint=True,may_proceed=True)
        return p
    monkeypatch.setattr(SC,'preflight',confirmed)
    a=B.create(db,{'node_ids':[1,2,3]})
    assert (a['preflight']['full'],a['preflight']['partial'],a['preflight']['unavailable'])==(1,1,1)
    B.coverage_decision(db,a['id'],'full_only')
    assert [p['node_id'] for p in calls]==[1]
    assert B.get(db,a['id'])['finished']==3


def test_explicit_release_reset_barrier_prevents_startup_resurrection(db,tmp_path,monkeypatch):
    from app.data.restoration import ResearchRestorationEngine
    from app import paths as P
    db.set_meta('v95_restoration_barrier','{"epoch":"fixture-reset"}')
    def forbidden(*a,**k):raise AssertionError('Reset barrier allowed filesystem discovery')
    monkeypatch.setattr(P,'ensure_layout',forbidden)
    report=ResearchRestorationEngine(data_root=tmp_path,db=db).restore_all(emit_logs=False)
    assert report['ok'] and report['auto_restore_disabled'] and report['nodes_restored']==0
    assert db.one('SELECT COUNT(*) n FROM strategies')['n']==0
