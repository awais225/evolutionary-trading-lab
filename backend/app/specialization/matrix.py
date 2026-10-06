"""
Strategy specialization — diagnostic matrices + specialized children (spec §9).

For a promising strategy we analyze performance across:
  TIMEFRAME (re-run genome on each timeframe's dataset)
  SESSION / DAY / REGIME / DIRECTION (grouped from realized trades)

The system does NOT auto-delete losing dimensions. Instead it proposes
specialized children (exclude Tuesday, London-only, ADX>25, high-vol only,
London+high-vol, ...) which must pass the same validation battery
independently.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..backtest.engine import BacktestRequest, run_backtest
from ..config import get_config
from ..data.engine import get_data_engine
from ..fitness.evaluator import fitness

log = logging.getLogger("specialization.matrix")

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
SESSION_NAMES = ["asia", "london", "newyork", "london_ny_overlap"]


def _group_stats(trades: List[Dict], key_fn) -> Dict[str, Dict]:
    out: Dict[str, Dict] = {}
    for t in trades:
        for k in key_fn(t):
            g = out.setdefault(k, {"trades": 0, "pnl": 0.0, "wins": 0, "gross_profit": 0.0,
                                   "gross_loss": 0.0})
            g["trades"] += 1
            pnl = t.get("pnl", 0.0)
            g["pnl"] += pnl
            if pnl > 0:
                g["wins"] += 1
                g["gross_profit"] += pnl
            else:
                g["gross_loss"] += -pnl
    for g in out.values():
        g["pnl"] = round(g["pnl"], 2)
        g["win_rate"] = round(g["wins"] / g["trades"], 3) if g["trades"] else 0.0
        g["pf"] = round(g["gross_profit"] / g["gross_loss"], 3) if g["gross_loss"] > 0 else (
            None if g["gross_profit"] == 0 else 999.0)
        del g["wins"]; del g["gross_profit"]; del g["gross_loss"]
    return out


def _session_of_trade(t: Dict) -> List[str]:
    import datetime as dt
    s = t.get("session") or ""
    keys = [s] if s else []
    ts = t.get("entry_ts")
    if ts:
        hour = dt.datetime.utcfromtimestamp(ts).hour
        if 12 <= hour < 16:
            keys.append("london_ny_overlap")
    return keys


def trade_matrices(trades: List[Dict]) -> Dict[str, Any]:
    """Session/day/regime/direction matrices grouped from realized trades."""
    return {
        "session": _group_stats(trades, _session_of_trade),
        "day": _group_stats(trades, lambda t: [DAY_NAMES[t["dow"]]] if 0 <= t.get("dow", -1) < 5 else []),
        "regime": _group_stats(trades, lambda t: [t.get("regime") or "none"]),
        "direction": _group_stats(trades, lambda t: [t.get("side", "?")]),
    }


def timeframe_matrix(genome: Dict, symbol: str) -> Dict[str, Any]:
    """Re-run the same genome on each timeframe dataset (cheap detail runs)."""
    cfg = get_config()
    de = get_data_engine()
    out: Dict[str, Any] = {}
    import copy
    for tf in cfg.data.timeframes:
        ds = de.latest_dataset(symbol, tf)
        if not ds or not ds["id"]:
            out[tf] = {"error": "no dataset"}
            continue
        g = copy.deepcopy(genome)
        g["timeframe"] = tf
        split = de.train_test_split(ds["id"])
        res = run_backtest(BacktestRequest(genome=g, dataset_id=ds["id"], stage="matrix_tf",
                                           window=(0, split["cut"]), seed_salt="tfmat"))
        if res.ok:
            f, _ = fitness(res.metrics, g)
            out[tf] = {"profit": res.metrics["net_profit"], "pf": res.metrics["profit_factor"],
                       "dd": res.metrics["max_drawdown_pct"], "trades": res.metrics["trades"],
                       "return_pct": res.metrics["total_return_pct"],
                       "sharpe": res.metrics["sharpe"], "fitness": f}
        else:
            out[tf] = {"error": res.error}
    return out


def full_matrices(genome: Dict, symbol: str, trades: List[Dict]) -> Dict[str, Any]:
    return {
        "timeframe": timeframe_matrix(genome, symbol),
        **trade_matrices(trades),
    }


# ---------------- specialized child proposals ----------------

def propose_specializations(genome: Dict, matrices: Dict[str, Any],
                            max_children: int = 5) -> List[Dict[str, Any]]:
    """Analyze weaknesses/strengths and propose directed child mutations.

    Each proposal: {"action","params","reason","expected"} — converted into
    real children by genome.ops.directed and independently validated.
    """
    props: List[Dict[str, Any]] = []
    min_group_trades = 8

    # --- day analysis: propose excluding persistently losing days ---
    day_m = matrices.get("day") or {}
    for day, st in day_m.items():
        if st["trades"] >= min_group_trades and (st.get("pf") or 0) < 0.85 and st["pnl"] < 0:
            props.append({"action": "exclude_day",
                          "params": {"day": DAY_NAMES.index(day)},
                          "reason": f"{day} loses money (PnL {st['pnl']}, PF {st.get('pf')}, "
                                    f"{st['trades']} trades) — test a child excluding {day}",
                          "expected": "improved PF by removing a negative-expectancy day"})

    # --- session analysis: concentration in one session ---
    sess_m = matrices.get("session") or {}
    good = [(s, st) for s, st in sess_m.items()
            if st["trades"] >= min_group_trades and st["pnl"] > 0 and (st.get("pf") or 0) > 1.15]
    if good:
        best = max(good, key=lambda kv: kv[1]["pnl"])
        other_loss = any(st["pnl"] < 0 for s, st in sess_m.items()
                         if s != best[0] and st["trades"] >= min_group_trades)
        if other_loss:
            props.append({"action": "restrict_sessions",
                          "params": {"sessions": [best[0]]},
                          "reason": f"profits concentrated in {best[0]} session "
                                    f"(PnL {best[1]['pnl']}); other sessions lose — "
                                    f"test a {best[0]}-only child",
                          "expected": "higher PF from session specialization"})
        if best[0] == "london_ny_overlap":
            props.append({"action": "restrict_sessions",
                          "params": {"sessions": ["london_ny_overlap"]},
                          "reason": "performance concentrated during London/NY overlap",
                          "expected": "specialized overlap-only child"})

    # --- regime analysis ---
    reg_m = matrices.get("regime") or {}
    ranging = reg_m.get("ranging")
    if ranging and ranging["trades"] >= min_group_trades and ranging["pnl"] < 0:
        props.append({"action": "add_adx_filter", "params": {"threshold": 25},
                      "reason": f"strategy underperforms during ranging conditions "
                                f"(PnL {ranging['pnl']}, {ranging['trades']} trades) — "
                                f"test an ADX>25 trend filter",
                      "expected": "fewer low-quality entries in ranges"})
    hv = reg_m.get("high_volatility")
    if hv and hv["trades"] >= min_group_trades and hv["pnl"] > 0 and (hv.get("pf") or 0) > 1.2:
        props.append({"action": "add_regime_filter",
                      "params": {"regime": "high_volatility"},
                      "reason": f"positive edge concentrated in high volatility "
                                f"(PnL {hv['pnl']}, PF {hv.get('pf')})",
                      "expected": "high-volatility specialist"})
    trending = reg_m.get("trending")
    if trending and trending["trades"] >= min_group_trades and trending["pnl"] > 0 \
            and (trending.get("pf") or 0) > 1.2:
        props.append({"action": "add_regime_filter", "params": {"regime": "trending"},
                      "reason": f"positive edge concentrated in trending regime "
                                f"(PF {trending.get('pf')})",
                      "expected": "trend specialist"})

    # --- direction analysis ---
    dir_m = matrices.get("direction") or {}
    sides = [(s, st) for s, st in dir_m.items() if st["trades"] >= min_group_trades]
    if len(sides) == 2:
        (s1, st1), (s2, st2) = sides
        if st1["pnl"] > 0 and st2["pnl"] < 0:
            props.append({"action": "restrict_direction",
                          "params": {"direction": "long" if s1 == "buy" else "short"},
                          "reason": f"{s1} side profitable ({st1['pnl']}) while {s2} "
                                    f"loses ({st2['pnl']}) — test directional specialist",
                          "expected": "removal of negative-expectancy side"})

    # --- timeframe analysis ---
    tf_m = matrices.get("timeframe") or {}
    cur_tf = genome.get("timeframe")
    better = [(tf, st) for tf, st in tf_m.items()
              if tf != cur_tf and isinstance(st, dict) and st.get("fitness")
              and st["trades"] >= min_group_trades]
    cur_fit = (tf_m.get(cur_tf) or {}).get("fitness") if isinstance(tf_m.get(cur_tf), dict) else None
    if better and cur_fit is not None:
        best_tf, best_st = max(better, key=lambda kv: kv[1]["fitness"])
        if best_st["fitness"] > cur_fit * 1.25 + 0.02:
            props.append({"action": "change_timeframe", "params": {"timeframe": best_tf},
                          "reason": f"same logic scores better on {best_tf} "
                                    f"(fitness {best_st['fitness']} vs {round(cur_fit,3)})",
                          "expected": "timeframe-specialized child"})

    return props[:max_children]
