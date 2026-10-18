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
    for name, definition in [('owner_pid', 'INTEGER'), ('cancel_requested', 'INTEGER NOT NULL DEFAULT 0'), ('preflight', "TEXT NOT NULL DEFAULT '{}'"), ('coverage_decision', 'TEXT')]:
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
    out = {**r, 'payload': json.loads(r['payload']), 'results': json.loads(r['results']), 'preflight': json.loads(r['preflight'] or '{}')}
    orphan = not _alive(r.get('owner_pid')) or (r.get('owner_pid') == os.getpid() and ident not in _ACTIVE)
    if out['status'] in ('QUEUED', 'RUNNING', 'PREFLIGHTING') and orphan:
        db.x("UPDATE deeptest_batches SET status='INTERRUPTED' WHERE id=? AND status IN ('QUEUED','RUNNING','PREFLIGHTING')", (ident,))
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
    payload.setdefault('requested_end', int(time.time()//60)*60)
    ident = 'batch-' + uuid.uuid4().hex[:20]
    with _LOCK, _claim_lock(db):
        # One active identical selection/range/settings submission, even from
        # concurrent HTTP clicks. A terminal batch may be explicitly submitted again.
        canonical=json.dumps({**payload,'node_ids':sorted(payload['node_ids'])},sort_keys=True,separators=(',',':'))
        for prior in db.q("SELECT id,payload FROM deeptest_batches WHERE status IN ('QUEUED','RUNNING','PREFLIGHTING','AWAITING_COVERAGE_DECISION')"):
            previous=json.loads(prior['payload']);previous['node_ids']=sorted(previous['node_ids'])
            if json.dumps(previous,sort_keys=True,separators=(',',':'))==canonical:return get(db,prior['id'])
        _ACTIVE.add(ident)
        db.x('INSERT INTO deeptest_batches(id,status,payload,results,updated_at,owner_pid) VALUES (?,?,?,?,?,?)',
             (ident, 'QUEUED', json.dumps(payload), '[]', time.time(), os.getpid()))
        try:
            _POOL.submit(_prepare, db, ident)
        except Exception:
            _ACTIVE.discard(ident)
            db.x("UPDATE deeptest_batches SET status='FAILED' WHERE id=?", (ident,))
            raise
    return get(db, ident)


def cancel(db, ident):
    get(db, ident)
    db.x('UPDATE deeptest_batches SET cancel_requested=1 WHERE id=?', (ident,))
    db.x("UPDATE deeptest_batches SET status='CANCELLED' WHERE id=? AND status='AWAITING_COVERAGE_DECISION'", (ident,))
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
        _POOL.submit(_prepare, db, ident)
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
                pre = (job.get('preflight') or {}).get('nodes', {}).get(str(node))
                if pre:
                    rec['coverage'] = pre.get('coverage')
                    rec['requested_period'] = pre.get('period')
                    rec['coverage_decision'] = job.get('coverage_decision')
                    if not pre.get('ok') or ((pre.get('coverage') or {}).get('coverage_status')!='COMPLETE' and job.get('coverage_decision') == 'full_only'):
                        rec.update(status='SKIPPED',reason=pre.get('error') or 'Incomplete coverage excluded by batch decision')
                        save('RUNNING');continue
                    if (pre.get('coverage') or {}).get('coverage_status')!='COMPLETE':
                        current = SC.preflight(db,node_id=node,duration_key=request.get('duration_key','3M'),requested_end=request.get('requested_end'),settings=request.get('settings'),node_settings=request.get('node_settings'))
                        if (current.get('coverage') or {}).get('confirmation_fingerprint') != pre['coverage']['confirmation_fingerprint']:
                            rec.update(status='SKIPPED',reason='Coverage/data/settings changed after batch consent; preflight again')
                            save('RUNNING');continue
                        SC.record_decision(db,confirmation_fingerprint=pre['coverage']['confirmation_fingerprint'],decision='YES_PROCEED',node_id=node)
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


def _prepare(db,ident):
    """Persist per-node coverage asynchronously; no historical runs before consent."""
    from . import scenarios as SC
    try:
        job=get(db,ident);payload=job['payload']
        db.x("UPDATE deeptest_batches SET status='PREFLIGHTING' WHERE id=?",(ident,))
        report={'nodes':{},'full':0,'partial':0,'unavailable':0,'selected':len(payload['node_ids'])}
        for node in payload['node_ids']:
            if db.one('SELECT cancel_requested FROM deeptest_batches WHERE id=?',(ident,))['cancel_requested']:
                db.x("UPDATE deeptest_batches SET status='CANCELLED',preflight=? WHERE id=?",(json.dumps(report),ident));return
            try:
                p=SC.preflight(db,node_id=node,duration_key=payload.get('duration_key','3M'),requested_end=payload.get('requested_end'),settings=payload.get('settings'),node_settings=payload.get('node_settings_by_id',{}).get(str(node),payload.get('node_settings',{})))
            except Exception as exc:p={'ok':False,'error':str(exc)[:200]}
            report['nodes'][str(node)]=p
            kind='unavailable' if not p.get('ok') else 'partial' if (p.get('coverage') or {}).get('coverage_status')!='COMPLETE' else 'full'
            report[kind]+=1
            if len(report['nodes'])%10==0:db.x('UPDATE deeptest_batches SET preflight=?,updated_at=? WHERE id=?',(json.dumps(report),time.time(),ident))
        waiting=report['partial']+report['unavailable']>0
        db.x('UPDATE deeptest_batches SET preflight=?,status=?,updated_at=? WHERE id=?',(json.dumps(report),'AWAITING_COVERAGE_DECISION' if waiting else 'QUEUED',time.time(),ident))
        if not waiting:_run(db,ident)
    except Exception as exc:
        db.x("UPDATE deeptest_batches SET status='FAILED',results=? WHERE id=?",(json.dumps([{'node_id':None,'status':'FAILED','reason':'Coverage preflight: '+str(exc)[:200]}]),ident))
    finally:_ACTIVE.discard(ident)

def coverage_decision(db,ident,decision):
    if decision not in ('partial','full_only','cancel'):raise ValueError('Unknown batch coverage decision')
    with _LOCK,_claim_lock(db):
        job=get(db,ident)
        if job['status']!='AWAITING_COVERAGE_DECISION':raise ValueError('Batch is not awaiting coverage consent')
        if decision=='cancel':return cancel(db,ident)
        db.x("UPDATE deeptest_batches SET status='QUEUED',coverage_decision=?,owner_pid=? WHERE id=?",(decision,os.getpid(),ident))
        _ACTIVE.add(ident);_POOL.submit(_run,db,ident)
    return get(db,ident)
