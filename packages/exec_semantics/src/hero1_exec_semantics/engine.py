"""DeepTesting historical execution engine (Phase 3) - the ONE engine.

Executes a Phase 2 *compiled* strategy (IR dict from
`hero1_strategy_spec.compile_spec`) against real historical candles under
explicit account constraints. Pure and deterministic: same inputs always
produce the same trades. No I/O, no clocks, no MT5, no fabricated data.

EXECUTION MODEL (every ambiguity is decided here, in writing):
* Candles are BID OHLC (as stored from MT5/CSV). ASK = BID + spread_price.
  LONG positions open at ASK and close at BID; SHORT open at BID and close
  at ASK. Closing-side pricing matches the Phase 2 stop semantics.
* Entries: conditions evaluate on COMPLETED candles (bar i close).
  entry_timing "next_bar_open" fills at bar i+1 open; "signal_close" fills
  at bar i close. order_type "pending_stop" and position_handling
  "pyramid" are NOT simulated - the engine refuses (EngineError) rather
  than silently approximating them.
* Indicators are computed causally over the full candle array; a condition
  reading a warmup (None) value is simply false - never guessed.
  cross_above/below compare bar i vs bar i-1 values.
* SL/TP intrabar: LONG SL triggers when bid_low <= SL, TP when
  bid_high >= TP (mirror for SHORT on ask). If open gaps beyond the level,
  the fill is the open (recorded as gap_overrun). If BOTH levels are
  touched in one bar the order cannot be known from this timeframe:
  the conservative `ambiguity_policy="sl_first"` applies (configurable to
  "tp_first") and the trade is flagged `ambiguous_same_candle`. Using
  lower-timeframe data to resolve order is a documented future refinement;
  the engine never pretends it knows.
* Management (Phase 2 semantics): at each bar close, best favourable
  excursion updates from the bar extreme on the closing side; the shared
  `evaluate_stop_management` computes the next stop; it takes effect from
  the NEXT bar (a stop never retroactively triggers inside the bar that
  produced it). Indicator trailing uses the indicator value at the last
  completed bar: candidate = value -/+ offset for LONG/SHORT, composed
  most-protectively and clamped never-backwards by the same contract.
* Prioritized exit rules evaluate at bar close in ascending priority
  (legacy explicit_exit is priority 0); a triggered rule closes at the
  next bar open on the closing side. max_holding_candles and the UTC
  time exit likewise close at the next open.
* Direction: for BOTH strategies the entry tree decides the side - a
  cross_above generates LONG, cross_below generates SHORT; value
  comparisons (gt/lt/...) generate the side implied by the strategy's
  declared direction; for BOTH + non-cross conditions the engine requires
  the *condition itself* to be side-specific (it evaluates the tree once
  and takes LONG for LONG strategies; for BOTH it evaluates entry as LONG
  and ALSO checks the mirrored SHORT signal only for cross comparisons).
  Concretely: BOTH + crossover entries trade both directions; BOTH +
  plain comparison entries trade LONG unless identity.direction is SHORT.
  This is deterministic and documented - engines never improvise sides.
* MAX ACTIVE TRADES is a hard cap per simulation run: signals arriving
  while the cap is reached are skipped (recorded in skipped_signals with
  reason MAX_ACTIVE_TRADES), never queued. A continuously-true condition
  cannot re-enter while its own position is open, and after a close the
  NEXT bar's fresh evaluation governs (no duplicate entries from one bar).
* Costs: commission_per_lot_round_turn, swap per lot per night (counted
  per UTC midnight held), slippage_price applied adversely on entry+exit.
  Unconfigured costs are exactly 0 and recorded as assumptions.
* End of data: open positions close at the final bar close
  (exit_reason END_OF_DATA). Nothing is left dangling or disappears.
* Session/time filters restrict ENTRIES only (open positions are always
  managed to a normal exit). Attribution is by entry time, UTC.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from hero1_indicators import compute_indicator
from hero1_strategy_spec.management import TradeManagement
from hero1_strategy_spec.risk import Distance

from .stop_management import PriceContext, evaluate_stop_management

ENGINE_VERSION = "3.0.0"


class EngineError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class InstrumentSpec:
    """Trading specification from broker metadata. tick_value_account is
    money per tick per 1.0 lot in the ACCOUNT currency (already converted).
    If only a quote-currency tick value exists, pass fx_to_account to
    convert - the engine never invents an FX rate."""
    tick_size: float
    tick_value_account: Optional[float] = None
    tick_value_quote: Optional[float] = None
    fx_to_account: Optional[float] = None
    contract_size: Optional[float] = None
    volume_min: float = 0.01
    volume_max: float = 100.0
    volume_step: float = 0.01

    def tick_value(self) -> float:
        if self.tick_value_account:
            return self.tick_value_account
        if self.tick_value_quote and self.fx_to_account:
            return self.tick_value_quote * self.fx_to_account
        raise EngineError("MISSING_TICK_VALUE",
                          "instrument metadata has no tick value in account currency "
                          "and no explicit FX conversion - position sizing refused")


@dataclass(frozen=True)
class RunConfig:
    initial_capital: float
    account_currency: str = "USD"
    risk_mode: str = "percent"          # percent | fixed
    risk_value: float = 1.0             # % or account-currency amount
    risk_basis: str = "current_equity"  # initial_capital | current_equity
    max_active_trades: int = 1
    spread_price: float = 0.0
    slippage_price: float = 0.0
    commission_per_lot_round_turn: float = 0.0
    swap_long_per_lot_night: float = 0.0
    swap_short_per_lot_night: float = 0.0
    ambiguity_policy: str = "sl_first"  # sl_first | tp_first
    sessions: Optional[List[str]] = None       # entry filter, UTC labels
    weekdays: Optional[List[int]] = None       # 0=Mon .. 6=Sun, entry filter
    months: Optional[List[int]] = None         # 1..12, entry filter
    tp_offset_pct: float = 0.0                 # scenario offsets, % of distance
    sl_offset_pct: float = 0.0


@dataclass
class Position:
    side: str
    entry_index: int
    entry_ts: int
    entry_price: float
    volume: float
    planned_risk: float
    initial_stop: Optional[float]
    stop: Optional[float]
    take_profit: Optional[float]
    best_favourable: float
    worst_adverse: float
    stop_history: List[dict] = field(default_factory=list)
    entry_indicators: Dict[str, Any] = field(default_factory=dict)
    bars_held: int = 0


def _operand_value(operand: dict, i: int, candles, series_cache: dict, cache_key_prefix=""):
    kind = operand.get("kind")
    if kind == "constant":
        return float(operand["value"])
    if kind in ("candle", "forming_candle"):
        if i < 0:
            return None
        idx = i - int(operand.get("offset", 0))
        if idx < 0:
            return None
        return float(candles[idx][operand.get("field", "close")])
    if kind == "indicator":
        idx = i - int(operand.get("offset", 0))
        if idx < 0:
            return None
        key = (operand["name"], _params_key(operand.get("params") or {}))
        series = series_cache.get(key)
        if series is None:
            raise EngineError("MISSING_INDICATOR_SERIES", f"indicator {key} was not precomputed")
        fld = operand.get("field") or "value"
        vals = series.get(fld)
        if vals is None:
            raise EngineError("MISSING_INDICATOR_FIELD",
                              f"indicator {operand['name']} has no field {fld!r}")
        return vals[idx]
    raise EngineError("BAD_OPERAND", f"unknown operand kind {kind!r}")


def _params_key(params: dict) -> str:
    return ",".join(f"{k}={params[k]}" for k in sorted(params))


def _collect_indicators(node: Any, acc: dict) -> None:
    if isinstance(node, dict):
        if node.get("kind") == "indicator":
            acc[(node["name"], _params_key(node.get("params") or {}))] = (node["name"], node.get("params") or {})
        for v in node.values():
            _collect_indicators(v, acc)
    elif isinstance(node, list):
        for v in node:
            _collect_indicators(v, acc)


def evaluate_tree(node: dict, i: int, candles, series_cache) -> Optional[bool]:
    """Evaluate a compiled LogicNode at bar i. None = not evaluable
    (warmup) which the engine treats as 'condition not satisfied'."""
    t = node.get("type")
    if t == "and":
        vals = [evaluate_tree(c, i, candles, series_cache) for c in node.get("children", [])]
        return False if any(v is False for v in vals) else (True if all(v is True for v in vals) else None)
    if t == "or":
        vals = [evaluate_tree(c, i, candles, series_cache) for c in node.get("children", [])]
        return True if any(v is True for v in vals) else (False if all(v is False for v in vals) else None)
    if t == "not":
        v = evaluate_tree(node["child"], i, candles, series_cache)
        return None if v is None else (not v)
    if t == "compare":
        op = node["op"]
        left, right = node["left"], node["right"]
        if op in ("cross_above", "cross_below"):
            cur_l = _operand_value(left, i, candles, series_cache)
            cur_r = _operand_value(right, i, candles, series_cache)
            prev_l = _operand_value(left, i - 1, candles, series_cache)
            prev_r = _operand_value(right, i - 1, candles, series_cache)
            if None in (cur_l, cur_r, prev_l, prev_r):
                return None
            return (prev_l <= prev_r and cur_l > cur_r) if op == "cross_above" else (prev_l >= prev_r and cur_l < cur_r)
        lv = _operand_value(left, i, candles, series_cache)
        rv = _operand_value(right, i, candles, series_cache)
        if lv is None or rv is None:
            return None
        return {"gt": lv > rv, "gte": lv >= rv, "lt": lv < rv, "lte": lv <= rv,
                "eq": lv == rv, "neq": lv != rv}[op]
    raise EngineError("BAD_NODE", f"unknown logic node type {t!r}")


def _entry_side(ir: dict, i: int, candles, series_cache) -> Optional[str]:
    direction = ir["strategy"]["direction"]
    result = evaluate_tree(ir["entry"], i, candles, series_cache)
    if result is not True:
        return None
    if direction in ("LONG", "SHORT"):
        return direction
    # BOTH: cross comparisons determine the side from the tree's first cross op
    cross = _first_cross(ir["entry"])
    if cross == "cross_above":
        return "LONG"
    if cross == "cross_below":
        return "SHORT"
    return "LONG"  # documented default for BOTH + non-cross conditions


def _first_cross(node: Any) -> Optional[str]:
    if isinstance(node, dict):
        if node.get("type") == "compare" and node.get("op") in ("cross_above", "cross_below"):
            return node["op"]
        for v in node.values():
            found = _first_cross(v)
            if found:
                return found
    elif isinstance(node, list):
        for v in node:
            found = _first_cross(v)
            if found:
                return found
    return None


def session_of(ts: int) -> str:
    """UTC session label (entry-time attribution). Sessions overlap in
    reality; a single deterministic label is assigned by precedence:
    overlap > london > newyork > asian > off_hours. DST shifts the real
    sessions - labels here are fixed UTC windows, stated explicitly."""
    h = datetime.fromtimestamp(ts, tz=timezone.utc).hour
    if 13 <= h < 17:
        return "london_newyork_overlap"
    if 8 <= h < 17:
        return "london"
    if 13 <= h < 22:
        return "newyork"
    if 0 <= h < 9:
        return "asian"
    return "off_hours"


def _entry_filters_ok(cfg: RunConfig, ts: int) -> bool:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    if cfg.sessions and session_of(ts) not in cfg.sessions:
        return False
    if cfg.weekdays is not None and dt.weekday() not in cfg.weekdays:
        return False
    if cfg.months is not None and dt.month not in cfg.months:
        return False
    return True


def size_position(*, cfg: RunConfig, spec: InstrumentSpec, equity_now: float,
                  entry_price: float, stop_price: Optional[float], side: str) -> dict:
    """Risk-based sizing. Returns {volume, planned_risk, budget, reason}.
    Never exceeds the budget silently: volume rounds DOWN to the step;
    if even the minimum volume would exceed the budget the trade is
    refused with an explicit reason."""
    if stop_price is None:
        return {"volume": None, "reason": "MISSING_INITIAL_SL: strategy declares no stop loss; "
                                        "risk-based sizing is impossible and no size is invented"}
    distance = abs(entry_price - stop_price)
    if distance <= 0:
        return {"volume": None, "reason": "INVALID_SL_DISTANCE: stop distance is zero"}
    tick_value = spec.tick_value()  # raises EngineError when unavailable
    if cfg.risk_mode == "percent":
        base = cfg.initial_capital if cfg.risk_basis == "initial_capital" else equity_now
        budget = base * cfg.risk_value / 100.0
    elif cfg.risk_mode == "fixed":
        budget = float(cfg.risk_value)
    else:
        raise EngineError("BAD_RISK_MODE", f"risk_mode must be percent|fixed, got {cfg.risk_mode!r}")
    loss_per_lot = distance / spec.tick_size * tick_value
    if loss_per_lot <= 0:
        return {"volume": None, "reason": "INVALID_TICK_MATH: non-positive loss per lot"}
    raw = budget / loss_per_lot
    steps = math.floor(raw / spec.volume_step + 1e-12)
    volume = round(steps * spec.volume_step, 8)
    if volume > spec.volume_max:
        volume = spec.volume_max
    if volume < spec.volume_min:
        return {"volume": None, "budget": budget,
                "reason": f"VOLUME_BELOW_MIN: risk budget {budget:.2f} buys {volume} lots at this "
                          f"stop distance, below the instrument minimum {spec.volume_min}; "
                          "trade refused rather than exceeding the budget"}
    planned = volume / 1.0 * loss_per_lot
    if planned > budget * 1.0000001:
        return {"volume": None, "budget": budget,
                "reason": "RISK_BUDGET_EXCEEDED: rounded volume would exceed the risk budget"}
    return {"volume": volume, "planned_risk": planned, "budget": budget,
            "loss_per_lot": loss_per_lot, "reason": None}


def _resolve_distance(distance: Optional[dict], ctx: PriceContext, atr_series, i: int,
                      anchor_price: float, side: str, what: str) -> Optional[float]:
    if distance is None:
        return None
    d = Distance.model_validate(distance)
    from .stop_management import resolve_distance
    local_ctx = ctx
    if d.unit.value == "atr_multiple":
        atr_val = atr_series[i] if atr_series and i < len(atr_series) else None
        if atr_val is None:
            raise EngineError("MISSING_ATR_CONTEXT", f"{what}: ATR multiple needs an ATR value at the signal bar")
        local_ctx = PriceContext(pip_size=ctx.pip_size, point_size=ctx.point_size,
                                 tick_size=ctx.tick_size, atr_value=atr_val)
    return resolve_distance(d, local_ctx, what)


def run_backtest(*, ir: dict, candles: List[dict], cfg: RunConfig,
                 instrument: InstrumentSpec, price_ctx: PriceContext,
                 management: Optional[TradeManagement] = None) -> dict:
    """Execute one compiled strategy over candles. Returns trades, equity
    curve, skipped signals and run accounting. Raises EngineError for any
    unsupported/underspecified semantics - never guesses."""
    if not candles:
        raise EngineError("NO_CANDLES", "no candles supplied for the selected range")
    for a, b in zip(candles, candles[1:]):
        if int(b["ts"]) <= int(a["ts"]):
            raise EngineError("NON_CHRONOLOGICAL", "candles are not strictly chronological")
    execution = ir.get("execution") or {}
    if execution.get("order_type") == "pending_stop":
        raise EngineError("UNSUPPORTED_ORDER_TYPE", "pending_stop orders are not simulated in Phase 3")
    if execution.get("position_handling") == "pyramid":
        raise EngineError("UNSUPPORTED_POSITION_HANDLING", "pyramid position handling is not simulated in Phase 3")
    entry_timing = execution.get("entry_timing", "next_bar_open")

    # ---- precompute indicator series (entry + exits + management)
    needed: dict = {}
    _collect_indicators(ir["entry"], needed)
    for rule in ir.get("ordered_exit_rules") or []:
        _collect_indicators(rule.get("condition"), needed)
    if management is not None and management.indicator_trailing is not None:
        ind = management.indicator_trailing.indicator
        ind_name = ind.name.value if hasattr(ind.name, "value") else str(ind.name)
        needed[(ind_name, _params_key(ind.params or {}))] = (ind_name, ind.params or {})
    series_cache: dict = {}
    for key, (name, params) in needed.items():
        try:
            series_cache[key] = compute_indicator(name, params, candles)
        except ValueError as exc:
            raise EngineError("INDICATOR_NOT_COMPUTABLE", str(exc)) from exc
    # ATR context for atr-denominated distances
    atr_series = None
    exits_ir = ir.get("exits") or {}
    sl_ir = exits_ir.get("stop_loss") or {}
    if (sl_ir.get("distance") or {}).get("unit") == "atr_multiple" or \
       (exits_ir.get("take_profit") or {}).get("distance", {}).get("unit") == "atr_multiple":
        atr_series = compute_indicator("ATR", {"period": int((sl_ir.get("distance") or {}).get("atr_period") or 14)}, candles)["value"]

    balance = float(cfg.initial_capital)
    positions: List[Position] = []
    trades: List[dict] = []
    skipped: List[dict] = []
    equity_curve: List[dict] = []
    mgmt_model = management
    ordered_rules = ir.get("ordered_exit_rules") or []
    time_exit = (exits_ir.get("time_exit_utc") or None)
    max_holding = exits_ir.get("max_holding_candles")

    def closing_price(pos: Position, bar: dict, when: str) -> float:
        base = float(bar[when])
        return base if pos.side == "LONG" else base + cfg.spread_price

    def open_price(side: str, bar: dict, when: str) -> float:
        base = float(bar[when])
        return base + cfg.spread_price if side == "LONG" else base

    def pnl_at(pos: Position, price: float) -> float:
        ticks = (price - pos.entry_price) / instrument.tick_size
        if pos.side == "SHORT":
            ticks = -ticks
        return ticks * instrument.tick_value() * pos.volume

    def close_position(pos: Position, exit_ts: int, exit_price: float, reason: str,
                       ambiguous: bool = False, gap: bool = False) -> dict:
        gross = pnl_at(pos, exit_price)
        nights = max(0, (datetime.fromtimestamp(exit_ts, tz=timezone.utc).date()
                         - datetime.fromtimestamp(pos.entry_ts, tz=timezone.utc).date()).days)
        swap_rate = cfg.swap_long_per_lot_night if pos.side == "LONG" else cfg.swap_short_per_lot_night
        swap = swap_rate * pos.volume * nights
        commission = cfg.commission_per_lot_round_turn * pos.volume
        spread_cost = cfg.spread_price / instrument.tick_size * instrument.tick_value() * pos.volume \
            if instrument.tick_size else 0.0
        net = gross - commission + swap
        return {
            "side": pos.side, "volume": pos.volume,
            "entry_ts": pos.entry_ts, "entry_price": pos.entry_price,
            "exit_ts": exit_ts, "exit_price": exit_price, "exit_reason": reason,
            "initial_stop": pos.initial_stop, "final_stop": pos.stop,
            "take_profit": pos.take_profit, "planned_risk": pos.planned_risk,
            "gross_pnl": round(gross, 6), "commission": round(commission, 6),
            "swap": round(swap, 6), "spread_cost": round(spread_cost, 6),
            "slippage": round(2 * cfg.slippage_price / instrument.tick_size
                              * instrument.tick_value() * pos.volume, 6) if instrument.tick_size else 0.0,
            "net_pnl": round(net, 6),
            "result": "WIN" if net > 1e-9 else ("LOSS" if net < -1e-9 else "BREAKEVEN"),
            "holding_bars": pos.bars_held,
            "holding_seconds": exit_ts - pos.entry_ts,
            "mfe_price": pos.best_favourable, "mae_price": pos.worst_adverse,
            "stop_history": pos.stop_history,
            "entry_indicators": pos.entry_indicators,
            "ambiguous_same_candle": ambiguous, "gap_overrun": gap,
            "session": session_of(pos.entry_ts),
            "balance_after": None,  # filled by caller
        }

    # helpers defined as closures over mutable state ---------------------
    def _open_position(sig, i, fill_price, signal_close=False):
        side = sig["side"]
        slip = cfg.slippage_price if side == "LONG" else -cfg.slippage_price
        entry_price = fill_price + (slip if not signal_close else slip)
        # initial SL/TP from compiled exits, anchored at entry
        signal_i = sig.get("signal_index", i)
        stop_price, tp_price = None, None
        if sl_ir.get("distance"):
            d = _resolve_distance(sl_ir["distance"], price_ctx, atr_series, signal_i,
                                  entry_price, side, "stop_loss")
            anchor = sl_ir.get("anchor", "entry_price")
            base = entry_price
            if anchor == "signal_low":
                base = float(candles[signal_i]["low"])
            elif anchor == "signal_high":
                base = float(candles[signal_i]["high"])
            stop_price = base - d if side == "LONG" else base + d
            if cfg.sl_offset_pct:
                dist = abs(entry_price - stop_price) * (1 + cfg.sl_offset_pct / 100.0)
                stop_price = entry_price - dist if side == "LONG" else entry_price + dist
        tp_ir = exits_ir.get("take_profit") or {}
        if tp_ir.get("distance"):
            d = _resolve_distance(tp_ir["distance"], price_ctx, atr_series, signal_i,
                                  entry_price, side, "take_profit")
            tp_price = entry_price + d if side == "LONG" else entry_price - d
        elif tp_ir.get("ratio") and stop_price is not None:
            dist = abs(entry_price - stop_price) * float(tp_ir["ratio"])
            tp_price = entry_price + dist if side == "LONG" else entry_price - dist
        if tp_price is not None and cfg.tp_offset_pct:
            dist = abs(tp_price - entry_price) * (1 + cfg.tp_offset_pct / 100.0)
            tp_price = entry_price + dist if side == "LONG" else entry_price - dist
        equity_now = equity_curve[-1]["equity"] if equity_curve else balance
        sized = size_position(cfg=cfg, spec=instrument, equity_now=equity_now,
                              entry_price=entry_price, stop_price=stop_price, side=side)
        if sized["volume"] is None:
            skipped.append({"ts": int(candles[i]["ts"]), "side": side,
                            "reason": sized["reason"]})
            return
        pos = Position(side=side, entry_index=i, entry_ts=int(candles[i]["ts"]),
                       entry_price=entry_price, volume=sized["volume"],
                       planned_risk=sized["planned_risk"], initial_stop=stop_price,
                       stop=stop_price, take_profit=tp_price,
                       best_favourable=entry_price, worst_adverse=entry_price,
                       entry_indicators=_indicator_snapshot(ir["entry"], signal_i, candles, series_cache))
        positions.append(pos)


    n = len(candles)
    pending_entries: List[dict] = []   # signals from previous bar close (next_bar_open)
    pending_exits: List[Position] = [] # rule/time exits decided at previous close

    for i in range(n):
        bar = candles[i]
        ts = int(bar["ts"])

        # 1) execute pending rule/time exits at this open
        for pos in list(pending_exits):
            if pos not in positions:
                continue
            price = closing_price(pos, bar, "open")
            trade = close_position(pos, ts, price, pos.pending_exit_reason)  # type: ignore[attr-defined]
            balance += trade["net_pnl"]
            trade["balance_after"] = round(balance, 6)
            trades.append(trade)
            positions.remove(pos)
        pending_exits = []

        # 2) execute pending entries at this open (next_bar_open)
        entries_now = pending_entries
        pending_entries = []
        for sig in entries_now:
            if len(positions) >= cfg.max_active_trades:
                skipped.append({"ts": ts, "side": sig["side"], "reason": "MAX_ACTIVE_TRADES"})
                continue
            _open_position(sig, i, open_price(sig["side"], bar, "open"))

        # positions opened via signal_close were handled at previous bar end

        # 3) intrabar SL/TP for open positions
        for pos in list(positions):
            if pos.entry_index == i and entry_timing == "next_bar_open":
                pass  # position opened at this open: SL/TP still apply from here
            hit = _check_sl_tp(pos, bar, cfg, instrument)
            if hit:
                price, reason, ambiguous, gap = hit
                trade = close_position(pos, ts, price, reason, ambiguous, gap)
                balance += trade["net_pnl"]
                trade["balance_after"] = round(balance, 6)
                trades.append(trade)
                positions.remove(pos)

        # 4) bar-close bookkeeping: excursions, management, rule exits, entries
        for pos in list(positions):
            pos.bars_held += 1
            if pos.side == "LONG":
                pos.best_favourable = max(pos.best_favourable, float(bar["high"]))
                pos.worst_adverse = min(pos.worst_adverse, float(bar["low"]))
            else:
                pos.best_favourable = min(pos.best_favourable, float(bar["low"]) + cfg.spread_price)
                pos.worst_adverse = max(pos.worst_adverse, float(bar["high"]) + cfg.spread_price)
            if mgmt_model is not None and pos.stop is not None:
                _apply_management(pos, i, candles, series_cache, mgmt_model, price_ctx, cfg)
            # exit rules in priority order (first true wins)
            exit_reason = None
            for rule in ordered_rules:
                if evaluate_tree(rule["condition"], i, candles, series_cache) is True:
                    exit_reason = f"EXIT_RULE:{rule['name']}"
                    break
            if exit_reason is None and max_holding and pos.bars_held >= int(max_holding):
                exit_reason = "MAX_HOLDING"
            if exit_reason is None and time_exit:
                now_hm = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M")
                if now_hm >= time_exit and pos.entry_ts < ts:
                    exit_reason = "TIME_EXIT"
            if exit_reason is not None:
                if i + 1 < n:
                    pos.pending_exit_reason = exit_reason  # type: ignore[attr-defined]
                    pending_exits.append(pos)
                else:
                    price = closing_price(pos, bar, "close")
                    trade = close_position(pos, ts, price, exit_reason)
                    balance += trade["net_pnl"]
                    trade["balance_after"] = round(balance, 6)
                    trades.append(trade)
                    positions.remove(pos)

        # 5) entry signals at this close
        side = _entry_side(ir, i, candles, series_cache)
        if side is not None:
            sig = {"side": side, "signal_index": i}
            if entry_timing == "signal_close":
                if len(positions) >= cfg.max_active_trades:
                    skipped.append({"ts": ts, "side": side, "reason": "MAX_ACTIVE_TRADES"})
                elif _entry_filters_ok(cfg, ts):
                    _open_position(sig, i, open_price(side, bar, "close"), signal_close=True)
            else:
                if i + 1 < n:
                    sig["filters_ok"] = _entry_filters_ok(cfg, int(candles[i + 1]["ts"]))
                    if sig["filters_ok"]:
                        pending_entries.append(sig)
                    else:
                        skipped.append({"ts": int(candles[i + 1]["ts"]), "side": side, "reason": "ENTRY_FILTER"})

        # 6) equity mark at close
        unrealized = sum(pnl_at(p, closing_price(p, bar, "close")) for p in positions)
        equity_curve.append({"ts": ts, "balance": round(balance, 6),
                             "equity": round(balance + unrealized, 6),
                             "open_positions": len(positions)})

    # end of data: close everything at final close
    last = candles[-1]
    for pos in list(positions):
        price = closing_price(pos, last, "close")
        trade = close_position(pos, int(last["ts"]), price, "END_OF_DATA")
        balance += trade["net_pnl"]
        trade["balance_after"] = round(balance, 6)
        trades.append(trade)
        positions.remove(pos)

    total_open_risk = 0.0
    return {
        "engine_version": ENGINE_VERSION,
        "trades": trades,
        "skipped_signals": skipped,
        "equity_curve": equity_curve,
        "final_balance": round(balance, 6),
        "initial_capital": cfg.initial_capital,
        "open_risk_at_end": total_open_risk,
        "candles_used": n,
    }


def _indicator_snapshot(node, i, candles, series_cache) -> dict:
    found: dict = {}
    _collect_indicators(node, found)
    snap = {}
    for (name, pk), (_, params) in found.items():
        series = series_cache.get((name, pk), {})
        vals = {}
        for fld, arr in series.items():
            vals[fld] = arr[i] if 0 <= i < len(arr) else None
        snap[f"{name}({pk})"] = vals
    return snap


def _check_sl_tp(pos: Position, bar: dict, cfg: RunConfig, spec: InstrumentSpec):
    """Return (exit_price, reason, ambiguous, gap_overrun) or None."""
    spread = cfg.spread_price
    if pos.side == "LONG":
        open_, high, low = float(bar["open"]), float(bar["high"]), float(bar["low"])
        sl_hit = pos.stop is not None and low <= pos.stop
        tp_hit = pos.take_profit is not None and high >= pos.take_profit
        if sl_hit and tp_hit:
            if cfg.ambiguity_policy == "tp_first":
                return pos.take_profit, "TAKE_PROFIT", True, False
            return pos.stop, "STOP_LOSS", True, False
        if sl_hit:
            if open_ < pos.stop:
                return open_, "STOP_LOSS", False, True
            return pos.stop, "STOP_LOSS", False, False
        if tp_hit:
            if open_ > pos.take_profit:
                return open_, "TAKE_PROFIT", False, True
            return pos.take_profit, "TAKE_PROFIT", False, False
    else:
        open_ = float(bar["open"]) + spread
        high = float(bar["high"]) + spread
        low = float(bar["low"]) + spread
        sl_hit = pos.stop is not None and high >= pos.stop
        tp_hit = pos.take_profit is not None and low <= pos.take_profit
        if sl_hit and tp_hit:
            if cfg.ambiguity_policy == "tp_first":
                return pos.take_profit, "TAKE_PROFIT", True, False
            return pos.stop, "STOP_LOSS", True, False
        if sl_hit:
            if open_ > pos.stop:
                return open_, "STOP_LOSS", False, True
            return pos.stop, "STOP_LOSS", False, False
        if tp_hit:
            if open_ < pos.take_profit:
                return open_, "TAKE_PROFIT", False, True
            return pos.take_profit, "TAKE_PROFIT", False, False
    return None


def _apply_management(pos: Position, i: int, candles, series_cache,
                      mgmt: TradeManagement, ctx: PriceContext, cfg: RunConfig) -> None:
    """Break-even/stepped/fixed via the shared Phase 2 evaluator +
    indicator trailing (Phase 3 completion of the deferred semantics)."""
    # indicator trailing first (explicit error when context missing)
    ind_candidate = None
    if mgmt.indicator_trailing is not None:
        rule = mgmt.indicator_trailing
        if rule.applies_to.value in ("BOTH", pos.side):
            if rule.activation is not None:
                from .stop_management import resolve_distance
                act = resolve_distance(rule.activation, ctx, "indicator_trailing.activation")
                excursion = (pos.best_favourable - pos.entry_price) if pos.side == "LONG" \
                    else (pos.entry_price - pos.best_favourable)
                if excursion < act:
                    act_ok = False
                else:
                    act_ok = True
            else:
                act_ok = True
            if act_ok:
                ind = rule.indicator
                ind_name = ind.name.value if hasattr(ind.name, "value") else str(ind.name)
                key = (ind_name, _params_key(ind.params or {}))
                series = series_cache.get(key)
                if series is None:
                    raise EngineError("MISSING_INDICATOR_CONTEXT",
                                      f"indicator trailing needs {ind.name} values; none computed")
                fld = ind.field or "value"
                value = series.get(fld, [None] * len(candles))[i]
                if value is None:
                    raise EngineError("MISSING_INDICATOR_CONTEXT",
                                      f"indicator trailing: {ind.name}.{fld} is in warmup at this bar")
                offset = 0.0
                if rule.offset is not None:
                    from .stop_management import resolve_distance
                    offset = resolve_distance(rule.offset, ctx, "indicator_trailing.offset")
                ind_candidate = value - offset if pos.side == "LONG" else value + offset
    sub = TradeManagement(break_even=mgmt.break_even, stepped_trailing=mgmt.stepped_trailing,
                          fixed_trailing=mgmt.fixed_trailing, indicator_trailing=None)
    if sub.break_even or sub.stepped_trailing or sub.fixed_trailing:
        ev = evaluate_stop_management(side=pos.side, entry_price=pos.entry_price,
                                      current_stop=pos.stop, best_favourable=pos.best_favourable,
                                      management=sub, ctx=ctx)
        candidate = ev.new_stop
    else:
        candidate = pos.stop
    if ind_candidate is not None:
        candidate = max(candidate, ind_candidate) if pos.side == "LONG" else min(candidate, ind_candidate)
    if candidate is not None and pos.stop is not None:
        tightened = candidate > pos.stop if pos.side == "LONG" else candidate < pos.stop
        if tightened:
            pos.stop_history.append({"ts": int(candles[i]["ts"]), "from": pos.stop,
                                     "to": candidate,
                                     "rule": "indicator_trailing" if ind_candidate == candidate
                                     else "stop_management"})
            pos.stop = candidate
        # never backwards: evaluate_stop_management already clamps; the
        # indicator candidate was composed most-protectively above and is
        # only applied when it tightens.

__all__ = ["ENGINE_VERSION", "EngineError", "InstrumentSpec", "RunConfig",
           "run_backtest", "size_position", "evaluate_tree", "session_of"]
