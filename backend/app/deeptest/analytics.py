"""V6.6 §11 — DeepTest analytics: the full metric set and time breakdowns,
computed ONLY from the scenario's own persisted trade records.

Accounting conventions (documented, also shipped in every scenario):
* Net P&L = sum of trade net P&L (gross P&L minus modelled costs: spread, slippage
  points, commission, swap when modelled).
* Win = net P&L > 0; Loss = net P&L <= 0. Win rate = wins / total trades.
* Expectancy per trade = mean(net P&L). Return % = net P&L / initial capital.
* Max drawdown % is computed on the equity curve of closed-trade cumulative P&L
  added to the initial capital (peak-to-trough of that realised curve).
* Sharpe = mean(period returns) / std(period returns) × sqrt(annualisation),
  where period returns are per-trade returns on initial capital; with < 2 trades
  Sharpe is N/A (None) — never 0 and never NaN/Inf.
* Consecutive wins/losses = longest run over the trade sequence.
* Undefined cases (zero denominators, empty sets, insufficient samples) return
  ``None`` and are listed in ``unavailable`` with a reason. No NaN/Inf ever
  leaves this module.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

DOW_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _f(v: Any) -> Optional[float]:
    try:
        x = float(v)
        return None if (math.isnan(x) or math.isinf(x)) else x
    except (TypeError, ValueError):
        return None


def _round(v: Optional[float], nd: int = 4) -> Optional[float]:
    return None if v is None else round(v, nd)


def _mean(xs: List[float]) -> Optional[float]:
    return (sum(xs) / len(xs)) if xs else None


def _std(xs: List[float]) -> Optional[float]:
    n = len(xs)
    if n < 2:
        return None
    mu = sum(xs) / n
    var = sum((x - mu) ** 2 for x in xs) / (n - 1)
    return math.sqrt(var) if var >= 0 else None


def eligible_trades(trades):
    """Unique finite closed observations; never collapse anonymous legacy rows."""
    unique=[];seen=set()
    for t in trades or []:
        if t.get('status') and str(t['status']).upper() not in ('CLOSED','COMPLETED'):continue
        if _f(t.get('net_pnl')) is None:continue
        identity=(t['run_id'],t['trade_no']) if t.get('run_id') is not None and t.get('trade_no') is not None else None
        if identity is not None and identity in seen:continue
        if identity is not None:seen.add(identity)
        unique.append(t)
    return unique


def summarize(trades: List[Dict[str, Any]], *, initial_capital: float) -> Dict[str, Any]:
    """The §11 summary metrics + breakdowns for one scenario's trades."""
    unavailable: List[Dict[str, str]] = []
    tds = eligible_trades(trades)
    nt = len(tds)
    cap = float(initial_capital or 0.0)

    def na(field: str, reason: str, out=None):
        unavailable.append({"field": field, "reason": reason})
        return out

    if cap <= 0:
        na("return_pct", "initial capital is not positive")

    pnls = [float(t["net_pnl"]) for t in tds]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_profit = sum(wins) if wins else 0.0
    gross_loss = -sum(losses) if losses else 0.0
    net = sum(pnls) if nt else 0.0

    costs = {"commission": 0.0, "spread_cost": 0.0, "slippage_cost": 0.0, "swap_cost": 0.0}
    for t in tds:
        for k in costs:
            v = _f(t.get(k))
            if v is not None:
                costs[k] += v

    # equity curve on realised trades
    eq = cap if cap > 0 else 0.0
    peak = eq
    max_dd = 0.0
    max_dd_pct = None
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        dd = peak - eq
        max_dd = max(max_dd, dd)
    if cap > 0:
        max_dd_pct = max_dd / cap

    pf = None
    if gross_loss > 0:
        pf = gross_profit / gross_loss
    elif gross_profit > 0:
        na("profit_factor", "no losing trades — profit factor is undefined (would be infinite)")
    else:
        na("profit_factor", "no trades")

    per_trade_rets = [p / cap for p in pnls] if cap > 0 else []
    sharpe = None
    if nt >= 2 and cap > 0:
        sd = _std(per_trade_rets)
        mu = _mean(per_trade_rets)
        if sd and sd > 1e-12 and mu is not None:
            sharpe = mu / sd * math.sqrt(252.0)     # per-trade returns, annualised ×√252
        else:
            na("sharpe", "zero dispersion in per-trade returns")
    else:
        na("sharpe", "fewer than two trades" if nt < 2 else "no capital base")

    # streaks
    max_win_run = max_loss_run = 0
    cur_w = cur_l = 0
    for p in pnls:
        if p > 0:
            cur_w += 1; cur_l = 0
        else:
            cur_l += 1; cur_w = 0
        max_win_run = max(max_win_run, cur_w)
        max_loss_run = max(max_loss_run, cur_l)

    longs = [t for t in tds if str(t.get("side") or "").lower() in ("buy", "long")]
    shorts = [t for t in tds if str(t.get("side") or "").lower() in ("sell", "short")]

    def side_block(sub: List[Dict[str, Any]]) -> Dict[str, Any]:
        ps = [float(t["net_pnl"]) for t in sub]
        w = [p for p in ps if p > 0]
        return {"trades": len(sub),
                "net_pnl": _round(sum(ps), 2) if ps else 0.0,
                "win_rate": _round(len(w) / len(ps), 4) if ps else None}

    def hold_stats(sub: List[Dict[str, Any]]) -> Dict[str, Any]:
        hs = [_f(t.get("hold_seconds")) for t in sub]
        hs = [h for h in hs if h is not None and h >= 0]
        if not hs:
            return {"avg_seconds": None, "max_seconds": None, "avg_minutes": None}
        return {"avg_seconds": _round(sum(hs) / len(hs), 1),
                "max_seconds": _round(max(hs), 1),
                "avg_minutes": _round(sum(hs) / len(hs) / 60.0, 2)}

    # day-of-week / session breakdowns
    by_dow: Dict[str, Dict[str, Any]] = {d: {"trades": 0, "net_pnl": 0.0, "wins": 0} for d in DOW_NAMES}
    by_session: Dict[str, Dict[str, Any]] = {}
    for t in tds:
        d = t.get("dow")
        name = DOW_NAMES[int(d)] if (d is not None and 0 <= int(d) <= 6) else None
        if name:
            b = by_dow[name]
            b["trades"] += 1
            b["net_pnl"] += float(t["net_pnl"])
            b["wins"] += (1 if float(t["net_pnl"]) > 0 else 0)
        s = str(t.get("session") or "unknown")
        sb = by_session.setdefault(s, {"trades": 0, "net_pnl": 0.0, "wins": 0})
        sb["trades"] += 1
        sb["net_pnl"] += float(t["net_pnl"])
        sb["wins"] += (1 if float(t["net_pnl"]) > 0 else 0)
    for block in list(by_dow.values()) + list(by_session.values()):
        block["net_pnl"] = _round(block["net_pnl"], 2)
        block["win_rate"] = _round(block["wins"] / block["trades"], 4) if block["trades"] else None

    summary = {
        "initial_capital": _round(cap, 2),
        "final_equity": _round(cap + net, 2) if cap > 0 else None,
        "net_profit": _round(net, 2),
        "gross_profit": _round(gross_profit, 2),
        "gross_loss": _round(gross_loss, 2),
        "total_costs": _round(sum(costs.values()), 2),
        "costs": {k: _round(v, 2) for k, v in costs.items()},
        "net_return_pct": _round(net / cap, 6) if cap > 0 else None,
        "max_drawdown": _round(max_dd, 2),
        "max_drawdown_pct": _round(max_dd_pct, 6) if max_dd_pct is not None else None,
        "sharpe": _round(sharpe, 3),
        "profit_factor": _round(pf, 3),
        "trades": nt,
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate": _round(len(wins) / nt, 4) if nt else na("win_rate", "no trades"),
        "avg_win": _round(sum(wins) / len(wins), 4) if wins else na("avg_win", "no winning trades"),
        "avg_loss": _round(sum(losses) / len(losses), 4) if losses else na("avg_loss", "no losing trades"),
        "largest_win": _round(max(wins), 2) if wins else None,
        "largest_loss": _round(min(losses), 2) if losses else None,
        "expectancy_per_trade": _round(net / nt, 4) if nt else na("expectancy_per_trade", "no trades"),
        "long": side_block(longs),
        "short": side_block(shorts),
        "holding": hold_stats(tds),
        "max_consecutive_wins": max_win_run,
        "max_consecutive_losses": max_loss_run,
    }

    from .loss_sequences import calculate
    # Metrics and sequences describe the SAME unique eligible retained rows.
    # calculate(raw) remains strict for callers checking an incomplete population.
    sequence = calculate(tds)
    summary.update(sequence)
    summary['max_consecutive_losses'] = sequence['max_consecutive_losing_trades']
    if sequence['loss_sequence_reason']:
        na('max_consecutive_losses', sequence['loss_sequence_reason'])
        na('max_consecutive_loss_currency', sequence['loss_sequence_reason'])
    return {
        "summary": summary,
        "breakdowns": {
            "by_day_of_week": by_dow,
            "by_session": by_session,
            "by_direction": {"long": side_block(longs), "short": side_block(shorts)},
        },
        "unavailable": unavailable,
        "conventions": {
            "net_pnl": "gross P&L minus modelled spread, slippage and commission costs",
            "win": "net P&L > 0",
            "expectancy_per_trade": "mean(net P&L) over all trades",
            "max_drawdown_pct": "peak-to-trough of the realised closed-trade equity curve over initial capital",
            "sharpe": "mean/std of per-trade returns on initial capital, annualised x sqrt(252); N/A below 2 trades",
            "profit_factor": "gross profit / gross loss; N/A when there are no losing trades",
            "sessions": "labelled by the stored bar timestamps (UTC clock hour bands); scenario "
                        "session filters are recorded in the scenario settings",
            "no_nan_policy": "every undefined metric is null with a reason in `unavailable`",
        },
    }


def reconcile(trades: List[Dict[str, Any]], summary: Dict[str, Any]) -> Dict[str, Any]:
    """§11 E: trade-level P&L must reconcile with the summary's net profit."""
    tds = [t for t in (trades or []) if _f(t.get("net_pnl")) is not None]
    total = sum(float(t["net_pnl"]) for t in tds)
    claimed = summary.get("net_profit")
    ok = claimed is not None and abs(total - float(claimed)) <= 0.02 * max(1, len(tds))
    return {"ok": bool(ok), "trade_level_sum": _round(total, 2),
            "summary_net_profit": claimed,
            "delta": _round(total - float(claimed), 4) if claimed is not None else None}
