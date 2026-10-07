"""
Fast local backtesting engine.

Design:
  * Conditions are evaluated VECTORIZED over precomputed cached feature
    arrays (a strategy never recomputes indicators itself).
  * Trade simulation is an event loop over entry signals only (not every
    bar), with intra-bar SL/TP/trailing resolution.
  * Deterministic & reproducible: slippage is seeded by
    (genome hash, stage, window, stress params) — same inputs => same trades.
  * Models: bid/ask spread, commission, swap, slippage, execution delay
    (fill on next bar open), min/max hold, trailing stops, exit conditions,
    session/day/regime filters, risk-based position sizing, sub-windows
    (train/test/walk-forward), stress multipliers.
"""
from __future__ import annotations

import copy
import hashlib
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..config import get_config
from ..data.engine import get_data_engine
from ..features.engine import get_feature_engine
from ..genome.schema import genome_hash, referenced_features
from .execution import (ExecutionParams, SlippageModel, entry_fill_price,
                        exit_fill_price, lots_for_risk, commission_cost, swap_cost)

log = logging.getLogger("backtest.engine")

CMP_OPS = {">": np.greater, "<": np.less, ">=": np.greater_equal,
           "<=": np.less_equal, "==": np.equal}

REGIME_NAMES = ["trending", "ranging", "breakout", "high_volatility",
                "low_volatility", "expansion", "compression", "momentum"]
REGIME_FEATURES = [f"regime:{r}" for r in REGIME_NAMES]


@dataclass
class BacktestRequest:
    genome: Dict[str, Any]
    dataset_id: str
    stage: str = "detail"                    # screen | detail | oos | wf | stress | mc
    window: Optional[Tuple[int, int]] = None  # row slice of the dataset
    spread_mult: float = 1.0
    slippage_mult: float = 1.0
    commission_mult: float = 1.0
    direction_override: Optional[str] = None
    perturb: float = 0.0                     # threshold perturbation strength
    seed_salt: str = ""
    max_trades: int = 3000
    mc_skip_prob: float = 0.0                # monte-carlo: randomly skip entries


@dataclass
class Trade:
    side: str
    entry_idx: int
    entry_ts: float
    entry_price: float
    lots: float
    sl: float
    tp: float
    exit_idx: int = -1
    exit_ts: float = 0.0
    exit_price: float = 0.0
    exit_reason: str = ""
    pnl: float = 0.0
    spread_paid: float = 0.0
    slippage_points: float = 0.0
    session: str = ""
    dow: int = -1
    regime: str = ""


@dataclass
class BacktestResult:
    ok: bool
    metrics: Dict[str, Any] = field(default_factory=dict)
    trades: List[Dict[str, Any]] = field(default_factory=list)
    equity_curve: List[List[float]] = field(default_factory=list)
    error: str = ""
    runtime_ms: float = 0.0


class _Ctx:
    """Per-run evaluation context: sliced feature cache."""

    def __init__(self, dataset_id: str, cache: Dict[str, np.ndarray], n: int):
        self.dataset_id = dataset_id
        self.cache = cache
        self.n = n


class Backtester:
    def __init__(self):
        self._feat = get_feature_engine()
        self._data = get_data_engine()

    # ---------- vectorized condition evaluation ----------
    def _get(self, ctx: _Ctx, name: str) -> np.ndarray:
        arr = ctx.cache.get(name)
        if arr is None:
            arr = self._feat.get(ctx.dataset_id, name)
            if len(arr) != ctx.n:      # safety: align by tail slice
                arr = arr[-ctx.n:]
            ctx.cache[name] = arr
        return arr

    def _eval(self, node: Any, ctx: _Ctx) -> np.ndarray:
        if not isinstance(node, dict):
            return np.zeros(ctx.n, dtype=bool)
        if "op" in node:
            op = node["op"]
            if op in ("and", "or"):
                acc: Optional[np.ndarray] = None
                for c in node["clauses"]:
                    v = self._eval(c, ctx)
                    acc = v if acc is None else (acc & v if op == "and" else acc | v)
                return acc if acc is not None else np.zeros(ctx.n, dtype=bool)
            if op == "not":
                return ~self._eval(node["clause"], ctx)
            return np.zeros(ctx.n, dtype=bool)
        t = node.get("type")
        if t == "compare":
            left = self._get(ctx, node["left"])
            right = node["right"]
            if isinstance(right, str):
                right = self._get(ctx, right)
            with np.errstate(invalid="ignore"):
                out = CMP_OPS[node["cmp"]](left, right)
            return np.nan_to_num(out, nan=False).astype(bool)
        if t == "crossover":
            a = self._get(ctx, node["a"])
            b = self._get(ctx, node["b"])
            prev = a[:-1] - b[:-1]
            cur = a[1:] - b[1:]
            with np.errstate(invalid="ignore"):
                if node["dir"] == "up":
                    cross = (prev <= 0) & (cur > 0)
                else:
                    cross = (prev >= 0) & (cur < 0)
            out = np.zeros(ctx.n, dtype=bool)
            out[1:] = np.nan_to_num(cross, nan=False).astype(bool)
            return out
        return np.zeros(ctx.n, dtype=bool)

    # ---------- perturbation for robustness ----------
    @staticmethod
    def _perturb_genome(genome: Dict, strength: float, seed: str) -> Dict:
        rng = np.random.default_rng(int(hashlib.sha256(seed.encode()).hexdigest()[:12], 16))
        g = copy.deepcopy(genome)

        def walk(node):
            if isinstance(node, dict):
                if node.get("type") == "compare" and isinstance(node.get("right"), (int, float)):
                    r = float(node["right"])
                    if abs(r) >= 1:
                        node["right"] = round(r * (1.0 + rng.normal(0, 1) * strength * 0.08), 4)
                    else:
                        node["right"] = round(r + rng.normal(0, 1) * strength * 0.04, 4)
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(g.get("entry_long"))
        walk(g.get("entry_short"))
        return g

    # ---------- main run ----------
    def run(self, req: BacktestRequest) -> BacktestResult:
        t0 = time.perf_counter()
        cfg = get_config()
        genome = req.genome
        ghash = genome_hash(genome)
        try:
            df_full = self._data.get_frame(req.dataset_id)
        except Exception as e:
            return BacktestResult(ok=False, error=f"dataset error: {e}")

        lo, hi = req.window if req.window else (0, len(df_full))
        lo = max(0, lo); hi = min(hi, len(df_full))
        n = hi - lo
        if n < 300:
            return BacktestResult(ok=False, error="window too small")
        df = df_full.iloc[lo:hi]

        g = genome
        if req.perturb > 0:
            g = self._perturb_genome(genome, req.perturb, ghash + req.seed_salt + "perturb")

        # ---- feature cache (sliced) ----
        cache: Dict[str, np.ndarray] = {}
        ctx = _Ctx(req.dataset_id, cache, n)
        feats_needed = referenced_features(g) | {"close", "open", "high", "low"}
        ex = g.get("exit") or {}
        atr_spec = ex.get("atr_spec", "atr:14")
        feats_needed.add(atr_spec)
        feats_needed |= set(REGIME_FEATURES)
        try:
            for f in feats_needed:
                arr = self._feat.get(req.dataset_id, f)
                cache[f] = arr[lo:hi] if len(arr) >= hi else arr[-n:]
        except Exception as e:
            return BacktestResult(ok=False, error=f"feature error ({f}): {e}")

        max_hold = int(ex.get("max_hold_bars", cfg.backtest.max_hold_bars))
        min_hold = int(ex.get("min_hold_bars", 0))
        if req.stage == "screen":
            max_hold = min(max_hold, cfg.backtest.screen_max_hold_bars)
        sl_mult = float(ex.get("sl_atr_mult") or 0.0)
        tp_mult = float(ex.get("tp_atr_mult") or 0.0)
        trailing = ex.get("trailing")
        if sl_mult <= 0 and tp_mult <= 0 and max_hold <= 0:
            return BacktestResult(ok=False, error="no exit definition")
        atr = cache[atr_spec]

        # ---- filters ----
        allow = np.ones(n, dtype=bool)
        sessions = g.get("sessions")
        if sessions:
            sess = df["session"].astype(str).to_numpy()
            hour = df["hour"].to_numpy()
            m = np.isin(sess, [s for s in sessions if s != "london_ny_overlap"])
            if "london_ny_overlap" in sessions:
                m |= (hour >= 12) & (hour < 16)
            allow &= m
        days = g.get("days")
        if days is not None:
            allow &= np.isin(df["dow"].to_numpy(), list(days))
        regimes = g.get("regime_filters")
        if regimes:
            rm = np.zeros(n, dtype=bool)
            for r in regimes:
                key = f"regime:{r}"
                arr = cache.get(key)
                if arr is None:
                    arr = self._feat.get(req.dataset_id, key)[lo:hi]
                    cache[key] = arr
                rm |= np.nan_to_num(arr, nan=0.0) > 0.5
            allow &= rm

        direction = req.direction_override or g.get("direction", "both")
        sig_long = np.zeros(n, dtype=bool)
        sig_short = np.zeros(n, dtype=bool)
        try:
            if direction in ("both", "long") and g.get("entry_long"):
                sig_long = self._eval(g["entry_long"], ctx) & allow
            if direction in ("both", "short") and g.get("entry_short"):
                sig_short = self._eval(g["entry_short"], ctx) & allow
        except Exception as e:
            return BacktestResult(ok=False, error=f"condition eval error: {e}")
        finite_atr = np.isfinite(atr) & (atr > 0)
        sig_long &= finite_atr
        sig_short &= finite_atr

        if req.mc_skip_prob > 0:
            rng = np.random.default_rng(int(hashlib.sha256(
                (ghash + req.seed_salt + "mc").encode()).hexdigest()[:12], 16))
            keep = rng.random(n) > req.mc_skip_prob
            sig_long &= keep
            sig_short &= keep

        exit_cond = self._eval(ex["exit_condition"], ctx) if ex.get("exit_condition") else None

        # ---- execution model (V5 §5: driven by the symbol's broker specs) ----
        from .symbol_specs import execution_params_for
        ep, symbol_specs = execution_params_for(g.get("symbol") or "", cfg)
        ep.spread_mult = req.spread_mult
        ep.slippage_mult = req.slippage_mult
        ep.commission_mult = req.commission_mult
        seed = f"{ghash}|{req.stage}|{req.seed_salt}|{lo}-{hi}|{req.spread_mult}|{req.slippage_mult}|{req.perturb}"
        slip = SlippageModel(ep, seed)

        o = df["open"].to_numpy(); h = df["high"].to_numpy()
        l = df["low"].to_numpy(); c = df["close"].to_numpy()
        ts = df["ts"].to_numpy(np.float64)
        spread_pts = df["spread"].to_numpy(np.float64) * ep.spread_mult
        spread_pts = np.where(np.isfinite(spread_pts) & (spread_pts > 0), spread_pts,
                              ep.default_spread_points * ep.spread_mult)
        half_spread = spread_pts * ep.point / 2.0
        sess_arr = df["session"].astype(str).to_numpy()
        dow_arr = df["dow"].to_numpy(np.int64)

        # regime label per bar (vectorized argmax over regime feature matrix)
        rmat = np.vstack([np.nan_to_num(cache[f"regime:{r}"], nan=0.0) for r in REGIME_NAMES])
        best_i = np.argmax(rmat, axis=0)
        has_any = rmat.max(axis=0) > 0.5
        regime_labels = np.where(has_any, np.array(REGIME_NAMES, dtype=object)[best_i], "")

        balance = cfg.backtest.initial_balance
        equity = balance
        peak = balance
        max_dd = 0.0
        trades: List[Trade] = []
        eq_curve: List[List[float]] = []

        entries = np.nonzero(sig_long | sig_short)[0]
        if len(entries) > req.max_trades * 4:
            idx_keep = np.linspace(0, len(entries) - 1, req.max_trades * 4).astype(int)
            entries = entries[idx_keep]

        stops_level_skips = 0
        slip_draws = slip.draw(len(entries) * 2 + 2)
        s_i = 0
        next_free = 0
        risk_cfg = g.get("risk") or {}
        rpt = float(risk_cfg.get("risk_per_trade", cfg.backtest.risk_per_trade))
        max_lots = cfg.risk.max_position_size_lots

        for idx in entries:
            if idx < next_free or idx + 1 >= n:
                continue
            side = "buy" if sig_long[idx] else "sell"
            e_i = idx + 1                      # execution delay: fill next bar open
            a = atr[idx]
            if not np.isfinite(a) or a <= 0:
                continue
            sp_in = slip_draws[s_i]; sp_out = slip_draws[s_i + 1]; s_i += 2
            entry_price = entry_fill_price(side, o[e_i], half_spread[e_i], sp_in, ep.point)
            sl_dist = sl_mult * a if sl_mult > 0 else 0.0
            tp_dist = tp_mult * a if tp_mult > 0 else 0.0
            lots = lots_for_risk(equity, rpt, sl_dist if sl_dist > 0 else a * 1.5,
                                 ep.contract_size, max_lots,
                                 min_lots=ep.min_lots, lot_step=ep.lot_step)
            if lots <= 0:
                continue
            if sl_dist > 0:
                # V5 §5: the broker refuses stops closer than trade_stops_level.
                # Such an order would be rejected live, so it is not filled here:
                # it is counted and skipped (no invented fill, no widened stop).
                if ep.stops_level_points > 0 and (sl_dist / ep.point) < ep.stops_level_points:
                    stops_level_skips += 1
                    continue
                sl = entry_price - sl_dist if side == "buy" else entry_price + sl_dist
            else:
                sl = np.nan
            if tp_dist > 0:
                tp = entry_price + tp_dist if side == "buy" else entry_price - tp_dist
            else:
                tp = np.nan

            tr = Trade(side=side, entry_idx=e_i, entry_ts=float(ts[e_i]),
                       entry_price=entry_price, lots=lots, sl=sl, tp=tp,
                       spread_paid=2 * half_spread[e_i] * lots * ep.contract_size,
                       slippage_points=float(sp_in), session=str(sess_arr[idx]),
                       dow=int(dow_arr[idx]), regime=str(regime_labels[idx]))

            act_dist = (trailing or {}).get("activation_mult", 0.0) * a
            trail_dist = (trailing or {}).get("atr_mult", 0.0) * a
            activated = trailing is not None and act_dist <= 0
            best_trail = entry_price

            exit_i = -1; exit_price = np.nan; reason = ""
            limit = min(n - 1, e_i + max_hold)
            for j in range(e_i, limit + 1):
                held = j - e_i
                if trailing:
                    if not activated:
                        if (side == "buy" and h[j] - entry_price >= act_dist) or \
                           (side == "sell" and entry_price - l[j] >= act_dist):
                            activated = True
                    if activated:
                        if side == "buy":
                            best_trail = max(best_trail, h[j])
                            tsl = best_trail - trail_dist
                            if np.isnan(sl) or tsl > sl:
                                sl = tsl
                        else:
                            best_trail = min(best_trail, l[j])
                            tsl = best_trail + trail_dist
                            if np.isnan(sl) or tsl < sl:
                                sl = tsl
                hit_sl = np.isfinite(sl) and (l[j] <= sl if side == "buy" else h[j] >= sl)
                if hit_sl:                       # SL always armed (risk first)
                    exit_i, exit_price, reason = j, sl, "SL"
                    break
                hit_tp = np.isfinite(tp) and (h[j] >= tp if side == "buy" else l[j] <= tp)
                if hit_tp and held >= min_hold:
                    exit_i, exit_price, reason = j, tp, "TP"
                    break
                if exit_cond is not None and j > e_i and exit_cond[j] and held >= min_hold:
                    exit_i, exit_price, reason = j, c[j], "EXIT_COND"
                    break
                if j == limit:
                    exit_i, exit_price, reason = j, c[j], "MAX_HOLD"
                    break
            if exit_i < 0:
                next_free = e_i + 1
                continue

            fill = exit_fill_price(side, exit_price, half_spread[exit_i], sp_out, ep.point)
            dirn = 1.0 if side == "buy" else -1.0
            gross = dirn * (fill - tr.entry_price) * tr.lots * ep.contract_size
            days_held = max(0.0, (ts[exit_i] - tr.entry_ts) / 86400.0)
            costs = commission_cost(tr.lots, ep) + swap_cost(tr.lots, days_held, ep)
            tr.pnl = float(gross - costs)
            tr.exit_idx = exit_i; tr.exit_ts = float(ts[exit_i])
            tr.exit_price = fill; tr.exit_reason = reason
            equity = float(equity + tr.pnl)
            peak = max(peak, equity)
            if peak > 0:
                max_dd = max(max_dd, (peak - equity) / peak)
            trades.append(tr)
            eq_curve.append([float(ts[exit_i]), round(equity, 2)])
            next_free = exit_i + 1
            if len(trades) >= req.max_trades:
                break

        metrics = self._metrics(trades, equity, balance, max_dd, req, ep, ghash, lo, hi,
                                symbol_specs=symbol_specs, stops_level_skips=stops_level_skips)
        runtime = (time.perf_counter() - t0) * 1000.0
        metrics["runtime_ms"] = round(runtime, 1)
        # Complete trade history and equity curve (spec §11)
        return BacktestResult(ok=True, metrics=metrics,
                              trades=[self._trade_json(t) for t in trades],
                              equity_curve=eq_curve, runtime_ms=runtime)

    # ---------- metrics ----------
    @staticmethod
    def _trade_json(t: Trade) -> Dict:
        return {"side": t.side, "entry_ts": t.entry_ts, "entry_price": round(t.entry_price, 3),
                "exit_ts": t.exit_ts, "exit_price": round(t.exit_price, 3), "lots": t.lots,
                "pnl": round(float(t.pnl), 4), "exit_reason": t.exit_reason, "session": t.session,
                "dow": t.dow, "regime": t.regime,
                "slippage_points": round(float(t.slippage_points), 2),
                "spread_paid": round(float(t.spread_paid), 4),
                "hold_bars": int(t.exit_idx - t.entry_idx)}

    def _metrics(self, trades: List[Trade], equity: float, balance: float,
                 max_dd: float, req: BacktestRequest, ep: ExecutionParams,
                 ghash: str, lo: int, hi: int, symbol_specs=None,
                 stops_level_skips: int = 0) -> Dict:
        nt = len(trades)
        pnls = np.array([t.pnl for t in trades], dtype=np.float64)
        wins = pnls[pnls > 0]; losses = pnls[pnls <= 0]
        gross_profit = float(wins.sum()) if wins.size else 0.0
        gross_loss = float(-losses.sum()) if losses.size else 0.0
        net = float(pnls.sum()) if nt else 0.0
        pf_raw = (gross_profit / gross_loss) if gross_loss > 0 else (
            float("inf") if gross_profit > 0 else 0.0)
        pf = min(pf_raw, 10.0)
        win_rate = float(wins.size / nt) if nt else 0.0
        avg_trade = net / nt if nt else 0.0
        expectancy = avg_trade / balance if balance else 0.0
        if nt >= 2:
            rets = pnls / balance
            mu = float(rets.mean()); sd = float(rets.std(ddof=1))
            neg = rets[rets < 0]
            downside = float(neg.std(ddof=1)) if neg.size > 1 else 1e-9
            tf = req.genome.get("timeframe", "M15")
            tf_min = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60}.get(tf, 15)
            ann = math.sqrt((5 * 24 * 60) / tf_min)
            sharpe = mu / sd * ann if sd > 1e-12 else 0.0
            sortino = mu / downside * ann if downside > 1e-12 else 0.0
        else:
            sharpe = sortino = 0.0
        monthly: Dict[str, float] = {}
        for t in trades:
            key = pd.Timestamp(t.exit_ts, unit="s", tz="UTC").strftime("%Y-%m")
            monthly[key] = float(monthly.get(key, 0.0) + t.pnl)
        consistency = (sum(1 for v in monthly.values() if v > 0) / len(monthly)) if monthly else 0.0
        reason_counts: Dict[str, int] = {}
        for t in trades:
            reason_counts[t.exit_reason] = reason_counts.get(t.exit_reason, 0) + 1
        durations = [max(0.0, float(t.exit_ts - t.entry_ts)) for t in trades]
        avg_dur_s = float(np.mean(durations)) if nt else 0.0
        min_dur_s = float(np.min(durations)) if nt else 0.0
        max_dur_s = float(np.max(durations)) if nt else 0.0
        return {
            "trades": nt,
            "net_profit": round(net, 2),
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "profit_factor": round(pf, 3),
            "profit_factor_raw": None if pf_raw == float("inf") else round(pf_raw, 3),
            "win_rate": round(win_rate, 4),
            "avg_trade": round(avg_trade, 3),
            "expectancy": round(expectancy, 6),
            "max_drawdown_pct": round(float(max_dd), 4),
            "sharpe": round(float(sharpe), 3),
            "sortino": round(float(sortino), 3),
            "total_return_pct": round((equity - balance) / balance, 4) if balance else 0.0,
            "final_equity": round(float(equity), 2),
            "consistency": round(consistency, 3),
            "monthly_pnl": {k: round(float(v), 2) for k, v in sorted(monthly.items())},
            "avg_hold_bars": round(float(np.mean([t.exit_idx - t.entry_idx for t in trades])), 1) if nt else 0.0,
            "avg_trade_duration_seconds": round(avg_dur_s, 1),
            "min_trade_duration_seconds": round(min_dur_s, 1),
            "max_trade_duration_seconds": round(max_dur_s, 1),
            "avg_trade_duration_minutes": round(avg_dur_s / 60.0, 2),
            "min_trade_duration_minutes": round(min_dur_s / 60.0, 2),
            "exit_reasons": reason_counts,
            "avg_slippage_points": round(float(np.mean([t.slippage_points for t in trades])), 3) if nt else 0.0,
            "total_spread_cost": round(float(np.sum([t.spread_paid for t in trades])), 2) if nt else 0.0,
            "stage": req.stage,
            "dataset_id": req.dataset_id,
            "window": [int(lo), int(hi)],
            "stress": {"spread_mult": req.spread_mult, "slippage_mult": req.slippage_mult,
                       "commission_mult": req.commission_mult,
                       "perturb": req.perturb, "mc_skip_prob": req.mc_skip_prob},
            # V5 §5 — the execution model every number above was produced with.
            # Nothing here is inferred after the fact: the values are the ones
            # the fill loop actually used.
            "execution_model": {
                "symbol": symbol_specs.symbol,
                "specs_source": symbol_specs.source,
                "specs_verified": bool(symbol_specs.verified),
                "specs_notes": symbol_specs.notes,
                "contract_size": ep.contract_size,
                "point": ep.point,
                "tick_size": symbol_specs.tick_size,
                "tick_value": symbol_specs.tick_value,
                "volume_min": ep.min_lots,
                "volume_max": ep.max_lots,
                "volume_step": ep.lot_step,
                "stops_level_points": ep.stops_level_points,
                "default_spread_points": ep.default_spread_points,
                "commission_per_lot": ep.commission_per_lot * ep.commission_mult,
                "swap_per_lot_per_day": ep.swap_per_lot_per_day,
                "slippage_model": ep.slippage_model,
                "slippage_mean_points": ep.slippage_mean_points * ep.slippage_mult,
                "execution_delay_ms": ep.execution_delay_ms,
                "fill_rule": "signals evaluated on bar close; market fills at next bar open with spread+slippage",
                "stops_level_skips": int(stops_level_skips),
            },
            "genome_hash": ghash,
        }


_engine: Optional[Backtester] = None


def get_backtester() -> Backtester:
    global _engine
    if _engine is None:
        _engine = Backtester()
    return _engine


def run_backtest(req: BacktestRequest) -> BacktestResult:
    return get_backtester().run(req)
