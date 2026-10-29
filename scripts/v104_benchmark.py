"""Reproducible SYNTHETIC real-engine benchmark. NEVER use authoritative DATA.
Example: python scripts/v104_benchmark.py --data-root <fresh TEMP>/data --output <TEMP>/result.json
Run this SAME script with --source pointing at before/after worktrees. No broker,
live trading, production config writes, GPU dependencies, or CPU-target changes.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import ctypes
import ctypes.util
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace


def gpu_probe():
    info={'device_monitoring':'UNAVAILABLE','cuda_driver':'UNAVAILABLE','application_gpu_execution':'NOT IMPLEMENTED/UNVERIFIED','fallback':'existing CPU engine'}
    try:
        probe=subprocess.run(['nvidia-smi','--query-gpu=name,driver_version,memory.total,memory.used,utilization.gpu','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=10)
        if probe.returncode==0:info.update(device_monitoring='MEASURED device only, not app compute',device_csv=probe.stdout.strip())
    except (OSError,subprocess.TimeoutExpired):pass
    try:
        driver=ctypes.WinDLL('nvcuda.dll') if os.name=='nt' else ctypes.CDLL(ctypes.util.find_library('cuda') or 'libcuda.so.1')
        info['cuda_driver']='AVAILABLE (cuInit succeeded)' if driver.cuInit(0)==0 else 'UNAVAILABLE (cuInit failed)'
    except (OSError,AttributeError):pass
    return info


class Monitor:
    def __enter__(self):
        import psutil
        self.stop=threading.Event();self.peak_rss=0;self.cpu={};self.system_memory_percent=0
        def sample():
            p=psutil.Process()
            while not self.stop.is_set():
                total=0
                for proc in [p]+p.children(recursive=True):
                    try:
                        c=proc.cpu_times();key=str(proc.pid)+':'+str(proc.create_time());value=c.user+c.system
                        first,last=self.cpu.get(key,(value,value));self.cpu[key]=(first,value)
                        total+=proc.memory_info().rss
                    except psutil.Error:pass
                self.peak_rss=max(self.peak_rss,total);self.system_memory_percent=max(self.system_memory_percent,psutil.virtual_memory().percent)
                self.stop.wait(.02)
        self.thread=threading.Thread(target=sample,daemon=True);self.thread.start();return self
    def __exit__(self,*args):self.stop.set();self.thread.join()
    def report(self):return {'peak_observed_tree_rss_bytes':self.peak_rss,'system_memory_percent_peak':self.system_memory_percent,'sampled_cpu_seconds_by_pid':{k:max(0,b-a) for k,(a,b) in self.cpu.items()},'scope':'20ms benchmark sampler; first/short-lived process intervals may be missed, RSS includes shared pages'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--data-root',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--workers',type=int,nargs='+',default=[1,2,4]);parser.add_argument('--rounds',type=int,default=3)
    parser.add_argument('--bars',type=int,default=12000);parser.add_argument('--nodes',type=int,default=100)
    parser.add_argument('--generation-only',action='store_true',help='Measure births only, without DeepTest batches; useful for repeated short-run validation')
    parser.add_argument('--profile',choices=['off','light','detailed'],default='light')
    a=parser.parse_args();source=a.source.resolve();data=a.data_root.resolve();output=a.output.resolve()
    if data.exists() or data.is_relative_to(source) or output.is_relative_to(source) or output.is_relative_to(data):parser.error('Use a NONEXISTENT external isolated data-root and external output; never existing DATA')
    if not 1<=a.nodes<=100 or not 1<=a.rounds<=10 or not 100<=a.bars<=250000 or any(n not in (1,2,4,8) for n in a.workers):parser.error('nodes 1..100, rounds 1..10, bars 100..250000, workers 1/2/4/8')
    data.mkdir(parents=True);output.parent.mkdir(parents=True,exist_ok=True)
    os.environ['EVOLUTIONARY_LAB_DATA_ROOT']=str(data)
    sys.path[:0]=[str(source/'backend'),str(source/'tests')]
    from app.db.database import Database
    from app.evolution.engine import EvolutionEngine
    from app.deeptest import store as S,scenarios as SC,parallel
    from app.config import get_config
    from app import speed
    from test_v10_2_processing import bars,request,actual,metrics_without_observation
    speed.PROFILER.configure(a.profile,seconds=300)
    cfg=get_config();cfg.mt5.mode='simulator'
    cfg.mt5.login=0;cfg.mt5.password='';cfg.mt5.server='';cfg.mt5.path=''
    original_resources=vars(cfg.resources).copy()
    report={'fixture':'SYNTHETIC deterministic regression bars/genomes; NOT real market performance','environment':{'os':platform.platform(),'python':platform.python_version(),'logical_cpus':os.cpu_count(),'gpu':gpu_probe()},'settings':{'bars':a.bars,'nodes_per_round':a.nodes,'rounds':a.rounds,'speed_profile':a.profile,'generation_only':a.generation_only,'resources':original_resources,'broker_policy':'explicit simulator/table specs; no MT5 connection or orders'},'generation':[],'deeptest':[]}
    try:report['source_sha']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,text=True).strip()
    except (OSError,subprocess.CalledProcessError):report['source_sha']=None
    report['source_worktree_dirty']=bool(subprocess.check_output(['git','status','--porcelain'],cwd=source,text=True))
    report['source_fingerprint']=hashlib.sha256(b''.join(str(p.relative_to(source)).encode()+p.read_bytes() for p in sorted((source/'backend/app').rglob('*.py')))).hexdigest()
    digest=lambda value:hashlib.sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()
    for i in range(a.rounds):
        db=Database(str(data/f'generation-{i}.db'));engine=EvolutionEngine(db,run_id='ISOLATED-BENCH')
        engine.set_total_node_target(20000);engine._apply_timeframe_scope=lambda symbol:['M1']
        calls=Counter();db.conn.set_trace_callback(lambda sql:calls.update([sql.split()[0].upper()]))
        before_counters=dict(speed.PROFILER.snapshot(events=False)['counters'])
        cpu=time.process_time()
        with Monitor() as monitor:
            begin=time.perf_counter();count=engine.seed_population(a.nodes,'EURUSD');elapsed=time.perf_counter()-begin
        records=db.q('SELECT id,hash,parent_id,generation,genome,research_node_num FROM strategies ORDER BY id')
        counter= speed.PROFILER.snapshot(events=False)['counters']
        attempts=None if a.profile=='off' else counter.get('candidates.attempted',0)-before_counters.get('candidates.attempted',0)
        report['generation'].append({'nodes':count,'seconds':elapsed,'nodes_per_minute':60*count/elapsed,'candidate_attempts':attempts,'candidates_per_second':attempts/elapsed if attempts is not None else None,'rejections':None if a.profile=='off' else speed.PROFILER.snapshot(events=False)['counters'].get('candidates.invalid',0)-before_counters.get('candidates.invalid',0),'duplicates':None if a.profile=='off' else speed.PROFILER.snapshot(events=False)['counters'].get('candidates.duplicates',0)-before_counters.get('candidates.duplicates',0),'process_cpu_seconds':time.process_time()-cpu,'sql':dict(calls),'record_digest':digest(records),'rng_digest':digest(repr(engine._rng.getstate())),'resources':monitor.report()});db.close()
    frame=bars(a.bars);req=request(frame,max_trades=None);truth=None
    for requested in ([] if a.generation_only else a.workers):
        cfg.resources.deep_test_workers=requested;effective=parallel.worker_limit()
        db=Database(str(data/f'scenarios-{requested}.db'));parallel.shutdown()
        for repeat in range(a.rounds):
            def task(i):
                directory=data/f'job-{requested}-{repeat}-{i}';directory.mkdir()
                t=time.perf_counter()
                if effective==1:res=actual(req,frame,directory)
                else:res=parallel.execute(req,frame,SimpleNamespace(path=directory,update=lambda *a,**k:None),lambda:False,lambda *a:None)
                compute=time.perf_counter()-t
                if not res.ok:raise RuntimeError('Benchmark calculation failed: '+str(res.error))
                identity=digest(res.trades);metrics=digest(metrics_without_observation(res.metrics))
                normalized=SC._normalize_trades(res.trades,{})
                ident=f'isolated-{requested}-{repeat}-{i}';S.insert_scenario(db,dict(run_id=ident,node_id=1,status='RUNNING',metrics=res.metrics))
                t=time.perf_counter();S.insert_trades(db,ident,normalized);persist=time.perf_counter()-t
                S.update_scenario(db,ident,status='COMPLETED')
                total=S.list_trades(db,ident,limit=1)['total'];assert total==len(normalized)
                if total>3000:assert S.list_trades(db,ident,offset=3000,limit=1)['trades'][0]['trade_no']==3001
                return {'compute_seconds':compute,'persistence_seconds':persist,'trades':total,'trade_digest':identity,'metrics_digest':metrics,'worker':res.metrics.get('worker_execution')}
            with Monitor() as monitor:
                begin=time.perf_counter()
                with ThreadPoolExecutor(max_workers=effective) as pool:tasks=list(pool.map(task,range(4)))
                elapsed=time.perf_counter()-begin
            for job in tasks:
                if truth is None:truth=(job['trade_digest'],job['metrics_digest'])
                assert truth==(job['trade_digest'],job['metrics_digest']),'CPU worker modes changed trades/metrics'
            report['deeptest'].append({'requested_workers':requested,'effective_workers':effective,'round':repeat,'cold_pool':repeat==0,'scenarios':4,'total_seconds':elapsed,'scenarios_per_minute':240/elapsed,'bars_per_second':4*len(frame)/elapsed,'persisted_trades_per_second':sum(t['trades'] for t in tasks)/elapsed,'partial':0,'failed':0,'tasks':tasks,'resources':monitor.report()})
        parallel.shutdown();db.close()
    report['snapshot']=speed.PROFILER.snapshot();report['correctness']='All measured worker modes matched full trade and non-observational metric digests'
    output.write_text(json.dumps(report,indent=2,default=str))
    print(json.dumps({'generation_nodes_per_minute_median':statistics.median(g['nodes_per_minute'] for g in report['generation']),'batches':[{'requested_workers':x['requested_workers'],'effective_workers':x['effective_workers'],'seconds':x['total_seconds'],'cold':x['cold_pool']} for x in report['deeptest']],'output':str(output)},indent=2))
    speed.PROFILER.shutdown(persist=False)

if __name__=='__main__':
    if not __debug__:raise RuntimeError('Run without Python optimization; correctness assertions are required')
    main()
