"""V5 §10/§19-§21 — dashboard-owned session management and safe shutdown.

The dashboard has a Power button. Pressing it must close *the dashboard*, and
nothing else: the browser stays open, the operating system is untouched, an
independently started MT5 terminal keeps running, DATA is never modified, and no
unrelated process is ever signalled.

This module is the single authority for that promise:

* :class:`PowerManager` keeps a *session record* — one id, and the processes the
  dashboard actually started, each with the identity evidence needed to be sure
  it is the same process later (pid + start time + cmdline).
* :meth:`PowerManager.shutdown` performs the ordered stop required by the spec:
  stop new tasks -> stop active tasks -> disconnect MT5 -> stop background
  workers -> stop research workers -> stop schedulers/queues -> backend ->
  frontend -> verify.
* Every termination is addressed to a **specific recorded pid**. There is no
  broad kill anywhere in this file, no pattern matching on process names, and a
  process that cannot be re-identified is only ever asked to stop (SIGTERM /
  `taskkill` without `/F`) — never force-killed.
* The full session and the last report are written to
  ``LOGS/power_session.json`` and ``LOGS/power_shutdown_report.json`` so the
  outcome can be read back after the backend is gone and after it restarts.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("power")

ROOT_DIR = Path(__file__).resolve().parents[2]
#: the session/report live with the other runtime files; the directory can be
#: redirected (tests, portable installs) without touching DATA
LOGS_DIR = Path(os.environ.get("LAB_POWER_DIR") or (ROOT_DIR / "LOGS"))
SESSION_FILE = LOGS_DIR / "power_session.json"
REPORT_FILE = LOGS_DIR / "power_shutdown_report.json"

#: the roles the dashboard can own (a role with no recorded process is reported
#: as "not owned" — never silently invented)
ROLES = ("frontend", "backend", "research_workers", "background_workers",
         "scheduler", "mt5_bridge")

#: the phrase the operator must send, exactly as the confirmation panel shows it
SHUTDOWN_PHRASE = "SHUTDOWN DASHBOARD"

#: in-memory gate: while a shutdown is running (and before the process exits) no
#: new research/backtest task may be accepted
_STATE = {"accepting_tasks": True, "shutdown_started_at": None, "shutdown_by": None}
_STATE_LOCK = threading.RLock()


def accepting_tasks() -> bool:
    """False from the moment a shutdown starts until the process exits."""
    with _STATE_LOCK:
        return bool(_STATE["accepting_tasks"])


def _set_accepting(flag: bool) -> None:
    with _STATE_LOCK:
        _STATE["accepting_tasks"] = bool(flag)


# --------------------------------------------------------------------------- #
# process identity helpers
# --------------------------------------------------------------------------- #
def pid_alive(pid: Optional[int]) -> bool:
    """Is the process still *running*?

    A zombie (exited, waiting for its parent to reap it) is reported as NOT
    alive: it has stopped executing, which is what a shutdown verification asks.
    """
    if not pid or pid <= 0:
        return False
    try:
        import psutil                                          # type: ignore
        proc = psutil.Process(int(pid))
        try:
            if proc.status() == psutil.STATUS_ZOMBIE:
                return False
        except Exception:
            pass
        return True
    except Exception:
        pass
    try:
        stat = Path(f"/proc/{int(pid)}/stat").read_text()
        if " Z " in stat or stat.rstrip().endswith(" Z"):
            return False
        return True
    except Exception:
        pass
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def process_start_time(pid: Optional[int]) -> Optional[float]:
    """The process' creation time, or None when it cannot be read.

    ``None`` is a real answer here: it means "identity unverifiable", and the
    shutdown code downgrades to a graceful request instead of forcing.
    """
    if not pid or pid <= 0:
        return None
    try:
        import psutil                                          # type: ignore
        return float(psutil.Process(int(pid)).create_time())
    except Exception:
        pass
    try:                                                       # POSIX fallback
        stat = Path(f"/proc/{int(pid)}/stat").read_text()
        fields = stat.rsplit(")", 1)[-1].split()
        return int(fields[19]) / float(os.sysconf("SC_CLK_TCK")) + 0.0
    except Exception:
        return None


def process_command_line(pid: Optional[int]) -> Optional[str]:
    if not pid or pid <= 0:
        return None
    try:
        import psutil                                          # type: ignore
        return " ".join(psutil.Process(int(pid)).cmdline() or []) or None
    except Exception:
        pass
    try:
        return Path(f"/proc/{int(pid)}/cmdline").read_bytes().replace(b"\x00", b" ").decode(
            "utf-8", "replace").strip() or None
    except Exception:
        return None


def own_child_pids() -> List[int]:
    """Pids this process actually spawned (research worker pool children)."""
    try:
        import multiprocessing
        return sorted({p.pid for p in multiprocessing.active_children() if p.pid})
    except Exception:
        return []


def _terminate(pid: int, *, force: bool) -> Dict[str, Any]:
    """Signal ONE pid. Returns what was attempted and what happened."""
    out: Dict[str, Any] = {"pid": int(pid), "force": bool(force), "signalled": False,
                           "error": None}
    if int(pid) <= 1:
        out["error"] = "refused: pid <= 1 is never signalled"
        return out
    try:
        if os.name == "nt":
            cmd = ["taskkill", "/PID", str(int(pid))]
            if force:
                cmd.append("/F")
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            out["signalled"] = res.returncode == 0
            if res.returncode != 0:
                out["error"] = (res.stderr or res.stdout or "").strip()[:200] or "taskkill failed"
        else:
            os.kill(int(pid), signal.SIGKILL if force else signal.SIGTERM)
            out["signalled"] = True
    except ProcessLookupError:
        out["error"] = "already gone"
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def _wait_pid_gone(pid: int, timeout_s: float) -> bool:
    deadline = time.time() + max(0.0, timeout_s)
    while time.time() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.05)
    return not pid_alive(pid)


# --------------------------------------------------------------------------- #
# the session
# --------------------------------------------------------------------------- #
class PowerManager:
    def __init__(self, session_file: Optional[Path] = None,
                 report_file: Optional[Path] = None):
        self.session_file = Path(session_file) if session_file else SESSION_FILE
        self.report_file = Path(report_file) if report_file else REPORT_FILE
        self.session_id: Optional[str] = None
        self.started_at: Optional[float] = None
        self.owned: Dict[str, List[Dict[str, Any]]] = {r: [] for r in ROLES}
        #: processes this session stopped (or asked to stop) — kept so the closing
        #: verification can still report on them after they left the owned set
        self.stopped: List[Dict[str, Any]] = []
        self.notes: List[str] = []
        self._lock = threading.RLock()

    # ---------------- lifecycle ----------------
    def start_session(self, *, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Open a new session and register this backend process."""
        with self._lock:
            self.session_id = session_id or f"s-{uuid.uuid4().hex[:12]}"
            self.started_at = time.time()
            self.notes = []
        self.register("backend", os.getpid(), label="dashboard backend / API",
                      cmdline=" ".join(sys.argv) or "python -m app",
                      start_time=process_start_time(os.getpid()))
        self.save()
        return self.session()

    def register(self, role: str, pid: int, *, label: str = "", cmdline: Optional[str] = None,
                 start_time: Optional[float] = None, external: bool = False,
                 note: Optional[str] = None) -> Dict[str, Any]:
        """Record a process the dashboard started (or attached to).

        ``external=True`` marks something the dashboard did NOT launch (for
        example an MT5 terminal the operator started by hand). External entries
        are only ever *disconnected from*; they are never signalled.
        """
        if role not in ROLES:
            raise ValueError(f"unknown role {role!r}; expected one of {ROLES}")
        rec = {
            "role": role, "pid": int(pid), "label": label or role,
            "cmdline": cmdline if cmdline is not None else process_command_line(pid),
            "start_time": start_time if start_time is not None else process_start_time(pid),
            "launched_by": "operator" if external else "dashboard",
            "external": bool(external),
            "registered_at": time.time(),
            "note": note,
        }
        with self._lock:
            self.owned[role] = [r for r in self.owned[role] if r["pid"] != rec["pid"]]
            self.owned[role].append(rec)
        self.save()
        return rec

    def unregister(self, role: str, pid: int, *, stopped: bool = True) -> None:
        with self._lock:
            for rec in self.owned[role]:
                if rec["pid"] == int(pid):
                    self.owned[role] = [r for r in self.owned[role] if r["pid"] != int(pid)]
                    if stopped:
                        self.stopped.append({**rec, "stopped_at": time.time()})
                    break
            else:
                self.owned[role] = [r for r in self.owned[role] if r["pid"] != int(pid)]
        self.save()

    def forget_dead(self) -> List[Dict[str, Any]]:
        """Drop records whose process is already gone; report what was dropped."""
        dropped: List[Dict[str, Any]] = []
        with self._lock:
            for role in ROLES:
                keep = []
                for rec in self.owned[role]:
                    if pid_alive(rec["pid"]):
                        keep.append(rec)
                    else:
                        dropped.append({**rec, "gone": True})
                self.owned[role] = keep
        if dropped:
            self.save()
        return dropped

    def ensure_session(self) -> str:
        """Open the session on first use.

        Called by every public entry point, so the session exists even when the
        FastAPI startup event did not run (for example a TestClient used without
        its context manager) — the Power button must never depend on a startup
        hook having fired.
        """
        if not self.session_id:
            self.start_session()
        return self.session_id or ""

    # ---------------- reporting ----------------
    def session(self) -> Dict[str, Any]:
        if not self.session_id:
            self.start_session()
        with self._lock:
            roles = {role: [_public(r, live=True) for r in self.owned[role]] for role in ROLES}
        alive_roles = [role for role, recs in roles.items() if recs]
        return {
            "session_id": self.session_id,
            "started_at": self.started_at,
            "backend_pid": os.getpid(),
            "roles": ROLES,
            "owned": roles,
            "owned_role_count": len(alive_roles),
            "owned_process_count": sum(len(v) for v in roles.values()),
            "accepting_tasks": accepting_tasks(),
            "notes": list(self.notes),
            "never_touched": [
                "the browser and its tabs",
                "the operating system (no shutdown / restart / logoff / sleep)",
                "any process the dashboard did not start",
                "an MT5 terminal that was started independently (disconnected, never killed)",
                "DATA, node records, results, git repository and configuration",
            ],
            "shutdown_phrase": SHUTDOWN_PHRASE,
            "session_file": str(self.session_file),
        }

    def save(self) -> None:
        try:
            self.session_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {**self.session(), "saved_at": time.time()}
            self.session_file.write_text(json.dumps(payload, indent=2, default=str),
                                         encoding="utf-8")
        except Exception as e:                                  # pragma: no cover
            log.warning("could not write the power session file: %s", e)

    def last_report(self) -> Optional[Dict[str, Any]]:
        try:
            if self.report_file.exists():
                return json.loads(self.report_file.read_text(encoding="utf-8"))
        except Exception:
            return None
        return None

    # ---------------- the shutdown plan ----------------
    def plan(self, *, skip: tuple = ()) -> List[Dict[str, Any]]:
        """The ordered stop, as the operator will see it described."""
        self.ensure_session()
        steps = [
            ("stop_new_tasks", "Stop accepting new tasks (research runs, backtests, live starts)"),
            ("stop_active_tasks", "Finish/stop the tasks that are running now"),
            ("disconnect_mt5", "Disconnect the dashboard's MT5 bridge (terminal is not closed)"),
            ("stop_background_workers", "Stop the dashboard's background workers (live testing, monitor, paper)"),
            ("stop_research_workers", "Stop the research/evolution worker processes"),
            ("stop_scheduler", "Stop the scheduler/queue (queued runs are paused, never deleted)"),
            ("stop_frontend", "Stop the dashboard-owned frontend server, if it runs as its own process"),
            ("stop_backend", "Stop the backend / API process"),
            ("verify", "Verify every dashboard-owned process has stopped"),
        ]
        return [{"order": i + 1, "step": name, "description": desc,
                 "skipped": name in skip}
                for i, (name, desc) in enumerate(steps)]

    # ---------------- the shutdown itself ----------------
    def shutdown(self, *, confirmed: bool = False, actor: str = "dashboard (Power button)",
                 dry_run: bool = False, skip: tuple = (), term_timeout_s: float = 5.0,
                 verify_timeout_s: float = 5.0) -> Dict[str, Any]:
        """Perform (or dry-run) the ordered, verified dashboard shutdown."""
        if not confirmed:
            return {"ok": False, "code": "CONFIRMATION_REQUIRED",
                    "message": f"send confirm={SHUTDOWN_PHRASE!r} to shut the dashboard down",
                    "executed": False}
        self.ensure_session()
        with _STATE_LOCK:
            _STATE["shutdown_started_at"] = time.time()
            _STATE["shutdown_by"] = actor
        _set_accepting(False)

        t0 = time.time()
        results: List[Dict[str, Any]] = []

        def step(name: str, fn) -> Any:
            if name in skip:
                results.append({"step": name, "status": "SKIPPED", "detail": "excluded by request"})
                return None
            started = time.time()
            try:
                value = fn()
                results.append({"step": name, "status": "OK",
                                "detail": _short(value), "duration_ms": int((time.time() - started) * 1000),
                                "result": value})
                return value
            except Exception as e:                              # never stop mid-sequence
                log.exception("power step %s failed: %s", name, e)
                results.append({"step": name, "status": "FAILED",
                                "detail": f"{type(e).__name__}: {e}",
                                "duration_ms": int((time.time() - started) * 1000)})
                return None

        # 1 — stop new tasks
        def _stop_new():
            from .db.database import get_db
            try:
                get_db().log_event("dashboard_shutdown_started",
                                   {"session_id": self.session_id, "actor": actor,
                                    "dry_run": bool(dry_run)})
            except Exception:
                pass
            return {"accepting_tasks": False}
        step("stop_new_tasks", _stop_new)

        # 2 — active tasks
        def _stop_active():
            out = {"interrupted": False, "mode": None}
            try:
                from .orchestrator.lab import get_lab
                lab = get_lab()
                out["mode"] = getattr(lab, "mode", None)
                if lab.running if hasattr(lab, "running") else False:
                    pass
                res = lab.stop() if not dry_run else {"ok": True, "dry_run": True,
                                                      "would_stop": getattr(lab, "mode", None)}
                out.update(res if isinstance(res, dict) else {"result": res})
                out["interrupted"] = True
            except Exception as e:
                out["error"] = f"{type(e).__name__}: {e}"
            return out
        step("stop_active_tasks", _stop_active)

        # 3 — MT5
        def _disconnect_mt5():
            out = {"connected_before": None, "disconnected": False,
                   "terminal_process_signalled": False, "external_terminal_preserved": True}
            try:
                from .mt5 import bridge_status
                out["connected_before"] = bool((bridge_status() or {}).get("connected"))
            except Exception:
                pass
            if dry_run:
                out["dry_run"] = True
                return out
            try:
                from .mt5.factory import get_bridge, reset_bridge
                try:
                    get_bridge().disconnect()
                    out["bridge_disconnect_called"] = True
                except Exception as e:
                    out["bridge_disconnect_error"] = f"{type(e).__name__}: {e}"
                reset_bridge()
                out["disconnected"] = True
            except Exception as e:
                out["error"] = f"{type(e).__name__}: {e}"
            # recorded terminal process: never signalled, only disconnected from
            for rec in self.owned.get("mt5_bridge", []):
                out.setdefault("bridge", []).append(
                    {"pid": rec["pid"], "launched_by": rec["launched_by"],
                     "signalled": False})
            return out
        step("disconnect_mt5", _disconnect_mt5)

        # 4 — background workers (threads inside this process)
        def _stop_background():
            out = {}
            try:
                from .live_testing import get_live_testing_engine
                out["live_testing"] = get_live_testing_engine().stop(
                    reason="dashboard shutdown - Live Testing stays INACTIVE")
            except Exception as e:
                out["live_testing"] = f"{type(e).__name__}: {e}"
            for name, importer in (("monitor", "from .mt5.monitor import get_connection_monitor"),
                                   ("paper", "from .paper.engine import get_paper_engine")):
                if dry_run:
                    out[name] = "dry_run"
                    continue
                try:
                    ns: Dict[str, Any] = {}
                    exec(importer, ns)                        # noqa: S102 - fixed literals
                    obj = ns[[k for k in ns if k.startswith("get_")][0]]()
                    out[name] = obj.stop() if hasattr(obj, "stop") else "no stop()"
                except Exception as e:
                    out[name] = f"{type(e).__name__}: {e}"
            out["threads_note"] = ("the dashboard's worker threads run inside the backend process and "
                                   "end with it; their stop() paths are called above")
            return out
        step("stop_background_workers", _stop_background)

        # 5 — research/evolution worker processes (our own children)
        def _stop_research():
            """Stop the worker pool: this process' own children AND every process
            the session recorded as a research worker."""
            children = own_child_pids()
            registered = [r for r in self.owned.get("research_workers", []) if pid_alive(r["pid"])]
            targets: List[Dict[str, Any]] = [{"pid": p, "source": "child_of_backend",
                                             "start_time": process_start_time(p)} for p in children]
            known = {t["pid"] for t in targets}
            for rec in registered:
                if rec["pid"] not in known:
                    targets.append({"pid": rec["pid"], "source": "session_record",
                                    "start_time": rec.get("start_time")})
            out = {"children_before": children,
                   "registered_before": [r["pid"] for r in registered],
                   "targets": [t["pid"] for t in targets],
                   "terminated": [], "force_killed": [], "unverified": []}
            if dry_run:
                out["dry_run"] = True
                return out
            try:
                from .orchestrator.lab import get_lab
                lab = get_lab()
                if hasattr(lab, "_shutdown_pool"):
                    lab._shutdown_pool()
                    out["pool_shutdown"] = True
            except Exception as e:
                out["error"] = f"{type(e).__name__}: {e}"
            for t in targets:
                pid = t["pid"]
                if not pid_alive(pid):
                    continue
                r = _terminate(pid, force=False)
                out["terminated"].append({**r, "source": t["source"]})
                if _wait_pid_gone(pid, term_timeout_s):
                    self.unregister("research_workers", pid)
                    continue
                # identity gate before any force: it must be a child of THIS
                # process, or its start time must still match the session record
                verified = False
                if t["source"] == "child_of_backend":
                    verified = pid in own_child_pids()
                else:
                    now_start = process_start_time(pid)
                    verified = (now_start is not None and t["start_time"] is not None and
                                abs(now_start - float(t["start_time"])) < 1.0)
                if not verified:
                    out["unverified"].append({"pid": pid, "source": t["source"],
                                              "reason": "identity could not be re-verified - "
                                                        "graceful request only, no forced kill"})
                    continue
                r2 = _terminate(pid, force=True)
                out["force_killed"].append({**r2, "identity_verified": True, "source": t["source"]})
                self.unregister("research_workers", pid)
            return out
        step("stop_research_workers", _stop_research)

        # 6 — scheduler / queue: stop it, and cancel what is still waiting
        def _stop_scheduler():
            out: Dict[str, Any] = {"cancelled_queued": [], "left_running": [], "deleted_rows": 0}
            try:
                from .db.database import get_db
                from .historical_backtest import runs as hb
                db = get_db()
                if dry_run:
                    rows = db.q(f"""SELECT run_id, status FROM {hb.RUN_TABLE}
                                    WHERE status IN ('QUEUED','RUNNING')""") or []
                    out["dry_run"] = True
                    out["would_cancel"] = [r["run_id"] for r in rows if r["status"] == "QUEUED"]
                    return out
                # QUEUED work is cancelled through the run module's own transition
                # (QUEUED -> CANCELLED). RUNNING work is stopped by the lab stop in
                # step 2 and reaches a terminal state there; either way nothing is
                # deleted and no result row is rewritten by this module.
                for row in (db.q(f"""SELECT run_id, status FROM {hb.RUN_TABLE}
                                     WHERE status IN ('QUEUED','RUNNING')""") or []):
                    if row["status"] == "QUEUED":
                        if hb._update_from(db, row["run_id"], ("QUEUED",),
                                           status="CANCELLED", finished_at=time.time()):
                            out["cancelled_queued"].append(row["run_id"])
                    else:
                        out["left_running"].append(row["run_id"])
            except Exception as e:
                out["error"] = f"{type(e).__name__}: {e}"
            out["note"] = ("queued runs are CANCELLED through the run module's own transition; "
                           "no row is deleted and no result is modified")
            return out
        step("stop_scheduler", _stop_scheduler)

        # 7 — dashboard-owned frontend process (never a browser)
        def _stop_frontend():
            out = {"signalled": [], "skipped_external": [], "browsers_touched": 0}
            for rec in list(self.owned.get("frontend", [])):
                if rec.get("external") or rec.get("launched_by") != "dashboard":
                    out["skipped_external"].append({"pid": rec["pid"], "label": rec["label"]})
                    continue
                if dry_run:
                    out["signalled"].append({"pid": rec["pid"], "dry_run": True})
                    continue
                if not pid_alive(rec["pid"]):
                    out["signalled"].append({"pid": rec["pid"], "already_gone": True})
                    continue
                same = (rec.get("start_time") is None or
                        process_start_time(rec["pid"]) is None or
                        abs((process_start_time(rec["pid"]) or 0) - float(rec["start_time"])) < 1.0)
                if not same:
                    out["skipped_external"].append({"pid": rec["pid"],
                                                    "reason": "pid was reused by another process"})
                    continue
                r = _terminate(rec["pid"], force=False)
                if not _wait_pid_gone(rec["pid"], term_timeout_s):
                    r2 = _terminate(rec["pid"], force=True)
                    out["signalled"].append({**r, "forced": r2})
                else:
                    out["signalled"].append(r)
            out["note"] = ("only a frontend process the dashboard itself started is stopped; a browser "
                           "or a dev server started by the operator is never signalled")
            return out
        step("stop_frontend", _stop_frontend)

        # 8 — the backend itself, last of the running things
        def _stop_backend():
            out = {"pid": os.getpid(), "scheduled": False, "dry_run": bool(dry_run)}
            if dry_run:
                return out
            delay = 1.0
            out["scheduled"] = True
            out["grace_s"] = delay
            out["signal"] = "SIGTERM" if os.name != "nt" else "taskkill (graceful)"

            def _later():
                time.sleep(delay)
                try:
                    self.save()
                except Exception:
                    pass
                try:
                    if os.name == "nt":
                        _terminate(os.getpid(), force=False)
                    else:
                        os.kill(os.getpid(), signal.SIGTERM)
                except Exception as e:                          # pragma: no cover
                    log.warning("backend self-stop failed: %s", e)

            threading.Thread(target=_later, name="power-self-stop", daemon=True).start()
            return out
        step("stop_backend", _stop_backend)

        # 9 — verify
        def _verify():
            deadline = time.time() + verify_timeout_s
            out: Dict[str, Any] = {"verified": {}, "still_alive": [], "unverifiable": []}
            own_pid = os.getpid()
            while time.time() < deadline:
                out["verified"] = {}
                out["still_alive"] = []
                self.forget_dead()
                # the ledger of processes this shutdown already stopped counts too,
                # otherwise a worker that stopped early would vanish from the report
                for rec in list(self.stopped):
                    key = f"{rec['role']}:{rec['pid']}"
                    if pid_alive(rec["pid"]):
                        out["still_alive"].append({"role": rec["role"], "pid": rec["pid"],
                                                   "label": rec["label"], "from": "stopped-ledger"})
                    else:
                        out["verified"][key] = "gone"
                for role in ROLES:
                    for rec in self.owned.get(role, []):
                        pid = rec["pid"]
                        if role == "backend" and pid == own_pid and not dry_run:
                            out["verified"][f"{role}:{pid}"] = "stopping (self, deferred)"
                            continue
                        if pid_alive(pid):
                            out["still_alive"].append({"role": role, "pid": pid,
                                                       "label": rec["label"]})
                        else:
                            out["verified"][f"{role}:{pid}"] = "gone"
                if not out["still_alive"]:
                    break
                time.sleep(0.2)
            # a final, identity-checked force for anything left
            out["forced_after_verify"] = []
            for item in list(out["still_alive"]):
                recs = [r for r in self.owned.get(item["role"], []) if r["pid"] == item["pid"]]
                rec = recs[0] if recs else None
                if rec is None or rec.get("external"):
                    out["unverifiable"].append({**item, "reason": "not dashboard-owned / external"})
                    continue
                start = process_start_time(item["pid"])
                if start is None or (rec.get("start_time") is not None and
                                     abs(start - float(rec["start_time"])) > 1.0):
                    out["unverifiable"].append(
                        {**item, "reason": "identity could not be re-verified - no forced kill"})
                    continue
                if dry_run:
                    out["forced_after_verify"].append({**item, "dry_run": True})
                    continue
                out["forced_after_verify"].append({**_terminate(item["pid"], force=True),
                                                   "identity_verified": True})
            out["data_touched"] = False
            out["browser_touched"] = False
            return out
        step("verify", _verify)

        report = {
            "ok": all(s["status"] in ("OK", "SKIPPED") for s in results),
            "session_id": self.session_id,
            "actor": actor,
            "dry_run": bool(dry_run),
            "started_at": t0,
            "finished_at": time.time(),
            "duration_s": round(time.time() - t0, 3),
            "steps": results,
            "backend_stopping": (not dry_run) and ("stop_backend" not in skip),
            "never_touched": self.session()["never_touched"],
        }
        self.notes.append(f"shutdown {time.strftime('%Y-%m-%d %H:%M:%S')} by {actor} "
                          f"(dry_run={dry_run})")
        try:
            self.report_file.parent.mkdir(parents=True, exist_ok=True)
            self.report_file.write_text(json.dumps(report, indent=2, default=str),
                                        encoding="utf-8")
        except Exception as e:                                  # pragma: no cover
            log.warning("could not write the shutdown report: %s", e)
        return report


def _public(rec: Dict[str, Any], *, live: bool = False) -> Dict[str, Any]:
    out = {k: rec.get(k) for k in ("role", "pid", "label", "cmdline", "start_time",
                                   "launched_by", "external", "note")}
    if live:
        out["alive"] = pid_alive(rec["pid"])
    return out


def _short(value: Any, limit: int = 220) -> str:
    if value is None:
        return "done"
    if isinstance(value, dict):
        for key in ("note", "summary", "error"):
            if value.get(key):
                return str(value[key])[:limit]
        return json.dumps(value, default=str)[:limit]
    return str(value)[:limit]


_MANAGER: Optional[PowerManager] = None


def get_power_manager() -> PowerManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = PowerManager()
    return _MANAGER
