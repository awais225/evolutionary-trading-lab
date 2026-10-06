"""
Health Check and Readiness Poller for Evolutionary Trading Research Lab
Authoritative tool used by START.bat.

Polls http://127.0.0.1:8787/health until HTTP 200 is received or timeout expires.
Returns:
  0 = Backend is online and healthy
  1 = Timeout reached
"""
from __future__ import annotations

import sys
import time
import urllib.request


def wait_for_ready(url: str = "http://127.0.0.1:8787/health", max_seconds: int = 30) -> int:
    start_time = time.time()
    attempts = 0
    while time.time() - start_time < max_seconds:
        attempts += 1
        try:
            req = urllib.request.urlopen(url, timeout=1.5)
            if req.getcode() == 200:
                print(f"[OK] Backend health check passed.", flush=True)
                return 0
        except Exception:
            pass
        print(f"[..] Waiting for backend health check... ({attempts}/{max_seconds}s)", flush=True)
        time.sleep(1.0)

    print(f"[ERROR] Backend failed to respond at {url} within {max_seconds} seconds.", file=sys.stderr, flush=True)
    return 1


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8787/health"
    timeout = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    sys.exit(wait_for_ready(url, timeout))
