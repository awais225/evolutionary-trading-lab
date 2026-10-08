"""V5.2.3 §22 — a published fingerprint index for checkouts that have no .git.

The operator runs the dashboard from a GitHub **"Download ZIP"** export
(``E:\\evolutionary-trading-lab-main``).  Such a folder has no ``.git``, so
``git rev-parse HEAD`` cannot answer "which commit is this?" — the runtime
identity diagnostic printed ``LOCAL GIT HEAD: None`` and ``GIT_IDENTITY_MATCH:
FAIL`` even though the running build was perfectly identified by its hashes.

This tool removes that blind spot.  Every release records the fingerprints that
identify it *without* Git:

    src_hash        SHA-256 over ``frontend/src`` (the source of truth)
    index_sha256    SHA-256 of the built ``frontend/dist/index.html``
    entry_assets    the hashed bundle files the built shell references
    code_hash       SHA-256 over the backend ``app`` package files

``BUILD_FINGERPRINTS.json`` (repository root) is the published table of those
values per commit.  The diagnostic matches the running tree against it, so a ZIP
user learns *exactly* which release they run — and that a newer one exists.

Usage::

    python backend/tools/build_fingerprints.py --record --release V5.2.3
    python backend/tools/build_fingerprints.py --match      # what is this tree?

``--record`` stamps the CURRENT tree; run it after the frontend build and before
committing, so the entry lands in the same commit it describes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
INDEX_NAME = "BUILD_FINGERPRINTS.json"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _sha256_file(p: Path) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _git(root: Path, *args: str) -> Optional[str]:
    try:
        out = subprocess.run(["git", *args], cwd=str(root), capture_output=True,
                             text=True, timeout=30)
    except Exception:
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def tree_fingerprints(root: Path = REPO_ROOT) -> Dict[str, Any]:
    """The four fingerprints that identify THIS checkout, Git or no Git."""
    sys.path.insert(0, str(root / "backend"))
    from app.frontend_build import check_dist          # noqa: E402
    from app.runtime_identity import code_fingerprint  # noqa: E402

    dist = check_dist(root)
    code = code_fingerprint(root)
    return {
        "src_hash": dist.get("src_hash"),
        "src_file_count": dist.get("src_file_count"),
        "index_sha256": dist.get("index_sha256"),
        "entry_assets": dist.get("entry_assets"),
        "dist_status": dist.get("status"),
        "code_hash": code.get("code_hash"),
        "code_files": code.get("code_files"),
    }


def load_index(root: Path = REPO_ROOT) -> Dict[str, Any]:
    p = root / INDEX_NAME
    if not p.exists():
        return {"releases": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"releases": []}
    if isinstance(data, list):
        return {"releases": data}
    data.setdefault("releases", [])
    return data


def save_index(data: Dict[str, Any], root: Path = REPO_ROOT) -> Path:
    p = root / INDEX_NAME
    data["schema"] = 1
    data["note"] = ("Published fingerprints of every release, for checkouts without .git "
                    "(GitHub ZIP exports). Recorded by backend/tools/build_fingerprints.py.")
    data["releases"] = sorted(data["releases"], key=lambda r: str(r.get("recorded_at") or ""))
    p.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return p


def match(root: Path = REPO_ROOT, fp: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Which published release is this tree?  Never guesses."""
    fp = fp or tree_fingerprints(root)
    index = load_index(root)
    releases: List[Dict[str, Any]] = list(index.get("releases") or [])

    def _same(a: Any, b: Any) -> bool:
        """Exact match, or a recorded prefix (older entries kept 16 hex chars)."""
        a, b = str(a or ""), str(b or "")
        if not a or not b:
            return False
        return a == b or a.startswith(b) or b.startswith(a)

    hit = None
    for r in releases:
        if _same(r.get("src_hash"), fp.get("src_hash")) \
                and (not r.get("code_hash") or _same(r.get("code_hash"), fp.get("code_hash"))):
            hit = r
            break
    latest = releases[-1] if releases else None
    return {
        "found": bool(hit),
        "release": (hit or {}).get("release"),
        "commit": (hit or {}).get("commit"),
        "commit_short": ((hit or {}).get("commit") or "")[:7] or None,
        "recorded_at": (hit or {}).get("recorded_at"),
        "src_hash": fp.get("src_hash"),
        "index_sha256": fp.get("index_sha256"),
        "entry_assets": fp.get("entry_assets"),
        "code_hash": fp.get("code_hash"),
        "dist_status": fp.get("dist_status"),
        "latest_release": (latest or {}).get("release"),
        "latest_commit_short": ((latest or {}).get("commit") or "")[:7] or None,
        "is_latest": bool(hit and latest and hit.get("src_hash") == latest.get("src_hash")),
        "releases_published": len(releases),
        "note": ("identified by fingerprint (src_hash + code_hash); a .git checkout would "
                 "identify itself by commit as well"),
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="published build fingerprints (no .git needed)")
    ap.add_argument("--root", default=str(REPO_ROOT))
    ap.add_argument("--record", action="store_true", help="record THIS tree into the index")
    ap.add_argument("--match", action="store_true", help="identify this tree against the index")
    ap.add_argument("--release", help="release label to record (e.g. V5.2.3)")
    ap.add_argument("--commit", help="commit to record (default: git HEAD, else null)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()

    if args.match or not args.record:
        out = match(root)
        if args.json:
            print(json.dumps(out, indent=2, sort_keys=True))
        else:
            if out["found"]:
                print(f"FINGERPRINT_MATCH: {out['release']} (commit {out['commit_short']}, "
                      f"recorded {out['recorded_at']})")
                if not out["is_latest"]:
                    print(f"NOTE: a newer release is published: {out['latest_release']} "
                          f"({out['latest_commit_short']})")
            else:
                print(f"FINGERPRINT_MATCH: none — src {str(out['src_hash'])[:16]} / code "
                      f"{str(out['code_hash'])[:16]} matches no published release "
                      f"({out['releases_published']} published)")
            print(f"SRC_HASH={out['src_hash']}")
            print(f"INDEX_SHA256={out['index_sha256']}")
            print(f"ENTRY_ASSETS={out['entry_assets']}")
            print(f"CODE_HASH={out['code_hash']}")
        return 0 if out["found"] else 1

    fp = tree_fingerprints(root)
    commit = args.commit or _git(root, "rev-parse", "HEAD")
    entry = {
        "release": args.release or "UNLABELLED",
        "commit": commit,
        "src_hash": fp["src_hash"],
        "index_sha256": fp["index_sha256"],
        "entry_assets": fp["entry_assets"],
        "code_hash": fp["code_hash"],
        "src_file_count": fp.get("src_file_count"),
        "code_files": fp.get("code_files"),
        "recorded_at": _utc_now(),
    }
    index = load_index(root)
    # One entry per release: re-recording a release REPLACES its previous entry
    # (an intermediate, unpublished build must never keep claiming a release name),
    # and a byte-identical tree recorded under another name is not duplicated either.
    releases = [r for r in index["releases"]
                if r.get("release") != entry["release"] and r.get("src_hash") != entry["src_hash"]]
    releases.append(entry)
    index["releases"] = releases
    path = save_index(index, root)
    print(f"[OK] recorded {entry['release']} ({str(commit)[:7]}) in {path.name}")
    print(f"SRC_HASH={entry['src_hash']}")
    print(f"CODE_HASH={entry['code_hash']}")
    return 0


def _utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


if __name__ == "__main__":
    sys.exit(main())
