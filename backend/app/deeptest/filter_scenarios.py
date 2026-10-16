"""Durable named filter rules, deliberately separate from Deep run snapshots.
Assignment adds an entry gate only; original schedules/risk/arming stay untouched.
"""
import json,time,uuid,weakref,threading,hashlib
_LOCK=threading.RLock()
_READY=weakref.WeakKeyDictionary()
_ASSIGNMENTS=weakref.WeakKeyDictionary()
_REVISIONS=weakref.WeakKeyDictionary()
from .filter_rules import normalize

def mutation_lock(db):
    from contextlib import nullcontext
    from pathlib import Path
    from ..data.master import _store_lock
    path=getattr(db,'path',None)
    return _store_lock(Path(path).resolve().parent/'.filter-scenarios') if path and str(path)!=':memory:' else nullcontext()

def schema(db):
    if _READY.get(db)==id(db.conn):return
    with _LOCK,mutation_lock(db):_schema_locked(db)

def _schema_locked(db):
    if _READY.get(db)==id(db.conn):return
    _ASSIGNMENTS.pop(db,None)
    db.x('CREATE TABLE IF NOT EXISTS filter_scenarios (id TEXT PRIMARY KEY,name TEXT NOT NULL,scope TEXT NOT NULL,node_id INTEGER,rules TEXT NOT NULL,provenance TEXT,created_at REAL NOT NULL)')
    db.x('CREATE TABLE IF NOT EXISTS filter_scenario_assignments (node_id INTEGER NOT NULL,target TEXT NOT NULL,scenario_id TEXT NOT NULL,assigned_at REAL NOT NULL,PRIMARY KEY(node_id,target))')
    cols={r['name'] for r in db.q('PRAGMA table_info(filter_scenario_assignments)')}
    for col in ('rules_snapshot','source_version'):
        if col not in cols:db.x('ALTER TABLE filter_scenario_assignments ADD COLUMN '+col+' TEXT')
    _READY[db]=id(db.conn)

def listing(db,node_id=None):
    schema(db)
    rows=db.q('SELECT * FROM filter_scenarios WHERE scope=\'GLOBAL\' OR node_id=? ORDER BY created_at DESC',(node_id,))
    return [{**r,'rules':json.loads(r['rules']),'configuration_version':version(json.loads(r['rules'])),'provenance':json.loads(r['provenance'] or '{}')} for r in rows]

def save(db,payload):
    schema(db);name=str(payload.get('name') or '').strip();scope=payload.get('scope','GLOBAL')
    if not name or len(name)>120:raise ValueError('scenario name must contain 1..120 characters')
    if scope not in ('GLOBAL','NODE'):raise ValueError('scope must be GLOBAL or NODE')
    node_id=int(payload['node_id']) if scope=='NODE' else None
    if node_id is not None and not db.one('SELECT id FROM strategies WHERE id=?',(node_id,)):raise ValueError('canonical node does not exist')
    rules=normalize(payload.get('rules'));ident='filters-'+uuid.uuid4().hex[:16]
    db.x('INSERT INTO filter_scenarios VALUES (?,?,?,?,?,?,?)',(ident,name,scope,node_id,json.dumps(rules,sort_keys=True),json.dumps(payload.get('provenance') or {}),time.time()))
    return next(x for x in listing(db,node_id) if x['id']==ident)

def version(rules):return hashlib.sha256(json.dumps(normalize(rules),sort_keys=True).encode()).hexdigest()[:16]

def assignment(db,node_id,target='live'):
    schema(db)
    revision=(db.one('PRAGMA data_version')['data_version'],db.conn.total_changes)
    if _REVISIONS.get(db)!=revision:_ASSIGNMENTS.pop(db,None)
    if db not in _ASSIGNMENTS:
        result={}
        for row in db.q('SELECT s.*,a.node_id AS assigned_node,a.target,a.rules_snapshot,a.source_version,a.assigned_at FROM filter_scenario_assignments a JOIN filter_scenarios s ON s.id=a.scenario_id'):
            original=json.loads(row['rules']);snapshot=json.loads(row['rules_snapshot']) if row.get('rules_snapshot') else original
            result[(row['assigned_node'],row['target'])]={**row,'rules':snapshot,'configuration_version':row.get('source_version') or version(snapshot),'outdated':bool(row.get('source_version') and version(original)!=row['source_version'])}
        _ASSIGNMENTS[db]=result
        _REVISIONS[db]=revision
    return _ASSIGNMENTS[db].get((node_id,target))


def assign(db,node_id,ident,target='live'):
    with _LOCK,mutation_lock(db):return _assign_locked(db,node_id,ident,target)

def _assign_locked(db,node_id,ident,target='live'):
    schema(db)
    if target not in ('live','deep'):raise ValueError('target must be live or deep')
    if not db.one('SELECT id FROM strategies WHERE id=?',(node_id,)):raise ValueError('canonical node does not exist')
    if ident is None:
        db.x('DELETE FROM filter_scenario_assignments WHERE node_id=? AND target=?',(node_id,target))
        _ASSIGNMENTS.pop(db,None)
        return None
    sc=next((s for s in listing(db,node_id) if s['id']==ident),None)
    if not sc:raise ValueError('scenario is missing or out of node scope')
    db.x('INSERT INTO filter_scenario_assignments(node_id,target,scenario_id,assigned_at,rules_snapshot,source_version) VALUES (?,?,?,?,?,?) ON CONFLICT(node_id,target) DO UPDATE SET scenario_id=excluded.scenario_id,assigned_at=excluded.assigned_at,rules_snapshot=excluded.rules_snapshot,source_version=excluded.source_version',(node_id,target,ident,time.time(),json.dumps(sc['rules'],sort_keys=True),version(sc['rules'])))
    _ASSIGNMENTS.pop(db,None)
    return assignment(db,node_id,target)
