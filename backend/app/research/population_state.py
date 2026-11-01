"""V9.5 persisted population accounting; no strategy decisions."""
import hashlib,json,time
import threading,weakref
from ..db.read_revision import revision_snapshot
from ..read_diagnostics import phase
_CACHE = weakref.WeakKeyDictionary()
# One current grouped population per live Database, <= 4 MiB estimate/8192 groups.
_MAX_BYTES = 4 * 1024 * 1024
_MAX_GROUPS = 8192
_SQL = 'SELECT s.run_id,s.generation,s.status,s.data_source,COUNT(s.id) n,MAX(s.updated_at) updated,r.revision registry_version FROM node_registry_revision r LEFT JOIN strategies s ON 1=1 GROUP BY s.run_id,s.generation,s.status,s.data_source,r.revision'
_CACHE_LOCK = threading.Lock()


def _groups(db):
 # Preserve the read-only adapter contract. Without the full Database snapshot
 # API there is no safe revision proof: use the original uncached aggregate.
 if not all(hasattr(db, name) for name in ('q_rows', '_lock', 'conn', 'read_snapshot')):
  return db.q(_SQL)
 with revision_snapshot(db, 'population-groups-v1') as revision:
  with _CACHE_LOCK:
   hit = _CACHE.get(db)
  if revision is not None and hit is not None and hit[0] == revision:
   rows = hit[1]
  else:
   with phase('population.sql'):
    rows = tuple(db.q_rows(_SQL))
   # Publish before releasing the database gate: competing cold readers reuse
   # the same immutable grouped rows, rather than repeating the aggregate.
   if revision is not None:
    size = sum(128 * len(r) + sum(4 * len(v) for v in r if isinstance(v, (str, bytes))) for r in rows) if len(rows) <= _MAX_GROUPS else _MAX_BYTES + 1
    with _CACHE_LOCK:
     _CACHE.pop(db, None)
     if size <= _MAX_BYTES:
      _CACHE[db] = (revision, rows)
 # Scope filtering, dictionaries, hashing and accounting are outside the gate.
 return [dict(r) for r in rows]

ACTIVE_SQL="UPPER(COALESCE(data_source,''))<>'LEGACY_TEST' AND UPPER(COALESCE(status,''))<>'EXCLUDED'"
def snapshot(db,run_id=None,target=10000,include_excluded=False):
 with phase('population.reconstruction'):
  return _snapshot(db,run_id,target,include_excluded)


def _snapshot(db,run_id=None,target=10000,include_excluded=False):
 from ..evolution.engine import IN_FLIGHT_STATES,QUALIFIED_STATES,TERMINAL_STATES
 rows=_groups(db)
 registry_version=rows[0]['registry_version'] if rows else 0;groups=[r for r in rows if r['n']]
 active=[r for r in groups if str(r.get('data_source') or '').upper()!='LEGACY_TEST' and str(r.get('status') or '').upper()!='EXCLUDED']
 scope_rows=groups if include_excluded else active;scoped=[r for r in scope_rows if r['run_id']==run_id] if run_id else scope_rows;counts={}
 for r in scoped:counts[r['status']]=counts.get(r['status'],0)+r['n']
 total=sum(counts.values());generation=max((r['generation'] or 0 for r in scoped),default=0)
 alive=sum(counts.get(s,0) for s in IN_FLIGHT_STATES+QUALIFIED_STATES);dead=sum(counts.get(s,0) for s in TERMINAL_STATES)
 target=max(1,int(target));revision=hashlib.sha256(json.dumps([run_id,target,groups],sort_keys=True,default=str).encode()).hexdigest()[:24]
 return dict(schema_version=1,registry_version=registry_version,revision=revision,observed_at=time.time(),scope='current_run' if run_id else 'persisted_research',run_id=run_id,lifetime_total=sum(r['n'] for r in groups),lifetime_research=sum(r['n'] for r in active),excluded_total=sum(r['n'] for r in groups if str(r.get('data_source') or '').upper()=='LEGACY_TEST' or str(r.get('status') or '').upper()=='EXCLUDED'),legacy_total=sum(r['n'] for r in groups if str(r['data_source'] or '').upper()=='LEGACY_TEST'),orphan_run_id_total=sum(r['n'] for r in active if not r['run_id']),current_nodes=total,run_nodes=total,current_generation_nodes=sum(r['n'] for r in scoped if (r['generation'] or 0)==generation),generation_number=generation,status_counts=counts,target_nodes=target,remaining_nodes=max(0,target-total),progress_pct=round(total/target*100,2),is_target_reached=total>=target,alive_nodes=alive,dead_nodes=dead,other_nodes=total-alive-dead,backtesting_nodes=sum(counts.get(s,0) for s in ('BACKTESTING','TESTING')),validating_nodes=counts.get('VALIDATING',0),qualified_nodes=sum(counts.get(s,0) for s in QUALIFIED_STATES),pending_nodes=sum(counts.get(s,0) for s in IN_FLIGHT_STATES),all_persisted_nodes=sum(r['n'] for r in scope_rows))
