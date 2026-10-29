"""V10.3 bounded, fail-open, process-local pipeline diagnostics. No research writes.
Durations are monotonic; wall time is used only for human event timestamps.
Numeric/enum allowlists deliberately exclude SQL, payloads, messages and genomes.
"""
from collections import deque
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from functools import wraps
import asyncio
import copy
import json
import math
import os
from pathlib import Path
import platform
import re
import subprocess
import tempfile
import threading
import time
import uuid

_STACK = ContextVar('speed_stack', default=())
_RUN = ContextVar('speed_run', default=None)
_SUPPRESS = ContextVar('speed_suppressed', default=False)
_NUMBERS = {'target','starting_count','persisted_count','persisted_delta','batch_size','eligible_bars','processed_bars','trades','bytes','queue_seconds','status_code','node_id','sample_ms','request_ms','render_ms','rows','worker_pid','configured_workers','effective_workers','parent_pid','dispatch_span_id','worker_cpu_seconds','compute_wall_seconds'}
_ENUMS = {'state','error_type','profile','operation','scope','dispatch_span_id'}

def safe_id(value):
    return value if isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,96}',value) and not re.search(r'(ghp_|github_pat_|sk-|Bearer|password|secret|token)',value,re.I) else None

def safe_fields(values):
    out={}
    for key,value in (values or {}).items():
        if key in _NUMBERS and isinstance(value,(float,int)) and not isinstance(value,bool) and math.isfinite(value):out[key]=value
        elif key in _ENUMS and safe_id(value):out[key]=value
    return out

def owner():
    try:task=id(asyncio.current_task())
    except RuntimeError:task=None
    return (os.getpid(),threading.get_ident(),task)

class Profiler:
    def __init__(self, profile='light', capacity=2000):
        capacity=max(100,min(5000,int(capacity)))
        profile=profile if profile in ('off','light','detailed') else 'light'
        self.lock=threading.RLock();self.started=time.perf_counter();self.wall_started=time.time()
        self.profile=profile;self.capacity=capacity;self.sample_every=20;self.interval=1.0;self.deadline=None
        self.events=deque(maxlen=capacity);self.stages={};self.counters={};self.seen={};self.workers={};self.active={}
        self.seq=0;self.span_sequence=0;self.dropped=0;self.dropped_keys=0;self.failure_count=0;self.session=uuid.uuid4().hex
        self.resources=None;self.resource_error=None;self.peak_rss=0;self.closed=False
        self.stop=threading.Event();self.sampler=None;self._runtime=None

    def enabled(self):
        if self.deadline is not None and time.perf_counter()>=self.deadline:
            self.profile='light';self.deadline=None
            self.event('profiler','detailed_expired',fields={'profile':'light'})
        return self.profile!='off' and not self.closed and not _SUPPRESS.get()

    def configure(self, profile, capacity=2000, sample_every=20, interval=1., seconds=60):
        if profile not in ('off','light','detailed') or not 100<=capacity<=5000 or not 1<=sample_every<=1000 or not .5<=interval<=10 or not 1<=seconds<=300:
            raise ValueError('profile off/light/detailed; capacity 100..5000; sample_every 1..1000; interval .5..10; detailed seconds 1..300')
        with self.lock:
            removed=max(0,len(self.events)-capacity);self.dropped+=removed
            self.events=deque(list(self.events)[-capacity:],maxlen=capacity);self.capacity=capacity
            self.profile=profile;self.sample_every=sample_every;self.interval=interval
            self.deadline=time.perf_counter()+seconds if profile=='detailed' else None
            self.event('profiler','configured',fields={'profile':profile},force=True)

    def event(self, subsystem, kind, severity='INFO', fields=None, run_id=None, force=False, **links):
        try:
            if not force and not self.enabled():return
            with self.lock:
                self.seq+=1
                if len(self.events)==self.capacity:self.dropped+=1
                self.events.append({'seq':self.seq,'timestamp':time.time(),'elapsed_seconds':max(0,time.perf_counter()-self.started),'monotonic_seconds':time.perf_counter(),
                    'subsystem':safe_id(subsystem) or 'other','type':safe_id(kind) or 'event',
                    'severity':severity if severity in ('INFO','WARN','ERROR') else 'INFO',
                    'run_id':safe_id(run_id or _RUN.get()),'pid':os.getpid(),'thread_id':threading.get_ident(),
                    'fields':safe_fields(fields),**{k:v for k,v in links.items() if k in ('span_id','parent_span_id') and (isinstance(v,int) or v is None or safe_id(v))}})
                if not links.get('_ingested'):self.count('events.'+severity)
        except Exception:self.failure_count+=1

    def count(self, name, amount=1):
        try:
            if not self.enabled() or not safe_id(name) or not isinstance(amount,(int,float)) or not math.isfinite(amount):return
            with self.lock:
                if name not in self.counters and len(self.counters)>=128:self.dropped_keys+=1;return
                self.counters[name]=self.counters.get(name,0)+amount
        except Exception:self.failure_count+=1

    @contextmanager
    def span(self, name, hot=False, run_id=None, fields=None):
        frame=None;token=None;run_token=None;begin=None
        try:
            if self.enabled() and safe_id(name):
                with self.lock:
                    n=self.seen.get(name,0)+1
                    if name in self.seen or len(self.seen)<256:self.seen[name]=n
                    if not hot or self.profile=='detailed' or n%self.sample_every==1:
                        key=owner();stack=_STACK.get();parent=stack[-1] if stack and stack[-1]['owner']==key else None
                        self.span_sequence+=1
                        begin=time.perf_counter();frame={'name':name,'owner':key,'children':0.,'parent':parent,'begin':begin,'id':f'{self.session}.{self.span_sequence}'}
                        token=_STACK.set((*stack[-63:],frame));run_token=_RUN.set(safe_id(run_id) or _RUN.get())
                        if len(self.active)<128:self.active[key]=(name,begin,_RUN.get())
        except Exception:self.failure_count+=1
        failed=None
        try:yield
        except BaseException as exc:
            failed=type(exc).__name__;raise
        finally:
            if frame is not None:
                try:
                    elapsed=max(0.,time.perf_counter()-begin)
                    with self.lock:
                        self._record(name,elapsed,max(0.,elapsed-frame['children']),failed,os.getpid(),parent is None)
                        if parent:parent['children']+=elapsed
                    if not hot or self.profile=='detailed' or elapsed>=.1 or failed:
                        self.event(name.split('.')[0],'stage','ERROR' if failed else 'INFO',
                                   {'operation':name,'sample_ms':elapsed*1000,**({'error_type':failed} if failed else {})},
                                   span_id=frame['id'],parent_span_id=parent['id'] if parent else None)
                except Exception:self.failure_count+=1
                finally:
                    try:_STACK.reset(token)
                    except Exception:self.failure_count+=1
                    try:_RUN.reset(run_token)
                    except Exception:self.failure_count+=1
                    try:
                        with self.lock:
                            if parent:self.active[frame['owner']]=(parent['name'],parent['begin'],_RUN.get())
                            else:self.active.pop(frame['owner'],None)
                    except Exception:self.failure_count+=1

    def _record(self,name,elapsed,exclusive,failed,pid,root=False):
        key=f'{pid}:{name}'
        if key not in self.stages:
            if len(self.stages)>=256:self.dropped_keys+=1;return
            self.stages[key]={'stage':name,'pid':pid,'count':0,'inclusive_seconds':0.,'self_seconds':0.,'max_seconds':0.,'failures':0,'samples':deque(maxlen=256)}
        value=self.stages[key];value['count']+=1;value['inclusive_seconds']+=elapsed;value['self_seconds']+=exclusive
        value['max_seconds']=max(value['max_seconds'],elapsed);value['failures']+=bool(failed);value['samples'].append(elapsed)
        key=f'{pid}:{threading.get_ident()}'
        if key not in self.workers and len(self.workers)<128:self.workers[key]={'pid':pid,'thread_id':threading.get_ident(),'root_tasks':0,'root_active_seconds':0.,'failures':0,'idle_seconds':None,'active_scope':'root task wall time including waits, not CPU useful-work time'}
        worker=self.workers.get(key)
        if worker and root:
            worker['root_tasks']+=1;worker['root_active_seconds']+=elapsed;worker['failures']+=bool(failed)

    def snapshot(self, events=True):
        runtime=self.runtime()
        with self.lock:
            active=self.enabled()
            now=time.perf_counter();rows=[]
            for v in self.stages.values():
                row={k:copy.deepcopy(x) for k,x in v.items() if k!='samples'};samples=sorted(v['samples'])
                row.update(percentile_sample_count=len(samples),percentile_scope='last 256 retained measured samples; child imports last 32/task; not all calls',
                    p50_seconds=samples[len(samples)//2] if len(samples)>=5 else None,
                    p95_seconds=samples[min(len(samples)-1,int(len(samples)*.95))] if len(samples)>=5 else None)
                if events:row["samples_seconds"]=list(v["samples"])
                rows.append(row)
            elapsed=max(0,now-self.started);c=dict(self.counters)
            rates={key:{'count':c.get(name,0),'per_second':c.get(name,0)/max(elapsed,1e-9),'unit':key+'/second'} for key,name in [('candidate_attempts','candidates.attempted'),('persisted_nodes','candidates.persisted'),('processed_bars','backtest.processed_bars'),('completed_trades','backtest.trades')]}
            result={'schema_version':1,'profiler_version':'V10.3','snapshot_id':uuid.uuid4().hex,'generated_at':time.time(),'session_id':self.session,
                'window':{'started_at':self.wall_started,'elapsed_seconds':elapsed,'scope':'aggregate metrics since process session start; events bounded separately'},
                'recording':{'profile':self.profile,'active':active,'detailed_remaining_seconds':max(0,self.deadline-now) if self.deadline else None,'sampling_interval_seconds':self.interval,'hot_stage_sample_every':1 if self.profile=='detailed' else self.sample_every,'retention_capacity':self.capacity},
                'runtime':runtime,'counters':c,'errors_retries_cancellations':{'error_event_count':c.get('events.ERROR',0),'warning_event_count':c.get('events.WARN',0),'retry_reseeds':c.get('candidates.retry_reseeds',0),'cancellation_scope':'See deeptest CANCELLED phase events; observations are not asserted unique runs','failed_stage_calls_scope':'Stage failures are nested observations, not unique incidents'},'throughput':{**rates,'completed_tests_per_minute':{'count':c.get('backtest.completed_tests',0),'value':60*c.get('backtest.completed_tests',0)/max(elapsed,1e-9),'unit':'tests/minute'}},'stages':rows,'workers':copy.deepcopy(self.workers),
                'active_operations':[{'stage':n,'elapsed_seconds':max(0,now-b),'run_id':r,'pid':k[0],'thread_id':k[1]} for k,(n,b,r) in self.active.items()],
                'resources':copy.deepcopy(self.resources),'resource_error':self.resource_error,'resource_availability':'MEASURED' if self.resources and not self.resource_error else 'UNAVAILABLE','peak_sampled_rss_bytes':self.peak_rss or None,
                'retention':{'retained_events':len(self.events),'dropped_events':self.dropped,'latest_seq':self.seq,'oldest_seq':self.events[0]['seq'] if self.events else None,'dropped_metric_keys':self.dropped_keys},
                'instrumentation_failures':self.failure_count,'local_stage_call_counts':dict(self.seen),
                'gaps':['Self time excludes only instrumented synchronous children; it is not CPU time. Never sum inclusive nested spans.',
                    'Different PIDs and concurrent runs overlap: do not sum process elapsed times into end-to-end wall time.',
                    'Light hot-stage samples are measured subsets, never extrapolated. Percentiles use at most last 256 calls.',
                    'OS CPU percentage is not useful-work utilization. Thread idle time, GPU kernel attribution, OS I/O wait and hardware counters unavailable.',
                    'App worker queue metrics are measured where submission envelopes exist. Missing resource values remain null.',
                    'Frontend timings cover instrumented diagnostic requests/render commits, not every browser paint or all app requests.',
                    'Aggregates cover this process session; reconfiguration/off periods are included in the wall-window rate denominator.',
                    'Only allowlisted numeric fields/enums, opaque run IDs and exception class names are retained; raw error text/SQL/payloads excluded.',
                    'Light self time excludes recorded same-thread/task children only. Unsampled hot children remain in parent self.',
                    'Exact FastAPI JSON encoding, frontend paint and chart timings unavailable; ASGI request-through-stream includes serialization and send waits.',
                    'Root logging handler timings cover handlers present at startup, not private later or non-propagating handlers.',
                    'Worker imports last 32 measured samples per stage/task and last 64 retained child events; omitted child events add to retention drops.',
                    'Idle seconds/useful-work CPU are unavailable. Root task wall includes waiting; configured capacity is never useful-worker count.',
                    'Peak RSS is observed sampled process-tree RSS sum, not unique physical memory; short-lived processes may be missed.',
                    'CSV stream wall includes consumer backpressure; cross-process dispatch IDs are causal links, not nested CPU/self timing.']}
            result['bottlenecks']=[{'classification':'MEASURED','stage':v['stage'],'pid':v['pid'],'measured_self_seconds':v['self_seconds'],'calls':v['count'],'note':'Measured elapsed self-time, not proof of CPU-bound execution; compare within PID and window.'} for v in sorted([x for x in rows if x['stage']!='api.request_through_stream'],key=lambda v:v['self_seconds'],reverse=True)[:8]]
            result['bottlenecks'].append({'classification':'UNAVAILABLE','stage':'gpu.application_compute','note':'No application GPU kernel attribution; device activity alone is insufficient.'})
            if events:result['events']=copy.deepcopy(list(self.events))
            for r in rows:
                if ('lock_wait' in r['stage'] or 'queue_delay' in r['stage']) and r['count']>=5 and (r['p50_seconds'] or 0)>.01:
                    result['bottlenecks'].append({'classification':'SUSPECTED','stage':r['stage'],'pid':r['pid'],'note':'Measured median wait exceeds 10ms over at least five samples. Investigate contention/scheduling; this is not proof of cause.'})
            return result

    def runtime(self):
        if self._runtime is None:
            sha=os.environ.get('LAB_COMMIT_SHA','')
            if not re.fullmatch('[0-9a-f]{40}',sha):
                try:sha=subprocess.check_output(['git','-C',str(Path(__file__).resolve().parents[2]),'rev-parse','HEAD'],stderr=subprocess.DEVNULL,timeout=1,text=True).strip()
                except Exception:sha=None
            from .versions import APP_VERSION
            self._runtime={'application_version':APP_VERSION,'commit_sha':sha,'os':platform.system(),'os_release':platform.release(),'machine':platform.machine(),'python':platform.python_version(),'pid':os.getpid()}
        return dict(self._runtime)

    def start_sampler(self):
        with self.lock:
            if self.sampler and self.sampler.is_alive():return
            if self.closed:return
            self.sampler=threading.Thread(target=self._sample,name='speed-sampler',daemon=True);self.sampler.start()

    def _sample(self):
        previous={};last=time.perf_counter()
        try:
            import psutil
            psutil.cpu_percent(interval=None,percpu=True)
        except Exception:self.resource_error='UNAVAILABLE: psutil';return
        while not self.stop.wait(self.interval):
            if not self.enabled():continue
            token=_SUPPRESS.set(True)
            try:
                now=time.perf_counter();members=[psutil.Process()];members+=members[0].children(recursive=True);processes=[];rss=0
                for proc in members[:128]:
                    try:
                        t=proc.cpu_times();total=t.user+t.system;identity=(proc.pid,proc.create_time());mem=proc.memory_info().rss;rss+=mem
                        percent=100*max(0,total-previous[identity])/max(now-last,1e-9) if identity in previous else None
                        previous[identity]=total;processes.append({'pid':proc.pid,'cpu_percent_one_core_100':percent,'cpu_seconds':total,'rss_bytes':mem})
                    except psutil.Error:pass
                delta_interval=now-last;previous={k:v for k,v in previous.items() if k[0] in {x['pid'] for x in processes}};last=now
                vm=psutil.virtual_memory()
                from .resources.manager import get_resource_manager
                metrics=get_resource_manager().live_metrics()
                with self.lock:
                    self.resources={'sampled_at':time.time(),'per_core_cpu_percent':psutil.cpu_percent(interval=None,percpu=True),'processes':processes,'observed_process_count':len(processes),'process_count_truncated':len(members)>128,
                        'rss_bytes':rss,'system_memory_percent':vm.percent,'available_memory_bytes':vm.available,
                        'gpu':metrics.get('gpu'),'workers':metrics.get('workers'),'deep_test':metrics.get('deep_test'),'jobs':metrics.get('jobs'),
                        'cpu_delta_interval_seconds':delta_interval,'saturation':{'memory_pressure':vm.percent>=85,'cpu_scope':'system per-core; application tree CPU separately sampled'}}
                    self.peak_rss=max(self.peak_rss,rss);self.resource_error=None
            except Exception as exc:self.resource_error='UNAVAILABLE: '+type(exc).__name__
            finally:_SUPPRESS.reset(token)

    def ingest(self, data):
        try:
            if not self.enabled():return
            with self.lock:
                self.dropped+=int(data.get('retention',{}).get('dropped_events',0))+max(0,len(data.get('events',[]))-64)
                for row in data.get('stages',[])[:256]:
                    name=row.get('stage');pid=row.get('pid');key=f'{pid}:{name}'
                    if not safe_id(name) or not isinstance(pid,int):continue
                    if key not in self.stages and len(self.stages)>=256:self.dropped_keys+=1;continue
                    target=self.stages.setdefault(key,{'stage':name,'pid':pid,'count':0,'inclusive_seconds':0.,'self_seconds':0.,'max_seconds':0.,'failures':0,'samples':deque(maxlen=256)})
                    for k in ('count','inclusive_seconds','self_seconds','failures'):
                        v=row.get(k,0)
                        if isinstance(v,(int,float)) and math.isfinite(v) and v>=0:target[k]+=v
                    target['max_seconds']=max(target['max_seconds'],row.get('max_seconds',0))
                for row in data.get('stages',[])[:256]:
                    target=self.stages.get(f"{row.get('pid')}:{row.get('stage')}")
                    if target:
                        for sample in row.get('sample_values',[])[:32]:
                            if isinstance(sample,(int,float)) and math.isfinite(sample) and sample>=0:target['samples'].append(sample)
                for k,v in data.get('workers',{}).items():
                    if k not in self.workers and len(self.workers)<128:
                        self.workers[k]={key:value for key,value in v.items() if key in ('pid','thread_id','root_tasks','root_active_seconds','failures')}
                        self.workers[k].update(idle_seconds=None,active_scope='root task wall including waits, not CPU useful-work time')
                    elif k in self.workers:
                        for name in ('root_tasks','root_active_seconds','failures'):self.workers[k][name]+=v.get(name,0)
                for k,v in data.get('counters',{}).items():self.count(k,v)
                for e in data.get('events',[])[-64:]:
                    self.event(e.get('subsystem'),e.get('type'),e.get('severity'),e.get('fields'),e.get('run_id'),span_id=e.get('span_id'),parent_span_id=e.get('parent_span_id'),_ingested=True)
                    if self.events and isinstance(e.get('pid'),int):self.events[-1]['pid']=e['pid']
                    if self.events and isinstance(e.get('thread_id'),int):self.events[-1]['thread_id']=e['thread_id']
                    if self.events and isinstance(e.get('timestamp'),(int,float)):self.events[-1]['timestamp']=e['timestamp']
                    if self.events and isinstance(e.get('monotonic_seconds'),(int,float)):
                        self.events[-1]['monotonic_seconds']=e['monotonic_seconds'];self.events[-1]['elapsed_seconds']=max(0,e['monotonic_seconds']-self.started)
        except Exception:self.failure_count+=1

    def shutdown(self, persist=True):
        self.event('profiler','shutdown',force=True);self.stop.set();self.closed=True
        if self.sampler and self.sampler.is_alive() and self.sampler is not threading.current_thread():self.sampler.join(timeout=3)
        try:
            if persist:
                from . import paths as P
                folder=(Path(tempfile.gettempdir())/'EvolutionaryTradingLab'/'V10.3'/'speed').resolve()
                if folder in (P.DATA_ROOT.resolve(),P.ROOT_DIR.resolve()) or P.DATA_ROOT.resolve() in folder.parents or P.ROOT_DIR.resolve() in folder.parents:raise ValueError('Unsafe diagnostic TEMP')
                folder.mkdir(parents=True,exist_ok=True)
                dest=folder/('speed-'+self.session+'.json');dest.write_text(json.dumps(self.snapshot()),encoding='utf-8')
                for old in sorted([f for f in folder.glob('speed-*.json') if re.fullmatch(r'speed-[a-f0-9]{32}\.json',f.name)],key=lambda f:f.stat().st_mtime,reverse=True)[3:]:
                    if old.is_file() and not old.is_symlink():old.unlink()
        except Exception:self.failure_count+=1
        self.closed=True
        uninstall_logging()

PROFILER=Profiler(profile='off' if os.environ.get('LAB_SPEED_PROFILE')=='off' else 'light')

@contextmanager
def span(name, **kwargs):
    """Fail-open even if profiler entry/exit fails. Never swallow business errors."""
    manager=None;entered=False
    try:
        manager=PROFILER.span(name,**kwargs);manager.__enter__();entered=True
    except Exception:pass
    try:yield
    except BaseException as exc:
        if entered:
            try:manager.__exit__(type(exc),exc,exc.__traceback__)
            except BaseException:pass
        raise
    else:
        if entered:
            try:manager.__exit__(None,None,None)
            except Exception:pass

def event(*args,**kwargs):
    try:PROFILER.event(*args,**kwargs)
    except Exception:pass

def count(name,amount=1):
    try:PROFILER.count(name,amount)
    except Exception:pass

def profiled(name, hot=False, run_arg=None):
    def decorate(fn):
        @wraps(fn)
        def call(*args,**kwargs):
            try:rid=args[run_arg] if run_arg is not None and len(args)>run_arg else getattr(args[0] if args else None,'active_run_id',None)
            except Exception:rid=None
            with span(name,hot=hot,run_id=safe_id(rid)):
                return fn(*args,**kwargs)
        return call
    return decorate

def dispatch(payload, run_id=None):
    try:
        return {**payload,'_speed_envelope':{'parent_pid':os.getpid(),'submitted':time.perf_counter(),'run_id':safe_id(run_id),'profile':PROFILER.profile,'sample_every':PROFILER.sample_every,'dispatch_span_id':_STACK.get()[-1]['id'] if _STACK.get() else None}}
    except Exception:pass
    return payload

def worker_task(fn):
    @wraps(fn)
    def call(payload):
        global PROFILER
        envelope=payload.get('_speed_envelope')
        if not envelope:return fn(payload)
        cleaned={k:v for k,v in payload.items() if k!='_speed_envelope'}
        remote=envelope.get('parent_pid')!=os.getpid();previous=PROFILER
        try:
            if remote:
                try:
                    PROFILER=Profiler(profile=envelope.get('profile','light'),capacity=100)
                    PROFILER.sample_every=envelope.get('sample_every',20)
                except Exception:pass
            with span('worker.task',run_id=envelope.get('run_id')):
                duration('worker.queue_delay',max(0,time.perf_counter()-envelope['submitted']))
                event('worker','started',fields={'queue_seconds':max(0,time.perf_counter()-envelope['submitted']),'worker_pid':os.getpid(),'parent_pid':envelope.get('parent_pid'),'dispatch_span_id':envelope.get('dispatch_span_id')})
                result=fn(cleaned)
                if not result.get('ok'):
                    event('worker','failed','ERROR');count('worker.result_failures')
            if remote:
                try:
                    if not PROFILER.enabled():return result
                    report=PROFILER.snapshot()
                    for row in report['stages']:
                        row.pop("samples_seconds",None)
                        row['sample_values']=list(PROFILER.stages[f"{row['pid']}:{row['stage']}"]['samples'])[-32:]
                    result={**result,'_speed_diagnostics':report}
                except Exception:pass
            return result
        finally:
            if remote:PROFILER=previous
    return call

def collect_result(result):
    if isinstance(result,dict):
        data=result.pop('_speed_diagnostics',None)
        if data:
            try:PROFILER.ingest(data)
            except Exception:pass
    return result

_WORKER_REPORT_OK=False
def worker_config():
    try:return {'profile':PROFILER.profile,'sample_every':PROFILER.sample_every,'run_id':_RUN.get(),'parent_pid':os.getpid(),'dispatch_span_id':_STACK.get()[-1]['id'] if _STACK.get() else None}
    except Exception:return {'profile':'off','sample_every':20}
def prepare_worker(config):
    global PROFILER,_WORKER_REPORT_OK
    _WORKER_REPORT_OK=False
    try:
        PROFILER=Profiler(profile=(config or {}).get('profile','light'),capacity=100)
        PROFILER.sample_every=(config or {}).get('sample_every',20)
        _RUN.set(safe_id((config or {}).get('run_id')));_STACK.set(())
        _WORKER_REPORT_OK=True
    except Exception:pass
def worker_report():
    try:
        if not _WORKER_REPORT_OK or not PROFILER.enabled():return None
        report=PROFILER.snapshot()
        with PROFILER.lock:
            for row in report['stages']:
                stored=PROFILER.stages.get(f"{row['pid']}:{row['stage']}")
                row.pop("samples_seconds",None)
                row['sample_values']=list(stored['samples'])[-32:] if stored else []
        return report
    except Exception:return None

def duration(name,seconds,pid=None):
    try:
        if PROFILER.enabled() and safe_id(name) and isinstance(seconds,(float,int)) and math.isfinite(seconds) and seconds>=0:
            with PROFILER.lock:PROFILER._record(name,seconds,seconds,None,pid or os.getpid())
    except Exception:pass

def ingest_worker(data):
    try:PROFILER.ingest(data or {})
    except Exception:pass

_LOG_WRAPPERS=[]
_WARNING_HANDLER=None
def install_logging():
    global _WARNING_HANDLER
    try:
        import logging
        if _WARNING_HANDLER is not None:return
        class Warnings(logging.Handler):
            def emit(self,record):
                event('logging','error' if record.levelno>=logging.ERROR else 'warning','ERROR' if record.levelno>=logging.ERROR else 'WARN')
                count('logging.errors' if record.levelno>=logging.ERROR else 'logging.warnings')
        root=logging.getLogger()
        for handler in root.handlers:
            original=handler.emit
            def measured(record,original=original):
                with span('logging.handler',hot=True):return original(record)
            handler.emit=measured;_LOG_WRAPPERS.append((handler,original,measured))
        _WARNING_HANDLER=Warnings(level=logging.WARNING);root.addHandler(_WARNING_HANDLER)
    except Exception:pass

def uninstall_logging():
    global _WARNING_HANDLER
    try:
        import logging
        if _WARNING_HANDLER is not None:logging.getLogger().removeHandler(_WARNING_HANDLER)
        for handler,original,wrapper in _LOG_WRAPPERS:
            if handler.emit is wrapper:handler.emit=original
        _LOG_WRAPPERS.clear();_WARNING_HANDLER=None
    except Exception:pass
