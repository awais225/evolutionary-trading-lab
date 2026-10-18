"""V9.1 canonical projection/bookmarks/range regressions; no real orders."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_v6_8_authority_and_workflow import _seed
from test_v6_9_canonical_data import patched_db
from test_v7_0_range_fetch import _WindowBridge, _MemStore, _friday_noon_utc
from app.db.database import Database


@pytest.fixture()
def registry(tmp_path, monkeypatch):
    db = Database(str(tmp_path / 'registry.db'))
    _seed(db, 718, status='SURVIVED', research_num=318, run_id='PRESERVED', metrics={'total_return_pct': 1.481, 'trades': 905})
    _seed(db, 1505, status='QUALIFIED', research_num=718, run_id='CURRENT', metrics={'total_return_pct': .0917, 'trades': 57})
    _seed(db, 1506, status='PAPER', research_num=None, run_id='CURRENT', metrics={'total_return_pct': .2})
    _seed(db, 1507, status='QUALIFIED', research_num=4, run_id='PRESERVED', data_source='LEGACY_TEST')
    import app.api.routes as R
    class Idle:
        def eligible_nodes(self): return []
        def excluded_nodes(self): return []
    monkeypatch.setattr(R, '_live_engine', lambda: Idle())
    monkeypatch.setattr(R, '_worker_state', lambda sid: {'state': 'IDLE', 'running': False})
    with patched_db(db):
        yield db, R
    db.close()


def test_default_frozen_qualified_and_separate_gate_inventory(registry):
    db, R = registry
    v = R.nodes_index(limit=0)
    assert {r['node_id'] for r in v['nodes']} == {1505, 1506}
    assert v['total'] == v['filter_totals']['qualified'] == 2
    assert 718 not in {r['node_id'] for r in v['nodes']}
    assert R.nodes_index(filter='gate_passed', limit=0)['total'] == 3
    assert 1507 not in {r['node_id'] for r in R.nodes_index(filter='all',limit=0)['nodes']}
    assert v['population_snapshot']['legacy_total']==1


def test_aliases_are_not_guessed_or_merged_and_run_provenance_is_own(registry):
    db, R = registry
    rows = R.nodes_index(search='718', limit=0)['nodes']
    assert {r['node_id'] for r in rows} == {718, 1505}
    by = {r['node_id']: r for r in rows}
    assert by[718]['generation_metrics']['return_pct'] == 1.481
    assert by[1505]['generation_metrics']['return_pct'] == .0917
    assert by[718]['identity']['experiment'] == 'PRESERVED'
    assert by[1505]['identity']['experiment'] == 'CURRENT'
    assert next(r for r in R.nodes_index(limit=0)['nodes'] if r['node_id'] == 1506)['research_node_num'] is None
    assert db.get_strategy(1506)['research_node_num'] is None, 'drawer/detail must not guess a local alias'


def test_newest_detail_wins_over_old_detail_and_newer_screen(registry):
    db, R = registry
    for stage, value in [('detail', .4), ('screen', .99)]:
        db.x('INSERT INTO backtests(strategy_id,stage,metrics,created_at,dataset_id) VALUES(?,?,?,?,?)',
             (1505, stage, json.dumps({'total_return_pct': value}), 1, 'saved'))
    row = R.nodes_index(search='1505')['nodes'][0]
    assert row['generation_metrics']['return_pct'] == .4
    assert row['generation_record']['stage'] == 'detail'
    from app.research.node_data import node_data_snapshot
    top = node_data_snapshot(db)['tops']['return_pct']
    assert next(t for t in top if t['canonical_node_id'] == 1505)['value'] == .4


@pytest.mark.parametrize('desc,expected', [(False, [1505, 1506, 718]), (True, [718, 1506, 1505])])
def test_numeric_string_sort_full_set_before_paging(registry, desc, expected):
    db, R = registry
    for sid, value in [(1505, '2'), (1506, '10'), (718, '100')]:
        db.x('UPDATE backtests SET metrics=? WHERE strategy_id=?', (json.dumps({'profit_factor': value}), sid))
    ids = []
    for offset in range(4):
        page = R.nodes_index(filter='all', sort_by='generation_pf', sort_desc=desc, limit=1, offset=offset)
        ids.extend(r['node_id'] for r in page['nodes'])
    assert ids == expected
    from app.research.registry import sort_nodes
    rows = [{'node_id': 2, 'n': '2'}, {'node_id': 1, 'n': 2}, {'node_id': 4, 'n': float('nan')}, {'node_id': 3, 'n': ''}]
    assert [r['node_id'] for r in sort_nodes(rows, lambda r:r['n'], descending=desc)] == [1, 2, 3, 4]


def test_desired_stars_idempotent_persistent_and_exact_live_filter(registry):
    db, R = registry
    before = db.q('SELECT id,status,genome,fitness FROM strategies ORDER BY id')
    for _ in range(3):
        assert R.set_shortlist_item(718, {'starred': True})['starred']
    assert db.q('SELECT strategy_id FROM research_shortlist') == [{'strategy_id': 718}]
    first = db.one('SELECT created_at FROM research_shortlist')['created_at']
    R.set_shortlist_item(718, {'starred': True})
    assert db.one('SELECT created_at FROM research_shortlist')['created_at'] == first
    assert [r['node_id'] for r in R.nodes_index(starred_only=True, limit=0)['nodes']] == [718]
    # Independent SQLite connection models refresh/restart, not browser memory.
    with sqlite3.connect(db.path) as con:
        assert con.execute('SELECT strategy_id FROM research_shortlist').fetchall() == [(718,)]
    assert db.q('SELECT id,status,genome,fitness FROM strategies ORDER BY id') == before
    R.set_shortlist_item(718, {'starred': False}); R.set_shortlist_item(718, {'starred': False})
    assert R.nodes_index(starred_only=True)['nodes'] == []
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc: R.set_shortlist_item(99999, {'starred': True})
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc: R.set_shortlist_item(1505, {'starred': 'false'})
    assert exc.value.status_code == 422


def test_cache_invalidation_direct_sql_and_stars_do_not_invalidate(registry):
    db, R = registry
    from app.research.node_data import node_data_snapshot
    a = node_data_snapshot(db)
    R.set_shortlist_item(1505, {'starred': True})
    assert node_data_snapshot(db)['cached'] is True
    assert node_data_snapshot(db)['revision'] == a['revision']
    db.x("UPDATE strategies SET status='FAILED',research_node_num=999 WHERE id=1505")
    b = node_data_snapshot(db)
    assert b['counts']['qualified'] == a['counts']['qualified'] - 1
    assert b['identity']['1505']['research_node_num'] == 999
    assert b['revision'] != a['revision']
    with sqlite3.connect(db.path) as con:
        con.execute("UPDATE backtests SET metrics=? WHERE strategy_id=718", (json.dumps({'total_return_pct': 5}),))
    c = node_data_snapshot(db)
    assert c['tops']['return_pct'][0]['value'] == 5
    assert c['revision'] != b['revision']
    assert R.node_data_get(summary=True)['node_data'].get('identity') is None


def test_bulk_queries_not_proportional_to_population(registry):
    db, R = registry
    def measure():
        calls=[]; db.conn.set_trace_callback(calls.append)
        try: R.nodes_index(filter='all', limit=2)
        finally: db.conn.set_trace_callback(None)
        return len(calls)
    a = measure()
    for sid in range(2000, 2200): _seed(db, sid, status='QUALIFIED')
    b = measure()
    assert b <= a + 2 and b < 60, (a,b)


@pytest.fixture()
def history(monkeypatch):
    import app.data.engine as E
    end = _friday_noon_utc()
    bridge = _WindowBridge(end - 1100 * 86400, end)  # covers calendar 3Y including leap day
    store = _MemStore()
    monkeypatch.setattr(E, 'get_bridge', lambda: bridge)
    monkeypatch.setattr(E.master, 'get_master', lambda *a, **kw: store)
    monkeypatch.setattr(E.DataEngine, '_materialize_dataset_view', lambda *a: 'fixture-dataset')
    return E.DataEngine(), bridge, store, end


@pytest.mark.parametrize('key', ['1D','7D','14D','1M','3M','6M','1Y','2Y','3Y'])
def test_all_nine_periods_persist_real_in_range_counts(history, key):
    from app.deeptest.periods import resolve_period
    engine, bridge, store, end = history
    pr = resolve_period(key, end_ts=end)
    result = engine.sync_master('XAUUSD','M15', force_full=True, request_start=pr['start_ts'], request_end=pr['end_ts'])
    rep = result['fetch']
    assert rep['requested_start'] == pr['start_ts'] and rep['requested_end'] == pr['end_ts']
    assert rep['persisted_in_range'] == rep['persisted_rows'] == store.rows == len(store.seen)
    assert all(pr['start_ts'] <= t <= pr['end_ts'] for t in store.seen)
    assert rep['chunk_results'] and rep['chunks'] == len(rep['chunk_results'])
    assert sum(c['accepted_bars'] for c in rep['chunk_results']) == rep['bars_collected_raw']
    assert all(c['out_of_range_bars'] == 0 for c in rep['chunk_results'])
    if key == '3Y':
        assert store.rows > 70000  # far beyond the reported 2,683-bar regression
        assert rep['persisted_range_first'] <= pr['start_ts'] + 900 * 2
        assert rep['persisted_range_last'] == end
        assert result['sync_status'] == 'COMPLETE'
    assert rep['cache_source'] == 'bridge_range + persisted_master'


def test_empty_prefix_does_not_stop_before_available_later_chunks(history):
    engine, bridge, store, end = history
    bridge.avail_start = end - 20 * 86400
    start = end - 1095 * 86400
    result = engine.sync_master('XAUUSD','M15', force_full=True, request_start=start, request_end=end)
    assert len(bridge.range_calls) >= 7
    assert store.rows > 1000
    assert result['sync_status'] == 'PARTIAL'
    assert len([c for c in result['fetch']['chunk_results'] if not c['accepted_bars']]) >= 5
    assert result['fetch']['uncovered'] and result['partial_reason']


def test_short_cache_fetches_head_tail_and_material_internal_gap(history):
    engine, bridge, store, end = history
    # Short cached subset cannot satisfy 3Y; subsequent internal hole is refetched.
    store.append({'ts': [end - i * 900 for i in range(2683)]})
    start = end - 1095 * 86400
    result = engine.sync_master('XAUUSD','M15', request_start=start, request_end=end)
    assert store.rows > 70000 and result['fetch']['persisted_in_range'] == store.rows
    a,b=end-60*86400,end-50*86400
    store.seen = {t for t in store.seen if not a < t < b}
    store.rows=len(store.seen)
    bridge.range_calls.clear()
    result = engine.sync_master('XAUUSD','M15', request_start=start, request_end=end)
    assert any(x <= a and y >= b for x,y in bridge.range_calls)
    assert result['sync_status'] == 'COMPLETE'
    bridge.range_calls.clear()
    result = engine.sync_master('XAUUSD','M15', request_start=start, request_end=end)
    assert result['fetch']['chunks'] == 0 and not bridge.range_calls
    assert result['fetch']['cache_source'] == 'persisted_master'


def test_unsorted_duplicates_out_of_range_not_trusted(history, monkeypatch):
    engine, bridge, store, end = history
    original=bridge.copy_rates_range
    def hostile(s,t,a,b):
        rows=original(s,t,a,b)
        from test_v7_0_range_fetch import _FakeBar
        return list(reversed(rows)) + rows[:5] + [_FakeBar(a-100000),_FakeBar(b+100000)]
    monkeypatch.setattr(bridge,'copy_rates_range',hostile)
    result=engine.sync_master('XAUUSD','M15',force_full=True,request_start=end-7*86400,request_end=end)
    rep=result['fetch']
    assert all(end-7*86400 <= t <= end for t in store.seen)
    assert rep['chunk_results'][0]['out_of_range_bars'] == 2
    assert rep['persisted_in_range'] == rep['bars_received'] == len(store.seen)


def test_empty_explicit_range_never_uses_recent_fallback(history, monkeypatch):
    engine, bridge, store, end=history
    monkeypatch.setattr(bridge,'copy_rates_range',lambda *a: [])
    monkeypatch.setattr(bridge,'copy_rates',lambda *a: pytest.fail('explicit range silently replaced by recent subset'))
    result=engine.sync_master('XAUUSD','M15',force_full=True,request_start=end-1095*86400,request_end=end)
    assert result['status']=='no_data_from_bridge'
    assert result['fetch']['persisted_in_range']==0
    assert result['fetch']['uncovered'] and result['partial_reason']


def test_three_year_real_parquet_store_reopens_with_unique_dates(history, tmp_path, monkeypatch):
    import app.data.master as M
    import app.data.engine as E
    engine,bridge,_,end=history
    db=Database(str(tmp_path/'history.db'))
    dirs={k:tmp_path/k for k in ('raw','quarantine','metadata')}
    for p in dirs.values(): p.mkdir()
    monkeypatch.setattr(M.P,'symbol_dirs',lambda s: dirs)
    monkeypatch.setattr(M,'get_db',lambda:db)
    monkeypatch.setattr(E,'get_db',lambda:db)
    store=M.MasterStore('XAUUSD','M15',bridge.profile())
    monkeypatch.setattr(E.master,'get_master',lambda *a,**k:store)
    start=end-1095*86400
    result=engine.sync_master('XAUUSD','M15',force_full=True,request_start=start,request_end=end)
    reopened=M.MasterStore('XAUUSD','M15',bridge.profile())
    frame=reopened.read_range(start,end)
    assert len(frame)>70000
    assert frame['ts'].is_unique and frame['ts'].is_monotonic_increasing
    assert frame['ts'].min() >= start and frame['ts'].max() == end
    assert result['fetch']['persisted_in_range']==reopened.rows==len(frame)
    assert len(list(store.raw_dir.glob('*.parquet'))) >= 36
    db.close()


def test_terminal_per_call_cap_2683_is_walked_until_three_year_union(history, monkeypatch):
    engine,bridge,store,end=history
    original=bridge.copy_rates_range
    monkeypatch.setattr(bridge,'copy_rates_range',lambda s,t,a,b: original(s,t,a,b)[-2683:])
    start=end-1095*86400
    result=engine.sync_master('XAUUSD','M15',force_full=True,request_start=start,request_end=end)
    assert result['sync_status']=='COMPLETE'
    assert result['fetch']['persisted_in_range']==store.rows>70000
    assert all(c['accepted_bars']<=2683 for c in result['fetch']['chunk_results'])
    assert len(result['fetch']['chunk_results'])>7
    assert result['fetch']['uncovered']==[]


def test_real_bridge_history_none_error_distinct_from_successful_empty(monkeypatch):
    from types import SimpleNamespace
    import app.mt5.mt5_real as M
    captured=[]
    native=SimpleNamespace(symbol_select=lambda *a:True, last_error=lambda:(-4,'history unavailable'),
        copy_rates_range=lambda symbol,tf,a,b: captured.append((symbol,tf,a,b)))
    monkeypatch.setattr(M,'MT5_PACKAGE_AVAILABLE',True)
    monkeypatch.setattr(M,'mt5',native)
    monkeypatch.setattr(M,'_TF_MAP',{'M15':15})
    bridge=M.MT5RealBridge.__new__(M.MT5RealBridge);bridge._connected=True
    assert bridge.copy_rates_range('XAUUSD','M15',100000,200000)==[]
    assert bridge.last_history_result['status']=='error'
    assert 'history unavailable' in bridge.last_history_result['error']
    assert captured[0][:2]==('XAUUSD',15)
    assert captured[0][2].utcoffset().total_seconds()==0
    native.copy_rates_range=lambda *a:[]
    assert bridge.copy_rates_range('XAUUSD','M15',100000,200000)==[]
    assert bridge.last_history_result['status']=='empty'


def test_different_inflight_goal_not_silently_joined(monkeypatch):
    import app.deeptest.fetch_job as F
    class Thread:
        def __init__(self,**kwargs): pass
        def start(self): pass
    monkeypatch.setattr(F.threading,'Thread',Thread)
    monkeypatch.setattr(F,'_JOBS',{})
    a=F.start_fetch('XAUUSD','M15',{'duration_key':'1D'})
    assert a['ok'] and a['job']['period']['start_ts'] < a['job']['period']['end_ts']
    assert F.start_fetch('XAUUSD','M15',{'duration_key':'1D'})['joined_existing']
    b=F.start_fetch('XAUUSD','M15',{'duration_key':'3Y'})
    assert b['ok'] is False and 'different history range' in b['error']


def test_failed_chunks_record_errors_without_recent_substitution(history,monkeypatch):
    import app.data.engine as E
    engine,bridge,store,end=history
    bridge.fail_all=True
    monkeypatch.setattr(E.time,'sleep',lambda *a:None)
    result=engine.sync_master('XAUUSD','M15',force_full=True,request_start=end-7*86400,request_end=end)
    rep=result['fetch']
    assert result['status']=='no_data_from_bridge' and result['partial_reason']=='bridge error'
    assert rep['errors'] and rep['persisted_in_range']==0
    assert all(c['status']=='error' and c['attempts']==3 and c['error'] for c in rep['chunk_results'])


@pytest.fixture(autouse=True)
def _synthetic_memory_history_is_not_the_operator_broker_baseline(monkeypatch):
    # Range-fetch tests use SIMULATOR bridges and isolated in-memory stores.
    # Do not ask them to mutate the real provisioned IC Markets source. The
    # independent V9.2 tests retain every broker-provenance rejection assertion.
    import app.data.history as H
    monkeypatch.setattr(H,'canonical_profile',lambda *a:None)
