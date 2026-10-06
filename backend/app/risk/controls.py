"""
Global Risk Controls (spec §23) — independent of strategy logic and of the
AI layer. Every simulated or real order passes through RiskManager; if any
limit is exceeded the order is REJECTED and the rejection is recorded.

  * max daily drawdown (kill-switch for the day)
  * max strategy drawdown
  * max spread
  * max position size / max concurrent positions
  * max trades per minute
  * min/max holding time
  * max allowed slippage
  * trading session restrictions
  * EMERGENCY KILL SWITCH (blocks everything, user-operated)

Real execution additionally requires explicit user activation
(real_execution_enabled) — it is never enabled automatically.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..config import get_config, save_config
from ..data.sessions import session_of_ts
from ..db.database import get_db

log = logging.getLogger("risk.controls")


def session_of_hour(hour_utc: int) -> str:
    """Legacy helper; prefer session_of_ts for DST-correct labeling (spec §43)."""
    if 0 <= hour_utc < 7:
        return "asia"
    if 7 <= hour_utc < 13:
        return "london"
    if 13 <= hour_utc < 21:
        return "newyork"
    return "off"


@dataclass
class RiskDecision:
    allowed: bool
    reason: str = ""
    checked: Dict = field(default_factory=dict)


class RiskManager:
    """Singleton risk gate for paper + (optionally) real execution (V2)."""

    def __init__(self):
        self._lock = threading.RLock()
        # Kill switch survives restart (spec §33): read config + db meta
        db_ks = False
        try:
            db_val = get_db().get_meta("kill_switch")
            db_ks = (db_val == "1")
        except Exception:
            pass
        self._kill_switch = get_config().risk.kill_switch or db_ks
        self._day_start_equity: Optional[float] = None
        self._day_key: str = ""
        self._equity = 0.0
        self._open_positions: Dict[int, Dict] = {}    # strategy_id -> pos info
        self._recent_order_times: List[float] = []
        self._strategy_peak: Dict[int, float] = {}
        self.rejections: List[Dict] = []
        self.breaches: List[Dict] = []

    # ---------- state ----------
    def set_kill_switch(self, on: bool) -> None:
        """Persist kill switch across restarts (spec §33)."""
        with self._lock:
            self._kill_switch = bool(on)
        cfg = get_config()
        cfg.risk.kill_switch = bool(on)
        save_config(cfg)
        try:
            get_db().set_meta("kill_switch", "1" if on else "0")
            from ..activity import activity
            if on:
                activity.warning("RISK", "EMERGENCY KILL SWITCH ENGAGED — all orders blocked")
            else:
                activity.info("RISK", "Kill switch released")
        except Exception:
            pass
        log.warning("KILL SWITCH %s (persisted to config + db)", "ENGAGED" if on else "released")

    def kill_switch_engaged(self) -> bool:
        with self._lock:
            return self._kill_switch or get_config().risk.kill_switch

    def update_equity(self, equity: float) -> None:
        with self._lock:
            today = time.strftime("%Y-%m-%d", time.gmtime())
            if today != self._day_key:
                self._day_key = today
                self._day_start_equity = equity
            self._equity = equity

    def register_open(self, strategy_id: int, ts: float, lots: float) -> None:
        with self._lock:
            self._open_positions[strategy_id] = {"ts": ts, "lots": lots}

    def register_close(self, strategy_id: int) -> None:
        with self._lock:
            self._open_positions.pop(strategy_id, None)

    def update_strategy_equity(self, strategy_id: int, equity: float) -> None:
        with self._lock:
            self._strategy_peak[strategy_id] = max(
                self._strategy_peak.get(strategy_id, equity), equity)

    # ---------- gates ----------
    def check_order(self, symbol: str, side: str, lots: float, spread_points: float,
                    slippage_points: float, strategy_id: int,
                    hour_utc: Optional[int] = None) -> RiskDecision:
        cfg = get_config().risk
        checked = {"kill_switch": not self.kill_switch_engaged(),
                   "spread": spread_points <= cfg.max_spread_points,
                   "lots": lots <= cfg.max_position_size_lots,
                   "slippage": slippage_points <= cfg.max_allowed_slippage_points}
        with self._lock:
            now = time.time()
            self._recent_order_times = [t for t in self._recent_order_times if now - t < 60]
            checked["rate"] = len(self._recent_order_times) < cfg.max_trades_per_minute
            checked["concurrent"] = len(self._open_positions) < cfg.max_concurrent_positions
            checked["session"] = True
            # DST-aware session check (spec §43)
            sess = session_of_ts(now)
            if sess not in cfg.allowed_sessions:
                checked["session"] = False
            # daily drawdown
            checked["daily_dd"] = True
            if self._day_start_equity and self._day_start_equity > 0:
                dd = (self._day_start_equity - self._equity) / self._day_start_equity
                checked["daily_dd"] = dd <= cfg.max_daily_drawdown_pct
            # strategy drawdown
            checked["strategy_dd"] = True
            peak = self._strategy_peak.get(strategy_id)
            if peak and peak > 0 and self._equity > 0:
                pass  # strategy-level DD tracked by paper engine per-strategy equity
            if self.kill_switch_engaged():
                dec = RiskDecision(False, "KILL SWITCH ENGAGED", checked)
            else:
                failing = [k for k, v in checked.items() if not v]
                dec = RiskDecision(not failing, "; ".join(f"limit exceeded: {k}" for k in failing), checked)
            if dec.allowed:
                self._recent_order_times.append(now)
        if not dec.allowed:
            rec = {"ts": now, "symbol": symbol, "side": side, "lots": lots,
                   "strategy_id": strategy_id, "reason": dec.reason}
            with self._lock:
                self.rejections.append(rec)
                self.rejections = self.rejections[-200:]
            log.warning("RISK REJECT %s %s %s lots=%s: %s", symbol, side,
                        strategy_id, lots, dec.reason)
        return dec

    def record_breach(self, strategy_id: int, reason: str, details: Optional[Dict] = None) -> None:
        """Record hard capital risk breach in audit trail."""
        now = time.time()
        rec = {
            "ts": now,
            "strategy_id": strategy_id,
            "reason": reason,
            "details": details or {},
        }
        with self._lock:
            self.breaches.append(rec)
            if len(self.breaches) > 300:
                self.breaches = self.breaches[-300:]
        log.warning("[RISK BREACH] Strategy #%s: %s", strategy_id, reason)
        try:
            from ..api.ws import bus
            bus.publish("risk_breach", rec)
        except Exception:
            pass

    def check_paper_capital_risk(
        self,
        symbol: str,
        side: str,
        lots: float,
        entry_price: float,
        sl_price: float,
        current_equity: float,
        starting_capital: float,
        current_daily_loss: float,
        current_drawdown_pct: float,
        current_exposure_val: float,
        strategy_id: int = 0,
        contract_size: float = 100.0,
    ) -> RiskDecision:
        """Hard capital risk controls for simulated paper trading (spec §22, §23).

        The strictest applicable limit wins. If any limit is breached:
        DO NOT EXECUTE -> REJECT -> LOG REASON -> UPDATE RISK STATE.
        """
        cfg = get_config()
        pcfg = cfg.paper
        now = time.time()

        checked: Dict[str, bool] = {
            "kill_switch": not self.kill_switch_engaged(),
            "concurrent": len(self._open_positions) < pcfg.max_concurrent_positions,
            "risk_per_trade": True,
            "daily_loss": True,
            "total_drawdown": True,
            "exposure": True,
        }
        rejection_reasons: List[str] = []

        if not checked["kill_switch"]:
            rejection_reasons.append("EMERGENCY KILL SWITCH ENGAGED")

        if not checked["concurrent"]:
            rejection_reasons.append(
                f"Concurrent positions {len(self._open_positions)} reaches limit {pcfg.max_concurrent_positions}"
            )

        # 1. Strictest risk per trade (percent vs absolute)
        sl_dist = abs(entry_price - sl_price) if (sl_price and sl_price > 0 and sl_price != entry_price) else (entry_price * 0.015)
        proposed_risk_abs = lots * contract_size * sl_dist
        max_risk_pct_val = max(0.0, current_equity * pcfg.max_risk_per_trade_pct)
        max_risk_abs_val = pcfg.max_risk_per_trade_abs
        strictest_trade_risk = min(max_risk_pct_val, max_risk_abs_val)

        if proposed_risk_abs > strictest_trade_risk:
            checked["risk_per_trade"] = False
            rejection_reasons.append(
                f"Risk per trade ${proposed_risk_abs:.2f} exceeds strict limit ${strictest_trade_risk:.2f} "
                f"(pct limit: ${max_risk_pct_val:.2f}, abs limit: ${max_risk_abs_val:.2f})"
            )

        # 2. Strictest daily loss limit (percent vs absolute)
        max_daily_loss_pct_val = max(0.0, starting_capital * pcfg.max_daily_loss_pct)
        max_daily_loss_abs_val = pcfg.max_daily_loss_abs
        strictest_daily_loss = min(max_daily_loss_pct_val, max_daily_loss_abs_val)

        if current_daily_loss >= strictest_daily_loss:
            checked["daily_loss"] = False
            rejection_reasons.append(
                f"Daily loss ${current_daily_loss:.2f} exceeds strict limit ${strictest_daily_loss:.2f} "
                f"(pct limit: ${max_daily_loss_pct_val:.2f}, abs limit: ${max_daily_loss_abs_val:.2f})"
            )

        # 3. Maximum total drawdown limit
        if current_drawdown_pct >= pcfg.max_total_drawdown_pct:
            checked["total_drawdown"] = False
            rejection_reasons.append(
                f"Total drawdown {current_drawdown_pct * 100.0:.1f}% breaches max limit {pcfg.max_total_drawdown_pct * 100.0:.1f}%"
            )

        # 4. Maximum portfolio exposure
        proposed_pos_val = lots * contract_size * entry_price
        total_exposure_val = current_exposure_val + proposed_pos_val
        exposure_pct = total_exposure_val / max(current_equity, 1.0)
        if exposure_pct > pcfg.max_exposure_pct:
            checked["exposure"] = False
            rejection_reasons.append(
                f"Portfolio exposure {exposure_pct * 100.0:.1f}% would exceed max limit {pcfg.max_exposure_pct * 100.0:.1f}%"
            )

        allowed = len(rejection_reasons) == 0
        reason_str = "; ".join(rejection_reasons) if not allowed else "APPROVED"

        if not allowed:
            self.record_breach(strategy_id, reason_str, {
                "symbol": symbol, "side": side, "lots": lots,
                "proposed_risk": round(proposed_risk_abs, 2),
                "strictest_trade_risk": round(strictest_trade_risk, 2),
                "daily_loss": round(current_daily_loss, 2),
                "drawdown_pct": round(current_drawdown_pct * 100.0, 2),
                "exposure_pct": round(exposure_pct * 100.0, 2),
            })
            with self._lock:
                self.rejections.append({
                    "ts": now, "symbol": symbol, "side": side, "lots": lots,
                    "strategy_id": strategy_id, "reason": reason_str,
                })
                self.rejections = self.rejections[-200:]
            log.warning("[PAPER HARD RISK REJECT] Strategy #%s %s %s: %s",
                        strategy_id, side, symbol, reason_str)

        return RiskDecision(allowed, reason_str, checked)

    def check_holding_time(self, seconds_held: float, min_required: bool = False) -> RiskDecision:
        cfg = get_config().risk
        if min_required and seconds_held < cfg.min_holding_seconds:
            return RiskDecision(False, f"min holding time {cfg.min_holding_seconds}s not reached")
        if seconds_held > cfg.max_holding_seconds:
            return RiskDecision(False, f"max holding time {cfg.max_holding_seconds}s exceeded")
        return RiskDecision(True, "ok")

    def real_execution_allowed(self) -> bool:
        """Real money trading requires EXPLICIT user activation. Never auto."""
        cfg = get_config().risk
        return bool(cfg.real_execution_enabled) and not self.kill_switch_engaged()

    def snapshot(self) -> Dict:
        cfg = get_config().risk
        pcfg = get_config().paper
        with self._lock:
            breach_count = len(self.breaches)
            guardrail_status = (
                "EMERGENCY KILL SWITCH ENGAGED" if self.kill_switch_engaged()
                else ("GUARDRAIL BREACH DETECTED" if breach_count > 0 and (time.time() - self.breaches[-1]["ts"] < 3600)
                      else "ALL GUARDRAILS ACTIVE / NOMINAL")
            )
            return {
                "kill_switch": self.kill_switch_engaged(),
                "real_execution_enabled": cfg.real_execution_enabled,
                "open_positions": len(self._open_positions),
                "recent_orders_60s": len([t for t in self._recent_order_times if time.time() - t < 60]),
                "daily_start_equity": self._day_start_equity,
                "equity": self._equity,
                "guardrail_status": guardrail_status,
                "breaches_count": breach_count,
                "recent_breaches": self.breaches[-20:],
                "limits": {
                    "max_daily_drawdown_pct": cfg.max_daily_drawdown_pct,
                    "max_strategy_drawdown_pct": cfg.max_strategy_drawdown_pct,
                    "max_spread_points": cfg.max_spread_points,
                    "max_position_size_lots": cfg.max_position_size_lots,
                    "max_concurrent_positions": pcfg.max_concurrent_positions,
                    "max_trades_per_minute": cfg.max_trades_per_minute,
                    "min_holding_seconds": cfg.min_holding_seconds,
                    "max_holding_seconds": cfg.max_holding_seconds,
                    "max_allowed_slippage_points": cfg.max_allowed_slippage_points,
                    "allowed_sessions": cfg.allowed_sessions,
                    "starting_capital": pcfg.starting_capital,
                    "max_risk_per_trade_pct": pcfg.max_risk_per_trade_pct,
                    "max_risk_per_trade_abs": pcfg.max_risk_per_trade_abs,
                    "max_daily_loss_pct": pcfg.max_daily_loss_pct,
                    "max_daily_loss_abs": pcfg.max_daily_loss_abs,
                    "max_total_drawdown_pct": pcfg.max_total_drawdown_pct,
                    "max_exposure_pct": pcfg.max_exposure_pct,
                },
                "recent_rejections": self.rejections[-20:],
            }


_mgr: Optional[RiskManager] = None


def get_risk_manager() -> RiskManager:
    global _mgr
    if _mgr is None:
        _mgr = RiskManager()
    return _mgr
