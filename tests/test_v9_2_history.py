"""Permanent historical baseline: real MasterStore, full CSV frames and restart reads.
All broker objects are fakes; no terminal connection or order is possible.
"""
import json
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

@pytest.fixture
def history_store(tmp_path, monkeypatch):
    from app.data import master as M,history as H
    from app.db.database import Database
    db=Database(str(tmp_path/'metadata.db'))
    monkeypatch.setattr(M,'get_db',lambda:db)
    def dirs(symbol):
        out={k:tmp_path/symbol/k for k in ('raw','quarantine','metadata')}
        for p in out.values():p.mkdir(parents=True,exist_ok=True)
        return out
    monkeypatch.setattr(M.P,'symbol_dirs',dirs)
    monkeypatch.setattr(H,'root',lambda:tmp_path/'HISTORICAL')
    return M,db

def bars(ts):
    return pd.DataFrame({'ts':np.asarray(ts,dtype=float),'open':100.,'high':102.,'low':99.,'close':101.,'tick_volume':5,'spread':2.})

def test_historical_head_internal_backfill_and_restart(history_store):
    M,db=history_store;s=M.MasterStore('XAUUSD','M1',{})
    s.append(bars([1700000120,1700000240]));out=s.append(bars([1700000000,1700000060,1700000120,1700000180,1700000240,1700000300]))
    assert out['accepted']==4
    reopened=M.MasterStore('XAUUSD','M1',{})
    assert reopened.rows==6
    assert reopened.read_range(1700000000,1700000300)['ts'].tolist()==[1700000000+i*60 for i in range(6)]
    assert reopened.append(bars([1700000000,1700000060]))['duplicates_identical']==2

def test_full_validation_not_preview_and_invalid_datetime():
    from app.deeptest.csv_import import validate_frame
    ts=pd.date_range('2026-01-01',periods=20031,freq='min')
    frame=bars(np.arange(20031));frame['timestamp']=ts.astype(str);frame=frame.drop(columns='ts')
    r=validate_frame(frame.to_csv(index=False).encode(),symbol='XAUUSD',timeframe='M1',timezone_offset_hours=0)
    assert r['ok'] and r['rows_valid']==20031
    assert len(r['frame'])==20031
    bad=b'date,time,open,high,low,close\nnotdate,no,100,102,99,101\n2026.01.01,01:00,100,102,99,101\n'
    r=validate_frame(bad,symbol='XAUUSD',timeframe='M1',timezone_offset_hours=0)
    assert r['rows_rejected']==1

def test_us_dst_not_europe_and_yearly_rule():
    from app.data.history import localize_source
    # US summer, but Europe still winter: March16 2026. November2 standard.
    stamps=pd.Series(pd.to_datetime(['2026-03-16 01:00','2026-10-26 01:00','2026-11-02 01:00','2020-10-01 00:00']))
    out=localize_source(stamps,'ICMARKETS_NY_CLOSE')
    assert [x.strftime('%Y-%m-%d %H:%M') for x in out]==['2026-03-15 22:00','2026-10-25 22:00','2026-11-01 23:00','2020-09-30 21:00']

def test_volume_and_explicit_timezone(history_store):
    from app.deeptest.csv_import import validate_frame
    raw=b'<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>\n2026.03.16\t01:00:00\t100\t102\t99\t101\t99\t7\t11\n'
    r=validate_frame(raw,symbol='XAUUSD',timeframe='M1',source_timezone='ICMARKETS_NY_CLOSE')
    assert r['ok'] and not r['needs_timezone_confirmation']
    assert r['frame'].iloc[0]['tick_volume']==99 and r['frame'].iloc[0]['volume']==7
    assert pd.to_datetime(r['frame'].iloc[0]['ts'],unit='s',utc=True).isoformat()=='2026-03-15T22:00:00+00:00'

def test_failure_rolls_back_partition_and_metadata(history_store,monkeypatch):
    M,db=history_store;s=M.MasterStore('XAUUSD','M1',{})
    s.append(bars([1700000000]));old=s.meta_path.read_bytes();real=s._save_meta
    def fail(*args):raise OSError('disk-full injected after partition write')
    monkeypatch.setattr(s,'_save_meta',fail)
    with pytest.raises(OSError):s.append(bars([1700000060]))
    reopened=M.MasterStore('XAUUSD','M1',{})
    assert reopened.rows==1 and len(reopened.read_range())==1 and reopened.meta_path.read_bytes()==old

def test_mt5_correction_is_explicit_not_csv_silent(history_store):
    M,db=history_store;s=M.MasterStore('XAUUSD','M1',{})
    s.append(bars([1700000000]));new=bars([1700000000]);new['close']=100.5
    csv=s.append(new,{'source_type':'CSV_IMPORT'});assert csv['conflicts']==1 and s.read_range().iloc[0]['close']==101.
    mt5=s.append(new,{'source_type':'MT5','allow_corrections':True});assert mt5['updated']==1 and s.read_range().iloc[0]['close']==100.5
    assert M.MasterStore('XAUUSD','M1',{}).rows==1

def test_canonical_routing_independent_of_active_bridge(history_store):
    M,db=history_store
    from app.data import history as H
    H.root().mkdir();(H.root()/'manifest.json').write_text(json.dumps({'symbol':'XAUUSD','timeframes':['M15']}))
    assert M.get_master('XAUUSD','M15',{'source':'SIMULATOR'}).profile==H.PROFILE
    assert M.get_master('EURUSD','M15',{'source':'SIMULATOR'}).profile=={'source':'SIMULATOR'}


def test_latest_fetch_above_terminal_recent_limit_chunked_and_restarted(history_store,monkeypatch):
    M,db=history_store
    from app.data import history as H,history_latest as L
    from app.data.engine import DataEngine
    import app.data.engine as E
    H.root().mkdir();(H.root()/'manifest.json').write_text(json.dumps({'symbol':'XAUUSD','timeframes':['M1','M5']}))
    s=M.get_master('XAUUSD','M1',H.PROFILE);start=1700000000.;s.append(bars([start]));other=M.get_master('XAUUSD','M5',H.PROFILE);other.append(bars([start]));fp=other.fingerprint
    calls=[]
    class Fake:
        def copy_rates_range(self,sym,tf,lo,hi):
            calls.append((lo,hi));ts=np.arange(start,start+6001*60,60);return bars(ts[(ts>=lo)&(ts<=hi)]).to_dict('records')
    monkeypatch.setattr(E,'get_data_engine',lambda:DataEngine())
    out=L._sync_pair(Fake(),'M1',start+6001*60)
    assert out['after']==6001 and out['added']==6000 and len(calls)==2
    assert M.get_master('XAUUSD','M1',H.PROFILE).rows==6001
    assert M.get_master('XAUUSD','M5',H.PROFILE).fingerprint==fp
    out=L._sync_pair(Fake(),'M1',start+6001*60)
    assert out['added']==0 and out['after']==6001 and out['identical']==3


def test_signed_offsets_both_directions_and_nonfinite():
    from app.live_testing.offsets import apply_offsets
    from types import SimpleNamespace
    specs=SimpleNamespace(pip_size=.1,tick_size=.1,digits=1)
    for side,sl,tp in [('BUY',90.,110.),('SELL',110.,90.)]:
        sign=1 if side=='BUY' else -1
        r=apply_offsets(side=side,entry=100,sl=sl,tp=tp,sl_offset_pips=2,tp_offset_pips=3,sl_offset_sign='+ve',tp_offset_sign='-ve',specs=specs)
        assert r['ok'] and r['sl']==pytest.approx(sl-sign*.2) and r['tp']==pytest.approx(tp-sign*.3)
        r=apply_offsets(side=side,entry=100,sl=sl,tp=tp,sl_offset_pips=2,tp_offset_pips=3,sl_offset_sign='-ve',tp_offset_sign='+ve',specs=specs)
        assert r['ok'] and r['sl']==pytest.approx(sl+sign*.2) and r['tp']==pytest.approx(tp+sign*.3)
    assert not apply_offsets(side='BUY',entry=100,sl=90,tp=110,tp_offset_pips=float('nan'),specs=specs)['ok']
    assert apply_offsets(side='BUY',entry=100,sl=90,tp=110,sl_offset_pips=0,tp_offset_pips=0,specs=specs)['sl']==90


def test_offset_storage_legacy_mapping_and_sign_isolation(tmp_path):
    from app.db.database import Database
    db=Database(str(tmp_path/'signs.db'))
    db.set_live_test_config(1,{'sl_offset_pips':-2,'tp_offset_pips':3,'is_active':False})
    c=db.get_live_test_config(1)
    assert c['sl_offset_sign']=='-ve' and c['tp_offset_sign']=='-ve' and c['offset_schema_version']==1
    db.set_live_test_config(2,{'sl_offset_pips':2,'tp_offset_pips':3,'sl_offset_sign':'+ve','tp_offset_sign':'+ve','offset_schema_version':2,'is_active':False})
    c=db.get_live_test_config(2)
    assert c['tp_offset_sign']=='+ve' and c['offset_schema_version']==2 and not c['is_active']
    assert db.get_live_test_config(1)['tp_offset_sign']=='-ve'


def test_full_commit_against_recent_cache_then_deep_store_reads(history_store,monkeypatch):
    M,db=history_store
    from app.data import history as H
    from app.deeptest.csv_import import validate_frame,commit_import
    from app.deeptest.coverage import _mstore,pair_coverage
    import app.db.database as D
    monkeypatch.setattr(D,'get_db',lambda:db)
    H.root().mkdir();(H.root()/'manifest.json').write_text(json.dumps({'symbol':'XAUUSD','timeframes':['M1']}))
    n=25031;ts=1700000000+np.arange(n)*60
    s=M.get_master('XAUUSD','M1',H.PROFILE);s.append(bars(ts[-300:]))
    raw=bars(ts).to_csv(index=False).encode()
    r=validate_frame(raw,symbol='XAUUSD',timeframe='M1',timezone_offset_hours=0,filename='regression.csv')
    result=commit_import(r,symbol='XAUUSD',timeframe='M1',timezone_confirmed='UTC confirmed')
    assert result['ok'] and result['merge']['accepted']==n-300 and result['merge']['duplicates_identical']==300
    assert result['available_unique_bars']==n
    # Exactly the same resolver used by Deep's coverage and scenario execution.
    assert _mstore('XAUUSD','M1').rows==n
    assert len(_mstore('XAUUSD','M1').read_range(ts[0],ts[-1]))==n


def test_concurrent_real_store_instances_do_not_lose_backfill(history_store):
    from concurrent.futures import ThreadPoolExecutor
    M,db=history_store;M.MasterStore('XAUUSD','M1',{}).append(bars([1700000120]))
    a=M.MasterStore('XAUUSD','M1',{});b=M.MasterStore('XAUUSD','M1',{})
    with ThreadPoolExecutor(2) as ex:
        futures=[ex.submit(a.append,bars([1700000000,1700000060])),ex.submit(b.append,bars([1700000180,1700000240]))]
        for f in futures:assert f.result()['accepted']==2
    assert M.MasterStore('XAUUSD','M1',{}).rows==5


def test_fixed_offsets_nonfinite_ohlc_and_unknown_zone_are_refused():
    from app.deeptest.csv_import import validate_frame
    raw=b'ts,open,high,low,close\n1700000000,100,102,99,101\n1700000060,inf,inf,99,101\n'
    r=validate_frame(raw,symbol='XAUUSD',timeframe='M1',timezone_offset_hours=0)
    assert r['rows_rejected']==1 and len(r['frame'])==1
    assert not validate_frame(raw,symbol='XAUUSD',timeframe='M1',timezone_offset_hours=float('nan'))['ok']
    raw=b'date,time,open,high,low,close\n2026.01.01,01:00,100,102,99,101\n'
    assert not validate_frame(raw,symbol='XAUUSD',timeframe='M1',source_timezone='Not/A_Zone')['ok']


def test_canonical_snapshot_cannot_overwrite_or_be_hijacked_by_recent_mt5_cache(history_store,monkeypatch,tmp_path):
    M,db=history_store
    from app.data import history as H,engine as E
    from types import SimpleNamespace
    H.root().mkdir();(H.root()/'manifest.json').write_text(json.dumps({'symbol':'XAUUSD','timeframes':['M1']}))
    e=E.DataEngine();s=M.get_master('XAUUSD','M1',H.PROFILE);s.append(e._normalize(bars([1700000000,1700000060,1700000120])))
    cache=tmp_path/'cache';cache.mkdir();mt5=tmp_path/'MT5/XAUUSD';mt5.mkdir(parents=True)
    recent=mt5/'M1.parquet';bars([1700000120]).to_parquet(recent);before=recent.read_bytes()
    monkeypatch.setattr(E.P,'MT5_XAUUSD_DIR',mt5);monkeypatch.setattr(E,'get_db',lambda:db)
    monkeypatch.setattr(E,'get_config',lambda:SimpleNamespace(data=SimpleNamespace(cache_dir=str(cache))))
    did=e._materialize_dataset_view(s)
    assert recent.read_bytes()==before
    e._frames.clear();assert len(e.get_frame(did))==3
    assert recent.read_bytes()==before


def test_broker_isolation_guard():
    from app.data.history import accepts_bridge
    from types import SimpleNamespace
    assert accepts_bridge(SimpleNamespace(source='MT5',is_simulated=False,profile=lambda:{'server':'ICMarketsSC-Demo','broker':'IC Markets'}))
    assert not accepts_bridge(SimpleNamespace(source='MT5',is_simulated=False,profile=lambda:{'server':'OtherBroker-Live'}))
    assert not accepts_bridge(SimpleNamespace(source='SIMULATOR',is_simulated=True))


def test_restart_recovers_interrupted_partition_commit(history_store):
    import base64,shutil
    M,db=history_store;s=M.MasterStore('XAUUSD','M1',{});s.append(bars([1700000000]));meta=s.meta_path.read_bytes()
    month=next(iter(s._meta['partitions']));backup=s.raw_dir/'history-txn-interrupted';backup.mkdir()
    p=s.raw_dir/(month+'.parquet');shutil.copy2(p,backup/p.name)
    bars([1700000000,1700000060]).to_parquet(p,index=False)
    (s.raw_dir/'.history-transaction.json').write_text(json.dumps({'backup':str(backup),'months':[month],'metadata':base64.b64encode(meta).decode()}))
    reopened=M.MasterStore('XAUUSD','M1',{})
    assert reopened.rows==1 and len(reopened.read_range())==1 and not backup.exists()

@pytest.mark.parametrize('which',['first','last'])
def test_latest_fetch_adapts_to_2683_successful_native_cap(history_store,monkeypatch,which):
    M,db=history_store
    from app.data import history as H,history_latest as L,engine as E
    H.root().mkdir();(H.root()/'manifest.json').write_text(json.dumps({'symbol':'XAUUSD','timeframes':['M1']}))
    start=1700000000.;M.get_master('XAUUSD','M1',H.PROFILE).append(bars([start]))
    class Capped:
        def copy_rates_range(self,sym,tf,lo,hi):
            stamps=np.arange(start,start+6001*60,60);rows=bars(stamps[(stamps>=lo)&(stamps<=hi)]).to_dict('records')
            return rows[:2683] if which=='first' else rows[-2683:]
    out=L._sync_pair(Capped(),'M1',start+6001*60)
    assert out['after']==6001 and out['added']==6000 and len(out['chunks'])>=3 and out['status']=='COMPLETE'


def test_cancel_and_failed_latest_ranges_preserve_baseline(history_store):
    import threading
    M,db=history_store
    from app.data import history as H,history_latest as L
    H.root().mkdir();(H.root()/'manifest.json').write_text(json.dumps({'symbol':'XAUUSD','timeframes':['M1']}))
    start=1700000000.;store=M.get_master('XAUUSD','M1',H.PROFILE);store.append(bars([start]));fp=store.fingerprint
    class Unavailable:
        def copy_rates_range(self,*args):raise OSError('unavailable fake terminal')
    event=threading.Event();event.set();out=L._sync_pair(Unavailable(),'M1',start+600*60,cancel_event=event)
    assert out['status']=='CANCELLED' and out['after']==1 and out['fingerprint']==fp
    out=L._sync_pair(Unavailable(),'M1',start+600*60,cancel_event=threading.Event())
    assert out['status']=='PARTIAL' and out['after']==1 and out['fingerprint']==fp and out['errors']
