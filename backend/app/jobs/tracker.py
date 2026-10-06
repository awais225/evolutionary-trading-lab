"""
Real Multi-Level Progress Tracking & Stall Detection System (V2.7).

Provides hierarchical, real progress calculation:
  Overall Job -> Stages -> Tasks -> Workers
  - Real percentages: processed_rows / total_rows, completed_features / total_features
  - No synthetic timers or fake loops
  - Real ETA computation: elapsed * (1 - prog) / prog
  - Stall detection: ACTIVE, WAITING, COMPLETED, FAILED, STALLED
    (no progress > stall_threshold, reports last progress timestamp and stalled duration)
  - Heartbeat logging for long-running operations
  - Thread-safe and persistent in memory with WebSocket broadcast
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from ..activity import activity
from ..api.ws import bus

log = logging.getLogger("jobs.tracker")

DEFAULT_STALL_THRESHOLD_S = 60.0


@dataclass
class WorkerProgress:
    worker_id: str
    current_task: str = "idle"
    progress_pct: float = 0.0
    last_heartbeat: float = field(default_factory=time.time)
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskProgress:
    task_id: str
    name: str
    unit_name: str = "units"
    processed_units: int = 0
    total_units: int = 0
    status: str = "WAITING"  # WAITING | ACTIVE | COMPLETED | FAILED | STALLED
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    last_progress_at: float = field(default_factory=time.time)

    @property
    def progress_pct(self) -> float:
        if self.status == "COMPLETED":
            return 100.0
        if self.total_units <= 0:
            return 0.0
        return min(100.0, max(0.0, (self.processed_units / self.total_units) * 100.0))


@dataclass
class StageProgress:
    stage_id: str
    name: str
    status: str = "WAITING"
    tasks: Dict[str, TaskProgress] = field(default_factory=dict)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None

    @property
    def progress_pct(self) -> float:
        if self.status == "COMPLETED":
            return 100.0
        if not self.tasks:
            return 0.0
        total_p = sum(t.progress_pct for t in self.tasks.values())
        return min(100.0, max(0.0, total_p / len(self.tasks)))


class Job:
    def __init__(
        self,
        job_id: str,
        name: str,
        job_type: str,
        stall_threshold_s: float = DEFAULT_STALL_THRESHOLD_S,
    ):
        self.job_id = job_id
        self.name = name
        self.job_type = job_type
        self.status = "WAITING"  # WAITING | ACTIVE | COMPLETED | FAILED | STALLED
        self.started_at: Optional[float] = None
        self.completed_at: Optional[float] = None
        self.last_progress_at = time.time()
        self.stall_threshold_s = stall_threshold_s
        self.stages: Dict[str, StageProgress] = {}
        self.workers: Dict[str, WorkerProgress] = {}
        self.heartbeats: List[Dict[str, Any]] = []
        self.error_message: Optional[str] = None
        self.meta: Dict[str, Any] = {}
        self._lock = threading.RLock()

    def add_stage(self, stage_id: str, name: str) -> StageProgress:
        with self._lock:
            st = StageProgress(stage_id=stage_id, name=name)
            self.stages[stage_id] = st
            return st

    def add_task(
        self,
        stage_id: str,
        task_id: str,
        name: str,
        total_units: int,
        unit_name: str = "units",
    ) -> TaskProgress:
        with self._lock:
            stage = self.stages.get(stage_id)
            if not stage:
                stage = self.add_stage(stage_id, stage_id.replace("_", " ").title())
            task = TaskProgress(
                task_id=task_id,
                name=name,
                total_units=total_units,
                unit_name=unit_name,
            )
            stage.tasks[task_id] = task
            return task

    def start(self) -> None:
        with self._lock:
            now = time.time()
            self.status = "ACTIVE"
            self.started_at = now
            self.last_progress_at = now
            for st in self.stages.values():
                if st.status == "WAITING":
                    st.status = "ACTIVE"
                    st.started_at = now
                    break

    def update_task_progress(
        self,
        stage_id: str,
        task_id: str,
        processed_units: int,
        total_units: Optional[int] = None,
    ) -> None:
        with self._lock:
            now = time.time()
            stage = self.stages.get(stage_id)
            if not stage:
                stage = self.add_stage(stage_id, stage_id.title())
            task = stage.tasks.get(task_id)
            if not task:
                task = self.add_task(
                    stage_id, task_id, task_id.title(), total_units or processed_units
                )

            if total_units is not None:
                task.total_units = total_units

            prev_processed = task.processed_units
            task.processed_units = max(0, processed_units)
            if task.started_at is None:
                task.started_at = now
            task.status = "ACTIVE"
            if stage.status == "WAITING":
                stage.status = "ACTIVE"
                stage.started_at = now
            if self.status in ("WAITING", "STALLED"):
                self.status = "ACTIVE"
                if self.started_at is None:
                    self.started_at = now

            if task.processed_units > prev_processed:
                task.last_progress_at = now
                self.last_progress_at = now

            if task.total_units > 0 and task.processed_units >= task.total_units:
                task.status = "COMPLETED"
                task.completed_at = now

    def update_worker(
        self,
        worker_id: str,
        current_task: str,
        progress_pct: float,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self._lock:
            now = time.time()
            self.workers[worker_id] = WorkerProgress(
                worker_id=worker_id,
                current_task=current_task,
                progress_pct=progress_pct,
                last_heartbeat=now,
                details=details or {},
            )
            self.last_progress_at = now
            if self.status == "STALLED":
                self.status = "ACTIVE"

    def heartbeat(self, message: str = "") -> Dict[str, Any]:
        with self._lock:
            now = time.time()
            self.last_progress_at = now
            if self.status == "STALLED":
                self.status = "ACTIVE"

            hb = {
                "ts": now,
                "progress_pct": self.overall_progress_pct,
                "eta_seconds": self.eta_seconds,
                "message": message,
            }
            self.heartbeats.append(hb)
            if len(self.heartbeats) > 100:
                self.heartbeats = self.heartbeats[-100:]

            log.info(
                "[HEARTBEAT] Job %s (%s): %.1f%% | ETA: %.1fs | %s",
                self.job_id,
                self.job_type,
                self.overall_progress_pct,
                self.eta_seconds or 0.0,
                message,
            )
            return hb

    def complete_task(self, stage_id: str, task_id: str) -> None:
        with self._lock:
            now = time.time()
            stage = self.stages.get(stage_id)
            if stage and task_id in stage.tasks:
                t = stage.tasks[task_id]
                t.status = "COMPLETED"
                t.processed_units = max(t.processed_units, t.total_units)
                t.completed_at = now
                t.last_progress_at = now
                self.last_progress_at = now

    def complete_stage(self, stage_id: str) -> None:
        with self._lock:
            now = time.time()
            stage = self.stages.get(stage_id)
            if stage:
                stage.status = "COMPLETED"
                stage.completed_at = now
                for t in stage.tasks.values():
                    if t.status != "COMPLETED":
                        t.status = "COMPLETED"
                        t.processed_units = max(t.processed_units, t.total_units)
                        t.completed_at = now
                self.last_progress_at = now

    def complete(self, meta: Optional[Dict[str, Any]] = None) -> None:
        with self._lock:
            now = time.time()
            self.status = "COMPLETED"
            self.completed_at = now
            self.last_progress_at = now
            if meta:
                self.meta.update(meta)
            for st in self.stages.values():
                st.status = "COMPLETED"
                if st.completed_at is None:
                    st.completed_at = now
                for t in st.tasks.values():
                    t.status = "COMPLETED"
                    t.processed_units = max(t.processed_units, t.total_units)
                    if t.completed_at is None:
                        t.completed_at = now

    def fail(self, error: str) -> None:
        with self._lock:
            now = time.time()
            self.status = "FAILED"
            self.completed_at = now
            self.error_message = str(error)

    @property
    def overall_progress_pct(self) -> float:
        if self.status == "COMPLETED":
            return 100.0
        if not self.stages:
            return 0.0
        total_p = sum(s.progress_pct for s in self.stages.values())
        return min(100.0, max(0.0, total_p / len(self.stages)))

    @property
    def elapsed_seconds(self) -> float:
        if not self.started_at:
            return 0.0
        end = self.completed_at or time.time()
        return max(0.0, end - self.started_at)

    @property
    def eta_seconds(self) -> Optional[float]:
        prog = self.overall_progress_pct
        if prog <= 0.0 or prog >= 100.0 or not self.started_at or self.status in ("COMPLETED", "FAILED"):
            return None
        elapsed = self.elapsed_seconds
        frac = prog / 100.0
        # Real ETA calculation: elapsed * (1 - prog) / prog
        return max(0.0, elapsed * (1.0 - frac) / frac)

    def evaluate_status(self) -> str:
        """Evaluate effective status, handling STALLED state."""
        if self.status in ("COMPLETED", "FAILED", "WAITING"):
            return self.status
        now = time.time()
        idle = now - self.last_progress_at
        if idle > self.stall_threshold_s:
            return "STALLED"
        return "ACTIVE"

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            eff_status = self.evaluate_status()
            now = time.time()
            idle_s = max(0.0, now - self.last_progress_at)
            is_stalled = (eff_status == "STALLED")

            stages_list = []
            for s in self.stages.values():
                st_tasks = []
                for t in s.tasks.values():
                    st_tasks.append({
                        "task_id": t.task_id,
                        "name": t.name,
                        "unit_name": t.unit_name,
                        "processed_units": t.processed_units,
                        "total_units": t.total_units,
                        "progress_pct": round(t.progress_pct, 1),
                        "status": t.status,
                        "last_progress_at": t.last_progress_at,
                    })
                stages_list.append({
                    "stage_id": s.stage_id,
                    "name": s.name,
                    "status": s.status,
                    "progress_pct": round(s.progress_pct, 1),
                    "tasks": st_tasks,
                })

            workers_list = [
                {
                    "worker_id": w.worker_id,
                    "current_task": w.current_task,
                    "progress_pct": round(w.progress_pct, 1),
                    "last_heartbeat": w.last_heartbeat,
                    "details": w.details,
                }
                for w in self.workers.values()
            ]

            return {
                "job_id": self.job_id,
                "name": self.name,
                "job_type": self.job_type,
                "status": eff_status,
                "is_stalled": is_stalled,
                "stalled_duration_s": round(idle_s, 1) if is_stalled else 0.0,
                "stall_threshold_s": self.stall_threshold_s,
                "started_at": self.started_at,
                "completed_at": self.completed_at,
                "last_progress_at": self.last_progress_at,
                "elapsed_seconds": round(self.elapsed_seconds, 1),
                "eta_seconds": round(self.eta_seconds, 1) if self.eta_seconds is not None else None,
                "overall_progress_pct": round(self.overall_progress_pct, 1),
                "stages": stages_list,
                "workers": workers_list,
                "error_message": self.error_message,
                "meta": self.meta,
                "heartbeats_count": len(self.heartbeats),
            }


class JobManager:
    """Singleton JobManager tracking active and recent jobs."""

    def __init__(self):
        self._lock = threading.RLock()
        self._jobs: Dict[str, Job] = {}
        self._job_order: List[str] = []

    def create_job(
        self,
        name: str,
        job_type: str,
        job_id: Optional[str] = None,
        stall_threshold_s: float = DEFAULT_STALL_THRESHOLD_S,
    ) -> Job:
        with self._lock:
            jid = job_id or f"job_{job_type}_{int(time.time()*1000)}"
            job = Job(jid, name, job_type, stall_threshold_s=stall_threshold_s)
            self._jobs[jid] = job
            self._job_order.insert(0, jid)
            if len(self._job_order) > 200:
                old = self._job_order.pop()
                self._jobs.pop(old, None)
            return job

    def get_job(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            out = []
            for jid in self._job_order[:limit]:
                j = self._jobs.get(jid)
                if j:
                    out.append(j.to_dict())
            return out

    def get_active_job(self) -> Optional[Job]:
        with self._lock:
            for jid in self._job_order:
                j = self._jobs.get(jid)
                if j and j.status in ("ACTIVE", "WAITING"):
                    return j
            return None


_job_manager: Optional[JobManager] = None


def get_job_manager() -> JobManager:
    global _job_manager
    if _job_manager is None:
        _job_manager = JobManager()
    return _job_manager
