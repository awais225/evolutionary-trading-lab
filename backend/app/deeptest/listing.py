"""Lightweight saved-result listings. Never reads deeptest_trades.

Only this process's metric-service cache, proven at the SAME SQLite revision,
can supply financial summaries. Legacy stored analytics/version labels alone
are not freshness proofs. A cold/stale entry is explicitly UNVERIFIED, not mathematical N/A, until
Inspect or another detailed read calculates it. No derived result is written to history.
"""
import copy
from collections import defaultdict
from . import metric_service as M, store as S
from ..db.read_revision import revision_snapshot
from ..read_diagnostics import phase

# Intentionally exclude stored analytics/metrics: these may be large, stale and
# lack a matching trade-revision proof. All metadata needed to open details stays.
_COLUMNS = ('run_id,node_id,status,coverage_status,duration_key,requested_start,'
            'requested_end,actual_start,actual_end,bars_used,signature,finished_at,'
            'created_at,dataset_id,dataset_fingerprint,symbol,timeframe,error,notes')
_VALID = ('COMPLETED', 'PARTIAL')
_REASON = 'Historical metrics have no current saved-trade revision proof. No validation is queued automatically; Inspect validates the selected scenario.'


def _read(db, node_id=None, statuses=None):
    S.ensure_schema(db)
    where=[];args=[]
    if node_id is not None:
        where.append('node_id=?');args.append(int(node_id))
    if statuses:
        where.append('status IN ('+','.join('?' for _ in statuses)+')');args.extend(statuses)
    query='SELECT '+_COLUMNS+' FROM deeptest_scenarios'
    if where:query+=' WHERE '+' AND '.join(where)
    # Stable tie-breaker agrees with the preferred/latest index selection.
    query+=' ORDER BY node_id,created_at DESC,run_id DESC'
    with phase('deeptest.listing_total'):
        with revision_snapshot(db, M.VERSION) as rev:
            headers=db.q_rows(query,tuple(args))
            prefs=db.q_rows('SELECT node_id,preferred_run_id,explicitly_selected FROM deeptest_prefs')
            with M._LOCK:
                cache=M._CACHE.get(db, {})
                values={r['run_id']:cache[(rev,r['run_id'])][0] for r in headers if rev is not None and (rev,r['run_id']) in cache}
        # Everything below is pure Python over detached, immutable snapshot rows.
        rows=[]
        for header in headers:
            row=dict(header);value=values.get(row['run_id'])
            if value is not None:
                analytics=copy.deepcopy({k:value[k] for k in ('summary','provenance','unavailable') if k in value})
                proof={**M.validation_provenance(analytics, listing=True),'availability':'VERIFIED'}
                if proof.get('trade_count_matches_stored') is False:
                    # A current calculation of AVAILABLE rows is not proof of a
                    # complete retained population. Keep detailed results intact,
                    # but never promote an incomplete headline to listing truth.
                    reason='Saved trade count differs from the stored expectation; open Inspect to review available rows.'
                    proof.update(availability='UNAVAILABLE',reason=reason)
                    proof.setdefault('integrity','INCOMPLETE_TRADE_RECORDS')
                    analytics['summary']={k:(v if k in ('initial_capital','account_currency') else None)
                                          for k,v in analytics['summary'].items()}
                    analytics.setdefault('unavailable',[]).append({'field':'trade_metrics','reason':reason})
                row.update(analytics=analytics,metrics=copy.deepcopy(analytics['summary']),
                           metric_provenance=proof)
            else:
                provenance=dict(run_id=row['run_id'],node_id=row['node_id'],
                                metric_version=M.VERSION,availability='UNAVAILABLE',reason=_REASON,
                                validation_state='HISTORICAL_UNVERIFIED',validation_reason=_REASON,field_states={},
                                execution_status=row['status'],coverage_status=row['coverage_status'])
                row.update(analytics={'summary':{},'unavailable':[{'field':'trade_metrics','reason':_REASON}]},
                           metrics={},metric_provenance=provenance)
            rows.append(row)
        return rows, {r['node_id']:dict(r) for r in prefs}


def scenarios(db, node_id=None, statuses=None):
    rows,prefs=_read(db,node_id,statuses)
    by_node=defaultdict(list)
    for row in rows:by_node[int(row['node_id'])].append(row)
    for nid,items in by_node.items():
        chosen,source,_=_choose(items,prefs.get(nid))
        for row in items:
            row['preferred_run_id']=chosen['run_id'] if chosen else None
            row['preference_source']=source if chosen else None
    return rows


def _choose(rows, preference):
    valid=[r for r in rows if r['status'] in _VALID]
    chosen=None
    if preference and preference.get('explicitly_selected'):
        chosen=next((r for r in valid if r['run_id']==preference.get('preferred_run_id')),None)
    source='explicit' if chosen is not None else 'latest_valid'
    if chosen is None and valid:chosen=valid[0]
    if chosen is not None:chosen={**chosen,'preference_source':source}
    return chosen,source,len(valid)


def node_summaries(db):
    from .scenarios import _format_node_summary
    rows,prefs=_read(db);by_node=defaultdict(list)
    for row in rows:by_node[int(row['node_id'])].append(row)
    return [_format_node_summary(nid,items,_choose(items,prefs.get(nid))[0])
            for nid,items in sorted(by_node.items())]


def index(db):
    rows,prefs=_read(db);by_node=defaultdict(list)
    for row in rows:by_node[int(row['node_id'])].append(row)
    result={}
    for nid,items in by_node.items():
        chosen,source,count=_choose(items,prefs.get(nid))
        if chosen is not None:
            result[nid]={'preferred':chosen,'preference_source':source,'valid_count':count}
    return result
