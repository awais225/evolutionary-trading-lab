"""Port and Hero1 instance checker — authoritative tool used by START.bat.
Adapted from the proven legacy Evolutionary Trading Lab utility (v10.4-throughput).

Modes:
  python scripts/check_port.py [PORT]             backend mode (default PORT=8788)
      exit 0  FREE       nothing listening / nothing answering
      exit 10 OURS       an identity-verified Hero1 backend answers (never a bare HTTP 200)
      exit 20 FOREIGN    something else is listening (unrelated software / unidentifiable)
  python scripts/check_port.py --frontend [PORT]     frontend mode (default PORT=5173)
      exit 0  FREE | exit 10 OURS (Hero1 dashboard HTML) | exit 20 FOREIGN
"""
from __future__ import annotations

import json
import socket
import sys
import urllib.error
import urllib.request


def _get(url: str, timeout: float = 1.5):
    try:
        req = urllib.request.Request(url, headers={"Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return r.getcode(), json.loads(raw)
            except ValueError:
                return r.getcode(), raw
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:
        return None, None


def is_hero1_health(body) -> bool:
    """Hero1 backend signature — measured, not assumed from HTTP 200 alone."""
    return (isinstance(body, dict)
            and body.get("status") in ("ok", "degraded")
            and isinstance(body.get("checks"), dict)
            and "registry_db" in body["checks"]
            and isinstance(body.get("version"), str))


def is_hero1_frontend(body) -> bool:
    """Vite dev server / static host serving the actual Hero1 dashboard shell."""
    if not isinstance(body, str):
        return False
    low = body.lower()
    return "<title>hero1" in low or "hero1 — trading research workstation" in low


def port_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.6)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except OSError:
            return False


def check_backend(port: int) -> int:
    code, body = _get(f"http://127.0.0.1:{port}/health")
    if is_hero1_health(body):
        print(f"[OK] Port {port}: HERO1 backend answers /health (status={body.get('status')}).")
        return 10
    if code is not None:
        print(f"[WARN] Port {port}: HTTP {code} but NOT the Hero1 health schema — treating as foreign.")
        return 20
    if port_listening(port):
        print(f"[WARN] Port {port}: something is listening but did not answer /health — treating as foreign.")
        return 20
    print(f"[OK] Port {port} is free.")
    return 0


def check_frontend(port: int) -> int:
    code, body = _get(f"http://127.0.0.1:{port}/")
    if code == 200 and is_hero1_frontend(body):
        print(f"[OK] Port {port}: HERO1 dashboard is served.")
        return 10
    if code is not None:
        print(f"[WARN] Port {port}: HTTP {code} but not the Hero1 dashboard — treating as foreign.")
        return 20
    if port_listening(port):
        print(f"[WARN] Port {port}: something is listening but did not answer / — treating as foreign.")
        return 20
    print(f"[OK] Port {port} is free.")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    frontend = False
    if args and args[0] == "--frontend":
        frontend = True
        args = args[1:]
    port = int(args[0]) if args else (5173 if frontend else 8788)
    sys.exit(check_frontend(port) if frontend else check_backend(port))
