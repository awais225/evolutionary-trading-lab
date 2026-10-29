"""Bounded opt-in V10.2 diagnostics. No genomes, credentials or market rows.
Use capture() explicitly; disabled unless LAB_V102_TRACE=1. Files are exclusive
UUID outputs in system TEMP, never DATA or the source tree. Not a benchmark.
"""
from collections import defaultdict
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import uuid

_CURRENT = ContextVar('v102_performance', default=None)
_CAPTURE_LOCK = threading.Lock()
_CAPTURE_COUNT = 0
_CAPTURE_MAX = 32


class Trace:
    def __init__(self, name):
        self.name = name
        self.values = defaultdict(lambda: {'count':0,'total_seconds':0.0,'max_seconds':0.0,'samples_seconds':[]})
        self.counters = defaultdict(int)
        self.stop = threading.Event()
        self.started = time.perf_counter()
        self.peak_rss = 0
        self.cpu_samples = 0
        self.logical_cpu_sum = []
        self.process_cpu_samples = []
        self.lock = threading.Lock()
        self.sampler = threading.Thread(target=self._sample,daemon=True,name='v102-measurement')
        self.sampler.start()

    def record(self, phase, seconds):
        with self.lock:
            value = self.values[phase];value['count'] += 1;value['total_seconds'] += seconds
            value['max_seconds'] = max(value['max_seconds'],seconds)
            if len(value['samples_seconds']) < 128:value['samples_seconds'].append(seconds)

    def count(self, name, amount=1):
        with self.lock:self.counters[name] += amount

    def _sample(self):
        import psutil
        process = psutil.Process()
        psutil.cpu_percent(interval=None,percpu=True)  # prime this sampler thread
        previous = {};previous_time = time.perf_counter()
        while not self.stop.wait(.5):
            try:
                cpus=psutil.cpu_percent(interval=None,percpu=True)
                self.cpu_samples += 1
                if not self.logical_cpu_sum:self.logical_cpu_sum=[0.0]*len(cpus)
                for i,value in enumerate(cpus[:len(self.logical_cpu_sum)]):self.logical_cpu_sum[i]+=value
                children=process.children(recursive=True)
                members=[process]+children
                self.peak_rss=max(self.peak_rss,sum(p.memory_info().rss for p in members if p.is_running()))
                if len(self.process_cpu_samples)<128:
                    now=time.perf_counter()
                    totals={str(p.pid):p.cpu_times().user+p.cpu_times().system for p in members if p.is_running()}
                    self.process_cpu_samples.append({'elapsed_seconds':now-self.started,'cpu_seconds':totals,
                        'cpu_percent':{pid:100*max(0,value-previous[pid])/max(now-previous_time,1e-9) for pid,value in totals.items() if pid in previous}})
                    previous,previous_time=totals,now
            except psutil.Error:pass

    def close(self):
        self.stop.set();self.sampler.join(timeout=2)
        from . import paths as P
        parent=Path(tempfile.gettempdir()).resolve()
        if parent==P.ROOT_DIR.resolve() or P.ROOT_DIR.resolve() in parent.parents or parent==P.DATA_ROOT.resolve() or P.DATA_ROOT.resolve() in parent.parents:
            raise RuntimeError('Instrumentation TEMP overlaps source/data; no fallback')
        folder=parent/'EvolutionaryTradingLab'/'V10.2'/'traces';folder.mkdir(parents=True,exist_ok=True)
        elapsed=time.perf_counter()-self.started
        report={'name':self.name,'wall_seconds':elapsed,'phases':dict(self.values),'counters':dict(self.counters),
                'nodes_per_minute':self.counters.get('candidates.persisted',0)*60/max(elapsed,1e-9),
                'cpu_sample_count':self.cpu_samples, 'sampled_peak_process_tree_rss_bytes':self.peak_rss or None,
                'per_logical_cpu_mean_percent':[x/max(1,self.cpu_samples) for x in self.logical_cpu_sum],
                'process_cpu_seconds_samples':self.process_cpu_samples,
                'gpu_application_activity':'not measured; device telemetry is not CUDA attribution'}
        with (folder/('trace-'+uuid.uuid4().hex+'.json')).open('x',encoding='utf-8') as stream:json.dump(report,stream,indent=2)


def current():return _CURRENT.get()


@contextmanager
def capture(name):
    if os.environ.get('LAB_V102_TRACE')!='1' or current() is not None:
        yield current();return
    global _CAPTURE_COUNT
    with _CAPTURE_LOCK:
        allowed=_CAPTURE_COUNT < _CAPTURE_MAX
        if allowed:_CAPTURE_COUNT+=1
    if not allowed:
        yield None;return
    try:trace=Trace(name)
    except Exception:
        yield None;return
    token=_CURRENT.set(trace)
    try:yield trace
    finally:
        _CURRENT.reset(token)
        try:trace.close()
        except Exception:pass


@contextmanager
def _timed(trace, name):
    begun=time.perf_counter()
    try:yield
    finally:
        try:trace.record(name,time.perf_counter()-begun)
        except Exception:pass


@contextmanager
def measure(name):
    from .speed import span
    trace=current()
    with span(name, hot=True):
        with (_timed(trace,name) if trace is not None else nullcontext()):
            yield
            if name in ("candidate.random_construction","candidate.mutation","candidate.crossover"):
                from .speed import count as speed_count
                speed_count("candidates.constructed")


def count(name, amount=1):
    trace=current()
    if trace is not None:
        try:trace.count(name,amount)
        except Exception:pass
    from .speed import count as speed_count
    speed_count(name,amount)


def traced(name):
    """Capture one outer generation call, not a trace/file per candidate."""
    from functools import wraps
    def decorate(function):
        @wraps(function)
        def invoke(*args,**kwargs):
            if os.environ.get('LAB_V102_TRACE')!='1':
                from .speed import span
                with span(name, run_id=getattr(args[0] if args else None,'active_run_id',None)):
                    return function(*args,**kwargs)
            with capture(name):
                with measure(name+'.total'):return function(*args,**kwargs)
        return invoke
    return decorate
