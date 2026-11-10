"""Host metrics and scoped process lifecycle controls."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_registry_session

router = APIRouter(prefix="/api/v1/system", tags=["system"])
_STOP_ALL_LOCK=__import__("threading").Lock()


class ProcessAction(BaseModel):
    timeout_seconds: int = 30
    confirm: str | None = None


class DataPreviewRequest(BaseModel):
    category: str


class DataDeleteRequest(BaseModel):
    category: str
    confirm: str


def _task_rows(session):
    from hero1_storage.registry_models import DashboardTask
    return session.query(DashboardTask).order_by(DashboardTask.created_at.desc()).limit(500).all()


def _process_snapshot(session):
    from app import task_service
    task_service.sync_fetch_tasks(session)
    rows=_task_rows(session)
    counts={s:sum(1 for t in rows if t.status==s) for s in ("QUEUED","RUNNING","COMPLETED","FAILED","CANCELLED","BLOCKED")}
    active=[task_service.task_dict(t) for t in rows if t.status in task_service.ACTIVE]
    from app.api.routers import monte_carlo as mc_router
    workers=mc_router.worker_process_snapshot()
    return {"accepting_new_jobs":task_service.accepting_jobs(),"worker_count":len(active),"queued_jobs":counts["QUEUED"],"running_jobs":counts["RUNNING"],"counts":counts,"active_jobs":active,"worker_processes":workers,"worker_process_count":sum(1 for w in workers if w["alive"])}


def _request_all_cancellations(session):
    import json
    from app import task_service, deeptesting_service, market_data_service
    from hero1_storage.registry_models import DashboardTask
    active=session.query(DashboardTask).filter(DashboardTask.status.in_(task_service.ACTIVE)).all()
    requested=[];unsupported=[]
    for task in active:
        if task.category=="MONTE_CARLO":
            payload=json.loads(task.payload_json or "{}");payload.update({"cancel_requested":True,"cancellation_reason":"STOP ALL PROCESSES","cancellation_requested_at":__import__("time").time()})
            task_service.update_task(session,task.id,payload=payload,summary="Cancellation requested by STOP ALL PROCESSES")
            requested.append(task.id)
        elif task.category=="DEEPTESTING":
            if deeptesting_service.request_task_cancel(task.id): requested.append(task.id)
            else: unsupported.append({"task_id":task.id,"reason":"no matching in-process cancellation token; will be reconciled on restart"})
        elif task.category=="DATA_FETCHING":
            payload=json.loads(task.payload_json or "{}");op_id=payload.get("operation_id")
            if op_id and market_data_service.cancel_operation(op_id): requested.append(task.id)
            else: unsupported.append({"task_id":task.id,"reason":"market-data operation does not expose an active cancellation token"})
        else:
            unsupported.append({"task_id":task.id,"reason":f"worker category {task.category} has no cancellable worker"})
    return requested,unsupported


def _reconcile_terminated_monte_carlo(task_ids:list[str], reason:str)->None:
    """After OS exit is confirmed, finalize tasks while retaining the last checkpoint."""
    import json,time
    from app import task_service
    from hero1_storage.engine import registry_session,instrument_session
    from hero1_storage.registry_models import DashboardTask
    from hero1_storage import instrument_models as im
    reg=registry_session()
    try:
        for task_id in task_ids:
            task=reg.get(DashboardTask,task_id)
            if not task or task.status not in task_service.ACTIVE: continue
            payload=json.loads(task.payload_json or "{}")
            run_id=payload.get("monte_carlo_run_id")
            db=instrument_session(task.instrument_id) if task.instrument_id else None
            try:
                run=db.get(im.MonteCarloRun,run_id) if db and run_id else None
                if run:
                    saved=json.loads(run.result_json or "{}")
                    if saved:
                        saved["partial"]=True
                        summary=saved.setdefault("summary",{})
                        summary["partial"]=True
                        summary["simulations_completed"]=int(task.completed_units or summary.get("simulations_completed",0))
                    else:
                        saved={"partial":True,"summary":{"partial":True,"simulations_requested":int(task.total_units or 0),"simulations_completed":int(task.completed_units or 0),"seed":run.seed,"method":run.method,"trade_count":0},"per_simulation":[],"representative_curves":[]}
                    run.result_json=json.dumps(saved,separators=(",",":"))
                    run.status="CANCELLED";run.error=reason;run.finished_at=time.time();db.commit()
            finally:
                if db: db.close()
            task_service.finish_task(reg,task_id,"CANCELLED",error=reason,summary=f"Worker process terminated; retained {task.completed_units or 0}/{task.total_units or 0} completed simulations",completed_units=int(task.completed_units or 0))
    finally: reg.close()


def _stop_all_impl(timeout_seconds:int,reopen_gate:bool=True):
    import time
    from app import task_service
    from hero1_storage.engine import registry_session
    timeout_seconds=max(1,min(120,int(timeout_seconds)))
    task_service.set_accepting_jobs(False)
    reg=registry_session()
    try:
        requested,unsupported=_request_all_cancellations(reg)
    finally: reg.close()
    deadline=time.monotonic()+timeout_seconds
    while time.monotonic()<deadline:
        reg=registry_session()
        try:
            from hero1_storage.registry_models import DashboardTask
            task_service.sync_fetch_tasks(reg)
            active=reg.query(DashboardTask).filter(DashboardTask.status.in_(task_service.ACTIVE)).count()
            if not active: break
        finally: reg.close()
        time.sleep(.1)
    from app.api.routers import monte_carlo as mc_router
    termination=mc_router.terminate_worker_processes(grace_seconds=1.0)
    finalized=termination["terminated"]+termination["cancelled_queued"]
    _reconcile_terminated_monte_carlo(finalized,"Worker process terminated by STOP ALL after cancellation grace period")
    reg=registry_session()
    try:
        snapshot=_process_snapshot(reg)
    finally: reg.close()
    snapshot["termination"]=termination
    if snapshot["worker_count"]==0 and snapshot["worker_process_count"]==0 and reopen_gate: task_service.set_accepting_jobs(True)
    snapshot["accepting_new_jobs"]=task_service.accepting_jobs()
    snapshot.update({"cancellation_requested":requested,"unsupported_workers":unsupported,"stopped":snapshot["worker_count"]==0 and snapshot["worker_process_count"]==0 and not termination["still_alive"],"timeout_seconds":timeout_seconds})
    return snapshot


def _stop_all(timeout_seconds:int,reopen_gate:bool=True):
    # Serialize repeated/concurrent clicks so signals, joins and terminal-state
    # reconciliation remain idempotent.
    with _STOP_ALL_LOCK:
        return _stop_all_impl(timeout_seconds,reopen_gate)


@router.get("/processes")
def process_status(session: Session = Depends(get_registry_session)):
    """Read actual persisted task states and host resource metrics."""
    snapshot=_process_snapshot(session)
    snapshot["resources"]=system_metrics()
    return snapshot


@router.post("/processes/stop")
def stop_all_processes(body:ProcessAction,session:Session=Depends(get_registry_session)):
    """Cooperatively cancel only HERO1-managed jobs; never kill OS processes."""
    snapshot=_stop_all(body.timeout_seconds)
    if not snapshot["stopped"]:
        snapshot["message"]="Some HERO1 workers did not acknowledge cancellation before timeout. New jobs remain blocked; refresh status and retry. No operating-system processes were killed."
    else:
        snapshot["message"]="All supported HERO1 background jobs reached terminal states."
    return snapshot


@router.post("/shutdown")
def shutdown(body:ProcessAction,session:Session=Depends(get_registry_session)):
    """Cancel supported jobs, then request orderly server termination."""
    if body.confirm!="SHUTDOWN HERO1":
        raise HTTPException(422,"explicit confirmation text required: SHUTDOWN HERO1")
    snapshot=_stop_all(body.timeout_seconds,reopen_gate=False)
    if not snapshot["stopped"]:
        from app import task_service
        task_service.set_accepting_jobs(True)
        raise HTTPException(409,{"message":"Shutdown withheld because active work did not stop safely; inspect the returned process status and retry.","processes":snapshot})
    import os,signal,threading
    # SIGTERM is delivered to this API process only; uvicorn's signal handler
    # runs lifespan cleanup. A daemon timer lets the HTTP response finish first.
    threading.Timer(.7,lambda:os.kill(os.getpid(),signal.SIGTERM)).start()
    snapshot["message"]="Active jobs stopped; orderly backend shutdown has been requested. Restart HERO1 to use it again."
    snapshot["shutdown_requested"]=True
    return snapshot


@router.post("/data/preview")
def data_delete_preview(body:DataPreviewRequest):
    from app import data_management_service
    try:return data_management_service.preview(body.category)
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc


@router.post("/data/delete")
def data_delete(body:DataDeleteRequest):
    from app import data_management_service
    cat=body.category.upper()
    expected={"NODES":"DELETE NODES DATA","DEEPTESTING":"DELETE DEEPTESTING DATA","MONTE_CARLO":"DELETE MONTE CARLO DATA","MARKET_DATA":"DELETE MARKET DATA","LOGS":"DELETE LOGS","ALL":"DELETE ALL DATA"}.get(cat)
    if body.confirm!=expected:
        raise HTTPException(422,f"type the exact confirmation text: {expected or 'invalid category'}")
    try:return data_management_service.execute(cat)
    except RuntimeError as exc:raise HTTPException(409,str(exc)) from exc
    except Exception as exc:raise HTTPException(500,f"data deletion failed: {type(exc).__name__}: {exc}") from exc


@router.get("/metrics")
def system_metrics():
    """Return host metrics; GPU absence is explicit, never represented as 0%."""
    try:
        import psutil
        vm = psutil.virtual_memory()
        cpu = {
            "utilization_pct": psutil.cpu_percent(interval=None),
            "logical_threads": psutil.cpu_count(logical=True),
            "physical_cores": psutil.cpu_count(logical=False),
            "load_average": list(psutil.getloadavg()) if hasattr(psutil, "getloadavg") else None,
            "ram_used_bytes": int(vm.used), "ram_total_bytes": int(vm.total),
            "ram_utilization_pct": float(vm.percent),
        }
    except Exception as exc:
        cpu = {"available": False, "reason": f"CPU/RAM metrics unavailable: {type(exc).__name__}"}
    gpu = {"available": False, "reason": "No supported GPU monitoring interface is available in this API environment"}
    try:
        import pynvml
        pynvml.nvmlInit()
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
            util = pynvml.nvmlDeviceGetUtilizationRates(handle)
            name = pynvml.nvmlDeviceGetName(handle)
            if isinstance(name, bytes): name = name.decode(errors="replace")
            try: temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
            except Exception: temp = None
            gpu = {"available": True, "name": name, "utilization_pct": int(util.gpu),
                   "vram_used_bytes": int(mem.used), "vram_total_bytes": int(mem.total), "temperature_c": temp}
        finally: pynvml.nvmlShutdown()
    except Exception:
        pass
    return {"scope": "API host / LMS sandbox", "cpu_ram": cpu, "gpu": gpu}
