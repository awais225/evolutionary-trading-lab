"""Post-start instance verification — used by START.bat (adapted from legacy --verify-served).

Proves the answering server IS the one this launch started, running THIS code.
Modes:
  --mode backend --url U [--root REPO_ROOT] [--token T]
      /health must carry the Hero1 signature; if --root given and the server
      reports project_root (env HERO1_REPO_ROOT), they must match; if --token
      given and the server reports start_token, they must match.
      A mismatch means the port is answered by a DIFFERENT Hero1 (another
      checkout or an earlier launch) — never silently accepted.
  --mode frontend-proxy --url U
      The vite dev server must forward /health to OUR backend (proves
      frontend<->backend connectivity). A 200 page alone is NOT sufficient.

Exit 0 verified / 1 not verified / 2 misuse.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def _get(url: str, timeout: float = 2.0):
    try:
        req = urllib.request.Request(url, headers={"Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.getcode(), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception:
        return None, ""


def _hero1_health(raw: str):
    try:
        body = json.loads(raw)
    except ValueError:
        return None
    if (isinstance(body, dict) and body.get("status") in ("ok", "degraded")
            and isinstance(body.get("checks"), dict) and "registry_db" in body["checks"]
            and isinstance(body.get("version"), str)):
        return body
    return None


def verify_backend(url: str, root: str | None, token: str | None) -> int:
    code, raw = _get(url)
    body = _hero1_health(raw) if code and code < 500 else None
    if body is None:
        print(f"[FAIL] {url} did not answer with the Hero1 health schema (HTTP {code}).")
        return 1
    ok = True
    reported_root = body.get("project_root")
    reported_token = body.get("start_token")
    if root and reported_root:
        norm = os.path.normcase(os.path.normpath(root))
        rep = os.path.normcase(os.path.normpath(str(reported_root)))
        if norm != rep:
            print(f"[FAIL] /health project_root is {reported_root!r}, expected {root!r} — "
                  f"another Hero1 checkout answers this port.")
            ok = False
    elif root:
        print("[WARN] Server does not report project_root (started without HERO1_REPO_ROOT); "
              "identity limited to the health schema.")
    if token and reported_token:
        if reported_token != token:
            print(f"[FAIL] /health start_token mismatch — the answering process was NOT started "
                  f"by this launcher run.")
            ok = False
    elif token:
        print("[WARN] Server does not report start_token (HERO1_START_TOKEN not set in the "
              "backend process); cannot prove it is this launch.")
    if ok:
        print(f"[OK] Instance verified: Hero1 backend v{body.get('version')}, "
              f"registry_db={body.get('checks', {}).get('registry_db', {}).get('status', '?')}"
              + (f", root={reported_root}" if reported_root else "")
              + (f", token={reported_token}" if reported_token else "") + ".")
        return 0
    return 1


def verify_frontend_proxy(url: str) -> int:
    code, raw = _get(url)
    body = _hero1_health(raw) if code and code < 500 else None
    if body is None:
        print(f"[FAIL] {url} did not reach a Hero1 backend through the vite proxy "
              f"(HTTP {code}). The frontend page may render, but the API is NOT connected.")
        return 1
    print(f"[OK] Frontend proxy verified: {url} -> Hero1 backend "
          f"(registry_db={body.get('checks', {}).get('registry_db', {}).get('status', '?')}).")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=["backend", "frontend-proxy"])
    ap.add_argument("--url", required=True)
    ap.add_argument("--root", default=None)
    ap.add_argument("--token", default=None)
    args = ap.parse_args()
    if args.mode == "backend":
        sys.exit(verify_backend(args.url, args.root, args.token))
    sys.exit(verify_frontend_proxy(args.url))
