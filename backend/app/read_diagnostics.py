"""Opt-in, bounded read-path timings. No SQL, parameters, payloads or files.

Use capture() in tests/tools, or LAB_V1052_READ_TRACE=1 and snapshot() for a
process-local aggregate. Disabled by default. Nested hold spans are inclusive:
db.snapshot_hold includes nested db.lock_hold; do not sum these as exclusive CPU.
"""
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import os
import threading
import time

_CURRENT = ContextVar('v1052_read_trace', default=None)

class Timings:
    MAX_PHASES = 64
    MAX_SAMPLES = 128

    def __init__(self):
        self.lock = threading.Lock()
        self.values = {}

    def record(self, name, elapsed):
        with self.lock:
            if name not in self.values:
                if len(self.values) >= self.MAX_PHASES:
                    return
                self.values[name] = [0, 0., 0., deque(maxlen=self.MAX_SAMPLES)]
            v = self.values[name]
            v[0] += 1; v[1] += elapsed; v[2] = max(v[2], elapsed); v[3].append(elapsed)

    def snapshot(self):
        with self.lock:
            return {k: dict(count=v[0], total_seconds=v[1], max_seconds=v[2],
                            samples_seconds=list(v[3])) for k, v in self.values.items()}

_GLOBAL = Timings()

def snapshot():
    return _GLOBAL.snapshot()

@contextmanager
def capture(timings=None):
    trace = timings if timings is not None else Timings()
    token = _CURRENT.set(trace)
    try:
        yield trace
    finally:
        _CURRENT.reset(token)

@contextmanager
def phase(name):
    trace = _CURRENT.get()
    if trace is None and os.environ.get('LAB_V1052_READ_TRACE') == '1':
        trace = _GLOBAL
    if trace is None:
        yield
        return
    start = time.perf_counter()
    try:
        yield
    finally:
        try:trace.record(name, time.perf_counter() - start)
        except Exception:pass

def timed(name):
    def decorate(fn):
        @wraps(fn)
        def invoke(*a, **kw):
            with phase(name):
                return fn(*a, **kw)
        # Resolve postponed annotations against the original route's globals,
        # not this wrapper module's globals (FastAPI inspects these annotations).
        import inspect
        invoke.__signature__ = inspect.signature(fn, eval_str=True)
        return invoke
    return decorate
