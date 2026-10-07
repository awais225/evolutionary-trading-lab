"""
Port and Lab Instance Checker for Evolutionary Trading Research Lab
Authoritative tool used by START.bat.

Checks port 8787 (or passed port):
  - Exit code 0  = FREE (ready to start backend)
  - Exit code 10 = OUR_LAB (an Evolutionary Trading Research Lab backend answers
                   on this port - identified by the lab's own API, never by the
                   mere fact that /health returned HTTP 200)
  - Exit code 20 = OCCUPIED (something else is listening: unrelated software, or
                   a process that cannot be identified as this lab)

The distinction matters: the launcher only ever stops a process that has been
positively identified as this application, and it warns (rather than silently
reusing) anything it cannot identify.
"""
from __future__ import annotations

import json
import socket
import sys
import urllib.error
import urllib.request

try:
    import psutil
except ImportError:
    psutil = None


def _json(url: str, timeout: float = 1.5):
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return r.getcode(), json.loads(raw)
            except ValueError:
                return r.getcode(), raw
    except urllib.error.HTTPError as e:          # the server answered, with an error code
        return e.code, None
    except Exception:
        return None, None


def identify_lab(port: int = 8787) -> str:
    """``"lab"`` | ``"legacy-lab"`` | ``"other"`` | ``"none"`` — measured, not assumed.

    * ``lab``        - answers ``/system/build`` with the lab's own payload, so
                       its identity (root, source hash, process) can be read;
    * ``legacy-lab`` - answers ``/health`` with the lab's health schema but has
                       no ``/system/build`` (a build from before that endpoint):
                       it is this application, but it cannot be proven current;
    * ``other``      - something is listening and it is not this application;
    * ``none``       - nothing is listening.
    """
    code, body = _json(f"http://127.0.0.1:{port}/system/build")
    if code == 200 and isinstance(body, dict) and body.get("app"):
        return "lab"
    code, body = _json(f"http://127.0.0.1:{port}/health")
    if code in (200, 503) and isinstance(body, dict):
        if "startup" in body or "app_version" in body or "ready" in body:
            return "legacy-lab"
    if code is not None and isinstance(body, str) and "evolutionary trading" in body.lower():
        return "legacy-lab"
    if code is not None:
        return "other"
    return "none"


def check_port(port: int = 8787) -> int:
    # 1. Is OUR lab already answering here? Identify it, do not guess from a 200.
    kind = identify_lab(port)
    if kind == "lab":
        print(f"[OK] Existing Evolutionary Trading Research Lab backend detected on port {port} "
              f"(identified by /system/build).", flush=True)
        return 10
    if kind == "legacy-lab":
        print(f"[OK] An older Evolutionary Trading Research Lab backend is on port {port} "
              f"(no /system/build: it cannot prove which build it serves and will be replaced).",
              flush=True)
        return 10

    # 2. Check if TCP port is accepting connections
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.8)
    res = s.connect_ex(("127.0.0.1", port))
    s.close()

    if res != 0:
        # Connection refused: port is free
        print(f"[OK] Port {port} is available.", flush=True)
        return 0

    # 3. Port is occupied by something that is not this lab
    occupying_pid = "UNKNOWN"
    if psutil:
        try:
            for c in psutil.net_connections(kind="tcp"):
                if c.laddr.port == port and c.status == "LISTEN" and c.pid:
                    occupying_pid = str(c.pid)
                    break
        except Exception:
            pass

    if kind == "other":
        code, _ = _json(f"http://127.0.0.1:{port}/health")
        print(f"[ERROR] Port {port} is answered by something that is NOT this lab "
              f"(HTTP {code} on /health, no lab identity). PID: {occupying_pid}. "
              f"Unrelated software is never stopped by this launcher.", flush=True)
    else:
        print(f"[ERROR] Port {port} is already in use (PID: {occupying_pid}).", flush=True)
    return 20


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
    sys.exit(check_port(port))
