"""Durable named filter rules, deliberately separate from Deep run snapshots.
Assignment adds an entry gate only; original schedules/risk/arming stay untouched.
"""
import json,time,uuid,weakref,threading
_LOCK=threading.RLock()
_READY=weakref.WeakSet()
_ASSIGNMENTS=weakref.WeakKeyDictionary()
from .filter_rules import normalize

def schema(db):
    if db in _READY:return
    db.x('CREATE TABLE IF NOT EXISTS filter_scenarios (id TEXT PRIMARY KEY,name TEXT NOT NULL,scope TEXT NOT NULL,node_id INTEGER,rules TEXT NOT NULL,provenance TEXT,created_at REAL NOT NULL)')
    db.x('CREATE TABLE IF NOT EXISTS filter_scenario_assignments (node_id INTEGER NOT NULL,target TEXT NOT NULL,scenario_id TEXT NOT NULL,assigned_at REAL NOT NULL,PRIMARY KEY(node_id,target))')
    _READY.add(db)

def listing(db,node_id=None):
    schema(db)
    rows=db.q('SELECT * FROM filter_scenarios WHERE scope=\'GLOBAL\' OR node_id=? ORDER BY created_at DESC',(node_id,))
    return [{**r,'rules':json.loads(r['rules']),'provenance':json.loads(r['provenance'] or '{}')} for r in rows]

def save(db,payload):
    schema(db);name=str(payload.get('name') or '').strip();scope=payload.get('scope','GLOBAL')
    if not name or len(name)>120:raise ValueError('scenario name must contain 1..120 characters')
    if scope not in ('GLOBAL','NODE'):raise ValueError('scope must be GLOBAL or NODE')
    node_id=int(payload['node_id']) if scope=='NODE' else None
    if node_id is not None and not db.one('SELECT id FROM strategies WHERE id=?',(node_id,)):raise ValueError('canonical node does not exist')
    rules=normalize(payload.get('rules'));ident='filters-'+uuid.uuid4().hex[:16]
    db.x('INSERT INTO filter_scenarios VALUES (?,?,?,?,?,?,?)',(ident,name,scope,node_id,json.dumps(rules,sort_keys=True),json.dumps(payload.get('provenance') or {}),time.time()))
    return next(x for x in listing(db,node_id) if x['id']==ident)

def assignment(db,node_id,target='live'):
    schema(db)
    if db not in _ASSIGNMENTS:
        _ASSIGNMENTS[db]={(row['assigned_node'],row['target']):{**row,'rules':json.loads(row['rules'])} for row in db.q('SELECT s.*,a.node_id AS assigned_node,a.target FROM filter_scenario_assignments a JOIN filter_scenarios s ON s.id=a.scenario_id')}
    return _ASSIGNMENTS[db].get((node_id,target))

def assign(db,node_id,ident,target='live'):
    schema(db)
    if target not in ('live','deep'):raise ValueError('target must be live or deep')
    if not db.one('SELECT id FROM strategies WHERE id=?',(node_id,)):raise ValueError('canonical node does not exist')
    if ident is None:
        db.x('DELETE FROM filter_scenario_assignments WHERE node_id=? AND target=?',(node_id,target))
        _ASSIGNMENTS.pop(db,None)
        return None
    sc=next((s for s in listing(db,node_id) if s['id']==ident),None)
    if not sc:raise ValueError('scenario is missing or out of node scope')
    db.x('INSERT INTO filter_scenario_assignments VALUES (?,?,?,?) ON CONFLICT(node_id,target) DO UPDATE SET scenario_id=excluded.scenario_id,assigned_at=excluded.assigned_at',(node_id,target,ident,time.time()))
    _ASSIGNMENTS.pop(db,None)
    return assignment(db,node_id,target)
