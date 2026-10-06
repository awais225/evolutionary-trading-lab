"""
Research Directory Exporter & Complete Trade History (spec §10, §11).

SQLite remains the transactional database.
RESEARCH/ contains human-readable exports and complete time-series Parquets:
  RESEARCH/
    strategies/strategy_{id:06d}.json
    generations/generation_{id:06d}.json
    experiments/experiment_{fingerprint}.json
    backtests/strategy_{id:06d}/
      metrics.json
      trades.parquet       (COMPLETE trade history, spec §11)
      equity.parquet       (COMPLETE equity curve, spec §11)
    validations/strategy_{id:06d}.json
    paper_trading/strategy_{id:06d}/
      trades.parquet
      equity.parquet
    hypotheses/hypothesis_{id:06d}.json
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from .. import paths as P
from ..config import get_config

log = logging.getLogger("research.export")


def _safe_json_dump(obj: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def export_strategy(strategy_id: int, data: Dict[str, Any]) -> Path:
    """Export strategy record to RESEARCH/strategies/strategy_000001.json."""
    p = P.RESEARCH_DIR / "strategies" / f"strategy_{strategy_id:06d}.json"
    _safe_json_dump(data, p)
    return p


def export_generation(gen_id: int, data: Dict[str, Any]) -> Path:
    """Export generation summary to RESEARCH/generations/generation_000001.json."""
    p = P.RESEARCH_DIR / "generations" / f"generation_{gen_id:06d}.json"
    _safe_json_dump(data, p)
    return p


def export_experiment(fingerprint: str, data: Dict[str, Any]) -> Path:
    """Export experiment record to RESEARCH/experiments/experiment_HASH.json."""
    p = P.RESEARCH_DIR / "experiments" / f"experiment_{fingerprint}.json"
    _safe_json_dump(data, p)
    return p


def export_backtest(
    strategy_id: int,
    metrics: Dict[str, Any],
    all_trades: Optional[List[Dict[str, Any]]] = None,
    equity_curve: Optional[List[List[float]]] = None,
) -> Path:
    """Export complete backtest metrics and full trade history + equity curve in Parquet (spec §11)."""
    base = P.RESEARCH_DIR / "backtests" / f"strategy_{strategy_id:06d}"
    base.mkdir(parents=True, exist_ok=True)

    # 1. Metrics JSON
    _safe_json_dump(metrics, base / "metrics.json")

    # 2. Complete Trades Parquet (never truncated to only a few hundred trades)
    if all_trades is not None and len(all_trades) > 0:
        tdf = pd.DataFrame(all_trades)
        tmp_t = base / "trades.parquet.tmp"
        tdf.to_parquet(tmp_t, index=False)
        tmp_t.replace(base / "trades.parquet")

    # 3. Complete Equity Curve Parquet
    if equity_curve is not None and len(equity_curve) > 0:
        edf = pd.DataFrame(equity_curve, columns=["ts", "equity"])
        tmp_e = base / "equity.parquet.tmp"
        edf.to_parquet(tmp_e, index=False)
        tmp_e.replace(base / "equity.parquet")

    return base


def export_validation(strategy_id: int, data: Dict[str, Any]) -> Path:
    """Export validation report to RESEARCH/validations/strategy_000001.json."""
    p = P.RESEARCH_DIR / "validations" / f"strategy_{strategy_id:06d}.json"
    _safe_json_dump(data, p)
    return p


def export_paper_trading(
    strategy_id: int,
    all_trades: Optional[List[Dict[str, Any]]] = None,
    equity_curve: Optional[List[List[float]]] = None,
) -> Path:
    """Export paper trading complete history to RESEARCH/paper_trading/strategy_000001/."""
    base = P.RESEARCH_DIR / "paper_trading" / f"strategy_{strategy_id:06d}"
    base.mkdir(parents=True, exist_ok=True)

    if all_trades is not None and len(all_trades) > 0:
        tdf = pd.DataFrame(all_trades)
        tmp_t = base / "trades.parquet.tmp"
        tdf.to_parquet(tmp_t, index=False)
        tmp_t.replace(base / "trades.parquet")

    if equity_curve is not None and len(equity_curve) > 0:
        edf = pd.DataFrame(equity_curve, columns=["ts", "equity"])
        tmp_e = base / "equity.parquet.tmp"
        edf.to_parquet(tmp_e, index=False)
        tmp_e.replace(base / "equity.parquet")

    return base


def export_hypothesis(hypothesis_id: int, data: Dict[str, Any]) -> Path:
    """Export hypothesis to RESEARCH/hypotheses/hypothesis_000001.json."""
    p = P.RESEARCH_DIR / "hypotheses" / f"hypothesis_{hypothesis_id:06d}.json"
    _safe_json_dump(data, p)
    return p
