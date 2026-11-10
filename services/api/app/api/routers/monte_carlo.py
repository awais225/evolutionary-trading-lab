from __future__ import annotations
import csv, hashlib, io, json, secrets, time, uuid, zipfile, traceback, logging
import multiprocessing
import threading
from fastapi import APIRouter, Depends, HTTPException
from hero1_storage.engine import instrument_session, registry_session
from hero1_storage.registry_models import DashboardTask
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.deps import get_instrument_context, get_instrument_session, get_registry_session
from hero1_storage import instrument_models as im
from app.monte_carlo_service import METHODS, partial_summary, simulate
from app import task_service

router=APIRouter(prefix="/api/v1/instruments/{instrument}/monte-carlo",tags=["monte-carlo"])
_LOG=logging.getLogger("hero1.api.monte_carlo")
# Isolated, bounded-by-admission OS processes make stuck Monte Carlo jobs
# individually terminable without taking down the API process. Children receive
# only immutable scalar identifiers and create their own DB sessions.
_MC_PROCESSES:dict[str,multiprocessing.Process]={}
_MC_PENDING:dict[str,tuple]={}
_MC_POOL_LOCK=threading.RLock()
_MC_MAX_PROCESSES=2
_MC_STOPPING=False
_MC_SUPERVISOR:threading.Thread|None=None
_MC_SUPERVISOR_WAKE=threading.Event()

def _spawn_pending_locked():
    global _MC_SUPERVISOR
    alive=sum(p.is_alive() for p in _MC_PROCESSES.values())
    while not _MC_STOPPING and alive<_MC_MAX_PROCESSES and _MC_PENDING:
        task_id=next(iter(_MC_PENDING));target,args=_MC_PENDING.pop(task_id)
        ctx=multiprocessing.get_context("spawn")
        process=ctx.Process(target=target,args=args,name=f"hero1-mc-{task_id[:8]}",daemon=True)
        process.start();_MC_PROCESSES[task_id]=process;alive+=1

def _reconcile_abnormal_exit(task_id:str,exitcode:int|None)->None:
    """A crashed child is never allowed to leave its durable task RUNNING."""
    try:
        reg=registry_session()
        try:
            task=reg.get(DashboardTask,task_id)
            if not task or task.status not in ("QUEUED","RUNNING"): return
            payload=json.loads(task.payload_json or "{}")
            mcid=payload.get("monte_carlo_run_id")
            instrument=task.instrument_id
            if mcid and instrument:
                db=instrument_session(instrument)
                try:
                    mc=db.get(im.MonteCarloRun,mcid)
                    if mc and mc.status in ("QUEUED","RUNNING"):
                        reason=f"Monte Carlo worker process exited unexpectedly (exit code {exitcode})"
                        mc.status="FAILED";mc.error=reason;mc.finished_at=time.time();db.commit()
                        task_service.finish_task(reg,task_id,"FAILED",error=reason,summary="Worker process exited unexpectedly")
                finally: db.close()
        finally: reg.close()
    except Exception: _LOG.exception("Failed reconciling crashed Monte Carlo worker",extra={"task_id":task_id,"exitcode":exitcode})

def _worker_supervisor():
    while True:
        _MC_SUPERVISOR_WAKE.wait(.2);_MC_SUPERVISOR_WAKE.clear()
        with _MC_POOL_LOCK:
            for task_id,process in list(_MC_PROCESSES.items()):
                if not process.is_alive():
                    process.join(timeout=0);_MC_PROCESSES.pop(task_id,None)
                    if process.exitcode not in (0,None): _reconcile_abnormal_exit(task_id,process.exitcode)
            _spawn_pending_locked()
            if not _MC_PENDING and not _MC_PROCESSES:
                return

def _ensure_supervisor_locked():
    global _MC_SUPERVISOR
    if _MC_SUPERVISOR is None or not _MC_SUPERVISOR.is_alive():
        _MC_SUPERVISOR=threading.Thread(target=_worker_supervisor,name="hero1-mc-supervisor",daemon=True)
        _MC_SUPERVISOR.start()
    _MC_SUPERVISOR_WAKE.set()

def _reap_worker_processes():
    with _MC_POOL_LOCK:
        for task_id, process in list(_MC_PROCESSES.items()):
            if not process.is_alive():
                process.join(timeout=0);_MC_PROCESSES.pop(task_id, None)
                if process.exitcode not in (0,None): _reconcile_abnormal_exit(task_id,process.exitcode)
        _spawn_pending_locked()
        if _MC_PENDING or _MC_PROCESSES: _ensure_supervisor_locked()

def worker_process_snapshot():
    _reap_worker_processes()
    with _MC_POOL_LOCK:
        return [{"task_id":task_id,"pid":process.pid,"alive":process.is_alive()}
                for task_id,process in _MC_PROCESSES.items()]

def _register_worker_process(task_id:str,target,args:tuple):
    """Register a serializable, process-isolated worker (also used by lifecycle tests)."""
    with _MC_POOL_LOCK:
        if _MC_STOPPING: raise RuntimeError("Monte Carlo workers are stopping; retry after process status clears")
        _MC_PENDING[task_id]=(target,tuple(args))
        _spawn_pending_locked();_ensure_supervisor_locked()

def _submit_worker(*args):
    task_id=args[2]
    _register_worker_process(task_id,_mc_worker,tuple(args))

def terminate_worker_processes(grace_seconds:float=2.0)->dict:
    """Stop scheduling, then terminate/kill all active MC OS processes."""
    global _MC_STOPPING
    with _MC_POOL_LOCK:
        _MC_STOPPING=True
        queued=list(_MC_PENDING)
        _MC_PENDING.clear()
        selected=list(_MC_PROCESSES.items())
    graceful_deadline=time.monotonic()+max(0.0,grace_seconds)
    for _,process in selected:
        if process.is_alive(): process.join(timeout=max(0.0,graceful_deadline-time.monotonic()))
    for _,process in selected:
        if process.is_alive(): process.terminate()
    terminate_deadline=time.monotonic()+1.0
    for _,process in selected:
        if process.is_alive(): process.join(timeout=max(0.0,terminate_deadline-time.monotonic()))
    forced=[]
    for task_id,process in selected:
        if process.is_alive():
            forced.append(task_id)
            killer=getattr(process,"kill",None)
            if killer: killer()
            elif process.pid: os.kill(process.pid,9)
            process.join(timeout=max(2.0,grace_seconds))
    still_alive=[task_id for task_id,process in selected if process.is_alive()]
    exited=[]
    with _MC_POOL_LOCK:
        for task_id,process in selected:
            if not process.is_alive():
                process.join(timeout=0);_MC_PROCESSES.pop(task_id,None);exited.append(task_id)
        if not still_alive: _MC_STOPPING=False
        _MC_SUPERVISOR_WAKE.set()
    return {"terminated":exited,"force_killed":forced,"still_alive":still_alive,"cancelled_queued":queued}

def shutdown_workers(wait:bool=True):
    # Lifespan shutdown calls STOP ALL first. As a final safety net, no managed
    # child is allowed to outlive an orderly API shutdown.
    return terminate_worker_processes(1.0)

class StartBody(BaseModel):
    node_ids:list[int]=Field(min_length=1)
    methods:list[str]=Field(default_factory=lambda:["RESHUFFLE","BLOCK_BOOTSTRAP"])
    config:dict=Field(default_factory=dict)
    deeptest_run_ids:dict[int,int]=Field(default_factory=dict)
    scenario_ids:dict[int,int]=Field(default_factory=dict)
    idempotency_key:str|None=None

@router.get("/nodes")
def eligible_nodes(instrument:str=Depends(get_instrument_context), session:Session=Depends(get_instrument_session)):
    nodes=session.query(im.Node).filter_by(instrument_id=instrument.upper()).order_by(im.Node.id).all()
    latest={}
    for r in session.query(im.DeepTestRun).filter_by(instrument_id=instrument.upper()).order_by(im.DeepTestRun.id.desc()).all():
        latest.setdefault(r.node_id,r)
    out=[]
    for n in nodes:
        r=latest.get(n.id); m=json.loads(r.metrics_json) if r and r.metrics_json else {}
        count=session.query(im.DeepTestRunTrade).filter_by(run_id=r.id).count() if r else 0
        trade_details=[]
        if r:
            trade_details=[json.loads(t.detail_json) if t.detail_json else {} for t in session.query(im.DeepTestRunTrade).filter_by(run_id=r.id).all()]
        stress_ok=bool(trade_details) and all(t.get("spread_cost") is not None and t.get("slippage") is not None for t in trade_details)
        saved_scenarios=session.query(im.DeepTestScenarioDef).filter_by(instrument_id=instrument.upper(),node_id=n.id).order_by(im.DeepTestScenarioDef.id).all()
        out.append({"scenarios":[{"id":s.id,"name":s.name,"node_id":s.node_id,"node_version":s.node_version,"source_run_id":s.source_run_id,"filters":json.loads(s.filters_json or "{}"),"stats":json.loads(s.stats_json or "{}"),"trade_count":len(json.loads(s.trades_json or "[]"))} for s in saved_scenarios],"node_id":n.id,"name":n.name,"instrument":n.instrument_id,"timeframe":n.timeframe,
          "direction":(json.loads(n.spec_json).get("identity",{}).get("direction") if n.spec_json else None),
          "version":n.version,"starred":bool(n.starred),"origin":n.origin,"folder":(json.loads(n.spec_json).get("identity",{}).get("folder") if n.spec_json else None),"tags":(json.loads(n.spec_json).get("identity",{}).get("tags",[]) if n.spec_json else []),"deeptest_status":r.status if r else "NOT_TESTED",
          "deeptest_run_id":r.id if r else None,"deeptest_started_at":r.started_at if r else None,"deeptest_finished_at":r.finished_at if r else None,"date_range":((json.loads(r.config_json).get("actual_range") or json.loads(r.config_json).get("requested_range") or json.loads(r.config_json).get("range")) if r and r.config_json else None),"fingerprint":r.fingerprint if r else n.spec_hash,
          "trade_count":count,"metrics":m,"readiness":(json.loads(r.data_quality_json).get("readiness") if r and r.data_quality_json else "UNVERIFIED"),
          "eligible":bool(r and r.status=="DONE" and count>0),
          "reason":None if r and r.status=="DONE" and count>0 else "Requires a completed DeepTesting run with at least one trade",
          "method_availability":{"RESHUFFLE":count>0,"BLOCK_BOOTSTRAP":count>0,"START_POINT":count>0,"EXECUTION_STRESS":stress_ok},
          "method_reasons":{"EXECUTION_STRESS":None if stress_ok else "Requires recorded spread_cost and slippage for every trade"}})
    return {"nodes":out}

def _watch_api_parent(parent_pid:int)->None:
    """Exit child if its API parent dies unexpectedly (prevent orphan writers)."""
    import os,signal
    while os.getppid()==parent_pid:
        time.sleep(.05)
    os.kill(os.getpid(),signal.SIGTERM)

def _mc_worker(instrument: str, mc_id: str, task_id: str, node_id: int, deep_run_id: int) -> None:
    """Run one node/method in an isolated OS process with fresh DB sessions."""
    import multiprocessing as _mp, os as _os
    if _mp.current_process().name!="MainProcess":
        threading.Thread(target=_watch_api_parent,args=(_os.getppid(),),name="hero1-parent-watch",daemon=True).start()
    from app import task_service
    reg = registry_session(); db = instrument_session(instrument)
    started = time.time()
    try:
        task = reg.get(DashboardTask, task_id)
        mc = db.get(im.MonteCarloRun, mc_id)
        deep = db.get(im.DeepTestRun, deep_run_id)
        if not task or not mc or not deep:
            raise RuntimeError("Monte Carlo task/run/DeepTesting source missing")
        cfg = json.loads(mc.config_json)
        rows = db.query(im.DeepTestRunTrade).filter_by(run_id=deep_run_id).order_by(im.DeepTestRunTrade.entry_ts).all()
        selected_trade_ids = set(cfg.get("filtered_trade_ids") or [])
        trades = [{**(json.loads(t.detail_json) if t.detail_json else {}), "id": t.id,
                   "net_pnl": t.net_pnl, "entry_ts": t.entry_ts, "exit_ts": t.exit_ts}
                  for t in rows if not selected_trade_ids or t.id in selected_trade_ids]
        if cfg.get("scenario_id") is not None and not selected_trade_ids:
            raise RuntimeError("saved scenario has no persisted filtered source trades")
        task_service.update_task(reg, task_id, status="RUNNING", current_unit=f"Node {node_id} · {mc.method}")
        def is_cancelled():
            reg.expire_all(); current = reg.get(DashboardTask, task_id)
            return bool(current and (json.loads(current.payload_json or "{}").get("cancel_requested")))
        def on_batch(done, metrics, paths):
            # Persist actual completed outputs after each bounded batch.
            partial = {"partial": True, "summary": partial_summary(metrics,int(cfg["simulations"]),mc.seed,mc.method,len(trades),cfg.get("deep_baseline_return_pct")), "per_simulation": metrics, "representative_curves": paths[:int(cfg.get("displayed_curves",10))], "sample_curves": paths[:int(cfg.get("displayed_curves",10))]}
            mc.result_json = json.dumps(partial, separators=(",", ":")); db.commit()
            elapsed = max(time.time()-started, .001)
            reg.expire_all(); latest_task=reg.get(DashboardTask,task_id)
            progress_payload=json.loads(latest_task.payload_json or "{}") if latest_task else {}
            speed=done/elapsed
            eta=max(0,int((int(cfg["simulations"])-done)/speed)) if done>=5 and speed>0 else None
            progress_payload.update({"elapsed_seconds":elapsed,"simulations_per_second":speed,"estimated_remaining_seconds":eta})
            task_service.update_task(reg, task_id, completed_units=done,
                current_unit=f"Node {node_id} · {mc.method} · {done}/{cfg['simulations']} simulations",
                summary=f"{done}/{cfg['simulations']} · {done/elapsed:.2f} sims/sec",
                payload=progress_payload)
        result = simulate(trades, cfg, mc.method, on_batch=on_batch, cancel_check=is_cancelled)
        was_cancelled = bool(result["summary"].get("cancelled")) or is_cancelled()
        if was_cancelled:
            result["partial"] = True; result["summary"]["partial"] = True
            result["robustness"] = None; result["eligibility"] = None
            result["partial_sample_count"] = result["summary"]["simulations_completed"]
        warnings = json.loads(mc.warning_json or "[]")
        mc.result_json = json.dumps(result, separators=(",", ":")); mc.finished_at=time.time()
        if was_cancelled:
            mc.status="CANCELLED"
            task_service.finish_task(reg, task_id, "CANCELLED", summary=f"Cancelled after {result['summary']['simulations_completed']} simulations", completed_units=result["summary"]["simulations_completed"])
        else:
            mc.status="DONE"
            task_service.finish_task(reg, task_id, "COMPLETED", summary=f"{result['summary']['simulations_completed']} simulations completed")
        db.commit()
    except Exception as exc:
        error_detail=f"{type(exc).__name__}: {exc}"
        traceback_text=traceback.format_exc()
        _LOG.exception("Monte Carlo worker failed",extra={"task_id":task_id,"run_id":mc_id,"node_id":node_id,"instrument":instrument})
        try:
            latest=reg.get(DashboardTask,task_id) if reg else None
            cancel_requested=bool(latest and json.loads(latest.payload_json or "{}").get("cancel_requested"))
            if 'mc' in locals() and mc is not None:
                if cancel_requested:
                    mc.status="CANCELLED"
                    if not mc.result_json:
                        mc.result_json=json.dumps({"partial":True,"summary":{"simulations_requested":int(json.loads(mc.config_json).get("simulations",0)),"simulations_completed":0,"seed":mc.seed,"method":mc.method},"per_simulation":[],"representative_curves":[]})
                else:
                    mc.status="FAILED"; mc.error=f"{error_detail}\n{traceback_text}"
                mc.finished_at=time.time(); db.commit()
            if cancel_requested:
                task_service.finish_task(reg,task_id,"CANCELLED",summary="Cancelled before the first simulation completed",completed_units=int(latest.completed_units or 0))
            else:
                task_service.finish_task(reg, task_id, "FAILED", error=f"{error_detail}\n{traceback_text}"[-12000:], summary="Monte Carlo worker failed")
        except Exception:
            _LOG.exception("Could not persist Monte Carlo failure state",extra={"task_id":task_id,"run_id":mc_id})
            try:
                recovery=registry_session()
                try:
                    task_service.finish_task(recovery,task_id,"FAILED",error=f"{error_detail}; failure persistence error: {traceback.format_exc()}"[-12000:],summary="Monte Carlo worker failed; persistence reconciliation required")
                finally:
                    recovery.close()
            except Exception:
                _LOG.exception("Failure state could not be reconciled to the registry",extra={"task_id":task_id,"run_id":mc_id})
    finally:
        db.close(); reg.close()


@router.post("/runs")
def start_runs(body:StartBody,instrument:str=Depends(get_instrument_context),session:Session=Depends(get_instrument_session),registry:Session=Depends(get_registry_session)):
    import threading
    if not task_service.accepting_jobs(): raise HTTPException(503,"HERO1 is stopping background work; retry after process status is clear")
    bad=set(body.methods)-METHODS
    if bad: raise HTTPException(422,f"unsupported methods: {sorted(bad)}")
    if not body.methods: raise HTTPException(422,"select at least one available method")
    config={"simulations":1000,"random_points":1000,"displayed_curves":10,"initial_capital":200000,"risk_pct":1,"max_active_trades":1,"max_drawdown_pct":25,"min_survival_pct":99,"max_p99_drawdown_pct":35,"block_size":10,"batch_size":25,**body.config}
    if not 10 <= int(config["simulations"]) <= 5000: raise HTTPException(422,"simulations must be between 10 and 5000")
    result=[]
    base_identity={"instrument":instrument.upper(),"node_ids":sorted(body.node_ids),"methods":sorted(body.methods),"config":{k:v for k,v in config.items() if k!="seed"},"deeptest_run_ids":body.deeptest_run_ids}
    deterministic_default_seed=int.from_bytes(hashlib.sha256(json.dumps(base_identity,sort_keys=True,separators=(",",":"),default=str).encode()).digest()[:4],"big")
    for node_id in body.node_ids:
        node=session.query(im.Node).filter_by(id=node_id,instrument_id=instrument.upper()).first()
        if not node:
            result.append({"node_id":node_id,"status":"FAILED","error":"Node not found for selected instrument"});continue
        run_id=body.deeptest_run_ids.get(node_id)
        scenario_id=body.scenario_ids.get(node_id)
        scenario=session.query(im.DeepTestScenarioDef).filter_by(id=scenario_id,node_id=node_id,instrument_id=instrument.upper()).first() if scenario_id is not None else None
        if scenario_id is not None and scenario is None:
            result.append({"node_id":node_id,"scenario_id":scenario_id,"status":"BLOCKED","error":"Saved scenario does not belong to selected parent node"});continue
        if scenario is not None:
            run_id=scenario.source_run_id
        deep=(session.get(im.DeepTestRun,run_id) if run_id else session.query(im.DeepTestRun).filter_by(node_id=node_id,instrument_id=instrument.upper(),status="DONE").order_by(im.DeepTestRun.id.desc()).first())
        if not deep or deep.node_id!=node_id or deep.instrument_id!=instrument.upper() or deep.status!="DONE":
            result.append({"node_id":node_id,"scenario_id":scenario_id,"status":"BLOCKED","error":"No matching completed DeepTesting run"});continue
        if scenario is not None and deep.id != scenario.source_run_id:
            result.append({"node_id":node_id,"scenario_id":scenario_id,"status":"BLOCKED","error":"Scenario source run mismatch"});continue
        rows=session.query(im.DeepTestRunTrade).filter_by(run_id=deep.id).order_by(im.DeepTestRunTrade.entry_ts).all()
        filtered_ids=set(json.loads(scenario.trades_json or "[]") and [t["id"] for t in json.loads(scenario.trades_json)] or []) if scenario else set()
        trades=[{**(json.loads(t.detail_json) if t.detail_json else {}),"id":t.id,
                 "net_pnl":t.net_pnl,"entry_ts":t.entry_ts,"exit_ts":t.exit_ts}
                for t in rows if scenario is None or t.id in filtered_ids]
        if not trades:
            result.append({"node_id":node_id,"status":"BLOCKED","error":"DeepTesting trade ledger is empty"});continue
        for method in body.methods:
            if method=="EXECUTION_STRESS" and not all(t.get("spread_cost") is not None and t.get("slippage") is not None for t in trades):
                result.append({"node_id":node_id,"method":method,"status":"BLOCKED","error":"execution stress requires recorded spread_cost and slippage for every trade"});continue
            base_seed=int(config.get("seed") if config.get("seed") is not None else deterministic_default_seed)
            method_salt=sum((i+1)*ord(ch) for i,ch in enumerate(method))
            seed=(base_seed + node_id*1000003 + method_salt) % (2**32)
            source_quality=json.loads(deep.data_quality_json) if deep.data_quality_json else {}
            source_readiness=source_quality.get("readiness")
            if isinstance(source_readiness,dict): source_readiness=source_readiness.get("status")
            known_logic_failures=source_quality.get("known_logic_compliance_failures",source_quality.get("logic_compliance_failures"))
            deep_config=json.loads(deep.config_json) if deep.config_json else {}
            deep_metrics=json.loads(deep.metrics_json) if deep.metrics_json else {}
            execution_cost_assumptions={key:deep_config.get(key) for key in ("spread_mode","commission_per_lot","swap_long_per_lot","swap_short_per_lot","slippage_price","tick_size","tick_value_account")}
            tested_range=deep_config.get("actual_range") or deep_config.get("requested_range") or deep_config.get("range")
            scenario_stats=json.loads(scenario.stats_json or "{}") if scenario else {}
            scenario_equity=[]
            if scenario:
                equity=float(config.get("initial_capital",200000))
                for trade in sorted(trades,key=lambda t: float(t.get("exit_ts") or t.get("entry_ts") or 0)):
                    equity += float(trade.get("net_pnl") or 0)
                    scenario_equity.append({"equity":equity})
            run_config={**config,"seed":seed,"base_seed":base_seed,"tested_range":tested_range,"source_run_id":deep.id,"scenario_id":scenario.id if scenario else None,"scenario_name":scenario.name if scenario else None,"filtered_trade_ids":[t["id"] for t in trades] if scenario else None,"scenario_filters":json.loads(scenario.filters_json or "{}") if scenario else None,"deep_baseline_return_pct":scenario_stats.get("returnPct",scenario_stats.get("return_pct",deep_metrics.get("return_on_initial_capital_pct"))) if scenario else deep_metrics.get("return_on_initial_capital_pct"),"deep_baseline_net_profit":scenario_stats.get("net_profit",scenario_stats.get("net",deep_metrics.get("net_profit"))) if scenario else deep_metrics.get("net_profit"),"deep_baseline_equity_curve":(scenario_equity if scenario else (json.loads(deep.equity_json) if deep.equity_json else [])),"execution_cost_assumptions":execution_cost_assumptions,"source_data_readiness":source_readiness,"known_logic_compliance_failures":known_logic_failures,"task_id":None}
            trade_sequence_hash=hashlib.sha256(json.dumps(trades,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()
            study_identity={"instrument":instrument.upper(),"node_id":node.id,"node_version":deep.node_version or node.version,"scenario_id":scenario.id if scenario else None,"source_run_id":deep.id,"source_run_fingerprint":deep.fingerprint,"trade_sequence_hash":trade_sequence_hash,"method":method,"config":run_config}
            study_fingerprint=hashlib.sha256(json.dumps(study_identity,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()
            # Lazily backfill pre-fingerprint rows only when they share the exact
            # node/source/scenario/method and can be canonically reidentified.
            legacy=session.query(im.MonteCarloRun).filter_by(instrument_id=instrument.upper(),node_id=node_id,deeptest_run_id=deep.id,scenario_id=scenario.id if scenario else None,method=method,study_fingerprint=None).all()
            for prior in legacy:
                prior_config=json.loads(prior.config_json or "{}")
                prior_config.pop("task_id",None);prior_config.setdefault("source_run_id",deep.id)
                prior_config.setdefault("scenario_id",scenario.id if scenario else None)
                prior_identity={"instrument":instrument.upper(),"node_id":node.id,"node_version":prior.node_version or deep.node_version or node.version,"scenario_id":scenario.id if scenario else None,"source_run_id":deep.id,"source_run_fingerprint":deep.fingerprint,"trade_sequence_hash":trade_sequence_hash,"method":method,"config":prior_config}
                prior.study_fingerprint=hashlib.sha256(json.dumps(prior_identity,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()
            if legacy: session.commit()
            dedupe_key=study_fingerprint
            completed=session.query(im.MonteCarloRun).filter_by(instrument_id=instrument.upper(),study_fingerprint=study_fingerprint,status="DONE").order_by(im.MonteCarloRun.created_at.desc()).first()
            if completed:
                result.append({"id":completed.id,"node_id":node_id,"scenario_id":scenario.id if scenario else None,"method":method,"seed":seed,"status":"ALREADY_COMPLETED","completed_at":completed.finished_at,"duplicate_reused":True})
                continue
            existing=registry.query(DashboardTask).filter(DashboardTask.dedupe_key==dedupe_key,DashboardTask.status.in_(("QUEUED","RUNNING"))).first()
            if existing:
                prior=json.loads(existing.payload_json or "{}")
                result.append({"id":prior.get("monte_carlo_run_id"),"task_id":existing.id,"node_id":node_id,"method":method,"seed":seed,"status":existing.status,"total_simulations":int(config["simulations"]),"duplicate_reused":True})
                continue
            mcid=str(uuid.uuid4())
            row=im.MonteCarloRun(id=mcid,instrument_id=instrument.upper(),node_id=node_id,deeptest_run_id=deep.id,scenario_id=scenario.id if scenario else None,node_version=deep.node_version,fingerprint=deep.fingerprint,study_fingerprint=study_fingerprint,method=method,seed=seed,status="RUNNING",config_json=json.dumps(run_config),created_at=time.time())
            session.add(row);session.commit()
            try:
                task=task_service.create_task(registry,category="MONTE_CARLO",instrument=instrument.upper(),label=f"Monte Carlo · Node {node_id} · {method}",total_units=int(config["simulations"]),status="QUEUED",dedupe_key=dedupe_key,payload={"monte_carlo_run_id":mcid,"node_id":node_id,"scenario_id":scenario.id if scenario else None,"deeptest_run_id":deep.id,"fingerprint":deep.fingerprint,"method":method,"seed":seed,"config":run_config})
            except RuntimeError as exc:
                session.delete(row);session.commit()
                raise HTTPException(503,str(exc)) from exc
            except IntegrityError:
                registry.rollback()
                existing=registry.query(DashboardTask).filter(DashboardTask.dedupe_key==dedupe_key,DashboardTask.status.in_(("QUEUED","RUNNING"))).first()
                session.delete(row);session.commit()
                if not existing: raise
                prior=json.loads(existing.payload_json or "{}")
                result.append({"id":prior.get("monte_carlo_run_id"),"task_id":existing.id,"node_id":node_id,"method":method,"seed":seed,"status":existing.status,"total_simulations":int(config["simulations"]),"duplicate_reused":True})
                continue
            run_config["task_id"]=task.id; row.config_json=json.dumps(run_config); session.commit()
            try:
                _submit_worker(instrument.upper(),mcid,task.id,node_id,deep.id)
            except Exception as exc:
                row.status="FAILED";row.error=f"Monte Carlo worker process could not start: {type(exc).__name__}: {exc}";row.finished_at=time.time();session.commit()
                task_service.finish_task(registry,task.id,"FAILED",error=row.error,summary="Monte Carlo worker process failed to start")
                result.append({"id":mcid,"task_id":task.id,"node_id":node_id,"method":method,"seed":seed,"status":"FAILED","error":row.error})
                continue
            result.append({"id":mcid,"task_id":task.id,"node_id":node_id,"scenario_id":scenario.id if scenario else None,"scenario_name":scenario.name if scenario else None,"deeptest_run_id":deep.id,"method":method,"seed":seed,"status":"QUEUED","total_simulations":int(config["simulations"])})
    return {"runs":result,"async":True}

@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id:str,instrument:str=Depends(get_instrument_context),registry:Session=Depends(get_registry_session)):
    task=registry.get(DashboardTask,task_id)
    if not task or task.category!="MONTE_CARLO" or task.instrument_id!=instrument.upper(): raise HTTPException(404,"Monte Carlo task not found")
    if task.status not in ("QUEUED","RUNNING"):
        return {"task_id":task_id,"status":task.status,"cancel_requested":False}
    payload=json.loads(task.payload_json or "{}")
    payload.update({"cancel_requested":True,"cancellation_reason":"user requested cancellation","cancellation_requested_at":time.time()})
    task_service.update_task(registry,task_id,payload=payload,summary="Cancellation requested")
    return {"task_id":task_id,"status":task.status,"cancel_requested":True}

@router.get("/runs")
def history(node_id:int|None=None,instrument:str=Depends(get_instrument_context),session:Session=Depends(get_instrument_session)):
    q=session.query(im.MonteCarloRun).filter_by(instrument_id=instrument.upper())
    if node_id is not None:q=q.filter_by(node_id=node_id)
    rows=q.order_by(im.MonteCarloRun.created_at.desc()).all()
    return {"runs":[{"id":r.id,"node_id":r.node_id,"scenario_id":r.scenario_id,"scenario_name":(session.get(im.DeepTestScenarioDef,r.scenario_id).name if r.scenario_id and session.get(im.DeepTestScenarioDef,r.scenario_id) else json.loads(r.config_json or "{}").get("scenario_name")),"instrument":r.instrument_id,"timeframe":(session.get(im.Node,r.node_id).timeframe if session.get(im.Node,r.node_id) else None),"deeptest_run_id":r.deeptest_run_id or json.loads(r.config_json or "{}").get("source_run_id_snapshot") or json.loads(r.config_json or "{}").get("source_run_id"),"method":r.method,"seed":r.seed,"status":r.status,"config":json.loads(r.config_json),"fingerprint":r.fingerprint,"study_fingerprint":r.study_fingerprint,"node_version":r.node_version,"summary":(json.loads(r.result_json).get("summary") if r.result_json else None),"robustness":(json.loads(r.result_json).get("robustness") if r.result_json else None),"eligibility":(json.loads(r.result_json).get("eligibility") if r.result_json else None),"warnings":json.loads(r.warning_json or "[]"),"created_at":r.created_at,"error":r.error} for r in rows]}

@router.get("/runs/{run_id}")
def get_result(run_id:str,instrument:str=Depends(get_instrument_context),session:Session=Depends(get_instrument_session)):
    r=session.query(im.MonteCarloRun).filter_by(id=run_id,instrument_id=instrument.upper()).first()
    if not r:raise HTTPException(404,"Monte Carlo run not found")
    saved_config=json.loads(r.config_json); node=session.get(im.Node,r.node_id); deep=session.get(im.DeepTestRun,r.deeptest_run_id)
    return {"id":r.id,"task_id":saved_config.get("task_id"),"node_id":r.node_id,"scenario_id":r.scenario_id,"scenario_name":(session.get(im.DeepTestScenarioDef,r.scenario_id).name if r.scenario_id and session.get(im.DeepTestScenarioDef,r.scenario_id) else None),"node_name":node.name if node else None,"instrument":r.instrument_id,"timeframe":deep.timeframe if deep else (node.timeframe if node else None),"node_version":r.node_version,"fingerprint":r.fingerprint,"study_fingerprint":r.study_fingerprint,"deeptest_run_id":r.deeptest_run_id or saved_config.get("source_run_id_snapshot") or saved_config.get("source_run_id"),"source_run_deleted":bool(saved_config.get("source_run_deleted")),"method":r.method,"seed":r.seed,"status":r.status,"config":saved_config,"result":json.loads(r.result_json) if r.result_json else None,"warnings":json.loads(r.warning_json) if r.warning_json else [],"error":r.error}

@router.delete("/runs/{run_id}")
def delete_run(run_id:str,instrument:str=Depends(get_instrument_context),session:Session=Depends(get_instrument_session)):
    row=session.query(im.MonteCarloRun).filter_by(id=run_id,instrument_id=instrument.upper()).first()
    if not row: raise HTTPException(404,"Monte Carlo run not found")
    if row.status in ("QUEUED","RUNNING"):
        raise HTTPException(409,"Cannot delete a queued or running Monte Carlo study")
    # Delete only the selected study record; its node, DeepTesting source run,
    # other studies and task audit record are intentionally left untouched.
    session.delete(row); session.commit()
    return {"id":run_id,"deleted":True}

@router.get("/runs/{run_id}/export")
def export_result(run_id:str,format:str="zip",instrument:str=Depends(get_instrument_context),session:Session=Depends(get_instrument_session)):
    r=session.query(im.MonteCarloRun).filter_by(id=run_id,instrument_id=instrument.upper()).first()
    if not r or not r.result_json:raise HTTPException(404,"completed Monte Carlo result not found")
    config=json.loads(r.config_json); result=json.loads(r.result_json)
    source_run_id=r.deeptest_run_id or config.get("source_run_id_snapshot") or config.get("source_run_id")
    deep=session.get(im.DeepTestRun,r.deeptest_run_id) if r.deeptest_run_id is not None else None
    node=session.get(im.Node,r.node_id)
    if format=="txt":
        text=f"HERO1 MONTE CARLO\nrun={r.id}\nnode={r.node_id}\nDeepTesting run={source_run_id}\nmethod={r.method}\nseed={r.seed}\nscore={result['robustness']['score']}\neligibility={result['eligibility']['status']}\nsummary={json.dumps(result['summary'],indent=2)}\nwarnings={r.warning_json}\n"
        return Response(text,media_type="text/plain",headers={"Content-Disposition":f'attachment; filename="monte_carlo_{r.id}.txt"'})
    mc_result = result
    manifest={
        "format":"HERO1_MONTE_CARLO/1", "id":r.id, "task_id":config.get("task_id"),
        "node_id":r.node_id, "node_name":node.name if node else None,
        "deeptest_run_id":source_run_id, "source_run_deleted":bool(config.get("source_run_deleted")), "study_fingerprint":r.study_fingerprint, "strategy_version":r.node_version,
        "fingerprint":r.fingerprint, "instrument":r.instrument_id,
        "timeframe":deep.timeframe if deep else None, "method":r.method,
        "seed":r.seed, "seed_strategy":mc_result.get("summary",{}).get("seed_strategy"), "status":r.status, "partial":bool(mc_result.get("partial")),
        "requested_simulations":mc_result.get("summary",{}).get("simulations_requested"),
        "completed_simulations":len(mc_result.get("per_simulation",[])),
        "created_at":r.created_at, "finished_at":r.finished_at,
        "warning_count":len(json.loads(r.warning_json or "[]")),
    }
    metrics=mc_result.get("per_simulation",[])
    csv_buffer=io.StringIO(newline="")
    csv_fields=list(dict.fromkeys(["simulation_index","simulation_seed",*(key for metric in metrics for key in metric.keys())]))
    writer=csv.DictWriter(csv_buffer,fieldnames=csv_fields,extrasaction="ignore")
    writer.writeheader(); writer.writerows(metrics)
    csv_text=csv_buffer.getvalue()
    original={"run_id":source_run_id,"node_id":r.node_id,"status":deep.status if deep else "MISSING",
        "fingerprint":deep.fingerprint if deep else r.fingerprint,
        "metrics":json.loads(deep.metrics_json) if deep and deep.metrics_json else None,
        "config":json.loads(deep.config_json) if deep and deep.config_json else None,
        "data_quality":json.loads(deep.data_quality_json) if deep and deep.data_quality_json else None,
        "trade_count":mc_result.get("summary",{}).get("trade_count")}
    identity={"node_id":r.node_id,"node_name":node.name if node else None,"node_version":r.node_version,
        "fingerprint":r.fingerprint,"instrument":r.instrument_id,"timeframe":deep.timeframe if deep else None,
        "strategy_json":json.loads(node.spec_json) if node and node.spec_json else None}
    execution={"method":r.method,"configuration":config if r.method=="EXECUTION_STRESS" else None,
        "required_evidence":["spread_cost","slippage"],
        "disclosure":mc_result.get("method_description"),
        "source_deeptesting_run_id":source_run_id,
        "cost_ledger_exported":False,
        "note":"Cost values are sourced from persisted DeepTesting trades during simulation; this archive does not duplicate the original trade ledger."}
    out=io.BytesIO()
    with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json",json.dumps(manifest,indent=2))
        z.writestr("configuration.json",json.dumps(config,indent=2))
        z.writestr("original_deeptesting_reference.json",json.dumps(original,indent=2))
        z.writestr("strategy_identity.json",json.dumps(identity,indent=2))
        z.writestr("simulation_results.csv",csv_text)
        z.writestr("simulation_results.json",json.dumps(metrics,indent=2))
        z.writestr("aggregate_metrics.json",json.dumps(mc_result.get("summary"),indent=2))
        z.writestr("percentile_distributions.json",json.dumps({"return":mc_result.get("summary",{}).get("return_percentiles"),"drawdown":mc_result.get("summary",{}).get("drawdown_percentiles"),"simulation_distributions":mc_result.get("summary",{}).get("simulation_distributions")},indent=2))
        z.writestr("equity_curves.json",json.dumps({"percentiles":mc_result.get("equity_percentiles"),"representative":mc_result.get("representative_curves"),"time_basis":"normalized trade progress; not historical candle timestamps"},indent=2))
        z.writestr("robustness_score.json",json.dumps(mc_result.get("robustness"),indent=2))
        z.writestr("eligibility.json",json.dumps(mc_result.get("eligibility"),indent=2))
        z.writestr("execution_stress.json",json.dumps(execution,indent=2))
        z.writestr("warnings.json",json.dumps(json.loads(r.warning_json or "[]"),indent=2))
        z.writestr("evidence_readiness.json",json.dumps(original.get("data_quality"),indent=2))
    return Response(out.getvalue(),media_type="application/zip",headers={"Content-Disposition":f'attachment; filename="monte_carlo_{r.id}.zip"'})
