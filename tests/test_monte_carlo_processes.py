"""OS-boundary tests for STOP ALL escalation; all state is disposable."""
from __future__ import annotations

import os
import signal
import time
from pathlib import Path

from app.api.routers import monte_carlo as mc_router
from app import task_service
from hero1_storage.engine import registry_session
from hero1_storage.registry_models import DashboardTask


def ignore_soft_termination(ready_file: str) -> None:
    """An intentionally uncooperative child; only SIGKILL can stop this loop."""
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    Path(ready_file).write_text(str(os.getpid()))
    while True:
        time.sleep(0.05)


def test_stop_all_kills_multiple_uncooperative_os_workers_and_reopens_only_after_exit(client, tmp_path):
    ready_files=[]
    task_ids=[]
    reg=registry_session()
    try:
        for index in range(2):
            task=task_service.create_task(reg,category="MONTE_CARLO",instrument="XAUUSD",
                label=f"uncooperative {index}",total_units=100,status="RUNNING",
                payload={"node_id":index+1,"method":"START_POINT"})
            task.completed_units=4
            reg.commit()
            path=tmp_path/f"uncooperative-{index}.pid"
            mc_router._register_worker_process(task.id,ignore_soft_termination,(str(path),))
            task_ids.append(task.id);ready_files.append(path)
    finally:
        reg.close()
    deadline=time.monotonic()+10
    while time.monotonic()<deadline and not all(p.exists() for p in ready_files):
        time.sleep(.02)
    assert all(p.exists() for p in ready_files),"workers did not start as OS processes"
    pids=[int(p.read_text()) for p in ready_files]
    assert all(pid != os.getpid() for pid in pids)
    assert len(pids)==2 and len(set(pids))==2

    response=client.post("/api/v1/system/processes/stop",json={"timeout_seconds":1})
    assert response.status_code==200,response.text
    stopped=response.json()
    assert stopped["stopped"] is True,stopped
    assert stopped["worker_process_count"]==0,stopped
    assert stopped["termination"]["force_killed"]==task_ids
    assert stopped["termination"]["still_alive"]==[]
    assert stopped["accepting_new_jobs"] is True
    for pid in pids:
        try:
            os.kill(pid,0)
        except ProcessLookupError:
            pass
        else:
            raise AssertionError(f"worker PID {pid} still exists after STOP ALL")
    assert client.get("/api/v1/system/processes").status_code==200
    reg=registry_session()
    try:
        for task_id in task_ids:
            task=reg.get(DashboardTask,task_id)
            assert task.status=="CANCELLED"
            assert task.completed_units==4
            assert "terminated" in task.error.lower()
    finally:
        reg.close()
    # Idempotent empty STOP ALL leaves API/admission healthy.
    again=client.post("/api/v1/system/processes/stop",json={"timeout_seconds":1}).json()
    assert again["stopped"] and again["worker_process_count"]==0 and again["accepting_new_jobs"] is True
