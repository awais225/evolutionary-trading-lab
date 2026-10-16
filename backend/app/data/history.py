"""V9.2 permanent baseline history. Original CSVs are immutable source artifacts;
monthly MasterStore partitions are the authoritative operational layer.
Only explicitly configured pairs are routed here, never simulator history.
"""
import json, time, threading, hashlib
from pathlib import Path
import pandas as pd
from .. import paths as P

TIMEFRAMES = ('M1','M5','M15','M30','H1','H4','D1')
PROFILE = {'source':'USER_HISTORY','broker':'ICMARKETS','server':'CANONICAL'}
_MANIFEST_CACHE = (None, None)
_GUARD = threading.RLock()


def root(): return P.DATA_ROOT / 'HISTORICAL'
def manifest():
    global _MANIFEST_CACHE
    p=root()/'manifest.json'
    if not p.exists(): return {}
    key=(str(p.resolve()),p.stat().st_mtime_ns)
    if _MANIFEST_CACHE[0] != key:
        _MANIFEST_CACHE=(key,json.loads(p.read_text()))
    return _MANIFEST_CACHE[1]


def canonical_profile(symbol,timeframe):
    m=manifest()
    if str(symbol).upper()==m.get('symbol') and str(timeframe).upper() in m.get('timeframes',[]): return PROFILE
    return None


def localize_source(parsed, name):
    """Explicit rule, not inferred from gaps. IC server wall clock = NY+7h.
    Ambiguous/nonexistent source times are rejected instead of guessed.
    """
    if name == 'ICMARKETS_NY_CLOSE':
        return (parsed - pd.Timedelta(hours=7)).dt.tz_localize('America/New_York',ambiguous='NaT',nonexistent='NaT').dt.tz_convert('UTC')
    return parsed.dt.tz_localize(name,ambiguous='NaT',nonexistent='NaT').dt.tz_convert('UTC')


def inventory():
    from .master import get_master
    records=[]
    for tf in manifest().get('timeframes',[]):
        store=get_master(manifest()['symbol'],tf,PROFILE)
        rec=next((r for r in manifest().get('imports',[]) if r['timeframe']==tf),{})
        records.append({'symbol':store.symbol,'timeframe':tf,'unique_bars':store.rows,'first_ts':store.first_ts,'last_ts':store.last_ts,
            'fingerprint':store.fingerprint,'version':store.version,'timezone':rec.get('source_timezone'),
            'source':'CSV baseline + trusted MT5 closed-bar corrections','ready':store.rows>0,
            'last_successful_update':store._meta.get('updated_at',rec.get('imported_at')),
            'gap_status':'see per-file gap diagnostics; missing bars are not fabricated'})
    return records


def import_baselines(directory, source_timezone, confirmed=False):
    if not confirmed: raise ValueError('explicit source timezone confirmation required')
    from ..deeptest.csv_import import validate_frame,commit_import
    from .master import get_master
    root().mkdir(parents=True,exist_ok=True)
    names={tf:f'XAUUSD_{"Daily" if tf=="D1" else tf}.csv' for tf in TIMEFRAMES}
    # Explicit configuration routes all consumers to the same store, independent of bridge.
    m={'schema_version':1,'symbol':'XAUUSD','timeframes':list(TIMEFRAMES),'source_timezone':source_timezone,
       'timezone_confirmed':True,'confirmation':'user selected IC Markets server-time convention in V9.2 task',
       'conflict_policy':'CSV preserves incumbent+records conflicts; trusted MT5 updates corrected closed bars with audit records',
       'imports':[]}
    staged=[]
    for tf,name in names.items():
        t=time.perf_counter();raw=(Path(directory)/name).read_bytes()
        report=validate_frame(raw,symbol='XAUUSD',timeframe=tf,filename=name,source_timezone=source_timezone)
        if not report.get('ok'):raise ValueError(f'{name}: {report.get("error") or report.get("warnings")}')
        staged.append((tf,name,raw,report,t))
    prior=manifest()
    if prior and prior.get('source_timezone')!=source_timezone:
        raise ValueError('existing baseline timezone differs; an audited explicit migration is required, not reinterpretation')
    m['status']='IMPORTING'
    stage=root()/'manifest.tmp';stage.write_text(json.dumps(m,indent=2));stage.replace(root()/'manifest.json')
    for tf,name,raw,report,t in staged:
        target=root()/'baseline'/name;target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists() and target.read_bytes()!=raw:raise ValueError('immutable baseline already exists with different bytes: '+name)
        if not target.exists():target.write_bytes(raw)
        result=commit_import(report,symbol='XAUUSD',timeframe=tf,timezone_confirmed=source_timezone+' (explicit user confirmation)')
        if not result.get('ok'):raise ValueError(str(result))
        store=get_master('XAUUSD',tf,PROFILE)
        # Read the operational data back, not the preview. Reopen after import for evidence.
        frame=store.read_range(report['earliest_ts'],report['latest_ts'])
        if len(frame)<report['rows_valid']:raise RuntimeError('persisted readback does not contain full validated range')
        rec={k:v for k,v in report.items() if k!='frame'}
        rec.update({'imported_at':time.time(),'duration_seconds':time.perf_counter()-t,'result':result,
                    'persistent_readback_bars':len(frame),'original_sha256':hashlib.sha256(raw).hexdigest()})
        m['imports'].append(rec)
        tmp=root()/'manifest.tmp';tmp.write_text(json.dumps(m,indent=2,default=str));tmp.replace(root()/'manifest.json')
    m["status"]="READY"
    tmp=root()/"manifest.tmp";tmp.write_text(json.dumps(m,indent=2,default=str));tmp.replace(root()/"manifest.json")
    return m


def archive_source(raw,filename,report,result):
    """Archive exact bytes BEFORE merging; atomic, separately truthful receipt."""
    from .master import _store_lock
    digest=hashlib.sha256(raw).hexdigest();name=Path(filename or 'uploaded.csv').name
    directory=root()/'imports'/digest;directory.mkdir(parents=True,exist_ok=True)
    with _store_lock(directory):
        p=directory/name;receipt=directory/'provenance.json'
        if p.exists() and p.read_bytes()!=raw:raise RuntimeError('immutable original collision')
        if not p.exists():
            tmp=p.with_suffix(p.suffix+'.tmp');tmp.write_bytes(raw);tmp.replace(p)
        artifact=str(p.relative_to(P.DATA_ROOT))
        if result.get('status')=='PREPARED' and receipt.exists():return artifact
        meta={k:v for k,v in report.items() if k!='frame'}
        meta['commit_result']=result;meta['original_sha256']=digest
        tmp=receipt.with_suffix('.tmp');tmp.write_text(json.dumps(meta,indent=2,default=str));tmp.replace(receipt)
        return artifact


def accepts_bridge(bridge):
    """An IC baseline cannot silently ingest an unrelated broker's feed."""
    if bridge.source!='MT5' or bridge.is_simulated:return False
    profile=bridge.profile()
    text=''.join(c for c in (str(profile.get('broker',''))+' '+str(profile.get('server',''))).lower() if c.isalnum())
    return 'icmarkets' in text or 'internationalcapitalmarkets' in text
