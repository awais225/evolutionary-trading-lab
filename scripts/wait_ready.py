"""Readiness poller — authoritative tool used by START.bat (adapted from legacy).

Polls a URL until it reports the expected identity or the timeout expires.
  --require backend   expect Hero1 /health signature (status ok|degraded + registry_db)
  --require frontend  expect HTTP 200 HTML containing the Hero1 dashboard title
  --require any       expect any HTTP 200
Exit 0 ready / 1 timeout / 2 misuse.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_port import is_hero1_health, is_hero1_frontend  # noqa: E402


def wait_for_ready(url: str, max_seconds: int, require: str) -> int:
    start = time.time()
    attempts = 0
    while time.time() - start < max_seconds:
        attempts += 1
        try:
            req = urllib.request.Request(url, headers={"Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=1.5) as r:
                code = r.getcode()
                raw = r.read().decode("utf-8", "replace")
            ok = False
            if require == "any":
                ok = code == 200
            elif require == "backend":
                try:
                    ok = code == 200 and is_hero1_health(json.loads(raw))
                except ValueError:
                    ok = False
            elif require == "frontend":
                ok = code == 200 and is_hero1_frontend(raw)
            if ok:
                print(f"[OK] {url} answered as Hero1 ({require}).", flush=True)
                return 0
        except urllib.error.HTTPError:
            pass
        except Exception:
            pass
        print(f"[..] Waiting for {url} ({require})... ({int(time.time() - start)}s/{max_seconds}s)", flush=True)
        time.sleep(1.0)
    print(f"[ERROR] {url} did not become ready ({require}) within {max_seconds} seconds.", file=sys.stderr, flush=True)
    return 1


if __name__ == "__main__":
    args = sys.argv[1:]
    url = args[0] if args else "http://127.0.0.1:8788/health"
    timeout = int(args[1]) if len(args) > 1 else 120
    require = "any"
    if "--require" in args:
        i = args.index("--require")
        require = args[i + 1]
    if require not in ("backend", "frontend", "any"):
        print(f"misuse: --require must be backend|frontend|any, got {require!r}", file=sys.stderr)
        sys.exit(2)
    sys.exit(wait_for_ready(url, timeout, require))
