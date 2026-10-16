import datetime as dt,json,time
import numpy as np
import pytest

def ts(s):return dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).timestamp()

def test_filter_overlap_weekday_and_live_bar_parity():
    from app.deeptest.filter_rules import normalize,reason,memberships
    from app.live_testing.schedule import evaluate,bar_mask
    # London and NY both open at 13:00Z on a July Monday.
    t=ts('2026-07-06T13:00:00');other=t+86400
    assert set(memberships(t))>={'london','newyork','london_newyork_overlap'}
    rules=normalize({'exclude_by_day':{'mon':['london']}})
    assert reason({'entry_ts':t},rules) is not None and reason({'entry_ts':other},rules) is None
    assert not evaluate({'filter_rules':rules},now=t)['allowed']
    assert evaluate({'filter_rules':rules},now=other)['allowed']
    assert bar_mask({'filter_rules':rules},ts=np.array([t,other]),dow=np.array([0,1]),session=np.array(['newyork','newyork'])).tolist()==[False,True]

def test_calendar_five_plus_iso_weeks_union_and_no_trade():
    from app.deeptest.inspect_calendar import aggregate
    start=ts('2025-12-25T00:00:00');end=ts('2026-02-10T00:00:00')
    trades=[{'entry_ts':ts('2026-01-05T14:00:00'),'exit_ts':ts('2026-01-05T15:00:00'),'net_pnl':10,'side':'BUY','trade_no':1},
            {'entry_ts':ts('2026-02-02T14:00:00'),'exit_ts':ts('2026-02-02T15:00:00'),'net_pnl':-3,'side':'SELL','trade_no':2}]
    out=aggregate(trades,1000,{},start,end)
    assert len(out['weeks'])==8 and out['weeks'][1]['iso_week']=='2026-W01'
    assert sum(w['metrics']['trades'] for w in out['weeks'])==2 and out['total']['metrics']['net_profit']==7
    assert out['weekdays'][1]['state']=='NO_TRADES' and out['weekdays'][1]['metrics']['win_rate'] is None
    out=aggregate(trades,1000,{'include_sessions':['london','newyork']},start,end)
    assert out['kept_trades']==2 # not four overlapping memberships
    assert sum(x['metrics']['trades'] for x in out['sessions'])>2 # explicitly non-additive panels

def test_scenario_durable_assignment_removal_risk_and_arm_untouched(tmp_path):
    from app.db.database import Database
    from app.deeptest.filter_scenarios import save,assign,assignment,listing
    path=str(tmp_path/'scenario.db');db=Database(path)
    node=db.insert_strategy({'hash':'v92-one','symbol':'XAUUSD','timeframe':'M1','genome':{},'status':'SURVIVED'})
    db.set_live_test_config(node,{'risk_pct':2,'is_active':False,'enabled':False,'days':['Mon']});before=db.get_live_test_config(node)
    sc=save(db,{'name':'Monday London off','scope':'NODE','node_id':node,'rules':{'exclude_by_day':{'mon':['london']}}})
    assign(db,node,sc['id'],'live');assert assignment(db,node)['rules']['exclude_by_day']=={'mon':['london']}
    assert db.get_live_test_config(node)==before
    reopened=Database(path);assert assignment(reopened,node)['id']==sc['id']
    assign(reopened,node,None,'live');assert assignment(reopened,node) is None and reopened.get_live_test_config(node)==before
    assert len(listing(reopened,node))==1
    with pytest.raises(ValueError):save(db,{'name':'bad','rules':{'schema_version':99}})

def test_generation_window_settings_and_missing_history_isolation(tmp_path,monkeypatch):
    from app.db.database import Database
    from app.research import generation_window as G
    from app.data import history as H
    db=Database(str(tmp_path/'window.db'));before=db.q('SELECT * FROM backtests');nodebefore=db.q('SELECT * FROM strategies')
    assert G.settings(db)==G.DEFAULT
    for bad in [0,-1,1.2,True,float('nan'),3651]:
        with pytest.raises(ValueError):G.save_settings(db,{'days':bad})
    cfg=G.save_settings(db,{'days':3,'enabled':True});monkeypatch.setattr(H,'canonical_profile',lambda *a:None)
    genome={'symbol':'XAUUSD','timeframe':'M1','exit':{}}
    result=G.run(genome,G.prepare(genome,cfg,now=ts('2026-10-16T12:00:00')))
    assert result['status']=='INSUFFICIENT_HISTORY' and result['metrics'] is None
    G.persist(db,123,result);assert G.recent(db)[0]['node_id']==123
    assert db.q('SELECT * FROM backtests')==before and db.q('SELECT * FROM strategies')==nodebefore

def test_window_uses_existing_backtester_and_cache_dimensions(monkeypatch):
    from app.research import generation_window as G
    import app.backtest.engine as B
    calls=[]
    class Result:
        ok=True;error=None;metrics={'trades':2,'profit_factor':1.2}
    monkeypatch.setattr(B,'run_backtest',lambda req:(calls.append(req) or Result()))
    ctx={'status':'READY','dataset_id':'real-persistent-snapshot','dataset_fingerprint':'abc','window':[100,500],'days':3,'cache_key':'data-window-key','requested_start':1,'requested_end':2}
    a=G.run({'symbol':'XAUUSD','timeframe':'M1','direction':'both'},ctx)
    b=G.run({'symbol':'XAUUSD','timeframe':'M1','direction':'long'},ctx)
    assert a['status']=='COMPLETED' and a['namespace']=='generation_window'
    assert len(calls)==2 and calls[0].window==(100,500) and calls[0].stage=='detail'
    assert a['cache_key']!=b['cache_key'] and a['metrics']=={'trades':2,'profit_factor':1.2}

def test_worker_default_does_not_run_extra_window(monkeypatch):
    import app.backtest.engine as B
    from app.orchestrator.workers import bt_worker
    calls=[]
    class Result:ok=True;error=None;metrics={};trades=[];equity_curve=[]
    monkeypatch.setattr(B,'run_backtest',lambda req:(calls.append(req) or Result()))
    out=bt_worker({'genome':{},'dataset_id':'fixture','stage':'screen'})
    assert out['ok'] and len(calls)==1 and 'generation_window' not in out


def test_registry_ready_is_durable_metadata_only_after_first_reconciliation(tmp_path,monkeypatch):
    from app.db.database import Database
    from app.research import startup_registry as R
    import app.data.restoration as restoration
    db=Database(str(tmp_path/'registry.db'));calls=[]
    class ExistingRestoration:
        _restored=False
        def restore_all(self,emit_logs=False):calls.append('actual existing restoration entrypoint');return {'ok':True}
    engine=ExistingRestoration();monkeypatch.setattr(restoration,'get_restoration_engine',lambda:engine);monkeypatch.setattr(R,'_stamp',lambda:'actual-artifact-directory-stamp')
    assert R.ensure_ready(db)['mode']=='reconciled_from_actual_history'
    assert R.ensure_ready(Database(str(tmp_path/'registry.db')))['mode']=='persisted_registry'
    assert len(calls)==1 and engine._restored


def test_assignment_cache_tracks_replaced_database_connection(tmp_path):
    from app.db.database import Database
    from app.deeptest.filter_scenarios import save,assign,assignment
    a=Database(str(tmp_path/'before.db'));b=Database(str(tmp_path/'after.db'))
    for db in (a,b):db.insert_strategy({'hash':'existing-node','symbol':'XAUUSD','timeframe':'M1','genome':{},'status':'SURVIVED'})
    class ReopenedDatabase:
        active=a
        @property
        def conn(self):return self.active.conn
        def __getattr__(self,name):return getattr(self.active,name)
    db=ReopenedDatabase();sc=save(db,{'name':'Scoped original gate','scope':'GLOBAL','rules':{'exclude_days':['mon']}});assign(db,1,sc['id']);assert assignment(db,1)
    db.active=b
    assert assignment(db,1) is None # never leak an old node's gate into a replacement DB
