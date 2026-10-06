"""
Port and Lab Instance Checker for Evolutionary Trading Research Lab
Authoritative tool used by START.bat.

Checks port 8787 (or passed port):
  - Exit code 0  = FREE (ready to start backend)
  - Exit code 10 = OUR_LAB (existing lab instance healthy on this port)
  - Exit code 20 = OCCUPIED (occupied by an unrelated process)
"""
from __future__ import annotations

import socket
import sys
import urllib.request

try:
    import psutil
except ImportError:
    psutil = None


def check_port(port: int = 8787) -> int:
    # 1. Try health endpoint first to see if our lab is already running
    try:
        req = urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1.5)
        if req.getcode() == 200:
            print(f"[OK] Existing Evolutionary Trading Research Lab backend detected on port {port}.", flush=True)
            return 10
    except Exception:
        pass

    # 2. Check if TCP port is accepting connections
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.8)
    res = s.connect_ex(("127.0.0.1", port))
    s.close()

    if res != 0:
        # Connection refused: port is free
        print(f"[OK] Port {port} is available.", flush=True)
        return 0

    # 3. Port is occupied by unrelated process
    occupying_pid = "UNKNOWN"
    if psutil:
        try:
            for c in psutil.net_connections(kind="tcp"):
                if c.laddr.port == port and c.status == "LISTEN" and c.pid:
                    occupying_pid = str(c.pid)
                    break
        except Exception:
            pass

    print(f"[ERROR] Port {port} is already in use (PID: {occupying_pid}).", flush=True)
    return 20


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
    sys.exit(check_port(port))
