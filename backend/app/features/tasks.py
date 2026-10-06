"""
Feature Task State Machine & Watchdog (V2.7 Repair).

Maintains authoritative task states:
  QUEUED -> RUNNING -> COMPLETED | REUSED | SKIPPED | FAILED | STALLED | CANCELLED
With:
  - Real progress: completed_features / total_features (no fake progress)
  - Individual feature progress & timings
  - Periodic heartbeat (task, feature, elapsed, rows, worker, CPU, GPU, RAM)
  - Multi-stage watchdog & stall detection (STALL CHECK -> STALLED)
  - Duplicate task prevention (locking per dataset + feature set)
  - Stop/start cleanup safety
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import psutil

from ..activity import activity
from ..api.ws import bus

log = logging.getLogger("features.tasks")

VALID_STATES = (
    "QUEUED",
    "RUNNING",
    "COMPLETED",
    "REUSED",
    "SKIPPED",
    "FAILED",
    "STALLED",
    "CANCELLED",
)


@dataclass
class SingleFeatureProgress:
    name: str
    status: str = "QUEUED"  # QUEUED, RUNNING, COMPLETED, FAILED, REUSED
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    elapsed_s: float = 0.0
    rows: int = 0
    error: Optional[str] = None


class FeatureTask:
    def __init__(
        self,
        task_id: str,
        dataset_id: str,
        timeframe: str,
        total_features: int,
        feature_specs: List[str],
        feature_set_id: str = "core_v1",
        schema_version: str = "core_v1",
        worker_id: str = "worker-1",
        stall_threshold_s: float = 45.0,
    ):
        self.task_id = task_id
        self.dataset_id = dataset_id
        self.timeframe = timeframe
        self.feature_set_id = feature_set_id
        self.schema_version = schema_version
        self.worker_id = worker_id
        self.status = "QUEUED"
        self.total_features = total_features
        self.completed_features = 0
        self.current_feature: Optional[str] = None
        self.started_at: Optional[float] = None
        self.completed_at: Optional[float] = None
        self.last_heartbeat = time.time()
        self.stall_threshold_s = stall_threshold_s
        self.error: Optional[str] = None
        self.features: Dict[str, SingleFeatureProgress] = {
            spec: SingleFeatureProgress(name=spec) for spec in feature_specs
        }
        self.rows_total = 0
        self.rows_processed = 0
        self.correlation_id = f"corr-{uuid.uuid4().hex[:8]}"
        self._lock = threading.RLock()
        self._last_logged_hb = 0.0

    @property
    def progress_pct(self) -> float:
        if self.status in ("COMPLETED", "REUSED"):
            return 100.0
        if self.total_features <= 0:
            return 0.0
        return min(100.0, round((self.completed_features / self.total_features) * 100.0, 1))

    @property
    def elapsed_seconds(self) -> float:
        if not self.started_at:
            return 0.0
        end = self.completed_at or time.time()
        return round(max(0.0, end - self.started_at), 1)

    def start(self, rows_total: int = 0) -> None:
        with self._lock:
            now = time.time()
            self.status = "RUNNING"
            self.started_at = now
            self.last_heartbeat = now
            self.rows_total = rows_total
            log.info(
                "[FEATURE] Task %s (%s %s) started on %s (%d features)",
                self.task_id, self.dataset_id, self.timeframe, self.worker_id, self.total_features
            )

    def start_feature(self, feature_name: str) -> None:
        with self._lock:
            now = time.time()
            self.current_feature = feature_name
            self.last_heartbeat = now
            if feature_name in self.features:
                f_prog = self.features[feature_name]
                f_prog.status = "RUNNING"
                f_prog.started_at = now

            self._broadcast_task_update()

    def complete_feature(self, feature_name: str, rows: int = 0) -> None:
        with self._lock:
            now = time.time()
            self.last_heartbeat = now
            if feature_name in self.features:
                f_prog = self.features[feature_name]
                f_prog.status = "COMPLETED"
                f_prog.completed_at = now
                if f_prog.started_at:
                    f_prog.elapsed_s = round(now - f_prog.started_at, 2)
                f_prog.rows = rows

            self.completed_features = min(self.total_features, self.completed_features + 1)
            self.rows_processed = rows
            self._broadcast_task_update()

    def fail_feature(self, feature_name: str, error: str) -> None:
        with self._lock:
            now = time.time()
            self.last_heartbeat = now
            if feature_name in self.features:
                f_prog = self.features[feature_name]
                f_prog.status = "FAILED"
                f_prog.completed_at = now
                f_prog.error = str(error)

    def heartbeat(self, rows_processed: Optional[int] = None) -> Dict[str, Any]:
        """Emit periodic heartbeat with real system resource and progress snapshot."""
        with self._lock:
            now = time.time()
            self.last_heartbeat = now
            if rows_processed is not None:
                self.rows_processed = rows_processed

            if self.status == "STALLED":
                self.status = "RUNNING"
                log.info("[WATCHDOG] Task %s recovered from STALLED to RUNNING", self.task_id)

            from ..resources.manager import get_resource_manager
            rm = get_resource_manager()
            live = rm.live_metrics()
            cpu_pct = live["cpu"]["percent"]
            ram_gb = round(live["memory"]["used_mb"] / 1024.0, 1)

            # Accurate GPU processing status (never fake utilization)
            gpu_enabled = live["gpu"]["enabled"]
            gpu_avail = live["gpu"]["available"]
            gpu_proc = "ACTIVE" if (gpu_enabled and gpu_avail and live["gpu"]["utilization_pct"] > 0) else "IDLE"

            hb_data = {
                "task_id": self.task_id,
                "dataset_id": self.dataset_id,
                "timeframe": self.timeframe,
                "feature": self.current_feature,
                "progress_pct": self.progress_pct,
                "completed": self.completed_features,
                "total": self.total_features,
                "elapsed_s": self.elapsed_seconds,
                "rows_processed": self.rows_processed,
                "rows_total": self.rows_total,
                "worker_id": self.worker_id,
                "cpu_percent": cpu_pct,
                "gpu_processing": gpu_proc,
                "ram_gb": ram_gb,
                "ts": now,
            }

            # Throttle log emission to once per 2 seconds
            if now - self._last_logged_hb >= 2.0:
                self._last_logged_hb = now
                log.debug(
                    "[HEARTBEAT] Task %s: %s | %d/%d (%.1f%%) | Worker: %s | CPU: %.1f%% | GPU: %s | RAM: %.1f GB",
                    self.task_id, self.current_feature or "prep", self.completed_features,
                    self.total_features, self.progress_pct, self.worker_id, cpu_pct, gpu_proc, ram_gb
                )

            return hb_data

    def check_stall(self) -> Tuple[bool, float]:
        """Check if task has stalled (no progress for > threshold)."""
        with self._lock:
            if self.status not in ("RUNNING", "STALLED"):
                return False, 0.0
            now = time.time()
            idle_s = now - self.last_heartbeat
            if idle_s > self.stall_threshold_s:
                if self.status != "STALLED":
                    self.status = "STALLED"
                    log.warning(
                        "[WATCHDOG] STALL DETECTED: Task %s (%s %s) inactive for %.1fs (feature: %s, worker: %s)",
                        self.task_id, self.dataset_id, self.timeframe, idle_s, self.current_feature, self.worker_id
                    )
                    activity.warning(
                        "FEATURES",
                        f"Task {self.task_id} STALLED: {self.current_feature} inactive for {idle_s:.1f}s",
                        operation_id=self.task_id
                    )
                    self._broadcast_task_update()
                return True, idle_s
            return False, idle_s

    def complete(self, reused: bool = False) -> None:
        with self._lock:
            now = time.time()
            self.status = "REUSED" if reused else "COMPLETED"
            self.completed_at = now
            self.last_heartbeat = now
            self.completed_features = self.total_features
            self.current_feature = None
            log.info(
                "[FEATURE] Task %s (%s %s) %s in %.1fs",
                self.task_id, self.dataset_id, self.timeframe, self.status, self.elapsed_seconds
            )
            self._broadcast_task_update()

    def fail(self, error_message: str) -> None:
        with self._lock:
            now = time.time()
            self.status = "FAILED"
            self.completed_at = now
            self.last_heartbeat = now
            self.error = str(error_message)
            log.error("[FEATURE] Task %s (%s) FAILED: %s", self.task_id, self.dataset_id, error_message)
            self._broadcast_task_update()

    def cancel(self, reason: str = "cancelled") -> None:
        with self._lock:
            now = time.time()
            self.status = "CANCELLED"
            self.completed_at = now
            self.error = reason
            log.info("[FEATURE] Task %s (%s) CANCELLED: %s", self.task_id, self.dataset_id, reason)
            self._broadcast_task_update()

    def _broadcast_task_update(self) -> None:
        bus.publish("feature_task_update", self.to_dict())

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            now = time.time()
            idle_s = round(max(0.0, now - self.last_heartbeat), 1)

            from ..resources.manager import get_resource_manager
            rm = get_resource_manager()
            live = rm.live_metrics()
            gpu_proc = "ACTIVE" if (live["gpu"]["enabled"] and live["gpu"]["available"] and live["gpu"]["utilization_pct"] > 0) else "IDLE"

            return {
                "task_id": self.task_id,
                "dataset_id": self.dataset_id,
                "timeframe": self.timeframe,
                "feature_set_id": self.feature_set_id,
                "schema_version": self.schema_version,
                "worker_id": self.worker_id,
                "status": self.status,
                "progress_pct": self.progress_pct,
                "completed_features": self.completed_features,
                "total_features": self.total_features,
                "current_feature": self.current_feature,
                "elapsed_seconds": self.elapsed_seconds,
                "last_heartbeat_ago_s": idle_s,
                "rows_processed": self.rows_processed,
                "rows_total": self.rows_total,
                "error": self.error,
                "correlation_id": self.correlation_id,
                "cpu_percent": live["cpu"]["percent"],
                "gpu_processing": gpu_proc,
                "gpu_enabled": live["gpu"]["enabled"],
                "ram_gb": round(live["memory"]["used_mb"] / 1024.0, 1),
            }


class FeatureTaskManager:
    """Singleton task manager ensuring single task per dataset, watchdog, and tracking."""

    def __init__(self):
        self._lock = threading.RLock()
        self._tasks: Dict[str, FeatureTask] = {}
        self._dataset_locks: Dict[str, threading.Lock] = {}
        self._task_counter = 0

    def create_or_attach(
        self,
        dataset_id: str,
        timeframe: str,
        feature_specs: List[str],
        worker_id: str = "worker-1",
        stall_threshold_s: float = 45.0,
    ) -> Tuple[FeatureTask, bool]:
        """Create a new feature task or attach to an existing active task."""
        with self._lock:
            # Check if active task already exists for this dataset
            for t in self._tasks.values():
                if t.dataset_id == dataset_id and t.status in ("RUNNING", "QUEUED"):
                    log.info(
                        "[FEATURE] Attaching to existing active task %s for %s",
                        t.task_id, dataset_id
                    )
                    return t, False

            self._task_counter += 1
            tid = f"FP-{self._task_counter:06d}"
            task = FeatureTask(
                task_id=tid,
                dataset_id=dataset_id,
                timeframe=timeframe,
                total_features=len(feature_specs),
                feature_specs=feature_specs,
                worker_id=worker_id,
                stall_threshold_s=stall_threshold_s,
            )
            self._tasks[tid] = task
            return task, True

    def get_task(self, task_id: str) -> Optional[FeatureTask]:
        with self._lock:
            return self._tasks.get(task_id)

    def get_active_task(self) -> Optional[FeatureTask]:
        with self._lock:
            for t in reversed(list(self._tasks.values())):
                if t.status in ("RUNNING", "QUEUED", "STALLED"):
                    return t
            return None

    def list_tasks(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            out = []
            for t in reversed(list(self._tasks.values())):
                out.append(t.to_dict())
                if len(out) >= limit:
                    break
            return out

    def check_all_stalls(self) -> List[Dict[str, Any]]:
        """Watchdog check across all running tasks."""
        with self._lock:
            stalled = []
            for t in self._tasks.values():
                is_stalled, idle_s = t.check_stall()
                if is_stalled:
                    stalled.append({"task_id": t.task_id, "dataset_id": t.dataset_id, "idle_s": idle_s})
            return stalled

    def cancel_all_active(self, reason: str = "Research loop stopped") -> None:
        """Safe cleanup when research loop stops."""
        with self._lock:
            for t in self._tasks.values():
                if t.status in ("RUNNING", "QUEUED", "STALLED"):
                    t.cancel(reason)


_feature_task_mgr: Optional[FeatureTaskManager] = None


def get_feature_task_manager() -> FeatureTaskManager:
    global _feature_task_mgr
    if _feature_task_mgr is None:
        _feature_task_mgr = FeatureTaskManager()
    return _feature_task_mgr
