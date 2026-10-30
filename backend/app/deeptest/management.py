"""Explicit destructive DeepTest scope. No strategy/live/history/settings SQL here."""
from contextlib import contextmanager, nullcontext
from pathlib import Path
import json
import os
import re
import shutil
import tempfile
import time

from . import store as S, scenarios as SC
from .runtime import alive, _ACTIVE
from ..data.master import _store_lock
from .. import speed

class DeletionBusy(ValueError):pass

@contextmanager
def persistence_guard(db):
    path=getattr(db,'path',None)
    with (_store_lock(Path(path).resolve().parent/'.deep-persistence') if path and str(path)!=':memory:' else nullcontext()):yield


def _scratch(run_ids):
    """Only marked exact job directories inside OS TEMP; no shared cache paths."""
    from .. import paths as P
    temp_root=Path(tempfile.gettempdir()).resolve()
    root=(temp_root/'EvolutionaryTradingLab'/'DeepTest').resolve()
    if not root.is_relative_to(temp_root) or root.is_relative_to(P.DATA_ROOT.resolve()) or root.is_relative_to(P.ROOT_DIR.resolve()):raise DeletionBusy('Unsafe TEMP configuration; deletion refused')
    result=[]
    if not root.is_dir():return result
    for path in root.iterdir():
        if path.is_symlink() or not path.is_dir() or path.resolve().parent!=root or not re.fullmatch(r'dt-[a-zA-Z0-9-]+-[a-f0-9]{8}',path.name):continue
        ident=path.name.rsplit('-',1)[0]
        if ident not in run_ids:continue
        marker=path/'.deeptest-owner.json'
        if not marker.is_file() or marker.is_symlink() or marker.stat().st_size>4096:continue
        try:data=json.loads(marker.read_text())
        except (ValueError,OSError):continue
        if data.get('id')!=path.name:continue
        if alive(data.get('pid')) and (data.get('pid')!=os.getpid() or ident in _ACTIVE):raise DeletionBusy('A scratch owner is still alive; cancel and wait before deleting')
        result.append(path)
    return result


def delete(db, *, run_id=None, confirmation, node_ids=None, include_definitions=False):
    if node_ids is not None:
        node_ids=sorted(set(int(n) for n in node_ids))
        if not node_ids or any(n<=0 for n in node_ids):raise ValueError('Nonempty canonical node IDs required')
    all_scope=run_id is None and node_ids is None
    expected='DELETE SELECTED NODE SCENARIOS' if node_ids is not None else 'DELETE ALL DEEPTEST DATA' if run_id is None else 'DELETE '+run_id
    if confirmation!=expected:raise ValueError('Explicit typed confirmation required: '+expected)
    S.ensure_schema(db)
    from . import bulk
    path=getattr(db,'path',None)
    request_lock=_store_lock(Path(path).resolve().parent/'.deep-requests') if path and str(path)!=':memory:' else nullcontext()
    # Same ordering as submission; the file guard also serializes other processes'
    # result writers. Never wait on a Future while holding a database/writer lock.
    from . import filter_scenarios as F
    if include_definitions:F.schema(db)
    with SC._REQUEST_LOCK, request_lock, persistence_guard(db), SC._LOCK, (F._LOCK if include_definitions else nullcontext()), (F.mutation_lock(db) if include_definitions else nullcontext()), S._WRITE_LOCK:
        tables={r['name'] for r in db.q("SELECT name FROM sqlite_master WHERE type='table'")}
        if bulk._ACTIVE:raise DeletionBusy('A DeepTest batch is active; cancel it and wait before deletion')
        if 'deeptest_batches' in tables:
            for row in db.q('SELECT status,owner_pid FROM deeptest_batches'):
                if row['status'] in ('QUEUED','RUNNING','PREFLIGHTING','AWAITING_COVERAGE_DECISION'):raise DeletionBusy('An active/awaiting batch could recreate scenarios; cancel and wait')
                if row.get('owner_pid') not in (None,os.getpid()) and alive(row['owner_pid']):raise DeletionBusy('A foreign batch owner is alive; deletion refused')
        rows=db.q('SELECT run_id,status FROM deeptest_scenarios'+(' WHERE run_id=?' if run_id is not None else ''),(run_id,) if run_id is not None else ())
        if run_id is not None and not rows:raise KeyError('Scenario does not exist')
        if node_ids is not None:rows=[r for r in db.q('SELECT run_id,node_id,status FROM deeptest_scenarios') if r['node_id'] in set(node_ids)]
        ids={r['run_id'] for r in rows}
        if all_scope:ids.update(r['run_id'] for r in db.q('SELECT DISTINCT run_id FROM deeptest_trades') if r['run_id'])
        for row in rows:
            if row['status'] not in ('COMPLETED','PARTIAL','CANCELLED','FAILED','INTERRUPTED'):raise DeletionBusy('Scenario is active; cancel and wait for its worker to stop')
        # Match cancel/finish ordering: scenario state lock BEFORE result writer.
        # Never acquire the scenario lock while holding only the writer lock.
        for ident in ids:
            future=SC._FUTURES.get(ident)
            if ident in _ACTIVE or (future is not None and not future.done()):raise DeletionBusy('Worker still stopping; deletion refused until it has stopped writing')
        if 'deeptest_claims' in tables:
            claims=db.q('SELECT run_id,owner_pid FROM deeptest_claims')
            for c in claims:
                if c['run_id'] in ids and c['owner_pid']!=os.getpid() and alive(c['owner_pid']):raise DeletionBusy('Cannot prove that a foreign worker has stopped; deletion refused')
        artifacts=_scratch(ids)
        if all_scope:
            statements=[('INSERT OR IGNORE INTO deeptest_deleted_runs SELECT run_id,? FROM deeptest_scenarios',(time.time(),)),
                        ('INSERT OR IGNORE INTO deeptest_deleted_runs SELECT DISTINCT run_id,? FROM deeptest_trades WHERE run_id IS NOT NULL',(time.time(),))]
            for table in ('deeptest_trades','deeptest_prefs','deeptest_claims','deeptest_scenarios','deeptest_batches'):
                if table in tables:statements.append(('DELETE FROM '+table,()))
        else:
            statements=[]
            for ident in sorted(ids):
                statements.extend([('INSERT OR IGNORE INTO deeptest_deleted_runs VALUES (?,?)',(ident,time.time())),('DELETE FROM deeptest_trades WHERE run_id=?',(ident,)),('DELETE FROM deeptest_prefs WHERE preferred_run_id=?',(ident,))])
                if 'deeptest_claims' in tables:statements.append(('DELETE FROM deeptest_claims WHERE run_id=?',(ident,)))
                statements.append(('DELETE FROM deeptest_scenarios WHERE run_id=?',(ident,)))
            if 'deeptest_batches' in tables:
                for batch in db.q('SELECT id,results FROM deeptest_batches'):
                    result=json.loads(batch['results']);kept=[r for r in result if r.get('run_id') not in ids]
                    if kept!=result:statements.append(('UPDATE deeptest_batches SET results=? WHERE id=?',(json.dumps(kept),batch['id'])))
        definitions,retained=definition_scope(db,ids,node_ids,all_scope) if include_definitions else ([],[])
        if include_definitions:
            if all_scope:statements.append(("DELETE FROM filter_scenario_assignments WHERE target='deep'",()))
            else:
                for nid in node_ids or []:statements.append(("DELETE FROM filter_scenario_assignments WHERE target='deep' AND node_id=?",(nid,)))
            for ident in definitions:
                statements.extend([("DELETE FROM filter_scenario_assignments WHERE target='deep' AND scenario_id=?",(ident,)),('DELETE FROM filter_scenarios WHERE id=?',(ident,))])
        db.transaction(statements)
        cleanup_errors=0
        for artifact in artifacts:
            try:shutil.rmtree(artifact)
            except OSError:cleanup_errors+=1
        speed.event('deeptest','deleted',run_id=run_id,fields={'rows':len(rows)})
        return {'ok':True,'deleted_scenarios':len(rows),'deleted_definitions':len(definitions),'retained_live_or_shared_definitions':retained,'scope':'DeepTest scenario/trade/preference/claim records; completed batch records for delete-all only',
                'preserved':'nodes, genomes, ancestry, generation, shortlist, market data, shared caches, live/unrelated backtests and configuration',
                'artifact_cleanup_errors':cleanup_errors,'recreation_guard':'durable deleted-run tombstones'}


def definition_scope(db,run_ids,node_ids,all_scope):
    deletable=[];retained=[]
    selected=set(node_ids or [])
    for row in db.q('SELECT * FROM filter_scenarios'):
        prov=json.loads(row.get('provenance') or '{}')
        candidate=(row['scope']=='NODE' and (all_scope or row['node_id'] in selected)) or (all_scope and row['scope']=='GLOBAL')
        if not candidate:continue
        refs=db.q('SELECT node_id,target FROM filter_scenario_assignments WHERE scenario_id=?',(row['id'],))
        shared=any(ref['target']=='live' or (not all_scope and ref['node_id'] not in selected) for ref in refs)
        origin=db.one('SELECT node_id FROM deeptest_scenarios WHERE run_id=?',(prov.get('run_id'),)) if prov.get('run_id') else None
        proven_deep=prov.get('domain')=='deeptest' or any(ref['target']=='deep' for ref in refs) or (origin is not None and (row['scope']=='GLOBAL' or origin['node_id']==row['node_id']))
        if shared or prov.get('domain')=='live_testing' or not proven_deep:retained.append(row['id'])
        else:deletable.append(row['id'])
    return deletable,retained


def preview(db,node_ids=None):
    from . import filter_scenarios as F
    F.schema(db);S.ensure_schema(db)
    selected=None if node_ids is None else {int(n) for n in node_ids}
    if selected is not None and (not selected or any(n<=0 for n in selected)):raise ValueError('Select at least one canonical node')
    rows=[r for r in db.q('SELECT run_id,node_id,status FROM deeptest_scenarios') if selected is None or r['node_id'] in selected]
    ids={r['run_id'] for r in rows}
    if selected is None:ids.update(r['run_id'] for r in db.q('SELECT DISTINCT run_id FROM deeptest_trades'))
    counts=db.q('SELECT run_id,COUNT(*) c FROM deeptest_trades GROUP BY run_id')
    definitions,retained=definition_scope(db,ids,node_ids,selected is None)
    return {'nodes':len(selected) if selected is not None else len({r['node_id'] for r in rows}),'runs':len(rows),'scenarios':len(definitions),'trade_records':sum(r['c'] for r in counts if r['run_id'] in ids),'retained_live_or_shared_definitions':len(retained),'active_runs':[r['run_id'] for r in rows if r['status'] not in ('COMPLETED','PARTIAL','CANCELLED','FAILED','INTERRUPTED')],'run_ids':sorted(ids),'preserved':'nodes/genomes/ancestry/generation/stars/shortlist/market/unrelated backtests/Live configs/AI research; live/shared definitions retained'}


def bulk_delete(db,node_ids,confirmation):
    from concurrent.futures import TimeoutError,CancelledError
    expected='DELETE ALL SCENARIOS' if node_ids is None else 'DELETE SELECTED NODE SCENARIOS'
    if confirmation!=expected:raise ValueError('Type exactly '+expected)
    from . import bulk
    tables={row['name'] for row in db.q("SELECT name FROM sqlite_master WHERE type='table'")}
    active=('QUEUED','RUNNING','PREFLIGHTING','AWAITING_COVERAGE_DECISION')
    if 'deeptest_batches' in tables:
        deadline=time.monotonic()+10
        for batch in db.q('SELECT id,payload,status FROM deeptest_batches'):
            if batch['status'] not in active:continue
            owned=set(json.loads(batch['payload']).get('node_ids') or [])
            if node_ids is not None and (not owned or not owned<=set(node_ids)):
                raise DeletionBusy('A batch includes unrelated nodes; stop it explicitly first. No unrelated jobs cancelled or data deleted')
            bulk.cancel(db,batch['id'])
            while True:
                state=db.one('SELECT status FROM deeptest_batches WHERE id=?',(batch['id'],))
                if state and state['status'] not in active and batch['id'] not in bulk._ACTIVE:break
                if time.monotonic()>=deadline:raise DeletionBusy('Batch cancellation requested; still stopping. No records deleted; retry when stopped')
                # API endpoint runs in a worker thread; never holds writer locks.
                import threading
                threading.Event().wait(.05)
    plan=preview(db,node_ids)
    # Cancellation happens BEFORE taking deletion/write locks. Terminal state
    # alone is insufficient: wait for all affected writer Futures to finish.
    for ident in plan['active_runs']:SC.cancel_run(db,ident)
    deadline=time.monotonic()+10
    for ident in plan['run_ids']:
        future=SC._FUTURES.get(ident)
        if future is not None:
            try:future.result(timeout=max(0,deadline-time.monotonic()))
            except TimeoutError:raise DeletionBusy('Cancellation requested; worker still stopping. No records deleted; retry after it stops')
            except CancelledError:pass
            except Exception:pass # failed Future is terminal; final guards below recheck owners
    return delete(db,node_ids=node_ids,include_definitions=True,confirmation='DELETE ALL DEEPTEST DATA' if node_ids is None else expected)
