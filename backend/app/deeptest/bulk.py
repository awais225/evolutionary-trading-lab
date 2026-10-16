"""Durable, bounded historical queue; no live execution imports.
A process owns a batch until it exits. Cancelling only stops observation/remaining
entries, never an existing/shared historical run. Restart requires explicit resume.
"""
import json
import os
import threading
import time
import uuid
from contextlib import nullcontext
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix='deep-batch')
_ACTIVE = set()
_LOCK = threading.RLock()


def schema(db):
    db.x('CREATE TABLE IF NOT EXISTS deeptest_batches(id TEXT PRIMARY KEY,status TEXT NOT NULL,payload TEXT NOT NULL,results TEXT NOT NULL,updated_at REAL NOT NULL)')
    cols = {r['name'] for r in db.q('PRAGMA table_info(deeptest_batches)')}
    for name, definition in [('owner_pid', 'INTEGER'), ('cancel_requested', 'INTEGER NOT NULL DEFAULT 0')]:
        if name not in cols:
            db.x('ALTER TABLE deeptest_batches ADD COLUMN ' + name + ' ' + definition)


def _alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # cannot establish death: fail closed


def get(db, ident):
    schema(db)
    r = db.one('SELECT * FROM deeptest_batches WHERE id=?', (ident,))
    if not r:
        raise ValueError('batch does not exist')
    out = {**r, 'payload': json.loads(r['payload']), 'results': json.loads(r['results'])}
    orphan = not _alive(r.get('owner_pid')) or (r.get('owner_pid') == os.getpid() and ident not in _ACTIVE)
    if out['status'] in ('QUEUED', 'RUNNING') and orphan:
        db.x("UPDATE deeptest_batches SET status='INTERRUPTED' WHERE id=? AND status IN ('QUEUED','RUNNING')", (ident,))
        out['status'] = 'INTERRUPTED'
    out['total'] = len(out['payload']['node_ids'])
    out['finished'] = sum(r['status'] not in ('QUEUED', 'RUNNING') for r in out['results'])
    return out


def _claim_lock(db):
    from ..data.master import _store_lock
    path = getattr(db, 'path', None)
    return _store_lock(Path(path).resolve().parent / '.deep-batches') if path and str(path) != ':memory:' else nullcontext()


def create(db, payload):
    schema(db)
    payload = dict(payload)
    payload['node_ids'] = list(dict.fromkeys(int(i) for i in payload['node_ids']))
    if not 1 <= len(payload['node_ids']) <= 5000 or any(i <= 0 for i in payload['node_ids']):
        raise ValueError('select 1..5000 positive canonical IDs')
    ident = 'batch-' + uuid.uuid4().hex[:20]
    with _LOCK, _claim_lock(db):
        _ACTIVE.add(ident)
        db.x('INSERT INTO deeptest_batches(id,status,payload,results,updated_at,owner_pid) VALUES (?,?,?,?,?,?)',
             (ident, 'QUEUED', json.dumps(payload), '[]', time.time(), os.getpid()))
        try:
            _POOL.submit(_run, db, ident)
        except Exception:
            _ACTIVE.discard(ident)
            db.x("UPDATE deeptest_batches SET status='FAILED' WHERE id=?", (ident,))
            raise
    return get(db, ident)


def cancel(db, ident):
    get(db, ident)
    db.x('UPDATE deeptest_batches SET cancel_requested=1 WHERE id=?', (ident,))
    return {'ok': True, 'note': 'Stops the remaining queue; shared/existing tests are retained, not cancelled.'}


def resume(db, ident):
    with _LOCK, _claim_lock(db):
        job = get(db, ident)
        if job['status'] not in ('INTERRUPTED', 'CANCELLED'):
            raise ValueError('only interrupted/cancelled batches may explicitly resume')
        payload = {**job['payload'], 'retry_incomplete': True}
        _ACTIVE.add(ident)
        db.x("UPDATE deeptest_batches SET status='QUEUED',owner_pid=?,cancel_requested=0,payload=? WHERE id=?",
             (os.getpid(), json.dumps(payload), ident))
        _POOL.submit(_run, db, ident)
    return get(db, ident)


def _run(db, ident):
    from . import scenarios as SC, store as S
    results = []
    def save(status):
        db.x('UPDATE deeptest_batches SET status=?,results=?,updated_at=? WHERE id=?',
             (status, json.dumps(results), time.time(), ident))
    def cancelled():
        return bool(db.one('SELECT cancel_requested FROM deeptest_batches WHERE id=?', (ident,))['cancel_requested'])
    try:
        job = get(db, ident)
        payload, results = job['payload'], job['results']
        done = {r['node_id'] for r in results if r['status'] in ('COMPLETED', 'PARTIAL', 'REUSED', 'SKIPPED', 'FAILED')}
        save('RUNNING')
        for node in payload['node_ids']:
            if cancelled():
                break
            if node in done:
                continue
            results[:] = [r for r in results if r['node_id'] != node]
            rec = {'node_id': node, 'status': 'RUNNING'}
            results.append(rec)
            save('RUNNING')
            try:
                request = {k: v for k, v in payload.items() if k not in ('node_ids', 'node_settings_by_id')}
                request.update(node_id=node, node_settings=payload.get('node_settings_by_id', {}).get(str(node), payload.get('node_settings', {})))
                res = SC.start_run(db, request)
                if not res.get('ok'):
                    rec.update(status='SKIPPED', reason=res.get('error'), needs_confirmation=bool(res.get('needs_confirmation')), retry_required=bool(res.get('retry_required')))
                    save('RUNNING')
                    continue
                run = res.get('run_id') or res['scenario']['run_id']
                rec['run_id'] = run
                deadline = time.monotonic() + 1800
                while True:
                    scenario = S.get_scenario(db, run)
                    if not scenario:
                        raise ValueError('queued scenario disappeared')
                    if scenario['status'] in SC.TERMINAL:
                        rec.update(status=scenario['status'], reused=bool(res.get('already_deep_tested')), reason=scenario.get('error'))
                        break
                    if cancelled() or time.monotonic() > deadline:
                        rec.update(status='INTERRUPTED', reason='Observation stopped; existing run retained. Inspect before explicit resume.')
                        break
                    time.sleep(.15)
            except Exception as exc:
                rec.update(status='FAILED', reason=str(exc)[:200])
            save('RUNNING')
        if cancelled():
            seen = {r['node_id'] for r in results}
            results.extend({'node_id': n, 'status': 'CANCELLED', 'reason': 'Not started: remaining queue cancelled'} for n in payload['node_ids'] if n not in seen)
            save('CANCELLED')
        else:
            save('COMPLETED_WITH_ISSUES' if any(r['status'] not in ('COMPLETED', 'REUSED') for r in results) else 'COMPLETED')
    except Exception as exc:
        results.append({'node_id': None, 'status': 'FAILED', 'reason': 'Batch orchestration: ' + str(exc)[:200]})
        save('FAILED')
    finally:
        _ACTIVE.discard(ident)
