"""One shared, explicit, read-only MT5 history job for Overview and Deep.
No engine/live worker is started and no order interface is called here.
"""
import copy, json, math, threading, time
from collections import deque
from . import history as H
from .master import get_master
from .integrity import TF_SECONDS, validate_batch
_LOCK=threading.RLock()
_JOB=None
_CANCEL=threading.Event()
TERMINAL={'COMPLETE','PARTIAL','FAILED','CANCELLED'}
CHUNK_BARS=5000


def status():
    with _LOCK:
        job=copy.deepcopy(_JOB)
    if job is None:
        p=H.root()/'latest_fetch.json'
        if p.exists():
            job=json.loads(p.read_text())
            if job.get('status') not in TERMINAL:
                job.update(status='FAILED',error='process restarted before completion; persisted chunks remain available')
    return {'ok':True,'job':job,'inventory':H.inventory()}


def _set(**kw):
    with _LOCK:
        _JOB.update(kw)
        H.root().mkdir(parents=True,exist_ok=True)
        p=H.root()/'latest_fetch.json';tmp=p.with_suffix('.tmp')
        tmp.write_text(json.dumps(_JOB,indent=2,default=str));tmp.replace(p)


def start(timeframes=None):
    global _JOB
    selected=list(dict.fromkeys(timeframes or H.TIMEFRAMES))
    if not selected or any(tf not in H.TIMEFRAMES for tf in selected):return {'ok':False,'error':'select supported timeframes'}
    if any(not H.canonical_profile('XAUUSD',tf) for tf in selected):return {'ok':False,'error':'confirmed baseline is not configured for every selected timeframe'}
    with _LOCK:
        if _JOB and _JOB['status'] not in TERMINAL:
            return {'ok':set(selected)==set(_JOB['timeframes']),'joined_existing':True,'job':copy.deepcopy(_JOB),'error':None if set(selected)==set(_JOB['timeframes']) else 'another shared history scope is running; wait or cancel'}
        _CANCEL.clear()
        _JOB={'status':'QUEUED','started_at':time.time(),'finished_at':None,'timeframes':selected,'results':[], 'progress_pct':0,'operation':'checking real MT5 availability','source':'MT5 closed bars; native UTC epochs','error':None}
        _set(status='QUEUED')
        threading.Thread(target=_run,args=(selected,),daemon=True,name='shared-mt5-history').start()
        return {'ok':True,'job':copy.deepcopy(_JOB)}


def cancel():
    _CANCEL.set()
    return {'ok':True,'note':'cancels between bounded native calls; a native MT5 call cannot be forcibly interrupted'}


def _sync_pair(bridge,tf,now,progress=lambda **kw:None,cancel_event=None):
    from .engine import get_data_engine
    from .history_range import coverage
    import pandas as pd
    event=cancel_event or _CANCEL
    store=get_master('XAUUSD',tf,H.PROFILE)
    if not store.rows:raise ValueError('no persisted baseline; validate/import first instead of downloading whole history')
    step=TF_SECONDS[tf];start=max(store.first_ts,store.last_ts-2*step);end=now-step
    result={'symbol':'XAUUSD','timeframe':tf,'requested_start':start,'requested_end':end,'before':store.rows,
            'added':0,'updated':0,'identical':0,'conflicts':0,'rejected':0,'chunks':[],'errors':[]}
    pending=deque([(start,end)]) if start<=end else deque();scheduled=set();deadline=time.monotonic()+900
    while pending:
        cursor,goal=pending.popleft()
        hi=min(goal,cursor+CHUNK_BARS*step)
        if (cursor,hi) in scheduled:continue
        scheduled.add((cursor,hi))
        if hi<goal:pending.append((hi+step,goal))
        if event.is_set():break
        if time.monotonic()>deadline or len(result['chunks'])>=512:
            result['errors'].append('bounded fetch deadline/chunk limit reached');break
        bars=None
        for attempt in range(3):
            if event.is_set():break
            try:
                bars=list(bridge.copy_rates_range('XAUUSD',tf,cursor,hi) or [])
                if bars:break
            except Exception as exc:
                if attempt==2:result['errors'].append('MT5 range request failed: '+type(exc).__name__)
            if attempt<2:event.wait(.15*(attempt+1))
        chunk={'start':cursor,'end':hi,'received':len(bars or []),'accepted':0}
        if bars:
            df=pd.DataFrame([b.to_dict() if hasattr(b,'to_dict') else dict(b) for b in bars])
            if 'ts' not in df:raise ValueError('MT5 returned no UTC timestamp field')
            df=df[(df.ts>=cursor)&(df.ts<=hi)&(df.ts+step<=now)].sort_values('ts').drop_duplicates('ts',keep='last')
            if len(df):
                first,last=float(df.ts.min()),float(df.ts.max())
                # A successful native call may be capped (e.g.2683 bars).
                # Walk its genuinely unreturned head/tail too; never jump past it.
                if first>cursor+2*step:pending.append((cursor,first-step))
                if last<hi-2*step:pending.append((last+step,hi))
                clean,check=validate_batch(df,tf,quarantine_dir=store.quarantine_dir,tag="latest_"+tf)
                result['rejected']+=check.rows_quarantined
                if len(clean):
                    clean=get_data_engine()._normalize(clean)
                    out=store.append(clean,{**({k:bridge.profile().get(k) for k in ('source','broker','server')} if hasattr(bridge,'profile') else {}),'source_type':'MT5','source':'MT5','allow_corrections':True,'range':[cursor,hi],'timestamp_convention':'native MT5 UTC epoch'})
                    result['added']+=out.get('accepted',0)
                    result['updated']+=out.get('updated',0)
                    result['identical']+=out.get('duplicates_identical',0)
                    result['conflicts']+=out.get('conflicts',0)
                    chunk['accepted']=out.get('accepted',0)
        else:result['errors'].append('no bars returned for requested chunk; closure vs unavailable history unresolved')
        result['chunks'].append(chunk)
        progress(cursor=hi,start=start,end=end,timeframe=tf)
    # Reopen: response is backed by stored bars and persisted fingerprint.
    store=get_master('XAUUSD',tf,H.PROFILE)
    result.update(after=store.rows,first_ts=store.first_ts,last_ts=store.last_ts,fingerprint=store.fingerprint)
    result['coverage']=coverage(store,start,end,step) if start<=end else {'status':'COMPLETE','note':'no newly closed bar yet'}
    result['status']='CANCELLED' if event.is_set() else 'PARTIAL' if result['errors'] or result['rejected'] or result['coverage']['status']=='PARTIAL' else 'COMPLETE'
    return result


def _run(selected):
    try:
        from ..mt5 import get_bridge
        bridge=get_bridge()
        if not H.accepts_bridge(bridge):raise RuntimeError('confirmed IC Markets MT5 bridge unavailable or broker identity unknown; simulator/other broker cannot update this baseline')
        _set(bridge_profile={k:bridge.profile().get(k) for k in ('source','broker','server')},status='FETCHING',operation='incremental closed-bar history with two-bar correction overlap')
        results=[];now=time.time()
        for i,tf in enumerate(selected):
            if _CANCEL.is_set():break
            def prog(**p):
                part=(p['cursor']-p['start'])/max(p['end']-p['start'],1)
                _set(progress_pct=round(100*i/len(selected),1),operation=tf+': persisted chunk through '+str(p['cursor'])+'; timeframe '+str(i+1)+'/'+str(len(selected)))
            try:result=_sync_pair(bridge,tf,now,prog)
            except Exception as exc:result={'timeframe':tf,'status':'FAILED','error':str(exc)[:200]}
            results.append(result);_set(results=list(results))
        outcome='CANCELLED' if _CANCEL.is_set() else 'COMPLETE' if results and all(r['status']=='COMPLETE' for r in results) else 'FAILED' if results and all(r['status']=='FAILED' for r in results) else 'PARTIAL'
        _set(status=outcome,finished_at=time.time(),progress_pct=100 if outcome=='COMPLETE' else _JOB['progress_pct'],operation='shared history job finished; inspect actual coverage and errors')
    except Exception as exc:_set(status='FAILED',finished_at=time.time(),error=str(exc)[:200])
    finally:
        H.root().mkdir(parents=True,exist_ok=True)
        p=H.root()/'latest_fetch.json';tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(_JOB,indent=2,default=str));tmp.replace(p)
