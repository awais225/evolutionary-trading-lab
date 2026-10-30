"""V10.5 read-only authority for persisted DeepTest trades. Never rewrites history.
Cache keys include SQLite external data_version, local total_changes, connection
identity, run and method version. Snapshot reads cover headers AND all trade rows.
"""
import copy
import hashlib
import json
import threading
import time
import weakref
from collections import OrderedDict
from . import analytics as A

VERSION='V10.5-realised-initial-v1'
_CACHE=weakref.WeakKeyDictionary()
_LOCK=threading.RLock()
MAX_BYTES=8*1024*1024

def _revision(db):return (id(db.conn),db.one('PRAGMA data_version')['data_version'],db.conn.total_changes,VERSION)

def _cached(db,key,make):
    with _LOCK:
        cache=_CACHE.setdefault(db,OrderedDict())
        if key in cache:cache.move_to_end(key);return copy.deepcopy(cache[key][0])
    value=make();size=len(json.dumps(value,default=str))
    with _LOCK:
        cache=_CACHE.setdefault(db,OrderedDict())
        if size<=MAX_BYTES:
            cache[key]=(copy.deepcopy(value),size)
            while len(cache)>512 or sum(v[1] for v in cache.values())>MAX_BYTES:cache.popitem(last=False)
    return value

def capital(sc):
    # Actual run config wins; ZERO is evidence, not permission for a 10,000 fallback.
    for source,block in [('settings',sc.get('settings')),('provenance',sc.get('provenance')),('metrics',sc.get('metrics')),('analytics', (sc.get('analytics') or {}).get('summary'))]:
        for field in ('total_capital','initial_capital','initial_balance'):
            if field in (block or {}) and block[field] is not None:return A._f(block[field]),source+'.'+field
    return None,'unavailable: no stored run capital'

def currency(sc):
    for block in (sc.get('settings'),sc.get('provenance'),sc.get('metrics')):
        for field in ('account_currency','currency'):
            v=(block or {}).get(field)
            if isinstance(v,str) and len(v)==3 and v.isascii() and v.isalpha():return v.upper()
    return None

def calculate(sc,trades,selection=False):
    cap,source=capital(sc);out=A.summarize(trades,initial_capital=cap)
    old=(sc.get('analytics') or {}).get('summary') or sc.get('metrics') or {}
    available=len(trades);expected=old.get('trades')
    snapshot=hashlib.sha256(json.dumps(trades,sort_keys=True,default=str,separators=(',',':')).encode()).hexdigest()
    out['summary']['account_currency']=currency(sc)
    out['provenance']={'run_id':sc['run_id'],'node_id':sc['node_id'],'trade_snapshot':snapshot,'metric_version':VERSION,'calculated_at':time.time(),'initial_capital':cap,'capital_source':source,'account_currency':currency(sc),'execution_status':sc['status'],'coverage_status':sc.get('coverage_status'),'available_trade_rows':available,'eligible_closed_trades':out['summary']['trades'],'stored_expected_trade_count':expected,'trade_count_matches_stored':expected is None or expected==available,'bars_processed':(sc.get('metrics') or {}).get('bars_processed',sc.get('bars_used')),'expected_bars':(sc.get('provenance') or {}).get('expected_bars'),'requested_start':sc.get('requested_start'),'requested_end':sc.get('requested_end'),'equity_method':out['summary']['equity_method'],'drawdown_denominator':'initial_capital','scope':'available persisted closed trades; PARTIAL is not complete coverage'}
    if not selection and not trades and expected is not None and expected>0:
        out['summary']={k:None for k in out['summary']}
        out['summary'].update(initial_capital=cap,account_currency=currency(sc),drawdown_denominator='initial_capital',equity_method='unavailable: saved snapshot expects trades but closed-trade rows are missing')
        out['unavailable'].append({'field':'trade_metrics','reason':'Stored snapshot expects closed trades; none are available. No financial metrics fabricated from an empty population'})
        out['provenance']['integrity']='MISSING_TRADE_RECORDS'
    out['legacy_snapshot_preserved']=True
    return out

def enrich_many(db,scenarios):
    if not scenarios:return []
    from . import store as S
    result=[];supplied={row['run_id']:row for row in scenarios}
    # Bound simultaneously loaded runs; no repeated all-history table scan.
    for offset in range(0,len(scenarios),8):
        ids=[row['run_id'] for row in scenarios[offset:offset+8]]
        with db.read_snapshot():
            rev=_revision(db)
            headers={row['run_id']:S.hydrate(dict(row)) for row in db.q('SELECT * FROM deeptest_scenarios WHERE run_id IN ('+','.join('?' for _ in ids)+')',tuple(ids))}
            with _LOCK:
                cache=_CACHE.setdefault(db,OrderedDict())
                existing={ident:copy.deepcopy(cache[(rev,ident)][0]) for ident in ids if (rev,ident) in cache}
            missing=[ident for ident in ids if ident not in existing]
            by={ident:[] for ident in missing}
            if missing:
                for row in db.q('SELECT * FROM deeptest_trades WHERE run_id IN ('+','.join('?' for _ in missing)+') ORDER BY run_id,trade_no,id',tuple(missing)):by[row['run_id']].append(dict(row))
        # CPU work is OUTSIDE the database lock/read transaction. Headers and
        # rows above are one captured snapshot; subsequent writes change the key.
        for ident in ids:
            if ident not in headers:continue
            sc=headers[ident]
            computed=existing[ident] if ident in existing else _cached(db,(rev,ident),lambda:calculate(sc,by[ident]))
            sc['analytics']=computed
            sc['metrics']={**(sc.get('metrics') or {}),**computed['summary']}
            sc['metric_provenance']=computed['provenance']
            if supplied[ident].get('preference_source'):sc['preference_source']=supplied[ident]['preference_source']
            result.append(sc)
    return result

def enrich(db,sc):return enrich_many(db,[sc])[0] if sc else None

def filtered(db,run_id,payload):
    from . import store as S
    from .filter_rules import normalize,reason,context,DAYS
    from .inspect_calendar import aggregate
    rules=normalize(payload)
    with db.read_snapshot():
        sc=S.get_scenario(db,run_id)
        if sc is None:raise KeyError('Scenario not found')
        key=(_revision(db),run_id,'filter',json.dumps({'rules':rules,'legacy':payload.get('schema_version') is None},sort_keys=True))
        with _LOCK:
            cache=_CACHE.setdefault(db,OrderedDict())
            if key in cache:return copy.deepcopy(cache[key][0])
        raw=[dict(r) for r in db.q('SELECT * FROM deeptest_trades WHERE run_id=? ORDER BY trade_no,id',(run_id,))]
    # Financial aggregation/calendar construction must not stall result writers.
    def make():
        trades=A.eligible_trades(raw);kept=[];excluded=[];dropped=[]
        for t in trades:
            if payload.get('schema_version') is None:
                day=t.get('dow');d=DAYS[int(day)] if day is not None and str(day).isdigit() and 0<=int(day)<7 else None
                session=str(t.get('session') or '').lower()
                why='day:'+d if d in rules['exclude_days'] else 'session:'+session if session in rules['exclude_sessions'] else None
            else:why=reason(t,rules)
            if why:excluded.append(t);dropped.append({'trade_no':t.get('trade_no'),'reason':why})
            else:kept.append(t)
        original=calculate(sc,raw)
        if original['provenance'].get('integrity')=='MISSING_TRADE_RECORDS':raise ValueError('Closed trades are missing; saved summary alone cannot support filtered recalculation')
        inc=calculate(sc,kept,selection=True);exc=calculate(sc,excluded,selection=True)
        for block,rows in [(original,trades),(inc,kept),(exc,excluded)]:block.update(_legacy_groups(rows))
        for block,label in [(original,'original'),(inc,'included'),(exc,'excluded')]:
            block['provenance'].update(selection=label,source_available_trade_rows=len(raw),trade_count_matches_stored=original['provenance']['trade_count_matches_stored'])
        calendar=aggregate(kept,capital(sc)[0],None,sc.get('requested_start'),sc.get('requested_end'))
        calendar.update(original_trades=len(trades),kept_trades=len(kept),excluded_trades=len(excluded),rules=rules)
        return {'ok':True,'run_id':run_id,'original':original,'filtered':inc,'excluded':exc,'trade_counts':{'original':len(trades),'kept':len(kept),'excluded':len(excluded)},'kept_trade_numbers':[t.get('trade_no') for t in kept],'excluded_trades':dropped,'entry_rules':rules,'active':bool(excluded or rules.get('daily_schedule')),'calendar':calendar,'disclosure':'Post-trade exclusion analysis, NOT a re-simulation: historical sizes/fees are retained. Available saved closed trades only; exit-ordered realised equity rebuilt independently for each population. PARTIAL coverage remains PARTIAL. UTC entry filters use versioned DST-aware memberships; legacy clients use recorded labels.'}
    return _cached(db,key,make)


def _legacy_groups(rows):
    """Compatibility report blocks, same eligible P&L; labels are explicit."""
    import datetime as dt
    from .filter_rules import context
    months={};exits={};days={};sessions={}
    for t in rows:
        pnl=float(t['net_pnl']);ts,day,ss=context(t)
        try:month=dt.datetime.fromtimestamp(ts,dt.timezone.utc).strftime('%Y-%m') if ts is not None else 'unknown'
        except (ValueError,OverflowError,OSError):month='unknown'
        months[month]=months.get(month,0.)+pnl
        for target,key in [(exits,t.get('exit_reason') or 'UNKNOWN'),(days,day or 'unknown'),(sessions,str(t.get('session') or 'unknown'))]:
            block=target.setdefault(key,{'count':0,'net_pnl':0.});block['count']+=1;block['net_pnl']+=pnl
    return {'monthly_pnl':{k:round(v,2) for k,v in months.items()},'exit_reasons':exits,'by_day_of_week':days,'by_session':sessions,'grouping_note':'by_session compatibility labels are recorded session labels; calendar uses versioned UTC entry memberships. All groups use the same monetary/equity method.'}
