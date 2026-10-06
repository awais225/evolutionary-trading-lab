"""
Single-Instance PID Lease File and Port Pre-Bind Checker for Evolutionary Trading Research Lab.

Ensures only ONE instance of the laboratory backend runs on the system.
Maintains a lease file at LOGS/lab.pid containing:
  - pid: OS Process ID
  - port: Listening TCP port (default 8787)
  - started_at: UNIX timestamp of backend startup
  - updated_at: UNIX timestamp of latest heartbeat
"""
from __future__ import annotations

import json
import logging
import os
import socket
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

log = logging.getLogger("lease")

PID_FILE_PATH = Path(__file__).resolve().parents[2] / "LOGS" / "lab.pid"


def is_pid_alive(pid: int) -> bool:
    """Check if process with given PID is currently active."""
    if pid <= 0:
        return False
    try:
        import psutil
        return psutil.pid_exists(pid)
    except Exception:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False


def check_port_free(port: int = 8787) -> Tuple[bool, str]:
    """Verify whether TCP port is available for binding."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        res = s.connect_ex(("127.0.0.1", port))
        if res == 0:
            return False, f"Port {port} is already actively listening/occupied."
        return True, ""
    except Exception as e:
        return False, f"Error checking port {port}: {e}"
    finally:
        try:
            s.close()
        except Exception:
            pass


class PidLeaseManager:
    def __init__(self, pid_file: Optional[Path] = None, port: int = 8787):
        self.pid_file = pid_file or PID_FILE_PATH
        self.port = port
        self._acquired = False

    def get_current_lease(self) -> Optional[Dict[str, Any]]:
        """Read and return current lease metadata if file exists."""
        if not self.pid_file.exists():
            return None
        try:
            data = json.loads(self.pid_file.read_text(encoding="utf-8"))
            return data
        except Exception:
            return None

    def acquire(self) -> Tuple[bool, str]:
        """Attempt to acquire PID lease. Returns (success, message)."""
        current_pid = os.getpid()

        # Check existing lease file
        if self.pid_file.exists():
            lease = self.get_current_lease()
            if lease:
                existing_pid = lease.get("pid")
                if existing_pid and existing_pid != current_pid:
                    if is_pid_alive(existing_pid):
                        msg = (
                            f"Another laboratory backend instance is already running (PID: {existing_pid}, "
                            f"Port: {lease.get('port', self.port)}). Dual instance blocked."
                        )
                        log.warning("[LEASE] %s", msg)
                        return False, msg
                    else:
                        log.info("[LEASE] Detected stale PID lease from deceased process %d. Overwriting.", existing_pid)

        # Pre-bind port check
        free, reason = check_port_free(self.port)
        if not free:
            log.warning("[LEASE] Pre-bind port check failed: %s", reason)
            # If port is occupied by another process, fail safely
            return False, reason

        # Write fresh lease file
        try:
            self.pid_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "pid": current_pid,
                "port": self.port,
                "started_at": time.time(),
                "updated_at": time.time(),
                "version": "V2.9",
            }
            self.pid_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            self._acquired = True
            log.info("[LEASE] Acquired PID lease for PID %d on port %d", current_pid, self.port)
            return True, f"Lease acquired for PID {current_pid}"
        except Exception as e:
            msg = f"Failed to write PID lease file: {e}"
            log.error("[LEASE] %s", msg)
            return False, msg

    def update_heartbeat(self) -> None:
        """Update lease heartbeat timestamp."""
        if not self._acquired or not self.pid_file.exists():
            return
        try:
            lease = self.get_current_lease() or {}
            lease["updated_at"] = time.time()
            self.pid_file.write_text(json.dumps(lease, indent=2), encoding="utf-8")
        except Exception:
            pass

    def release(self) -> None:
        """Release PID lease if owned by current process."""
        current_pid = os.getpid()
        if self.pid_file.exists():
            try:
                lease = self.get_current_lease()
                if lease and lease.get("pid") == current_pid:
                    self.pid_file.unlink(missing_ok=True)
                    log.info("[LEASE] Released PID lease for PID %d", current_pid)
            except Exception as e:
                log.warning("[LEASE] Failed to release PID lease: %s", e)
        self._acquired = False


_default_lease_mgr: Optional[PidLeaseManager] = None


def get_lease_manager(port: int = 8787) -> PidLeaseManager:
    global _default_lease_mgr
    if _default_lease_mgr is None:
        _default_lease_mgr = PidLeaseManager(port=port)
    return _default_lease_mgr
