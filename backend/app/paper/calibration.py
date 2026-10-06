"""
Live calibration (spec §16).

Aggregates execution statistics observed by the paper engine (spread,
slippage, latency — from the REAL MT5 feed when connected, or the labelled
simulator feed) and compares them with the backtest execution assumptions.

History is never silently rewritten: we report
    BACKTEST ASSUMPTION  vs  OBSERVED EXECUTION
and offer an explicit user-triggered recalibration that updates the config
used by FUTURE simulations.
"""
from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional

from ..config import get_config, update_config
from ..db.database import get_db
from ..mt5 import get_bridge

log = logging.getLogger("paper.calibration")


def observed_stats() -> Dict:
    db = get_db()
    row = db.one("""SELECT COUNT(*) n,
                    AVG(slippage_points) slip, AVG(spread_points) spread,
                    AVG(exec_delay_ms) delay,
                    MAX(slippage_points) slip_max
                    FROM executions WHERE result='FILLED'""")
    by_source = db.q("""SELECT source, COUNT(*) n, AVG(slippage_points) slip,
                        AVG(spread_points) spread, AVG(exec_delay_ms) delay
                        FROM executions WHERE result='FILLED' GROUP BY source""")
    return {"overall": dict(row or {}), "by_source": by_source}


def report() -> Dict:
    cfg = get_config()
    obs = observed_stats()
    o = obs["overall"]
    n = int(o.get("n") or 0)
    bt = cfg.backtest
    rows = [
        {"metric": "avg_slippage_points",
         "backtest_assumption": bt.slippage_mean_points,
         "observed": round(float(o.get("slip") or 0), 3) if n else None,
         "samples": n},
        {"metric": "avg_spread_points",
         "backtest_assumption": bt.default_spread_points,
         "observed": round(float(o.get("spread") or 0), 3) if n else None,
         "samples": n},
        {"metric": "exec_delay_ms",
         "backtest_assumption": bt.execution_delay_ms,
         "observed": round(float(o.get("delay") or 0), 1) if n else None,
         "samples": n},
    ]
    for r in rows:
        if r["observed"] is not None and r["backtest_assumption"]:
            r["delta_pct"] = round((r["observed"] - r["backtest_assumption"]) /
                                   abs(r["backtest_assumption"]) * 100.0, 1)
    return {
        "feed_source": get_bridge().source,
        "assumption_vs_observed": rows,
        "by_source": obs["by_source"],
        "note": "Historical results are NOT modified. Recalibration only "
                "affects FUTURE backtests/paper simulations and requires an "
                "explicit user action.",
    }


def snapshot_to_db() -> None:
    """Persist a calibration snapshot (called periodically by orchestrator)."""
    db = get_db()
    cfg = get_config().backtest
    obs = observed_stats()["overall"]
    n = int(obs.get("n") or 0)
    if n < 5:
        return
    source = get_bridge().source
    db.x("""INSERT INTO calibration (ts,source,symbol,metric,backtest_assumption,observed,samples)
            VALUES (?,?,?,?,?,?,?)""",
         (time.time(), source, get_config().data.symbol, "slippage_points",
          cfg.slippage_mean_points, float(obs.get("slip") or 0), n))
    db.x("""INSERT INTO calibration (ts,source,symbol,metric,backtest_assumption,observed,samples)
            VALUES (?,?,?,?,?,?,?)""",
         (time.time(), source, get_config().data.symbol, "spread_points",
          cfg.default_spread_points, float(obs.get("spread") or 0), n))


def recalibrate(apply_to_config: bool = True) -> Dict:
    """Explicit user action: adopt observed stats for FUTURE simulations."""
    rep = report()
    applied = {}
    if apply_to_config:
        for r in rep["assumption_vs_observed"]:
            if r["observed"] is None:
                continue
            if r["metric"] == "avg_slippage_points":
                update_config("backtest", {"slippage_mean_points": max(0.1, r["observed"])})
                applied["slippage_mean_points"] = r["observed"]
            elif r["metric"] == "avg_spread_points":
                update_config("backtest", {"default_spread_points": max(0.5, r["observed"])})
                applied["default_spread_points"] = r["observed"]
            elif r["metric"] == "exec_delay_ms":
                update_config("backtest", {"execution_delay_ms": int(max(20, r["observed"]))})
                applied["execution_delay_ms"] = r["observed"]
        log.warning("Execution model RECALIBRATED from observed paper stats: %s", applied)
        get_db().log_event("recalibrated", applied)
    return {"applied": applied, "report": rep}
