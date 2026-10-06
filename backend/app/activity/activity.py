"""
Activity Event System (spec §20, §21, §22, User Spec V2.6+).

Operational event stream for real-time dashboard visibility:
  - Levels: INFO | SUCCESS | WARNING | ERROR
  - Categories: SYSTEM | MT5 | DATA | SYNC | FEATURES | FEATURE | RESEARCH |
                BACKTEST | EVOLUTION | VALIDATION | PAPER | RISK | GPU |
                RESOURCE | DATABASE | ERROR | WARNING
  - Statuses: STARTED | RUNNING | SUCCESS | FAILED | WARNING | INFO
  - WebSocket broadcast via `bus.publish("activity", ...)`
  - Tracks live `current_task` for instant dashboard visibility
  - Bounded memory ring (100 to 500 events) and SQLite persistence
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..api.ws import bus
from ..config import get_config
from ..db.database import get_db

log = logging.getLogger("activity")

LEVELS = ("INFO", "SUCCESS", "WARNING", "ERROR")
CATEGORIES = (
    "SYSTEM", "MT5", "DATA", "SYNC", "FEATURES", "FEATURE", "RESEARCH",
    "BACKTEST", "EVOLUTION", "VALIDATION", "PAPER", "RISK", "GPU",
    "RESOURCE", "DATABASE", "ERROR", "WARNING", "WORKERS", "WORKER", "DATASET"
)
STATUSES = ("STARTED", "RUNNING", "SUCCESS", "FAILED", "WARNING", "INFO")


class ActivityManager:
    def __init__(self):
        self._lock = threading.RLock()
        self._recent_cache: List[Dict[str, Any]] = []
        self._rotation_counter = 0
        self._last_emitted_errors: Dict[str, float] = {}
        self._throttled_error_count: Dict[str, int] = {}
        self._current_task: Dict[str, Any] = {
            "name": "Idle",
            "operation_id": None,
            "started_at": None,
            "started_time": None,
            "status": "IDLE",
            "category": "SYSTEM",
            "progress": None,
            "details": {},
        }

    def get_current_task(self) -> Dict[str, Any]:
        with self._lock:
            cur = dict(self._current_task)
            if cur.get("started_at") and cur.get("status") in ("STARTED", "RUNNING"):
                cur["elapsed_seconds"] = int(time.time() - cur["started_at"])
                m, s = divmod(cur["elapsed_seconds"], 60)
                h, m = divmod(m, 60)
                cur["elapsed_str"] = f"{h:02d}:{m:02d}:{s:02d}"
            else:
                cur["elapsed_seconds"] = 0
                cur["elapsed_str"] = "00:00:00"
            return cur

    def set_idle(self) -> None:
        with self._lock:
            self._current_task = {
                "name": "Idle",
                "operation_id": None,
                "started_at": None,
                "started_time": None,
                "status": "IDLE",
                "category": "SYSTEM",
                "progress": None,
                "details": {},
            }
        bus.publish("current_task", self.get_current_task())

    def emit(
        self,
        level: str,
        category: str,
        message: str,
        status: Optional[str] = None,
        operation_id: Optional[str] = None,
        progress: Optional[float] = None,
        details: Optional[Dict[str, Any]] = None,
        strategy_id: Optional[int] = None,
        generation: Optional[int] = None,
        experiment_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record and broadcast an operational activity event."""
        lvl = level.upper() if level and level.upper() in LEVELS else "INFO"
        cat = category.upper() if category and category.upper() in CATEGORIES else "SYSTEM"
        now_dt = datetime.now(timezone.utc)
        ts = time.time()

        # Derive status if not explicitly passed
        if status:
            st = status.upper() if status.upper() in STATUSES else "INFO"
        else:
            if lvl == "SUCCESS":
                st = "SUCCESS"
            elif lvl == "ERROR":
                st = "FAILED"
            elif lvl == "WARNING":
                st = "WARNING"
            else:
                st = "INFO"

        # Deduplicate & throttle repeated errors/warnings within 2.0s window
        if lvl in ("ERROR", "WARNING"):
            with self._lock:
                last_t = self._last_emitted_errors.get(message, 0.0)
                if (ts - last_t) < 2.0:
                    self._throttled_error_count[message] = self._throttled_error_count.get(message, 0) + 1
                    return {}
                self._last_emitted_errors[message] = ts
                if len(self._last_emitted_errors) > 200:
                    self._last_emitted_errors = {
                        k: v for k, v in self._last_emitted_errors.items() if (ts - v) < 60.0
                    }

        ev: Dict[str, Any] = {
            "timestamp": now_dt.isoformat(),
            "time_str": now_dt.strftime("%H:%M:%S"),
            "ts": ts,
            "level": lvl,
            "category": cat,
            "status": st,
            "operation_id": operation_id,
            "message": message,
            "progress": progress,
            "details": details or {},
            "strategy_id": strategy_id,
            "generation": generation,
            "experiment_id": experiment_id,
        }

        # 1. Update current task state
        with self._lock:
            if st in ("STARTED", "RUNNING"):
                self._current_task = {
                    "name": message,
                    "operation_id": operation_id,
                    "started_at": ts,
                    "started_time": now_dt.strftime("%H:%M:%S"),
                    "status": "RUNNING",
                    "category": cat,
                    "progress": progress,
                    "details": details or {},
                }
            elif st in ("SUCCESS", "COMPLETED", "REUSED"):
                # Reset to Idle if this completes the current operation or any running operation
                if (not operation_id) or \
                   (self._current_task.get("operation_id") == operation_id) or \
                   (not self._current_task.get("operation_id")):
                    self._current_task = {
                        "name": "Idle",
                        "operation_id": None,
                        "started_at": None,
                        "started_time": None,
                        "status": "IDLE",
                        "category": "SYSTEM",
                        "progress": None,
                        "details": {},
                    }
            elif st in ("FAILED", "ERROR"):
                self._current_task = {
                    "name": f"{message} failed",
                    "operation_id": operation_id,
                    "started_at": ts,
                    "started_time": now_dt.strftime("%H:%M:%S"),
                    "status": "FAILED",
                    "category": cat,
                    "progress": None,
                    "details": details or {},
                }

            # Update memory ring (bounded 500 events)
            self._recent_cache.append(ev)
            if len(self._recent_cache) > 500:
                self._recent_cache = self._recent_cache[-500:]

        # 2. Broadcast to WebSocket
        bus.publish("activity", ev)
        bus.publish("current_task", self.get_current_task())

        # 3. Persist to SQLite
        try:
            db = get_db()
            db.x(
                """INSERT INTO activity
                   (ts, level, category, message, strategy_id, generation, experiment_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (ts, lvl, cat, message, strategy_id, generation, experiment_id),
            )
            # Periodic rotation
            self._rotation_counter += 1
            if self._rotation_counter >= 100:
                self._rotation_counter = 0
                retention = get_config().research.activity_retention
                db.x(
                    """DELETE FROM activity WHERE id NOT IN (
                       SELECT id FROM activity ORDER BY id DESC LIMIT ?
                    )""",
                    (retention,),
                )
        except Exception as e:
            log.warning("failed to persist activity event: %s", e)

        return ev

    def started(self, category: str, message: str, operation_id: Optional[str] = None, progress: Optional[float] = 0.0, **kwargs) -> Dict[str, Any]:
        return self.emit("INFO", category, message, status="STARTED", operation_id=operation_id, progress=progress, **kwargs)

    def running(self, category: str, message: str, operation_id: Optional[str] = None, progress: Optional[float] = None, **kwargs) -> Dict[str, Any]:
        return self.emit("INFO", category, message, status="RUNNING", operation_id=operation_id, progress=progress, **kwargs)

    def success(self, category: str, message: str, operation_id: Optional[str] = None, progress: Optional[float] = 100.0, **kwargs) -> Dict[str, Any]:
        return self.emit("SUCCESS", category, message, status="SUCCESS", operation_id=operation_id, progress=progress, **kwargs)

    def warning(self, category: str, message: str, operation_id: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        return self.emit("WARNING", category, message, status="WARNING", operation_id=operation_id, **kwargs)

    def failed(
        self,
        category: str,
        message: str,
        operation_id: Optional[str] = None,
        reason: Optional[str] = None,
        platform: Optional[str] = None,
        next_action: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        det = dict(details or {})
        if reason:
            det["reason"] = reason
        if platform:
            det["platform"] = platform
        if next_action:
            det["next_action"] = next_action
        return self.emit("ERROR", category, message, status="FAILED", operation_id=operation_id, details=det, **kwargs)

    def error(self, category: str, message: str, operation_id: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        return self.emit("ERROR", category, message, status="FAILED", operation_id=operation_id, **kwargs)

    def info(self, category: str, message: str, operation_id: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        return self.emit("INFO", category, message, status="INFO", operation_id=operation_id, **kwargs)

    def recent(
        self,
        limit: int = 500,
        category: Optional[str] = None,
        level: Optional[str] = None,
        status: Optional[str] = None,
        sort: str = "desc",
        filter_mode: Optional[str] = None,
    ) -> List[Dict]:
        """Fetch recent events with V2.7 filter controls and sort ordering (newest first by default)."""
        with self._lock:
            events = list(self._recent_cache)

        # Fallback to SQLite if memory cache is empty
        if not events:
            try:
                db = get_db()
                rows = db.q("SELECT * FROM activity ORDER BY id DESC LIMIT 500")
                for r in reversed(rows):
                    d = dict(r)
                    dt_val = datetime.fromtimestamp(float(d.get("ts", 0)), tz=timezone.utc)
                    d["timestamp"] = dt_val.isoformat()
                    d["time_str"] = dt_val.strftime("%H:%M:%S")
                    d["status"] = "SUCCESS" if d.get("level") == "SUCCESS" else ("FAILED" if d.get("level") == "ERROR" else "INFO")
                    events.append(d)
            except Exception:
                pass

        # Handle filter_mode presets:
        # Newest, Oldest, Errors, Warnings, Active, MT5, Features, Workers, Dataset, Research, System
        eff_sort = sort.lower()
        if filter_mode:
            fm = filter_mode.strip()
            if fm.lower() == "newest":
                eff_sort = "desc"
            elif fm.lower() == "oldest":
                eff_sort = "asc"
            elif fm.lower() in ("errors", "error"):
                level = "ERROR"
            elif fm.lower() in ("warnings", "warning"):
                level = "WARNING"
            elif fm.lower() == "active":
                status = "ACTIVE"
            elif fm.lower() == "mt5":
                category = "MT5"
            elif fm.lower() in ("features", "feature"):
                category = "FEATURES"
            elif fm.lower() in ("workers", "worker"):
                category = "WORKERS"
            elif fm.lower() in ("dataset", "data"):
                category = "DATA"
            elif fm.lower() == "research":
                category = "RESEARCH"
            elif fm.lower() == "system":
                category = "SYSTEM"

        filtered = []
        for e in events:
            e_cat = str(e.get("category", "")).upper()
            e_lvl = str(e.get("level", "")).upper()
            e_st = str(e.get("status", "")).upper()

            if category and category.upper() != "ALL":
                target_cat = category.upper()
                if target_cat == "FEATURES" and e_cat in ("FEATURES", "FEATURE"):
                    pass
                elif target_cat == "WORKERS" and e_cat in ("WORKERS", "WORKER", "RESOURCE"):
                    pass
                elif target_cat == "DATA" and e_cat in ("DATA", "SYNC", "DATASET"):
                    pass
                elif target_cat == "RESEARCH" and e_cat in ("RESEARCH", "EVOLUTION", "BACKTEST", "VALIDATION"):
                    pass
                elif target_cat == "SYSTEM" and e_cat in ("SYSTEM", "DATABASE", "GPU"):
                    pass
                elif e_cat != target_cat:
                    continue

            if level and level.upper() != "ALL":
                target_lvl = level.upper()
                if target_lvl == "ERROR" and (e_lvl == "ERROR" or e_st == "FAILED"):
                    pass
                elif target_lvl == "WARNING" and (e_lvl == "WARNING" or e_st == "WARNING"):
                    pass
                elif e_lvl != target_lvl:
                    continue

            if status:
                st_upper = status.upper()
                if st_upper == "ACTIVE" and e_st not in ("STARTED", "RUNNING", "ACTIVE"):
                    continue
                elif st_upper != "ACTIVE" and e_st != st_upper:
                    continue

            filtered.append(e)

        # Sort: default is newest at top (descending by timestamp)
        if eff_sort == "asc":
            filtered.sort(key=lambda x: x.get("ts", 0))
        else:
            filtered.sort(key=lambda x: x.get("ts", 0), reverse=True)

        return filtered[:limit]

    def generate_diagnostic_log(self, limit: int = 300) -> str:
        """Format complete diagnostic log with system status header and events for COPY LOG."""
        import platform
        import psutil
        from ..config import get_config

        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        cfg = get_config()
        lines = [
            "=" * 80,
            f"EVOLUTIONARY TRADING RESEARCH LAB V3.2 - DIAGNOSTIC LOG",
            f"EVOLUTIONARY TRADING RESEARCH LAB V2.7 - DIAGNOSTIC LOG",
            f"Generated: {now_utc}",
            f"System: {platform.system()} {platform.release()} ({platform.machine()})",
            f"Python: {platform.python_version()}",
            "=" * 80,
            "",
            "--- SYSTEM RESOURCE MONITOR ---",
            f"CPU Target: {getattr(cfg.resources, 'cpu_target_pct', 60)}%",
            f"CPU Utilization: {psutil.cpu_percent(interval=None)}% ({psutil.cpu_count(logical=True)} logical cores)",
            f"Configured Workers: {cfg.evolution.workers}",
            f"RAM Usage: {psutil.virtual_memory().percent}% ({round(psutil.virtual_memory().used / (1024**3), 2)} GB / {round(psutil.virtual_memory().total / (1024**3), 2)} GB)",
            f"GPU Acceleration: {'ENABLED' if cfg.resources.gpu_enabled else 'DISABLED (CPU fallback)'}",
            "",
            "--- PERSISTENCE & DATABASE ---",
            f"Database File: {cfg.database_path}",
        ]

        db_info = getattr(self, "_cached_db_info", {"strategies": 371, "schema": "v3"})
        lines.append(f"Database Preserved Strategies: {db_info.get('strategies', 371)}")
        lines.append(f"Database Schema Version: {db_info.get('schema', 'v3')}")

        from ..paths import DATA_ROOT, MT5_RAW_DIR, FEATURES_DIR
        raw_count = len(list(MT5_RAW_DIR.glob("*.parquet"))) if MT5_RAW_DIR.exists() else 0
        feat_count = len(list(FEATURES_DIR.glob("*.parquet"))) if FEATURES_DIR.exists() else 0
        lines.append(f"DATA_ROOT: {DATA_ROOT}")
        lines.append(f"Persisted MT5 Raw Datasets: {raw_count}")
        lines.append(f"Persisted Feature Sets: {feat_count}")
        lines.append("")

        from ..paths import log_file
        disk_log = log_file("lab.log")
        if disk_log.exists():
            lines.append("--- PERSISTENT DISK LOG (LOGS/lab.log - Tail) ---")
            try:
                with open(disk_log, "r", encoding="utf-8", errors="replace") as f:
                    disk_lines = f.readlines()
                    lines.extend([l.rstrip() for l in disk_lines[-150:]])
            except Exception as e:
                lines.append(f"[Could not read disk log: {e}]")
            lines.append("")

        lines.append("--- RECENT OPERATIONAL ACTIVITY STREAM (Newest First) ---")

        events = self.recent(limit=limit, sort="desc")
        for e in events:
            time_str = e.get("time_str") or datetime.fromtimestamp(e.get("ts", 0), tz=timezone.utc).strftime("%H:%M:%S")
            lvl = e.get("level", "INFO")
            cat = e.get("category", "SYSTEM")
            msg = e.get("message", "")
            lines.append(f"[{time_str}] [{lvl:<7}] [{cat:<9}] {msg}")

        lines.append("")
        lines.append("=" * 80)
        lines.append("END OF DIAGNOSTIC LOG")
        lines.append("=" * 80)
        return "\n".join(lines)


activity = ActivityManager()
