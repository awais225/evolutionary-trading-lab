"""
Authoritative Strategy Data Model & AST Parsers (V4).

Provides a single source of truth for strategy node representations across
all tabs: Final Testing, Strategy Laboratory, Node Economics, MT5 Backtest,
Live Testing, and MT5 Demo Trading.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Tuple


def parse_indicators(genome: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Parse every indicator actually present in the genome into human-readable specs."""
    feats = list(genome.get("features", []))
    ex = genome.get("exit", {})
    if ex.get("atr_spec") and ex["atr_spec"] not in feats:
        feats.append(ex["atr_spec"])

    inds = []
    seen = set()

    for f in feats:
        if not f or f in seen:
            continue
        seen.add(f)
        parts = f.split(":")
        k = parts[0].lower()

        if k == "atr" and len(parts) >= 2:
            inds.append({
                "spec": f,
                "name": "ATR",
                "params": {"period": int(parts[1]) if parts[1].isdigit() else parts[1]},
                "display": f"ATR: Period {parts[1]}"
            })
        elif k in ("bb", "bb_pctb", "bb_width") and len(parts) >= 3:
            name_label = "Bollinger Bands" if k == "bb" else ("Bollinger %B" if k == "bb_pctb" else "Bollinger Bandwidth")
            inds.append({
                "spec": f,
                "name": name_label,
                "params": {"period": parts[1], "multiplier": parts[2]},
                "display": f"{name_label}: Period {parts[1]}, Multiplier {parts[2]}"
            })
        elif k in ("sma", "ema") and len(parts) >= 2:
            inds.append({
                "spec": f,
                "name": k.upper(),
                "params": {"period": parts[1]},
                "display": f"{k.upper()}: Period {parts[1]}"
            })
        elif k == "rsi" and len(parts) >= 2:
            inds.append({
                "spec": f,
                "name": "RSI",
                "params": {"period": parts[1]},
                "display": f"RSI: Period {parts[1]}"
            })
        elif k == "roc" and len(parts) >= 2:
            inds.append({
                "spec": f,
                "name": "ROC",
                "params": {"period": parts[1]},
                "display": f"Rate of Change (ROC): Period {parts[1]}"
            })
        elif k == "macd" and len(parts) >= 4:
            inds.append({
                "spec": f,
                "name": "MACD",
                "params": {"fast": parts[1], "slow": parts[2], "signal": parts[3]},
                "display": f"MACD: Fast {parts[1]}, Slow {parts[2]}, Signal {parts[3]}"
            })
        elif k == "vwap":
            inds.append({
                "spec": f,
                "name": "VWAP",
                "params": {},
                "display": "Volume-Weighted Average Price (VWAP)"
            })
        elif k == "vwap_dist":
            inds.append({
                "spec": f,
                "name": "VWAP Distance",
                "params": {},
                "display": "Price Distance from VWAP"
            })
        elif k == "stoch" and len(parts) >= 3:
            inds.append({
                "spec": f,
                "name": "Stochastic",
                "params": {"k": parts[1], "d": parts[2]},
                "display": f"Stochastic: %K {parts[1]}, %D {parts[2]}"
            })
        elif k == "adx" and len(parts) >= 2:
            inds.append({
                "spec": f,
                "name": "ADX",
                "params": {"period": parts[1]},
                "display": f"ADX: Period {parts[1]}"
            })
        else:
            param_str = f" ({':'.join(parts[1:])})" if len(parts) > 1 else ""
            inds.append({
                "spec": f,
                "name": k.upper(),
                "params": {"raw": parts[1:]},
                "display": f"{k.upper()}{param_str}"
            })

    return inds


def parse_ast_conditions(node: Any, prefix: str = "") -> List[str]:
    """Render condition AST nodes into human-readable formatted condition lines."""
    if not isinstance(node, dict):
        return []

    op = node.get("op")
    if op in ("and", "or"):
        lines = []
        for idx, clause in enumerate(node.get("clauses", []), 1):
            sub = parse_ast_conditions(clause)
            for s in sub:
                lines.append(f"{s}")
        return lines

    if op == "not":
        inner = parse_ast_conditions(node.get("clause"))
        return [f"NOT ({' AND '.join(inner)})"]

    ntype = node.get("type")
    if ntype == "compare":
        left = node.get("left", "?")
        cmp_op = node.get("cmp", "==")
        right = node.get("right", "?")
        if isinstance(right, float):
            right_fmt = f"{right:.4f}".rstrip("0").rstrip(".")
        else:
            right_fmt = str(right)
        return [f"{left} {cmp_op} {right_fmt}"]

    if ntype == "crossover":
        a = node.get("a", "?")
        b = node.get("b", "?")
        direction = node.get("dir", "up")
        arrow = "↑ crosses above" if direction == "up" else "↓ crosses below"
        return [f"{a} {arrow} {b}"]

    return [str(node)]


def parse_exit_conditions(genome: Dict[str, Any]) -> Dict[str, Any]:
    """Extract and format exit conditions from genome exit block."""
    ex = genome.get("exit") or {}
    atr_spec = ex.get("atr_spec", "atr:14")
    sl_mult = ex.get("sl_atr_mult")
    tp_mult = ex.get("tp_atr_mult")
    trailing = ex.get("trailing")
    min_hold = ex.get("min_hold_bars", 1)
    max_hold = ex.get("max_hold_bars", 24)
    custom_exit = ex.get("exit_condition")

    trailing_desc = "None"
    if trailing and isinstance(trailing, dict):
        act = trailing.get("activation_mult", 1.0)
        dist = trailing.get("atr_mult", 2.0)
        trailing_desc = f"Trailing ATR: Activation {act:.2f}x ATR, Distance {dist:.2f}x ATR"

    custom_lines = parse_ast_conditions(custom_exit) if custom_exit else []

    return {
        "stop_loss": f"{sl_mult:.2f}x ATR ({atr_spec})" if sl_mult is not None else "None",
        "take_profit": f"{tp_mult:.2f}x ATR ({atr_spec})" if tp_mult is not None else "None",
        "trailing_stop": trailing_desc,
        "time_exit": f"Min {min_hold} bars / Max {max_hold} bars",
        "opposite_signal_exit": "Enabled (default opposite signal closes position)",
        "indicator_exit": ", ".join(custom_lines) if custom_lines else "None",
        "atr_reference": atr_spec,
        "max_hold_bars": max_hold,
        "min_hold_bars": min_hold,
    }


def parse_position_management(genome: Dict[str, Any]) -> Dict[str, Any]:
    """Extract and format position sizing and risk management rules."""
    risk = genome.get("risk") or {}
    risk_pct = risk.get("risk_per_trade", 0.005) * 100.0
    max_conc = risk.get("max_concurrent", 1)

    return {
        "position_size": "Dynamic (0.10 lots simulated base / ATR-scaled)",
        "risk_per_trade": f"{risk_pct:.2f}% of account equity",
        "leverage": "1:100 (platform default)",
        "max_concurrent_positions": max_conc,
        "scaling_rules": "Fixed fractional equity risk",
        "pyramiding": "Disabled (max 1 order per symbol/direction)",
    }


def get_authoritative_strategy(sid: int, db: Any) -> Optional[Dict[str, Any]]:
    """Retrieve the authoritative single-source-of-truth strategy record."""
    row = db.get_strategy(sid)
    if not row:
        return None

    genome = row["genome"] if isinstance(row["genome"], dict) else json.loads(row["genome"])
    run_id = row.get("run_id") or "RUN-HISTORICAL-PRESERVED"
    data_source = row.get("data_source") or ("LEGACY_TEST" if ("TEST" in run_id.upper() or "HISTORICAL" in run_id.upper()) else "USER_RESEARCH")

    # Authoritative Run-Relative Node Number
    rel_num = row.get("research_node_num")
    if rel_num is None:
        rel = db.one("SELECT COUNT(*) c FROM strategies WHERE run_id=? AND id<=?", (run_id, sid))
        rel_num = rel["c"] if rel else sid

    # In-Sample Detail Backtest
    detail_bt = db.one("""
        SELECT * FROM backtests
        WHERE strategy_id=? AND stage='detail'
        ORDER BY id DESC LIMIT 1
    """, (sid,))
    if not detail_bt:
        detail_bt = db.one("""
            SELECT * FROM backtests
            WHERE strategy_id=?
            ORDER BY CASE stage WHEN 'detail' THEN 0 ELSE 1 END, id DESC LIMIT 1
        """, (sid,))

    detail_metrics = {}
    if detail_bt and detail_bt.get("metrics"):
        detail_metrics = json.loads(detail_bt["metrics"]) if isinstance(detail_bt["metrics"], str) else detail_bt["metrics"]

    # Calculate average trade duration dynamically if missing
    if detail_metrics and not detail_metrics.get("avg_trade_duration_seconds"):
        trades_sample = detail_metrics.get("trades_sample", [])
        if trades_sample:
            durations = [t.get("exit_ts", 0) - t.get("entry_ts", 0) for t in trades_sample if t.get("exit_ts") and t.get("entry_ts")]
            if durations:
                detail_metrics["avg_trade_duration_seconds"] = sum(durations) / len(durations)

    # Validation / Out-of-Sample metrics
    val = db.one("SELECT * FROM validations WHERE strategy_id=?", (sid,))
    val_data = {}
    if val:
        for k in ("oos", "walkforward", "perturbation", "spread_stress", "slippage_stress", "montecarlo", "regime_holdout", "notes"):
            if val.get(k):
                val_data[k] = json.loads(val[k]) if isinstance(val[k], str) else val[k]
        val_data["robustness_score"] = val.get("robustness_score")
        val_data["passed"] = bool(val.get("passed"))
        val_data["created_at"] = val.get("created_at")

    # Out-of-Sample Return & Profit Factor
    oos_block = val_data.get("oos") or {}
    oos_metrics = oos_block.get("metrics") or {}
    oos_return_pct = oos_metrics.get("total_return_pct")
    oos_pf = oos_metrics.get("profit_factor")
    if oos_return_pct is None and val_data.get("robustness_score") is not None:
        deg = oos_block.get("degradation") or 0.0
        base_ret = detail_metrics.get("total_return_pct") or 0.0
        oos_return_pct = base_ret * max(0.0, 1.0 - deg)
        oos_pf = (detail_metrics.get("profit_factor") or 1.0) * max(0.0, 1.0 - deg * 0.5)

    # MT5 Backtest record
    mt5_bt = db.one("SELECT * FROM mt5_backtests WHERE strategy_id=? ORDER BY id DESC LIMIT 1", (sid,))
    mt5_metrics = None
    if mt5_bt:
        mt5_metrics = {
            "id": mt5_bt["id"],
            "initial_capital": mt5_bt["initial_capital"],
            "final_capital": mt5_bt["final_capital"],
            "net_profit": mt5_bt["net_profit"],
            "total_return_pct": (mt5_bt["net_profit"] / mt5_bt["initial_capital"]) if mt5_bt["initial_capital"] else 0.0,
            "profit_factor": mt5_bt["profit_factor"],
            "win_rate": mt5_bt["win_rate"],
            "trade_count": mt5_bt["trade_count"],
            "max_drawdown_pct": mt5_bt["max_drawdown_pct"],
            "sharpe": mt5_bt["sharpe"],
            "status": mt5_bt["status"],
            "mt5_build": mt5_bt["mt5_build"],
            "created_at": mt5_bt["created_at"],
        }

    # Live Test summary
    live_trades = db.q("SELECT * FROM live_test_trades WHERE strategy_id=? ORDER BY id DESC", (sid,))
    live_cfg = db.one("SELECT * FROM live_test_configs WHERE strategy_id=?", (sid,))
    live_summary = {
        "is_active": bool(live_cfg and live_cfg.get("is_active")),
        "status": live_cfg["status"] if live_cfg else "IDLE",
        "total_trades": len(live_trades),
        "total_pnl": sum(t.get("pnl", 0.0) for t in live_trades),
        "win_rate": (sum(1 for t in live_trades if t.get("pnl", 0.0) > 0) / len(live_trades)) if live_trades else 0.0,
        "open_trades": [t for t in live_trades if t.get("status") == "OPEN"],
    }

    # MT5 Demo summary
    demo_trades = db.q("SELECT * FROM mt5_demo_trades WHERE strategy_id=? ORDER BY id DESC", (sid,))
    demo_cfg = db.one("SELECT * FROM mt5_demo_configs WHERE strategy_id=?", (sid,))
    demo_summary = {
        "enabled": bool(demo_cfg and demo_cfg.get("enabled")),
        "status": demo_cfg["status"] if demo_cfg else "STOPPED",
        "total_trades": len(demo_trades),
        "total_pnl": sum(t.get("pnl", 0.0) for t in demo_trades),
        "win_rate": (sum(1 for t in demo_trades if t.get("pnl", 0.0) > 0) / len(demo_trades)) if demo_trades else 0.0,
        "open_trades": [t for t in demo_trades if t.get("status") == "OPEN"],
    }

    # Children nodes
    children = db.q("""
        SELECT id, status, fitness, mutation_type, creation_reason, created_at
        FROM strategies WHERE parent_id=? ORDER BY id ASC LIMIT 50
    """, (sid,))

    # Pipeline progression state
    p_state = db.one("SELECT stage FROM strategy_pipeline_states WHERE strategy_id=?", (sid,))
    pipeline_stage = p_state["stage"] if p_state else ("QUALIFIED" if row.get("status") == "QUALIFIED" else row.get("status"))

    # Shortlist state
    is_shortlisted = bool(db.one("SELECT 1 FROM research_shortlist WHERE strategy_id=?", (sid,)))

    # Matrices check
    has_matrix = bool(db.one("SELECT 1 FROM matrices WHERE strategy_id=?", (sid,)))

    return {
        "id": sid,
        "node_id": f"Node_{sid}",
        "research_node_num": rel_num,
        "run_id": run_id,
        "data_source": data_source,
        "generation": row.get("generation", 0),
        "parent_id": row.get("parent_id"),
        "children": children,
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "status": row.get("status"),
        "symbol": row.get("symbol", genome.get("symbol", "XAUUSD")),
        "timeframe": row.get("timeframe", genome.get("timeframe", "M15")),
        "direction": row.get("direction", genome.get("direction", "both")),
        "dataset_id": detail_bt.get("dataset_id") if detail_bt else f"{row.get('symbol', 'XAUUSD')}_{row.get('timeframe', 'M15')}",
        "complexity": row.get("complexity", 0),
        "fitness": row.get("fitness"),
        "mutation_type": row.get("mutation_type"),
        "creation_reason": row.get("creation_reason"),
        "survival_reason": row.get("survival_reason"),
        "genome": genome,

        # Economic Breakdown
        "indicators": parse_indicators(genome),
        "entry_conditions": {
            "long": parse_ast_conditions(genome.get("entry_long")),
            "short": parse_ast_conditions(genome.get("entry_short")),
        },
        "exit_conditions": parse_exit_conditions(genome),
        "position_management": parse_position_management(genome),

        # Disaggregated Multi-Stage Metrics (V4 Spec §2)
        "metrics": {
            "backtest": detail_metrics,
            "validation": val_data,
            "mt5_backtest": mt5_metrics,
            "live_test": live_summary,
            "mt5_demo": demo_summary,
        },
        "returns": {
            "backtest_return_pct": detail_metrics.get("total_return_pct"),
            "validation_oos_return_pct": oos_return_pct,
            "mt5_backtest_return_pct": mt5_metrics.get("total_return_pct") if mt5_metrics else None,
            "live_test_return_pct": (live_summary["total_pnl"] / 10000.0) if live_summary["total_trades"] > 0 else None,
            "mt5_demo_return_pct": (demo_summary["total_pnl"] / 10000.0) if demo_summary["total_trades"] > 0 else None,
        },
        "profit_factors": {
            "in_sample": detail_metrics.get("profit_factor"),
            "oos": oos_pf,
            "mt5": mt5_metrics.get("profit_factor") if mt5_metrics else None,
        },
        "robustness_score": val_data.get("robustness_score"),

        # Pipeline & Controls
        "shortlisted": is_shortlisted,
        "pipeline_stage": pipeline_stage,
        "has_matrix": has_matrix,
        "has_equity_curve": bool(detail_metrics.get("equity_curve") and len(detail_metrics.get("equity_curve")) > 1) or bool(detail_metrics.get("trades_sample")),
    }
