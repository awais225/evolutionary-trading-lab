"""V4.3 controlled Live Testing engine (demo only).

Pipeline (spec §3 - §14):
    Strategy/Node -> Risk calculation -> Live market state -> Controlled
    execution (V4.2 path) -> Trade monitoring -> Stage-by-stage live log

Safety invariants (spec §4, §5, §15, §20, §21):
  * The engine always starts INACTIVE. ``_MODE["active"]`` lives in memory only;
    there is no database or config flag that could re-arm it on restart, after a
    page/app reload, an MT5 reconnect or a research-run change. Activation is an
    explicit operator action with a confirmation payload.
  * Deactivation blocks *new* orders immediately and never closes existing
    positions (STOP NEW TRADES != CLOSE EXISTING POSITIONS).
  * No automatic retries. An ambiguous broker response is marked
    ``UNKNOWN - VERIFY MT5`` and reconciled against the broker before anything
    else may happen.
  * Execution goes through :mod:`app.mt5.execution` unchanged, so the V4.2 demo
    account guard, order validation, duplicate protection and audit trail all
    still apply.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..api.ws import bus
from ..backtest.engine import Backtester, _Ctx
from ..config import get_config
from ..data.engine import DataEngine
from ..db.database import get_db
from ..features import library
from ..genome.schema import referenced_features
from ..mt5 import get_bridge
from ..mt5 import execution as mt5_exec
from ..paper.engine import library_feature_spec
from ..risk.controls import session_of_hour
from .risk import (RiskBlock, compute_risk, resolve_per_node_limit,
                   resolve_risk_pct, risk_limits)

log = logging.getLogger("livelab.live_testing")

# ---------------------------------------------------------------- constants ---
MODE_INACTIVE = "INACTIVE"
MODE_ACTIVE = "ACTIVE"

STAGE_SIGNAL = "SIGNAL DETECTED"
STAGE_RISK = "RISK CALCULATED"
STAGE_VOLUME = "VOLUME CALCULATED"
STAGE_MARKET = "MARKET VALIDATED"
STAGE_ORDER_VALIDATED = "ORDER VALIDATED"
STAGE_ORDER_SENT = "ORDER SENT"
STAGE_BROKER = "BROKER RESPONSE"
STAGE_POSITION = "POSITION VERIFIED"
STAGE_BLOCKED = "BLOCKED"
STAGE_REJECTED = "REJECTED"
STAGE_UNKNOWN = "UNKNOWN - VERIFY MT5"
STAGE_CLOSED = "POSITION CLOSED"
STAGE_INFO = "INFO"

STAGES_ALL = (STAGE_SIGNAL, STAGE_RISK, STAGE_VOLUME, STAGE_MARKET,
              STAGE_ORDER_VALIDATED, STAGE_ORDER_SENT, STAGE_BROKER,
              STAGE_POSITION, STAGE_BLOCKED, STAGE_REJECTED, STAGE_UNKNOWN,
              STAGE_CLOSED, STAGE_INFO)

# live_test_trades.status values owned by this layer
TRADE_BLOCKED = "BLOCKED"                      # never sent to the broker
TRADE_UNCONFIRMED = "EXECUTED_UNCONFIRMED"     # broker replied OK, not yet verified
TRADE_POSITION = "POSITION_OPEN"               # verified live position
TRADE_PENDING = "PENDING_ORDER"                # verified working order
TRADE_UNKNOWN = "UNKNOWN"                      # ambiguous result - verify in MT5
TRADE_CLOSED_MISSING = "CLOSED_MISSING"        # was live, no longer present at the broker

ACTIVE_TRADE_STATUSES = (TRADE_UNCONFIRMED, TRADE_POSITION, TRADE_PENDING, TRADE_UNKNOWN)

# statuses of strategies that must never receive live-test execution (spec §16)
# V5.2.2 — the one live-tradeability predicate lives in ``eligibility`` so the
# engine, the population counters and the Live Testing page cannot disagree.
from .eligibility import EXCLUDED_STATUSES as _EXCLUDED_STATUSES  # noqa: E402
from .eligibility import tradeability as _tradeability

_TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600}

# In-memory, never persisted: Live Testing is INACTIVE after any restart (§4/§21).
_MODE: Dict[str, Any] = {"active": False, "activated_at": None, "deactivated_at": None,
                         "stop_reason": None, "activation": None}
_MODE_LOCK = threading.RLock()


def _iso(ts: Optional[float]) -> Optional[str]:
    if not ts:
        return None
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat(timespec="seconds")


def _fmt_num(v: Any, nd: int = 2) -> str:
    try:
        if v is None:
            return "n/a"
        f = float(v)
        return f"{f:.{nd}f}" if f == f else "n/a"   # NaN -> n/a
    except (TypeError, ValueError):
        return str(v)


def _human_stage_message(stage: str, ev: Dict[str, Any]) -> str:
    """Human-readable stage line - never a raw object, never [object Object]."""
    node = ev.get("node_id")
    sym = ev.get("symbol") or "?"
    side = (ev.get("side") or "").upper()
    d = ev.get("detail") or {}
    head = f"#{node} {sym} {side}".strip()
    if stage == STAGE_SIGNAL:
        return (f"{head} - signal detected on closed bar {d.get('bar_time', 'n/a')} "
                f"(tf {d.get('timeframe', '?')}, source {d.get('source', '?')})")
    if stage == STAGE_RISK:
        return (f"{head} - risk {_fmt_num(d.get('risk_pct'), 2)}% of equity "
                f"{_fmt_num(d.get('equity'), 2)} = {_fmt_num(d.get('risk_amount'), 2)} "
                f"({d.get('risk_pct_source', 'global_default')})")
    if stage == STAGE_VOLUME:
        return (f"{head} - volume {_fmt_num(d.get('volume'), 2)} lots "
                f"(stop {_fmt_num(d.get('stop_distance'), 2)}, tick {d.get('tick_size')}/"
                f"{d.get('tick_value')}, min {d.get('volume_min')} step {d.get('volume_step')})")
    if stage == STAGE_MARKET:
        return (f"{head} - market validated: bid {_fmt_num(d.get('bid'), 5)} / "
                f"ask {_fmt_num(d.get('ask'), 5)}, spread {_fmt_num(d.get('spread'), 2)}, "
                f"tick age {_fmt_num(d.get('age_s'), 1)}s")
    if stage == STAGE_ORDER_VALIDATED:
        return (f"{head} - order validated: {_fmt_num(d.get('volume'), 2)} lots @ "
                f"{_fmt_num(d.get('price'), 5)}, SL {_fmt_num(d.get('sl'), 5)}, "
                f"TP {_fmt_num(d.get('tp'), 5)}")
    if stage == STAGE_ORDER_SENT:
        return (f"{head} - order sent to MT5 ({d.get('symbol')} {side} "
                f"{_fmt_num(d.get('volume'), 2)} lots, magic {d.get('magic')})")
    if stage == STAGE_BROKER:
        return (f"{head} - broker response: {d.get('status')}"
                + (f" retcode {d.get('retcode')}" if d.get("retcode") is not None else "")
                + f" ({d.get('message', '')})")
    if stage == STAGE_POSITION:
        tid = d.get("position_ticket") or d.get("order_ticket") or d.get("deal_ticket") or d.get("ticket")
        return (f"{head} - position verified at broker: {d.get('status')} ticket {tid}")
    if stage == STAGE_BLOCKED:
        return f"{head} - BLOCKED at {d.get('blocked_stage', '?')}: {d.get('reason', '')}"
    if stage == STAGE_REJECTED:
        return f"{head} - REJECTED by MT5: {d.get('reason', '')}"
    if stage == STAGE_UNKNOWN:
        return (f"{head} - UNKNOWN - VERIFY MT5: {d.get('reason', '')} "
                "(no automatic retry)")
    if stage == STAGE_CLOSED:
        return f"{head} - live test position closed ({d.get('reason', 'closed at broker')})"
    return f"{head} - {d.get('summary') or ev.get('message') or stage}"


def _safe_describe(describe_fn, genome: Dict[str, Any]) -> Optional[str]:
    try:
        return describe_fn(genome)
    except Exception:
        return None


def _market_clock():
    """The wall clock the market-state panel reads (narrow seam; tests pin it so
    calendar weekends never flip market-closed verdicts in the suite)."""
    return datetime.now(timezone.utc)


class LiveTestingEngine:
    """Controlled live-testing loop; runs whenever the backend runs but is inert
    until the operator activates it (spec §4/§5)."""

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._running = False
        self._lock = threading.RLock()
        self._backtester = Backtester()
        self._de = DataEngine()
        self.last_signal_bar: Dict[int, float] = {}
        self._last_opened: Dict[str, float] = {}      # reuse-guard, in-memory only
        self._unresolved: Dict[str, float] = {}       # symbol -> ts of an ambiguous result
        self._order_lock = threading.Lock()           # one order at a time, ever
        # V6.5 §6.5 — reconciliation/sync state, always visible in the UI
        self.sync_state: Dict[str, Any] = {"last_sync_ts": None, "certainty": "NEVER_SYNCED",
                                          "positions_reconciled": 0, "exits_captured": 0,
                                          "recovered": 0, "missing_or_unmatched": 0,
                                          "errors": 0}
        self._cycle_count = 0
        self._last_cycle_ts: Optional[float] = None
        self._last_cycle_error: Optional[str] = None
        self._last_noop_reason: Optional[str] = None
        self.recent_events: List[Dict[str, Any]] = []
        self.stats = {"signals": 0, "blocked": 0, "orders_sent": 0, "positions_opened": 0,
                      "unknown": 0, "loop_errors": 0}

    # ------------------------------------------------------------------ mode ---
    @property
    def active(self) -> bool:
        return bool(_MODE["active"])

    def activation_payload(self) -> Dict[str, Any]:
        limits = risk_limits()
        cfg = get_config()
        nodes = self.eligible_nodes()
        return {
            "mt5_account": None,          # filled by the API layer from the execution state
            "account_status": None,
            "symbols": sorted({str((n.get("genome") or {}).get("symbol") or "?") for n in nodes}),
            "nodes": [{"node_id": n["id"], "strategy_id": n["id"], "symbol": (n.get("genome") or {}).get("symbol"),
                       "timeframe": (n.get("genome") or {}).get("timeframe"),
                       "risk_pct": n.get("_effective_risk_pct"), "risk_pct_source": n.get("_risk_source")}
                      for n in nodes],
            "node_count": len(nodes),
            "risk_pct_default": limits["risk_pct_default"],
            "risk_pct_max": limits["risk_pct_max"],
            "max_active_trades": limits["max_active_trades"],
            "market": self.market_panel(),
            "warning": ("Demo orders can be placed on the MT5 DEMO account when Live Testing is "
                        "activated. Activation does not close any existing position."),
            "default_risk_pct": cfg.live_testing.risk_pct_default,
        }

    def activate(self, *, confirmed: bool = False, armed_by: str = "operator",
                 risk_pct_default: Optional[float] = None,
                 max_active_trades: Optional[int] = None,
                 node_ids: Optional[List[int]] = None) -> Dict[str, Any]:
        """Explicit activation with confirmation (spec §5). Nothing else can set ACTIVE."""
        if not confirmed:
            return {"ok": False, "code": "CONFIRMATION_REQUIRED",
                    "error": "Live Testing activation requires confirmed=True after the operator "
                             "reviewed the confirmation panel"}
        cfg = get_config().live_testing
        if risk_pct_default is not None:
            cfg.risk_pct_default = float(risk_pct_default)
        if max_active_trades is not None:
            cfg.max_active_trades = int(max_active_trades)
        if cfg.risk_pct_default <= 0 or cfg.risk_pct_default > cfg.risk_pct_max:
            return {"ok": False, "code": "RISK_PCT_INVALID",
                    "error": f"default risk {cfg.risk_pct_default:g}% must be >0 and <= "
                             f"{cfg.risk_pct_max:g}%"}
        if cfg.max_active_trades < 1:
            return {"ok": False, "code": "MAX_ACTIVE_INVALID",
                    "error": "max active trades must be >= 1"}
        if node_ids is not None:
            keep = {int(x) for x in node_ids}
            db = get_db()
            for n in self.eligible_nodes():
                if n["id"] not in keep:
                    db.set_live_test_config(n["id"], is_active=0)
        payload = self.activation_payload()
        with _MODE_LOCK:
            _MODE.update({"active": True, "activated_at": time.time(),
                          "deactivated_at": None, "stop_reason": None,
                          "activation": payload})
        self._last_opened.clear()
        db = get_db()
        db.log_event("live_testing_activated", {"risk_pct_default": cfg.risk_pct_default,
                                                "max_active_trades": cfg.max_active_trades,
                                                "armed_by": armed_by,
                                                "nodes": payload["node_count"],
                                                "symbols": payload["symbols"]})
        bus.publish("live_testing_mode", {"active": True, "activated_at": _MODE["activated_at"],
                                          "nodes": payload["node_count"]})
        self._stage({"stage": STAGE_INFO, "status": "ACTIVE", "node_id": None, "symbol": None,
                     "side": None, "message": None,
                     "detail": {"summary": f"Live Testing activated by {armed_by} - "
                                           f"{payload['node_count']} node(s), risk "
                                           f"{cfg.risk_pct_default:g}%, max "
                                           f"{cfg.max_active_trades} active trade(s)"}})
        return {"ok": True, "mode": MODE_ACTIVE, "activation": payload,
                "activated_at": _MODE["activated_at"]}

    def deactivate(self, *, reason: str = "operator stopped live testing",
                   interrupted: bool = False) -> Dict[str, Any]:
        """STOP LIVE TESTING: blocks new orders immediately; never closes positions."""
        with _MODE_LOCK:
            was = bool(_MODE["active"])
            _MODE.update({"active": False, "deactivated_at": time.time(),
                          "stop_reason": reason})
        db = get_db()
        db.log_event("live_testing_deactivated", {"reason": reason, "was_active": was,
                                                  "interrupted": bool(interrupted)})
        bus.publish("live_testing_mode", {"active": False, "reason": reason,
                                          "interrupted": bool(interrupted)})
        self._stage({"stage": STAGE_INFO, "status": "INACTIVE", "node_id": None, "symbol": None,
                     "side": None, "message": None,
                     "detail": {"summary": f"Live Testing INACTIVE - {reason}. New orders blocked; "
                                           "existing positions are NOT closed."}})
        return {"ok": True, "mode": MODE_INACTIVE, "was_active": was, "reason": reason,
                "positions_closed": 0,
                "note": "Existing live-test positions were left untouched (no automatic liquidation)."}

    def get_mode(self) -> Dict[str, Any]:
        return {"active": self.active, "mode": MODE_ACTIVE if self.active else MODE_INACTIVE,
                "activated_at": _MODE.get("activated_at"), "activated_at_iso": _iso(_MODE.get("activated_at")),
                "deactivated_at": _MODE.get("deactivated_at"), "stop_reason": _MODE.get("stop_reason"),
                # display-only: "was armed earlier in this process" never trades by itself
                "previously_active": bool(_MODE.get("activation") or _MODE.get("deactivated_at")),
                "activation": _MODE.get("activation")}

    # ------------------------------------------------------------------ lifecycle ---
    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> Dict[str, Any]:
        """Start the monitoring thread. The thread being up does NOT mean trading:
        the engine only evaluates nodes when the mode is ACTIVE (spec §4)."""
        with self._lock:
            if self._running:
                return {"ok": False, "error": "already running"}
            self._stop.clear()
            self._running = True
            self._thread = threading.Thread(target=self._loop, daemon=True,
                                            name="live-testing-engine")
            self._thread.start()
        get_db().log_event("live_testing_engine_started",
                           {"active": False, "note": "starts INACTIVE by design"})
        return {"ok": True, "active": self.active, "mode": MODE_ACTIVE if self.active else MODE_INACTIVE}

    def stop(self, *, interrupted: bool = True, reason: str = "backend shutdown - Live Testing stays INACTIVE") -> Dict[str, Any]:
        with self._lock:
            self._stop.set()
            self._running = False
        if self.active or interrupted:
            self.deactivate(reason=reason, interrupted=interrupted)
        else:
            with _MODE_LOCK:
                _MODE["active"] = False
        return {"ok": True}

    def _loop(self) -> None:
        cfg = get_config()
        while not self._stop.is_set():
            started = time.time()
            self._cycle_count += 1
            try:
                self._cycle()
                self._last_cycle_error = None
            except Exception as e:  # never let the loop die
                self.stats["loop_errors"] += 1
                self._last_cycle_error = f"{type(e).__name__}: {e}"
                log.exception("live testing cycle error: %s", e)
            self._last_cycle_ts = time.time()
            wait = max(1.0, float(cfg.live_testing.tick_interval_s) - (time.time() - started))
            self._stop.wait(wait)

    # ------------------------------------------------------------------ config / eligibility ---
    def live_test_configs(self) -> List[Dict[str, Any]]:
        """Active live-test configs joined to their node (spec §8, §16)."""
        db = get_db()
        rows = db.q(
            """SELECT c.*, s.status AS strategy_status, s.fitness AS strategy_fitness,
                      s.genome AS strategy_genome, s.hash AS genome_hash,
                      s.run_id AS run_id, s.research_node_num AS research_node_num,
                      COALESCE(s.data_source,'') AS data_source
               FROM live_test_configs c JOIN strategies s ON s.id = c.strategy_id
               WHERE c.is_active=1 ORDER BY c.strategy_id""")
        out = []
        for r in rows:
            try:
                r["genome"] = json.loads(r["strategy_genome"]) if r.get("strategy_genome") else {}
            except Exception:
                r["genome"] = {}
            out.append(r)
        return out

    def eligible_nodes(self) -> List[Dict[str, Any]]:
        """Configs whose node may actually trade (spec §16) - never LEGACY_TEST,
        never dead/killed/retired/invalid, must carry a usable trading config."""
        limits = risk_limits()
        nodes, notes = [], []
        for r in self.live_test_configs():
            sid = r["strategy_id"]
            genome = r.get("genome") or {}
            risk = resolve_risk_pct(limits["risk_pct_default"], r.get("risk_pct"))
            entry = {"id": sid, "node_id": sid, "strategy_id": sid,
                     "genome": genome, "config": r,
                     "_effective_risk_pct": risk["risk_pct"], "_risk_source": risk["source"]}
            # V5.2.2 — the SAME predicate the population counter uses ("eligibility"),
            # so the number the operator reads and the nodes the engine trades agree
            # by construction.  ``status`` is the stored status of this config's node.
            verdict = _tradeability({"data_source": r.get("data_source"),
                                     "status": r.get("strategy_status")}, genome=genome)
            if not verdict["ok"]:
                notes.append({**entry, "reason": verdict["reason"], "code": verdict["code"]})
                continue
            nodes.append(entry)
        self._eligibility_notes = notes
        self._eligible = nodes
        return nodes

    def excluded_nodes(self) -> List[Dict[str, Any]]:
        return list(getattr(self, "_eligibility_notes", []))

    @staticmethod
    def _bridge_connected(bridge=None) -> bool:
        """Same connection notion the V4.2 demo guard uses (single source of truth)."""
        b = bridge or get_bridge()
        try:
            return bool(getattr(b, "_connected", False))
        except Exception:
            return False

    # ------------------------------------------------------------------ market panel ---
    def current_regime(self, node: Dict[str, Any]) -> Optional[str]:
        """Dominant market regime on the node's last closed bar (§13).

        Uses the *research engine's own* regime vocabulary and the lab's own
        feature store (the same ``regime:*`` features the backtest engine
        evaluates), so the live schedule gate and the historical backtest agree
        on what "trending" means. Returns ``None`` when the features are not
        available: the caller then reports "regime not evaluated" instead of
        pretending the rule passed.
        """
        try:
            from ..backtest.engine import REGIME_NAMES
            from ..data.engine import get_data_engine
            from ..features.engine import get_feature_engine
        except Exception:
            return None
        g = node.get("genome") or {}
        symbol = str(g.get("symbol") or "").upper()
        timeframe = str(g.get("timeframe") or "")
        if not symbol or not timeframe:
            return None
        try:
            ds = get_data_engine().latest_dataset(symbol, timeframe)
            if not ds:
                return None
            dataset_id = ds.get("dataset_id") if isinstance(ds, dict) else ds
            feats = get_feature_engine()
            scores: Dict[str, float] = {}
            for name in REGIME_NAMES:
                arr = feats.get(dataset_id, f"regime:{name}")
                if arr is None or len(arr) == 0:
                    continue
                last = float(np.nan_to_num(arr[-1], nan=0.0))
                scores[name] = last
            if not scores:
                return None
            best = max(scores, key=scores.get)
            return best if scores[best] > 0.5 else None
        except Exception:
            return None

    def market_panel(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Compact MT5-authoritative market state (spec §11/§12)."""
        bridge = get_bridge()
        cfg = get_config()
        symbols: List[str] = []
        if symbol:
            symbols = [symbol]
        else:
            try:
                symbols = sorted({(n.get("genome") or {}).get("symbol") for n in self.eligible_nodes()} - {None})
            except Exception:
                symbols = []
        if not symbols:
            symbols = [cfg.data.symbol]
        sym = symbols[0]
        now = time.time()
        out: Dict[str, Any] = {
            "symbol": sym, "source": bridge.source, "connection": None, "connected": False,
            "bid": None, "ask": None, "spread": None, "point": None, "digits": None,
            "tick_time": None, "tick_age_s": None, "data_fresh": False,
            "symbol_tradable": None, "session_status": None, "trading_available": False,
            "reasons": [], "ts": now, "other_symbols": symbols[1:],
        }
        out["connected"] = self._bridge_connected(bridge)
        out["connection"] = "CONNECTED" if out["connected"] else "DISCONNECTED"
        try:
            out["bridge_available"] = bool(bridge.available())
        except Exception:
            out["bridge_available"] = None
        if not out["connected"]:
            out["reasons"].append(f"MT5 not connected ({out['connection']}) - live trading unavailable")
            return out
        try:
            si = bridge.symbol_info(sym)
        except Exception:
            si = None
        if si is None:
            out["reasons"].append(f"no market information for {sym}")
            return out
        out["digits"] = getattr(si, "digits", None)
        out["point"] = getattr(si, "point", None)
        # same tradability notion the V4.2 validator uses (single source of truth)
        out["symbol_tradable"] = bool(getattr(si, "trade_allowed", False))
        out["trade_allowed"] = getattr(si, "trade_allowed", None)
        out["trade_mode"] = getattr(si, "trade_mode", None)
        out["trade_mode_raw"] = getattr(si, "trade_mode_raw", None)
        out["volume_min"] = getattr(si, "volume_min", None)
        out["volume_max"] = getattr(si, "volume_max", None)
        out["volume_step"] = getattr(si, "volume_step", None)
        out["tick_size"] = getattr(si, "trade_tick_size", None)
        out["tick_value"] = getattr(si, "trade_tick_value", None)
        out["stops_level"] = getattr(si, "trade_stops_level", None)
        tick = None
        try:
            tick = bridge.latest_tick(sym)
        except Exception:
            tick = None
        if tick is None:
            out["reasons"].append(f"no tick available for {sym} - refusing to guess a price")
        else:
            out["bid"] = float(tick.bid)
            out["ask"] = float(tick.ask)
            out["spread"] = round(out["ask"] - out["bid"], 8)
            ts = float(getattr(tick, "ts", 0) or 0)
            out["tick_time"] = _iso(ts)
            out["tick_age_s"] = round(max(0.0, now - ts), 2) if ts else None
            fresh = out["tick_age_s"] is not None and out["tick_age_s"] <= cfg.live_testing.max_data_age_s
            out["data_fresh"] = fresh
            if not fresh:
                out["reasons"].append(
                    f"market data for {sym} is stale ({_fmt_num(out['tick_age_s'], 1)}s old, "
                    f"limit {cfg.live_testing.max_data_age_s}s)")
            if out["ask"] <= 0 or out["bid"] <= 0 or out["ask"] < out["bid"]:
                out["data_fresh"] = False
                out["reasons"].append(f"invalid quote for {sym} (bid {out['bid']} / ask {out['ask']})")
        clock = _market_clock()
        hour = clock.hour
        out["session_status"] = session_of_hour(hour)
        weekend = clock.weekday() >= 5
        market_closed = weekend and bridge.source != "SIMULATOR"
        if market_closed:
            out["reasons"].append("market closed (weekend) - broker would reject new orders")
        if not out["symbol_tradable"]:
            out["reasons"].append(f"{sym} is not tradable on this account")
        out["market_open"] = not market_closed
        out["trading_available"] = bool(out["data_fresh"] and out["symbol_tradable"]
                                        and not market_closed and out["connected"])
        return out

    # ------------------------------------------------------------------ trade counter ---
    def count_activity(self) -> Dict[str, Any]:
        """Active live-test trades counted from MT5 positions/orders (spec §13).

        MT5/broker state is authoritative: an audit row in the local database is
        NOT an open position.
        """
        bridge = get_bridge()
        try:
            get_db().ensure_live_trade_columns()   # V4.3 traceability columns
        except Exception as e:
            log.warning("live_test_trades migration notice: %s", e)
        src = str(getattr(bridge, "source", "UNKNOWN") or "UNKNOWN")
        out = {"active_total": 0, "positions": 0, "orders": 0, "recent_executed": 0,
               "source": f"MT5 ({src})", "position_tickets": [], "order_tickets": [],
               "recent": [], "counted": False, "error": None}
        if src != "MT5":
            # a simulator bridge has no broker state: report "not counted" instead of
            # pretending there are zero live-test trades on the account.
            out["error"] = (f"broker positions/orders unavailable: the active bridge is "
                            f"'{src}', not a real MT5 connection")
            return out
        try:
            positions = bridge.positions_get() or []
            orders = bridge.orders_get() or []
            out["counted"] = True
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {e}"
            return out
        def _magic(row: Any) -> int:
            return int((row.get("magic") if isinstance(row, dict) else getattr(row, "magic", 0)) or 0)

        def _ticket(row: Any) -> Any:
            return row.get("ticket") if isinstance(row, dict) else getattr(row, "ticket", None)

        for p in positions:
            if mt5_exec.LIVE_TEST_MAGIC_BASE <= _magic(p) < mt5_exec.LIVE_TEST_MAGIC_BASE + 1000:
                out["positions"] += 1
                out["position_tickets"].append(_ticket(p))
        for o in orders:
            if mt5_exec.LIVE_TEST_MAGIC_BASE <= _magic(o) < mt5_exec.LIVE_TEST_MAGIC_BASE + 1000:
                out["orders"] += 1
                out["order_tickets"].append(_ticket(o))
        out["active_total"] = out["positions"] + out["orders"]
        # V6.5 §5.4 — attribution by ORIGINATING node (the magic stamp written at
        # submission); the same position seen twice is counted once.
        per_node: Dict[str, int] = {}
        seen_t = set()
        for p in positions:
            m = _magic(p)
            if not (mt5_exec.LIVE_TEST_MAGIC_BASE <= m < mt5_exec.LIVE_TEST_MAGIC_BASE + 1000):
                continue
            t = _ticket(p)
            if t in seen_t:
                continue
            if t is not None:
                seen_t.add(t)
            nk = str(m - mt5_exec.LIVE_TEST_MAGIC_BASE)
            per_node[nk] = per_node.get(nk, 0) + 1
        out["per_node"] = per_node
        db = get_db()
        cutoff = time.time() - 3600.0
        rows = db.q("SELECT open_ts,updated_at,status,strategy_id,symbol,side FROM live_test_trades "
                    "WHERE COALESCE(updated_at, open_ts, 0)>=? AND status IN "
                    "('EXECUTED_UNCONFIRMED','POSITION_OPEN','PENDING_ORDER','UNKNOWN',"
                    "'CLOSED_MISSING') ORDER BY id DESC LIMIT 25", (cutoff,))
        out["recent_executed"] = len(rows)
        out["recent"] = [{"ts": r.get("updated_at") or r.get("open_ts"),
                          "ts_iso": _iso(r.get("updated_at") or r.get("open_ts")), "status": r["status"],
                          "node_id": r["strategy_id"], "symbol": r["symbol"], "side": r["side"]}
                         for r in rows]
        return out

    def order_limit_reached(self, activity: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        limits = risk_limits()
        act = activity if activity is not None else self.count_activity()
        limit = limits["max_active_trades"]
        return {"reached": bool(act.get("counted") and act["active_total"] >= limit),
                "active_total": act.get("active_total", 0), "limit": limit,
                "counted": act.get("counted", False), "source": act.get("source"),
                "pending_orders": act.get("orders", 0), "positions": act.get("positions", 0)}

    # ------------------------------------------------------------------ schedule ---
    def schedule_state(self, node: Dict[str, Any], *, spread_points: Optional[float] = None,
                       open_positions: int = 0,
                       now: Optional[float] = None,
                       regime: Optional[str] = None,
                       timeframe: Optional[str] = None) -> Dict[str, Any]:
        """V5 §11 — evaluate this node's live schedule against its real history.

        The inputs are all from the lab's own records: how many trades the node
        has taken today and when its last entry was. Nothing is cached, so the
        answer the dashboard shows is the answer the engine acts on.
        """
        from .schedule import evaluate as _eval
        sid = node.get("id")
        cfg = node.get("config") or {}
        db = get_db()
        trades_today = 0
        last_entry_ts = None
        try:
            rows = db.q("SELECT open_ts, status FROM live_test_trades WHERE strategy_id=?", (sid,)) or []
            day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0,
                                                           microsecond=0).timestamp()
            for r in rows:
                ts = r.get("open_ts")
                if ts is None:
                    continue
                if float(ts) >= day_start:
                    trades_today += 1
                if last_entry_ts is None or float(ts) > last_entry_ts:
                    last_entry_ts = float(ts)
        except Exception:
            pass
        if timeframe is None:
            timeframe = (node.get("genome") or {}).get("timeframe")
        state = _eval(cfg, now=now, spread_points=spread_points,
                      trades_today=trades_today, open_positions=open_positions,
                      last_entry_ts=last_entry_ts, regime=regime, timeframe=timeframe)
        state["trades_today"] = trades_today
        state["last_entry_ts"] = last_entry_ts
        state["strategy_id"] = sid
        state["timeframe"] = timeframe
        state["regime"] = regime
        # §15 — the node's own signal components, as permitted by the schedule
        from .schedule import condition_enabled
        state["conditions"] = {
            name: condition_enabled(cfg, name)
            for name in ("entry_long", "entry_short", "exit_condition", "trailing_stop")
        }
        return state

    # ------------------------------------------------------------------ status ---
    def status(self) -> Dict[str, Any]:
        db = get_db()
        sync = dict(self.sync_state)
        limits = risk_limits()
        try:
            activity = self.count_activity()
        except Exception as e:
            activity = {"active_total": None, "counted": False, "error": str(e), "source": "MT5"}
        nodes = self.eligible_nodes()
        excluded = self.excluded_nodes()
        rows = db.q("SELECT status, COUNT(*) AS n FROM live_test_trades GROUP BY status")
        recorded = {r["status"]: int(r["n"]) for r in rows}
        if activity.get("counted"):
            limit_info = self.order_limit_reached(activity)
        else:
            # unknown broker state is NOT the same thing as "limit reached"
            limit_info = {"reached": None, "unknown": True, "limit": limits["max_active_trades"],
                          "counted": False, "active_total": None,
                          "reason": activity.get("error") or "broker state unavailable"}
        return {
            "mode": MODE_ACTIVE if self.active else MODE_INACTIVE,
            "active": self.active,
            "activated_at": _MODE.get("activated_at"), "activated_at_iso": _iso(_MODE.get("activated_at")),
            "deactivated_at": _MODE.get("deactivated_at"), "stop_reason": _MODE.get("stop_reason"),
            "previously_active": bool(_MODE.get("activation") or _MODE.get("deactivated_at")),
            "engine_running": self.running,
            "risk": {**limits},
            "nodes": [{"node_id": n["id"], "symbol": (n.get("genome") or {}).get("symbol"),
                       "timeframe": (n.get("genome") or {}).get("timeframe"),
                       "risk_pct": n.get("_effective_risk_pct"), "risk_pct_source": n.get("_risk_source"),
                       "status": n["config"].get("strategy_status")} for n in nodes],
            "node_count": len(nodes),
            "excluded": [{"node_id": n["id"], "reason": n.get("reason")} for n in excluded],
            "excluded_count": len(excluded),
            "max_active_trades": limits["max_active_trades"],
            "limit": limit_info,
            "activity": activity,
            "records": {"active": sum(recorded.get(s, 0) for s in ACTIVE_TRADE_STATUSES),
                        "blocked": recorded.get(TRADE_BLOCKED, 0),
                        "unknown": recorded.get(TRADE_UNKNOWN, 0),
                        "total": sum(recorded.values()), "by_status": recorded},
            "last_cycle_ts": self._last_cycle_ts, "last_cycle_iso": _iso(self._last_cycle_ts),
            "cycle_count": self._cycle_count, "last_cycle_error": self._last_cycle_error,
            "last_noop_reason": self._last_noop_reason,
            "stats": dict(self.stats),
            "recent_events": self.recent_events[:20],
            "sync": sync,
            "max_active_trades_per_node_default": int(
                getattr(get_config().live_testing, "max_active_trades_per_node_default", 1) or 1),
        }

    # ------------------------------------------------------------------ stage log ---
    def _stage(self, ev: Dict[str, Any]) -> Dict[str, Any]:
        """Record one stage of the live-test lifecycle (spec §14)."""
        detail = ev.get("detail") or {}
        row = {"ts": time.time(), "node_id": ev.get("node_id"), "symbol": ev.get("symbol"),
               "side": (ev.get("side") or None), "stage": ev.get("stage"),
               "status": ev.get("status") or detail.get("status") or "OK",
               "detail": detail, "message": None}
        row["ts_iso"] = _iso(row["ts"])
        row["message"] = _human_stage_message(row["stage"], row)
        try:
            db = get_db()
            row["id"] = db.record_live_test_event({
                "ts": row["ts"], "node_id": row["node_id"], "symbol": row["symbol"],
                "side": row["side"], "stage": row["stage"], "status": row["status"],
                "message": row["message"],
                "detail_json": json.dumps(detail, default=str)[:4000]})
        except Exception as e:            # logging must never break execution
            log.warning("live-test stage log failed (%s): %s", row["stage"], e)
            row["id"] = None
        self.recent_events.insert(0, row)
        del self.recent_events[60:]
        try:
            bus.publish("live_test_stage", {k: row[k] for k in
                                            ("ts", "ts_iso", "node_id", "symbol", "side",
                                             "stage", "status", "message")})
        except Exception:
            pass
        log.info("LIVE TEST | %s | %s", row["stage"], row["message"])
        return row

    # ------------------------------------------------------------------ evaluation ---
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
        df["session"] = [session_of_hour(h) for h in df["hour"]]
        if bridge.source == "SIMULATOR":
            df = df[df["dow"] < 5].reset_index(drop=True)
        return df

    def _live_features(self, df: pd.DataFrame, specs: List[str]) -> Dict[str, np.ndarray]:
        cache: Dict[str, np.ndarray] = {}
        for spec in specs:
            if spec in cache:
                continue
            fam = library.feature_family(spec)
            if fam in ("close", "open", "high", "low"):
                cache[fam] = df[fam].to_numpy(np.float64)
                continue
            try:
                fn, args = library.parse_spec(library_feature_spec(fam, spec))
                cache.update(fn(df, *args))
            except Exception as e:
                log.warning("live-test feature %s failed: %s", spec, e)
        return cache

    def _cycle(self) -> None:
        if not self.active:
            self._last_noop_reason = "Live Testing is INACTIVE - no evaluation, no orders"
            return
        cfg = get_config()
        nodes = self.eligible_nodes()
        if not nodes:
            self._last_noop_reason = "no eligible live-test nodes configured"
            return
        # V6 — a node with its own dedicated live-testing worker is evaluated by
        # that worker only. The shared loop must never double-evaluate it: one
        # node = one live-testing worker = one order path (no duplicate orders).
        try:
            from .workers import get_worker_manager
            _mgr = get_worker_manager()
            owned = [n for n in nodes if _mgr.is_running(int(n.get("id")))]
            if owned:
                nodes = [n for n in nodes if not _mgr.is_running(int(n.get("id")))]
                if not nodes:
                    self._last_noop_reason = ("all eligible nodes are running under their own "
                                              "per-node live-testing workers")
                    return
        except Exception:
            pass
        self._last_noop_reason = None
        # pre-flight: broker state (spec §20) - never trade while disconnected, and
        # an interrupted session must never auto-resume when MT5 comes back.
        bridge = get_bridge()
        if not self._bridge_connected(bridge):
            self._last_noop_reason = "MT5 not connected - live trading unavailable"
            if self.active:
                self.deactivate(reason="MT5 disconnected - live trading unavailable "
                                       "(reactivate manually after reconnect)",
                                interrupted=True)
            return
        try:
            self._reconcile()
        except Exception as e:
            log.warning("live-test reconcile failed: %s", e)
        frames: Dict[Tuple[str, str], Optional[pd.DataFrame]] = {}
        for node in nodes:
            try:
                self._evaluate_node(node, bridge, frames)
            except Exception as e:            # one bad node must not stop the loop
                self.stats["loop_errors"] += 1
                log.exception("live-test node %s failed: %s", node["id"], e)

    def _evaluate_node(self, node: Dict[str, Any], bridge, frames: Dict) -> None:
        db = get_db()
        sid = node["id"]
        g = node["genome"] or {}
        symbol = str(g.get("symbol") or "").upper()
        tf = g.get("timeframe", "M15")
        ex = g.get("exit") or {}
        key = (symbol, tf)
        if key not in frames:
            frames[key] = self._live_frame(symbol, tf)
        df = frames.get(key)
        if df is None or df.empty:
            return
        n = len(df)
        last_closed_ts = float(df["ts"].iloc[-2])
        if self.last_signal_bar.get(sid) == last_closed_ts:
            return
        needed = list(referenced_features(g)) + ["close", "open", "high", "low"]
        needed.append(ex.get("atr_spec", "atr:14"))
        needed += [f"regime:{r}" for r in g.get("regime_filters") or []]
        cache = self._live_features(df, sorted(set(needed)))
        for k, v in list(cache.items()):
            if len(v) != n:
                cache[k] = v[-n:]
        ctx = _Ctx("live", cache, n)
        allow = np.ones(n, dtype=bool)
        sessions = g.get("sessions")
        if sessions:
            sess = df["session"].to_numpy()
            hour = df["hour"].to_numpy()
            mask = np.isin(sess, [x for x in sessions if x != "london_ny_overlap"])
            if "london_ny_overlap" in sessions:
                mask |= (hour >= 12) & (hour < 16)
            allow &= mask
        if g.get("days") is not None:
            allow &= np.isin(df["dow"].to_numpy(), list(g["days"]))
        for r in g.get("regime_filters") or []:
            arr = cache.get(f"regime:{r}")
            if arr is not None:
                allow &= np.nan_to_num(arr, nan=0.0) > 0.5
        atr = cache.get(ex.get("atr_spec", "atr:14"))
        i = n - 2                                   # last CLOSED bar
        if atr is None or not np.isfinite(atr[i]):
            self.last_signal_bar[sid] = last_closed_ts
            return
        direction = g.get("direction", "both")
        sig_l = sig_s = False
        try:
            if direction in ("both", "long") and g.get("entry_long"):
                sig_l = bool(self._backtester._eval(g["entry_long"], ctx)[i] and allow[i])
            if direction in ("both", "short") and g.get("entry_short"):
                sig_s = bool(self._backtester._eval(g["entry_short"], ctx)[i] and allow[i])
        except Exception as e:
            log.warning("live-test eval error #%s: %s", sid, e)
        self.last_signal_bar[sid] = last_closed_ts
        if not (sig_l or sig_s):
            return
        side = "BUY" if sig_l else "SELL"
        self.stats["signals"] += 1
        bar_time = datetime.fromtimestamp(last_closed_ts, tz=timezone.utc).isoformat(timespec="minutes")
        # V6.5 §3.2 — read-only explanation/metadata hook AT THE SIGNAL BOUNDARY:
        # the engine rule as stored, the condition tree with values evaluated at
        # the decision bar, and the indicator values. It never feeds back into the
        # decision (facts are recorded AFTER the signal is fixed).
        explain = self._explain_signal(g, cache, ctx, i, side, float(atr[i]),
                                       ex.get("atr_spec", "atr:14"), bar_time, tf)
        db.log_event("live_test_signal", {"strategy_id": sid, "side": side,
                                          "ts": last_closed_ts, "source": bridge.source})
        self._stage({"stage": STAGE_SIGNAL, "node_id": sid, "symbol": symbol, "side": side,
                     "status": "SIGNAL", "detail": {"bar_time": bar_time, "timeframe": tf,
                                                    "source": bridge.source,
                                                    "atr": round(float(atr[i]), 6),
                                                    "run_id": node["config"].get("run_id"),
                                                    "signal_id": explain.get("signal_id"),
                                                    "entry_reason": explain}})
        self._attempt(node, side, float(atr[i]), ex, df, explain=explain)

    # ------------------------------------------------------------------ execution ---
    def _attempt(self, node: Dict[str, Any], side: str, atr_val: float, ex: Dict[str, Any],
                 df: pd.DataFrame, explain: Optional[Dict[str, Any]] = None) -> None:
        """One order attempt with full staging; blocked/rejected/unknown are final."""
        sid = node["id"]
        g = node["genome"] or {}
        symbol = str(g.get("symbol") or "").upper()
        cfg = get_config()
        db = get_db()
        limits = risk_limits()
        mode = self.get_mode()

        # ---- V5 §11: the node's live schedule is enforced HERE, before any other
        # gate, with the engine's own trade history as its input. A schedule the
        # UI can display but the engine ignores is not a schedule; this is the one
        # place an order can be started, so the rule set lives here.
        from .schedule import evaluate as _eval_schedule
        try:
            _panel = self.market_panel(symbol)
            _spread_pts = None
            if _panel.get("ask") is not None and _panel.get("bid") is not None:
                _point = _panel.get("point") or 0.01
                _spread_pts = (float(_panel["ask"]) - float(_panel["bid"])) / float(_point)
            _regime = None
            try:
                _regime = self.current_regime(node)          # dominant label on the last closed bar
            except Exception:                                # a missing label never blocks by itself
                _regime = None
            _sched_eval = self.schedule_state(
                node, spread_points=_spread_pts, regime=_regime,
                open_positions=(self.count_activity() or {}).get("active_total", 0))
        except Exception as e:                      # never trade on an unevaluable schedule
            self._block(node, symbol, side, STAGE_ORDER_VALIDATED, "SCHEDULE_UNEVALUABLE",
                        f"the node's live schedule could not be evaluated ({type(e).__name__}: {e}) "
                        "- refusing to trade")
            return
        if not _sched_eval["allowed"]:
            self.stats["blocked"] += 1
            self._stage({"stage": STAGE_BLOCKED, "node_id": sid, "symbol": symbol, "side": side,
                         "status": "SCHEDULE_BLOCKED",
                         "detail": {"blocked_stage": STAGE_ORDER_VALIDATED,
                                    "reason": _sched_eval["reason"],
                                    "rules": _sched_eval["rules"],
                                    "local_time": _sched_eval["local_time"],
                                    "timezone": _sched_eval["timezone"]}})
            db.log_event("live_test_schedule_blocked",
                         {"strategy_id": sid, "symbol": symbol, "side": side,
                          "reason": _sched_eval["reason"]})
            return
        # §15 — the schedule's signal-condition switches are part of the schedule:
        # a component switched off must never be traded on, whatever the genome says.
        from .schedule import condition_enabled
        _cond = {"long": condition_enabled(node.get("config"), "entry_long"),
                 "short": condition_enabled(node.get("config"), "entry_short")}
        if (side or "").upper() == "BUY" and not _cond["long"]:
            self._block(node, symbol, side, STAGE_ORDER_VALIDATED, "CONDITION_DISABLED",
                        "the schedule switched this node's long entry rule OFF")
            return
        if (side or "").upper() == "SELL" and not _cond["short"]:
            self._block(node, symbol, side, STAGE_ORDER_VALIDATED, "CONDITION_DISABLED",
                        "the schedule switched this node's short entry rule OFF")
            return
        self._stage({"stage": STAGE_ORDER_VALIDATED, "node_id": sid, "symbol": symbol, "side": side,
                     "status": "SCHEDULE_OK",
                     "detail": {"local_time": _sched_eval["local_time"],
                                "timezone": _sched_eval["timezone"],
                                "regime": _sched_eval.get("regime"),
                                "timeframe": _sched_eval.get("timeframe"),
                                "conditions": _cond,
                                "rules": _sched_eval["rules"]}})

        # ---- safety gates (spec §3, §4, §10, §20) ----
        if not mode["active"]:
            self._block(node, symbol, side, STAGE_ORDER_VALIDATED, "LIVE_TESTING_INACTIVE",
                        "Live Testing is INACTIVE - signal recorded, no order placed")
            return
        bridge = get_bridge()
        if not self._bridge_connected(bridge):
            self._block(node, symbol, side, STAGE_MARKET, "MT5_DISCONNECTED",
                        "MT5 is not connected - live trading unavailable")
            return
        try:
            from ..mt5 import execution as mt5_exec
            estate = mt5_exec.execution_state()
        except Exception as e:
            self._block(node, symbol, side, STAGE_ORDER_VALIDATED, "ACCOUNT_SAFETY_UNKNOWN",
                        f"account safety state unavailable ({type(e).__name__}) - blocking")
            return
        if not estate.get("execution_allowed"):
            guard = estate.get("account_safety") or {}
            reason = guard.get("blocked_reason") or estate.get("blocked_reason") or \
                "account is not a positively identified DEMO account"
            self._block(node, symbol, side, STAGE_ORDER_VALIDATED,
                        estate.get("blocked_code") or "ACCOUNT_NOT_VERIFIED",
                        f"execution disabled: {reason}")
            return


        reuse = self._last_opened.get(symbol)
        if reuse is not None and (time.time() - reuse) < 60.0:
            self._block(node, symbol, side, STAGE_ORDER_VALIDATED, "LIVE_TEST_DUPLICATE_GUARD",
                        f"a live-test order for {symbol} was sent {int(time.time() - reuse)}s ago - "
                        "duplicate protection (no immediate re-entry)")
            return

        # A previous ambiguous outcome for this symbol blocks further attempts until
        # the operator verifies MT5 (spec §15: never auto-retry a timeout/unknown).
        unresolved_ts = self._unresolved.get(symbol)
        if unresolved_ts is not None:
            self._block(node, symbol, side, STAGE_ORDER_SENT, "UNRESOLVED_BROKER_STATE",
                        f"a previous live-test attempt for {symbol} ended "
                        f"UNKNOWN - VERIFY MT5 ({int(time.time() - unresolved_ts)}s ago); "
                        "confirm the broker state (VERIFY WITH MT5) before new orders",
                        {"retries": 0, "since_ts": unresolved_ts})
            return

        # V6.5 §5 — the admission gate runs AFTER the more specific re-entry
        # diagnostics above (duplicate guard, unresolved broker state) so the
        # operator sees the most actionable reason first; limits are still
        # enforced atomically at the execution boundary immediately after.
        # V6.5 §5 — the ADMISSION GATE: global AND per-node limits enforced at the
        # execution boundary, atomically (lock + reservation) so concurrent node
        # workers cannot jointly exceed a cap. Counts come from broker state plus
        # held reservations; unreadable broker state refuses (never "assume zero").
        activity = self.count_activity()
        if not activity.get("counted"):
            self._block(node, symbol, side, STAGE_ORDER_VALIDATED, "BROKER_STATE_UNAVAILABLE",
                        "could not read MT5 positions/orders - refusing to trade blind")
            return
        from .admission import get_admission
        from ..mt5.execution import LIVE_TEST_MAGIC_BASE, live_test_magic as _lt_magic
        from ..config import get_config as _get_cfg
        _cfg_live = _get_cfg().live_testing
        per_node_limit = resolve_per_node_limit(node)
        node_active = bool((node.get("config") or {}).get("is_active"))
        adm = get_admission().admit(
            node_id=sid, magic=_lt_magic(sid), symbol=symbol, bridge=bridge,
            magic_lo=LIVE_TEST_MAGIC_BASE, magic_hi=LIVE_TEST_MAGIC_BASE + 1000,
            global_limit=int(limits["max_active_trades"]),
            per_node_limit=int(per_node_limit["effective"]),
            node_active=node_active)
        reservation = None
        if not adm.get("ok"):
            self.stats["blocked"] += 1
            code = adm.get("code") or "LIMIT_REACHED"
            detail = {"blocked_stage": STAGE_ORDER_VALIDATED, "reason": adm.get("reason"),
                      "code": code, "active_total": adm.get("active_total", activity.get("active_total")),
                      "limit": adm.get("global_limit", limits["max_active_trades"]),
                      "active_node": adm.get("active_node"),
                      "node_limit": adm.get("node_limit", per_node_limit["effective"]),
                      "node_limit_source": per_node_limit["source"],
                      "positions": activity["positions"], "orders": activity["orders"],
                      "positions_closed": 0, "positions_replaced": 0}
            # the operator-facing contract keeps the documented wording
            # ("TRADE LIMIT REACHED"); the machine code names the exact gate.
            human = adm.get("reason") or ""
            if code == "GLOBAL_LIMIT_REACHED":
                human = (f"TRADE LIMIT REACHED - {detail.get('active_total')} active "
                         f"MT5 live-test trade(s) (limit {detail.get('limit')}); "
                         "no new order sent, no position closed or replaced")
            elif code == "NODE_LIMIT_REACHED":
                human = (f"TRADE LIMIT REACHED (node) - node {sid} is at its max active "
                         f"trades ({detail.get('active_node')}/{detail.get('node_limit')}, "
                         f"{per_node_limit['source']}); no new order sent")
            self._stage({"stage": STAGE_BLOCKED, "node_id": sid, "symbol": symbol, "side": side,
                         "status": "LIMIT_REACHED" if "LIMIT" in code else code,
                         "detail": {**detail, "reason": human}})
            db.record_live_test_execution({"strategy_id": sid, "symbol": symbol, "side": side,
                                           "timeframe": g.get("timeframe"), "status": TRADE_BLOCKED,
                                           "run_id": node["config"].get("run_id"),
                                           "result": detail})
            try:
                from . import ledger as _ledger
                _ledger.record_event(db, event_uid=f"block:{sid}:{int(time.time()*1000)}",
                                     event_type="ADMISSION_REFUSED", strategy_id=sid,
                                     symbol=symbol, side=side, status=code,
                                     payload=detail, source="admission")
            except Exception:
                pass
            return
        reservation = adm.get("reservation")

        # ---- market panel (authoritative MT5 quote + freshness) ----
        panel = self.market_panel(symbol)
        if not panel.get("trading_available"):
            self._block(node, symbol, side, STAGE_MARKET, "MARKET_DATA_NOT_TRADABLE",
                        "; ".join(panel.get("reasons") or ["market state is not tradable"]),
                        {"bid": panel.get("bid"), "ask": panel.get("ask"),
                         "tick_age_s": panel.get("tick_age_s"), "data_fresh": panel.get("data_fresh")})
            return
        entry = float(panel["ask"] if side == "BUY" else panel["bid"])
        spread = round(float(panel["ask"]) - float(panel["bid"]), 8)
        self._stage({"stage": STAGE_MARKET, "node_id": sid, "symbol": symbol, "side": side,
                     "status": "OK",
                     "detail": {"bid": panel["bid"], "ask": panel["ask"], "spread": spread,
                                "tick_age_s": panel["tick_age_s"], "source": panel.get("source"),
                                "session": panel.get("session_status")}})

        # ---- risk + volume (spec §7 - §9) ----
        sl_dist = float(ex.get("sl_atr_mult") or 0.0) * atr_val
        tp_dist = float(ex.get("tp_atr_mult") or 0.0) * atr_val
        if sl_dist <= 0:
            self._block(node, symbol, side, STAGE_RISK, "SL_MISSING",
                        "node exit model produced no stop-loss distance (sl_atr_mult=0)")
            return
        sl_price = round(entry - sl_dist, 8) if side == "BUY" else round(entry + sl_dist, 8)
        tp_price = None
        if tp_dist > 0:
            tp_price = round(entry + tp_dist, 8) if side == "BUY" else round(entry - tp_dist, 8)
        # V6.5 §9 — experimental per-node SL/TP offsets (default 0/0 = EXACTLY the
        # strategy's own levels). Applied to the already-computed default levels
        # only; never re-derives signal or structural levels. Positive SL offset
        # moves SL farther from entry; positive TP offset moves TP inward (§9.2);
        # negative offsets do the documented reverse. All adjusted levels are
        # tick-normalized and validated against broker constraints or the order
        # is safely refused (never submitted invalid).
        sl_default_lvl, tp_default_lvl = sl_price, tp_price
        offset_meta = {"sl_offset_pips": 0.0, "tp_offset_pips": 0.0,
                       "sl_default": sl_default_lvl, "tp_default": tp_default_lvl,
                       "sl_effective": sl_default_lvl, "tp_effective": tp_default_lvl,
                       "offsets_applied": False}
        _sl_off = node["config"].get("sl_offset_pips")
        _tp_off = node["config"].get("tp_offset_pips")
        try:
            _sl_off = float(_sl_off) if _sl_off not in (None, "") else 0.0
            _tp_off = float(_tp_off) if _tp_off not in (None, "") else 0.0
        except (TypeError, ValueError):
            self._block(node, symbol, side, STAGE_RISK, "OFFSET_INVALID",
                        "SL/TP offset values must be numeric (pips)")
            return
        offset_meta["sl_offset_pips"] = _sl_off
        offset_meta["tp_offset_pips"] = _tp_off
        if _sl_off or _tp_off:
            try:
                from ..backtest.symbol_specs import get_symbol_specs
                from .offsets import apply_offsets
                specs = get_symbol_specs(symbol)
                _si_chk = None
                try:
                    _si_chk = bridge.symbol_info(symbol)
                except Exception:
                    _si_chk = None
                res_off = apply_offsets(
                    side=side, entry=entry, sl=sl_price, tp=tp_price,
                    sl_offset_pips=_sl_off, tp_offset_pips=_tp_off, specs=specs,
                    stops_level_points=(getattr(_si_chk, "trade_stops_level", None)
                                        if _si_chk is not None else None),
                    freeze_level_points=(getattr(_si_chk, "trade_freeze_level", None)
                                         if _si_chk is not None else None))
                offset_meta.update(res_off.get("meta") or {})
                if not res_off.get("ok"):
                    self._block(node, symbol, side, STAGE_RISK,
                                str(res_off.get("code") or "OFFSET_ERROR"),
                                str(res_off.get("reason") or "offset application refused"),
                                dict(offset_meta))
                    return
                sl_price, tp_price = res_off["sl"], res_off["tp"]
                sl_dist = abs(entry - sl_price)
                tp_dist = abs(entry - tp_price) if tp_price is not None else 0.0
            except Exception as e:
                self._block(node, symbol, side, STAGE_RISK, "OFFSET_ERROR",
                            f"SL/TP offset application failed ({type(e).__name__}: {e}) - "
                            "refusing to submit")
                return
        try:
            si = bridge.symbol_info(symbol)
        except Exception:
            si = None
        try:
            # V6.5.1 §8 — the risk mode travels with the node's config: Mode A
            # (PERCENT of the configured capital basis) or Mode B (AMOUNT). With
            # the fields unset this is exactly the historical equity*% path.
            _acct = ((estate.get("account_safety") or {}).get("account") or {})
            trace = compute_risk(node_id=sid, strategy_id=sid, symbol=symbol, side=side,
                                 entry=entry, sl=sl_price,
                                 equity=_acct.get("equity"),
                                 balance=_acct.get("balance"),
                                 global_pct=limits["risk_pct_default"],
                                 override_pct=node["config"].get("risk_pct"),
                                 spec=si,
                                 risk_mode=node["config"].get("risk_mode"),
                                 risk_amount=node["config"].get("risk_amount"),
                                 capital_basis=node["config"].get("risk_capital_basis"))
        except RiskBlock as rb:
            stage = STAGE_VOLUME if rb.code.startswith("VOLUME") else STAGE_RISK
            self._block(node, symbol, side, stage, rb.code, rb.message, rb.detail)
            return
        self._stage({"stage": STAGE_RISK, "node_id": sid, "symbol": symbol, "side": side,
                     "status": "OK",
                     "detail": {k: trace[k] for k in ("equity", "risk_pct", "risk_pct_source",
                                                      "risk_amount", "global_risk_pct",
                                                      "risk_pct_max", "risk_mode",
                                                      "capital_basis", "capital")}})
        sz = trace["sizing"]
        self._stage({"stage": STAGE_VOLUME, "node_id": sid, "symbol": symbol, "side": side,
                     "status": "OK",
                     "detail": {k: sz.get(k) for k in ("volume", "stop_distance", "tick_size",
                                                       "tick_value", "volume_min", "volume_max",
                                                       "volume_step", "risk_per_lot",
                                                       "actual_risk", "raw_volume")}})

        # ---- order validation through the V4.2 path (spec §2: on top, not around) ----
        from ..mt5 import execution as mt5_exec
        magic = mt5_exec.live_test_magic(sid)
        payload = {"symbol": symbol, "side": side, "volume": sz["volume"], "sl": sl_price,
                   "tp": tp_price if tp_price else None, "price": entry, "strategy_id": sid,
                   "magic": magic, "comment": f"evolab-livetest-{sid}",
                   # The explicit human confirmation for live testing is the activation
                   # (§5: confirmation panel -> confirm -> ACTIVE); the loop never runs
                   # without it, and never retries on its own.
                   "confirm": mt5_exec.PLACE_CONFIRMATION}
        try:
            report = mt5_exec.validate_order_request(
                bridge, symbol=symbol, side=side, volume=sz["volume"], sl=sl_price,
                tp=(tp_price if tp_price else None), price=entry, strategy_id=sid,
                magic=magic, require_live=True)
        except Exception as e:
            self._block(node, symbol, side, STAGE_ORDER_VALIDATED, "VALIDATION_ERROR",
                        f"order validation failed: {type(e).__name__}: {e}")
            return
        if not report.get("ok"):
            self._block(node, symbol, side, STAGE_ORDER_VALIDATED,
                        "ORDER_VALIDATION_FAILED", report.get("reason") or "order validation failed",
                        {"errors": report.get("errors"), "checks": report.get("checks")})
            return
        self._stage({"stage": STAGE_ORDER_VALIDATED, "node_id": sid, "symbol": symbol, "side": side,
                     "status": "OK",
                     "detail": {"volume": sz["volume"], "price": entry, "sl": sl_price,
                                "tp": tp_price, "magic": magic, "checks": report.get("checks")}})

        # ---- send (single-flight, no retries) ----
        if not self._order_lock.acquire(blocking=False):
            self._block(node, symbol, side, STAGE_ORDER_SENT, "ORDER_IN_FLIGHT",
                        "another live-test order is already in flight - duplicate protection")
            return
        row_id = None
        try:
            with _MODE_LOCK:
                if not _MODE["active"]:
                    self._block(node, symbol, side, STAGE_ORDER_SENT, "LIVE_TESTING_INACTIVE",
                                "Live Testing was stopped - order not sent")
                    return
            self._stage({"stage": STAGE_ORDER_SENT, "node_id": sid, "symbol": symbol, "side": side,
                         "status": "SENT",
                         "detail": {"symbol": symbol, "volume": sz["volume"], "sl": sl_price,
                                    "tp": tp_price, "price": entry, "magic": magic}})
            self.stats["orders_sent"] += 1
            try:
                # V5.1a-next §C — the ONE order path: the V4.2 executor, which runs
                # mt5.order_check() and then bridge.send_market_order() ->
                # mt5.order_send(). The legacy bridge.real_market_order() helper is
                # NEVER used here (it speaks a different, unvalidated request shape).
                res = mt5_exec.place_demo_order(payload=payload)
            except mt5_exec.MT5ExecutionError as e:
                res = self._error_to_result(e)
            except Exception as e:                                 # unexpected -> UNKNOWN
                res = {"status": "UNKNOWN", "ok": False, "sent": None,
                       "broker": {"message": f"{type(e).__name__}: {e}",
                                  "safe_to_retry": False, "retcode": None},
                       "order": {}}
            status = str(res.get("status") or "UNKNOWN").upper()
            sent = res.get("sent")
            if sent is False and status in ("REJECTED",):
                # blocked before anything left the process -> record it as blocked
                self.stats["blocked"] += 1
                try:
                    from .admission import get_admission
                    get_admission().settle(reservation, "REJECTED")
                except Exception:
                    pass
                self._block(node, symbol, side, str(res.get("blocked_stage") or STAGE_ORDER_VALIDATED),
                            str(res.get("blocked_code") or "EXECUTION_BLOCKED"),
                            str((res.get("broker") or {}).get("message") or "execution blocked"))
                return
            self.stats["unknown"] += int(status == "UNKNOWN")
            broker = res.get("broker") or {}
            order = res.get("order") or {}
            execution = res.get("execution") or {}
            detail = {"status": status, "retcode": broker.get("retcode"),
                      "message": broker.get("message") or "n/a",
                      "reason": broker.get("comment") or broker.get("message"),
                      "safe_to_retry": bool(broker.get("safe_to_retry")),
                      "position_ticket": order.get("position_ticket"),
                      "order_ticket": order.get("ticket"),
                      "deal_ticket": order.get("deal_ticket"),
                      "exec_price": execution.get("exec_price"),
                      "sl_broker": execution.get("sl_broker"), "tp_broker": execution.get("tp_broker"),
                      "sl_tp_verified": execution.get("sl_tp_verified"),
                      "client_order_id": res.get("client_order_id"), "retries": 0,
                      # V5.1a-next §A/§C — the raw order_send facts travel with the
                      # live-test trace too, so "UNKNOWN" always carries its reason.
                      "result_class": res.get("result_class"),
                      "order_send": res.get("order_send"),
                      "diagnostic_phase": (res.get("diagnostic") or {}).get("phase"),
                      "diagnostic": res.get("diagnostic"),
                      "preflight": res.get("preflight"),
                      "next_actions": res.get("next_actions")}
            self._stage({"stage": STAGE_BROKER, "node_id": sid, "symbol": symbol, "side": side,
                         "status": status, "detail": detail})
            row_status, local = self._record_result(node, payload, trace, res, status,
                                                    explain=explain, offset_meta=offset_meta)
            row_id = local.get("row_id")
            # V6.5 §5.3.7 — reservation settlement follows the OUTCOME evidence:
            # a verified fill or a verified rejection releases the slot; an
            # UNCERTAIN outcome (timeout / UNKNOWN) KEEPS it until reconciliation.
            try:
                from .admission import get_admission
                if status == "UNKNOWN" or row_status in (TRADE_UNKNOWN, TRADE_UNCONFIRMED):
                    get_admission().settle(reservation, "UNCERTAIN")
                elif res.get("ok"):
                    get_admission().settle(reservation, "EXECUTED")
                else:
                    get_admission().settle(reservation, "REJECTED")
            except Exception:
                pass
            if status == "UNKNOWN":
                self._unresolved[symbol] = time.time()
            if status == "UNKNOWN" or (not res.get("ok") and res.get("safe_to_retry") is False
                                       and status not in ("REJECTED",)):
                self._stage({"stage": STAGE_UNKNOWN, "node_id": sid, "symbol": symbol, "side": side,
                             "status": "UNKNOWN",
                             "detail": {**detail, "row_id": row_id,
                                        "reason": (detail.get("reason") or detail.get("message")
                                                   or "broker response ambiguous")}})
            elif res.get("ok"):
                self._stage({"stage": STAGE_POSITION, "node_id": sid, "symbol": symbol,
                             "side": side, "status": row_status, "detail": detail})
                self.stats["positions_opened"] += int(row_status in (TRADE_POSITION, TRADE_PENDING))
                self._last_opened[symbol] = time.time()
            else:
                self._stage({"stage": STAGE_REJECTED, "node_id": sid, "symbol": symbol, "side": side,
                             "status": "REJECTED",
                             "detail": {**detail, "row_id": row_id,
                                        "reason": (detail.get("reason") or detail.get("message")
                                                   or "MT5 rejected the order")}})
            try:
                bus.publish("live_test_order", {"node_id": sid, "symbol": symbol, "side": side,
                                                "status": status, "ok": bool(res.get("ok")),
                                                "volume": sz["volume"], "ticket": res.get("position_ticket")})
            except Exception:
                pass
        finally:
            self._order_lock.release()

    def _block(self, node: Dict[str, Any], symbol: str, side: str, stage: str, code: str,
               message: str, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Record a blocked attempt. Never sends anything, never retries."""
        self.stats["blocked"] += 1
        detail = {"blocked_stage": stage, "code": code, "reason": message}
        detail.update(extra or {})
        self._stage({"stage": STAGE_BLOCKED, "node_id": node["id"], "symbol": symbol, "side": side,
                     "status": "BLOCKED", "detail": detail})
        try:
            db = get_db()
            db.record_live_test_execution({
                "strategy_id": node["id"], "symbol": symbol, "side": side,
                "timeframe": (node.get("genome") or {}).get("timeframe"),
                "status": TRADE_BLOCKED, "run_id": node["config"].get("run_id"),
                "risk_pct": node.get("_effective_risk_pct"), "result": detail})
        except Exception as e:
            log.warning("failed to record blocked live-test attempt: %s", e)
        try:
            bus.publish("live_test_blocked", {"node_id": node["id"], "symbol": symbol,
                                              "side": side, "stage": stage, "code": code,
                                              "reason": message})
        except Exception:
            pass
        return {"ok": False, "blocked": True, "code": code, "stage": stage, "message": message}

    @staticmethod
    def _error_to_result(e: Any) -> Dict[str, Any]:
        """Translate a V4.2 MT5ExecutionError into the engine's result shape.

        Nothing is retried (spec §15); failures after the send stage are reported
        as UNKNOWN so the operator verifies MT5 instead of the system guessing.
        """
        code = str(getattr(e, "code", "") or "EXECUTION_ERROR")
        stage = str(getattr(e, "stage", "") or "")
        message = str(getattr(e, "message", "") or e)
        posted = stage in ("SENDING", "INTERPRETING", "VERIFYING")
        details = getattr(e, "details", None) or {}
        if posted:
            return {"ok": False, "status": "UNKNOWN", "sent": True,
                    "broker": {"message": message, "retcode": None, "safe_to_retry": False,
                               "comment": f"{code} at {stage}"},
                    "order": {"position_ticket": None, "ticket": None, "deal_ticket": None},
                    "execution": {},
                    "result_class": details.get("result_class"),
                    "order_send": details.get("order_send"),
                    "diagnostic": details.get("diagnostic")}
        return {"ok": False, "status": "REJECTED", "sent": False, "blocked_code": code,
                "blocked_stage": {"VALIDATION": STAGE_ORDER_VALIDATED,
                                  "ACCOUNT_SAFETY": STAGE_ORDER_VALIDATED,
                                  "TRADE_CAPABILITY": STAGE_ORDER_VALIDATED,
                                  "DUPLICATE_GATE": STAGE_ORDER_SENT,
                                  "CONFIRMATION": STAGE_ORDER_SENT}.get(stage, STAGE_ORDER_VALIDATED),
                # V5.3 §1 — a pre-send capability block keeps its read-only evidence
                "preflight": details.get("trade_capability") or details.get("preflight"),
                "broker": {"message": message, "retcode": None, "safe_to_retry": False,
                           "comment": code},
                "order": {}, "execution": {}}

    # ---------------------------------------------------------- explanation ---
    def _explain_signal(self, g: Dict[str, Any], cache: Dict[str, Any], ctx: Any,
                        i: int, side: str, atr_val: float, atr_spec: str,
                        bar_time: str, tf: Any) -> Dict[str, Any]:
        """V6.5 §3.2 — FACTS recorded at decision time, nothing reconstructed.

        ``explanation_kind='engine_rule_and_feature_snapshot'`` marks exactly what
        this is: the engine's own entry rule (verbatim from the genome) plus the
        condition tree with each subtree's computed value at the decision bar,
        plus the indicator values it references.  Nothing here can change the
        signal's result — it is built after the boolean signal is fixed.
        """
        from ..genome.schema import describe as _describe
        rule = g.get("entry_long" if side == "BUY" else "entry_short")
        feature_values: Dict[str, Any] = {}
        for spec in sorted(referenced_features(g)):
            arr = cache.get(spec)
            v = None
            if arr is not None and len(arr) > i:
                try:
                    fv = float(arr[i])
                    v = fv if np.isfinite(fv) else None
                except Exception:
                    v = None
            feature_values[spec] = v
        tree = self._annotate_rule(rule, ctx, i) if rule is not None else None
        sig_id = f"sig-{g.get('timeframe')}-{int(time.time() * 1000)}"
        return {
            "signal_id": sig_id,
            "explanation_kind": "engine_rule_and_feature_snapshot",
            "recorded_at": time.time(),
            "recorded_at_iso": _iso(time.time()),
            "side": side,
            "entry_rule": rule,                       # verbatim engine input
            "entry_rule_text": _safe_describe(_describe, g),
            "condition_tree": tree,                   # per-node evaluated facts
            "indicator_values": feature_values,
            "atr": {"spec": atr_spec, "value": (float(atr_val) if atr_val is not None else None)},
            "signal_bar_time_utc": bar_time,
            "timeframe": tf,
            "direction": g.get("direction"),
            "sessions": g.get("sessions"),
            "days": g.get("days"),
            "regime_filters": g.get("regime_filters"),
            "genome_hash": g.get("_hash") or None,
            "note": ("facts recorded at decision time from the engine's own rule and "
                     "feature cache; thresholds are those stored in the rule"),
        }

    def _annotate_rule(self, node: Any, ctx: Any, i: int) -> Any:
        """Recursively attach computed values to a rule tree (read-only)."""
        if not isinstance(node, dict):
            return {"node": node, "evaluated": False}
        out: Dict[str, Any] = {"node": node}
        try:
            arr = self._backtester._eval(node, ctx)
            v = float(arr[i])
            out["value_at_bar"] = v if np.isfinite(v) else None
            out["evaluated"] = True
        except Exception as e:
            out["evaluated"] = False
            out["eval_error"] = f"{type(e).__name__}: {e}"[:120]
        if node.get("op") in ("and", "or"):
            out["clauses"] = [self._annotate_rule(c, ctx, i) for c in node.get("clauses") or []]
        elif node.get("op") == "not":
            out["clause"] = self._annotate_rule(node.get("clause"), ctx, i)
        elif node.get("type") == "compare":
            for key in ("left", "right"):
                v = node.get(key)
                if isinstance(v, str):
                    arr = ctx.cache.get(v) if hasattr(ctx, "cache") else None
                    try:
                        fv = float(arr[i])
                        out[key + "_value"] = fv if np.isfinite(fv) else None
                    except Exception:
                        out[key + "_value"] = None
        elif node.get("type") == "crossover":
            for key in ("a", "b"):
                arr = ctx.cache.get(node.get(key)) if hasattr(ctx, "cache") else None
                try:
                    fv = float(arr[i])
                    out[key + "_value"] = fv if np.isfinite(fv) else None
                except Exception:
                    out[key + "_value"] = None
        return out

    def _record_result(self, node: Dict[str, Any], payload: Dict[str, Any], trace: Dict[str, Any],
                       res: Dict[str, Any], status: str,
                       explain: Optional[Dict[str, Any]] = None,
                       offset_meta: Optional[Dict[str, Any]] = None) -> Tuple[str, Dict[str, Any]]:
        """Persist the canonical V6.5 ledger record (research statistics untouched, §18).

        The row is written from the ACTUAL broker response; fields MT5 did not
        provide stay NULL with an evidence source — never fabricated (§6.1)."""
        sid = node["id"]
        db = get_db()
        genome = node.get("genome") or {}
        if status == "POSITION_OPEN":
            row_status = TRADE_POSITION
        elif status == "PENDING_ORDER":
            row_status = TRADE_PENDING
        elif status == "REJECTED":
            row_status = TRADE_BLOCKED
        elif status in ("TIMEOUT_UNKNOWN",):
            row_status = TRADE_UNKNOWN
        else:
            row_status = TRADE_UNCONFIRMED
        order = res.get("order") or {}
        execution = res.get("execution") or {}
        broker = res.get("broker") or {}
        ticket = order.get("position_ticket") or order.get("ticket") or order.get("deal_ticket")
        if status == "UNKNOWN":
            row_status = TRADE_UNKNOWN
        row_id = None
        try:
            from . import ledger as _ledger
            off = offset_meta or {}
            entry_reason = None
            if explain is not None:
                try:
                    entry_reason = json.dumps(explain, default=str)
                except Exception:
                    entry_reason = None
            signal_ts = (explain or {}).get("recorded_at")
            now_ts = time.time()
            row_id = _ledger.upsert_trade(db, {
                "strategy_id": sid, "ticket": ticket,
                "position_ticket": order.get("position_ticket"),
                "order_ticket": order.get("ticket"),
                "deal_ticket": order.get("deal_ticket"),
                "comment": payload.get("comment"),
                "magic": payload.get("magic"),
                "strategy_identity": node["config"].get("run_id"),
                "signal_id": (explain or {}).get("signal_id"),
                "signal_ts": signal_ts, "submit_ts": now_ts, "response_ts": now_ts,
                "fill_ts": (now_ts if res.get("ok") else None),
                "order_type": "MARKET",
                "fill_status": row_status,
                "symbol": payload["symbol"],
                "timeframe": genome.get("timeframe", "M15"), "side": payload["side"],
                "entry_price": payload.get("price"),
                "exec_price": execution.get("exec_price") or payload.get("price"),
                "sl": payload.get("sl"), "tp": payload.get("tp"),
                "sl_requested": off.get("sl_default"), "tp_requested": off.get("tp_default"),
                "sl_confirmed": execution.get("sl_broker") or payload.get("sl"),
                "tp_confirmed": execution.get("tp_broker") or payload.get("tp"),
                "sl_default": off.get("sl_default"), "tp_default": off.get("tp_default"),
                "sl_offset_pips": off.get("sl_offset_pips") or 0.0,
                "tp_offset_pips": off.get("tp_offset_pips") or 0.0,
                "spread_points": execution.get("spread_points"),
                "slippage_points": execution.get("slippage_points"),
                "broker_comment": broker.get("comment") or broker.get("message"),
                "entry_reason": entry_reason,
                "account_login": broker.get("account_login"),
                "account_server": broker.get("account_server"),
                "volume_filled": execution.get("volume") or order.get("volume"),
                "volume_current": execution.get("volume") or order.get("volume"),
                "lots": payload["volume"],
                "open_ts": now_ts, "session": None, "status": row_status,
                "run_id": node["config"].get("run_id"), "risk_pct": trace.get("risk_pct"),
                "risk_amount": trace.get("risk_amount"),
                "client_order_id": res.get("client_order_id"), "retcode": broker.get("retcode"),
                "result": res,
                "evidence_source": ("broker_response" if (res.get("ok") or broker.get("retcode")
                                                          is not None)
                                    else "local_record_only"),
                "data_complete": 0 if row_status in (TRADE_UNKNOWN, TRADE_UNCONFIRMED) else 1,
                "last_sync_ts": now_ts,
            })
            if row_id is not None:
                uid_base = res.get("client_order_id") or f"{sid}-{int(now_ts * 1000)}"
                _ledger.record_event(
                    db, event_uid=f"submit:{uid_base}", event_type="ORDER_SUBMITTED",
                    ts=now_ts, strategy_id=sid,
                    position_ticket=order.get("position_ticket"),
                    order_ticket=order.get("ticket"), deal_ticket=order.get("deal_ticket"),
                    stage=STAGE_ORDER_SENT, status=status, symbol=payload["symbol"],
                    side=payload["side"],
                    payload={"retcode": broker.get("retcode"), "row_id": row_id,
                             "sl_default": off.get("sl_default"), "tp_default": off.get("tp_default"),
                             "sl_effective": payload.get("sl"), "tp_effective": payload.get("tp"),
                             "sl_offset_pips": off.get("sl_offset_pips"),
                             "tp_offset_pips": off.get("tp_offset_pips"),
                             "signal_id": (explain or {}).get("signal_id")},
                    source="live_engine")
        except Exception as e:
            log.warning("failed to persist live-test trade record: %s", e)
        return row_status, {"row_id": row_id, "status": row_status}

    # ------------------------------------------------------------------ monitoring ---
    def _reconcile(self) -> Dict[str, Any]:
        """Verify recorded live-test trades against the broker (spec §13, §15).

        - recorded-but-unverified rows are promoted to POSITION_OPEN / PENDING_ORDER
          when the broker really holds them;
        - rows the broker never confirms stay EXECUTED_UNCONFIRMED (or UNKNOWN after
          a grace period) so the operator is told to verify MT5 - never auto-retried;
        - rows that vanished from the broker are marked CLOSED_MISSING (lifecycle only;
          research state is untouched, spec §17).
        """
        db = get_db()
        out = {"checked": 0, "verified": 0, "closed": 0, "unknown": 0}
        try:
            rows = db.q("SELECT * FROM live_test_trades WHERE status IN (?,?,?,?) "
                        "ORDER BY id DESC LIMIT 200",
                        (TRADE_UNCONFIRMED, TRADE_POSITION, TRADE_PENDING, TRADE_UNKNOWN))
        except Exception:
            return out
        if not rows:
            return out
        bridge = get_bridge()

        def _get(row: Any, key: str, default: Any = None) -> Any:
            return row.get(key, default) if isinstance(row, dict) else getattr(row, key, default)

        def _alive(ticket: Any, deal: Any, cid: Any, magic: Any, symbol: Any) -> Optional[str]:
            """'' = gone, None = cannot tell (leave the record alone), else the status."""
            try:
                for p in bridge.positions_get() or []:
                    if (ticket and int(_get(p, "ticket", -1) or -1) == int(ticket)) or \
                       (cid and str(cid) in str(_get(p, "comment", "") or "") and
                            _get(p, "symbol") == symbol):
                        return TRADE_POSITION
                for o in bridge.orders_get() or []:
                    if (ticket and int(_get(o, "ticket", -1) or -1) == int(ticket)) or \
                       (magic and int(_get(o, "magic", 0) or 0) == int(magic) and
                            _get(o, "symbol") == symbol):
                        return TRADE_PENDING
            except Exception as e:
                log.warning("reconcile query failed: %s", e)
                return None
            return ""

        self._clear_verified_unknowns(db, bridge, out)
        # V6.5 §5.3.8 — reconcile LIMIT RESERVATIONS with the broker state too:
        # an uncertain slot the broker proves unused is released here.
        try:
            from .admission import get_admission
            from ..mt5.execution import LIVE_TEST_MAGIC_BASE
            out["reservations"] = get_admission().reconcile(
                bridge, LIVE_TEST_MAGIC_BASE, LIVE_TEST_MAGIC_BASE + 1000)
        except Exception as e:
            log.warning("admission reconcile failed: %s", e)
        self._recover_broker_positions(db, bridge, out)

        for r in rows:
            out["checked"] += 1
            state = _alive(r.get("ticket"), r.get("deal_ticket"), r.get("client_order_id"),
                           r.get("magic"), r.get("symbol"))
            if state is None:
                continue
            if state in (TRADE_POSITION, TRADE_PENDING):
                if state != r["status"]:
                    db.update_live_test_trade(r["id"], status=state)
                    self._stage({"stage": STAGE_POSITION, "node_id": r["strategy_id"],
                                 "symbol": r["symbol"], "side": r["side"], "status": state,
                                 "detail": {"status": state, "position_ticket": r.get("ticket"),
                                            "verified_by": "broker reconciliation"}})
                out["verified"] += 1
            elif state == "":
                age = time.time() - float(r.get("ts") or 0)
                if r["status"] in (TRADE_POSITION, TRADE_PENDING):
                    # V6.5 §6.3 — before the row is closed out, capture the ACTUAL
                    # exit evidence (exit deals, realized P/L, broker reason) from
                    # deal history. No deal history ⇒ the row keeps an explicit
                    # 'evidence unavailable' classification — never invented exits.
                    exit_info = self._capture_exit(db, bridge, r)
                    close_reason = exit_info.get("close_reason") or "closed at broker"
                    db.update_live_test_trade(r["id"], status=TRADE_CLOSED_MISSING,
                                              close_ts=exit_info.get("exit_ts") or time.time(),
                                              close_reason=close_reason)
                    out["closed"] += 1
                    if exit_info.get("captured"):
                        out["exits_captured"] = out.get("exits_captured", 0) + 1
                    self._stage({"stage": STAGE_CLOSED, "node_id": r["strategy_id"],
                                 "symbol": r["symbol"], "side": r["side"], "status": TRADE_CLOSED_MISSING,
                                 "detail": {"reason": "position/order no longer present at the broker",
                                            "position_closed_by": "broker/external",
                                            "exit_evidence": exit_info,
                                            "note": "no automatic re-entry"}})
                elif r["status"] == TRADE_UNCONFIRMED and age > 45:
                    db.update_live_test_trade(r["id"], status=TRADE_UNKNOWN)
                    out["unknown"] += 1
                    self._stage({"stage": STAGE_UNKNOWN, "node_id": r["strategy_id"],
                                 "symbol": r["symbol"], "side": r["side"], "status": "UNKNOWN",
                                 "detail": {"reason": "broker reply was OK but the position was not "
                                                      "found when verified - UNKNOWN - VERIFY MT5",
                                            "retries": 0}})
        # V6.5 §6.5 — the synchronization state is part of the outcome, always.
        self.sync_state.update({
            "last_sync_ts": time.time(),
            "positions_reconciled": out.get("verified", 0),
            "closed_detected": out.get("closed", 0),
            "exits_captured": out.get("exits_captured", 0),
            "recovered": out.get("recovered", 0),
            "missing_or_unmatched": out.get("missing", 0),
            "unknown_rows": out.get("unknown", 0),
            "errors": out.get("errors", 0),
            "checked_rows": out.get("checked", 0),
        })
        self.sync_state["certainty"] = ("SUFFICIENT" if not out.get("errors")
                                        else "INSUFFICIENT")
        return out

    # V6.5 §6.3 — MT5 source-of-truth exit capture (read-only) ---------------
    #: MetaTrader5 deal-entry / deal-reason codes (stable broker semantics).
    _DEAL_ENTRY_OUT = (1, 3)          # DEAL_ENTRY_OUT, DEAL_ENTRY_OUT_BY
    _DEAL_REASON_SL = 4
    _DEAL_REASON_TP = 5
    _DEAL_REASON_SO = 6

    def _capture_exit(self, db, bridge, row: Dict[str, Any]) -> Dict[str, Any]:
        """Capture exit-deal evidence for a position that vanished from the
        broker. Evidence only — a missing deal history is recorded as missing,
        never fabricated (§6.3 last paragraph)."""
        from . import ledger as _ledger
        info: Dict[str, Any] = {"captured": False, "deals": 0, "close_reason": None,
                                "exit_reason_source": None, "exit_ts": None,
                                "exit_price": None, "gross_pnl": None, "net_pnl": None,
                                "close_kind": None, "evidence_source": "deal_history"}
        ticket = row.get("ticket")
        deals: List[Dict[str, Any]] = []
        try:
            getter = getattr(bridge, "deal_history", None)
            if getter and ticket:
                deals = getter(int(ticket)) or []
        except Exception as e:
            info["evidence_source"] = "deal_history_unavailable"
            info["error"] = f"{type(e).__name__}: {e}"
        exit_deals = [d for d in deals
                      if int(d.get("entry") or 0) in self._DEAL_ENTRY_OUT]
        info["deals"] = len(exit_deals)
        if not exit_deals:
            info["close_reason"] = "closed at broker (exit deal not retrievable)"
            info["evidence_source"] = "position_absent"
            info["close_kind"] = "UNKNOWN"
            return info
        gross = commission = swap = fees = 0.0
        vol = 0.0
        price_acc = 0.0
        reason_codes = set()
        for d in exit_deals:
            gross += float(d.get("profit") or 0.0)
            commission += float(d.get("commission") or 0.0)
            swap += float(d.get("swap") or 0.0)
            fees += float(d.get("fee") or 0.0)
            vol += float(d.get("volume") or 0.0)
            price_acc += float(d.get("price") or 0.0) * float(d.get("volume") or 0.0)
            reason_codes.add(int(d.get("reason") if d.get("reason") is not None else -1))
        exit_price = (price_acc / vol) if vol > 0 else (exit_deals[-1].get("price"))
        exit_ts = max((int(d.get("time") or 0) for d in exit_deals), default=0) or None
        # classify BY BROKER REASON evidence; anything else is UNKNOWN/UNCONFIRMED
        if self._DEAL_REASON_SL in reason_codes:
            close_reason, src = "SL_EXIT", "broker_deal_reason"
        elif self._DEAL_REASON_TP in reason_codes:
            close_reason, src = "TP_EXIT", "broker_deal_reason"
        elif self._DEAL_REASON_SO in reason_codes:
            close_reason, src = "STOP_OUT", "broker_deal_reason"
        elif reason_codes and reason_codes <= {0, 1, 2, 3}:
            close_reason, src = "MANUAL_OR_EXPERT_CLOSE", "broker_deal_reason"
        else:
            close_reason, src = "UNKNOWN_EXIT", "broker_deal_unclassified"
        try:
            open_vol = float(row.get("lots") or row.get("volume_filled") or 0.0) or None
            full = (open_vol is None) or (vol + 1e-9 >= open_vol)
        except Exception:
            full = True
        info.update({"captured": True, "close_reason": close_reason,
                     "exit_reason_source": src, "exit_ts": exit_ts,
                     "exit_price": exit_price, "gross_pnl": gross,
                     "net_pnl": round(gross + commission + swap + fees, 8),
                     "close_kind": "FULL" if full else "PARTIAL",
                     "commission": commission, "swap": swap, "fees": fees,
                     "closed_volume": vol,
                     "exit_deal_tickets": [d.get("ticket") for d in exit_deals]})
        try:
            _ledger.update_trade(
                db, int(row["id"]),
                exit_price=exit_price,
                exit_deal_ticket=(exit_deals[-1].get("ticket") or None),
                exit_order_ticket=(exit_deals[-1].get("order") or None),
                exit_reason=close_reason, exit_reason_source=src,
                gross_pnl=gross, commission=commission, swap=swap, fees=fees,
                net_pnl=info["net_pnl"], closed_volume=vol,
                close_kind=info["close_kind"],
                duration_s=(float(exit_ts) - float(row.get("open_ts") or exit_ts))
                            if exit_ts else None,
                evidence_source="broker_deal_history", data_complete=1,
                pnl=gross)
            # realized net P/L also lands in the legacy pnl column consumers read
            db.update_live_test_trade(int(row["id"]), exit_price=exit_price,
                                      close_reason=close_reason)
            for d in exit_deals:
                _ledger.record_event(
                    db, event_uid=f"deal:{int(d.get('ticket') or 0)}",
                    event_type="EXIT_DEAL", ts=float(d.get("time") or time.time()),
                    strategy_id=row.get("strategy_id"),
                    position_ticket=int(ticket) if ticket else None,
                    order_ticket=d.get("order"), deal_ticket=d.get("ticket"),
                    stage=STAGE_CLOSED, status=close_reason,
                    symbol=row.get("symbol"), side=row.get("side"),
                    payload={"deal": d, "classification": close_reason,
                             "classification_source": src},
                    source="broker_deal_history")
        except Exception as e:
            info["error"] = f"persist failed: {type(e).__name__}: {e}"
        return info

    def _recover_broker_positions(self, db, bridge, out: Dict[str, Any]) -> None:
        """V6.5 §6.5 — backfill ledger rows for managed positions the broker
        holds but no local record covers (e.g. trades that happened while the
        app was down). Facts come from the broker; unknown fields stay NULL."""
        from . import ledger as _ledger
        try:
            from ..mt5.execution import LIVE_TEST_MAGIC_BASE
            positions = bridge.positions_get() or []
        except Exception as e:
            out["errors"] = out.get("errors", 0) + 1
            out["error_detail"] = f"{type(e).__name__}: {e}"
            return
        try:
            known = {r.get("ticket") for r in
                     (db.q("SELECT ticket FROM live_test_trades "
                           "WHERE status NOT IN ('REJECTED','BLOCKED')") or [])}
            rows_all = db.q("SELECT id, ticket, client_order_id, status FROM live_test_trades") or []
        except Exception:
            rows_all = []
        def _covered(p: Dict[str, Any]) -> bool:
            t = p.get("ticket")
            if t in known:
                return True
            cmt = str(p.get("comment") or "")
            for r in rows_all:
                if r.get("ticket") and int(r["ticket"]) == int(t or -1):
                    return True
                cid = r.get("client_order_id")
                if cid and str(cid) in cmt:
                    return True
            return False
        for p in positions:
            try:
                m = int(p.get("magic") or 0)
                if not (LIVE_TEST_MAGIC_BASE <= m < LIVE_TEST_MAGIC_BASE + 1000):
                    continue
                if _covered(p):
                    continue
                row_id = _ledger.upsert_trade(db, {
                    "strategy_id": m - LIVE_TEST_MAGIC_BASE,
                    "ticket": p.get("ticket"), "position_ticket": p.get("ticket"),
                    "symbol": p.get("symbol"), "side": ("BUY" if int(p.get("type") or 0) == 0
                                                        else "SELL"),
                    "entry_price": p.get("price_open"),
                    "exec_price": p.get("price_open"),
                    "sl": p.get("sl"), "tp": p.get("tp"), "lots": p.get("volume"),
                    "volume_current": p.get("volume"), "volume_filled": p.get("volume"),
                    "open_ts": float(p.get("time") or 0) or None,
                    "status": TRADE_POSITION, "magic": m,
                    "comment": p.get("comment"),
                    "evidence_source": "broker_recovered",
                    "data_complete": 0,
                    "entry_reason": json.dumps({
                        "explanation_kind": "broker_recovered_no_local_record",
                        "note": ("position existed at the broker with no local ledger row "
                                 "(e.g. opened while the app was down); entry facts come "
                                 "from the broker, signal explanation unavailable")}),
                    "last_sync_ts": time.time()})
                out["recovered"] = out.get("recovered", 0) + 1
                if row_id is not None:
                    _ledger.record_event(
                        db, event_uid=f"recover:{int(p.get('ticket') or 0)}",
                        event_type="BROKER_POSITION_RECOVERED",
                        ts=time.time(), strategy_id=m - LIVE_TEST_MAGIC_BASE,
                        position_ticket=p.get("ticket"),
                        stage=STAGE_POSITION, status=TRADE_POSITION,
                        symbol=p.get("symbol"),
                        payload={"position": p, "row_id": row_id},
                        source="broker_reconciliation")
                    self._stage({"stage": STAGE_POSITION, "node_id": m - LIVE_TEST_MAGIC_BASE,
                                 "symbol": p.get("symbol"), "side": None,
                                 "status": "RECOVERED",
                                 "detail": {"position_ticket": p.get("ticket"),
                                            "note": "broker position with no local record — "
                                                    "ledger row created from broker facts"}})
            except Exception as e:
                out["errors"] = out.get("errors", 0) + 1
                log.warning("broker-position recovery failed for %s: %s",
                            p.get("ticket"), e)

    def _clear_verified_unknowns(self, db, bridge, out: Dict[str, Any],
                                 grace_s: float = 60.0) -> None:
        """Release the UNKNOWN gate once MT5 has been checked and shows nothing.

        The audit record keeps its UNKNOWN status; only the *blocking* state is
        cleared, and only when the attempt is older than the grace period and the
        broker holds no matching position or order (spec §15).
        """
        for sym in list(self._unresolved.keys()):
            try:
                rows = db.q("SELECT id, open_ts FROM live_test_trades WHERE symbol=? "
                            "AND status IN (?,?)", (sym, TRADE_UNKNOWN, TRADE_UNCONFIRMED))
                if not rows:
                    self._unresolved.pop(sym, None)
                    continue
                if any(time.time() - float(r.get("open_ts") or 0) <= grace_s for r in rows):
                    continue
                try:
                    present = bool(bridge.positions_get(symbol=sym)) or bool(bridge.orders_get(symbol=sym))
                except Exception:
                    continue
                if present:
                    continue                      # a real position exists -> reconcile promoted it
                self._unresolved.pop(sym, None)
                out["unknown_cleared"] = out.get("unknown_cleared", 0) + 1
                self._stage({"stage": STAGE_INFO, "node_id": None, "symbol": sym, "side": None,
                             "status": "VERIFIED",
                             "detail": {"summary": f"{sym}: previous ambiguous attempt verified "
                                                   "against MT5 - no matching position or order, "
                                                   "the record stays UNKNOWN for audit"}})
            except Exception as e:
                log.warning("unresolved-state check failed for %s: %s", sym, e)

    # ------------------------------------------------------------------ test hooks ---
    def reset(self) -> None:
        try:
            from .admission import get_admission
            get_admission().reset()
        except Exception:
            pass
        """Test hook: hard reset of in-memory state (never touches MT5 or DATA)."""
        with _MODE_LOCK:
            _MODE.update({"active": False, "activated_at": None, "deactivated_at": None,
                          "stop_reason": None, "activation": None})
        with self._lock:
            self.last_signal_bar.clear()
            self._last_opened.clear()
            self._unresolved.clear()
            self._cycle_count = 0
            self._last_cycle_ts = None
            self._last_cycle_error = None
            self._last_noop_reason = None
            self.recent_events = []
            for k in self.stats:
                self.stats[k] = 0


_ENGINE: Optional[LiveTestingEngine] = None
_ENGINE_LOCK = threading.Lock()


def get_live_testing_engine() -> LiveTestingEngine:
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is None:
            _ENGINE = LiveTestingEngine()
        return _ENGINE
