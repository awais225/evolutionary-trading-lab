"""DeepTesting analytics over REAL simulated trades and equity curves.

Every number derives from the persisted trade ledger / equity curve of an
actual engine run. Metrics that cannot be computed honestly are None
(JSON null) with the UI rendering "N/A" - never 0, never invented.

Definitions (documented, deterministic):
* Drawdown is computed on the EQUITY curve (balance + unrealized at each
  bar close). Max drawdown amount is peak-to-trough in account currency;
  percentage is relative to the peak. Drawdown events are peak->trough
  episodes with recovery timestamps.
* Sharpe/Sortino use DAILY equity returns (resampled at UTC day closes),
  annualized by sqrt(252); they are None with <2 daily returns or when
  there are no trades. This methodology is stated wherever shown.
* Streaks run over trades in exit-time order.
* Time buckets attribute trades by ENTRY time (UTC), per the brief.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .engine import session_of


def _ordered(trades: List[dict]) -> List[dict]:
    return sorted(trades, key=lambda t: (t["exit_ts"], t["entry_ts"]))


def compute_metrics(trades: List[dict], equity_curve: List[dict],
                    initial_capital: float) -> Dict[str, Any]:
    trades = _ordered(trades)
    wins = [t for t in trades if t["net_pnl"] > 1e-9]
    losses = [t for t in trades if t["net_pnl"] < -1e-9]
    breakeven = [t for t in trades if abs(t["net_pnl"]) <= 1e-9]
    gross_profit = sum(t["net_pnl"] for t in wins)
    gross_loss = sum(t["net_pnl"] for t in losses)
    net = sum(t["net_pnl"] for t in trades)
    dd = drawdown_analysis(equity_curve)
    streaks = streak_analysis(trades)
    final_equity = equity_curve[-1]["equity"] if equity_curve else initial_capital
    final_balance = equity_curve[-1]["balance"] if equity_curve else initial_capital
    daily = _daily_returns(equity_curve)
    sharpe = sortino = None
    if len(daily) >= 2 and trades:
        mean = statistics.fmean(daily)
        sd = statistics.pstdev(daily)
        if sd > 0:
            sharpe = round(mean / sd * math.sqrt(252), 4)
        downside = [r for r in daily if r < 0]
        if downside:
            dsd = math.sqrt(sum(r * r for r in downside) / len(daily))
            if dsd > 0:
                sortino = round(mean / dsd * math.sqrt(252), 4)
    recovery = None
    if dd["max_drawdown_amount"] > 0 and net > 0:
        recovery = round(net / dd["max_drawdown_amount"], 4)
    span_days = 0.0
    if equity_curve:
        span_days = max(1e-9, (equity_curve[-1]["ts"] - equity_curve[0]["ts"]) / 86400.0)
    return {
        "total_trades": len(trades),
        "winning_trades": len(wins), "losing_trades": len(losses),
        "breakeven_trades": len(breakeven),
        "win_rate": round(len(wins) / len(trades), 6) if trades else None,
        "gross_profit": round(gross_profit, 4),
        "gross_loss": round(gross_loss, 4),
        "net_profit": round(net, 4),
        "profit_factor": round(gross_profit / abs(gross_loss), 4) if gross_loss < 0 else None,
        "avg_win": round(gross_profit / len(wins), 4) if wins else None,
        "avg_loss": round(gross_loss / len(losses), 4) if losses else None,
        "largest_win": round(max((t["net_pnl"] for t in wins), default=0.0), 4),
        "largest_loss": round(min((t["net_pnl"] for t in losses), default=0.0), 4),
        "expectancy": round(net / len(trades), 4) if trades else None,
        "avg_return_per_trade_pct": round(net / len(trades) / initial_capital * 100, 6) if trades and initial_capital else None,
        "return_on_initial_capital_pct": round(net / initial_capital * 100, 4) if initial_capital else None,
        "initial_capital": initial_capital,
        "final_balance": final_balance, "final_equity": final_equity,
        "avg_holding_seconds": round(statistics.fmean([t["holding_seconds"] for t in trades]), 2) if trades else None,
        "max_holding_seconds": max((t["holding_seconds"] for t in trades), default=None),
        "trades_per_day": round(len(trades) / span_days, 4) if trades else 0.0,
        "max_simultaneous_positions": max((e["open_positions"] for e in equity_curve), default=0),
        "max_drawdown_amount": dd["max_drawdown_amount"],
        "max_drawdown_pct": dd["max_drawdown_pct"],
        "avg_drawdown_amount": dd["avg_drawdown_amount"],
        "max_drawdown_duration_seconds": dd["max_duration_seconds"],
        "max_recovery_seconds": dd["max_recovery_seconds"],
        "current_drawdown_amount": dd["current_drawdown_amount"],
        "current_drawdown_pct": dd["current_drawdown_pct"],
        "drawdown_events": dd["events"],
        "recovery_factor": recovery,
        "sharpe_ratio": sharpe, "sortino_ratio": sortino,
        "sharpe_method": "daily equity returns, sqrt(252) annualization; null when <2 daily returns or no trades",
        "max_consecutive_wins": streaks["max_consecutive_wins"],
        "max_consecutive_losses": streaks["max_consecutive_losses"],
        "current_streak": streaks["current"],
        "streaks": streaks,
        "long_trades": sum(1 for t in trades if t["side"] == "LONG"),
        "short_trades": sum(1 for t in trades if t["side"] == "SHORT"),
        "long_net": round(sum(t["net_pnl"] for t in trades if t["side"] == "LONG"), 4),
        "short_net": round(sum(t["net_pnl"] for t in trades if t["side"] == "SHORT"), 4),
    }


def _daily_returns(equity_curve: List[dict]) -> List[float]:
    by_day: Dict[Any, float] = {}
    for e in equity_curve:
        day = datetime.fromtimestamp(e["ts"], tz=timezone.utc).date()
        by_day[day] = e["equity"]
    days = sorted(by_day)
    out = []
    for a, b in zip(days, days[1:]):
        if by_day[a]:
            out.append(by_day[b] / by_day[a] - 1.0)
    return out


def drawdown_analysis(equity_curve: List[dict]) -> Dict[str, Any]:
    if not equity_curve:
        return {"max_drawdown_amount": 0.0, "max_drawdown_pct": 0.0,
                "avg_drawdown_amount": 0.0, "max_duration_seconds": 0,
                "max_recovery_seconds": 0, "current_drawdown_amount": 0.0,
                "current_drawdown_pct": 0.0, "events": []}
    peak = equity_curve[0]["equity"]
    peak_ts = equity_curve[0]["ts"]
    events = []
    in_dd = False
    trough = peak
    trough_ts = peak_ts
    dd_marks = []
    for e in equity_curve:
        eq = e["equity"]
        if eq >= peak:
            if in_dd:
                events.append({"peak_ts": peak_ts, "trough_ts": trough_ts,
                               "recovery_ts": e["ts"], "amount": round(peak - trough, 4),
                               "pct": round((peak - trough) / peak * 100, 4) if peak else 0.0,
                               "duration_seconds": trough_ts - peak_ts,
                               "recovery_seconds": e["ts"] - trough_ts})
                in_dd = False
            peak, peak_ts = eq, e["ts"]
            trough, trough_ts = eq, e["ts"]
        else:
            in_dd = True
            if eq < trough:
                trough, trough_ts = eq, e["ts"]
            dd_marks.append(peak - eq)
    if in_dd:
        events.append({"peak_ts": peak_ts, "trough_ts": trough_ts, "recovery_ts": None,
                       "amount": round(peak - trough, 4),
                       "pct": round((peak - trough) / peak * 100, 4) if peak else 0.0,
                       "duration_seconds": trough_ts - peak_ts, "recovery_seconds": None})
    worst = max(events, key=lambda x: x["amount"], default=None)
    last = equity_curve[-1]
    return {
        "max_drawdown_amount": worst["amount"] if worst else 0.0,
        "max_drawdown_pct": worst["pct"] if worst else 0.0,
        "avg_drawdown_amount": round(statistics.fmean(dd_marks), 4) if dd_marks else 0.0,
        "max_duration_seconds": max((e["duration_seconds"] for e in events), default=0),
        "max_recovery_seconds": max((e["recovery_seconds"] or 0 for e in events), default=0),
        "current_drawdown_amount": round(peak - last["equity"], 4) if last["equity"] < peak else 0.0,
        "current_drawdown_pct": round((peak - last["equity"]) / peak * 100, 4) if peak and last["equity"] < peak else 0.0,
        "events": events[:50],
    }


def streak_analysis(trades: List[dict]) -> Dict[str, Any]:
    trades = _ordered(trades)
    runs: List[dict] = []
    current: Optional[dict] = None
    for t in trades:
        kind = "WIN" if t["net_pnl"] > 1e-9 else ("LOSS" if t["net_pnl"] < -1e-9 else "BREAKEVEN")
        if current and current["kind"] == kind:
            current["count"] += 1
            current["pnl"] += t["net_pnl"]
            current["end_ts"] = t["exit_ts"]
        else:
            if current:
                runs.append(current)
            current = {"kind": kind, "count": 1, "pnl": t["net_pnl"],
                       "start_ts": t["exit_ts"], "end_ts": t["exit_ts"]}
    if current:
        runs.append(current)
    win_runs = [r for r in runs if r["kind"] == "WIN"]
    loss_runs = [r for r in runs if r["kind"] == "LOSS"]
    total = len(trades) or 1
    return {
        "max_consecutive_wins": max((r["count"] for r in win_runs), default=0),
        "max_consecutive_losses": max((r["count"] for r in loss_runs), default=0),
        "avg_winning_streak": round(statistics.fmean([r["count"] for r in win_runs]), 3) if win_runs else None,
        "avg_losing_streak": round(statistics.fmean([r["count"] for r in loss_runs]), 3) if loss_runs else None,
        "pct_trades_in_win_streaks": round(sum(r["count"] for r in win_runs) / total * 100, 2),
        "pct_trades_in_loss_streaks": round(sum(r["count"] for r in loss_runs) / total * 100, 2),
        "current": ({"kind": runs[-1]["kind"], "count": runs[-1]["count"]} if runs else None),
        "runs": [{**r, "pnl": round(r["pnl"], 4)} for r in runs][-50:],
    }


def time_buckets(trades: List[dict]) -> Dict[str, List[dict]]:
    trades = _ordered(trades)

    def bucket(key_fn, label_fn):
        groups: Dict[str, List[dict]] = defaultdict(list)
        for t in trades:
            groups[label_fn(t)].append(t)
        out = []
        for label in sorted(groups, key=key_fn):
            g = groups[label]
            wins = sum(1 for t in g if t["net_pnl"] > 1e-9)
            out.append({"bucket": label, "trades": len(g),
                        "win_rate": round(wins / len(g), 4) if g else None,
                        "net_pnl": round(sum(t["net_pnl"] for t in g), 4),
                        "expectancy": round(sum(t["net_pnl"] for t in g) / len(g), 4) if g else None})
        return out

    def dt(t):
        return datetime.fromtimestamp(t["entry_ts"], tz=timezone.utc)

    return {
        "by_hour": bucket(lambda l: int(l.split(":")[0]), lambda t: f"{dt(t).hour:02d}:00"),
        "by_session": bucket(lambda l: l, lambda t: session_of(t["entry_ts"])),
        "by_weekday": bucket(lambda l: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].index(l),
                             lambda t: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][dt(t).weekday()]),
        "by_month": bucket(lambda l: l, lambda t: dt(t).strftime("%Y-%m")),
        "by_year": bucket(lambda l: l, lambda t: str(dt(t).year)),
    }


def apply_trade_filters(trades: List[dict], filters: Optional[dict]) -> List[dict]:
    """Trade-level filters with AND/OR over a condition list. Each condition:
    {field, op: eq|ne|gte|lte|in, value}. `logic` at top level: AND|OR."""
    if not filters or not filters.get("conditions"):
        return list(trades)
    logic = str(filters.get("logic", "AND")).upper()

    def ok(t: dict, cond: dict) -> bool:
        field, op, value = cond.get("field"), cond.get("op", "eq"), cond.get("value")
        actual = t.get(field)
        if field == "hour":
            actual = datetime.fromtimestamp(t["entry_ts"], tz=timezone.utc).hour
        if field == "weekday":
            actual = datetime.fromtimestamp(t["entry_ts"], tz=timezone.utc).weekday()
        if field == "month":
            actual = datetime.fromtimestamp(t["entry_ts"], tz=timezone.utc).month
        if actual is None:
            return False
        if op == "eq":
            return actual == value
        if op == "ne":
            return actual != value
        if op == "gte":
            return actual >= value
        if op == "lte":
            return actual <= value
        if op == "in":
            return actual in (value or [])
        return False

    out = []
    for t in trades:
        results = [ok(t, c) for c in filters["conditions"]]
        if (all(results) if logic == "AND" else any(results)):
            out.append(t)
    return out


def rank_runs(rows: List[dict], weights: Dict[str, float], min_trades: int = 0,
              max_dd_pct: Optional[float] = None) -> List[dict]:
    """Weighted normalized ranking. Raw profit alone never dominates:
    every criterion is min-max normalized across eligible rows and the
    weighted mean is reported with its components. Ineligible rows
    (too few trades / drawdown breach) are flagged, not silently dropped."""
    fields = {
        "net_profit": 1, "profit_factor": 1, "win_rate": 1, "expectancy": 1,
        "recovery_factor": 1, "sharpe_ratio": 1, "sortino_ratio": 1,
        "total_trades": 1, "max_drawdown_amount": -1, "max_drawdown_pct": -1,
        "max_consecutive_losses": -1,
    }
    eligible = []
    for r in rows:
        m = r.get("metrics") or {}
        reason = None
        if (m.get("total_trades") or 0) < min_trades:
            reason = f"fewer than {min_trades} trades"
        if max_dd_pct is not None and (m.get("max_drawdown_pct") or 0) > max_dd_pct:
            reason = "max drawdown % exceeds ranking limit"
        eligible.append({**r, "eligible": reason is None, "ineligible_reason": reason})
    pool = [r for r in eligible if r["eligible"]]
    for criterion, direction in fields.items():
        if criterion not in weights:
            continue
        vals = [(r.get("metrics") or {}).get(criterion) for r in pool]
        vals = [v for v in vals if v is not None]
        if not vals:
            continue
        lo, hi = min(vals), max(vals)
        for r in pool:
            v = (r.get("metrics") or {}).get(criterion)
            score = 0.5 if (v is None or hi == lo) else (v - lo) / (hi - lo)
            if direction < 0:
                score = 1 - score
            r.setdefault("score_components", {})[criterion] = round(score, 4)
    total_w = sum(weights.get(c, 0) for c in fields if c in weights) or 1.0
    for r in eligible:
        comps = r.get("score_components") or {}
        r["rank_score"] = round(sum(comps.get(c, 0.5) * weights.get(c, 0)
                                    for c in comps) / total_w, 4) if r["eligible"] else None
    eligible.sort(key=lambda r: (r["rank_score"] is None, -(r["rank_score"] or 0)))
    for idx, r in enumerate(eligible):
        r["rank"] = idx + 1 if r["eligible"] else None
    return eligible


__all__ = ["compute_metrics", "drawdown_analysis", "streak_analysis",
           "time_buckets", "apply_trade_filters", "rank_runs"]
