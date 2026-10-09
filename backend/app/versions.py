"""
V2 version manifest (spec §41).

Every persistent artifact (datasets, features, experiments, backups) is stamped
with these versions so results produced by different engine generations can
never be silently mixed. Experiment fingerprints (backtest/fingerprint.py)
include the digest of this manifest.
"""
from __future__ import annotations

import hashlib
import inspect
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict

APP_VERSION = "3.6"
APP_NAME = "EVOLUTIONARY TRADING RESEARCH LAB V3.6"
FULL_VERSION_STRING = "EVOLUTIONARY TRADING RESEARCH LAB V3.6"

#: Product release the operator is running. This is a *label*, kept
#: deliberately outside ``manifest()`` so that the engine-generation digests in
#: stored experiments and backtest fingerprints cannot change because a release
#: was named. It is what ``/system/build`` and the dashboard's Build chip show,
#: next to the commit and the frontend fingerprint, and it must equal the newest
#: release name published in ``BUILD_FINGERPRINTS.json`` (V6) — otherwise the
#: dashboard would name a different build than the identity index does.
PRODUCT_RELEASE = "V6"

# Schema versions — bump when the corresponding structure/semantics change.
DB_SCHEMA_VERSION = 3          # SQLite migration target (PRAGMA user_version)
DATA_SCHEMA_VERSION = 3        # V3.6 authoritative persistent DATA root + node ledger
DATA_SCHEMA_VERSION_STRING = "3.6"
GENOME_SCHEMA_VERSION = 1      # genome DSL structure (unchanged from V1)
FEATURE_ENGINE_VERSION = 2     # incremental, algo-hash-invalidated cache
BACKTESTER_VERSION = 2         # exit_fill semantics + deterministic seeds
FITNESS_VERSION = 1            # weights + death rules evaluation
VALIDATION_VERSION = 2         # WF majority + robustness floor 0.60
RISK_VERSION = 2               # persistent kill switch + DST sessions
COST_MODEL_VERSION = 1         # commission/swap/spread model
SLIPPAGE_MODEL_VERSION = 1     # normal|uniform|empirical models
SESSIONS_VERSION = 2           # DST-aware session boundaries (zoneinfo)
CONFIG_VERSION = 2             # config layout incl. resources/appearance/research

_GIT_HASH: str | None = None


def git_hash() -> str:
    """Best-effort git commit hash (empty string when not a git checkout)."""
    global _GIT_HASH
    if _GIT_HASH is None:
        try:
            root = Path(__file__).resolve().parents[2]
            out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                 cwd=str(root), capture_output=True, timeout=5)
            _GIT_HASH = out.stdout.decode().strip() if out.returncode == 0 else ""
        except Exception:
            _GIT_HASH = ""
    return _GIT_HASH


def manifest() -> Dict[str, Any]:
    return {
        "app_version": APP_VERSION,
        "db_schema": DB_SCHEMA_VERSION,
        "data_schema": DATA_SCHEMA_VERSION,
        "genome_schema": GENOME_SCHEMA_VERSION,
        "feature_engine": FEATURE_ENGINE_VERSION,
        "backtester": BACKTESTER_VERSION,
        "fitness": FITNESS_VERSION,
        "validation": VALIDATION_VERSION,
        "risk": RISK_VERSION,
        "cost_model": COST_MODEL_VERSION,
        "slippage_model": SLIPPAGE_MODEL_VERSION,
        "sessions": SESSIONS_VERSION,
        "config": CONFIG_VERSION,
        "git": git_hash(),
    }


_MANIFEST_DIGEST: str | None = None


def manifest_digest() -> str:
    """Stable hash of the whole manifest (used inside experiment fingerprints)."""
    global _MANIFEST_DIGEST
    if _MANIFEST_DIGEST is None:
        m = manifest()
        blob = "|".join(f"{k}={m[k]}" for k in sorted(m))
        _MANIFEST_DIGEST = hashlib.sha256(blob.encode()).hexdigest()[:16]
    return _MANIFEST_DIGEST


def algo_hash(fn: Callable) -> str:
    """Content hash of a function's source — feature-cache invalidation key.

    If the implementation of an indicator changes, its hash changes and only
    the affected cached columns are invalidated (spec §8).
    """
    try:
        src = inspect.getsource(fn)
    except (OSError, TypeError):
        src = repr(fn)
    return hashlib.sha256(src.encode()).hexdigest()[:16]
