"""V10.4 isolated real-SQL/engine safety tests; no authoritative research."""
from concurrent.futures import CancelledError,Future,ThreadPoolExecutor
import hashlib
import inspect
import json
import os
from pathlib import Path
import pytest
from test_v10_2_processing import database,configuration,bars,request,actual,genome


def scenario(db,ident='dt-fixture',status='COMPLETED',node_id=1):
    from app.deeptest import store as S
    S.insert_scenario(db,dict(run_id=ident,node_id=node_id,status=status));return ident


def test_atomic_chunks_reduce_commits_without_losing_trades(database):
    from app.deeptest import store as S
    ident=scenario(database);calls=[];database.conn.set_trace_callback(calls.append)
    S.insert_trades(database,ident,[{'net_pnl':float(i),'entry_ts':1.,'exit_ts':2.} for i in range(6200)])
    assert sum(x=='COMMIT' for x in calls)<=26
    page=S.list_trades(database,ident,offset=3000,limit=1)
    assert page['total']==6200 and page['trades'][0]['trade_no']==3001 and page['trades'][0]['net_pnl']==3000.


def test_retry_equivalent_prefix_is_idempotent_and_conflict_is_refused(database):
    from app.deeptest import store as S
    ident=scenario(database);trades=[{'net_pnl':float(i),'sl':float('nan')} for i in range(600)]
    calls=[]
    def cancel():calls.append(1);return len(calls)>300
    with pytest.raises(CancelledError):S.insert_trades(database,ident,trades,cancelled=cancel)
    assert S.list_trades(database,ident)['total']==300
    S.insert_trades(database,ident,trades);S.insert_trades(database,ident,trades)
    assert S.list_trades(database,ident)['total']==600
    bad=[dict(t) for t in trades];bad[300]['net_pnl']=-999.
    with pytest.raises(ValueError,match='differs'):S.insert_trades(database,ident,bad)
    assert S.list_trades(database,ident)['total']==600


def test_chunk_sql_failure_rolls_back_only_current_checkpoint_and_can_resume(database):
    from app.deeptest import store as S
    import sqlite3
    ident=scenario(database)
    database.x("CREATE TRIGGER reject_fixture BEFORE INSERT ON deeptest_trades WHEN NEW.trade_no>=400 BEGIN SELECT RAISE(ABORT,'fixture'); END")
    trades=[{'net_pnl':float(i)} for i in range(1000)]
    with pytest.raises(sqlite3.IntegrityError):S.insert_trades(database,ident,trades)
    assert S.list_trades(database,ident)['total']==257
    database.x('DROP TRIGGER reject_fixture');S.insert_trades(database,ident,trades)
    assert S.list_trades(database,ident)['total']==1000


def test_two_connections_retry_same_run_without_duplicate_persistence(database):
    from app.db.database import Database
    from app.deeptest import store as S
    ident=scenario(database);other=Database(database.path)
    trades=[{'net_pnl':float(i)} for i in range(1000)]
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(S.insert_trades,db,ident,trades) for db in (database,other)]
            for f in futures:f.result(timeout=20)
        assert S.list_trades(database,ident)['total']==1000
    finally:other.close()


def protected_fixture(db):
    sid=db.insert_strategy(dict(hash='protected-fixture',genome=genome(),symbol='EURUSD',timeframe='M1',run_id='REAL-FIXTURE',data_source='USER_RESEARCH'))
    db.x('CREATE TABLE unrelated_results(id INTEGER PRIMARY KEY,payload TEXT)');db.x("INSERT INTO unrelated_results VALUES (1,'keep')")
    db.x('CREATE TABLE shared_market_fixture(ts REAL,close REAL)');db.x('INSERT INTO shared_market_fixture VALUES (1,2)')
    db.add_to_shortlist(sid,'preserve authoritative star')
    return sid


def preserved(db):
    return {t:db.q('SELECT * FROM '+t) for t in ['strategies','unrelated_results','shared_market_fixture','research_shortlist','deeptest_settings','deeptest_node_configs','deeptest_candle_sources']}


@pytest.mark.parametrize('all',[False,True])
def test_confirmed_delete_scope_and_durable_recreation_guard(database,all):
    from app.deeptest import store as S
    from app.deeptest.management import delete
    sid=protected_fixture(database);scenario(database,'dt-one',node_id=sid);scenario(database,'dt-two',node_id=sid)
    S.insert_trades(database,'dt-one',[{'net_pnl':1.}]);S.insert_trades(database,'dt-two',[{'net_pnl':2.}]);S.set_pref(database,sid,'dt-one')
    S.save_settings(database,{'total_capital':12345}) if hasattr(S,'save_settings') else None
    before=preserved(database)
    result=delete(database,confirmation='DELETE ALL DEEPTEST DATA') if all else delete(database,run_id='dt-one',confirmation='DELETE dt-one')
    assert result['ok'] and preserved(database)==before
    assert S.get_scenario(database,'dt-one') is None and S.list_trades(database,'dt-one')['total']==0
    assert (S.get_scenario(database,'dt-two') is None)==all
    assert S.get_pref(database,sid) is None
    with pytest.raises(ValueError,match='deleted'):S.insert_trades(database,'dt-one',[{'net_pnl':1.}])
    with pytest.raises(ValueError,match='deleted'):scenario(database,'dt-one')
    with pytest.raises(ValueError,match='Deleted'):S.set_pref(database,sid,'dt-one')


@pytest.mark.parametrize('status',['QUEUED','FETCHING','VALIDATING','RUNNING'])
def test_active_scenario_deletion_refused(database,status):
    from app.deeptest.management import delete,DeletionBusy
    scenario(database,status=status)
    with pytest.raises(DeletionBusy):delete(database,run_id='dt-fixture',confirmation='DELETE dt-fixture')


def test_cancelled_but_future_still_writing_is_not_deletable(database,monkeypatch):
    from app.deeptest.management import delete,DeletionBusy
    from app.deeptest import scenarios as SC
    ident=scenario(database,status='CANCELLED');future=Future();monkeypatch.setitem(SC._FUTURES,ident,future)
    with pytest.raises(DeletionBusy,match='stopping'):delete(database,run_id=ident,confirmation='DELETE '+ident)
    future.set_result(None);assert delete(database,run_id=ident,confirmation='DELETE '+ident)['ok']


def test_foreign_live_owner_refused(database,monkeypatch):
    from app.deeptest import management as M
    ident=scenario(database);database.x('CREATE TABLE deeptest_claims(signature TEXT,run_id TEXT,owner_pid INTEGER)')
    database.x('INSERT INTO deeptest_claims VALUES (?,?,?)',('fixture',ident,987654))
    monkeypatch.setattr(M,'alive',lambda pid:True)
    with pytest.raises(M.DeletionBusy,match='foreign'):M.delete(database,run_id=ident,confirmation='DELETE '+ident)


def test_delete_requires_confirmation_and_backend_scope_cannot_be_widened(database):
    from app.deeptest.management import delete
    from app.api.routes import router
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    ident=scenario(database);app=FastAPI();app.include_router(router)
    with pytest.raises(ValueError):delete(database,run_id=ident,confirmation='yes')
    with TestClient(app) as client:
        assert client.post('/api/deeptest/manage/delete-all',json={'confirmation':'yes','table':'strategies'}).status_code==422
        assert client.post('/api/deeptest/manage/delete',json={'run_id':ident,'confirmation':'DELETE '+ident,'table':'strategies'}).json()['ok']


def test_delete_only_marked_owned_scratch_preserves_shared_files(database,tmp_path,monkeypatch):
    from app.deeptest.management import delete
    from app.deeptest import management as M
    ident=scenario(database);monkeypatch.setattr(M.tempfile,'gettempdir',lambda:str(tmp_path))
    root=tmp_path/'EvolutionaryTradingLab/DeepTest';owned=root/(ident+'-abcd1234');owned.mkdir(parents=True)
    (owned/'.deeptest-owner.json').write_text(json.dumps({'id':owned.name,'pid':0}))
    (owned/'result.pickle').write_bytes(b'owned-fixture')
    shared=root/'shared-cache';shared.mkdir();(shared/'keep').write_text('keep')
    delete(database,run_id=ident,confirmation='DELETE '+ident)
    assert not owned.exists() and (shared/'keep').read_text()=='keep'


def test_partial_status_and_connection_reset_do_not_erase_committed_results(database):
    from app.deeptest import store as S
    from app.speed_api import SpeedMiddleware
    from fastapi import FastAPI
    ident=scenario(database,status='PARTIAL');S.insert_trades(database,ident,[{'net_pnl':1.}]*4000)
    # Transport send failure belongs to ASGI request, not the durable worker contract.
    import asyncio
    async def app(scope,receive,send):await send({'type':'http.response.body','body':b'fixture'})
    async def send(message):raise ConnectionResetError(10054,'fixture disconnect')
    async def receive():return {'type':'http.request','body':b''}
    with pytest.raises(ConnectionResetError):asyncio.run(SpeedMiddleware(app)({'type':'http','path':'/fixture'},receive,send))
    assert S.get_scenario(database,ident)['status']=='PARTIAL' and S.list_trades(database,ident)['total']==4000


def test_window_counts_do_not_confuse_full_source_with_eligible_or_warmup(configuration,tmp_path):
    frame=bars(8000);req=request(frame,max_trades=None);req.window=(1000,7000)
    result=actual(req,frame,tmp_path)
    assert result.ok and result.metrics['bars_processed']==6000 and result.metrics['eligible_bars']==6000
    assert len(frame)==8000 and result.trades[0]['entry_ts']>=float(frame.ts.iloc[1000])


def test_alias_count_in_insert_keeps_serial_ids_and_explicit_aliases(database):
    for i in range(4):
        sid=database.insert_strategy(dict(hash=str(i),genome=genome(),symbol='EURUSD',timeframe='M1',run_id='FIXTURE',research_node_num=55 if i==3 else None))
        row=database.one('SELECT research_node_num FROM strategies WHERE id=?',(sid,));assert row['research_node_num']==(55 if i==3 else i+1)


def test_live_historical_result_mapping_source_is_unchanged():
    from app.api.routes import nodes_index
    # Baseline function body is pinned, not rewritten for the STARRED category.
    expected=json.loads((Path(__file__).parent/'fixtures/v104_preserved_live.json').read_text())
    assert hashlib.sha256(inspect.getsource(nodes_index).encode()).hexdigest()==expected['nodes_index']
    repo=Path(__file__).resolve().parents[1]
    for file,digest in expected['files'].items():assert hashlib.sha256((repo/file).read_bytes()).hexdigest()==digest


def test_batch_writer_and_awaiting_confirmation_block_delete_all(database,monkeypatch):
    from app.deeptest import bulk,management as M
    bulk.schema(database);scenario(database)
    database.x('INSERT INTO deeptest_batches(id,status,payload,results,updated_at,owner_pid) VALUES (?,?,?,?,?,?)',('fixture','AWAITING_COVERAGE_DECISION','{}','[]',1,os.getpid()))
    with pytest.raises(M.DeletionBusy,match='batch'):M.delete(database,confirmation='DELETE ALL DEEPTEST DATA')
    database.x("UPDATE deeptest_batches SET status='COMPLETED'")
    assert M.delete(database,confirmation='DELETE ALL DEEPTEST DATA')['ok']
    assert database.one('SELECT COUNT(*) c FROM deeptest_batches')['c']==0


def test_deleted_one_does_not_leave_completed_batch_projection(database):
    from app.deeptest import bulk,management as M
    bulk.schema(database);scenario(database,'dt-one');scenario(database,'dt-two')
    results=[{'node_id':1,'run_id':'dt-one','status':'COMPLETED'},{'node_id':2,'run_id':'dt-two','status':'COMPLETED'}]
    database.x('INSERT INTO deeptest_batches(id,status,payload,results,updated_at,owner_pid) VALUES (?,?,?,?,?,?)',('fixture','COMPLETED','{}',json.dumps(results),1,os.getpid()))
    M.delete(database,run_id='dt-one',confirmation='DELETE dt-one')
    assert json.loads(database.one('SELECT results FROM deeptest_batches')['results'])==[results[1]]


def test_real_mutation_crossover_selection_lineage_and_rng_match_frozen_insert(tmp_path,configuration,monkeypatch):
    import importlib.util,types
    from app.db import database as D
    from app.evolution.engine import EvolutionEngine
    from app.evolution import engine as E
    reference=Path(__file__).parent/'fixtures/v104_insert_reference.py'
    spec=importlib.util.spec_from_file_location('v104_reference',reference);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(E.time,'time',lambda:1700000000.)
    outputs=[]
    for optimized in (False,True):
        db=D.Database(str(tmp_path/('optimized.db' if optimized else 'reference.db')))
        monkeypatch.setattr(D,'_db',db)
        if not optimized:db.insert_strategy=types.MethodType(module.insert_strategy,db)
        engine=EvolutionEngine(db,run_id='ISOLATED-LINEAGE-FIXTURE');engine._apply_timeframe_scope=lambda symbol:['M1'];engine.set_total_node_target(100)
        assert engine.seed_population(20,'EURUSD')==20
        db.x("UPDATE strategies SET status='SURVIVED',fitness=1.0")
        counts=engine.reproduce(20,'EURUSD')
        records=db.q('SELECT id,hash,parent_id,generation,genome,research_node_num,mutation_type,creation_reason FROM strategies ORDER BY id')
        assert any(r['parent_id'] for r in records)
        outputs.append((counts,records,engine._rng.getstate()))
        db.close()
    assert outputs[0]==outputs[1]


def test_cancel_delete_writer_lock_order_has_no_deadlock(database,monkeypatch):
    from app.deeptest import store as S,scenarios as SC,management as M
    import threading,time
    ident=scenario(database,status='RUNNING');committed=threading.Event();release=threading.Event();original=database.x;errors=[]
    def checkpoint(sql,args=()):
        result=original(sql,args)
        if 'INSERT INTO deeptest_trades' in sql:committed.set();assert release.wait(5)
        return result
    monkeypatch.setattr(database,'x',checkpoint)
    def writer():
        try:S.insert_trades(database,ident,[{'net_pnl':1.}]*200,cancelled=lambda:ident in SC._CANCELS)
        except CancelledError:pass
    def deleter():
        try:M.delete(database,run_id=ident,confirmation='DELETE '+ident)
        except M.DeletionBusy:pass
        except Exception as exc:errors.append(exc)
    thread=threading.Thread(target=writer);thread.start();assert committed.wait(3)
    cancel=threading.Thread(target=lambda:SC.cancel_run(database,ident));cancel.start()
    deadline=time.monotonic()+3
    while ident not in SC._CANCELS and time.monotonic()<deadline:time.sleep(.001)
    delete=threading.Thread(target=deleter);delete.start();release.set()
    for t in (thread,cancel,delete):t.join(5);assert not t.is_alive()
    SC._CANCELS.discard(ident);assert not errors


@pytest.mark.parametrize('unexpected_number',[None,0,3])
def test_retry_refuses_unmatched_historical_evidence_instead_of_marking_success(database,unexpected_number):
    from app.deeptest import store as S
    ident=scenario(database)
    database.x('INSERT INTO deeptest_trades(run_id,trade_no) VALUES (?,?)',(ident,unexpected_number))
    with pytest.raises(ValueError):S.insert_trades(database,ident,[{'net_pnl':1.}]*2)
    assert database.one('SELECT COUNT(*) c FROM deeptest_trades WHERE run_id=?',(ident,))['c']==1


def test_deletion_never_follows_scratch_root_link_outside_temp(database,tmp_path,monkeypatch):
    from app.deeptest import management as M
    ident=scenario(database);temp=tmp_path/'temp';outside=tmp_path/'outside';temp.mkdir();outside.mkdir()
    (temp/'EvolutionaryTradingLab').mkdir()
    try:(temp/'EvolutionaryTradingLab/DeepTest').symlink_to(outside,target_is_directory=True)
    except (OSError,NotImplementedError):pytest.skip('Native symlink privilege unavailable; no privilege/environment change requested')
    monkeypatch.setattr(M.tempfile,'gettempdir',lambda:str(temp))
    with pytest.raises(M.DeletionBusy,match='Unsafe TEMP'):M.delete(database,run_id=ident,confirmation='DELETE '+ident)
    assert database.one('SELECT run_id FROM deeptest_scenarios WHERE run_id=?',(ident,))
