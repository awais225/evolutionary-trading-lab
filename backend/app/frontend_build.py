"""
Frontend build identity — the single source of truth that ties ``frontend/src``
to the bundle the dashboard actually serves (``frontend/dist``).

Why this module exists
----------------------
``frontend/dist`` is a *generated* artifact and is intentionally not committed
(see ``.gitignore``). The application serves it from ``/`` (see ``app.main``).
That combination meant a ``git pull`` could update ``frontend/src`` while the
backend kept serving an older ``frontend/dist`` — the dashboard then looked
outdated even though the source, the commit and the push were correct.

To make that impossible to miss, every build writes a stamp next to the bundle:

    frontend/dist/build-info.json

containing the SHA-256 of the *source inputs* that produced it. The launcher
(``start.bat``), the backend (``/system/build``) and the doctor
(``backend/app/doctor.py``) all use this module, so there is exactly one
definition of "the bundle matches the source" and no place for the two to drift.

Status semantics (``check_dist``):

    FRESH     the bundle was built from exactly the current source inputs
    STALE     the bundle was built from different (older) source inputs
    UNSTAMPED the bundle exists but nobody recorded what produced it
    CORRUPT   dist/index.html changed after the build stamp was written
    BROKEN    a bundle asset referenced by index.html is missing on disk
    MISSING   frontend/dist/index.html does not exist

Anything other than ``FRESH`` means "build before serving". ``UNSTAMPED`` is
deliberately not treated as fresh: an unverifiable bundle must never be
presented as the current interface.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCHEMA = 1
STAMP_NAME = "build-info.json"

#: Files that determine the production bundle. Everything under ``frontend/src``
#: plus the HTML shell and the build configuration. ``frontend/tests`` is not
#: included: it is never bundled.
SRC_DIRNAME = "src"
EXTRA_INPUTS = (
    "index.html",
    "package.json",
    "package-lock.json",
    "vite.config.js",
    "vite.config.mjs",
    "vite.config.ts",
    ".env.production",
    "jsconfig.json",
    "tsconfig.json",
)

_ASSET_RE = re.compile(r"""["'](/?assets/[^"']+)["']""")


def frontend_dir(root: Path) -> Path:
    return Path(root) / "frontend"


def dist_dir(root: Path) -> Path:
    return frontend_dir(root) / "dist"


def _iter_inputs(root: Path) -> List[Path]:
    """Every file whose content influences the production bundle, sorted."""
    fe = frontend_dir(root)
    files: List[Path] = []
    src = fe / SRC_DIRNAME
    if src.is_dir():
        for p in src.rglob("*"):
            if p.is_file() and "__pycache__" not in p.parts:
                files.append(p)
    for name in EXTRA_INPUTS:
        p = fe / name
        if p.is_file():
            files.append(p)
    return sorted(files, key=lambda p: p.as_posix())


def source_fingerprint(root: Path) -> Tuple[str, int]:
    """SHA-256 over the relative path + content of every build input.

    Returns ``(hex_digest, file_count)``. The digest changes whenever any file
    under ``frontend/src`` (or the HTML shell / build config) changes — that is
    exactly the condition under which the served bundle is out of date.
    """
    root = Path(root)
    fe = frontend_dir(root)
    h = hashlib.sha256()
    n = 0
    for p in _iter_inputs(root):
        try:
            rel = p.relative_to(fe).as_posix()
        except ValueError:                                            # pragma: no cover - defensive
            rel = p.as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        try:
            h.update(p.read_bytes())
        except OSError:                                               # pragma: no cover - defensive
            h.update(b"<unreadable>")
        h.update(b"\0")
        n += 1
    return h.hexdigest(), n


def _sha256_file(path: Path) -> Optional[str]:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def entry_assets(dist: Path) -> List[str]:
    """Bundle files referenced by ``index.html`` (Vite emits hashed names)."""
    index = Path(dist) / "index.html"
    try:
        html = index.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out: List[str] = []
    for raw in _ASSET_RE.findall(html):
        rel = raw.lstrip("/")
        if rel not in out:
            out.append(rel)
    return out


def read_stamp(root: Path) -> Optional[Dict[str, Any]]:
    p = dist_dir(root) / STAMP_NAME
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def git_head(root: Path) -> Optional[str]:
    try:
        res = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        if res.returncode == 0:
            return res.stdout.strip() or None
    except Exception:                                                 # pragma: no cover - defensive
        pass
    return None


def write_stamp(root: Path, built_by: str = "frontend_build_guard") -> Dict[str, Any]:
    """Record what produced the current ``frontend/dist``. Called after a build."""
    root = Path(root)
    dist = dist_dir(root)
    index = dist / "index.html"
    src_hash, src_count = source_fingerprint(root)
    stamp: Dict[str, Any] = {
        "schema": SCHEMA,
        "src_hash": src_hash,
        "src_file_count": src_count,
        "index_sha256": _sha256_file(index),
        "entry_assets": entry_assets(dist),
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "built_by": built_by,
        "git_head": git_head(root),
    }
    dist.mkdir(parents=True, exist_ok=True)
    (dist / STAMP_NAME).write_text(json.dumps(stamp, indent=2) + "\n", encoding="utf-8")
    return stamp


def check_dist(root: Path) -> Dict[str, Any]:
    """Is ``frontend/dist`` the bundle produced by the current ``frontend/src``?"""
    root = Path(root)
    dist = dist_dir(root)
    index = dist / "index.html"
    src_hash, src_count = source_fingerprint(root)
    stamp = read_stamp(root)
    info: Dict[str, Any] = {
        "status": "MISSING",
        "ok": False,
        "needs_build": True,
        "reasons": [],
        "dist_dir": str(dist),
        "index_html": str(index),
        "src_hash": src_hash,
        "src_file_count": src_count,
        "stamp": stamp,
        "entry_assets": [],
        "index_sha256": None,
    }

    if not index.is_file():
        info["reasons"].append("frontend/dist/index.html does not exist")
        return info

    assets = entry_assets(dist)
    info["entry_assets"] = assets
    info["index_sha256"] = _sha256_file(index)
    missing_assets = [a for a in assets if not (dist / a).is_file()]

    if stamp is None:
        info["status"] = "UNSTAMPED"
        info["reasons"].append(
            "frontend/dist/build-info.json is missing, so the bundle cannot be proven "
            "to match frontend/src (rebuild to record it)")
        # A broken bundle is reported as BROKEN even when unstamped: it is not servable.
        if missing_assets:
            info["status"] = "BROKEN"
            info["reasons"] = [f"bundle asset missing from frontend/dist: {a}" for a in missing_assets]
        return info

    if str(stamp.get("src_hash") or "") != src_hash:
        info["status"] = "STALE"
        info["reasons"].append(
            "frontend/src (or the HTML shell / build config) changed after this bundle was built")
        return info

    if stamp.get("index_sha256") and stamp.get("index_sha256") != info["index_sha256"]:
        info["status"] = "CORRUPT"
        info["reasons"].append("frontend/dist/index.html changed after the build stamp was written")
        return info

    if missing_assets:
        info["status"] = "BROKEN"
        info["reasons"] = [f"bundle asset missing from frontend/dist: {a}" for a in missing_assets]
        return info

    info["status"] = "FRESH"
    info["ok"] = True
    info["needs_build"] = False
    return info


def served_payload(root: Path, serving: str = "static") -> Dict[str, Any]:
    """Body of ``GET /system/build``: what this backend is *actually* serving.

    The launcher reads this to decide whether an instance already listening on
    the dashboard port serves *this* repository at the *current* build, instead
    of assuming that anything answering ``/health`` is up to date.
    """
    root = Path(root)
    info = check_dist(root)
    return {
        "app": "EVOLUTIONARY TRADING RESEARCH LAB",
        "backend_root": str(root),
        "backend_root_norm": os.path.normcase(str(Path(root).resolve())),
        "frontend_dist": str(dist_dir(root)),
        "frontend_src": str(frontend_dir(root) / SRC_DIRNAME),
        "serving": serving,
        "dist_status": info["status"],
        "dist_matches_src": info["ok"],
        "dist_exists": Path(info["index_html"]).is_file(),
        "src_hash": info["src_hash"],
        "src_file_count": info["src_file_count"],
        "index_sha256": info["index_sha256"],
        "entry_assets": info["entry_assets"],
        "stamp": info["stamp"],
        "reasons": info["reasons"],
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
