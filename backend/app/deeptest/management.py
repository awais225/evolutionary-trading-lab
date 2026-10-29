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


def delete(db, *, run_id=None, confirmation):
    expected='DELETE ALL DEEPTEST DATA' if run_id is None else 'DELETE '+run_id
    if confirmation!=expected:raise ValueError('Explicit typed confirmation required: '+expected)
    S.ensure_schema(db)
    from . import bulk
    path=getattr(db,'path',None)
    request_lock=_store_lock(Path(path).resolve().parent/'.deep-requests') if path and str(path)!=':memory:' else nullcontext()
    # Same ordering as submission; the file guard also serializes other processes'
    # result writers. Never wait on a Future while holding a database/writer lock.
    with SC._REQUEST_LOCK, request_lock, persistence_guard(db), SC._LOCK, S._WRITE_LOCK:
        tables={r['name'] for r in db.q("SELECT name FROM sqlite_master WHERE type='table'")}
        if bulk._ACTIVE:raise DeletionBusy('A DeepTest batch is active; cancel it and wait before deletion')
        if 'deeptest_batches' in tables:
            for row in db.q('SELECT status,owner_pid FROM deeptest_batches'):
                if row['status'] in ('QUEUED','RUNNING','PREFLIGHTING','AWAITING_COVERAGE_DECISION'):raise DeletionBusy('An active/awaiting batch could recreate scenarios; cancel and wait')
                if row.get('owner_pid') not in (None,os.getpid()) and alive(row['owner_pid']):raise DeletionBusy('A foreign batch owner is alive; deletion refused')
        rows=db.q('SELECT run_id,status FROM deeptest_scenarios'+(' WHERE run_id=?' if run_id is not None else ''),(run_id,) if run_id is not None else ())
        if run_id is not None and not rows:raise KeyError('Scenario does not exist')
        ids={r['run_id'] for r in rows}
        if run_id is None:ids.update(r['run_id'] for r in db.q('SELECT DISTINCT run_id FROM deeptest_trades') if r['run_id'])
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
        if run_id is None:
            statements=[('INSERT OR IGNORE INTO deeptest_deleted_runs SELECT run_id,? FROM deeptest_scenarios',(time.time(),)),
                        ('INSERT OR IGNORE INTO deeptest_deleted_runs SELECT DISTINCT run_id,? FROM deeptest_trades WHERE run_id IS NOT NULL',(time.time(),))]
            for table in ('deeptest_trades','deeptest_prefs','deeptest_claims','deeptest_scenarios','deeptest_batches'):
                if table in tables:statements.append(('DELETE FROM '+table,()))
        else:
            statements=[('INSERT OR IGNORE INTO deeptest_deleted_runs VALUES (?,?)',(run_id,time.time())),
                        ('DELETE FROM deeptest_trades WHERE run_id=?',(run_id,)),
                        ('DELETE FROM deeptest_prefs WHERE preferred_run_id=?',(run_id,))]
            if 'deeptest_claims' in tables:statements.append(('DELETE FROM deeptest_claims WHERE run_id=?',(run_id,)))
            statements.append(('DELETE FROM deeptest_scenarios WHERE run_id=?',(run_id,)))
            # Completed batch ledgers are historical records; remove only this
            # scenario's cached result projection, not unrelated batch entries.
            if 'deeptest_batches' in tables:
                for batch in db.q('SELECT id,results FROM deeptest_batches'):
                    result=json.loads(batch['results'])
                    kept=[r for r in result if r.get('run_id')!=run_id]
                    if kept!=result:statements.append(('UPDATE deeptest_batches SET results=? WHERE id=?',(json.dumps(kept),batch['id'])))
        db.transaction(statements)
        cleanup_errors=0
        for artifact in artifacts:
            try:shutil.rmtree(artifact)
            except OSError:cleanup_errors+=1
        speed.event('deeptest','deleted',run_id=run_id,fields={'rows':len(rows)})
        return {'ok':True,'deleted_scenarios':len(rows),'scope':'DeepTest scenario/trade/preference/claim records; completed batch records for delete-all only',
                'preserved':'nodes, genomes, ancestry, generation, shortlist, market data, shared caches, live/unrelated backtests and configuration',
                'artifact_cleanup_errors':cleanup_errors,'recreation_guard':'durable deleted-run tombstones'}
