"""V10.2 opt-in independent historical computation; no DB/broker in children.
Parent owns claims, status, cancellation and durable result writes. Spawn (not
fork) avoids inherited SQLite handles. Serial remains the production default.
"""
import atexit
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, TimeoutError
from contextlib import contextmanager
import copy
import json
import multiprocessing
import os
from pathlib import Path
import pickle
import threading
import time

_LOCK = threading.RLock()
_PROCESS_POOL = None
_POOL_SIZE = None
_RESERVED_BYTES = 0


def worker_limit():
    from ..config import get_config
    from ..resources.manager import get_resource_manager
    cfg = get_config()
    requested = getattr(cfg.resources, 'deep_test_workers', 1)
    if isinstance(requested, bool) or int(requested) != requested or not 1 <= int(requested) <= 64:
        raise ValueError('deep_test_workers must be an integer 1..64 (default 1)')
    if int(requested) == 1:return 1
    logical = getattr(os, 'process_cpu_count', os.cpu_count)() or 1
    return max(1, min(int(requested), logical, get_resource_manager().effective_workers()))


def telemetry_snapshot():
    """Read-only local capacity/admission snapshot; never starts a worker pool.

    An admitted job can be queued, preparing data, computing or persisting.
    It is deliberately NOT reported as an observed busy CPU process.
    """
    import sys
    from ..config import get_config
    configured = get_config().resources.deep_test_workers
    try:
        effective, error = worker_limit(), None
    except (ValueError, TypeError, OverflowError) as exc:
        effective, error = None, str(exc)
    module = sys.modules.get(__package__ + '.scenarios')
    controller = getattr(module, '_executor', None)
    width, admitted = None, 0
    if controller is not None:
        with controller.lock:
            width, admitted = controller.width, controller.pending
    with _LOCK:
        reserved = _RESERVED_BYTES
    capacity = width if width is not None else effective
    return {'configured_processes': configured, 'effective_process_limit': effective,
            'controller_pool_width': width, 'admitted_jobs': admitted,
            'admission_capacity': capacity * 2 if capacity is not None else None,
            'observed_active_processes': None, 'reserved_memory_bytes': reserved,
            'scope': 'local backend controller; admitted includes queued jobs', 'error': error}


class ScenarioExecutor:
    """One reusable bounded controller pool, never created by status polling.
    Changing concurrency while admitted work exists is refused, not raced.
    """
    def __init__(self):
        self.pool = None
        self.width = None
        self.pending = 0
        self.closing = False
        self.lock = threading.RLock()

    def submit(self, function, *args):
        width = worker_limit()
        with self.lock:
            if self.closing:raise RuntimeError('DeepTest backend is shutting down; no new work admitted')
            if self.width != width:
                if self.pending:
                    raise RuntimeError('DeepTest concurrency changed with active jobs; await completion')
                if self.pool is not None:self.pool.shutdown(wait=True)
                self.pool = ThreadPoolExecutor(max_workers=width, thread_name_prefix='deeptest')
                self.width = width
            if self.pending >= width * 2:
                raise RuntimeError('DeepTest queue full; bounded to twice active worker capacity')
            self.pending += 1
            try:future = self.pool.submit(function, *args)
            except BaseException:
                self.pending -= 1
                raise
            def released(_):
                with self.lock:self.pending -= 1
            future.add_done_callback(released)
            return future

    def begin_shutdown(self):
        with self.lock:self.closing=True

    def shutdown(self):
        with self.lock:
            pool, self.pool = self.pool, None
            self.width = None
        if pool is not None:pool.shutdown(wait=True, cancel_futures=True)


@contextmanager
def reserve_memory(frame=None, *, source_rows=None):
    import psutil
    from ..config import get_config
    global _RESERVED_BYTES
    cfg = get_config().resources
    # Explicit conservative admission estimate, not a guarantee against native
    # allocation failure. Parent frame + worker frame/features/results included.
    estimate = max(256 * 1024**2, int(source_rows or 0)*512 if frame is None else int(frame.memory_usage(deep=True).sum())*16)
    vm = psutil.virtual_memory()
    capacity = vm.available
    if cfg.memory_limit_enabled:
        allowed = (vm.total * float(cfg.memory_limit_value) / 100 if cfg.memory_limit_mode == 'pct'
                   else float(cfg.memory_limit_value) * 1024**3)
        capacity = min(capacity, max(0, allowed - psutil.Process().memory_info().rss))
    with _LOCK:
        if estimate + _RESERVED_BYTES > capacity:
            raise MemoryError('DeepTest memory admission refused; reduce workers or requested history')
        _RESERVED_BYTES += estimate
    try:yield estimate
    finally:
        with _LOCK:_RESERVED_BYTES -= estimate


def _worker(req, source_path, result_path, cancel_path, progress_path, backtest_cfg, risk_cfg, submitted):
    """Small request + immutable IPC file; never pickle a full market frame."""
    from .runtime import scratch_backtest
    from ..config import LabConfig
    import pyarrow.feather as feather
    import psutil
    cfg = LabConfig()
    cfg.backtest, cfg.risk = backtest_cfg, risk_cfg
    begun = time.perf_counter()
    cpu_started = time.process_time()
    frame = feather.read_feather(source_path, memory_map=True, use_threads=False)
    def update(processed, eligible, trades):
        temporary = Path(str(progress_path) + '.tmp')
        temporary.write_text(json.dumps(dict(bars_processed=processed, eligible_bars=eligible,
                                            completed_trades=trades)), encoding='utf-8')
        temporary.replace(progress_path)
    result = scratch_backtest(req, frame, Path(source_path).parent, lambda *args:None,
                              configuration=cfg, cancelled=lambda:Path(cancel_path).exists(), progress=update)
    # Trusted owned scratch only; complete result stays on disk until the parent
    # consumes it. Large trade lists are not copied through the executor pipe.
    with Path(result_path).open('xb') as stream:pickle.dump(result, stream, protocol=5)
    return dict(worker_pid=os.getpid(), queue_wait_seconds=begun-submitted,
                compute_wall_seconds=time.perf_counter()-begun,
                worker_cpu_seconds=time.process_time()-cpu_started,
                worker_rss_bytes=psutil.Process().memory_info().rss,
                bars=len(frame), trades=len(result.trades))


def execute(req, frame, session, cancelled, phase):
    global _PROCESS_POOL, _POOL_SIZE
    import pyarrow.feather as feather
    from ..config import get_config
    from ..backtest.symbol_specs import execution_params_for
    width = worker_limit()
    if width <= 1:raise RuntimeError('Process path requires explicit deep_test_workers > 1')
    cfg = get_config()
    req = copy.copy(req)
    # Resolve broker facts exactly once in parent; a spawned worker must not
    # initialize a terminal or silently substitute its own configured defaults.
    req.execution_snapshot = copy.deepcopy(execution_params_for(req.genome.get('symbol') or '', cfg))
    source = session.path/'market.arrow'
    result = session.path/'complete-result.pickle'
    cancel = session.path/'cancel-requested'
    progress = session.path/'worker-progress.json'
    with reserve_memory(frame) as estimated:
        if not source.exists():feather.write_feather(frame, source, compression='uncompressed')
        with _LOCK:
            if _PROCESS_POOL is not None and _POOL_SIZE != width:
                raise RuntimeError('Process concurrency changed; restart idle backend to resize safely')
            if _PROCESS_POOL is None:
                _PROCESS_POOL = ProcessPoolExecutor(max_workers=width, mp_context=multiprocessing.get_context('spawn'))
                _POOL_SIZE = width
            future = _PROCESS_POOL.submit(_worker, req, str(source), str(result), str(cancel), str(progress),
                                          copy.deepcopy(cfg.backtest), copy.deepcopy(cfg.risk), time.perf_counter())
        latest = None
        while True:
            try:
                details = future.result(timeout=0.2)
                from ..performance import current
                observer = current()
                if observer is not None:
                    observer.record('worker.queue_wait',details['queue_wait_seconds'])
                    observer.record('worker.compute',details['compute_wall_seconds'])
                    observer.count('backtest.bars',details['bars'])
                    observer.count('backtest.trades',details['trades'])
                break
            except TimeoutError:
                if future.done():raise
                if cancelled() and not cancel.exists():cancel.touch(exist_ok=False)
                if progress.exists():
                    content = progress.read_text(encoding='utf-8')
                    if content != latest:
                        latest = content
                        state = json.loads(content)
                        session.update('BACKTESTING', f"Processing history: {state.get('bars_processed',0)}/{state.get('eligible_bars',0)} bars; {state.get('completed_trades',0)} closed trades", **state)
        # Never label cancellation as success, even if it arrived after compute.
        if cancelled():
            from ..backtest.engine import BacktestResult
            return BacktestResult(ok=False, error='cancelled by operator')
        phase('AGGREGATING_RESULTS', 'Loading complete owned worker result')
        from ..performance import measure
        with measure('worker.result_aggregation'):
            with result.open('rb') as stream:out = pickle.load(stream)
        out.metrics['worker_execution'] = {**details, 'estimated_memory_bytes':estimated}
        return out


def shutdown():
    global _PROCESS_POOL, _POOL_SIZE
    with _LOCK:
        pool, _PROCESS_POOL = _PROCESS_POOL, None
        _POOL_SIZE = None
    if pool is not None:pool.shutdown(wait=True, cancel_futures=True)


atexit.register(shutdown)
