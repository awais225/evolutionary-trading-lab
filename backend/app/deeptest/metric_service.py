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
from ..db.read_revision import revision_snapshot, token
from ..read_diagnostics import phase

VERSION='V10.5-realised-initial-v1'
_CACHE=weakref.WeakKeyDictionary()
_LOCK=threading.RLock()
MAX_BYTES=8*1024*1024

def _revision(db):return token(db, VERSION)

def _cached(db,key,make):
    if key is None:
        return make()
    with _LOCK:
        cache=_CACHE.setdefault(db,OrderedDict())
        hit=cache.get(key)
        if hit is not None:cache.move_to_end(key)
    if hit is not None:return copy.deepcopy(hit[0])
    value=make()
    with phase('deeptest.cache_serialization'):
        size=len(json.dumps(value,default=str))
    stored=copy.deepcopy(value) if size<=MAX_BYTES else None
    with _LOCK:
        cache=_CACHE.setdefault(db,OrderedDict())
        if size<=MAX_BYTES:
            cache[key]=(stored,size)
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
    cap,source=capital(sc)
    with phase('deeptest.metric_calculation'):
        out=A.summarize(trades,initial_capital=cap)
    old=(sc.get('analytics') or {}).get('summary') or sc.get('metrics') or {}
    available=len(trades);expected=old.get('trades')
    with phase('deeptest.json_fingerprint'):
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
        with revision_snapshot(db, VERSION) as rev:
            header_rows=db.q_rows('SELECT * FROM deeptest_scenarios WHERE run_id IN ('+','.join('?' for _ in ids)+')',tuple(ids))
            with _LOCK:
                cache=_CACHE.setdefault(db,OrderedDict())
                existing={ident:cache[(rev,ident)][0] for ident in ids if rev is not None and (rev,ident) in cache}
            missing=[ident for ident in ids if ident not in existing]
            trade_rows=[]
            if missing:
                with phase('deeptest.trade_retrieval'):
                    trade_rows=db.q_rows('SELECT * FROM deeptest_trades WHERE run_id IN ('+','.join('?' for _ in missing)+') ORDER BY run_id,trade_no,id',tuple(missing))
        with phase('deeptest.materialize'):
            headers={row['run_id']:S.hydrate(dict(row)) for row in header_rows}
            existing={ident:copy.deepcopy(value) for ident,value in existing.items()}
            by={ident:[] for ident in missing}
            for row in trade_rows:by[row['run_id']].append(dict(row))
        # CPU work is OUTSIDE the database lock/read transaction. Headers and
        # rows above are one captured snapshot; subsequent writes change the key.
        for ident in ids:
            if ident not in headers:continue
            sc=headers[ident]
            computed=existing[ident] if ident in existing else _cached(db,(rev,ident) if rev is not None else None,lambda:calculate(sc,by[ident]))
            sc['analytics']=computed
            sc['metrics']={**(sc.get('metrics') or {}),**computed['summary']}
            sc['metric_provenance']=validation_provenance(computed)
            if supplied[ident].get('preference_source'):sc['preference_source']=supplied[ident]['preference_source']
            result.append(sc)
    return result

def enrich(db,sc):return enrich_many(db,[sc])[0] if sc else None

def filtered(db,run_id,payload):
    from . import store as S
    from .filter_rules import normalize,reason,context,DAYS
    from .inspect_calendar import aggregate
    rules=normalize(payload)
    S.ensure_schema(db)
    with revision_snapshot(db, VERSION) as rev:
        header=db.q_rows('SELECT * FROM deeptest_scenarios WHERE run_id=?',(run_id,))
        if not header:raise KeyError('Scenario not found')
        key=(rev,run_id,'filter',json.dumps({'rules':rules,'legacy':payload.get('schema_version') is None},sort_keys=True)) if rev is not None else None
        with _LOCK:
            cache=_CACHE.setdefault(db,OrderedDict())
            hit=cache.get(key) if key is not None else None
        raw_rows=[]
        if hit is None:
            with phase('deeptest.trade_retrieval'):
                raw_rows=db.q_rows('SELECT * FROM deeptest_trades WHERE run_id=? ORDER BY trade_no,id',(run_id,))
    if hit is not None:return copy.deepcopy(hit[0])
    with phase('deeptest.materialize'):
        sc=S.hydrate(dict(header[0]))
        raw=[dict(r) for r in raw_rows]
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


def validation_provenance(value, *, listing=False):
    """Describe calculated evidence, never alter financial results or infer freshness.

    Called only for a captured calculation / SAME-revision cache hit. Legacy
    persisted summaries cannot call this path just by carrying a version/hash.
    Missing evidence is not mathematical N/A. Field-level N/A is meaningful
    only after calculation; positive/zero values and original reasons survive.
    """
    proof = dict(value.get('provenance') or {})
    missing = proof.get('integrity') == 'MISSING_TRADE_RECORDS'
    incomplete = proof.get('trade_count_matches_stored') is False
    if missing or (listing and incomplete):
        proof.update(validation_state='HISTORICAL_UNAVAILABLE',
                     validation_reason='Saved trade evidence is missing or incomplete; Inspect retains the available-row calculation and coverage warning.',
                     field_states={})
        return proof
    fields = {}
    reasons = {v.get('field'): v.get('reason') for v in value.get('unavailable', [])}
    for name, result in (value.get('summary') or {}).items():
        if result is not None:
            continue
        metadata = name in ('initial_capital', 'account_currency')
        fields[name] = dict(state='HISTORICAL_UNAVAILABLE' if metadata else 'NOT_APPLICABLE',
                           reason=reasons.get(name) or ('Not recorded in the saved run evidence.' if metadata else
                           'The validated calculator has no supported value for this metric; see its data/denominator/chronology disclosure.'))
    proof.update(validation_state='AUTHORITATIVE', field_states=fields,
                 validation_reason='Calculated from the captured available saved trades; coverage and stored-count qualifications remain in provenance.')
    return proof
