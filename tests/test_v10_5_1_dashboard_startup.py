"""Startup regression contracts on isolated synthetic saved records, not native DATA."""
from concurrent.futures import ThreadPoolExecutor
import threading
import pytest
from test_v10_2_processing import database, configuration
from test_v10_5_integrity import saved,node


def test_generation_index_never_reads_or_calculates_deeptest_trades(database, configuration, monkeypatch):
    from app.api import routes as R
    from app.deeptest import metric_service as M
    from app.deeptest import store as S
    rid,nid=saved(database)
    for i in range(6):saved(database,ident=f'dt-startup-archive-{i}',nid=nid)
    calls=[]
    def forbidden(*args,**kwargs):
        calls.append(True)
        raise AssertionError('Generation-only startup must not enter DeepTest authority')
    monkeypatch.setattr(M,'enrich_many',forbidden)
    sql=[];database.conn.set_trace_callback(sql.append)
    try:
        result=R.nodes_index(filter='all',limit=15,compact=True,metric_source='generation')
        assert result['ok'] and result['total']==1
        assert not calls
        assert not any('deeptest_trades' in q.lower() for q in sql)
        assert S.get_scenario(database,rid)['analytics']['summary']['trades']==3
    finally:database.conn.set_trace_callback(None)


@pytest.mark.parametrize('preference',['explicit','implicit','failed','partial','missing'])
def test_projection_selects_headers_before_authoritative_calculation(database,preference,monkeypatch):
    from app.deeptest import metric_service as M,store as S
    from app.stats.deeptest_projection import build_index,effective_for
    older,nid=saved(database,ident='dt-startup-older')
    newer,_=saved(database,ident='dt-startup-newer',nid=nid)
    S.update_scenario(database,older,created_at=1,status='FAILED' if preference=='failed' else 'PARTIAL' if preference=='partial' else 'COMPLETED')
    S.update_scenario(database,newer,created_at=2)
    if preference!='missing':S.set_pref(database,nid,older,explicit=preference!='implicit')
    other,nid2=saved(database,ident='dt-startup-other',nid=node(database,'other'))
    calculated=[];real=M.calculate
    def capture(sc,trades,*a,**kw):
        calculated.append(sc['run_id']);return real(sc,trades,*a,**kw)
    monkeypatch.setattr(M,'calculate',capture)
    index=build_index(database)
    chosen=older if preference in ('explicit','partial') else newer
    assert set(calculated)=={chosen,other} and len(calculated)==2
    assert index[nid]['valid_count']==(1 if preference=='failed' else 2)
    assert index[nid]['preferred']['run_id']==chosen
    assert index[nid]['preferred']['metric_provenance']['metric_version']==M.VERSION
    assert effective_for(index,nid,{})['metrics']['max_drawdown_pct']==.00135
    assert index[nid]['preferred']['metrics']['max_drawdown']==270
    calculated.clear();build_index(database);assert not calculated
    # Invalidation retains V10.5 authority: changed raw P&L cannot leave cached metrics.
    database.x('UPDATE deeptest_trades SET net_pnl=-220 WHERE run_id=? AND trade_no=1',(chosen,))
    assert build_index(database)[nid]['preferred']['analytics']['summary']['net_profit']==-170


def test_selected_missing_trade_evidence_stays_na_not_legacy_snapshot(database):
    from app.deeptest import store as S
    from app.stats.deeptest_projection import build_index
    rid,nid=saved(database)
    database.x('DELETE FROM deeptest_trades WHERE run_id=?',(rid,))
    block=build_index(database)[nid]['preferred']
    assert block['analytics']['summary']['max_drawdown_pct'] is None
    assert block['metric_provenance']['integrity']=='MISSING_TRADE_RECORDS'
    assert S.get_scenario(database,rid)['analytics']['summary']['max_drawdown_pct']==3.99415


def test_startup_status_refresh_retry_and_generation_requests_during_deep_calculation(database,configuration,monkeypatch):
    from app.orchestrator import lab as L
    from app.evolution import engine as E
    # Restore process singletons after this temporary DB closes; do not leak
    # its engine into later existing status tests in the same pytest process.
    monkeypatch.setattr(L,'_lab',None)
    monkeypatch.setattr(E,'_evo_engine',None)
    from app.main import app
    from fastapi.testclient import TestClient
    from app.deeptest import metric_service as M
    from app.stats.deeptest_projection import build_index
    saved(database)
    started=threading.Event();release=threading.Event();real=M.calculate
    def blocking(*a,**kw):
        started.set()
        assert release.wait(15),'test did not release independent DeepTest calculation'
        return real(*a,**kw)
    monkeypatch.setattr(M,'calculate',blocking)
    client=TestClient(app) # route-level startup requests, no native broker/lifespan bootstrap
    with ThreadPoolExecutor(max_workers=1) as pool:
        job=pool.submit(build_index,database)
        try:
            assert started.wait(5)
            for _ in range(3):
                for url in ('/api/status','/api/system/status_row','/api/nodes?filter=all&limit=15&compact=true&metric_source=generation'):
                    response=client.get(url)
                    assert response.status_code==200,(url,response.text[:300])
                    body=response.json()
                    if url=='/api/status':assert body['status_counts']['QUALIFIED']==1
                    elif url=='/api/system/status_row':
                        assert body['db']['total_strategies']==1
                        assert 'cpu' in body and 'gpu' in body and 'mt5' in body
                    else:assert body['total']==1
                assert client.get('/health').status_code in (200,503) # lifespan/readiness separate from responsive routing
                assert client.get('/api/ready').status_code in (200,503)
            assert not release.is_set() and not job.done()
        finally:release.set()
        assert job.result(timeout=10)
