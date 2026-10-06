"""
Paper Trading Engine (spec §15, §16).

Runs QUALIFIED/PAPER strategies against the LIVE market feed from the active
bridge (real MT5 quotes when connected; otherwise the clearly-labelled
SIMULATOR feed). No real money is ever used here.

For every signal it records the full execution trail:
  signal time, requested price, bid, ask, spread, execution time, execution
  price, execution delay, slippage, result — plus the regime, strategy
  version (genome hash) and P/L.

All orders pass through the global RiskManager FIRST; a rejection is itself
recorded. Live calibration aggregates observed spread/slippage/latency and
compares against backtest assumptions (never silently rewriting history).
"""
from __future__ import annotations

import copy
import json

from ..jsonutil import jd
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ..backtest.engine import Backtester, _Ctx
from ..config import get_config
from ..data.engine import DataEngine
from ..db.database import get_db
from ..features import library
from ..genome.schema import genome_hash, referenced_features, describe
from ..mt5 import get_bridge
from ..backtest.execution import exit_fill_price
from ..risk.controls import get_risk_manager, session_of_hour
from ..api.ws import bus

log = logging.getLogger("paper.engine")

TF_MIN = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60}


class PaperPosition:
    def __init__(self, strategy_id: int, genome_hash_: str, side: str, lots: float,
                 entry_price: float, sl: float, tp: float, entry_ts: float,
                 trade_row_id: int, trailing: Optional[Dict], atr_at_entry: float,
                 max_hold_bars: int, tf: str, source: str):
        self.strategy_id = strategy_id
        self.ghash = genome_hash_
        self.side = side
        self.lots = lots
        self.entry_price = entry_price
        self.sl = sl
        self.tp = tp
        self.entry_ts = entry_ts
        self.trade_row_id = trade_row_id
        self.trailing = trailing
        self.atr = atr_at_entry
        self.max_hold_bars = max_hold_bars
        self.tf = tf
        self.source = source
        self.activated = trailing is not None and (trailing.get("activation_mult", 0) <= 0)
        self.best = entry_price


class PaperEngine:
    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._running = False
        self._lock = threading.RLock()
        self.positions: Dict[int, PaperPosition] = {}     # strategy_id -> pos
        self.last_signal_bar: Dict[int, float] = {}
        self._backtester = Backtester()
        self._de = DataEngine()
        self.stats = {"started_at": None, "ticks_processed": 0, "signals": 0,
                      "orders": 0, "rejections": 0, "closed": 0, "loop_errors": 0}
        self.divergence_flags: List[Dict] = []

    def recover_open_positions(self) -> int:
        """Recover open positions from SQLite after restart (spec §12)."""
        db = get_db()
        risk = get_risk_manager()
        rows = db.q("SELECT * FROM paper_trades WHERE status='OPEN'")
        recovered = 0
        with self._lock:
            for r in rows:
                sid = r["strategy_id"]
                trailing = None
                if r.get("trailing_state"):
                    try:
                        trailing = json.loads(r["trailing_state"])
                    except Exception:
                        pass
                sl_val = float(r["sl"]) if (r.get("sl") is not None and pd.notna(r["sl"])) else float("nan")
                tp_val = float(r["tp"]) if (r.get("tp") is not None and pd.notna(r["tp"])) else float("nan")
                pos = PaperPosition(
                    strategy_id=sid,
                    genome_hash_=r["genome_hash"],
                    side=r["side"],
                    lots=float(r["lots"]),
                    entry_price=float(r["exec_price"]),
                    sl=sl_val,
                    tp=tp_val,
                    entry_ts=float(r["exec_ts"]),
                    trade_row_id=r["id"],
                    trailing=trailing,
                    atr_at_entry=float(r.get("atr") or 0.0),
                    max_hold_bars=int(r.get("max_hold_bars") or 96),
                    tf=r.get("tf") or "M15",
                    source=r.get("source") or "SIMULATOR",
                )
                self.positions[sid] = pos
                risk.register_open(sid, float(r["exec_ts"]), float(r["lots"]))
                recovered += 1
        if recovered:
            log.info("paper engine recovered %d open positions from database (no orphans, spec §12)", recovered)
        return recovered

    # ---------- lifecycle ----------
    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> Dict:
        with self._lock:
            if self._running:
                return {"ok": False, "error": "already running"}
            self.recover_open_positions()
            self._stop.clear()
            self._running = True
            self.stats["started_at"] = time.time()
            self._thread = threading.Thread(target=self._loop, daemon=True,
                                            name="paper-engine")
            self._thread.start()
        get_db().log_event("paper_started", {"source": get_bridge().source})
        return {"ok": True, "source": get_bridge().source,
                "simulated": get_bridge().source == "SIMULATOR"}

    def stop(self) -> Dict:
        with self._lock:
            self._stop.set()
            self._running = False
        get_db().log_event("paper_stopped", {})
        return {"ok": True}

    # ---------- strategy selection ----------
    def enabled_strategies(self) -> List[Dict]:
        cfg = get_config()
        db = get_db()
        rows = db.q(
            """SELECT s.* FROM strategies s
               WHERE s.status IN ('QUALIFIED','PAPER_ELIGIBLE','PAPER','PAPER_TRADING','PAPER_PASSED','FINAL')
               ORDER BY s.fitness DESC LIMIT ?""", (cfg.paper.enabled_strategies_max * 3,))
        out = []
        for r in rows:
            sid = r["id"]
            bt = db.one(
                """SELECT metrics FROM backtests WHERE strategy_id=? AND stage IN ('detail','screen')
                   ORDER BY CASE stage WHEN 'detail' THEN 0 ELSE 1 END, id DESC LIMIT 1""",
                (sid,)
            )
            val = db.one("SELECT robustness_score, passed FROM validations WHERE strategy_id=? ORDER BY id DESC LIMIT 1", (sid,))
            m = json.loads(bt["metrics"]) if (bt and bt.get("metrics")) else {}
            rob = float(val["robustness_score"]) if (val and val.get("robustness_score") is not None) else 0.0

            trades = int(m.get("trades") or 0)
            pf = float(m.get("profit_factor") or 0.0)
            dd = float(m.get("max_drawdown_pct") or 1.0)

            # Promotion validation criteria (spec §20):
            # Candidate must have min trade count >= 10, PF >= 1.05, DD <= 0.35, robustness >= 0.50
            # (or already established on paper)
            is_valid_candidate = (
                (trades >= 10 and pf >= 1.05 and dd <= 0.35 and (rob >= 0.50 or val is None or val.get("passed") == 1))
                or r["status"] in ("PAPER", "PAPER_TRADING", "PAPER_PASSED", "FINAL")
            )
            if not is_valid_candidate:
                continue

            r["genome"] = json.loads(r["genome"])
            r["metrics"] = m
            r["robustness_score"] = rob
            out.append(r)
            if len(out) >= cfg.paper.enabled_strategies_max:
                break
        return out

    # ---------- live frame & features ----------
    def _live_frame(self, symbol: str, tf: str, n_bars: int = 400) -> Optional[pd.DataFrame]:
        bridge = get_bridge()
        bars = bridge.copy_rates(symbol, tf, n_bars)
        if not bars or len(bars) < 120:
            return None
        df = pd.DataFrame([b.to_dict() for b in bars])
        dt = pd.to_datetime(df["ts"], unit="s", utc=True)
        df["hour"] = dt.dt.hour.astype(np.int8)
        df["minute"] = dt.dt.minute.astype(np.int8)
        df["dow"] = dt.dt.dayofweek.astype(np.int8)
        df["dom"] = dt.dt.day.astype(np.int8)
        df["session"] = [session_of_hour(h) for h in df["hour"]]
        if bridge.source == "SIMULATOR":
            df = df[df["dow"] < 5].reset_index(drop=True)
        return df

    def _live_features(self, df: pd.DataFrame, specs: List[str]) -> Dict[str, np.ndarray]:
        """Compute needed features directly on the live frame (small window)."""
        cache: Dict[str, np.ndarray] = {}
        for spec in specs:
            if spec in cache:
                continue
            fam = library.feature_family(spec)
            if fam == "close":
                cache["close"] = df["close"].to_numpy(np.float64)
                continue
            if fam in ("open", "high", "low"):
                cache[fam] = df[fam].to_numpy(np.float64)
                continue
            try:
                fn, args = library.parse_spec(library_feature_spec(fam, spec))
                out = fn(df, *args)
                cache.update(out)
            except Exception as e:
                log.warning("live feature %s failed: %s", spec, e)
        return cache

    # ---------- main loop ----------
    def _loop(self) -> None:
        db = get_db()
        bridge = get_bridge()
        cfg = get_config()
        symbol = cfg.data.symbol
        while not self._stop.is_set():
            try:
                self._cycle(symbol, bridge, db)
            except Exception as e:
                self.stats["loop_errors"] += 1
                log.exception("paper loop error: %s", e)
            self._stop.wait(max(0.5, cfg.paper.tick_interval_ms / 1000.0 * 3))

    def _cycle(self, symbol: str, bridge, db) -> None:
        cfg = get_config()
        risk = get_risk_manager()
        strategies = self.enabled_strategies()
        if not strategies:
            return
        # group by timeframe to fetch each live frame once
        frames: Dict[str, pd.DataFrame] = {}
        for s in strategies:
            tf = s["genome"].get("timeframe", "M15")
            if tf not in frames:
                frames[tf] = self._live_frame(symbol, tf)
            df = frames.get(tf)
            if df is None or df.empty:
                continue
            self.stats["ticks_processed"] += 1
            g = s["genome"]
            ghash = s["hash"]
            last_closed_ts = float(df["ts"].iloc[-2])

            # ---- manage open position first ----
            pos = self.positions.get(s["id"])
            if pos:
                self._manage_position(pos, df, bridge, symbol, db, risk)
                continue   # one position per strategy

            # ---- entry signal on the last CLOSED bar ----
            if self.last_signal_bar.get(s["id"]) == last_closed_ts:
                continue
            needed = list(referenced_features(g)) + ["close", "open", "high", "low"]
            ex = g.get("exit") or {}
            needed.append(ex.get("atr_spec", "atr:14"))
            needed += [f"regime:{r}" for r in g.get("regime_filters") or []]
            cache = self._live_features(df, sorted(set(needed)))
            n = len(df)
            ctx = _Ctx("live", cache, n)
            # normalize cache lengths
            for k, v in list(cache.items()):
                if len(v) != n:
                    cache[k] = v[-n:]
            allow = np.ones(n, dtype=bool)
            sessions = g.get("sessions")
            if sessions:
                sess = df["session"].to_numpy()
                hour = df["hour"].to_numpy()
                m = np.isin(sess, [x for x in sessions if x != "london_ny_overlap"])
                if "london_ny_overlap" in sessions:
                    m |= (hour >= 12) & (hour < 16)
                allow &= m
            if g.get("days") is not None:
                allow &= np.isin(df["dow"].to_numpy(), list(g["days"]))
            for r in g.get("regime_filters") or []:
                arr = cache.get(f"regime:{r}")
                if arr is not None:
                    allow &= np.nan_to_num(arr, nan=0.0) > 0.5
            atr = cache.get(ex.get("atr_spec", "atr:14"))
            if atr is None or not np.isfinite(atr[-2]):
                self.last_signal_bar[s["id"]] = last_closed_ts
                continue

            direction = g.get("direction", "both")
            sig_l = sig_s = False
            i = n - 2   # last closed bar
            try:
                if direction in ("both", "long") and g.get("entry_long"):
                    sig_l = bool(self._backtester._eval(g["entry_long"], ctx)[i] and allow[i])
                if direction in ("both", "short") and g.get("entry_short"):
                    sig_s = bool(self._backtester._eval(g["entry_short"], ctx)[i] and allow[i])
            except Exception as e:
                log.warning("live eval error #%s: %s", s["id"], e)
            self.last_signal_bar[s["id"]] = last_closed_ts
            if not (sig_l or sig_s):
                continue
            side = "buy" if sig_l else "sell"
            self.stats["signals"] += 1
            db.log_event("paper_signal", {"strategy_id": s["id"], "side": side,
                                          "ts": last_closed_ts, "source": bridge.source})
            bus.publish("paper_signal", {"strategy_id": s["id"], "side": side,
                                         "ts": last_closed_ts, "source": bridge.source})

            # ---- quote, risk gate, simulated execution ----
            tick = bridge.latest_tick(symbol)
            if tick is None:
                continue
            spread_pts = (tick.ask - tick.bid) / max(cfg.backtest.point_value, 1e-9)
            a = float(atr[i])
            sl_mult = float(ex.get("sl_atr_mult") or 0)
            tp_mult = float(ex.get("tp_atr_mult") or 0)
            sl_dist = sl_mult * a
            tp_dist = tp_mult * a

            acct = self.get_account_state(db)
            equity = max(100.0, acct["current_equity"])
            from ..backtest.execution import lots_for_risk
            lots = lots_for_risk(equity, (g.get("risk") or {}).get("risk_per_trade", 0.005),
                                 sl_dist if sl_dist > 0 else a * 1.5,
                                 cfg.backtest.contract_size, cfg.risk.max_position_size_lots)

            req_px = tick.ask if side == "buy" else tick.bid
            sl_price = (req_px - sl_dist) if side == "buy" else (req_px + sl_dist)

            dec = risk.check_order(symbol, side, lots, spread_pts,
                                   cfg.paper.slippage_mean_points, s["id"],
                                   hour_utc=datetime.now(timezone.utc).hour)
            hard_dec = risk.check_paper_capital_risk(
                symbol=symbol, side=side, lots=lots,
                entry_price=req_px, sl_price=sl_price,
                current_equity=equity,
                starting_capital=acct["starting_capital"],
                current_daily_loss=acct["daily_loss"],
                current_drawdown_pct=acct["max_drawdown_pct"] / 100.0,
                current_exposure_val=acct["current_exposure"],
                strategy_id=s["id"],
                contract_size=cfg.backtest.contract_size,
            )

            if not dec.allowed or not hard_dec.allowed:
                rejection_reason = dec.reason if not dec.allowed else hard_dec.reason
                self.stats["rejections"] += 1
                bus.publish("paper_rejected", {"strategy_id": s["id"], "reason": rejection_reason})
                db.x("""INSERT INTO executions (ts,source,symbol,side,strategy_id,
                        requested_price,bid,ask,spread_points,signal_ts,result,rejected_by)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (time.time(), bridge.source, symbol, side, s["id"],
                      req_px, tick.bid, tick.ask,
                      spread_pts, last_closed_ts, "REJECTED", f"HARD_RISK_MANAGER: {rejection_reason}"))
                continue

            order = bridge.simulate_market_order(symbol, side, lots)
            self.stats["orders"] += 1
            result = "FILLED" if order.ok else "FAILED"
            db.x("""INSERT INTO executions (ts,source,symbol,side,strategy_id,
                    requested_price,bid,ask,spread_points,signal_ts,exec_ts,
                    exec_delay_ms,slippage_points,result)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (time.time(), order.source or bridge.source, symbol, side, s["id"],
                  order.requested_price, tick.bid, tick.ask, spread_pts, last_closed_ts,
                  order.exec_ts, order.delay_ms, order.slippage_points, result))
            bus.publish("paper_order", {"strategy_id": s["id"], "side": side,
                                         "ok": order.ok, "price": order.exec_price,
                                         "delay_ms": order.delay_ms,
                                         "slippage_points": order.slippage_points,
                                         "source": order.source or bridge.source})
            if not order.ok or order.exec_price is None:
                continue
            entry_price = order.exec_price
            if sl_dist > 0:
                sl = entry_price - sl_dist if side == "buy" else entry_price + sl_dist
            else:
                sl = float("nan")
            if tp_dist > 0:
                tp = entry_price + tp_dist if side == "buy" else entry_price - tp_dist
            else:
                tp = float("nan")
            # regime label at entry
            regime = ""
            try:
                reg_cache = self._live_features(df, ["regime:trending", "regime:ranging"])
                tr = reg_cache.get("regime:trending", np.zeros(n))
                rg = reg_cache.get("regime:ranging", np.zeros(n))
                regime = "trending" if tr[i] > 0.5 else ("ranging" if rg[i] > 0.5 else "")
            except Exception:
                pass
            trade_id = db.x("""INSERT INTO paper_trades
                    (strategy_id,symbol,side,signal_ts,requested_price,bid,ask,
                     spread_points,exec_ts,exec_price,exec_delay_ms,slippage_points,
                     lots,status,regime,genome_hash,source,sl,tp,trailing_state,atr,tf,max_hold_bars)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (s["id"], symbol, side, last_closed_ts, order.requested_price,
                             tick.bid, tick.ask, spread_pts, order.exec_ts, entry_price,
                             order.delay_ms, order.slippage_points, lots, "OPEN",
                             regime, ghash, order.source or bridge.source,
                             sl, tp, json.dumps(ex.get("trailing")) if ex.get("trailing") else None,
                             a, tf, int(ex.get("max_hold_bars", 96))))
            risk.register_open(s["id"], time.time(), lots)
            db.update_strategy(s["id"], status="PAPER" if s["status"] == "QUALIFIED" else s["status"])
            with self._lock:
                self.positions[s["id"]] = PaperPosition(
                    s["id"], ghash, side, lots, entry_price, sl, tp,
                    float(order.exec_ts or time.time()), trade_id,
                    ex.get("trailing"), a, int(ex.get("max_hold_bars", 96)), tf,
                    order.source or bridge.source)

    # ---------- position management ----------
    def _manage_position(self, pos: PaperPosition, df: pd.DataFrame, bridge,
                         symbol: str, db, risk) -> None:
        cfg = get_config()
        row = df.iloc[-1]          # forming bar
        last_closed = df.iloc[-2]
        hi, lo = float(row["high"]), float(row["low"])
        tick = bridge.latest_tick(symbol)
        if tick is not None and tick.bid and tick.ask:
            cur_mid = (float(tick.bid) + float(tick.ask)) / 2.0
            half_spread = (float(tick.ask) - float(tick.bid)) / 2.0
        elif tick is not None and tick.last:
            cur_mid = float(tick.last)
            half_spread = 0.0
        else:
            cur_mid = float(row["close"])
            half_spread = 0.0
        exit_price = None
        reason = ""
        # trailing
        if pos.trailing:
            act = pos.trailing.get("activation_mult", 0) * pos.atr
            tdist = pos.trailing.get("atr_mult", 0) * pos.atr
            if not pos.activated:
                if (pos.side == "buy" and hi - pos.entry_price >= act) or \
                   (pos.side == "sell" and pos.entry_price - lo >= act):
                    pos.activated = True
            if pos.activated:
                if pos.side == "buy":
                    pos.best = max(pos.best, hi)
                    tsl = pos.best - tdist
                    if np.isnan(pos.sl) or tsl > pos.sl:
                        pos.sl = tsl
                else:
                    pos.best = min(pos.best, lo)
                    tsl = pos.best + tdist
                    if np.isnan(pos.sl) or tsl < pos.sl:
                        pos.sl = tsl
        if not np.isnan(pos.sl) and ((pos.side == "buy" and lo <= pos.sl) or
                                     (pos.side == "sell" and hi >= pos.sl)):
            exit_price, reason = pos.sl, "SL"
        elif not np.isnan(pos.tp) and ((pos.side == "buy" and hi >= pos.tp) or
                                       (pos.side == "sell" and lo <= pos.tp)):
            exit_price, reason = pos.tp, "TP"
        elif (time.time() - pos.entry_ts) > pos.max_hold_bars * TF_MIN.get(pos.tf, 15) * 60:
            exit_price, reason = cur_mid, "MAX_HOLD"
        if exit_price is None:
            return
        order = bridge.simulate_market_order(symbol, "sell" if pos.side == "buy" else "buy",
                                             pos.lots)
        # same fill semantics as the backtester: TP/SL exits fill at the level
        # adjusted for spread + observed slippage; MAX_HOLD exits at market mid
        slip_pts = float(order.slippage_points or 0.0) if order.ok else 0.0
        base = float(exit_price) if reason in ("TP", "SL") else cur_mid
        fill = exit_fill_price(pos.side, base, half_spread, slip_pts,
                               cfg.backtest.point_value)
        dirn = 1.0 if pos.side == "buy" else -1.0
        pnl = dirn * (fill - pos.entry_price) * pos.lots * cfg.backtest.contract_size
        pnl -= pos.lots * cfg.backtest.commission_per_lot
        now = time.time()
        db.x("""UPDATE paper_trades SET exit_ts=?, exit_price=?, exit_reason=?, pnl=?,
                status='CLOSED' WHERE id=?""",
             (now, fill, reason, round(pnl, 4), pos.trade_row_id))
        db.x("""INSERT INTO executions (ts,source,symbol,side,strategy_id,requested_price,
                exec_ts,exec_delay_ms,slippage_points,result)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
             (now, pos.source, symbol, "close_" + pos.side, pos.strategy_id, exit_price,
              order.exec_ts, order.delay_ms, order.slippage_points, "FILLED"))
        risk.register_close(pos.strategy_id)
        with self._lock:
            self.positions.pop(pos.strategy_id, None)
        self.stats["closed"] += 1
        db.log_event("paper_exit", {"strategy_id": pos.strategy_id, "reason": reason,
                                    "pnl": round(pnl, 2), "source": pos.source})
        bus.publish("paper_exit", {"strategy_id": pos.strategy_id, "reason": reason,
                                   "pnl": round(pnl, 2), "source": pos.source})
        self._check_divergence(pos.strategy_id, db)

        # Check for strategy promotion to PAPER_PASSED or PAPER_FAILED (spec §20, §26)
        strat_p_stats = db.one("""SELECT COUNT(*) n, COALESCE(SUM(pnl), 0) pnl,
                                  AVG(CASE WHEN pnl > 0 THEN 1.0 ELSE 0.0 END) wr
                                  FROM paper_trades WHERE strategy_id=? AND status='CLOSED'""", (pos.strategy_id,))
        if strat_p_stats and int(strat_p_stats["n"] or 0) >= 5:
            pnl_tot = float(strat_p_stats["pnl"] or 0.0)
            wr_tot = float(strat_p_stats["wr"] or 0.0)
            if pnl_tot > 0 and wr_tot >= 0.50:
                pass_msg = f"PAPER PASSED: Net PnL ${pnl_tot:.2f}, Win Rate {wr_tot:.1%}, Trades {strat_p_stats['n']}"
                db.update_strategy(pos.strategy_id, status="PAPER_PASSED", survival_reason=pass_msg)
                log.info("[PAPER PROMOTION] Strategy #%d PROMOTED to PAPER_PASSED: %s", pos.strategy_id, pass_msg)
            elif pnl_tot < -100:
                fail_msg = f"PAPER FAILED: Net loss ${pnl_tot:.2f} breaches floor"
                db.update_strategy(pos.strategy_id, status="PAPER_FAILED", failure_reason=fail_msg)
                log.warning("[PAPER DEMOTION] Strategy #%d demoted to PAPER_FAILED: %s", pos.strategy_id, fail_msg)

    def _strategy_pnl(self, sid: int, db) -> float:
        row = db.one("SELECT COALESCE(SUM(pnl),0) p FROM paper_trades WHERE strategy_id=? AND status='CLOSED'", (sid,))
        return float(row["p"] or 0)

    def _check_divergence(self, sid: int, db) -> None:
        """Flag strategies whose paper behavior diverges from backtest (spec §15)."""
        cfg = get_config()
        row = db.one("""SELECT COUNT(*) n, COALESCE(SUM(pnl),0) p,
                        AVG(CASE WHEN pnl>0 THEN 1.0 ELSE 0.0 END) wr
                        FROM paper_trades WHERE strategy_id=? AND status='CLOSED'""", (sid,))
        bt = db.one("""SELECT metrics FROM backtests WHERE strategy_id=? AND stage='detail'
                       ORDER BY id DESC LIMIT 1""", (sid,))
        if not row or row["n"] < 5 or not bt:
            return
        m = json.loads(bt["metrics"])
        bt_wr = m.get("win_rate", 0.0)
        paper_wr = row["wr"] or 0.0
        diff = abs(bt_wr - paper_wr)
        if diff > cfg.paper.divergence_alert_threshold:
            flag = {"strategy_id": sid, "ts": time.time(),
                    "backtest_win_rate": bt_wr, "paper_win_rate": round(paper_wr, 3),
                    "paper_trades": row["n"], "paper_pnl": round(row["p"], 2),
                    "note": "paper behavior diverges from backtest expectations — flagged for investigation"}
            with self._lock:
                self.divergence_flags = [f for f in self.divergence_flags if f["strategy_id"] != sid]
                self.divergence_flags.append(flag)
            db.log_event("paper_divergence", flag)
            bus.publish("paper_divergence", flag)

    # ---------- account state & capital risk controls (spec §22, §24) ----------
    def get_account_state(self, db=None) -> Dict[str, Any]:
        if db is None:
            db = get_db()
        cfg = get_config()
        bridge = get_bridge()
        symbol = cfg.data.symbol
        tick = bridge.latest_tick(symbol)
        cur_mid = (tick.bid + tick.ask) / 2.0 if (tick and tick.bid and tick.ask) else 2000.0

        starting_capital = float(cfg.paper.starting_capital)
        row = db.one("SELECT COALESCE(SUM(pnl), 0) p FROM paper_trades WHERE status='CLOSED'")
        realized_pnl = float(row["p"] or 0.0)

        # Unrealized PnL & Exposure across all currently open positions
        unrealized_pnl = 0.0
        current_exposure_val = 0.0
        with self._lock:
            for pos in self.positions.values():
                dirn = 1.0 if pos.side == "buy" else -1.0
                pos_val = pos.lots * cfg.backtest.contract_size * cur_mid
                current_exposure_val += pos_val
                p_pnl = dirn * (cur_mid - pos.entry_price) * pos.lots * cfg.backtest.contract_size
                p_pnl -= pos.lots * cfg.backtest.commission_per_lot
                unrealized_pnl += p_pnl

        current_balance = starting_capital + realized_pnl
        current_equity = current_balance + unrealized_pnl
        return_pct = round(((current_equity - starting_capital) / starting_capital) * 100.0, 2)

        # Daily loss (trades closed today UTC + negative unrealized)
        today_start_ts = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        today_closed = db.one(
            "SELECT COALESCE(SUM(pnl), 0) p FROM paper_trades WHERE status='CLOSED' AND exit_ts >= ?",
            (today_start_ts,)
        )
        today_realized = float(today_closed["p"] or 0.0) if today_closed else 0.0
        daily_loss_val = max(0.0, -(today_realized + min(0.0, unrealized_pnl)))
        daily_loss_pct = round((daily_loss_val / starting_capital) * 100.0, 2)

        # High watermark & drawdown
        peak_equity = max(starting_capital, starting_capital + realized_pnl)
        max_dd_val = max(0.0, peak_equity - current_equity)
        max_dd_pct = round((max_dd_val / peak_equity) * 100.0, 2)

        exposure_pct = round((current_exposure_val / max(current_equity, 1.0)) * 100.0, 2)
        concurrent_positions = len(self.positions)

        risk = get_risk_manager()
        guardrail_status = (
            "EMERGENCY KILL SWITCH ENGAGED" if risk.kill_switch_engaged()
            else ("GUARDRAIL BREACH DETECTED" if len(risk.breaches) > 0 and (time.time() - risk.breaches[-1]["ts"] < 3600)
                  else "ALL GUARDRAILS ACTIVE / NOMINAL")
        )

        return {
            "starting_capital": starting_capital,
            "current_balance": round(current_balance, 2),
            "current_equity": round(current_equity, 2),
            "realized_pnl": round(realized_pnl, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "return_pct": return_pct,
            "daily_loss": round(daily_loss_val, 2),
            "daily_loss_pct": daily_loss_pct,
            "max_drawdown": round(max_dd_val, 2),
            "max_drawdown_pct": max_dd_pct,
            "current_exposure": round(current_exposure_val, 2),
            "current_exposure_pct": exposure_pct,
            "concurrent_positions": concurrent_positions,
            "max_concurrent_positions": cfg.paper.max_concurrent_positions,
            "risk_per_trade_pct": round(cfg.paper.max_risk_per_trade_pct * 100.0, 2),
            "risk_per_trade_abs": cfg.paper.max_risk_per_trade_abs,
            "guardrail_status": guardrail_status,
            "risk_breaches": len(risk.breaches),
        }

    def promoted_strategies(self, db=None) -> List[Dict[str, Any]]:
        """List all strategies promoted to or eligible for paper trading (spec §21, §24)."""
        if db is None:
            db = get_db()
        cfg = get_config()
        risk = get_risk_manager()
        rows = db.q(
            """SELECT s.* FROM strategies s
               WHERE s.status IN ('QUALIFIED', 'PAPER_ELIGIBLE', 'PAPER', 'PAPER_TRADING', 'PAPER_PASSED', 'FINAL')
               ORDER BY s.fitness DESC LIMIT 60"""
        )
        out = []
        for r in rows:
            sid = r["id"]
            try:
                genome = json.loads(r["genome"])
            except Exception:
                genome = {}
            bt = db.one(
                """SELECT metrics FROM backtests WHERE strategy_id=? AND stage IN ('detail','screen')
                   ORDER BY CASE stage WHEN 'detail' THEN 0 ELSE 1 END, id DESC LIMIT 1""",
                (sid,)
            )
            val = db.one("SELECT robustness_score, passed FROM validations WHERE strategy_id=? ORDER BY id DESC LIMIT 1", (sid,))
            m = json.loads(bt["metrics"]) if (bt and bt.get("metrics")) else {}
            rob = float(val["robustness_score"]) if (val and val.get("robustness_score") is not None) else 0.0

            # Paper trades stats
            p_stats = db.one("""SELECT COUNT(*) n,
                                COALESCE(SUM(pnl), 0) pnl,
                                AVG(CASE WHEN pnl > 0 THEN 1.0 ELSE 0.0 END) wr,
                                COALESCE(SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END) / NULLIF(SUM(CASE WHEN pnl < 0 THEN ABS(pnl) ELSE 0 END), 0), 1.0) pf
                                FROM paper_trades WHERE strategy_id=? AND status='CLOSED'""", (sid,))
            p_trades = int(p_stats["n"] or 0)
            p_pnl = float(p_stats["pnl"] or 0.0)
            p_wr = float(p_stats["wr"] or 0.0) if (p_stats and p_stats.get("wr") is not None and p_trades > 0) else float(m.get("win_rate") or 0.0)
            p_pf = float(p_stats["pf"] or 1.0) if (p_stats and p_stats.get("pf") is not None and p_trades > 0) else float(m.get("profit_factor") or 1.0)

            is_open = sid in self.positions
            strat_breaches = len([b for b in risk.breaches if b.get("strategy_id") == sid])

            # Paper Result determination (spec §24)
            if r["status"] in ("PAPER_PASSED", "FINAL"):
                p_res = "PASSED"
            elif r["status"] == "PAPER_FAILED":
                p_res = "FAILED"
            elif p_trades >= 5 and p_pnl > 0:
                p_res = "PASSED"
            elif p_trades >= 5 and p_pnl < -100:
                p_res = "FAILED"
            elif is_open or p_trades > 0:
                p_res = "TESTING"
            else:
                p_res = "WAITING"

            item = {
                "id": sid,
                "node": f"Node_{sid}",
                "desc": describe(genome)[:120],
                "status": r["status"],
                "pnl": round(p_pnl, 2),
                "return_pct": round((p_pnl / cfg.paper.starting_capital) * 100.0, 2),
                "win_rate": round(p_wr * 100.0, 1),
                "profit_factor": round(p_pf, 2),
                "trades": p_trades if p_trades > 0 else int(m.get("trades") or 0),
                "drawdown": round(float(m.get("max_drawdown_pct") or 0.0) * 100.0, 1),
                "exposure": round(float(self.positions[sid].lots if is_open else 0.0) * 10.0, 1),
                "risk_breaches": strat_breaches,
                "paper_result": p_res,
                "qualification_reason": r.get("survival_reason") or f"QUALIFIED: WR {round(float(m.get('win_rate') or 0.0)*100, 1)}%, PF {round(float(m.get('profit_factor') or 1.0), 2)}, Rob {round(rob, 2)}",
                "timeframe": genome.get("timeframe", "M15"),
                "symbol": genome.get("symbol", cfg.data.symbol),
            }
            out.append(item)
        return out

    # ---------- introspection ----------
    def status(self) -> Dict:
        bridge = get_bridge()
        db = get_db()
        cfg = get_config()
        acct = self.get_account_state(db)
        return {
            "running": self._running,
            "feed_source": bridge.source,
            "feed_is_simulated": bridge.source == "SIMULATOR",
            "account": acct,
            "limits": {
                "starting_capital": cfg.paper.starting_capital,
                "max_risk_per_trade_pct": cfg.paper.max_risk_per_trade_pct,
                "max_risk_per_trade_abs": cfg.paper.max_risk_per_trade_abs,
                "max_daily_loss_pct": cfg.paper.max_daily_loss_pct,
                "max_daily_loss_abs": cfg.paper.max_daily_loss_abs,
                "max_total_drawdown_pct": cfg.paper.max_total_drawdown_pct,
                "max_concurrent_positions": cfg.paper.max_concurrent_positions,
                "max_exposure_pct": cfg.paper.max_exposure_pct,
            },
            "open_positions": [{"strategy_id": p.strategy_id, "side": p.side,
                                "entry_price": p.entry_price, "lots": p.lots,
                                "entry_ts": p.entry_ts, "source": p.source}
                               for p in self.positions.values()],
            "stats": self.stats,
            "divergence_flags": self.divergence_flags[-20:],
            "enabled_strategies": [{"id": s["id"], "fitness": s["fitness"],
                                    "status": s["status"],
                                    "desc": describe(s["genome"])[:160]}
                                   for s in self.enabled_strategies()],
            "promoted_strategies": self.promoted_strategies(db),
        }


def library_feature_spec(family: str, spec: str) -> str:
    """Map an output feature name back to a computable spec.

    e.g. 'macd_hist:12:26:9' -> 'macd:12:26:9'; 'close' -> 'price'.
    """
    base = spec.split(":")[0]
    params = spec.split(":")[1:]
    alias_to_family = {v: k for k, v in {
        "macd": "macd_hist", "stoch": "stoch_k", "bb": "bb_upper",
        "atr": "atr_pct", "volatility": "range_expansion", "volume": "relvol",
    }.items()}
    fam = library.feature_family(spec)
    if fam in library.FEATURE_FUNCS:
        if fam == base:
            return spec
        return ":".join([fam] + params) if params else fam
    if fam == "price":
        return "price"
    return spec


_engine: Optional[PaperEngine] = None


def get_paper_engine() -> PaperEngine:
    global _engine
    if _engine is None:
        _engine = PaperEngine()
    return _engine
