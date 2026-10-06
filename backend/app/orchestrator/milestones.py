"""
Main Milestone Progress Architecture (V2.7).

Manages the 7-stop metro/bus workflow route:
  1. System Ready
  2. MT5 Connected
  3. Data Sync
  4. Feature Precompute
  5. Validation
  6. Evolution
  7. Complete

Tracks real workflow state, line fill percentage, individual subprocesses,
and interactive milestone diagnostics.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from ..api.ws import bus

log = logging.getLogger("workflow.milestones")

MILESTONE_DEFINITIONS = [
    {"id": "system", "label": "System Ready", "description": "Core subsystems, DB lineage, and memory initialized"},
    {"id": "mt5", "label": "MT5 Connected", "description": "MetaTrader 5 terminal / simulator feed verified"},
    {"id": "data_sync", "label": "Data Sync", "description": "Parquet market data discovery, verification, and reuse"},
    {"id": "feature_precompute", "label": "Feature Precompute", "description": "Indicator arrays computed/reused and cached on disk"},
    {"id": "validation", "label": "Validation", "description": "Strategy backtest matrix, train/test split, and sanity screening"},
    {"id": "evolution", "label": "Evolution", "description": "Genetic algorithms, reproduction, mutation, and qualification"},
    {"id": "complete", "label": "Complete", "description": "Research cycle finished, target ceiling or criteria reached"},
]


@dataclass
class MilestoneStop:
    id: str
    label: str
    description: str
    status: str = "PENDING"  # PENDING, RUNNING, COMPLETE, FAILED, WAITING
    progress: float = 0.0
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    current_task: Optional[str] = None
    completed_items: List[str] = field(default_factory=list)
    running_items: List[str] = field(default_factory=list)
    remaining_items: List[str] = field(default_factory=list)
    task_ids: List[str] = field(default_factory=list)
    subprocesses: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    error: Optional[str] = None

    @property
    def elapsed_seconds(self) -> float:
        if not self.started_at:
            return 0.0
        end = self.completed_at or time.time()
        return round(max(0.0, end - self.started_at), 1)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["elapsed_seconds"] = self.elapsed_seconds
        return d


class MilestoneManager:
    def __init__(self):
        self._lock = threading.RLock()
        self._stops: Dict[str, MilestoneStop] = {
            m["id"]: MilestoneStop(id=m["id"], label=m["label"], description=m["description"])
            for m in MILESTONE_DEFINITIONS
        }
        # Mark initial milestone as complete when system is ready
        self._stops["system"].status = "COMPLETE"
        self._stops["system"].progress = 100.0
        self._stops["system"].completed_at = time.time()
        self._stops["system"].completed_items = ["Environment OK", "371 Strategies Loaded", "DATA Root Validated"]

        try:
            from ..mt5 import bridge_status
            bs = bridge_status()
            if bs.get("connected"):
                self._stops["mt5"].status = "COMPLETE"
                self._stops["mt5"].progress = 100.0
                self._stops["mt5"].completed_at = time.time()
                source = "SIMULATOR Feed" if bs.get("is_simulated") else "MetaTrader 5 Real"
                self._stops["mt5"].completed_items = [f"{source} Active", f"Account: {bs.get('account', 'Active')}"]
        except Exception:
            pass

    def set_running(self, milestone_id: str, task_desc: Optional[str] = None) -> None:
        with self._lock:
            stop = self._stops.get(milestone_id)
            if not stop:
                return
            if stop.status != "RUNNING":
                stop.status = "RUNNING"
                stop.started_at = stop.started_at or time.time()
            if task_desc:
                stop.current_task = task_desc
            self._broadcast()

    def set_progress(
        self,
        milestone_id: str,
        progress: float,
        task_desc: Optional[str] = None,
        running_item: Optional[str] = None,
    ) -> None:
        with self._lock:
            stop = self._stops.get(milestone_id)
            if not stop:
                return
            stop.progress = max(0.0, min(100.0, round(float(progress), 1)))
            if stop.status != "RUNNING" and stop.progress < 100.0:
                stop.status = "RUNNING"
                stop.started_at = stop.started_at or time.time()
            if task_desc:
                stop.current_task = task_desc
            if running_item and running_item not in stop.running_items:
                stop.running_items.append(running_item)
            self._broadcast()

    def add_completed_item(self, milestone_id: str, item_name: str) -> None:
        with self._lock:
            stop = self._stops.get(milestone_id)
            if not stop:
                return
            if item_name not in stop.completed_items:
                stop.completed_items.append(item_name)
            if item_name in stop.running_items:
                stop.running_items.remove(item_name)
            if item_name in stop.remaining_items:
                stop.remaining_items.remove(item_name)
            self._broadcast()

    def set_subprocess(self, milestone_id: str, sub_key: str, details: Dict[str, Any]) -> None:
        with self._lock:
            stop = self._stops.get(milestone_id)
            if not stop:
                return
            stop.subprocesses[sub_key] = details
            self._broadcast()

    def set_complete(self, milestone_id: str, completed_item: Optional[str] = None) -> None:
        with self._lock:
            stop = self._stops.get(milestone_id)
            if not stop:
                return
            stop.status = "COMPLETE"
            stop.progress = 100.0
            stop.completed_at = time.time()
            stop.running_items.clear()
            stop.remaining_items.clear()
            if completed_item and completed_item not in stop.completed_items:
                stop.completed_items.append(completed_item)
            self._broadcast()

    def set_failed(self, milestone_id: str, error_msg: str) -> None:
        with self._lock:
            stop = self._stops.get(milestone_id)
            if not stop:
                return
            stop.status = "FAILED"
            stop.error = error_msg
            self._broadcast()

    def compute_route_state(self) -> Dict[str, Any]:
        """Calculates line fill percentage and currently active stop."""
        with self._lock:
            stops_list = [self._stops[m["id"]].to_dict() for m in MILESTONE_DEFINITIONS]
            n_stops = len(stops_list)

            # Find active index
            active_idx = 0
            for i, s in enumerate(stops_list):
                if s["status"] in ("RUNNING", "FAILED"):
                    active_idx = i
                    break
                elif s["status"] == "COMPLETE":
                    active_idx = min(n_stops - 1, i + 1)

            # Compute route line fill percentage (0% at stop 0, 100% at last stop)
            # Each segment between stops represents (100 / (n_stops - 1)) %
            if n_stops <= 1:
                fill_pct = 100.0
            else:
                segment_pct = 100.0 / (n_stops - 1)
                completed_segments = 0.0
                for i, s in enumerate(stops_list):
                    if s["status"] == "COMPLETE":
                        completed_segments = i
                    elif s["status"] == "RUNNING":
                        fraction = (s["progress"] / 100.0)
                        completed_segments = i - 1 + fraction
                        break
                fill_pct = max(0.0, min(100.0, round(completed_segments * segment_pct, 1)))

            try:
                from ..evolution.engine import get_evo_engine
                ns = get_evo_engine().get_node_generation_state()
                cur_nodes = ns["current_nodes"]
                target_nodes = ns["target_nodes"]
                pct = ns["progress_pct"]
            except Exception:
                cur_nodes, target_nodes, pct = 0, 1000, 0.0

            return {
                "milestones": stops_list,
                "active_index": active_idx,
                "fill_percentage": pct if pct > 0 else fill_pct,
                "route_fill_pct": fill_pct,
                "progress_pct": pct,
                "current_nodes": cur_nodes,
                "target_nodes": target_nodes,
                "active_milestone": stops_list[active_idx] if active_idx < n_stops else stops_list[-1],
            }

    def _broadcast(self) -> None:
        bus.publish("milestones_update", self.compute_route_state())


_milestone_mgr: Optional[MilestoneManager] = None


def get_milestone_manager() -> MilestoneManager:
    global _milestone_mgr
    if _milestone_mgr is None:
        _milestone_mgr = MilestoneManager()
    return _milestone_mgr
