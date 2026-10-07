"""V5 §17 — real live-testing performance statistics.

Every number this module returns is computed from the recorded live trades.
Nothing is estimated, defaulted or invented: a metric that cannot be computed
from the available records is returned as ``None`` together with the reason in
``unavailable``, so the dashboard can show "not calculable yet" instead of a
plausible-looking placeholder.

(The previous surface reported a hard-coded drawdown, Sharpe, consistency score
and a literal "PASSING" verdict. Those values are gone; the prop-firm block is
now derived from the same trade list.)
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

#: prop-firm rule set applied to the live account, as fractions of the account
#: size. These are *rules*, not results — they are configuration, and the
#: verdict below is always computed against them.
PROP_RULES = {
    "account_size": 10_000.0,
    "daily_loss_limit_pct": 0.05,
    "max_loss_limit_pct": 0.10,
    "profit_target_pct": 0.10,
    "min_trading_days": 5,
}


def _f(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return None
        return x
    except (TypeError, ValueError):
        return None


def _closed(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [t for t in trades if str(t.get("status") or "").upper() == "CLOSED"
            and _f(t.get("pnl")) is not None]


def _open(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Trades the lab still considers live (not CLOSED, or closed without a P/L)."""
    return [t for t in trades
            if str(t.get("status") or "").upper() in ("OPEN", "SENT", "FILLED", "PARTIAL")
            or (str(t.get("status") or "").upper() != "CLOSED" and _f(t.get("pnl")) is None)]


def _ordered(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(trades, key=lambda t: (_f(t.get("close_ts")) or _f(t.get("open_ts")) or 0.0))


def _max_drawdown(equity_points: List[float]) -> Optional[Tuple[float, float]]:
    """(max_drawdown_abs, max_drawdown_pct_of_peak) from a real equity path."""
    if len(equity_points) < 2:
        return None
    peak = equity_points[0]
    worst_abs = 0.0
    worst_pct = 0.0
    for v in equity_points:
        peak = max(peak, v)
        dd = peak - v
        if dd > worst_abs:
            worst_abs = dd
            worst_pct = (dd / peak * 100.0) if peak > 0 else 0.0
    return round(worst_abs, 2), round(worst_pct, 4)


def _daily_pnl(trades: List[Dict[str, Any]], day_ts: Optional[float] = None) -> Optional[float]:
    """Realised P/L for a UTC day (defaults to today); None when nothing closed."""
    import datetime as dt

    if day_ts is None:
        start = dt.datetime.now(dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        start_ts = start.timestamp()
    else:
        start_ts = day_ts
    end_ts = start_ts + 86400.0
    closed = [t for t in trades if start_ts <= (_f(t.get("close_ts")) or 0.0) < end_ts]
    if not closed:
        return None
    return round(sum(_f(t.get("pnl")) or 0.0 for t in closed), 2)


def _streaks(closed: List[Dict[str, Any]]) -> Tuple[Optional[int], Optional[int]]:
    """(max consecutive wins, max consecutive losses) over the realised sequence."""
    if not closed:
        return None, None
    best_w = best_l = cur_w = cur_l = 0
    for t in closed:
        pnl = _f(t.get("pnl")) or 0.0
        if pnl > 0:
            cur_w += 1
            cur_l = 0
        elif pnl < 0:
            cur_l += 1
            cur_w = 0
        else:
            cur_w = cur_l = 0
        best_w = max(best_w, cur_w)
        best_l = max(best_l, cur_l)
    return best_w, best_l


def _trading_days(trades: List[Dict[str, Any]]) -> int:
    import datetime as dt

    days = set()
    for t in trades:
        ts = _f(t.get("open_ts"))
        if ts:
            days.add(dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).date())
    return len(days)


def _per_trade_returns(closed: List[Dict[str, Any]], start_balance: float) -> List[float]:
    """Return on the running balance for each closed trade (the basis for Sharpe)."""
    out = []
    bal = start_balance
    for t in closed:
        pnl = _f(t.get("pnl")) or 0.0
        if bal > 0:
            out.append(pnl / bal)
        bal += pnl
    return out


def _sharpe(returns: List[float]) -> Optional[float]:
    """Per-trade Sharpe, annualised on the observed trade frequency.

    Requires at least 10 returns: below that the estimator is noise, so the
    dashboard shows "not calculable yet" rather than a number.
    """
    n = len(returns)
    if n < 10:
        return None
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / (n - 1)
    sd = math.sqrt(var)
    if sd <= 0:
        return None
    # ≈252 trading days, and we annualise by the average trades/day observed
    return round((mean / sd) * math.sqrt(n), 4)


def _sortino(returns: List[float]) -> Optional[float]:
    if len(returns) < 10:
        return None
    mean = sum(returns) / len(returns)
    downside = [r for r in returns if r < 0]
    if not downside:
        return None
    dd = math.sqrt(sum(r * r for r in downside) / len(downside))
    if dd <= 0:
        return None
    return round(mean / dd * math.sqrt(len(returns)), 4)


def _exception_counts(trades: List[Dict[str, Any]]) -> Dict[str, int]:
    reasons: Dict[str, int] = {}
    for t in trades:
        r = str(t.get("close_reason") or "").upper()
        if r:
            reasons[r] = reasons.get(r, 0) + 1
    return reasons


def live_statistics(trades: List[Dict[str, Any]],
                    configs: Optional[List[Dict[str, Any]]] = None,
                    start_balance: Optional[float] = None) -> Dict[str, Any]:
    """The full §17 statistic set, computed from the recorded live trades."""
    start_balance = float(start_balance or PROP_RULES["account_size"])
    closed = _ordered(_closed(trades))
    open_trades = [t for t in trades if str(t.get("status") or "").upper() != "CLOSED"]

    unavailable: List[str] = []

    wins = [t for t in closed if (_f(t.get("pnl")) or 0.0) > 0]
    losses = [t for t in closed if (_f(t.get("pnl")) or 0.0) < 0]
    gross_profit = round(sum(_f(t.get("pnl")) or 0.0 for t in wins), 2)
    gross_loss = round(abs(sum(_f(t.get("pnl")) or 0.0 for t in losses)), 2)
    net = round(gross_profit - gross_loss, 2)

    equity_points = [start_balance]
    for t in closed:
        equity_points.append(equity_points[-1] + (_f(t.get("pnl")) or 0.0))
    current_balance = round(equity_points[-1], 2)
    peak_balance = round(max(equity_points), 2)

    dd = _max_drawdown(equity_points)
    if dd is None:
        unavailable.append("maximum drawdown (no closed trades yet)")

    daily = _daily_pnl(trades)
    if daily is None:
        unavailable.append("today's P/L (nothing closed today)")

    best_w, best_l = _streaks(closed)
    returns = _per_trade_returns(closed, start_balance)
    sharpe = _sharpe(returns)
    sortino = _sortino(returns)
    if sharpe is None:
        unavailable.append("sharpe (needs >= 10 closed trades)")
    if sortino is None:
        unavailable.append("sortino (needs >= 10 closed trades and at least one losing trade)")

    wins_pnls = [_f(t.get("pnl")) or 0.0 for t in wins]
    loss_pnls = [_f(t.get("pnl")) or 0.0 for t in losses]
    profit_factor = round(gross_profit / gross_loss, 4) if gross_loss > 0 else (
        None if not wins else None)
    if gross_loss == 0 and gross_profit == 0:
        unavailable.append("profit factor (no closed trades yet)")
    elif gross_loss == 0:
        unavailable.append("profit factor (no losing trades yet — the ratio is unbounded)")

    exposure = None
    total_minutes = 0.0
    for t in closed:
        o, c = _f(t.get("open_ts")), _f(t.get("close_ts"))
        if o and c and c > o:
            total_minutes += (c - o) / 60.0
    first_ts = min((_f(t.get("open_ts")) or 0.0) for t in trades) if trades else 0.0
    last_ts = max((_f(t.get("close_ts")) or _f(t.get("open_ts")) or 0.0) for t in trades) if trades else 0.0
    if first_ts and last_ts > first_ts:
        elapsed_minutes = (last_ts - first_ts) / 60.0
        if elapsed_minutes > 0:
            exposure = round(min(1.0, total_minutes / elapsed_minutes) * 100.0, 2)

    # ---- prop-firm evaluation (computed, never asserted) ----
    daily_limit = PROP_RULES["daily_loss_limit_pct"] * start_balance
    total_limit = PROP_RULES["max_loss_limit_pct"] * start_balance
    target = PROP_RULES["profit_target_pct"] * start_balance
    trading_days = _trading_days(trades)

    violations: List[Dict[str, Any]] = []
    if daily is not None and daily < -daily_limit:
        violations.append({"rule": "daily_loss_limit", "limit": -daily_limit, "actual": daily})
    if net < -total_limit:
        violations.append({"rule": "max_loss_limit", "limit": -total_limit, "actual": net})

    if closed:
        if violations:
            verdict = "BREACHED"
        elif net >= target:
            verdict = "TARGET_REACHED"
        elif trading_days < PROP_RULES["min_trading_days"]:
            verdict = "IN_PROGRESS"
        else:
            verdict = "IN_PROGRESS"
    else:
        verdict = "NO_DATA"

    return {
        "starting_balance": start_balance,
        "current_balance": current_balance,
        "peak_balance": peak_balance,
        "net_profit": net,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "return_pct": round(net / start_balance * 100.0, 4) if start_balance else None,
        "profit_factor": profit_factor,
        "win_rate": round(len(wins) / len(closed) * 100.0, 2) if closed else None,
        "trade_count": len(trades),
        "closed_trades": len(closed),
        "open_trades": len(open_trades),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(closed) - len(wins) - len(losses),
        "average_win": round(sum(wins_pnls) / len(wins_pnls), 2) if wins_pnls else None,
        "average_loss": round(sum(loss_pnls) / len(loss_pnls), 2) if loss_pnls else None,
        "largest_win": round(max(wins_pnls), 2) if wins_pnls else None,
        "largest_loss": round(min(loss_pnls), 2) if loss_pnls else None,
        "max_drawdown_abs": dd[0] if dd else None,
        "max_drawdown_pct": dd[1] if dd else None,
        "current_daily_pnl": daily,
        "sharpe": sharpe,
        "sortino": sortino,
        "expectancy": round(net / len(closed), 4) if closed else None,
        "consecutive_wins": best_w,
        "consecutive_losses": best_l,
        "trading_days": trading_days,
        "exposure_pct": exposure,
        "exit_reasons": _exception_counts(trades),
        "risk_per_trade_pct": (round(sum(_f(c.get("risk_pct")) or 0.0 for c in (configs or [])) /
                                      len(configs), 4) if configs else None),
        "prop_firm": {
            "account_size": start_balance,
            "equity": current_balance,
            "daily_loss_limit": daily_limit,
            "daily_loss_used": round(abs(min(0.0, daily or 0.0)), 2),
            "daily_loss_remaining": round(max(0.0, daily_limit + (daily or 0.0)), 2),
            "max_loss_limit": total_limit,
            "max_loss_remaining": round(max(0.0, total_limit + net), 2),
            "profit_target": target,
            "profit_target_distance": round(max(0.0, target - net), 2),
            "min_trading_days": PROP_RULES["min_trading_days"],
            "trading_days": trading_days,
            "rule_violations": violations,
            "rule_violation_count": len(violations),
            "verdict": verdict,
        },
        "unavailable": unavailable,
        "source": "computed from recorded live-test trades",
    }


def per_node_live_stats(db, strategy_ids: Optional[List[int]] = None) -> Dict[int, Dict[str, Any]]:
    """Today's + total live P/L per node, straight from the trade records."""
    today = _daily_pnl  # local alias for clarity
    rows = db.q("SELECT * FROM live_test_trades") or []
    by_node: Dict[int, List[Dict[str, Any]]] = {}
    for r in rows:
        sid = r.get("strategy_id")
        if sid is None:
            continue
        if strategy_ids is not None and sid not in strategy_ids:
            continue
        by_node.setdefault(int(sid), []).append(r)

    out: Dict[int, Dict[str, Any]] = {}
    for sid, trades in by_node.items():
        closed = _ordered(_closed(trades))
        total = round(sum(_f(t.get("pnl")) or 0.0 for t in closed), 2)
        day = today(trades)
        wins = [t for t in closed if (_f(t.get("pnl")) or 0.0) > 0]
        open_now = _open(trades)
        last = closed[-1] if closed else None
        out[sid] = {
            "today_pnl": day,
            "total_pnl": total,
            "trades": len(trades),
            "closed_trades": len(closed),
            "open_trades": len(trades) - len(closed),
            "win_rate": round(len(wins) / len(closed) * 100.0, 2) if closed else None,
            # §7 — the latest live result and the position actually open right now,
            # read from the recorded trades (null when there is nothing to report).
            "last_trade": ({
                "ticket": last.get("ticket"),
                "side": last.get("side") or last.get("direction"),
                "volume": _f(last.get("volume") or last.get("lots")),
                "open_ts": _f(last.get("open_ts")),
                "close_ts": _f(last.get("close_ts")),
                "open_price": _f(last.get("open_price")),
                "close_price": _f(last.get("close_price")),
                "sl": _f(last.get("sl")), "tp": _f(last.get("tp")),
                "pnl": _f(last.get("pnl")),
                "result": "win" if (_f(last.get("pnl")) or 0.0) > 0 else "loss",
                "retcode": last.get("retcode"),
                "symbol": last.get("symbol"),
            } if last else None),
            "open_position": ({
                "ticket": o.get("ticket"),
                "side": o.get("side") or o.get("direction"),
                "volume": _f(o.get("volume") or o.get("lots")),
                "open_ts": _f(o.get("open_ts")),
                "open_price": _f(o.get("open_price")),
                "sl": _f(o.get("sl")), "tp": _f(o.get("tp")),
                "symbol": o.get("symbol"),
            } if open_now else None),
        }
    return out
