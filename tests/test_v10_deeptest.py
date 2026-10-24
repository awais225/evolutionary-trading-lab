"""V10 deterministic fixtures, not the separate two-node real-data integration.
Run with EVOLUTIONARY_LAB_DATA_ROOT and --basetemp outside the workspace.
"""
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

import pytest

from test_v9_5_repairs import db, bulk_fixture  # reusable SQLite/mocked engine fixtures


def test_explicit_action_auto_partial_preserves_coverage_and_skips_none(db,bulk_fixture):
    B,calls,decisions=bulk_fixture
    j=B.create(db,{'node_ids':[1,2,3,2],'auto_partial':True})
    assert j['status']=='COMPLETED_WITH_ISSUES'
    assert [p['node_id'] for p in calls]==[1,2]
    assert j['coverage_decision']=='partial' and len(decisions)==1
    assert j['selected']==j['validated']==j['completed']==3
    assert j['success']==2 and j['skipped']==1 and j['percent']==100
    assert all(r['coverage'] and r['requested_period'] for r in j['results'])
    assert j['phase']=='COMPLETED_WITH_ISSUES' and j['elapsed_seconds']>=0
    assert j['log_path'].endswith('DEEPTEST.log') and not Path(j['temp_path']).exists()


def test_legacy_policy_still_waits_without_execution(db,bulk_fixture):
    B,calls,_=bulk_fixture
    j=B.create(db,{'node_ids':[2]})
    assert j['status']==j['phase']=='AWAITING_COVERAGE_DECISION' and calls==[]


@pytest.mark.parametrize('ids',[[],[0],[-1],list(range(1,5002))])
def test_invalid_selection_contract(ids):
    from app.deeptest.contracts import BulkDeepRequest
    with pytest.raises(ValueError):BulkDeepRequest(node_ids=ids,auto_partial=True)


def test_contract_compatible_default_and_strict_unknowns():
    from app.deeptest.contracts import BulkDeepRequest
    assert BulkDeepRequest(node_ids=[1]).auto_partial is False
    assert BulkDeepRequest(node_ids=[1],auto_partial=True).auto_partial
    with pytest.raises(ValueError):BulkDeepRequest(node_ids=[1],disable_validation=True)


def test_owner_probe_never_sends_any_signal(monkeypatch):
    from app.deeptest import runtime as R,bulk as B
    monkeypatch.setattr(os,'kill',lambda *a:pytest.fail('no signal may be sent, especially on Windows'))
    assert R.alive(os.getpid()) and B._alive(os.getpid())
    assert not R.alive(None) and not R.alive(-1) and not R.alive('invalid')


def test_worker_error_is_durable_and_has_traceback(db,bulk_fixture,monkeypatch):
    from app.deeptest import scenarios as SC
    B,_,_=bulk_fixture
    def fail(*a):raise RuntimeError('v10 injected worker exception')
    monkeypatch.setattr(SC,'start_run',fail)
    j=B.create(db,{'node_ids':[1],'auto_partial':True})
    assert j['failure']==1 and j['results'][0]['status']=='FAILED'
    text=Path(j['log_path']).read_text()
    assert j['id'] in text and 'Traceback' in text and 'v10 injected worker exception' in text
    assert not Path(j['temp_path']).exists()


def test_missing_id_is_excluded_not_fabricated(db,monkeypatch):
    from app.deeptest import bulk as B,scenarios as SC
    monkeypatch.setattr(B._POOL,'submit',lambda fn,*a:fn(*a))
    monkeypatch.setattr(SC,'start_run',lambda *a:pytest.fail('invalid ID must not reach engine'))
    j=B.create(db,{'node_ids':[999999],'auto_partial':True})
    assert j['preflight']['unavailable']==1 and j['results'][0]['status']=='SKIPPED'
    assert 'authoritative inventory' in j['results'][0]['reason']


def test_restart_is_interrupted_not_rerun(db,monkeypatch):
    from app.deeptest import bulk as B
    monkeypatch.setattr(B._POOL,'submit',lambda *a:None)
    j=B.create(db,{'node_ids':[1]});B._ACTIVE.discard(j['id'])
    assert B.get(db,j['id'])['status']=='INTERRUPTED'
    with pytest.raises(ValueError):B.coverage_decision(db,j['id'],'partial')


def test_scratch_cleanup_success_error_and_retry(tmp_path,monkeypatch):
    from app.deeptest import runtime as R
    monkeypatch.setattr(R,'temp_root',lambda:tmp_path)
    for fails in [False,True,False]:
        path=None
        try:
            with R.Session('dt-ownership-test') as s:
                path=s.path;(path/'intermediate').write_text('fixture')
                if fails:raise RuntimeError('test cleanup')
        except RuntimeError:pass
        assert path is not None and not path.exists()
    assert list(tmp_path.iterdir())==[]


def test_temp_failure_never_falls_back(db,bulk_fixture,monkeypatch):
    from app.deeptest import runtime as R
    B,calls,_=bulk_fixture
    def denied():raise PermissionError('unwritable system TEMP')
    monkeypatch.setattr(R,'temp_root',denied)
    j=B.create(db,{'node_ids':[1],'auto_partial':True})
    assert j['status']=='FAILED' and 'unwritable' in j['error'] and not calls


def test_workspace_temp_is_rejected(monkeypatch):
    from app.deeptest import runtime as R
    monkeypatch.setattr(R.tempfile,'gettempdir',lambda:str(R.P.ROOT_DIR/'temporary'))
    with pytest.raises(ValueError,match='restricted workspace'):R.temp_root()


def test_abandoned_cleanup_protects_active_recent_foreign_and_symlink(tmp_path):
    from app.deeptest import runtime as R
    for name,pid,age in [('dt-dead',99999999,9*86400),('dt-active',os.getpid(),9*86400),('dt-recent',99999999,10)]:
        p=tmp_path/name;p.mkdir();(p/'.deeptest-owner.json').write_text(json.dumps({'id':name,'pid':pid,'created_at':time.time()-age}))
    foreign=tmp_path/'dt-foreign';foreign.mkdir();(foreign/'research').write_text('preserve')
    target=tmp_path/'research';target.mkdir();(tmp_path/'dt-link').symlink_to(target,target_is_directory=True)
    R.cleanup_abandoned(tmp_path)
    assert not (tmp_path/'dt-dead').exists()
    assert all((tmp_path/n).exists() for n in ['dt-active','dt-recent','dt-foreign','dt-link','research'])


def test_safe_cancellation_preserves_completed_and_stops_scheduling(db,bulk_fixture,monkeypatch):
    from app.deeptest import scenarios as SC
    B,calls,_=bulk_fixture;original=SC.start_run
    def first(db,p):
        result=original(db,p)
        ident=db.one('SELECT id FROM deeptest_batches ORDER BY rowid DESC LIMIT 1')['id']
        B.cancel(db,ident)
        return result
    monkeypatch.setattr(SC,'start_run',first)
    j=B.create(db,{'node_ids':[1,2],'auto_partial':True})
    assert j['status']=='CANCELLED' and len(calls)==1
    assert j['results'][0]['status']=='COMPLETED' and j['results'][1]['status']=='CANCELLED'


def test_scratch_engine_matches_full_frame_reference_without_shared_writes(tmp_path,monkeypatch):
    import numpy as np,pandas as pd
    from app.deeptest.runtime import scratch_backtest
    from app.backtest.engine import BacktestRequest,Backtester
    from app.features.engine import FeatureEngine
    from app.data import storage
    x=np.arange(1600);c=2000+4*np.sin(x/13)+x/100
    frame=pd.DataFrame({'ts':1750000000+x*900,'open':c-.1,'high':c+1,'low':c-1,'close':c,'tick_volume':x%10+10,'spread':20.})
    from app.data.sessions import session_labels
    frame['session']=session_labels(frame['ts'].to_numpy())
    frame['dow']=pd.to_datetime(frame['ts'],unit='s',utc=True).dt.dayofweek
    genome={'symbol':'XAUUSD','timeframe':'M15','direction':'both','entry_long':{'type':'crossover','a':'ema:20','b':'ema:50','dir':'up'},'entry_short':{'type':'crossover','a':'ema:20','b':'ema:50','dir':'down'},'exit':{'atr_spec':'atr:14','sl_atr_mult':1.5,'tp_atr_mult':3,'max_hold_bars':48},'risk':{'risk_per_trade':.005}}
    req=BacktestRequest(genome=genome,dataset_id='unit-full-frame',stage='detail',window=(500,1500),seed_salt='same-full-source')
    data=SimpleNamespace(get_frame=lambda _:frame)
    reference=FeatureEngine(data_engine=data)
    monkeypatch.setattr(reference,'_load_disk_cache',lambda *a:None)
    monkeypatch.setattr(reference,'_persist',lambda *a:None)
    monkeypatch.setattr(storage,'read_feature_cache',lambda *a:None)
    engine=Backtester();engine._data=data;engine._feat=reference
    baseline=engine.run(req);assert baseline.ok,baseline.error
    for name in ['write_bars','write_feature_cache']:
        monkeypatch.setattr(storage,name,lambda *a,**k:pytest.fail('DeepTest must not write shared cache'))
    phases=[];result=scratch_backtest(req,frame,tmp_path,lambda phase,msg:phases.append(phase))
    assert result.ok,result.error
    assert result.trades==baseline.trades
    assert {k:v for k,v in result.metrics.items() if k!='runtime_ms'}=={k:v for k,v in baseline.metrics.items() if k!='runtime_ms'}
    assert phases==['PREPARING_FEATURES','BACKTESTING','BACKTESTING']


def test_terminal_published_after_trade_persistence():
    import inspect
    from app.deeptest.scenarios import _execute_body
    text=inspect.getsource(_execute_body)
    assert text.index('S.insert_trades')<text.index("status='PARTIAL' if coverage_state")
    from app.deeptest import bulk
    assert '1800' not in inspect.getsource(bulk._run_body)


def test_submission_failure_retains_recoverable_failed_job(db,monkeypatch):
    from app.deeptest import bulk as B
    def denied(*a):raise RuntimeError('executor unavailable')
    monkeypatch.setattr(B._POOL,'submit',denied)
    j=B.create(db,{'node_ids':[1],'auto_partial':True})
    assert j['status']==j['phase']=='FAILED' and j['id']
    assert 'executor unavailable' in j['error'] and j['id'] not in B._ACTIVE


def test_pending_queue_is_bounded_without_eviction(db,monkeypatch):
    from app.deeptest import bulk as B
    monkeypatch.setattr(B,'_ACTIVE',set())
    monkeypatch.setattr(B._POOL,'submit',lambda *a:None)
    jobs=[B.create(db,{'node_ids':[n]}) for n in range(1,9)]
    with pytest.raises(ValueError,match='queue is full'):B.create(db,{'node_ids':[9]})
    assert db.one('SELECT count(*) c FROM deeptest_batches')['c']==8
    assert len({j['id'] for j in jobs})==8


def test_late_child_callback_cannot_overwrite_cancelled_phase(db,bulk_fixture):
    B,_,_=bulk_fixture
    j=B.create(db,{'node_ids':[2]});B.coverage_decision(db,j['id'],'cancel')
    B._progress(db,j['id'],{'phase':'CANCELLED'})
    B._progress(db,j['id'],{'phase':'COMPLETED','started_at':0})
    assert B.get(db,j['id'])['phase']=='CANCELLED'


def test_slow_progress_warns_without_timeout_or_failure(tmp_path,monkeypatch):
    from app.deeptest import runtime as R
    monkeypatch.setattr(R,'temp_root',lambda:tmp_path)
    monkeypatch.setenv('LAB_DEEPTEST_STALL_WARNING_SECONDS','1')
    with R.Session('dt-slow-test') as session:
        session.update('BACKTESTING','fixture controlled slow operation')
        time.sleep(1.15)
        assert session.path.exists()
    text=(R.P.DATA_LOGS_DIR/'DEEPTEST.log').read_text()
    assert 'job=dt-slow-test phase=BACKTESTING' in text and 'not automatically failed' in text
    assert 'before_bytes=' in text and 'after_exists=False' in text


def test_setup_failure_cleans_only_new_directory(tmp_path,monkeypatch):
    from app.deeptest import runtime as R
    monkeypatch.setattr(R,'temp_root',lambda:tmp_path)
    original=Path.write_text
    def denied(path,*args,**kwargs):
        if path.name=='.deeptest-owner.json':raise PermissionError('marker unavailable')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'write_text',denied)
    with pytest.raises(PermissionError,match='marker unavailable'):
        with R.Session('dt-setup-fail'):pass
    assert list(tmp_path.iterdir())==[]


def test_core_owner_checks_have_no_signalling_code():
    import inspect
    from app.deeptest import bulk,scenarios
    assert 'os.kill' not in inspect.getsource(bulk._alive)
    assert 'os.kill' not in inspect.getsource(scenarios._start_run_locked)


def test_returned_engine_failure_is_logged_and_recoverable(db,monkeypatch):
    from app.deeptest import scenarios as SC,store as S,runtime as R
    S.ensure_schema(db)
    run=S.insert_scenario(db,{'node_id':1,'status':'QUEUED'})
    monkeypatch.setattr('app.db.database.get_db',lambda:db)
    monkeypatch.setattr(SC,'_execute_body',lambda ident:S.update_scenario(db,ident,status='FAILED',error='returned engine validation failure'))
    SC._execute(run)
    assert S.get_scenario(db,run)['status']=='FAILED'
    assert 'returned engine validation failure' in (R.P.DATA_LOGS_DIR/'DEEPTEST.log').read_text()


def test_legacy_missing_policy_flag_still_deduplicates(db,monkeypatch):
    from app.deeptest import bulk as B
    monkeypatch.setattr(B,'_ACTIVE',set())
    monkeypatch.setattr(B._POOL,'submit',lambda *a:None)
    j=B.create(db,{'node_ids':[1],'requested_end':1000})
    old=j['payload'];old.pop('auto_partial')
    db.x('UPDATE deeptest_batches SET payload=? WHERE id=?',(json.dumps(old),j['id']))
    assert B.create(db,{'node_ids':[1],'requested_end':1000})['id']==j['id']


def test_preflight_cancel_reports_cancelled_phase_and_runs_nothing(db,bulk_fixture,monkeypatch):
    from app.deeptest import scenarios as SC
    B,calls,_=bulk_fixture;original=SC.preflight
    def cancel_during(db,**kwargs):
        ident=db.one('SELECT id FROM deeptest_batches ORDER BY rowid DESC LIMIT 1')['id']
        B.cancel(db,ident)
        return original(db,**kwargs)
    monkeypatch.setattr(SC,'preflight',cancel_during)
    j=B.create(db,{'node_ids':[1,2],'auto_partial':True})
    assert j['status']==j['phase']=='CANCELLED' and not calls


def test_explicit_resume_cannot_bypass_queue_bound(db,monkeypatch):
    from app.deeptest import bulk as B
    monkeypatch.setattr(B,'_ACTIVE',set());monkeypatch.setattr(B._POOL,'submit',lambda *a:None)
    old=B.create(db,{'node_ids':[99]})
    B._ACTIVE.discard(old['id']);assert B.get(db,old['id'])['status']=='INTERRUPTED'
    for node in range(1,9):B.create(db,{'node_ids':[node]})
    with pytest.raises(ValueError,match='queue is full'):B.resume(db,old['id'])
    assert B.get(db,old['id'])['status']=='INTERRUPTED'
