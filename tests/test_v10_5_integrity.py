"""Isolated regression fixtures, NOT the user's example run or market evidence."""
import copy,datetime as dt,math,json
from concurrent.futures import Future,ThreadPoolExecutor
import pytest
from test_v10_2_processing import database,configuration,genome

def node(db,tag='one'):
    return db.insert_strategy(dict(hash='v105-fixture-'+tag,genome=genome(),symbol='EURUSD',timeframe='M1',run_id='FIXTURE',status='QUALIFIED'))

def saved(db,ident='dt-v105-fixture',nid=None,cap=200000,currency='EUR',status='PARTIAL'):
    from app.deeptest import store as S
    nid=nid or node(db)
    S.insert_scenario(db,dict(run_id=ident,node_id=nid,status=status,settings={'total_capital':cap,'account_currency':currency},metrics={'initial_capital':10000,'max_drawdown_pct':.8486,'bars_processed':70938},analytics={'summary':{'trades':3,'max_drawdown_pct':3.99415}},coverage_status='PARTIAL',bars_used=70938))
    monday=dt.datetime(2026,7,6,tzinfo=dt.timezone.utc).timestamp()
    trades=[dict(net_pnl=-120,entry_ts=monday+3600,exit_ts=monday+30000),dict(net_pnl=200,entry_ts=monday+14*3600,exit_ts=monday+10000),dict(net_pnl=-150,entry_ts=monday+14*3600,exit_ts=monday+20000)]
    S.insert_trades(db,ident,trades);return ident,nid

def test_authoritative_chronology_capital_and_monetary_percent_pair(database):
    from app.deeptest import store as S
    from app.deeptest.metric_service import enrich
    rid,nid=saved(database);sc=enrich(database,S.get_scenario(database,rid));s=sc['analytics']['summary']
    assert s['initial_capital']==200000 and s['net_profit']==-70
    assert s['max_drawdown']==270 and s['max_drawdown_pct']==.00135
    assert s['return_percent']==-.035 and s['drawdown_percent']==.135
    assert s['account_currency']=='EUR' and s['profit_factor']==round(200/270,3)
    assert s['trades']==3 and s['win_rate']==round(1/3,4)
    assert sc['status']=='PARTIAL' and sc['metric_provenance']['bars_processed']==70938
    # Raw legacy evidence is not overwritten by corrected read projections.
    assert S.get_scenario(database,rid)['analytics']['summary']['max_drawdown_pct']==3.99415

def test_detail_table_projection_export_same_snapshot(database,monkeypatch):
    from app.api import routes as R
    from app.stats.deeptest_projection import build_index,effective_for
    from app.deeptest.scenarios import node_deeptest_summary
    rid,nid=saved(database);monkeypatch.setattr(R,'get_db',lambda:database)
    detail=R.deeptest_run_get(rid)['scenario'];table=node_deeptest_summary(database,nid)['preferred']
    assert detail['analytics']==table['analytics']
    assert effective_for(build_index(database),nid,{})['metrics']['max_drawdown_pct']==detail['metrics']['max_drawdown_pct']
    assert R.deeptest_metrics_export(rid)['analytics']==detail['analytics']
    assert detail['metric_provenance']['trade_snapshot']==table['metric_provenance']['trade_snapshot']

def test_filtered_rebuild_original_included_excluded_reconcile(database):
    from app.deeptest.metric_service import filtered
    rid,_=saved(database);out=filtered(database,rid,{'schema_version':1,'exclude_by_day':{'mon':['asia']}})
    a,b,c=[out[k]['summary'] for k in ('original','filtered','excluded')]
    assert out['trade_counts']==dict(original=3,kept=2,excluded=1)
    assert a['net_profit']==b['net_profit']+c['net_profit']==-70
    assert b['net_profit']==50 and c['net_profit']==-120
    assert b['initial_capital']==a['initial_capital']==200000
    assert b['max_drawdown']==150 and b['max_drawdown_pct']==.00075
    assert b['profit_factor']==round(200/150,3) and b['win_rate']==.5
    assert out['calendar']['total']['metrics']['max_drawdown']==150
    assert out['original']['provenance']['execution_status']=='PARTIAL'

@pytest.mark.parametrize('pnls,cap', [([],100),([2,3],100),([-2,-3],100),([1,-2],0),([1,-2],None),([-10000,2],10),([0,0],100)])
def test_edge_cases_finite_no_clamping_and_unknown_capital(pnls,cap):
    from app.deeptest.analytics import summarize
    trades=[dict(net_pnl=p,exit_ts=100+i,entry_ts=50+i) for i,p in enumerate(pnls)]
    result=summarize(trades,initial_capital=cap);s=result['summary']
    assert s['trades']==len(pnls) and s['net_profit']==sum(pnls)
    json.dumps(result,allow_nan=False)
    if cap is None or cap<=0:assert s['net_return_pct'] is None and s['max_drawdown_pct'] is None
    if pnls==[-10000,2]:assert s['max_drawdown_pct']==1000 and s['max_drawdown']==10000
    if pnls and all(x>=0 for x in pnls):assert s['profit_factor'] is None

@pytest.mark.parametrize('bad',[None,'bad',float('nan'),float('inf')])
def test_invalid_exit_timestamp_makes_path_metrics_unavailable(bad):
    from app.deeptest.analytics import summarize
    r=summarize([{'net_pnl':-50,'exit_ts':bad},{'net_pnl':10,'exit_ts':10}],initial_capital=100)
    assert r['summary']['max_drawdown'] is None and r['summary']['max_drawdown_pct'] is None
    assert r['summary']['sharpe'] is None and r['summary']['net_profit']==-40 and r['unavailable']

def test_tied_exit_timestamp_uses_stable_trade_number_not_input_order():
    from app.deeptest.analytics import summarize
    trades=[{'trade_no':1,'exit_ts':1,'net_pnl':100},{'trade_no':2,'exit_ts':1,'net_pnl':-90},{'trade_no':3,'exit_ts':1,'net_pnl':10}]
    assert summarize(trades,initial_capital=100)==summarize(list(reversed(trades)),initial_capital=100)

def test_full_history_no_3000_limit_empty_filter_and_cache_invalidation(database):
    from app.deeptest import store as S
    from app.deeptest.metric_service import enrich,filtered
    from app.db.database import Database
    rid,nid=saved(database);database.x('DELETE FROM deeptest_trades WHERE run_id=?',(rid,))
    S.insert_trades(database,rid,[{'net_pnl':1,'entry_ts':1783299600,'exit_ts':1783299700+i} for i in range(6201)])
    sc=enrich(database,S.get_scenario(database,rid));assert sc['analytics']['summary']['trades']==6201
    trace=[];database.conn.set_trace_callback(trace.append);enrich(database,S.get_scenario(database,rid))
    assert not any('SELECT * FROM deeptest_trades' in x for x in trace)
    other=Database(database.path)
    try:other.x('UPDATE deeptest_trades SET net_pnl=-100 WHERE run_id=? AND trade_no=1',(rid,))
    finally:other.close()
    assert enrich(database,S.get_scenario(database,rid))['analytics']['summary']['net_profit']==6100
    out=filtered(database,rid,{'schema_version':1,'exclude_days':['mon','tue','wed','thu','fri','sat','sun']})
    assert out['filtered']['summary']['trades']==0 and out['excluded']['summary']['trades']==6201
    assert S.list_trades(database,rid,offset=3000,limit=1)['trades'][0]['trade_no']==3001

def test_missing_capital_never_falls_back_to_10000(database):
    from app.deeptest.metric_service import enrich
    from app.deeptest import store as S
    rid,_=saved(database);S.update_scenario(database,rid,settings={},metrics={},analytics={})
    s=enrich(database,S.get_scenario(database,rid))['analytics']['summary']
    assert s['initial_capital'] is None and s['net_return_pct'] is None and s['max_drawdown_pct'] is None

def test_conflicting_duplicate_evidence_refuses_metrics():
    from app.deeptest.analytics import summarize
    a=dict(run_id='x',trade_no=1,exit_ts=1,net_pnl=1)
    with pytest.raises(ValueError):summarize([a,{**a,'net_pnl':-3}],initial_capital=100)

@pytest.mark.parametrize('days,sessions',[([0],['asia']),([1,4],['london','newyork']),([6],[])])
def test_assignment_replaces_old_days_sessions_and_persists_snapshot(database,days,sessions):
    from app.deeptest import filter_scenarios as F
    from app.db.database import Database
    nid=node(database);database.set_live_test_config(nid,{'days':[2,3],'sessions':['asia'],'cooldown_minutes':17,'is_active':False})
    rules={'exclude_days':[d for i,d in enumerate(['mon','tue','wed','thu','fri','sat','sun']) if i not in days],'include_sessions':sessions}
    sc=F.save(database,dict(name='actual saved fixture',scope='NODE',node_id=nid,rules=rules))
    F.assign(database,nid,sc['id'])
    other=Database(database.path)
    try:
        assigned=F.assignment(other,nid);view=F.schedule_view(other.get_live_test_config(nid),assigned['rules'])
        assert view['days']==days and view['cooldown_minutes']==17
        assert set(view['sessions'])==set(sessions or ['asia','london','newyork','off','asia_london_overlap','london_newyork_overlap'])
        assert not view['is_active']
    finally:other.close()
    assert database.get_live_test_config(nid)['days']==[2,3] # removal restores original, no risk/config rewrite
    replacement=F.save(database,dict(name='replacement',scope='NODE',node_id=nid,rules={'exclude_days':['mon','tue','wed','thu','fri','sat'],'include_sessions':['newyork']}))
    F.assign(database,nid,replacement['id']);assert F.schedule_view({},F.assignment(database,nid)['rules'])['days']==[6]

def test_assigned_gate_takes_precedence_only_for_assigned_schedule():
    from app.live_testing.schedule import evaluate
    ts=dt.datetime(2026,7,6,1,tzinfo=dt.timezone.utc).timestamp()
    r={'schema_version':1,'exclude_days':['tue','wed','thu','fri','sat','sun'],'include_sessions':['asia']}
    base={'days':[2],'sessions':['newyork']}
    assert not evaluate(base,now=ts)['allowed']
    assert evaluate({**base,'filter_rules':{**r,'_assigned_schedule':True}},now=ts)['allowed']

def test_stale_schedule_save_refused_before_any_write(database,monkeypatch):
    from app.api import routes as R
    from app.deeptest import filter_scenarios as F
    from fastapi import HTTPException
    nid=node(database);sc=F.save(database,dict(name='saved',scope='NODE',node_id=nid,rules={'include_sessions':['asia']}));F.assign(database,nid,sc['id'])
    monkeypatch.setattr(R,'get_db',lambda:database)
    with pytest.raises(HTTPException) as e:R.live_testing_schedule_save(nid,{'days':[4],'sessions':['newyork'],'expected_assignment_version':'stale'})
    assert e.value.status_code==409 and database.get_live_test_config(nid) is None

def test_bulk_selected_scope_definitions_live_shared_and_protected_records(database):
    from app.deeptest import management as M,filter_scenarios as F,store as S
    a=node(database,'a');b=node(database,'b');ra,_=saved(database,'dt-a',a);rb,_=saved(database,'dt-b',b)
    database.add_to_shortlist(a,'preserve');database.set_live_test_config(a,{'is_active':False,'days':[2],'risk_pct':2})
    database.x('CREATE TABLE ai_knowledge_fixture(id INTEGER PRIMARY KEY,note TEXT)');database.x("INSERT INTO ai_knowledge_fixture VALUES (1,'preserve')")
    database.x('CREATE TABLE market_fixture(id INTEGER PRIMARY KEY,close REAL)');database.x('INSERT INTO market_fixture VALUES (1,4)')
    delete_def=F.save(database,dict(name='deep only',scope='NODE',node_id=a,rules={}));F.assign(database,a,delete_def['id'],'deep')
    live_def=F.save(database,dict(name='live shared',scope='NODE',node_id=a,rules={}));F.assign(database,a,live_def['id'],'live')
    plan=M.preview(database,[a]);assert plan['runs']==1 and plan['trade_records']==3 and plan['scenarios']==1
    result=M.bulk_delete(database,[a],'DELETE SELECTED NODE SCENARIOS');assert result['deleted_scenarios']==1 and result['deleted_definitions']==1
    assert S.get_scenario(database,rb) and not S.get_scenario(database,ra)
    assert database.get_strategy(a) and database.get_strategy(b) and database.get_live_test_config(a)['risk_pct']==2
    assert database.one('SELECT COUNT(*) c FROM research_shortlist')['c']==1
    assert database.one('SELECT * FROM ai_knowledge_fixture') and database.one('SELECT * FROM market_fixture')
    assert F.assignment(database,a,'live')['id']==live_def['id'] and F.assignment(database,a,'deep') is None
    with pytest.raises(ValueError):S.insert_trades(database,ra,[{'net_pnl':1}])
    M.bulk_delete(database,None,'DELETE ALL SCENARIOS');assert not S.list_scenarios(database)
    assert F.assignment(database,a,'live') and database.get_strategy(a)

def test_failed_transaction_rolls_back_every_owned_record(database):
    from app.deeptest import management as M,store as S
    import sqlite3
    rid,nid=saved(database)
    database.x("CREATE TRIGGER refuse_delete BEFORE DELETE ON deeptest_scenarios BEGIN SELECT RAISE(ABORT,'fixture rollback'); END")
    with pytest.raises(sqlite3.IntegrityError):M.bulk_delete(database,[nid],'DELETE SELECTED NODE SCENARIOS')
    assert S.get_scenario(database,rid) and S.list_trades(database,rid)['total']==3
    assert not database.one('SELECT * FROM deeptest_deleted_runs WHERE run_id=?',(rid,))

def test_confirmation_empty_selection_and_active_future_guards(database):
    from app.deeptest import management as M,scenarios as SC,store as S
    rid,nid=saved(database);future=Future();future.set_running_or_notify_cancel();SC._FUTURES[rid]=future
    try:
        with pytest.raises(ValueError):M.bulk_delete(database,[nid],'yes')
        with pytest.raises(ValueError):M.preview(database,[])
        with pytest.raises(M.DeletionBusy):M.delete(database,node_ids=[nid],confirmation='DELETE SELECTED NODE SCENARIOS')
        assert S.get_scenario(database,rid)
    finally:future.set_result(None);SC._FUTURES.pop(rid,None)


def test_missing_closed_trade_records_are_not_advertised_as_zero_metrics(database):
    from app.deeptest import store as S
    from app.deeptest.metric_service import enrich,filtered
    rid,_=saved(database);database.x('DELETE FROM deeptest_trades WHERE run_id=?',(rid,))
    sc=enrich(database,S.get_scenario(database,rid))
    assert sc['analytics']['summary']['net_profit'] is None and sc['analytics']['summary']['trades'] is None
    assert sc['metric_provenance']['integrity']=='MISSING_TRADE_RECORDS'
    assert sc['metric_provenance']['available_trade_rows']==0 and sc['metric_provenance']['stored_expected_trade_count']==3
    with pytest.raises(ValueError,match='missing'):filtered(database,rid,{'schema_version':1})


def test_delete_all_cancels_awaiting_batch_and_running_unowned_fixture_before_deleting(database):
    from app.deeptest import management as M,bulk,store as S,scenarios as SC
    rid,nid=saved(database,status='RUNNING');bulk.schema(database)
    database.x("INSERT INTO deeptest_batches(id,status,payload,results,updated_at) VALUES (?,?,?,?,?)",('fixture-batch','AWAITING_COVERAGE_DECISION',json.dumps({'node_ids':[nid]}),'[]',1))
    out=M.bulk_delete(database,None,'DELETE ALL SCENARIOS')
    assert out['ok'] and not S.get_scenario(database,rid)
    assert not database.q('SELECT * FROM deeptest_batches')
    assert database.one('SELECT run_id FROM deeptest_deleted_runs WHERE run_id=?',(rid,))
    SC._CANCELS.discard(rid)


def test_selected_delete_never_cancels_batch_containing_unrelated_nodes(database):
    from app.deeptest import management as M,bulk,store as S
    rid,nid=saved(database);bulk.schema(database)
    database.x("INSERT INTO deeptest_batches(id,status,payload,results,updated_at) VALUES (?,?,?,?,?)",('fixture-shared','AWAITING_COVERAGE_DECISION',json.dumps({'node_ids':[nid,nid+1]}),'[]',1))
    with pytest.raises(M.DeletionBusy,match='unrelated'):M.bulk_delete(database,[nid],'DELETE SELECTED NODE SCENARIOS')
    assert S.get_scenario(database,rid) and database.one('SELECT cancel_requested FROM deeptest_batches')['cancel_requested']==0


def test_reconciliation_tolerance_does_not_grow_with_trade_count():
    from app.deeptest.analytics import reconcile
    rows=[{'net_pnl':1.} for _ in range(6200)]
    assert reconcile(rows,{'net_profit':6200.})['ok']
    assert not reconcile(rows,{'net_profit':6100.})['ok']


@pytest.mark.parametrize('origin',['seed','ai'])
def test_local_and_ai_nodes_share_authoritative_persistent_shortlist(database,monkeypatch,origin):
    from app.api.routes import nodes_index
    from app.api import routes as R
    nid=node(database,origin);database.x('UPDATE strategies SET origin=? WHERE id=?',(origin,nid))
    monkeypatch.setattr(R,'get_db',lambda:database)
    database.add_to_shortlist(nid,'isolated authoritative favourite')
    assert nid in {r['node_id'] for r in nodes_index(filter='all',starred_only=True,limit=0)['nodes']}
    database.x('DELETE FROM research_shortlist WHERE strategy_id=?',(nid,))
    assert nid not in {r['node_id'] for r in nodes_index(filter='all',starred_only=True,limit=0)['nodes']}


def test_financial_cpu_work_does_not_hold_the_database_writer_lock(database,monkeypatch):
    from app.deeptest import metric_service as M,analytics as A,store as S
    rid,_=saved(database);original=A.summarize;checks=[]
    with ThreadPoolExecutor(max_workers=1) as pool:
        def inspect_lock(*args,**kwargs):
            if not checks:
                assert pool.submit(database.one,'SELECT 1 probe').result(timeout=2)['probe']==1
                checks.append(True)
            return original(*args,**kwargs)
        monkeypatch.setattr(A,'summarize',inspect_lock)
        assert M.enrich(database,S.get_scenario(database,rid))['analytics']['summary']['trades']==3
        assert checks


def test_bulk_delete_proves_definition_domain_and_preserves_unassigned_live_or_ambiguous_templates(database):
    from app.deeptest import filter_scenarios as F,management as M
    rid,nid=saved(database)
    live=F.save(database,dict(name='unassigned LIVE schedule',scope='NODE',node_id=nid,rules={}),owner_target='live')
    unknown=F.save(database,dict(name='unknown legacy ownership',scope='NODE',node_id=nid,rules={},provenance={'domain':'deeptest'}))
    deep=F.save(database,dict(name='unassigned DEEP schedule',scope='NODE',node_id=nid,rules={}),owner_target='deep')
    M.bulk_delete(database,[nid],'DELETE SELECTED NODE SCENARIOS')
    ids={row['id'] for row in F.listing(database,nid)}
    assert live['id'] in ids and unknown['id'] in ids and deep['id'] not in ids
    assert 'domain' not in unknown['provenance'] # client cannot widen deletion ownership
