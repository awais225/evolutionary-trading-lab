"""V10 scoped historical scratch/progress; never changes global trading settings."""
from ..speed import profiled as speed_profiled

import json
from contextvars import ContextVar
import logging
import logging.handlers
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time
import uuid

import psutil

from .. import paths as P

_LOG_LOCK = threading.RLock()
_ACTIVE = set()
CURRENT = ContextVar("deeptest_execution", default=None)
CALLBACKS = {}


def alive(pid):
    """Never send a signal: os.kill(pid, 0) terminates processes on Windows."""
    try:
        ident = int(pid or 0)
        return ident > 0 and psutil.Process(ident).status() not in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD)
    except psutil.AccessDenied:
        return True  # cannot establish death: protect another owner's work
    except (ValueError, TypeError, psutil.Error):
        return False


def logger():
    path = (P.DATA_LOGS_DIR / 'DEEPTEST.log').resolve()
    with _LOG_LOCK:
        log = logging.getLogger('deeptest.execution')
        if not any(getattr(h, 'baseFilename', None) == str(path) for h in log.handlers):
            path.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(path, maxBytes=5*1024*1024, backupCount=2, encoding='utf-8')
            handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
            log.addHandler(handler)
        log.setLevel(logging.INFO)
        return log


def exception(message, *args):
    """Keep failures visible through the central logger if dedicated storage fails."""
    try:
        logger().exception(message, *args)
    except OSError:
        logging.getLogger('deeptest').exception('DEEPTEST.log unavailable; ' + message, *args)


def temp_root():
    system_root = Path(tempfile.gettempdir()).resolve()
    path = (system_root / 'EvolutionaryTradingLab' / 'DeepTest').resolve()
    if not path.is_relative_to(system_root):
        raise ValueError('DeepTest TEMP symlink escapes system TEMP')
    # Refuse misconfigured TEMP rather than silently using the project/workspace.
    workspace = P.ROOT_DIR.parent.resolve()
    if path.is_relative_to(P.ROOT_DIR.resolve()) or (workspace.name == 'user' and path.is_relative_to(workspace)):
        raise ValueError('DeepTest system TEMP resolves inside the restricted workspace')
    path.mkdir(parents=True, exist_ok=True)
    return path


def cleanup_abandoned(root, age_seconds=7*86400):
    """Only marked, old, dead-owner scratch; never touch live/unrecognised paths."""
    for child in root.iterdir():
        if child.is_symlink() or not child.is_dir() or not re.fullmatch(r'(batch-|dt-)[a-zA-Z0-9-]+', child.name):
            continue
        try:
            marker = json.loads((child / '.deeptest-owner.json').read_text())
            if marker.get('id') != child.name or child.name in _ACTIVE or alive(marker.get('pid')):
                continue
            if time.time() - marker['created_at'] < age_seconds:
                continue
            shutil.rmtree(child)
            logger().info('job=%s phase=CLEANUP removed abandoned owned scratch', child.name)
        except (OSError, ValueError, KeyError):
            continue


class Session:
    def __init__(self, ident, progress=None):
        if not re.fullmatch(r'(batch-|dt-)[a-zA-Z0-9-]+', ident):
            raise ValueError('invalid DeepTest job identity')
        self.ident = ident
        self.progress = progress
        self.started = time.time()
        self.last_update = self.started
        self.phase = 'INITIALIZING'
        self.node = None
        self.path = None
        self._created = False
        self._ready = False
        self._stop = threading.Event()
        self.log = logger()  # failure is fatal: no silent logging/workspace fallback

    def __enter__(self):
        try:
            root = temp_root()
            cleanup_abandoned(root)
            self.path = root / (self.ident + '-' + uuid.uuid4().hex[:8])
            self.path.mkdir(exist_ok=False)
            self._created = True
            (self.path / '.deeptest-owner.json').write_text(json.dumps({'id': self.path.name, 'pid': os.getpid(), 'created_at': self.started}))
            self._ready = True
            _ACTIVE.add(self.ident)
            self.update('INITIALIZING', f'Background worker started; system TEMP={self.path}', temp_path=str(self.path), log_path=str(P.DATA_LOGS_DIR / 'DEEPTEST.log'))
            self._monitor = threading.Thread(target=self._watch, name='deep-progress-' + self.ident, daemon=True)
            self._monitor.start()
            return self
        except Exception:
            self.log.exception('job=%s phase=INITIALIZING TEMP/log setup failed; no workspace fallback', self.ident)
            self.close()
            raise

    def update(self, phase, message, node_id=None, **details):
        from ..speed import event
        fields={"state":phase,"node_id":node_id}
        for old,new in [("bars_processed","processed_bars"),("eligible_bars","eligible_bars"),("completed_trades","trades")]:
            if details.get(old) is not None:fields[new]=details[old]
        event("deeptest","phase","ERROR" if phase=="FAILED" else "WARN" if phase in ("CANCELLED","INTERRUPTED") else "INFO",run_id=self.ident,fields=fields)
        self.phase, self.node, self.last_update = phase, node_id, time.time()
        self.log.info('job=%s phase=%s node=%s elapsed=%.2fs %s', self.ident, phase, node_id, self.last_update-self.started, message)
        if self.progress:
            self.progress({'phase': phase, 'current_node': node_id, 'last_update': self.last_update, 'started_at': self.started, 'message': message, **details})

    def _watch(self):
        interval = max(1.0, float(os.environ.get('LAB_DEEPTEST_STALL_WARNING_SECONDS', '60')))
        while not self._stop.wait(interval):
            idle = time.time() - self.last_update
            if idle >= interval:
                self.log.warning('job=%s phase=%s node=%s progress unchanged %.1fs; worker process alive=%s; operation may be slow, not automatically failed', self.ident, self.phase, self.node, idle, alive(os.getpid()))

    def close(self):
        self._stop.set()
        monitor = getattr(self, '_monitor', None)
        if monitor and monitor is not threading.current_thread():
            monitor.join(timeout=1)
        try:
            if self.path and self.path.exists() and not self.path.is_symlink():
                if self._created and not self._ready:
                    # Setup failed in our newly/exclusively created UUID directory.
                    shutil.rmtree(self.path)
                    return
                marker = json.loads((self.path / '.deeptest-owner.json').read_text())
                if marker.get('id') != self.path.name or marker.get('pid') != os.getpid():
                    raise ValueError('scratch ownership changed; refusing cleanup')
                size=sum(p.stat().st_size for p in self.path.rglob('*') if not p.is_symlink() and p.is_file())
                shutil.rmtree(self.path)
                self.log.info('job=%s phase=CLEANUP temp=%s before_bytes=%s after_exists=%s elapsed=%.2fs', self.ident, self.path, size, self.path.exists(), time.time()-self.started)
        finally:
            _ACTIVE.discard(self.ident)

    def __exit__(self, typ, exc, tb):
        if exc:
            self.log.error('job=%s phase=%s node=%s worker failed', self.ident, self.phase, self.node, exc_info=(typ, exc, tb))
        self.close()


@speed_profiled("data.scratch_preparation")
def scratch_backtest(req, frame, directory, phase, *, configuration=None, cancelled=None, progress=None):
    """Same engine and feature functions; one node's caches, no shared-cache writes.

    Preserve the full source frame/window so indicator warmup and trade semantics
    are identical. Each scenario owns its feature cache and transient result state.
    """
    import numpy as np
    from ..speed import profiled as speed_profiled
    from types import SimpleNamespace
    from ..features.engine import FeatureEngine
    from ..features import library
    from ..backtest.engine import Backtester

    class ScratchFeatures(FeatureEngine):
        def _load_disk_cache(self, dataset_id, *args, **kwargs):
            self._disk_loaded.add(dataset_id)

        @speed_profiled("indicators.scratch_compute",hot=True)
        def _compute_spec(self, df, dataset_id, spec):
            fn, args = library.parse_spec(spec)
            out = {name: np.asarray(arr, dtype=np.float64) for name, arr in fn(df, *args).items()}
            for name, arr in out.items():
                self._put((dataset_id, name), arr)
            return out

    data = SimpleNamespace(get_frame=lambda ident: frame)
    features = ScratchFeatures(data_engine=data)
    engine = Backtester(data_engine=data, feature_engine=features, configuration=configuration)
    engine._cancelled, engine._progress = cancelled, progress
    # Prepare all engine-required arrays through the existing FeatureEngine.get.
    from ..genome.schema import referenced_features
    from ..backtest.engine import REGIME_FEATURES
    phase('PREPARING_FEATURES', 'Preparing strategy features')
    specs = referenced_features(req.genome) | {'close','open','high','low', (req.genome.get('exit') or {}).get('atr_spec','atr:14')} | set(REGIME_FEATURES)
    for spec in specs:
        features.get(req.dataset_id, spec)
    phase('BACKTESTING', 'Real historical engine started')
    result = engine.run(req)
    phase('BACKTESTING', 'Historical engine finished; ok=' + str(result.ok))
    return result
