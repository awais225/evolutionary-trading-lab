"""Host metrics and scoped process lifecycle controls."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_registry_session

router = APIRouter(prefix="/api/v1/system", tags=["system"])


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
    return {"accepting_new_jobs":task_service.accepting_jobs(),"worker_count":len(active),"queued_jobs":counts["QUEUED"],"running_jobs":counts["RUNNING"],"counts":counts,"active_jobs":active}


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


def _stop_all(timeout_seconds:int,reopen_gate:bool=True):
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
    reg=registry_session()
    try:
        snapshot=_process_snapshot(reg)
    finally: reg.close()
    if snapshot["worker_count"]==0 and reopen_gate: task_service.set_accepting_jobs(True)
    snapshot["accepting_new_jobs"]=task_service.accepting_jobs()
    snapshot.update({"cancellation_requested":requested,"unsupported_workers":unsupported,"stopped":snapshot["worker_count"]==0,"timeout_seconds":timeout_seconds})
    return snapshot


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
