"""V4.3 focused tests — controlled live testing, risk controls & monitoring (spec §23).

Safety of the test setup:

* the MT5 terminal is the **same test double the V4.2 suite uses** (imported from
  ``tests/test_v4_2_mt5_execution.py`` and extended with the V4.3 symbol-spec /
  bar-feed surface), injected at the *bridge boundary* so the product code path
  (risk -> volume -> validation -> V4.2 execution -> verification -> logging)
  runs unchanged;
* persistence is redirected to a temporary SQLite database, so no authoritative
  research row (and no DATA file) is written by this module;
* no test ever claims a real broker trade: everything is either a calculation,
  a blocked path, or a call into the double. The real MT5 demo checks live in
  ``scripts/verify_mt5_demo_execution.py`` and must run on the Windows host.

Covered cases 1-22 of spec §23 in order.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import time
from pathlib import Path

import pytest

from app.db.database import Database
from app.live_testing import engine as eng_mod
from app.live_testing import risk as risk_mod
from app.live_testing.engine import LiveTestingEngine
from app.live_testing.risk import RiskBlock, compute_risk, compute_volume, resolve_risk_pct
from app.mt5 import execution as ex
from app.mt5.bridge import Bar


# --------------------------------------------------------------------------
# reuse the V4.2 test double (single stand-in for the Windows MT5 terminal)
# --------------------------------------------------------------------------
def _load_v42_double():
    p = Path(__file__).resolve().parent / "test_v4_2_mt5_execution.py"
    spec = importlib.util.spec_from_file_location("v42_double", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_v42 = _load_v42_double()
FakeMT5Terminal = _v42.FakeMT5Terminal
FakeMT5Bridge = _v42.FakeMT5Bridge

SYMBOL = "XAUUSD"
_TF_SECONDS = 900


def make_bars(n: int = 200, *, start: float = 2400.0, ts_end: float | None = None) -> list:
    """Deterministic M15 candles around 2400 with a usable ATR."""
    ts_end = ts_end if ts_end is not None else (int(time.time() // _TF_SECONDS) * _TF_SECONDS)
    bars = []
    price = start - 4.0
    for i in range(n):
        drift = 0.02 * i
        wave = 0.6 * ((i % 8) - 3.5) / 3.5
        close = start + wave + drift
        open_ = price
        high = max(open_, close) + 0.55
        low = min(open_, close) - 0.55
        bars.append(Bar(ts=float(ts_end - (n - i) * _TF_SECONDS), open=open_, high=high,
                        low=low, close=close, tick_volume=100.0 + (i % 7)))
        price = close
    return bars


class LiveTestBridge(FakeMT5Bridge):
    """V4.2 double + the V4.3 sizing/market surface (tick value, bars)."""

    def __init__(self, term, **kw):
        super().__init__(term, **kw)
        self.trade_tick_size = 0.01
        self.trade_tick_value = 1.0
        self.currency_profit = "USD"
        self.bars = make_bars()
        self.send_calls = 0

    def symbol_info(self, symbol):
        si = super().symbol_info(symbol)
        if si is None:
            return None
        si.trade_tick_size = self.trade_tick_size
        si.trade_tick_value = self.trade_tick_value
        si.currency_profit = self.currency_profit
        return si

    def copy_rates(self, symbol, timeframe, n_bars):
        return self.bars[-int(n_bars):]

    def send_market_order(self, request):
        self.send_calls += 1
        return super().send_market_order(request)


def genome(side: str = "BUY", *, symbol: str = SYMBOL, timeframe: str = "M15",
           sl_mult: float = 1.5, tp_mult: float = 2.0) -> dict:
    cond = {"type": "compare", "left": "close", "cmp": ">", "right": 0}
    return {
        "symbol": symbol, "timeframe": timeframe,
        "direction": "long" if side == "BUY" else "short",
        "features": [],
        "entry_long": cond if side == "BUY" else None,
        "entry_short": None if side == "BUY" else cond,
        "exit": {"atr_spec": "atr:14", "sl_atr_mult": sl_mult, "tp_atr_mult": tp_mult,
                 "trailing": None, "min_hold_bars": 1, "max_hold_bars": 96,
                 "exit_condition": None},
        "sessions": None, "days": None, "regime_filters": None,
        "risk": {"risk_per_trade": 0.005, "max_concurrent": 1},
    }


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
@pytest.fixture
def term():
    return FakeMT5Terminal()


@pytest.fixture
def bridge(term):
    return LiveTestBridge(term)


@pytest.fixture
def db(tmp_path):
    """Scratch database holding the node/config rows used by these tests."""
    d = Database(str(tmp_path / "v43_live_test.db"))
    d.x("DELETE FROM live_test_configs")
    d.x("DELETE FROM live_test_trades")
    return d


@pytest.fixture
def live_cfg(monkeypatch):
    from app.config import get_config
    cfg = copy.deepcopy(get_config())
    cfg.live_testing.risk_pct_default = 1.0
    cfg.live_testing.risk_pct_max = 2.0
    cfg.live_testing.max_active_trades = 1
    cfg.live_testing.tick_interval_s = 0.05
    cfg.live_testing.max_data_age_s = 120
    cfg.live_testing.require_sl = True
    monkeypatch.setattr(eng_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(risk_mod, "get_config", lambda: cfg)
    return cfg


@pytest.fixture
def mt5_env(monkeypatch, bridge, term, db):
    """Wire the double + scratch DB into the product code path."""
    import app.db.database as database_mod
    import app.mt5.factory as factory
    import app.mt5.mt5_real as real

    # every lookup goes to the scratch database: the authoritative DATA database
    # is never opened, read or written by this module
    monkeypatch.setattr(database_mod, "get_db", lambda: db)
    monkeypatch.setattr(factory, "get_bridge", lambda: bridge)
    monkeypatch.setattr(real, "MT5_PACKAGE_AVAILABLE", True)
    monkeypatch.setattr(ex, "mt5_module", lambda: term)
    monkeypatch.setattr(ex, "_db", lambda: db)
    monkeypatch.setattr(eng_mod, "get_db", lambda: db)
    monkeypatch.setattr(eng_mod, "get_bridge", lambda: bridge)
    return bridge


def add_node(db, node_id: int, *, status: str = "SURVIVED", data_source: str = "USER_RESEARCH",
             side: str = "BUY", symbol: str = SYMBOL, **genome_over) -> int:
    g = genome(side, symbol=symbol, **genome_over)
    db.x("""INSERT OR REPLACE INTO strategies
            (id, hash, parent_id, generation, symbol, timeframe, direction, status,
             genome, complexity, fitness, created_at, updated_at, origin, run_id, data_source,
             research_node_num)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
         (node_id, f"hash{node_id}", None, 39, symbol, g["timeframe"], g["direction"], status,
          json.dumps(g), 3, 1.5, time.time(), time.time(), "research",
          "RUN-20261005-055251", data_source, node_id))
    return node_id


def enable_node(db, node_id: int, *, risk_pct=0.5, is_active=1) -> None:
    db.set_live_test_config(node_id, {"risk_pct": risk_pct, "is_active": bool(is_active),
                                      "timeframes": ["M15"], "days": None, "sessions": None})


def fresh_engine() -> LiveTestingEngine:
    e = LiveTestingEngine()
    e.reset()
    return e


def stages(engine) -> list:
    return [(ev["stage"], ev["status"]) for ev in reversed(engine.recent_events)]


def stage_names(engine) -> list:
    return [s for s, _ in stages(engine)]


def last_stage(engine) -> dict:
    return engine.recent_events[0]


# ==========================================================================
# 1-3. Live Testing starts INACTIVE / never auto-resumes
# ==========================================================================
def test_01_live_testing_starts_inactive(db, live_cfg, mt5_env):
    eng = fresh_engine()
    assert eng.active is False
    assert eng.get_mode()["mode"] == "INACTIVE"
    assert eng.status()["mode"] == "INACTIVE"
    # no persisted flag could re-arm it: the config has no "enabled" field
    assert not hasattr(live_cfg.live_testing, "enabled")
    assert not hasattr(live_cfg.live_testing, "active")


def test_02_refresh_or_new_session_does_not_reactivate(db, live_cfg, mt5_env):
    add_node(db, 501)
    enable_node(db, 501)
    eng = fresh_engine()
    assert eng.activate(confirmed=True)["ok"] is True
    assert eng.active is True
    # a page refresh / new process only re-reads the database: nothing there is ACTIVE
    reloaded = fresh_engine()
    assert reloaded.active is False
    assert reloaded.get_mode()["mode"] == "INACTIVE"
    assert not hasattr(reloaded, "_persisted_active")
    # the pre-existing per-node toggle (is_active=1) is a *scheduler* flag, not activation
    assert db.one("SELECT is_active FROM live_test_configs WHERE strategy_id=501")["is_active"] == 1


def test_03_backend_restart_does_not_reactivate(db, live_cfg, mt5_env):
    add_node(db, 502)
    enable_node(db, 502)
    eng = fresh_engine()
    eng.activate(confirmed=True)
    assert eng.active is True
    eng.stop(reason="restart")            # shutdown path
    assert eng.active is False
    assert eng.start()["ok"] is True       # startup path
    assert eng.active is False
    assert eng.get_mode()["mode"] == "INACTIVE"
    assert eng.get_mode()["previously_active"] is True   # display-only distinction
    eng.stop()


# ==========================================================================
# 4-5. account safety and MT5 availability
# ==========================================================================
def test_04_non_demo_account_blocks_execution(db, live_cfg, mt5_env, term):
    add_node(db, 504)
    enable_node(db, 504)
    term.trade_mode = 2                                   # REAL account
    eng = fresh_engine()
    eng.activate(confirmed=True)
    eng._cycle()
    assert term.sent == []                                # nothing reached the broker
    ev = last_stage(eng)
    assert ev["stage"] == "BLOCKED"
    assert ev["detail"]["code"] == "NON_DEMO_ACCOUNT"
    assert ev["status"] == "BLOCKED"
    assert db.q("SELECT * FROM live_test_trades WHERE status='BLOCKED'")


def test_05_mt5_unavailable_blocks_execution(db, live_cfg, mt5_env, term):
    add_node(db, 505)
    enable_node(db, 505)
    term.trade_mode = None                                # unknown -> never guessed
    eng = fresh_engine()
    eng.activate(confirmed=True)
    eng._cycle()
    assert term.sent == []
    assert last_stage(eng)["detail"]["code"] == "ACCOUNT_MODE_UNKNOWN"
    # and a bridge that is not a real terminal can never execute
    from app.mt5.simulator import SimulatorBridge
    guard = ex.demo_account_guard(SimulatorBridge())
    assert guard["demo_verified"] is False and guard["blocked_code"] == "MT5_UNAVAILABLE"


# ==========================================================================
# 6-10. risk calculation, limits and overrides
# ==========================================================================
def test_06_risk_calculation_returns_expected_volume():
    spec = {"trade_tick_size": 0.01, "trade_tick_value": 1.0, "volume_min": 0.01,
            "volume_max": 50.0, "volume_step": 0.01, "digits": 2, "trade_contract_size": 100.0}
    out = compute_volume(symbol=SYMBOL, side="BUY", entry=2400.0, sl=2398.5,
                         risk_amount=100.0, spec=spec)
    # risk per lot = 1.5 / 0.01 * 1.0 = 150 -> 100/150 = 0.6667 -> 0.66 lots (step 0.01)
    assert out["risk_per_lot"] == pytest.approx(150.0)
    assert out["volume"] == pytest.approx(0.66)
    assert out["actual_risk"] == pytest.approx(99.0)
    trace = compute_risk(node_id=1, strategy_id=1, symbol=SYMBOL, side="BUY", entry=2400.0,
                         sl=2398.5, equity=10000.0, global_pct=1.0, override_pct=None, spec=spec)
    assert trace["risk_amount"] == pytest.approx(100.0)
    assert trace["risk_pct_source"] == "global_default"
    # a 0.5% override halves the size, it does not touch the stop
    trace2 = compute_risk(node_id=1, strategy_id=1, symbol=SYMBOL, side="BUY", entry=2400.0,
                          sl=2398.5, equity=10000.0, global_pct=1.0, override_pct=0.5, spec=spec)
    assert trace2["volume"] == pytest.approx(0.33)
    assert trace2["risk_pct_source"] == "node_override"


def test_07_risk_above_maximum_is_blocked_not_clamped(live_cfg):
    spec = {"trade_tick_size": 0.01, "trade_tick_value": 1.0, "volume_min": 0.01,
            "volume_max": 50.0, "volume_step": 0.01}
    with pytest.raises(RiskBlock) as ei:
        compute_risk(node_id=1, strategy_id=1, symbol=SYMBOL, side="BUY", entry=2400.0,
                     sl=2398.0, equity=10000.0, global_pct=5.0, override_pct=None, spec=spec)
    assert ei.value.code == "RISK_PCT_ABOVE_MAXIMUM"
    assert "no silent clamping" in str(ei.value).lower() or "blocked" in str(ei.value).lower()
    with pytest.raises(RiskBlock) as ei2:
        compute_risk(node_id=1, strategy_id=1, symbol=SYMBOL, side="BUY", entry=2400.0,
                     sl=2398.0, equity=10000.0, global_pct=0.0, override_pct=None, spec=spec)
    assert ei2.value.code == "RISK_PCT_INVALID"


def test_08_node_override_wins_over_global(db, live_cfg, mt5_env):
    add_node(db, 508)
    enable_node(db, 508, risk_pct=0.5)
    eng = fresh_engine()
    nodes = eng.eligible_nodes()
    assert len(nodes) == 1
    assert nodes[0]["_effective_risk_pct"] == pytest.approx(0.5)
    assert nodes[0]["_risk_source"] == "node_override"


def test_09_global_risk_used_without_override(db, live_cfg, mt5_env):
    add_node(db, 509)
    enable_node(db, 509, risk_pct=None)      # NULL override -> global default applies
    eng = fresh_engine()
    nodes = eng.eligible_nodes()
    assert nodes[0]["_effective_risk_pct"] == pytest.approx(live_cfg.live_testing.risk_pct_default)
    assert nodes[0]["_risk_source"] == "global_default"
    assert resolve_risk_pct(1.0, None)["source"] == "global_default"


def test_10_invalid_sl_blocks_the_trade(db, live_cfg, mt5_env):
    add_node(db, 510, sl_mult=1.5)
    enable_node(db, 510)
    eng = fresh_engine()
    eng.activate(confirmed=True)
    # zero-distance SL -> rejected before any sizing
    with pytest.raises(RiskBlock) as ei:
        compute_risk(node_id=510, strategy_id=510, symbol=SYMBOL, side="BUY", entry=2400.0,
                     sl=2400.0, equity=10000.0, global_pct=1.0, override_pct=None,
                     spec={"trade_tick_size": 0.01, "trade_tick_value": 1.0,
                           "volume_min": 0.01, "volume_max": 50.0, "volume_step": 0.01})
    assert ei.value.code == "SL_DISTANCE_ZERO"
    with pytest.raises(RiskBlock) as ei2:
        compute_risk(node_id=510, strategy_id=510, symbol=SYMBOL, side="BUY", entry=2400.0,
                     sl=None, equity=10000.0, global_pct=1.0, override_pct=None,
                     spec={"trade_tick_size": 0.01, "trade_tick_value": 1.0,
                           "volume_min": 0.01, "volume_max": 50.0, "volume_step": 0.01})
    assert ei2.value.code == "SL_MISSING"
    # a node whose exit model produces no SL distance is blocked too
    db.set_live_test_config(510, {"is_active": False, "risk_pct": 0.5})
    add_node(db, 5101, sl_mult=0.0)
    enable_node(db, 5101)
    eng2 = fresh_engine()
    eng2.activate(confirmed=True)
    eng2._cycle()
    assert last_stage(eng2)["stage"] == "BLOCKED"
    assert last_stage(eng2)["detail"]["code"] == "SL_MISSING"
    # invalid broker symbol data blocks the calculation (never guessed)
    with pytest.raises(RiskBlock) as ei3:
        compute_volume(symbol=SYMBOL, side="BUY", entry=2400.0, sl=2398.0, risk_amount=100.0,
                       spec={"volume_min": 0.01})
    assert ei3.value.code == "INVALID_SYMBOL_DATA"


# ==========================================================================
# 11-13. limits, market panel, stale data
# ==========================================================================
def test_11_max_active_trade_limit_blocks_and_never_closes(db, live_cfg, mt5_env, term):
    add_node(db, 511)
    enable_node(db, 511)
    term.positions[900001] = {"ticket": 900001, "symbol": SYMBOL, "type": 0, "volume": 0.1,
                              "price_open": 2400.0, "sl": 2390.0, "tp": 2420.0,
                              "magic": ex.live_test_magic(511), "time": int(time.time()),
                              "comment": "live-test"}
    eng = fresh_engine()
    eng.activate(confirmed=True)
    act = eng.count_activity()
    assert act["positions"] == 1 and act["active_total"] == 1
    eng._cycle()
    assert term.sent == []                                   # nothing sent
    ev = last_stage(eng)
    assert ev["stage"] == "BLOCKED" and ev["status"] == "LIMIT_REACHED"
    assert "TRADE LIMIT REACHED" in ev["message"]
    assert 900001 in term.positions                          # existing trade untouched
    assert len(term.positions) == 1                          # nothing replaced/closed


def test_12_market_panel_reflects_mt5_state(db, live_cfg, mt5_env, bridge):
    bridge.bid, bridge.ask = 2500.50, 2501.00
    bridge.tick_ts = time.time()
    eng = fresh_engine()
    panel = eng.market_panel(SYMBOL)
    assert panel["symbol"] == SYMBOL
    assert panel["bid"] == pytest.approx(2500.50)
    assert panel["ask"] == pytest.approx(2501.00)
    assert panel["spread"] == pytest.approx(0.5)
    assert panel["point"] == 0.01 and panel["digits"] == 2
    assert panel["connected"] is True
    assert panel["data_fresh"] is True
    assert panel["tick_age_s"] is not None and panel["tick_age_s"] < 5
    assert panel["trading_available"] is True
    assert panel["session_status"]


def test_13_stale_or_missing_market_data_blocks(db, live_cfg, mt5_env, bridge, term):
    add_node(db, 513)
    enable_node(db, 513)
    bridge.tick_ts = time.time() - 3600                       # stale feed
    eng = fresh_engine()
    eng.activate(confirmed=True)
    panel = eng.market_panel(SYMBOL)
    assert panel["data_fresh"] is False and panel["trading_available"] is False
    assert any("stale" in r for r in panel["reasons"])
    eng._cycle()
    assert term.sent == []
    assert last_stage(eng)["detail"]["code"] == "MARKET_DATA_NOT_TRADABLE"
    # missing tick -> never guess a price
    bridge.latest_tick = lambda symbol: None
    eng2 = fresh_engine()
    eng2.activate(confirmed=True)
    eng2._cycle()
    assert term.sent == []
    assert "no tick available" in last_stage(eng2)["message"]


# ==========================================================================
# 14-17. full execution staging, duplicate protection, unknown results
# ==========================================================================
def test_14_buy_flow_reaches_all_stages(db, live_cfg, mt5_env, term, bridge):
    add_node(db, 514, side="BUY")
    enable_node(db, 514)
    eng = fresh_engine()
    eng.activate(confirmed=True)
    eng._cycle()
    names = stage_names(eng)
    for expected in ("SIGNAL DETECTED", "RISK CALCULATED", "VOLUME CALCULATED",
                     "MARKET VALIDATED", "ORDER VALIDATED", "ORDER SENT", "BROKER RESPONSE",
                     "POSITION VERIFIED"):
        assert expected in names, f"missing stage {expected}: {names}"
    assert len(term.sent) == 1
    sent = term.sent[0]
    assert sent["symbol"] == SYMBOL and sent["type"] == 0        # ORDER_TYPE_BUY
    assert sent["sl"] and sent["sl"] < sent["price"] and sent["tp"] > sent["price"]
    assert ex.LIVE_TEST_MAGIC_BASE <= int(sent["magic"]) < ex.LIVE_TEST_MAGIC_BASE + 1000
    row = db.q("SELECT * FROM live_test_trades ORDER BY id DESC")[0]
    assert row["status"] == "POSITION_OPEN"
    assert row["magic"] == sent["magic"] and row["ticket"] == 555001
    assert row["risk_pct"] == pytest.approx(0.5)
    assert row["run_id"] == "RUN-20261005-055251"
    assert row["lots"] == pytest.approx(sent["volume"])
    assert 555001 in term.positions
    # counter reflects the broker, not the local record
    act = eng.count_activity()
    assert act["positions"] == 1 and act["source"].startswith("MT5")
    # every stage carries timestamp, node, symbol, side, status and a readable message
    for ev in eng.recent_events:
        assert ev["ts"] and ev["ts_iso"] and ev["message"]
        assert "object object" not in ev["message"].lower()
        assert not ev["message"].startswith("{")


def test_15_sell_flow_reaches_all_stages(db, live_cfg, mt5_env, term):
    add_node(db, 515, side="SELL")
    enable_node(db, 515)
    eng = fresh_engine()
    eng.activate(confirmed=True)
    eng._cycle()
    names = stage_names(eng)
    for expected in ("SIGNAL DETECTED", "RISK CALCULATED", "VOLUME CALCULATED",
                     "MARKET VALIDATED", "ORDER VALIDATED", "ORDER SENT", "BROKER RESPONSE",
                     "POSITION VERIFIED"):
        assert expected in names, f"missing stage {expected}: {names}"
    sent = term.sent[0]
    assert sent["type"] == 1                                     # ORDER_TYPE_SELL
    assert sent["sl"] > sent["price"] and sent["tp"] < sent["price"]
    assert db.q("SELECT * FROM live_test_trades")[0]["side"] == "SELL"


def test_16_duplicate_protection_intact(db, live_cfg, mt5_env, term):
    live_cfg.live_testing.max_active_trades = 5
    add_node(db, 516, side="BUY")
    enable_node(db, 516)
    eng = fresh_engine()
    eng.activate(confirmed=True)
    eng._cycle()
    assert len(term.sent) == 1
    # same closed bar -> the engine does not even re-evaluate (per-bar dedupe)
    eng._cycle()
    assert len(term.sent) == 1
    # a fresh signal within the duplicate window is blocked, not re-sent
    eng.last_signal_bar.clear()
    eng._cycle()
    assert len(term.sent) == 1
    ev = last_stage(eng)
    assert ev["stage"] == "BLOCKED" and ev["detail"]["code"] == "LIVE_TEST_DUPLICATE_GUARD"
    # and the V4.2 client_order_id gate still blocks a repeated submission underneath
    payload = {"symbol": SYMBOL, "side": "buy", "volume": 0.01, "sl": 2395.0, "tp": 2410.0,
               "confirm": ex.PLACE_CONFIRMATION, "client_order_id": "dup-1"}
    first = ex.place_demo_order(dict(payload))
    assert first["status"] in ("POSITION_OPEN", "EXECUTED_UNCONFIRMED")
    sent_after_first = len(term.sent)
    with pytest.raises(ex.MT5ExecutionError) as ei:
        ex.place_demo_order(dict(payload))
    assert ei.value.code == "DUPLICATE_ORDER"
    assert len(term.sent) == sent_after_first          # the duplicate never reached the broker


def test_17_timeout_or_unknown_never_auto_retries(db, live_cfg, mt5_env, term, bridge):
    add_node(db, 517, side="BUY")
    enable_node(db, 517)
    bridge.send_market_order = lambda request: {"ok": False,
                                                "exception": "TimeoutError: order_send timed out"}
    eng = fresh_engine()
    eng.activate(confirmed=True)
    eng._cycle()
    assert last_stage(eng)["stage"] == "UNKNOWN - VERIFY MT5"
    assert "no automatic retry" in last_stage(eng)["message"].lower()
    row = db.q("SELECT * FROM live_test_trades ORDER BY id DESC")[0]
    assert row["status"] == "UNKNOWN"
    # same bar -> nothing further happens at all
    eng._cycle()
    assert len(eng.recent_events) > 0
    # a new signal is refused while the ambiguous attempt is unverified: never auto-retry
    eng.last_signal_bar.clear()
    eng._cycle()
    blocked = last_stage(eng)
    assert blocked["stage"] == "BLOCKED"
    assert blocked["detail"]["code"] == "UNRESOLVED_BROKER_STATE"
    assert "VERIFY MT5" in blocked["message"]
    assert "Timed out" not in blocked["message"]
    executed = ("SELECT COUNT(*) c FROM live_test_trades WHERE status IN "
                "('POSITION_OPEN','PENDING_ORDER','EXECUTED_UNCONFIRMED','UNKNOWN')")
    assert db.q(executed)[0]["c"] == 1     # exactly one order attempt ever reached MT5
    # a fresh bar does not bypass it either
    eng.last_signal_bar.clear()
    eng._cycle()
    assert last_stage(eng)["detail"]["code"] == "UNRESOLVED_BROKER_STATE"
    assert db.q(executed)[0]["c"] == 1
    # once the operator has verified MT5 (nothing at the broker) the gate is released
    db.x("UPDATE live_test_trades SET open_ts = open_ts - 300")
    eng._reconcile()
    assert "XAUUSD" not in eng._unresolved
    eng.last_signal_bar.clear()
    eng._cycle()
    assert last_stage(eng)["stage"] != "BLOCKED"


# ==========================================================================
# 18-19. disconnect / reconnect safety
# ==========================================================================
def test_18_disconnect_prevents_new_trades_and_keeps_positions(db, live_cfg, mt5_env, term, bridge):
    add_node(db, 518)
    enable_node(db, 518)
    eng = fresh_engine()
    eng.activate(confirmed=True)
    term.positions[900018] = {"ticket": 900018, "symbol": SYMBOL, "type": 0, "volume": 0.1,
                              "price_open": 2400.0, "sl": 2390.0, "tp": 2420.0,
                              "magic": ex.live_test_magic(518), "time": int(time.time()),
                              "comment": "live-test"}
    bridge._connected = False
    term.connected = False
    eng._cycle()
    assert term.sent == []                                   # no new trade while disconnected
    assert eng.active is False                               # interrupted -> INACTIVE
    assert 900018 in term.positions                          # existing position untouched
    panel = eng.market_panel(SYMBOL)
    assert panel["connected"] is False and panel["trading_available"] is False
    assert any("not connected" in r for r in panel["reasons"])
    assert "disconnected" in eng.get_mode()["stop_reason"].lower()


def test_19_reconnect_does_not_auto_resume(db, live_cfg, mt5_env, term, bridge):
    add_node(db, 519)
    enable_node(db, 519)
    eng = fresh_engine()
    eng.activate(confirmed=True)
    bridge._connected = False
    term.connected = False
    eng._cycle()
    assert eng.active is False
    # reconnect
    bridge._connected = True
    term.connected = True
    eng._cycle()
    assert term.sent == []                                   # still no orders
    assert eng.active is False and eng.get_mode()["mode"] == "INACTIVE"
    assert eng.get_mode()["previously_active"] is True
    # only an explicit activation may resume
    assert eng.activate(confirmed=False)["code"] == "CONFIRMATION_REQUIRED"
    assert eng.active is False
    assert eng.activate(confirmed=True)["ok"] is True and eng.active is True


# ==========================================================================
# 20-22. legacy isolation and research integrity
# ==========================================================================
def test_20_legacy_nodes_are_never_live_candidates(db, live_cfg, mt5_env):
    add_node(db, 520, data_source="LEGACY_TEST", status="SURVIVED")
    enable_node(db, 520)
    add_node(db, 521, data_source="USER_RESEARCH", status="SURVIVED")
    enable_node(db, 521)
    add_node(db, 522, data_source="USER_RESEARCH", status="DEAD")
    enable_node(db, 522)
    add_node(db, 523, data_source="USER_RESEARCH", status="QUALIFIED")
    db.set_live_test_config(523, {"is_active": True, "risk_pct": None})   # no genome -> blocked
    db.x("UPDATE strategies SET genome='{}' WHERE id=523")
    eng = fresh_engine()
    nodes = eng.eligible_nodes()
    assert [n["id"] for n in nodes] == [521]
    reasons = {n["id"]: n["reason"] for n in eng.excluded_nodes()}
    assert "LEGACY_TEST" in reasons[520]
    assert "DEAD" in reasons[522]
    assert "trading configuration" in reasons[523]
    # the V4.2 node-scope check agrees
    assert ex.assert_tradeable_strategy(520)["ok"] is False
    assert ex.assert_tradeable_strategy(521)["ok"] is True


def test_21_research_statistics_unchanged_by_live_testing(db, live_cfg, mt5_env, term):
    add_node(db, 531, status="SURVIVED")
    add_node(db, 532, status="QUALIFIED")
    enable_node(db, 531)
    before = {
        "strategies": db.one("SELECT COUNT(*) c FROM strategies")["c"],
        "by_status": {r["status"]: r["n"] for r in
                      db.q("SELECT status, COUNT(*) n FROM strategies GROUP BY status")},
        "run_id": db.one("SELECT run_id FROM strategies WHERE id=531")["run_id"],
        "fitness": db.one("SELECT fitness FROM strategies WHERE id=531")["fitness"],
        "events": db.one("SELECT COUNT(*) c FROM research_events")["c"]
        if db.one("SELECT COUNT(*) c FROM sqlite_master WHERE name='research_events'")["c"] else None,
    }
    eng = fresh_engine()
    eng.activate(confirmed=True)
    eng._cycle()
    assert len(term.sent) == 1                                # a trade did happen
    assert db.one("SELECT COUNT(*) c FROM strategies")["c"] == before["strategies"]
    assert {r["status"]: r["n"] for r in
            db.q("SELECT status, COUNT(*) n FROM strategies GROUP BY status")} == before["by_status"]
    assert db.one("SELECT status FROM strategies WHERE id=531")["status"] == "SURVIVED"
    assert db.one("SELECT fitness FROM strategies WHERE id=531")["fitness"] == before["fitness"]
    assert db.one("SELECT run_id FROM strategies WHERE id=531")["run_id"] == before["run_id"]
    if before["events"] is not None:
        assert db.one("SELECT COUNT(*) c FROM research_events")["c"] == before["events"]


def test_22_live_test_records_do_not_change_population_counts(db, live_cfg, mt5_env, term):
    add_node(db, 541, status="SURVIVED")
    add_node(db, 542, status="DEAD")
    add_node(db, 543, data_source="LEGACY_TEST", status="SURVIVED")
    enable_node(db, 541)
    pop_before = db.one("SELECT COUNT(*) c FROM strategies WHERE COALESCE(data_source,'')<>'LEGACY_TEST'")["c"]
    legacy_before = db.one("SELECT COUNT(*) c FROM strategies WHERE data_source='LEGACY_TEST'")["c"]
    eng = fresh_engine()
    eng.activate(confirmed=True)
    eng._cycle()
    assert db.one("SELECT COUNT(*) c FROM strategies WHERE COALESCE(data_source,'')<>'LEGACY_TEST'")["c"] == pop_before
    assert db.one("SELECT COUNT(*) c FROM strategies WHERE data_source='LEGACY_TEST'")["c"] == legacy_before
    rows = db.q("SELECT * FROM live_test_trades")
    assert len(rows) == 1 and rows[0]["status"] == "POSITION_OPEN"
    # the record lives in its own table - no research table received a row
    assert db.one("SELECT COUNT(*) c FROM paper_trades")["c"] == 0
    assert db.one("SELECT COUNT(*) c FROM mt5_demo_trades")["c"] == 0
    # the trade is traceable end-to-end (spec §18)
    r = rows[0]
    assert r["strategy_id"] == 541 and r["symbol"] == SYMBOL and r["side"] == "BUY"
    assert r["sl"] and r["tp"] and r["magic"] and r["client_order_id"]
    assert r["run_id"] == "RUN-20261005-055251" and r["risk_pct"] == pytest.approx(0.5)
    assert json.loads(r["result_json"])["status"] == "POSITION_OPEN"


# ==========================================================================
# extra: API surface, lifecycle monitoring, mode controls
# ==========================================================================
def test_api_reports_inactive_and_requires_confirmation(monkeypatch, db, live_cfg, mt5_env):
    from fastapi.testclient import TestClient
    from app.main import app
    import app.api.routes as routes

    eng = fresh_engine()
    monkeypatch.setattr(routes, "get_live_testing_engine", lambda: eng)
    monkeypatch.setattr(routes, "get_db", lambda: db)
    add_node(db, 550)
    enable_node(db, 550)
    c = TestClient(app)
    st = c.get("/api/live-testing/status").json()
    assert st["mode"] == "INACTIVE" and st["active"] is False
    assert st["node_count"] == 1 and st["risk"]["risk_pct_default"] == pytest.approx(1.0)
    assert c.post("/api/live-testing/activate", json={}).json()["code"] == "CONFIRMATION_REQUIRED"
    assert eng.active is False
    # STOP NEW TRADES is distinct from CLOSE EXISTING POSITIONS (not in V4.3)
    assert c.post("/api/live-testing/close-positions").status_code == 501
    act = c.post("/api/live-testing/activate", json={"confirm": True}).json()
    assert act["ok"] is True and eng.active is True
    assert c.get("/api/live-testing/status").json()["mode"] == "ACTIVE"
    de = c.post("/api/live-testing/deactivate", json={"reason": "test"}).json()
    assert de["mode"] == "INACTIVE" and de["positions_closed"] == 0
    assert c.get("/api/live-testing/log").status_code == 200
    assert c.get("/api/live-testing/counter").json()["activity"]["counted"] is True


def test_reconcile_marks_vanished_positions_closed(db, live_cfg, mt5_env, term):
    add_node(db, 560)
    enable_node(db, 560)
    eng = fresh_engine()
    eng.activate(confirmed=True)
    eng._cycle()
    row = db.q("SELECT * FROM live_test_trades ORDER BY id DESC")[0]
    assert row["status"] == "POSITION_OPEN"
    # broker position closed externally -> lifecycle monitoring notices, research untouched
    term.positions.clear()
    out = eng._reconcile()
    assert out["closed"] == 1
    assert db.one("SELECT status FROM live_test_trades WHERE id=?", (row["id"],))["status"] == "CLOSED_MISSING"
    assert db.one("SELECT status FROM strategies WHERE id=560")["status"] == "SURVIVED"
    assert last_stage(eng)["stage"] == "POSITION CLOSED"


def test_sizing_respects_broker_minimums(live_cfg):
    spec = {"trade_tick_size": 0.01, "trade_tick_value": 1.0, "volume_min": 0.10,
            "volume_max": 5.0, "volume_step": 0.10}
    with pytest.raises(RiskBlock) as ei:
        compute_volume(symbol=SYMBOL, side="BUY", entry=2400.0, sl=2399.9, risk_amount=0.5,
                       spec=spec)
    assert ei.value.code == "VOLUME_BELOW_MINIMUM"           # never silently clamped up
    out = compute_volume(symbol=SYMBOL, side="BUY", entry=2400.0, sl=2390.0, risk_amount=250.0,
                         spec=spec)
    assert out["volume"] == pytest.approx(0.20)              # step-aligned
    with pytest.raises(RiskBlock) as ei2:
        compute_volume(symbol=SYMBOL, side="BUY", entry=2400.0, sl=2390.0, risk_amount=100000.0,
                       spec=spec)
    assert ei2.value.code == "VOLUME_ABOVE_MAXIMUM"
