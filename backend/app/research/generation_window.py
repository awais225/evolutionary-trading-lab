"""Optional generation-time, fixed-calendar-day research window.
Separate results table: never becomes the latest qualification backtest, fitness,
eligibility result, or Deep/live metric. Existing Backtester remains unchanged.
"""
import copy,hashlib,json,math,threading,time
from ..jsonutil import jd
_LOCK=threading.RLock();_CACHE={}
DEFAULT={'enabled':True,'days':3,'schema_version':1}

def schema(db):
    db.x('CREATE TABLE IF NOT EXISTS generation_window_results (node_id INTEGER NOT NULL,cache_key TEXT NOT NULL,result TEXT NOT NULL,created_at REAL NOT NULL,PRIMARY KEY(node_id,cache_key))')

def settings(db):
    try:
        saved=json.loads(db.get_meta('generation_window_settings') or '{}')
        if not isinstance(saved,dict):return dict(DEFAULT)
        days=saved.get('days',3)
        if isinstance(days,bool) or not isinstance(days,(int,float)) or not math.isfinite(days) or int(days)!=days or not 1<=days<=3650:return dict(DEFAULT)
        enabled=saved.get('enabled',True)
        if not isinstance(enabled,bool):return dict(DEFAULT)
        return {**DEFAULT,'days':int(days),'enabled':enabled}
    except (ValueError,TypeError):return dict(DEFAULT)

def save_settings(db,payload):
    old=settings(db);v=payload.get('days',old['days'])
    if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or int(v)!=v or not 1<=v<=3650:raise ValueError('generation window days must be an integer 1..3650')
    if 'enabled' in payload and not isinstance(payload['enabled'],bool):raise ValueError('enabled must be boolean')
    out={**old,'days':int(v),'enabled':payload.get('enabled',old['enabled'])};db.set_meta('generation_window_settings',jd(out));return out

def prepare(genome,cfg,now=None):
    if not cfg['enabled']:return None
    load_started = time.perf_counter()
    from ..data.history import canonical_profile
    from ..data.master import get_master
    from ..data.integrity import TF_SECONDS
    from ..data.history_range import coverage
    from ..data.engine import get_data_engine
    symbol,timeframe=genome['symbol'],genome['timeframe'];step=TF_SECONDS[timeframe];end=math.floor((now or time.time())/step)*step;start=end-cfg['days']*86400
    record={'namespace':'generation_window','days':cfg['days'],'requested_start':start,'requested_end':end,'symbol':symbol,'timeframe':timeframe,'status':'INSUFFICIENT_HISTORY','metrics':None}
    if not canonical_profile(symbol,timeframe):return {**record,'reason':'no confirmed persistent baseline for this pair','cache_key':f'no-baseline:{symbol}:{timeframe}:{cfg["days"]}:{end}'}
    store=get_master(symbol,timeframe,canonical_profile(symbol,timeframe));base=f'UTC|{symbol}|{timeframe}|{store.fingerprint}|{cfg["days"]}|{start}|{end}'
    record['cache_key']=hashlib.sha256(base.encode()).hexdigest();record['dataset_fingerprint']=store.fingerprint
    with _LOCK:
        if base in _CACHE:
            cached=copy.deepcopy(_CACHE[base]);cached['view_cache_hit']=True;cached['load_seconds']=time.perf_counter()-load_started
            return cached
        cov=coverage(store,start,end-step,step);frame=store.read_range(start,end)
        record['coverage']=cov;record['available_bars']=len(frame)
        record['actual_start']=float(frame.ts.iloc[0]) if len(frame) else None;record['actual_end']=float(frame.ts.iloc[-1]) if len(frame) else None
        if len(frame)<300 or cov['status']!='COMPLETE':record['reason']='insufficient contiguous requested history or fewer than 300 in-range bars; range not silently shortened'
        else:
            engine=get_data_engine();did=engine._materialize_dataset_view(store);full=engine.get_frame(did)
            import numpy as np
            lo=int(np.searchsorted(full.ts.to_numpy(),start));hi=int(np.searchsorted(full.ts.to_numpy(),end))
            record.update(status='READY',dataset_id=did,window=[lo,hi])
        record.update(load_seconds=time.perf_counter()-load_started,view_cache_hit=False,timezone='UTC',cache_policy='dataset fingerprint/revision + symbol/TF + exact UTC range')
        _CACHE[base]=copy.deepcopy(record)
        while len(_CACHE)>16:_CACHE.pop(next(iter(_CACHE)))
        return record

def run(genome,context):
    if not context:return None
    from ..versions import manifest
    out=copy.deepcopy(context);out['namespace']='generation_window';t=time.perf_counter()
    from ..config import config_digest
    out['config_digest']=config_digest()
    out['strategy_hash']=hashlib.sha256(jd(genome).encode()).hexdigest();out['engine_versions']=manifest()
    out['cache_key']=hashlib.sha256((out['cache_key']+out['strategy_hash']+out['config_digest']+jd(out['engine_versions'])).encode()).hexdigest()
    if out['status']=='READY':
        from ..backtest.engine import BacktestRequest,run_backtest
        res=run_backtest(BacktestRequest(genome=genome,dataset_id=out['dataset_id'],stage='detail',window=tuple(out['window']),seed_salt='generation_window'))
        out.update(status='COMPLETED' if res.ok else 'FAILED',metrics=res.metrics if res.ok else None,reason=res.error)
    out['seconds']=time.perf_counter()-t
    return out

def persist(db,node_id,result):
    if not result:return
    schema(db);db.x('INSERT INTO generation_window_results VALUES (?,?,?,?) ON CONFLICT(node_id,cache_key) DO UPDATE SET result=excluded.result,created_at=excluded.created_at',(node_id,result['cache_key'],jd(result),time.time()))

def recent(db):
    schema(db);return [{'node_id':r['node_id'],**json.loads(r['result'])} for r in db.q('SELECT * FROM generation_window_results ORDER BY created_at DESC LIMIT 20')]
