"""Reconcile historical authority before publishing the first usable population.
Reuses the established restoration algorithm, never changes its thresholds.
A durable directory stamp makes unchanged subsequent startups metadata-only.
"""
import json,threading,time
from .. import paths as P
_LOCK=threading.RLock()

def _stamp():
    dirs=[P.DATA_ROOT/'nodes',P.DATA_ROOT/'strategies',P.DATA_ROOT/'research/nodes',P.RESEARCH_DIR/'strategies',P.RESEARCH_DIR/'backtests',P.RESEARCH_DIR/'validations']
    return json.dumps([(str(p),p.stat().st_mtime_ns if p.exists() else None) for p in dirs])

def ensure_ready(db):
    from ..data.restoration import get_restoration_engine
    with _LOCK:
        engine=get_restoration_engine();stamp=_stamp();started=time.perf_counter()
        if db.get_meta('v92_registry_stamp')==stamp:
            engine._restored=True
            return {'ready':True,'mode':'persisted_registry','seconds':time.perf_counter()-started}
        result=engine.restore_all(emit_logs=False)
        if not result.get('ok'):raise RuntimeError('historical registry reconciliation failed; refusing partial population')
        db.set_meta('v92_registry_stamp',_stamp())
        return {'ready':True,'mode':'reconciled_from_actual_history','seconds':time.perf_counter()-started}
