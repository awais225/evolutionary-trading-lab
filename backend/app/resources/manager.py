"""
Computational Resource Enforcement & Monitoring (spec §26, §27, §28, §29, §30, §31, §32).

Actual computational limits (not cosmetic):
  - CPU worker limiting for ProcessPool
  - Dynamic memory budget check before scheduling heavy batches
  - GPU initialization guard with safe CPU fallback (spec §31)
  - Live system metrics (CPU %, RAM used/limit, GPU/VRAM, active workers)
  - V3.5 Phase 2: Per-core CPU, Process CPU/RAM telemetry, dynamic 25/50/75/100% scaling, throughput tracking
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import psutil

from ..activity import activity
from ..config import get_config

log = logging.getLogger("resources.manager")


class ResourceManager:
    def __init__(self):
        self._lock = threading.RLock()
        self._gpu_checked = False
        self._gpu_available = False
        self._gpu_name = "None"
        self._gpu_vram_total_mb = 0.0
        self._gpu_vram_used_mb = 0.0
        self._gpu_utilization_pct = 0.0
        self._active_workers = 0
        self._queued_jobs = 0
        self._running_jobs = 0

        # V3.5 Phase 2: Application Process & Task Telemetry
        self._proc: Optional[psutil.Process] = None
        self._peak_process_ram_mb = 0.0
        self._completed_tasks = 0
        self._failed_tasks = 0
        self._task_durations: List[float] = []
        self._recent_completions: List[float] = []

    # ---------- Process & Task Telemetry (V3.5 Phase 2) ----------
    def _inspect_process_metrics(self) -> Tuple[float, float]:
        """Measure current application process and worker subprocess CPU % and RSS RAM in MB."""
        try:
            if self._proc is None:
                self._proc = psutil.Process()
            cpu = self._proc.cpu_percent(interval=None)
            mem_rss = self._proc.memory_info().rss
            for child in self._proc.children(recursive=True):
                try:
                    cpu += child.cpu_percent(interval=None)
                    mem_rss += child.memory_info().rss
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            mem_mb = round(mem_rss / (1024 ** 2), 1)
            cpu_pct = round(cpu, 1)
            with self._lock:
                if mem_mb > self._peak_process_ram_mb:
                    self._peak_process_ram_mb = mem_mb
            return cpu_pct, mem_mb
        except Exception:
            return 0.0, 0.0

    def record_task_completion(self, duration_s: float, success: bool = True) -> None:
        """Record completed backtest task duration and status for throughput tracking."""
        now = time.time()
        with self._lock:
            if success:
                self._completed_tasks += 1
            else:
                self._failed_tasks += 1
            self._task_durations.append(duration_s)
            if len(self._task_durations) > 200:
                self._task_durations.pop(0)
            self._recent_completions.append(now)
            cutoff = now - 60.0
            while self._recent_completions and self._recent_completions[0] < cutoff:
                self._recent_completions.pop(0)

    def tasks_per_minute(self) -> int:
        """Number of tasks completed in the trailing 60 seconds."""
        now = time.time()
        cutoff = now - 60.0
        with self._lock:
            while self._recent_completions and self._recent_completions[0] < cutoff:
                self._recent_completions.pop(0)
            return len(self._recent_completions)

    def avg_task_duration_ms(self) -> float:
        """Average backtest task execution duration in milliseconds."""
        with self._lock:
            if not self._task_durations:
                return 0.0
            return round((sum(self._task_durations) / len(self._task_durations)) * 1000.0, 1)

    # ---------- CPU & Worker limits (spec §27, V2.7, V3.5 Phase 2) ----------
    def effective_workers(self) -> int:
        """Enforces configured CPU target utilization (10%-100%) and dynamically sizes worker pool.

        Proportionally scales workers based on detected logical cores:
          - 100%: 100% of logical cores
          - 75%: round(cores * 0.75) (at least 1)
          - 50%: round(cores * 0.50) (at least 1)
          - 25%: round(cores * 0.25) (at least 1)
        """
        cfg = get_config()
        cores = os.cpu_count() or 2
        target_pct = getattr(cfg.resources, "cpu_target_pct", 60)

        if target_pct >= 100:
            target_workers = cores
        else:
            target_workers = max(1, round(cores * (target_pct / 100.0)))

        if cfg.resources.cpu_limit_enabled:
            configured_max = cfg.resources.cpu_workers_max
            if configured_max and configured_max > 0:
                eff = max(1, min(configured_max, target_workers, cores))
            else:
                eff = max(1, min(target_workers, cores))
        else:
            eff = max(1, min(target_workers, cores))

        # Memory pressure safeguard: if available RAM drops below 150MB, cap concurrency
        try:
            vmem = psutil.virtual_memory()
            if vmem.available < 150 * 1024 * 1024 and eff > 1:
                eff = 1
                log.warning("Memory pressure safeguard active: limiting workers to 1 (available: %d MB)",
                            vmem.available // (1024 ** 2))
        except Exception:
            pass

        # Keep configured evolution workers in sync with dynamically sized workers
        cfg.evolution.workers = eff
        return eff

    def set_cpu_target(self, target_pct: int) -> Dict[str, Any]:
        """Update CPU utilization target (10-100%) and dynamically resize worker pool without interrupting checkpoints."""
        target_pct = int(target_pct)
        if target_pct < 10 or target_pct > 100:
            raise ValueError(f"CPU target must be between 10% and 100%, got {target_pct}")
        target_pct = max(10, min(100, target_pct))

        from ..config import get_config, save_config
        cfg = get_config()
        cfg.resources.cpu_target_pct = target_pct
        eff = self.effective_workers()
        cfg.evolution.workers = eff
        save_config(cfg)

        # Dynamically resize orchestrator pool if lab is active
        try:
            from ..orchestrator.lab import get_lab
            lab = get_lab()
            if hasattr(lab, "resize_pool"):
                lab.resize_pool()
        except Exception:
            pass

        msg = f"ACTION: CPU target set to {target_pct}%, worker pool dynamically resized to {eff} workers"
        log.info(msg)
        activity.info("RESOURCE", msg)

        # Log to persistent benchmark diagnostic log
        try:
            from .. import paths as P
            benchmark_log = P.DATA_LOGS_DIR / "CPU_BENCHMARK_V3_5.log"
            entry = {
                "ts": time.time(),
                "time": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
                "event": "CPU_TARGET_CHANGED",
                "target_pct": target_pct,
                "effective_workers": eff,
                "cores": os.cpu_count() or 2,
            }
            with open(benchmark_log, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass

        return {"cpu_target_pct": target_pct, "effective_workers": eff}

    def set_gpu_enabled(self, enabled: bool) -> Dict[str, Any]:
        """Persisted GPU acceleration toggle (ON/OFF) with hardware probe & CPU fallback."""
        from ..config import get_config, save_config
        cfg = get_config()
        cfg.resources.gpu_enabled = bool(enabled)
        save_config(cfg)

        with self._lock:
            self._gpu_checked = False
            if not enabled:
                self._gpu_available = False
                self._gpu_name = "Disabled in Settings"
                self._gpu_utilization_pct = 0.0
                self._gpu_vram_used_mb = 0.0
                msg = "ACTION: GPU acceleration disabled (CPU-only mode)"
                log.info(msg)
                activity.info("GPU", msg)
            else:
                self._inspect_gpu(force=True)
                msg = f"ACTION: GPU acceleration enabled (status: {self._gpu_name})"
                log.info(msg)
                activity.info("GPU", msg)

        return {
            "gpu_enabled": cfg.resources.gpu_enabled,
            "gpu_available": self._gpu_available,
            "gpu_name": self._gpu_name,
        }

    def update_job_counts(self, running: int, queued: int, active_workers: int) -> None:
        with self._lock:
            self._running_jobs = running
            self._queued_jobs = queued
            self._active_workers = active_workers

    # ---------- Memory limit enforcement (spec §28) ----------
    def memory_budget_bytes(self) -> int:
        """Calculate user configured memory limit in bytes."""
        cfg = get_config()
        total_ram = psutil.virtual_memory().total
        if not cfg.resources.memory_limit_enabled:
            return total_ram

        val = cfg.resources.memory_limit_value
        mode = cfg.resources.memory_limit_mode
        if mode == "pct":
            pct = max(10.0, min(val, 95.0))
            return int(total_ram * (pct / 100.0))
        else:  # "gb"
            gb = max(1.0, val)
            return int(gb * 1024 * 1024 * 1024)

    def check_memory_budget(self, estimated_mb: float = 10.0) -> bool:
        """Check if executing a workload would exceed the configured memory budget.

        Returns False if system should throttle or pause rather than crash.
        """
        cfg = get_config()
        vmem = psutil.virtual_memory()

        # OOM safeguard: if system available RAM is dangerously low (< 100MB), throttle batch
        if vmem.available < 100 * 1024 * 1024:
            activity.warning("RESOURCE",
                             f"Critical low memory safeguard ({vmem.available // (1024**2)}MB available) — queuing batch")
            return False

        if not cfg.resources.memory_limit_enabled:
            return True

        budget = self.memory_budget_bytes()
        projected = vmem.used + int(estimated_mb * 1024 * 1024)

        if projected > budget:
            activity.warning("RESOURCE",
                             f"Memory budget reached ({vmem.used // (1024**2)}MB used, "
                             f"budget {budget // (1024**2)}MB) — queueing batch")
            return False
        return True

    # ---------- GPU detection & fallback (spec §29, §30, §31) ----------
    def _inspect_gpu(self, force: bool = False) -> None:
        """Safely probe for NVIDIA GPU via nvidia-smi without crashing (spec §31)."""
        if self._gpu_checked and not force:
            return
        self._gpu_checked = True

        cfg = get_config()
        if not cfg.resources.gpu_enabled:
            self._gpu_available = False
            self._gpu_name = "Disabled in Settings"
            return

        smi = shutil.which("nvidia-smi")
        if not smi:
            self._gpu_available = False
            self._gpu_name = "Not detected (CPU fallback)"
            log.info("nvidia-smi not found: GPU disabled, using CPU")
            return

        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,utilization.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2
            )
            if out.returncode == 0 and out.stdout.strip():
                line = out.stdout.strip().split("\n")[0]
                parts = [p.strip() for p in line.split(",")]
                self._gpu_name = parts[0]
                self._gpu_vram_total_mb = float(parts[1]) if len(parts) > 1 else 0.0
                self._gpu_vram_used_mb = float(parts[2]) if len(parts) > 2 else 0.0
                self._gpu_utilization_pct = float(parts[3]) if len(parts) > 3 else 0.0
                self._gpu_available = True
                activity.success("GPU", f"GPU initialized: {self._gpu_name} ({int(self._gpu_vram_total_mb)}MB VRAM)")
            else:
                self._gpu_available = False
                self._gpu_name = "Unavailable (CPU fallback)"
        except Exception as e:
            self._gpu_available = False
            self._gpu_name = "Unavailable (CPU fallback)"
            activity.warning("GPU", f"GPU initialization failed: {e} — falling back to CPU")

    def live_metrics(self) -> Dict[str, Any]:
        """Live resource monitor snapshot (spec §32, V2.7, V3.5 Phase 2)."""
        cfg = get_config()
        vmem = psutil.virtual_memory()
        cores_phys = psutil.cpu_count(logical=False) or 1
        cores_log = psutil.cpu_count(logical=True) or 2
        eff_workers = self.effective_workers()
        target_pct = getattr(cfg.resources, "cpu_target_pct", 60)

        # Process-level telemetry
        app_cpu_pct, app_mem_mb = self._inspect_process_metrics()

        # Per-core utilization
        try:
            per_core = [round(c, 1) for c in (psutil.cpu_percent(interval=None, percpu=True) or [])]
        except Exception:
            per_core = []

        # Update GPU
        self._inspect_gpu()

        budget_bytes = self.memory_budget_bytes()
        ram_limit_gb = round(budget_bytes / (1024 ** 3), 1)

        active_workers = min(self._active_workers, eff_workers)
        idle_workers = max(0, eff_workers - active_workers)

        return {
            "cpu": {
                "percent": round(psutil.cpu_percent(interval=None), 1),
                "app_percent": app_cpu_pct,
                "per_core": per_core,
                "target_pct": target_pct,
                "physical_cores": cores_phys,
                "logical_processors": cores_log,
                "configured_workers": cfg.evolution.workers,
                "effective_workers": eff_workers,
                "active_workers": active_workers,
                "idle_workers": idle_workers,
                "limit_enabled": cfg.resources.cpu_limit_enabled,
            },
            "memory": {
                "used_mb": round(vmem.used / (1024 ** 2), 1),
                "total_mb": round(vmem.total / (1024 ** 2), 1),
                "available_mb": round(vmem.available / (1024 ** 2), 1),
                "percent": round(vmem.percent, 1),
                "app_used_mb": app_mem_mb,
                "app_peak_mb": self._peak_process_ram_mb,
                "limit_gb": ram_limit_gb,
                "limit_enabled": cfg.resources.memory_limit_enabled,
                "mode": cfg.resources.memory_limit_mode,
                "value": cfg.resources.memory_limit_value,
            },
            "gpu": {
                "enabled": cfg.resources.gpu_enabled,
                "available": self._gpu_available,
                "name": self._gpu_name,
                "utilization_pct": self._gpu_utilization_pct if (cfg.resources.gpu_enabled and self._gpu_available) else 0.0,
                "vram_used_mb": round(self._gpu_vram_used_mb, 1) if (cfg.resources.gpu_enabled and self._gpu_available) else 0.0,
                "vram_total_mb": round(self._gpu_vram_total_mb, 1) if (cfg.resources.gpu_enabled and self._gpu_available) else 0.0,
                "workload_limit_pct": cfg.resources.gpu_workload_limit_pct,
            },
            "workers": {
                "configured": cfg.evolution.workers,
                "effective": eff_workers,
                "active": active_workers,
                "idle": idle_workers,
                "target_pct": target_pct,
            },
            "jobs": {
                "running": self._running_jobs,
                "queued": self._queued_jobs,
            },
            "tasks": {
                "completed": self._completed_tasks,
                "failed": self._failed_tasks,
                "tasks_per_minute": self.tasks_per_minute(),
                "avg_duration_ms": self.avg_task_duration_ms(),
            },
        }


_res_mgr: Optional[ResourceManager] = None


def get_resource_manager() -> ResourceManager:
    global _res_mgr
    if _res_mgr is None:
        _res_mgr = ResourceManager()
    return _res_mgr
