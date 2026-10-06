"""
Comprehensive Logging & Technical Execution Console Setup (V2.7).

Provides high-resolution structured operational logs:
  - Timestamp (millisecond precision)
  - Level (INFO, SUCCESS, WARNING, ERROR, DEBUG)
  - Module & Pipeline Location
  - Stage, Task, Dataset, Action, Progress, Worker, Resource Status, Result, Elapsed Time
  - Full exception tracebacks without generic concealment
  - In-memory ring buffer (up to 4000 entries) & disk log
  - Complete technical text export for one-click clipboard copy
"""
from __future__ import annotations

import logging
import re
import sys
import threading
import time
import traceback
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT_DIR = Path(__file__).resolve().parents[2]

_buffer: deque = deque(maxlen=4000)
_lock = threading.Lock()


class TechnicalLogHandler(logging.Handler):
    """Captures structured technical log records for the diagnostic execution console."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
            now_dt = datetime.fromtimestamp(record.created)
            ts_str = now_dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            time_str = now_dt.strftime("%H:%M:%S.%f")[:-3]

            level = record.levelname
            if "[SUCCESS]" in msg or "✓" in msg:
                level = "SUCCESS"

            # Parse module
            logger_name = record.name.upper()
            if "DATA" in logger_name:
                module = "DATA.ENGINE"
                category = "DATA"
            elif "FEATURE" in logger_name:
                module = "FEATURES.ENGINE"
                category = "FEATURES"
            elif "EVO" in logger_name or "NODE" in logger_name:
                module = "EVOLUTION.ENGINE"
                category = "NODE"
            elif "MT5" in logger_name:
                module = "MT5.BRIDGE"
                category = "MT5"
            elif "DB" in logger_name or "DATABASE" in logger_name:
                module = "DATABASE.SQLITE"
                category = "DATABASE"
            elif "RESOURCE" in logger_name or "GPU" in logger_name:
                module = "SYSTEM.RESOURCES"
                category = "GPU" if "GPU" in msg else "SYSTEM"
            elif "ORCHESTRATOR" in logger_name or "STAGE" in logger_name:
                module = "ORCHESTRATOR.LAB"
                category = "SYSTEM"
            else:
                module = record.name.upper()
                category = "SYSTEM"

            # Contextual stage from message or current orchestrator state
            stage = "PIPELINE"
            m_stage = re.search(r"stage=([A-Z_]+)|\[(STAGE|PIPELINE|FEATURES|DATA|NODE)\]", msg)
            if m_stage:
                stage = m_stage.group(1) or m_stage.group(2)

            # Contextual task / operation / dataset
            dataset = ""
            m_ds = re.search(r"(XAUUSD|EURUSD|BTCUSD|TEST)_[A-Z0-9]+|(XAUUSD|EURUSD|BTCUSD)\s+(M1|M5|M15|M30|H1|H4|D1)", msg)
            if m_ds:
                dataset = m_ds.group(0).replace(" ", "_")

            action = ""
            if "ACTION:" in msg:
                m_act = re.search(r"ACTION:\s*([^\n\r]+)", msg)
                if m_act:
                    action = m_act.group(1).strip()
            elif "REUSE" in msg:
                action = "REUSE"
            elif "EXTEND" in msg:
                action = "EXTEND"
            elif "BOOTSTRAP" in msg:
                action = "BOOTSTRAP"
            elif "MUTAT" in msg:
                action = "MUTATE"
            elif "CROSSOVER" in msg:
                action = "CROSSOVER"

            progress = ""
            m_prog = re.search(r"(\d+\s*/\s*\d+|\d+\.\d+%)", msg)
            if m_prog:
                progress = m_prog.group(1)

            worker = "Worker 1"
            m_wrk = re.search(r"worker[=-]?([a-zA-Z0-9_-]+)", msg, re.IGNORECASE)
            if m_wrk:
                worker = f"Worker {m_wrk.group(1)}"

            # Exception & traceback capture
            exc_text = ""
            if record.exc_info:
                exc_text = "".join(traceback.format_exception(*record.exc_info))
            elif record.exc_text:
                exc_text = record.exc_text

            entry: Dict[str, Any] = {
                "id": record.created,
                "ts": ts_str,
                "time_str": time_str,
                "level": level,
                "logger": record.name,
                "module": module,
                "category": category,
                "stage": stage,
                "dataset": dataset,
                "action": action,
                "progress": progress,
                "worker": worker,
                "message": msg,
                "exc_text": exc_text,
                "func_name": record.funcName,
                "line_no": record.lineno,
            }

            with _lock:
                _buffer.append(entry)
        except Exception:
            pass


def get_logs(limit: int = 500, level: Optional[str] = None, category: Optional[str] = None) -> List[Dict[str, Any]]:
    with _lock:
        items = list(_buffer)

    if level and level.upper() not in ("ALL", ""):
        lvl = level.upper()
        if lvl in ("INFO", "SUCCESS", "WARNING", "ERROR", "DEBUG"):
            items = [i for i in items if i["level"] == lvl]

    if category and category.upper() not in ("ALL", ""):
        cat = category.upper()
        items = [i for i in items if i.get("category") == cat or cat in i.get("module", "") or cat in i.get("message", "")]

    return items[-limit:]


def get_full_technical_log_text() -> str:
    """Return the entire available technical log formatted for clipboard copy."""
    with _lock:
        items = list(_buffer)

    lines = []
    lines.append("=" * 80)
    lines.append(f"EVOLUTIONARY TRADING RESEARCH LAB V3.2 - TECHNICAL DIAGNOSTIC LOG")
    lines.append(f"EVOLUTIONARY TRADING RESEARCH LAB - TECHNICAL DIAGNOSTIC LOG")
    lines.append(f"Export Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
    lines.append(f"Total Entries: {len(items):,}")
    lines.append("=" * 80)
    lines.append("")

    for item in items:
        ts = item.get("ts", "")
        lvl = item.get("level", "INFO").padEnd if hasattr(item.get("level", "INFO"), "padEnd") else item.get("level", "INFO")
        lvl = f"{item.get('level', 'INFO'):<7}"
        mod = item.get("module", "LAB")
        stg = item.get("stage", "PIPELINE")
        msg = item.get("message", "")

        header = f"{ts} | {lvl} | {mod} | {stg}"
        if item.get("dataset"):
            header += f" | {item['dataset']}"
        if item.get("action"):
            header += f" | ACTION: {item['action']}"
        if item.get("progress"):
            header += f" | {item['progress']}"
        if item.get("worker"):
            header += f" | {item['worker']}"

        lines.append(header)
        lines.append(msg)
        if item.get("exc_text"):
            lines.append("--- EXCEPTION TRACEBACK ---")
            lines.append(item["exc_text"].strip())
            lines.append("---------------------------")
        lines.append("")

    return "\n".join(lines)


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    from .paths import log_file, LOGS_DIR
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(log_file("lab.log"), encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    th = TechnicalLogHandler()
    th.setFormatter(fmt)
    root.addHandler(th)

    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
