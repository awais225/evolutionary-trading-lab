#!/usr/bin/env python3
"""Runtime dashboard identity diagnostic — READ-ONLY.

Answers one question with evidence: *which* GitHub commit / source / bundle /
process / dashboard is actually running on the dashboard port right now?

It inspects, it never changes anything:

* it does not stop or start any process, rebuild the frontend, write into the
  repository, touch DATA, Git, configuration, MT5 or the browser;
* the only bytes it writes are the served ``index.html`` saved into the system
  temporary directory (deleted again), which the brief explicitly allows.

The single most important line it prints:

    RUNNING_DASHBOARD_MATCHES_GITHUB: PASS|FAIL

Usage (normally called by CHECK_RUNNING_DASHBOARD.bat)::

    python backend/tools/dashboard_diagnostic.py --root . --url http://127.0.0.1:8787

Options: ``--json`` (machine-readable), ``--no-remote`` (skip ``git ls-remote``),
``--timeout`` (HTTP seconds), ``--strict`` (exit 3 when the verdict is FAIL).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve()
BACKEND = HERE.parents[1]                      # backend/
REPO_DEFAULT = BACKEND.parent                  # repository root
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

DEFAULT_URL = "http://127.0.0.1:8787"

# Launchers the operator may be double-clicking (spec §13). Only the ones that
# exist are reported; nothing is executed.
LAUNCHER_FILES = [
    "START.bat", "start.bat", "RUN_BACKEND.bat", "Prerequisite.bat", "pre-requisite.bat",
    "repair.bat", "doctor.bat", "scripts/build_and_serve.bat", "scripts/build_exe.bat",
    "scripts/run_backend.bat", "scripts/start_lab.bat",
]

# V5 UI signature: user-visible strings the *browser* must receive. They are
# looked for in the HTTP-served assets only — never in local frontend/src.
V5_FEATURES: List[Tuple[str, List[str]]] = [
    ("PowerButton", ["SHUTDOWN DASHBOARD"]),
    ("Deep Backtest", ["Deep Backtest"]),
    ("Live Testing", ["Live Testing", "Amount / risk (money)"]),
    ("LiveTestResults", ["Live Test Results"]),
    ("MT5 Demo Trading", ["MT5 Demo Trading"]),
    ("Trading Info", ["Trading Info"]),
    ("Prop-firm monitor", ["Prop-firm"]),
    ("Schedule", ["Schedule"]),
    ("Deep Backtest FROM SCRATCH", ["FROM SCRATCH"]),
    ("V5 build identity", ["Build:"]),
]

ASSET_RE = re.compile(r"""["'(](?:\./)?(assets/[A-Za-z0-9._@-]+\.(?:js|css))(?:[?"')])""")
CHUNK_RE = re.compile(r"""(?:^|["'/])([A-Za-z0-9._@-]*[A-Za-z0-9._@-]+\.(?:js|css))["')]""")


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def code_hash_of(root: Path) -> str:
    """The backend code fingerprint of a checkout (the same function the running
    backend reports about itself), falling back to a hash of backend/app/**/*.py."""
    try:
        from app.runtime_identity import code_fingerprint
        return str(code_fingerprint(Path(root))["code_hash"])
    except Exception:
        digest = hashlib.sha256()
        base = Path(root) / "backend" / "app"
        for path in sorted(base.rglob("*.py")) if base.is_dir() else []:
            digest.update(path.relative_to(base).as_posix().encode())
            digest.update(path.read_bytes())
        return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> Optional[str]:
    try:
        return sha256_bytes(Path(path).read_bytes())
    except Exception:
        return None


def run(cmd: List[str], cwd: Optional[Path] = None, timeout: float = 30.0,
        env_extra: Optional[Dict[str, str]] = None) -> Tuple[int, str]:
    """Run a command read-only and return (exit_code, combined output)."""
    env = dict(os.environ)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    env.setdefault("GIT_PAGER", "cat")
    if env_extra:
        env.update(env_extra)
    try:
        proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env,
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=timeout)
        return proc.returncode, proc.stdout.decode("utf-8", "replace").strip()
    except FileNotFoundError:
        return 127, f"{cmd[0]} not found"
    except subprocess.TimeoutExpired:
        return 124, f"{' '.join(cmd)} timed out after {timeout:.0f}s"
    except Exception as exc:                                    # pragma: no cover - defensive
        return 1, f"{type(exc).__name__}: {exc}"


def http_get(url: str, timeout: float = 10.0) -> Dict[str, Any]:
    """GET a URL. Returns status/headers/body even for 4xx/5xx, never raises."""
    req = urllib.request.Request(url, headers={"Accept": "*/*", "User-Agent": "lab-dashboard-diagnostic"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            body = res.read()
            return {"ok": True, "status": res.getcode(), "headers": {k.lower(): v for k, v in res.headers.items()},
                    "body": body}
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read()
        except Exception:
            body = b""
        return {"ok": False, "status": exc.code, "headers": dict(exc.headers or {}), "body": body}
    except Exception as exc:
        return {"ok": False, "status": None, "headers": {}, "body": b"", "error": f"{type(exc).__name__}: {exc}"}


def section(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def verdict_line(name: str, ok: bool) -> str:
    return f"{name}: {'PASS' if ok else 'FAIL'}"


# --------------------------------------------------------------------------- #
# 1. git identity
# --------------------------------------------------------------------------- #
def git_identity(root: Path, remote: bool = True, timeout: float = 30.0) -> Dict[str, Any]:
    info: Dict[str, Any] = {"root": str(root)}
    code, head = run(["git", "rev-parse", "HEAD"], cwd=root)
    info["local_head"] = head if code == 0 else None
    code, origin = run(["git", "rev-parse", "origin/main"], cwd=root)
    info["origin_main"] = origin if code == 0 else None
    code, branch = run(["git", "branch", "--show-current"], cwd=root)
    info["branch"] = branch if code == 0 else None
    code, status = run(["git", "status", "--short"], cwd=root, timeout=60.0)
    info["status_short"] = status if code == 0 else f"(git status failed: {status})"
    code, log = run(["git", "log", "-1", "--format=%H%n%h%n%ad%n%s"], cwd=root)
    if code == 0:
        parts = log.splitlines()
        info["last_commit"] = parts[0] if parts else None
        info["last_commit_short"] = parts[1] if len(parts) > 1 else None
        info["last_commit_date"] = parts[2] if len(parts) > 2 else None
        info["last_commit_subject"] = "\n".join(parts[3:]) if len(parts) > 3 else None
    if remote:
        code, ls = run(["git", "ls-remote", "origin", "refs/heads/main"], cwd=root, timeout=timeout)
        if code == 0 and ls:
            info["remote_main"] = ls.split()[0]
            info["remote_checked"] = True
        else:
            info["remote_main"] = None
            info["remote_checked"] = False
            info["remote_error"] = ls
    # the working tree's *tracked* state: untracked DATA/logs are expected and
    # irrelevant to identity, a tracked modification is not
    code, dirty = run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=root, timeout=60.0)
    info["tracked_changes"] = dirty if code == 0 else None
    info["git_identity_match"] = bool(
        info.get("local_head") and info.get("origin_main")
        and info["local_head"] == info["origin_main"]
        and (not info.get("remote_main") or info["remote_main"] == info["local_head"])
    )
    return info


# --------------------------------------------------------------------------- #
# 2. frontend source identity (the project's own tooling)
# --------------------------------------------------------------------------- #
def frontend_identity(root: Path) -> Dict[str, Any]:
    info: Dict[str, Any] = {}
    try:
        from app import frontend_build as fb
    except Exception as exc:                                    # pragma: no cover - defensive
        info["error"] = f"cannot import app.frontend_build: {exc}"
        info["frontend_build_match"] = False
        return info
    try:
        check = fb.check_dist(root)
        stamp = fb.read_stamp(root) or {}
        info.update({
            "src_dir": str(fb.frontend_dir(root) / "src"),
            "dist_dir": str(fb.dist_dir(root)),
            "src_hash": check.get("src_hash"),
            "src_file_count": check.get("src_file_count"),
            "index_sha256": check.get("index_sha256"),
            "entry_assets": check.get("entry_assets") or [],
            "dist_status": check.get("status"),
            "dist_matches_src": bool(check.get("ok")),
            "reasons": check.get("reasons") or [],
            "stamp": stamp,
            "build_timestamp": stamp.get("built_at"),
            "build_git_head": stamp.get("git_head"),
            "build_age_seconds": None,
            "dist_index_path": check.get("index_html"),
        })
        built = stamp.get("built_at")
        if built:
            try:
                ts = time.mktime(time.strptime(built, "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
                info["build_age_seconds"] = round(time.time() - ts, 1)
            except Exception:
                info["build_age_seconds"] = None
    except Exception as exc:                                    # pragma: no cover - defensive
        info["error"] = f"{type(exc).__name__}: {exc}"
    info["frontend_build_match"] = bool(info.get("dist_matches_src"))
    return info


# --------------------------------------------------------------------------- #
# 3. running process on the dashboard port
# --------------------------------------------------------------------------- #
def port_owner(port: int) -> Dict[str, Any]:
    """Best-effort owner of a listening TCP port. Windows detail is added by the
    BAT/PowerShell section; this covers the case where psutil is available."""
    info: Dict[str, Any] = {"port": port, "pid": None, "name": None, "exe": None,
                            "cmdline": None, "cwd": None, "source": None}
    try:
        import psutil
    except Exception:
        info["note"] = "psutil not available in this interpreter"
        return info
    try:
        conns = psutil.net_connections(kind="inet")
    except Exception as exc:
        info["note"] = f"net_connections unavailable: {exc}"
        return info
    for conn in conns:
        try:
            if conn.laddr and int(conn.laddr.port) == int(port) and conn.status == psutil.CONN_LISTEN:
                info["pid"] = conn.pid
                break
        except Exception:
            continue
    if info["pid"] is None:
        info["note"] = f"no listener seen on port {port}"
        return info
    try:
        proc = psutil.Process(int(info["pid"]))
        info["name"] = proc.name()
        info["exe"] = proc.exe()
        info["cmdline"] = " ".join(proc.cmdline())
        try:
            info["cwd"] = proc.cwd()
        except Exception:
            info["cwd"] = None
        info["source"] = "psutil"
    except Exception as exc:
        info["note"] = f"process detail unavailable: {exc}"
    return info


# --------------------------------------------------------------------------- #
# 4. served index.html and the assets the browser is told to load
# --------------------------------------------------------------------------- #
def fetch_served_index(url: str, timeout: float) -> Dict[str, Any]:
    res = http_get(url.rstrip("/") + "/", timeout=timeout)
    out: Dict[str, Any] = {
        "status": res.get("status"),
        "content_type": res["headers"].get("content-type"),
        "etag": res["headers"].get("etag"),
        "last_modified": res["headers"].get("last-modified"),
        "content_length": res["headers"].get("content-length"),
        "cache_control": res["headers"].get("cache-control"),
        "error": res.get("error"),
        "body_sha256": None,
        "script_references": [],
        "temp_path": None,
    }
    body = res.get("body") or b""
    if body:
        out["body_sha256"] = sha256_bytes(body)
        text = body.decode("utf-8", "replace")
        refs: List[str] = []
        for match in re.finditer(r"""(?:src|href)\s*=\s*["']([^"']+)["']""", text):
            ref = match.group(1).strip()
            if re.search(r"\.(?:js|css)(?:\?|$)", ref):
                refs.append(ref)
        out["script_references"] = refs
        # the brief asks for the served shell to be saved (outside the repo)
        try:
            tmp = Path(tempfile.mkdtemp(prefix="lab-dashboard-diag-"))
            target = tmp / "served-index.html"
            target.write_bytes(body)
            out["temp_path"] = str(target)
        except Exception:
            out["temp_path"] = None
    return out


def served_asset_bundle(url: str, entry_refs: List[str], timeout: float,
                        max_files: int = 80) -> Dict[str, Any]:
    """Fetch the served JS/CSS the browser loads, following the chunk references
    inside them, so the feature signature is judged on *served bytes*."""
    base = url.rstrip("/") + "/"
    files: Dict[str, Dict[str, Any]] = {}
    queue: List[str] = []
    for ref in entry_refs:
        name = ref.split("?")[0].lstrip("./")
        if name and name not in queue:
            queue.append(name)
    seen: List[str] = []
    while queue and len(files) < max_files:
        name = queue.pop(0)
        if name in files or name in seen:
            continue
        seen.append(name)
        res = http_get(base + name, timeout=timeout)
        body = res.get("body") or b""
        text = body.decode("utf-8", "replace")
        files[name] = {
            "http_status": res.get("status"),
            "bytes": len(body),
            "sha256": sha256_bytes(body) if body else None,
            "text": text,
        }
        if name.endswith(".js") and body:
            directory = name.rsplit("/", 1)[0] + "/" if "/" in name else ""
            for match in CHUNK_RE.finditer(text):
                candidate = match.group(1)
                if not candidate.endswith((".js", ".css")):
                    continue
                if candidate.startswith(("http:", "https:", "data:")):
                    continue
                full = candidate if "/" in candidate else directory + candidate
                if full not in files and full not in seen and "assets/" in full:
                    queue.append(full)
    return {"files": files, "fetched": seen}


def feature_signature(bundle: Dict[str, Any]) -> Dict[str, Any]:
    texts = "\n".join(f["text"] for f in bundle["files"].values())
    found: Dict[str, bool] = {}
    for label, needles in V5_FEATURES:
        found[label] = any(n in texts for n in needles)
    return {
        "features": found,
        "all_present": all(found.values()),
        "files_checked": len(bundle["files"]),
    }


def service_worker_scan(dist: Optional[Path], bundle: Dict[str, Any], root: Path) -> Dict[str, Any]:
    info: Dict[str, Any] = {"sw_files": [], "pwa_registration_in_served_js": False,
                            "manifest_links": [], "workbox": False}
    if dist and dist.is_dir():
        for pattern in ("sw.js", "service-worker.js", "workbox-*.js", "manifest.webmanifest", "manifest.json"):
            for path in list(dist.rglob(pattern))[:10]:
                info["sw_files"].append(str(path.relative_to(root)))
    texts = "\n".join(f["text"] for f in bundle["files"].values())
    info["pwa_registration_in_served_js"] = any(
        n in texts for n in ("serviceWorker", "navigator.serviceWorker.register", "workbox"))
    info["workbox"] = "workbox" in texts.lower()
    for html in [root / "frontend" / "index.html", (root / "frontend" / "public" / "index.html")]:
        try:
            if html.is_file():
                text = html.read_text(encoding="utf-8", errors="replace")
                info["manifest_links"] += re.findall(r"""rel\s*=\s*["'](?:manifest|serviceworker)["']""", text)
        except Exception:
            continue
    return info


def cache_headers(url: str, asset_refs: List[str], timeout: float) -> List[Dict[str, Any]]:
    rows = []
    targets = ["/"] + [r.split("?")[0].lstrip("./") for r in asset_refs]
    for target in targets[:8]:
        res = http_get(url.rstrip("/") + "/" + target.lstrip("/"), timeout=timeout)
        rows.append({
            "path": target if target == "/" else "/" + target.lstrip("/"),
            "status": res.get("status"),
            "cache_control": res["headers"].get("cache-control"),
            "etag": res["headers"].get("etag"),
            "content_type": res["headers"].get("content-type"),
        })
    return rows


# --------------------------------------------------------------------------- #
# 5. every physical frontend copy in the repository
# --------------------------------------------------------------------------- #
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "DATA", "LOGS",
             ".pytest_cache", "dist_electron"}


def frontend_copies(root: Path, limit: int = 40) -> List[Dict[str, Any]]:
    """Any place a dashboard shell could physically live."""
    hits: List[Dict[str, Any]] = []
    interesting_names = {"dist", "build", "static", "out", "public", "_internal", "frontend"}
    for path, dirs, files in os.walk(root, topdown=True):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        p = Path(path)
        depth = len(p.relative_to(root).parts)
        if depth > 5:
            dirs[:] = []
            continue
        if "index.html" in files:
            index = p / "index.html"
            try:
                stat = index.stat()
                text = index.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            hits.append({
                "dir": str(p.relative_to(root)),
                "index_html": str(index.relative_to(root)),
                "mtime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stat.st_mtime)),
                "size": stat.st_size,
                "sha256_prefix": (sha256_file(index) or "")[:16],
                "references_assets": bool(re.search(r"""src\s*=\s*["'][^"']*assets/""", text)),
                "is_built_shell": "index.html" in files and bool(re.search(r"""assets/index-""", text)),
                "is_source_shell": "/src/main" in text or "./src/" in text,
            })
            if len(hits) >= limit:
                break
        if depth >= 3 and not (interesting_names & set(dirs)) and "index.html" not in files:
            # keep walking but prune obviously irrelevant deep trees
            dirs[:] = [d for d in dirs if d in interesting_names or d.startswith("dist")]
    return hits


def pyinstaller_hints(root: Path) -> Dict[str, Any]:
    hints: Dict[str, Any] = {"spec_files": [], "exe_files": [], "meipass_references": False}
    for spec in list(root.glob("*.spec"))[:10]:
        hints["spec_files"].append(spec.name)
    for exe in list(root.glob("*.exe"))[:10]:
        hints["exe_files"].append(exe.name)
    for path in list(root.glob("backend/**/paths.py")) + list(root.glob("backend/app/*.py"))[:40]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if "_MEIPASS" in text:
            hints["meipass_references"] = True
            break
    return hints


# --------------------------------------------------------------------------- #
# 6. launchers
# --------------------------------------------------------------------------- #
def launcher_identity(root: Path) -> List[Dict[str, Any]]:
    code, tracked = run(["git", "ls-files"], cwd=root, timeout=60.0)
    tracked_set = set(tracked.splitlines()) if code == 0 else set()
    rows = []
    for rel in LAUNCHER_FILES:
        path = root / rel
        if not path.is_file():
            continue
        try:
            stat = path.stat()
            lines = [ln for ln in path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
        except Exception:
            continue
        rows.append({
            "file": rel,
            "tracked": rel in tracked_set,
            "mtime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stat.st_mtime)),
            "size": stat.st_size,
            "sha256_prefix": (sha256_file(path) or "")[:16],
            "first_lines": lines[:3],
            "last_lines": lines[-3:],
        })
    return rows


# --------------------------------------------------------------------------- #
# 7. the whole comparison
# --------------------------------------------------------------------------- #
def build_report(root: Path, url: str, *, remote: bool = True, timeout: float = 10.0,
                 remote_timeout: float = 30.0) -> Dict[str, Any]:
    report: Dict[str, Any] = {"root": str(root), "url": url, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    report["git"] = git_identity(root, remote=remote, timeout=remote_timeout)
    report["frontend"] = frontend_identity(root)
    report["backend_code"] = {"code_hash": code_hash_of(root)}
    try:                                     # the launcher's ./scripts markers, verbatim
        from app.runtime_identity import code_fingerprint
        fp = code_fingerprint(root)
        report["backend_code"].update({"file_count": fp.get("file_count"),
                                       "files": list((fp.get("files") or {}).keys())[:5]})
    except Exception:
        pass

    port = int(re.search(r":(\d+)$", url.rstrip("/")).group(1)) if re.search(r":(\d+)$", url.rstrip("/")) else 80
    report["port"] = port
    report["port_owner"] = port_owner(port)

    health = http_get(url.rstrip("/") + "/health", timeout=timeout)
    try:
        health_json = json.loads((health.get("body") or b"").decode("utf-8", "replace"))
    except Exception:
        health_json = None
    report["health"] = {
        "http_status": health.get("status"),
        "error": health.get("error"),
        "raw": health_json if health_json is not None else (health.get("body") or b"").decode("utf-8", "replace")[:2000],
    }

    build = http_get(url.rstrip("/") + "/system/build", timeout=timeout)
    try:
        build_json = json.loads((build.get("body") or b"").decode("utf-8", "replace"))
    except Exception:
        build_json = None
    report["system_build"] = {
        "http_status": build.get("status"),
        "error": build.get("error"),
        "raw": build_json if build_json is not None else (build.get("body") or b"").decode("utf-8", "replace")[:4000],
    }

    served = fetch_served_index(url, timeout)
    report["served_index"] = served

    entry = list(build_json.get("entry_assets") or []) if isinstance(build_json, dict) else []
    refs = [r for r in served["script_references"]]
    bundle = served_asset_bundle(url, refs, timeout)
    report["served_assets"] = {name: {k: v for k, v in info.items() if k != "text"}
                               for name, info in bundle["files"].items()}
    report["features"] = feature_signature(bundle)
    report["service_worker"] = service_worker_scan(Path(report["frontend"].get("dist_dir") or ""), bundle, root)
    report["cache_headers"] = cache_headers(url, refs, timeout)
    report["frontend_copies"] = frontend_copies(root)
    report["pyinstaller"] = pyinstaller_hints(root)
    report["launchers"] = launcher_identity(root)

    # ---------------- identity comparison ----------------
    local_src = report["frontend"].get("src_hash")
    local_index = report["frontend"].get("index_sha256")
    local_assets = list(report["frontend"].get("entry_assets") or [])
    served_index_sha = served.get("body_sha256")
    served_entry = [r.split("?")[0].lstrip("./") for r in refs]

    served_root = str((build_json or {}).get("backend_root") or "")
    served_src = (build_json or {}).get("src_hash")
    served_index_reported = (build_json or {}).get("index_sha256")
    served_assets_reported = list((build_json or {}).get("entry_assets") or [])
    proc = (build_json or {}).get("process") or {}
    serving_meta = (build_json or {}).get("served") or {}

    def _same_path(a: str, b: str) -> bool:
        if not a or not b:
            return False
        try:
            return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
        except Exception:
            return False

    checks = {
        "backend_reachable": report["system_build"]["http_status"] == 200 and isinstance(build_json, dict),
        "served_from_this_repo": _same_path(served_root, str(root)),
        "served_src_matches_local_src": bool(served_src and local_src and served_src == local_src),
        "served_index_matches_local_dist": bool(served_index_sha and local_index and served_index_sha == local_index),
        "served_index_matches_backend_report": bool(served_index_sha and served_index_reported and served_index_sha == served_index_reported),
        "served_entry_assets_match_local_dist": bool(served_entry and local_assets and served_entry == local_assets),
        "bundle_fresh": report["frontend"].get("dist_status") == "FRESH",
        # the process must be executing exactly the code in this checkout — the
        # same test the launcher's guard applies before it reuses an instance
        "process_code_current": bool(report["backend_code"].get("code_hash")
                                     and proc.get("code_hash")
                                     and report["backend_code"]["code_hash"] == proc.get("code_hash")
                                     and serving_meta.get("code_changed_since_start") is not True),
        "bundle_unchanged_since_start": serving_meta.get("changed_since_start") is False,
        "v5_feature_signature": bool(report["features"]["all_present"]),
        "no_service_worker": (not report["service_worker"]["sw_files"]
                              and not report["service_worker"]["pwa_registration_in_served_js"]),
    }
    served_hash_view = {
        "index_sha256": served_index_sha,
        "entry_assets": served_entry,
        "bytes": sum(f["bytes"] for f in bundle["files"].values()),
        "files": sorted(bundle["files"].keys()),
    }
    report["checks"] = checks
    report["served_hash_view"] = served_hash_view

    failing = [k for k, ok in checks.items() if not ok]
    report["verdict"] = {
        "running_dashboard_matches_github": not failing,
        "failing_checks": failing,
    }
    return report


# --------------------------------------------------------------------------- #
# 8. presentation
# --------------------------------------------------------------------------- #
def print_report(report: Dict[str, Any], root: Path) -> None:
    git = report["git"]
    fe = report["frontend"]
    sb = report["system_build"]
    si = report["served_index"]
    checks = report["checks"]

    print("################################################################################")
    print("#  EVOLUTIONARYTRADINGV5 — RUNTIME DASHBOARD IDENTITY DIAGNOSTIC (READ-ONLY)")
    print(f"#  repository : {report['root']}")
    print(f"#  dashboard  : {report['url']}")
    print(f"#  generated  : {report['generated_at']}")
    print("################################################################################")

    # ---- GIT IDENTITY -----------------------------------------------------
    section("GIT IDENTITY")
    print(f"LOCAL GIT HEAD:      {git.get('local_head')}")
    print(f"ORIGIN/MAIN:         {git.get('origin_main')}")
    print(f"REMOTE MAIN:         {git.get('remote_main') if git.get('remote_checked') else '(remote not checked)'}")
    print(f"HEAD == ORIGIN/MAIN: {git.get('local_head') == git.get('origin_main')}")
    print(f"Git branch:          {git.get('branch')}")
    print(f"Latest commit:       {git.get('last_commit')}")
    print(f"Latest commit date:  {git.get('last_commit_date')}")
    print(f"Latest commit msg:   {git.get('last_commit_subject')}")
    print("Working tree status (tracked changes only, untracked DATA/logs excluded):")
    print(f"  {git.get('tracked_changes') or '(clean)'}")
    print("Working tree status (full):")
    for line in str(git.get("status_short") or "(clean)").splitlines()[:25]:
        print(f"  {line}")
    print()
    print(verdict_line("GIT_IDENTITY_MATCH", bool(git.get("git_identity_match"))))

    # ---- FRONTEND SOURCE IDENTITY ----------------------------------------
    section("FRONTEND SOURCE IDENTITY")
    print(f"FRONTEND SOURCE:      {fe.get('src_dir')}")
    print(f"frontend/dist path:   {fe.get('dist_dir')}")
    print(f"SOURCE HASH:          {fe.get('src_hash')}  ({fe.get('src_file_count')} files)")
    print(f"DIST HASH:            {fe.get('index_sha256')}")
    print(f"DIST ENTRY ASSETS:    {fe.get('entry_assets')}")
    print(f"BUILD STAMP:          {fe.get('build_timestamp')}  (git {str(fe.get('build_git_head') or '')[:12]})")
    print(f"BUILD AGE:            {fe.get('build_age_seconds')} s")
    print(f"DIST STATUS:          {fe.get('dist_status')}")
    print(f"DIST MATCHES SOURCE:  {fe.get('dist_matches_src')}")
    for reason in fe.get("reasons") or []:
        print(f"  reason: {reason}")
    if fe.get("error"):
        print(f"  error: {fe['error']}")
    print()
    print(verdict_line("FRONTEND_BUILD_MATCH", bool(fe.get("frontend_build_match"))))

    # ---- RUNNING BACKEND PROCESS -----------------------------------------
    section("RUNNING BACKEND PROCESS  (port owner of the dashboard port)")
    owner = report["port_owner"]
    print(f"PORT {report['port']} OWNER:")
    print(f"  PID:               {owner.get('pid')}")
    print(f"  Process name:      {owner.get('name')}")
    print(f"  Executable path:   {owner.get('exe')}")
    print(f"  Command line:      {owner.get('cmdline')}")
    print(f"  Working directory: {owner.get('cwd')}")
    print(f"  Detected by:       {owner.get('source') or owner.get('note')}")
    proc = (sb.get("raw") or {}).get("process") if isinstance(sb.get("raw"), dict) else None
    if proc:
        print("  The answering backend states about itself (authoritative):")
        print(f"    pid:            {proc.get('pid')}")
        print(f"    python:         {proc.get('python')}")
        print(f"    cwd:            {proc.get('cwd')}")
        print(f"    started:        {proc.get('started_iso')}  (uptime {proc.get('uptime_s')} s)")
        print(f"    start token:    {proc.get('start_token')}")
        print(f"    code hash:      {proc.get('code_hash')}")
        print(f"    looks like app.main:app: "
              f"{'app.main:app' in str(proc.get('cmdline') or '') or 'app.main:app' in str(owner.get('cmdline') or 'present (see code hash / cwd)')}")
    else:
        print("  (the backend did not answer /system/build — see the HTTP identity section)")

    # ---- HTTP IDENTITY ----------------------------------------------------
    section("RUNNING HTTP IDENTITY")
    print(f"GET {report['url'].rstrip('/')}/health  ->  HTTP {report['health'].get('http_status')}")
    print(json.dumps(report["health"].get("raw"), indent=2)[:4000])
    print()
    print("RUNNING BACKEND BUILD IDENTITY")
    print(f"GET {report['url'].rstrip('/')}/system/build  ->  HTTP {sb.get('http_status')}")
    if isinstance(sb.get("raw"), dict):
        print(json.dumps(sb["raw"], indent=2, sort_keys=True))
    else:
        print(sb.get("raw") or "(no body)")
        if sb.get("error"):
            print(f"error: {sb['error']}")

    # ---- SERVED FRONTEND --------------------------------------------------
    section("RUNNING FRONTEND — ACTUAL HTTP RESPONSE")
    print(f"HTTP status:     {si.get('status')}")
    print(f"Content-Type:    {si.get('content_type')}")
    print(f"ETag:            {si.get('etag')}")
    print(f"Last-Modified:   {si.get('last_modified')}")
    print(f"Content-Length:  {si.get('content_length')}")
    print(f"Cache-Control:   {si.get('cache_control')}")
    print(f"index.html saved (temporary, outside the repository): {si.get('temp_path')}")
    print()
    print("ACTUAL SERVED INDEX:")
    print(f"  sha256: {si.get('body_sha256')}")
    print("ACTUAL SCRIPT REFERENCES:")
    for ref in si.get("script_references") or []:
        info = report["served_assets"].get(ref.split("?")[0].lstrip("./"))
        extra = f"  [HTTP {info['http_status']}, {info['bytes']} bytes, sha256 {(info['sha256'] or '')[:16]}]" if info else ""
        print(f"  {ref}{extra}")

    # ---- SERVED FRONTEND FINGERPRINT -------------------------------------
    section("SERVED FRONTEND FINGERPRINT")
    print(f"LOCAL SOURCE COMMIT:            {git.get('local_head')}")
    print(f"LOCAL SOURCE HASH:              {fe.get('src_hash')}")
    print(f"LOCAL DIST HASH:                {fe.get('index_sha256')}")
    print(f"LOCAL DIST ENTRY ASSETS:        {fe.get('entry_assets')}")
    rb = sb.get("raw") if isinstance(sb.get("raw"), dict) else {}
    print(f"RUNNING BACKEND COMMIT:         {rb.get('git_commit')}")
    print(f"RUNNING BACKEND SOURCE HASH:    {rb.get('src_hash')}")
    print(f"RUNNING BACKEND REPOSITORY:     {rb.get('backend_root')}")
    print(f"RUNNING FRONTEND HASH:          {rb.get('index_sha256')}")
    print(f"RUNNING FRONTEND ENTRY ASSETS:  {rb.get('entry_assets')}")
    print(f"RUNNING FRONTEND DIST STATUS:   {rb.get('dist_status')}  (matches src: {rb.get('dist_matches_src')})")
    print(f"LOCAL BACKEND CODE HASH:        {report['backend_code'].get('code_hash')}")
    print(f"RUNNING BACKEND CODE HASH:      {(rb.get('process') or {}).get('code_hash')}")
    print(f"HTTP INDEX ASSET (served):      {si.get('body_sha256')}")
    print(f"HTTP ENTRY ASSETS (served):     {[r.split('?')[0].lstrip('./') for r in si.get('script_references') or []]}")
    print()
    print("COMPARISONS:")
    for key, value in checks.items():
        print(f"  {key:38}: {value}")
    print()
    print(verdict_line("RUNNING_DASHBOARD_MATCHES_GITHUB", bool(report["verdict"]["running_dashboard_matches_github"])))
    if report["verdict"]["failing_checks"]:
        print("Failing checks: " + ", ".join(report["verdict"]["failing_checks"]))

    # ---- V5 FEATURE SIGNATURE --------------------------------------------
    section("FRONTEND UI IDENTITY  (measured on the HTTP-served bytes, not on frontend/src)")
    for label, present in report["features"]["features"].items():
        print(f"  {label:26}: {'PRESENT' if present else 'MISSING'}")
    print(f"  files fetched over HTTP  : {report['features']['files_checked']}")
    print(f"  served files             : {sorted(report['served_assets'].keys())}")
    print()
    print(verdict_line("SERVED_V5_FEATURE_SIGNATURE", bool(report["features"]["all_present"])))

    # ---- BROWSER / CACHE / SW --------------------------------------------
    section("BROWSER / CACHE / SERVICE WORKER")
    sw = report["service_worker"]
    print(f"Service worker files in dist:      {sw['sw_files'] or 'NONE'}")
    print(f"SW/PWA registration in served JS:  {sw['pwa_registration_in_served_js']}")
    print(f"Workbox in served JS:              {sw['workbox']}")
    print(f"manifest/serviceworker links:      {sw['manifest_links'] or 'NONE'}")
    print("HTTP cache headers:")
    for row in report["cache_headers"]:
        print(f"  {row['path']:42} HTTP {row['status']}  cache-control: {row['cache_control']}")
    shell_no_store = any(r["path"] == "/" and str(r["cache_control"] or "").startswith("no-store")
                         for r in report["cache_headers"])
    hashed_immutable = all(str(r["cache_control"] or "").startswith("public, max-age")
                           for r in report["cache_headers"] if r["path"] != "/" and str(r["path"]).endswith(".js"))
    print(f"  shell is no-store: {shell_no_store}   hashed JS immutable: {hashed_immutable}")
    print()
    print(verdict_line("BROWSER_CACHE_RISK_ABSENT", bool(checks["no_service_worker"] and shell_no_store)))

    # ---- ALL FRONTEND COPIES ---------------------------------------------
    section("ALL FRONTEND COPIES DISCOVERED UNDER THE REPOSITORY")
    copies = report["frontend_copies"]
    if not copies:
        print("  (no index.html found anywhere under the repository)")
    for row in copies:
        kind = ("built shell" if row.get("is_built_shell") else
                "source shell" if row.get("is_source_shell") else "other")
        print(f"  {row['dir']}")
        print(f"      index.html: {row['index_html']}")
        print(f"      exists: True   size: {row['size']}   mtime: {row['mtime']}   sha256: {row['sha256_prefix']}")
        print(f"      references assets/: {row['references_assets']}   kind: {kind}")
    print()
    pyi = report["pyinstaller"]
    print(f"PyInstaller hints: spec files {pyi['spec_files'] or 'NONE'}, packaged exe {pyi['exe_files'] or 'NONE'}, "
          f"_MEIPASS referenced in Python source: {pyi['meipass_references']}")

    # ---- LAUNCHERS --------------------------------------------------------
    section("NORMAL LAUNCHER IDENTITY")
    for row in report["launchers"]:
        print(f"  {row['file']:28} tracked: {str(row['tracked']):5}  size: {row['size']:7}  mtime: {row['mtime']}  sha256: {row['sha256_prefix']}")
        for line in row["first_lines"]:
            print(f"      | {line[:110]}")
        if len(row["last_lines"]) and row["last_lines"] != row["first_lines"]:
            print("      | ...")
            for line in row["last_lines"]:
                print(f"      | {line[:110]}")
    if not report["launchers"]:
        print("  (no launcher files found at the repository root)")

    # ---- FINAL SUMMARY ----------------------------------------------------
    ok = bool(report["verdict"]["running_dashboard_matches_github"])
    print()
    print("=" * 64)
    print("EVOLUTIONARYTRADINGV5 RUNTIME DIAGNOSTIC")
    print("=" * 64)
    print("")
    print(f"GITHUB REMOTE:              {git.get('remote_main') or git.get('origin_main')}")
    print("")
    print(f"LOCAL HEAD:                 {git.get('local_head')}")
    print("")
    print(f"GIT MATCH:                  {'PASS' if git.get('git_identity_match') else 'FAIL'}")
    print("")
    print(f"RUNNING BACKEND PID:        {report['port_owner'].get('pid') or ((rb.get('process') or {}).get('pid'))}")
    print("")
    print(f"RUNNING BACKEND REPOSITORY: {rb.get('backend_root')}")
    print("")
    print(f"RUNNING BACKEND COMMIT:     {rb.get('git_commit')}")
    print("")
    print(f"LOCAL SOURCE HASH:          {fe.get('src_hash')}")
    print("")
    print(f"LOCAL DIST HASH:            {fe.get('index_sha256')}")
    print("")
    print(f"RUNNING FRONTEND HASH:      {rb.get('index_sha256')}")
    print("")
    print(f"HTTP SERVED BUILD:          {si.get('body_sha256')}")
    print("")
    print(f"DIST MATCHES SOURCE:        {'PASS' if fe.get('dist_matches_src') else 'FAIL'}")
    print("")
    print(f"V5 UI SIGNATURE:            {'PASS' if report['features']['all_present'] else 'FAIL'}")
    print("")
    for label in ("Live Testing", "Deep Backtest", "MT5 Demo Trading", "Schedule", "PowerButton"):
        present = report["features"]["features"].get(
            label, report["features"]["features"].get("LiveTestResults" if label == "Live Testing" else label, False))
        print(f"{label.upper():<28}{'PRESENT' if present else 'MISSING'}")
    print("")
    print(f"SERVICE WORKER:             {'NONE' if not sw['sw_files'] and not sw['pwa_registration_in_served_js'] else 'PRESENT'}")
    print("")
    print("RUNNING_DASHBOARD_MATCHES_GITHUB:")
    print(f"  {'PASS' if ok else 'FAIL'}")
    if not ok:
        print("")
        print("  failing checks: " + ", ".join(report["verdict"]["failing_checks"]))
    print("")
    print("=" * 64)
    print("END DIAGNOSTIC")
    print("=" * 64)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only runtime dashboard identity diagnostic")
    parser.add_argument("--root", default=str(REPO_DEFAULT), help="repository root (default: this checkout)")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"dashboard URL (default: {DEFAULT_URL})")
    parser.add_argument("--no-remote", action="store_true", help="skip `git ls-remote` (offline evidence)")
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout in seconds")
    parser.add_argument("--remote-timeout", type=float, default=30.0, help="git ls-remote timeout in seconds")
    parser.add_argument("--json", action="store_true", help="print the machine-readable report only")
    parser.add_argument("--json-out", default=None, help="also write the JSON report to this path (outside the repo)")
    parser.add_argument("--strict", action="store_true", help="exit 3 when the final verdict is FAIL")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    if not (root / "frontend").is_dir():
        print(f"[DIAGNOSTIC] WARNING: {root} does not look like the repository root (no frontend/).")

    report = build_report(root, args.url, remote=not args.no_remote,
                          timeout=args.timeout, remote_timeout=args.remote_timeout)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print_report(report, root)

    if args.json_out:
        try:
            Path(args.json_out).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
            if not args.json:
                print(f"\n[DIAGNOSTIC] JSON report written to {args.json_out}")
        except Exception as exc:
            print(f"\n[DIAGNOSTIC] could not write {args.json_out}: {exc}")

    # leave no temporary bytes behind (the served index.html was inspection only)
    temp = (report.get("served_index") or {}).get("temp_path")
    if temp:
        try:
            shutil.rmtree(Path(temp).parent, ignore_errors=True)
        except Exception:
            pass

    ok = bool(report["verdict"]["running_dashboard_matches_github"])
    if args.strict and not ok:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
