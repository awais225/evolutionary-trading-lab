"""
Deterministic experiment fingerprinting (spec §9).

Every backtest, validation, and specialization run is stamped with a content-
addressed fingerprint. The fingerprint ties together:
  - genome hash
  - symbol & timeframe
  - dataset version & content fingerprint
  - feature engine version
  - backtester version
  - fitness version
  - validation version
  - risk & cost models (commission, swap, spread, slippage)
  - execution delay & parameters
  - stage & stage parameters (perturbation, seed_salt, stress multipliers)

If the fingerprint is identical:
  REUSE THE RESULT immediately.

If the fingerprint differs:
  Existing result is marked STALE (spec §9) and re-executed.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional

from ..config import config_digest, get_config
from ..versions import (
    BACKTESTER_VERSION,
    COST_MODEL_VERSION,
    FEATURE_ENGINE_VERSION,
    FITNESS_VERSION,
    SLIPPAGE_MODEL_VERSION,
    VALIDATION_VERSION,
    manifest_digest,
)


def compute_experiment_fingerprint(
    genome_hash: str,
    symbol: str,
    timeframe: str,
    stage: str,
    dataset_version: int = 1,
    dataset_fingerprint: str = "",
    params: Optional[Dict[str, Any]] = None,
) -> str:
    """Deterministic 32-character SHA256 hex digest of all experiment inputs."""
    cfg = get_config()

    payload = {
        "genome_hash": genome_hash,
        "symbol": symbol,
        "timeframe": timeframe,
        "stage": stage,
        "dataset_version": dataset_version,
        "dataset_fingerprint": dataset_fingerprint or "default",
        "manifest": manifest_digest(),
        "config_digest": config_digest(),
        "costs": {
            "commission": cfg.backtest.commission_per_lot,
            "swap": cfg.backtest.swap_per_lot_per_day,
            "slippage_model": cfg.backtest.slippage_model,
            "slippage_mean": cfg.backtest.slippage_mean_points,
            "point_value": cfg.backtest.point_value,
            "execution_delay_ms": cfg.backtest.execution_delay_ms,
        },
        "params": params or {},
    }

    canon = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:32]
