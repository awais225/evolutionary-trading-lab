"""
Frontend build guard — the launcher's authoritative check that the bundle it is
about to serve was built from the *current* ``frontend/src``.

Used by ``start.bat`` (and available by hand). Standard library only, so it runs
with the project's own virtual environment — no Node.js is required for the
checks themselves (Node is only needed to *build*).

Exit codes
----------
  ``--check``            0 = fresh, 10 = stale/missing/unstamped (build needed),
                         20 = broken bundle
  ``--stamp``            0 = stamp written, 1 = nothing to stamp
  ``--verify-served URL``0 = the running instance serves THIS repository, from
                         THIS source, with THIS bundle, by the process the
                         launcher started (when --expect-token is given)
                         11 = different repository/root
                         12 = same repository, but an older/different build
                         13 = instance is an older lab build without /system/build
                         14 = nothing answering on that URL
                         15 = the answering PROCESS is stale — started from
                              different code, or before the current bundle was
                              built, or it is not the process we just started
  ``--scan``             0 = the report was printed (SCAN_FOUND counts the lab
                         listeners seen; a completed scan is always a success)
  ``--identify URL``     0 = it is this lab answering, 1 = not this lab
  ``--stop URL``         0 = a lab instance identified and stopped (port freed),
                         3 = occupant is not this lab (nothing was touched),
                         4 = could not stop it, 5 = still listening afterwards

Safety: ``--stop`` never signals a process it has not identified as this lab.
It first tries the lab's own graceful shutdown (``POST /api/power/shutdown``);
only if that is unavailable (pre-V5 build) does it terminate the exact PID that
owns the dashboard port, after ``/health`` has confirmed the app identity.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

ROOT_DEFAULT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DEFAULT / "backend"))

from app import frontend_build as fb                                   # noqa: E402

LAB_MARKERS = ("EVOLUTIONARY TRADING RESEARCH LAB", "evolutionary trading lab")
SHUTDOWN_PHRASE = "SHUTDOWN DASHBOARD"


# --------------------------------------------------------------------------- #
# output helpers
# --------------------------------------------------------------------------- #
def emit(pairs: Dict[str, Any]) -> None:
    """KEY=VALUE lines — readable by a human and parsable by the launcher."""
    for k, v in pairs.items():
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)
        print(f"{k}={v}", flush=True)


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #
def _get_json(url: str, timeout: float = 3.0) -> Tuple[Optional[int], Any]:
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return r.getcode(), json.loads(raw)
            except ValueError:
                return r.getcode(), raw
    except urllib.error.HTTPError as e:                               # server answered, with an error code
        return e.code, None
    except Exception:
        return None, None


def _post_json(url: str, body: Dict[str, Any], timeout: float = 20.0) -> Tuple[Optional[int], Any]:
    try:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
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


def port_listening(host: str, port: int, timeout: float = 0.8) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex((host, port)) == 0
    finally:
        s.close()


def is_this_lab(base_url: str) -> bool:
    """Identify the app answering on the dashboard port (never guess)."""
    base = base_url.rstrip("/")
    code, body = _get_json(f"{base}/system/status", timeout=2.5)
    if code == 200 and isinstance(body, dict) and ("manifest" in body or "app_version" in body):
        return True
    code, body = _get_json(f"{base}/health", timeout=2.5)
    if code in (200, 503):
        if isinstance(body, dict) and ("startup" in body or "app_version" in body):
            return True
        if isinstance(body, str):
            return any(m.lower() in body.lower() for m in LAB_MARKERS)
    for path in ("/api/health/legacy", "/health"):
        code, body = _get_json(f"{base}{path}", timeout=2.5)
        if isinstance(body, dict) and "app_version" in body:
            return True
    return False


# --------------------------------------------------------------------------- #
# process lookup / stop
# --------------------------------------------------------------------------- #
def pid_on_port(port: int) -> Optional[int]:
    """PID listening on ``port`` — Windows ``netstat``, else lsof/ss. No guessing."""
    try:
        if os.name == "nt":
            out = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True,
                                 text=True, timeout=15).stdout
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[3].upper() == "LISTENING":
                    if parts[1].endswith(f":{port}"):
                        try:
                            return int(parts[4])
                        except ValueError:
                            continue
            return None
        for cmd in (["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
                    ["fuser", f"{port}/tcp"],
                    ["ss", "-ltnp"]):
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            except FileNotFoundError:
                continue
            out = (res.stdout or "").strip()
            if not out:
                continue
            if cmd[0] == "ss":
                for line in out.splitlines():
                    if f":{port}" in line and "pid=" in line:
                        frag = line.split("pid=")[1].split(",")[0]
                        if frag.isdigit():
                            return int(frag)
                continue
            for tok in out.replace("\n", " ").split():
                if tok.isdigit():
                    return int(tok)
    except Exception:                                                 # pragma: no cover - defensive
        pass
    return None


def kill_pid(pid: int) -> bool:
    try:
        if os.name == "nt":
            res = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                                 capture_output=True, text=True, timeout=30)
            return res.returncode == 0
        os.kill(pid, 15)
        return True
    except Exception:                                                 # pragma: no cover - defensive
        return False


def wait_port_free(port: int, seconds: float = 30.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if not port_listening("127.0.0.1", port):
            return True
        time.sleep(1.0)
    return not port_listening("127.0.0.1", port)


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def cmd_check(root: Path) -> int:
    info = fb.check_dist(root)
    emit({
        "GUARD_STATUS": info["status"],
        "GUARD_OK": bool(info["ok"]),
        "GUARD_NEEDS_BUILD": bool(info["needs_build"]),
        "FRONTEND_SRC": str(fb.frontend_dir(root) / fb.SRC_DIRNAME),
        "FRONTEND_DIST": info["dist_dir"],
        "SRC_HASH": info["src_hash"][:16],
        "SRC_FILES": info["src_file_count"],
        "DIST_ENTRY_ASSETS": info["entry_assets"],
        "REASONS": info["reasons"],
    })
    if info["status"] == "FRESH":
        print("[OK] frontend/dist was built from the current frontend/src.", flush=True)
        return 0
    if info["status"] == "BROKEN":
        print(f"[ERROR] frontend/dist is not servable ({info['status']}): "
              + "; ".join(info["reasons"]), flush=True)
        return 20
    print(f"[ACTION] frontend/dist must be rebuilt ({info['status']}): "
          + "; ".join(info["reasons"]), flush=True)
    return 10


def cmd_stamp(root: Path, built_by: str) -> int:
    index = fb.dist_dir(root) / "index.html"
    if not index.is_file():
        print("[ERROR] refusing to stamp: frontend/dist/index.html does not exist.", file=sys.stderr, flush=True)
        return 1
    stamp = fb.write_stamp(root, built_by=built_by)
    emit({"GUARD_STAMPED": True, "SRC_HASH": stamp["src_hash"][:16],
          "INDEX_SHA256": (stamp["index_sha256"] or "")[:16],
          "DIST_ENTRY_ASSETS": stamp["entry_assets"], "BUILT_AT": stamp["built_at"]})
    print("[OK] recorded frontend/dist/build-info.json for the current source.", flush=True)
    return 0


def cmd_summary(root: Path) -> int:
    info = fb.check_dist(root)
    stamp = info["stamp"] or {}
    print(json.dumps({
        "status": info["status"],
        "matches_src": info["ok"],
        "src_hash": info["src_hash"],
        "src_files": info["src_file_count"],
        "dist_entry_assets": info["entry_assets"],
        "stamp": {k: stamp.get(k) for k in ("built_at", "built_by", "git_head", "src_file_count")},
        "reasons": info["reasons"],
    }, indent=2), flush=True)
    return 0 if info["ok"] else 10


def _served(url: str) -> Tuple[int, Dict[str, Any]]:
    base = url.rstrip("/")
    code, body = _get_json(f"{base}/system/build", timeout=3.0)
    if code == 200 and isinstance(body, dict):
        return 0, body
    if code in (404, 405):
        return 13, {}
    return 14, {}


def local_code_hash(root: Path) -> str:
    """The code fingerprint of the checkout on disk (same function the backend uses)."""
    try:
        from app.runtime_identity import code_fingerprint
        return str(code_fingerprint(Path(root))["code_hash"])
    except Exception:                                                 # pragma: no cover - defensive
        return ""


def cmd_verify_served(root: Path, url: str, expect_token: Optional[str] = None) -> int:
    local = fb.check_dist(root)
    local_code = local_code_hash(root)
    code, served = _served(url)
    if code == 13:
        emit({"SERVED_STATUS": "LEGACY_INSTANCE",
              "SERVED_REASON": "the instance on this port predates /system/build, so it cannot "
                               "serve the current frontend"})
        print("[ACTION] the running instance is an older lab build (no /system/build).", flush=True)
        return 13
    if code == 14:
        emit({"SERVED_STATUS": "UNREACHABLE",
              "SERVED_REASON": f"nothing answered at {url}/system/build"})
        print(f"[ACTION] nothing answered at {url}/system/build.", flush=True)
        return 14

    served_root = str(served.get("backend_root") or "")
    same_root = os.path.normcase(os.path.abspath(served_root)) == os.path.normcase(os.path.abspath(str(root)))
    served_hash = str(served.get("src_hash") or "")
    proc = served.get("process") or {}
    loaded = served.get("loaded") or {}
    serving = served.get("served") or {}

    proc_code = str(proc.get("code_hash") or "")
    proc_token = str(proc.get("start_token") or "")
    proc_stale_code = bool(local_code and proc_code and proc_code != local_code)
    proc_no_code = not proc_code                      # a build that predates identity
    bundle_changed = bool(serving.get("changed_since_start"))
    token_mismatch = bool(expect_token and proc_token != expect_token)
    loaded_index = str(loaded.get("index_sha256") or "")
    local_index = str(local.get("index_sha256") or "")

    emit({
        "SERVED_STATUS": served.get("dist_status"),
        "SERVED_ROOT": served_root,
        "SERVED_SRC_HASH": served_hash[:16],
        "LOCAL_ROOT": str(root),
        "LOCAL_SRC_HASH": local["src_hash"][:16],
        "SAME_ROOT": same_root,
        "SERVED_MATCHES_LOCAL_SRC": served_hash == local["src_hash"],
        "SERVED_DIST_MATCHES_SRC": bool(served.get("dist_matches_src")),
        "SERVED_REASONS": served.get("reasons"),
        "PROCESS_PID": proc.get("pid"),
        "PROCESS_STARTED": proc.get("started_iso"),
        "PROCESS_UPTIME_S": proc.get("uptime_s"),
        "PROCESS_TOKEN": proc_token or None,
        "PROCESS_CODE_HASH": proc_code[:16] or None,
        "LOCAL_CODE_HASH": local_code[:16] or None,
        "PROCESS_CODE_IS_CURRENT": (proc_code == local_code) if (proc_code and local_code) else None,
        "PROCESS_BUNDLE_IS_CURRENT": (loaded_index == local_index) if (loaded_index and local_index) else None,
        "PROCESS_BUNDLE_CHANGED_SINCE_START": bundle_changed,
        "EXPECTED_TOKEN": expect_token or None,
    })

    if not same_root:
        print(f"[ACTION] the instance on this port serves a different repository "
              f"({served_root or 'unknown'}), not {root}.", flush=True)
        return 11
    if served_hash != local["src_hash"] or not served.get("dist_matches_src"):
        print("[ACTION] the instance on this port serves an older/different frontend build.", flush=True)
        return 12

    # ---- the filesystem agrees; now prove the PROCESS agrees -----------------
    if expect_token and token_mismatch:
        print(f"[ACTION] the process answering on this port is not the instance this launcher "
              f"started (start token {proc_token or 'absent'} != {expect_token}).", flush=True)
        return 15
    if proc_no_code:
        print("[ACTION] the answering process predates runtime identity, so it cannot be proven "
              "to run the current code; it must be restarted.", flush=True)
        return 15
    if proc_stale_code:
        print(f"[ACTION] the answering process was started from different backend code "
              f"({proc_code[:16]}) than the checkout on disk ({local_code[:16]}) - "
              f"a stale process, not a stale bundle.", flush=True)
        return 15
    if bundle_changed:
        print("[ACTION] the answering process survived a rebuild: it was started with bundle "
              f"{loaded_index[:16]}, the current bundle is {local_index[:16]}.", flush=True)
        return 15
    print("[OK] the running instance serves this repository at the current build, "
          "from the current code, with the current bundle.", flush=True)
    return 0


def cmd_scan(root: Path, ports: Sequence[int] = (8787, 5173)) -> int:
    """Report every lab-ish listener and the identity it presents.

    This answers, from the machine itself, "which physical index.html is my
    application serving?" — including the trap where a browser was opened on a
    dev server, or on a second checkout's backend.
    """
    found = 0
    local_src = fb.check_dist(root)["src_hash"][:16]
    emit({"SCAN_LOCAL_ROOT": str(root), "SCAN_LOCAL_SRC_HASH": local_src,
          "SCAN_PORTS": list(ports)})
    for port in ports:
        if not port_listening("127.0.0.1", port):
            emit({f"PORT_{port}": "free"})
            continue
        pid = pid_on_port(port)
        base = f"http://127.0.0.1:{port}"
        code, body = _get_json(f"{base}/system/build", timeout=2.5)
        if code == 200 and isinstance(body, dict):
            proc = body.get("process") or {}
            found += 1
            emit({f"PORT_{port}": "lab-backend",
                  f"PORT_{port}_PID": pid or proc.get("pid"),
                  f"PORT_{port}_ROOT": body.get("backend_root"),
                  f"PORT_{port}_SRC_HASH": str(body.get("src_hash") or "")[:16],
                  f"PORT_{port}_DIST": body.get("frontend_dist"),
                  f"PORT_{port}_INDEX_HTML": str(Path(str(body.get("frontend_dist") or "")) / "index.html"),
                  f"PORT_{port}_SERVED_INDEX": str((body.get("served") or {}).get("index_sha256") or "")[:16],
                  f"PORT_{port}_ENTRY_ASSETS": (body.get("served") or {}).get("entry_assets"),
                  f"PORT_{port}_TOKEN": (proc.get("start_token") or None),
                  f"PORT_{port}_STARTED": proc.get("started_iso")})
            continue
        code, health = _get_json(f"{base}/health", timeout=2.5)
        if is_this_lab(base) or (isinstance(health, dict) and "startup" in health):
            found += 1
            emit({f"PORT_{port}": "lab-backend-legacy", f"PORT_{port}_PID": pid,
                  f"PORT_{port}_NOTE": "predates /system/build: identity cannot be read, "
                                       "this instance must not be reused"})
            continue
        kind = "possibly-frontend-dev-server" if port == 5173 else "foreign"
        emit({f"PORT_{port}": kind, f"PORT_{port}_PID": pid,
              f"PORT_{port}_NOTE": ("a browser opened on this port shows whatever that process "
                                    "serves - it is not the launcher's dashboard" if kind != "foreign"
                                    else "unrelated software: never touched by this lab")})
    # V6.4 — --scan is a READ-ONLY REPORT ("report every lab-ish listener"): a
    # completed scan is exit 0 whether or not a lab is currently listening.
    # SCAN_FOUND states the count in machine-readable form; the exit code never
    # hides the report behind a "nothing found" failure.
    emit({"SCAN_FOUND": found})
    return 0


def cmd_identify(url: str) -> int:
    if not port_listening("127.0.0.1", _port_of(url)):
        print("[ACTION] nothing is listening on that port.", flush=True)
        return 1
    if is_this_lab(url):
        print("[OK] the occupant of this port is an Evolutionary Trading Research Lab backend.", flush=True)
        return 0
    print("[ACTION] the occupant of this port is NOT this lab - leaving it untouched.", flush=True)
    return 1


def _port_of(url: str) -> int:
    try:
        return int(url.rstrip("/").split("://")[-1].split("/")[0].split(":")[1])
    except Exception:
        return 8787


def cmd_stop(root: Path, url: str, port: Optional[int]) -> int:
    port = port or _port_of(url)
    base = url.rstrip("/")
    if not port_listening("127.0.0.1", port):
        print("[OK] nothing is listening - port already free.", flush=True)
        return 0
    if not is_this_lab(base):
        print("[ACTION] refusing to stop: the process on this port is not this lab.", flush=True)
        return 3

    # 1. prefer the lab's own graceful shutdown (V5 API)
    code, body = _post_json(f"{base}/api/power/shutdown",
                            {"confirm": SHUTDOWN_PHRASE, "reason": "launcher: replacing a stale instance"})
    if code == 200:
        print("[OK] existing instance accepted the dashboard shutdown request.", flush=True)
        if wait_port_free(port, 45):
            emit({"STOP_METHOD": "power_api", "STOPPED": True})
            return 0
        print("[ACTION] the instance acknowledged shutdown but the port is still busy.", flush=True)
    elif code is None:
        print("[ACTION] the instance did not answer the shutdown request.", flush=True)
    else:
        print(f"[ACTION] the instance refused the graceful shutdown (HTTP {code}); "
              f"falling back to stopping the exact PID that owns port {port}.", flush=True)

    # 2. fall back to the exact PID owning the dashboard port (identity already confirmed)
    pid = pid_on_port(port)
    if not pid:
        print("[ERROR] could not determine which process owns the dashboard port.", file=sys.stderr, flush=True)
        return 4
    emit({"STOP_PID": pid, "STOP_METHOD": "pid"})
    if not kill_pid(pid):
        print(f"[ERROR] could not stop PID {pid}.", file=sys.stderr, flush=True)
        return 4
    if wait_port_free(port, 30):
        emit({"STOPPED": True})
        print(f"[OK] stopped the previous lab instance (PID {pid}).", flush=True)
        return 0
    print(f"[ERROR] port {port} is still listening after stopping PID {pid}.", file=sys.stderr, flush=True)
    return 5


# --------------------------------------------------------------------------- #
def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="Evolutionary Trading Lab — frontend build guard")
    ap.add_argument("--root", default=str(ROOT_DEFAULT), help="repository root (default: parents[2] of this file)")
    ap.add_argument("--check", action="store_true", help="verify dist matches src (default)")
    ap.add_argument("--stamp", action="store_true", help="record frontend/dist/build-info.json after a build")
    ap.add_argument("--summary", action="store_true", help="print a JSON summary of the bundle identity")
    ap.add_argument("--print-summary", dest="summary", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--verify-served", metavar="URL", help="compare a running instance with this root/build")
    ap.add_argument("--expect-token", metavar="TOKEN",
                    help="require the answering process to present this start token (proves it is "
                         "the process this launch started)")
    ap.add_argument("--scan", action="store_true",
                    help="report every lab-ish listener (8787/5173) and the identity it presents")
    ap.add_argument("--identify", metavar="URL", help="is the instance on URL this lab?")
    ap.add_argument("--stop", metavar="URL", help="stop a lab instance on URL (graceful, else exact PID)")
    ap.add_argument("--port", type=int, default=8787, help="dashboard port (default 8787)")
    ap.add_argument("--built-by", default="frontend_build_guard", help="who is stamping the build")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()

    if args.stamp:
        return cmd_stamp(root, args.built_by)
    if args.summary:
        return cmd_summary(root)
    if args.verify_served:
        return cmd_verify_served(root, args.verify_served, expect_token=args.expect_token)
    if args.scan:
        return cmd_scan(root)
    if args.identify:
        return cmd_identify(args.identify)
    if args.stop:
        return cmd_stop(root, args.stop, args.port)
    return cmd_check(root)


if __name__ == "__main__":
    sys.exit(main())
