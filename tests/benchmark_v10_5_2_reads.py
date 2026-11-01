"""Reproducible synthetic read-only workload after fixture creation.

Run separately (not during other tests): python tests/benchmark_v10_5_2_reads.py
 --source /path/to/source --output /TEMP/result.json --work /TEMP/new-empty-dir
No production paths accepted; work must be a fresh directory under system TEMP.
No app lifespan, generation campaign, backtest or real broker is started.
"""
import argparse
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--work',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--nodes',type=int,default=26000)
    parser.add_argument('--deep-nodes',type=int,default=128)
    parser.add_argument('--scenarios',type=int,default=4)
    parser.add_argument('--trades',type=int,default=512)
    args=parser.parse_args();source=args.source.resolve();work=args.work.resolve()
    temp=Path(tempfile.gettempdir()).resolve()
    if work.exists() or temp not in work.parents or source==work or source in work.parents:
        raise SystemExit('Refusing: --work must be NEW, under TEMP, outside source.')
    work.mkdir(parents=True)
    os.environ['EVOLUTIONARY_LAB_DATA_ROOT']=str(work/'data')
    os.environ['EVOLUTIONARY_LAB_STARTUP_BLOCKING']='1'
    sys.path.insert(0,str(source/'backend'))
    # Fixture source is the candidate's helper, independently of product source.
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    from v1052_read_fixture import seed
    from app.db import database as D
    from app.deeptest import metric_service as M,store as S
    from app.api import routes as R
    from app import config as C
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    cfg=C.LabConfig();cfg.database_path=str(work/'synthetic.db')
    cfg.data.cache_dir=str(work/'data-cache');cfg.mt5.mode='simulator'
    cfg.resources.cpu_limit_enabled=False;cfg.resources.memory_limit_enabled=False
    C._config=cfg
    db=D.Database(cfg.database_path);D._db=db
    ids=seed(db,nodes=args.deep_nodes,scenarios_per_node=args.scenarios,
             trades_per_scenario=args.trades,population=args.nodes)
    sql_counts={};count_lock=threading.Lock()
    def observe(sql):
        q=sql.lower()
        key='trade_selects' if q.startswith('select') and 'from deeptest_trades' in q else 'population_aggregates' if 'group by s.run_id' in q else None
        if key:
            with count_lock:sql_counts[key]=sql_counts.get(key,0)+1
    db.conn.set_trace_callback(observe)

    class MeasuredLock:
        """Harness only: top-level hold intervals, no nested double counting."""
        def __init__(self,lock):
            self.lock=lock;self.local=threading.local();self.guard=threading.Lock();self.clear()
        def clear(self):self.wait=[];self.hold=[]
        def acquire(self,*a,**kw):
            start=time.perf_counter();got=self.lock.acquire(*a,**kw);end=time.perf_counter()
            if got:
                depth=getattr(self.local,'depth',0)
                if not depth:
                    self.local.start=end
                    with self.guard:self.wait.append(end-start)
                self.local.depth=depth+1
            return got
        def release(self):
            self.local.depth-=1
            if not self.local.depth:
                value=time.perf_counter()-self.local.start
                with self.guard:self.hold.append(value)
            self.lock.release()
        def __enter__(self):self.acquire();return self
        def __exit__(self,*a):self.release()
        def snapshot(self):
            with self.guard:
                return {k:dict(count=len(v),total_seconds=sum(v),max_seconds=max(v,default=0)) for k,v in [('wait',self.wait),('hold',self.hold)]}
    measured=MeasuredLock(db._lock);db._lock=measured
    try:
        from app import read_diagnostics as T
    except ImportError:T=None
    if T:os.environ['LAB_V1052_READ_TRACE']='1'
    def clear():
        with M._LOCK:M._CACHE.pop(db,None)
        try:
            from app.research import population_state as P
            if hasattr(P,'_CACHE'):
                with P._CACHE_LOCK:P._CACHE.pop(db,None)
        except ImportError:pass
        sql_counts.clear();measured.clear()
    def sample(fn):
        sql_counts.clear();measured.clear();start=time.perf_counter();result=fn()
        elapsed=time.perf_counter()-start
        return result,dict(seconds=elapsed,sql_counts=dict(sql_counts),lock=measured.snapshot())
    clear()
    cold,cold_t=sample(R.deeptest_nodes)
    warm,warm_t=sample(R.deeptest_nodes)
    selected=ids[-1]
    detail,detail_t=sample(lambda:R.deeptest_run_get(selected))
    after,after_t=sample(R.deeptest_nodes)
    financial=detail['scenario']['analytics']
    # Validate every node/scenario is still addressable, not hidden by a cap.
    assert cold['count']==args.deep_nodes
    assert sum(n['scenario_count'] for n in cold['nodes'])==len(ids)
    app=FastAPI();app.include_router(R.router)
    urls=['/api/status','/api/system/status_row','/api/workflow/task_state',
          '/api/workflow/milestones','/api/deeptest/nodes','/api/deeptest/scenarios',
          '/api/nodes?filter=all&limit=15&compact=true&metric_source=generation']
    with TestClient(app) as client:
        # Initialise read-only service objects before the concurrent measured
        # round, but record initial status timings separately and honestly.
        serial=[]
        for url in urls[:4]:
            resp,t=sample(lambda url=url:client.get(url))
            assert resp.status_code==200,(url,resp.text[:200])
            serial.append(dict(url=url,**t))
        rounds=[]
        for n in range(3):
            clear()
            barrier=threading.Barrier(len(urls))
            def get(url):
                barrier.wait(timeout=10);start=time.perf_counter();resp=client.get(url)
                return dict(url=url,seconds=time.perf_counter()-start,status=resp.status_code)
            start=time.perf_counter()
            with ThreadPoolExecutor(max_workers=len(urls)) as pool:results=list(pool.map(get,urls))
            rounds.append(dict(wall_seconds=time.perf_counter()-start,requests=results,
                               sql_counts=dict(sql_counts),lock=measured.snapshot()))
            assert all(r['status']==200 for r in results),results
    report=dict(label='SYNTHETIC Linux/Python read workload; NOT user Windows/1.68 GiB certification',
        python=sys.version,platform=sys.platform,source=str(source),
        fixture=dict(nodes=args.nodes,deep_nodes=args.deep_nodes,scenarios=len(ids),trades=len(ids)*args.trades,
                     database_bytes=Path(db.path).stat().st_size,wal_bytes=Path(db.path+'-wal').stat().st_size if Path(db.path+'-wal').exists() else 0),
        listing_cold=cold_t,listing_warm=warm_t,detail=detail_t,listing_after_detail=after_t,
        serial_initial_status=serial,concurrent_rounds=rounds,
        details_financial=financial,read_timings=T.snapshot() if T else None,
        metric_cache_entries=len(M._CACHE.get(db,{})),
        metric_cache_bytes=sum(v[1] for v in M._CACHE.get(db,{}).values()))
    report['details_financial']['provenance'].pop('calculated_at',None)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k in ('fixture','listing_cold','listing_warm','detail','listing_after_detail')},indent=2))
    for round in rounds:print('CONCURRENT',round['requests'])
    db.close()


if __name__=='__main__':main()
