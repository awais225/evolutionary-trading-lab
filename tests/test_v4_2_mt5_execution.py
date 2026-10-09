"""V4.2 focused tests — controlled MT5 DEMO execution foundation.

These tests never touch a real terminal and never mutate research DATA:

* the MT5 terminal is replaced by an injected test double at the *bridge
  boundary* (``app.mt5.factory.get_bridge`` / ``app.mt5.execution.mt5_module``)
  so the product code path (validation -> request build -> interpret -> verify)
  runs exactly as it does in production; the double only stands in for the
  Windows terminal, which cannot run in this environment;
* persistence is redirected to a temporary SQLite database via the injected
  ``_db`` hook, so no research row (and no authoritative DATA file) is written.

Covered (spec §18): demo-account safety, order validation, BUY execution,
SELL execution, SL/TP validation and verification, MT5 response interpretation,
duplicate submission protection, error formatting, legacy candidate isolation,
no-execution-on-load, audit persistence.
"""
import time

import pytest
from fastapi.testclient import TestClient

from app.mt5 import execution as ex
from app.mt5.bridge import AccountInfo, SymbolInfo, Tick


# --------------------------------------------------------------------------
# test double for the MT5 terminal (bridge boundary)
# --------------------------------------------------------------------------
class FakeMT5Terminal:
    """Stands in for MetaTrader5 + the running terminal."""

    def __init__(self, *, trade_mode=0, connected=True, account=True,
                 sending=True, register_position=True):
        self.trade_mode = trade_mode          # 0=demo 1=contest 2=real
        self.connected = connected
        self.account = account
        self.sending = sending
        self.register_position = register_position
        self.sent = []                        # every request handed to the terminal
        self.result_queue = []                # queued order_send results
        self.default_result = {"retcode": 10009, "order": 555001, "deal": 666001,
                               "price": 2400.10, "volume": 0.01, "comment": "done",
                               "sl": None, "tp": None}
        self.positions = {}
        self.orders = {}

    # --- terminal surface used by demo_account_guard ---
    def account_info(self):
        if not self.account:
            return None
        return type("A", (), {"login": 50123456, "server": "ICMarketsSC-Demo",
                              "trade_mode": self.trade_mode, "balance": 10000.0,
                              "equity": 10000.0, "margin_free": 9500.0,
                              "currency": "USD", "leverage": 500, "name": "Demo"})()

    def terminal_info(self):
        return type("T", (), {"name": "MetaTrader 5", "company": "Raw Trading Ltd",
                              "connected": self.connected, "trade_allowed": True})()


class FakeMT5Bridge:
    """Stands in for MT5RealBridge (same surface, no Windows)."""

    name = "mt5_real"
    source = "MT5"
    broker_name = "Raw Trading Ltd"
    server_name = "ICMarketsSC-Demo"
    account_id = "50123456"

    def __init__(self, term: FakeMT5Terminal, *, symbol=True, volume_min=0.01,
                 volume_max=50.0, volume_step=0.01, stops_level=50, trade_mode_raw=4):
        self.term = term
        self._connected = term.connected
        self._symbol = symbol
        self.volume_min, self.volume_max, self.volume_step = volume_min, volume_max, volume_step
        self.stops_level = stops_level
        self.trade_mode_raw = trade_mode_raw
        self.point = 0.01
        self.bid, self.ask = 2400.00, 2400.20
        self.tick_ts = time.time()

    # --- info ---
    def account_info(self):
        if not self.term.account:
            return None
        is_demo = self.term.trade_mode == 0
        return AccountInfo(login=50123456, server="ICMarketsSC-Demo", balance=10000.0,
                           equity=10000.0, currency="USD", is_demo=is_demo,
                           source="MT5", margin_free=9500.0, leverage=500)

    def symbol_info(self, symbol):
        if not self._symbol:
            return None
        return SymbolInfo(symbol=symbol, digits=2, point=self.point, spread_points=20.0,
                          trade_mode="4", source="MT5", visible=True,
                          trade_mode_raw=self.trade_mode_raw, trade_allowed=True,
                          volume_min=self.volume_min, volume_max=self.volume_max,
                          volume_step=self.volume_step, trade_stops_level=self.stops_level,
                          freeze_level=0, filling_modes=[1, 2])

    def latest_tick(self, symbol):
        return Tick(ts=self.tick_ts, bid=self.bid, ask=self.ask, source="MT5")

    # --- execution ---
    def send_market_order(self, request):
        self.term.sent.append(dict(request))
        if not self.term.sending:
            return {"ok": False, "unsupported": True, "retcode": None,
                    "error": "terminal not available"}
        res = dict(self.term.result_queue.pop(0)) if self.term.result_queue else dict(self.term.default_result)
        res.setdefault("sl", request.get("sl"))
        res.setdefault("tp", request.get("tp"))
        if res.get("retcode") == 10009 and res.get("order") and self.term.register_position:
            self.term.positions[int(res["order"])] = {
                "ticket": int(res["order"]), "symbol": request["symbol"],
                "type": 0 if request["type"] == 0 else 1,
                "volume": res.get("volume", request["volume"]),
                "price_open": res.get("price", request["price"]),
                "sl": res.get("sl") if res.get("sl") is not None else request.get("sl"),
                "tp": res.get("tp") if res.get("tp") is not None else request.get("tp"),
                "magic": request.get("magic", 0), "time": int(time.time()), "comment": "done"}
        if res.get("retcode") == 10008 and res.get("order"):
            self.term.orders[int(res["order"])] = {
                "ticket": int(res["order"]), "symbol": request["symbol"],
                "volume_initial": request["volume"], "volume_current": request["volume"],
                "price_open": res.get("price", request["price"]),
                "sl": request.get("sl"), "tp": request.get("tp"),
                "magic": request.get("magic", 0), "state": 1, "time_setup": int(time.time())}
        return {"ok": True, "retcode": res.get("retcode"), "raw": res, "request": dict(request)}

    def positions_get(self, ticket=None, symbol=None):
        vals = list(self.term.positions.values())
        if ticket is not None:
            return [p for p in vals if p["ticket"] == int(ticket)]
        if symbol:
            return [p for p in vals if p["symbol"] == symbol]
        return vals

    def orders_get(self, ticket=None, symbol=None):
        vals = list(self.term.orders.values())
        if ticket is not None:
            return [o for o in vals if o["ticket"] == int(ticket)]
        if symbol:
            return [o for o in vals if o["symbol"] == symbol]
        return vals

    def close_position(self, ticket, comment=""):
        if int(ticket) in self.term.positions:
            del self.term.positions[int(ticket)]
            return {"ok": True, "retcode": 10009}
        return {"ok": False, "error": "not found"}


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
@pytest.fixture
def term():
    return FakeMT5Terminal()


@pytest.fixture
def bridge(term):
    return FakeMT5Bridge(term)


@pytest.fixture
def mt5_env(monkeypatch, bridge, term):
    """Wire the double into the product code path."""
    import app.mt5.factory as factory
    import app.mt5.mt5_real as real

    monkeypatch.setattr(factory, "get_bridge", lambda: bridge)
    monkeypatch.setattr(real, "MT5_PACKAGE_AVAILABLE", True)
    monkeypatch.setattr(ex, "mt5_module", lambda: term)
    return bridge


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Redirect the manual-order audit log to a scratch database."""
    from app.db.database import Database
    db = Database(str(tmp_path / "v42_orders.db"))
    monkeypatch.setattr(ex, "_db", lambda: db)
    return db


def _payload(**over):
    p = {"symbol": "XAUUSD", "side": "buy", "volume": 0.01,
         "sl": 2390.00, "tp": 2410.00, "confirm": ex.PLACE_CONFIRMATION,
         "client_order_id": "ord-" + str(time.time_ns())}
    p.update(over)
    return p


# ==========================================================================
# 1-2. demo-account safety (spec §1, tests 1-2)
# ==========================================================================
def test_demo_account_accepted(mt5_env):
    g = ex.demo_account_guard(mt5_env)
    assert g["demo_verified"] is True
    assert g["blocked_code"] is None
    assert g["account"]["trade_mode_name"] == "DEMO"


def test_real_account_blocked(mt5_env, bridge, term, temp_db):
    term.trade_mode = 2  # ACCOUNT_TRADE_MODE_REAL
    g = ex.demo_account_guard(bridge)
    assert g["demo_verified"] is False
    assert g["blocked_code"] == "NON_DEMO_ACCOUNT"
    assert "not a DEMO account" in g["blocked_reason"]

    with pytest.raises(ex.MT5ExecutionError) as ei:
        ex.place_demo_order(_payload(), bridge=bridge)
    assert ei.value.code == "NON_DEMO_ACCOUNT"
    assert ei.value.stage == "ACCOUNT_SAFETY"
    assert term.sent == []          # nothing was ever sent to the terminal
    assert temp_db.get_manual_mt5_orders()[0]["status"] == "BLOCKED"


def test_contest_account_blocked(mt5_env, term):
    term.trade_mode = 1
    g = ex.demo_account_guard()
    assert g["demo_verified"] is False
    assert g["blocked_code"] == "NON_DEMO_ACCOUNT"


def test_unknown_trade_mode_is_blocked_not_guessed(mt5_env, term):
    term.trade_mode = None
    g = ex.demo_account_guard()
    assert g["demo_verified"] is False
    assert g["blocked_code"] == "ACCOUNT_MODE_UNKNOWN"


def test_missing_account_blocked(mt5_env, term):
    term.account = False
    g = ex.demo_account_guard()
    assert g["demo_verified"] is False
    assert g["blocked_code"] == "ACCOUNT_UNAVAILABLE"


def test_simulator_bridge_cannot_execute(monkeypatch, temp_db):
    """No MT5 package/terminal -> hard block (never a simulated 'success')."""
    from app.mt5.simulator import SimulatorBridge
    import app.mt5.mt5_real as real
    monkeypatch.setattr(real, "MT5_PACKAGE_AVAILABLE", False)
    sim = SimulatorBridge()
    g = ex.demo_account_guard(sim)
    assert g["demo_verified"] is False
    assert g["blocked_code"] == "MT5_UNAVAILABLE"
    assert "not a real MetaTrader 5 terminal" in g["blocked_reason"]
    with pytest.raises(ex.MT5ExecutionError) as ei:
        ex.place_demo_order(_payload(), bridge=sim)
    assert ei.value.code == "MT5_UNAVAILABLE"


def test_disconnected_terminal_blocked(mt5_env, bridge, term):
    bridge._connected = False
    term.connected = False
    g = ex.demo_account_guard(bridge)
    assert g["demo_verified"] is False
    assert g["blocked_code"] == "MT5_NOT_CONNECTED"


# ==========================================================================
# 3-4. validation (spec §7, tests 5-7)
# ==========================================================================
def _fail_ids(report):
    return {c["id"] for c in report["checks"] if not c["ok"]}


def test_validation_passes_for_a_valid_demo_buy(mt5_env):
    r = ex.validate_order_request(symbol="XAUUSD", side="buy", volume=0.01,
                                  sl=2390.0, tp=2410.0)
    assert r["placement_allowed"] is True
    assert r["errors"] == []
    assert r["normalized"]["symbol"] == "XAUUSD"


def test_validation_blocks_invalid_volume(mt5_env):
    below = ex.validate_order_request(symbol="XAUUSD", side="buy", volume=0.001, sl=2390, tp=2410)
    assert "volume_min" in _fail_ids(below) and not below["placement_allowed"]
    step = ex.validate_order_request(symbol="XAUUSD", side="buy", volume=0.015, sl=2390, tp=2410)
    assert "volume_step" in _fail_ids(step)
    high = ex.validate_order_request(symbol="XAUUSD", side="buy", volume=99.0, sl=2390, tp=2410)
    assert "volume_max" in _fail_ids(high)
    junk = ex.validate_order_request(symbol="XAUUSD", side="buy", volume="abc", sl=2390, tp=2410)
    assert "volume_numeric" in _fail_ids(junk)


def test_validation_blocks_invalid_sl_tp_direction(mt5_env):
    buy = ex.validate_order_request(symbol="XAUUSD", side="buy", volume=0.01, sl=2410, tp=2390)
    assert "sl_tp_direction" in _fail_ids(buy)
    sell = ex.validate_order_request(symbol="XAUUSD", side="sell", volume=0.01, sl=2390, tp=2410)
    assert "sl_tp_direction" in _fail_ids(sell)
    ok_sell = ex.validate_order_request(symbol="XAUUSD", side="sell", volume=0.01, sl=2410, tp=2390)
    assert "sl_tp_direction" not in _fail_ids(ok_sell)


def test_validation_blocks_stops_too_close(mt5_env):
    # stops_level 50 points * 0.01 = 0.50 minimum distance
    r = ex.validate_order_request(symbol="XAUUSD", side="buy", volume=0.01,
                                  sl=2399.90, tp=2400.30)
    assert "stops_level" in _fail_ids(r)


def test_validation_blocks_invalid_symbol_and_side(mt5_env, bridge):
    bridge._symbol = False
    assert "symbol_exists" in _fail_ids(ex.validate_order_request(symbol="NOPE", side="buy", volume=0.01))
    bridge._symbol = True
    assert "side_valid" in _fail_ids(ex.validate_order_request(symbol="XAUUSD", side="hold", volume=0.01))


def test_validation_blocks_negative_sl_tp(mt5_env):
    ids = _fail_ids(ex.validate_order_request(symbol="XAUUSD", side="buy", volume=0.01,
                                              sl=-5, tp=-1))
    assert "sl_valid" in ids and "tp_valid" in ids


def test_validation_blocks_stale_quote(mt5_env, bridge):
    bridge.tick_ts = time.time() - 3600
    assert "quote_fresh" in _fail_ids(ex.validate_order_request(symbol="XAUUSD", side="buy", volume=0.01))


def test_validation_reports_every_required_check(mt5_env):
    r = ex.validate_order_request(symbol="XAUUSD", side="buy", volume=0.01, sl=2390, tp=2410)
    ids = {c["id"] for c in r["checks"]}
    for required in ("mt5_package", "mt5_bridge", "mt5_connected", "account_available",
                     "demo_account", "symbol_exists", "symbol_tradable", "side_valid",
                     "volume_numeric", "volume_min", "volume_max", "volume_step",
                     "price_valid", "sl_valid", "tp_valid", "sl_tp_direction", "stops_level"):
        assert required in ids, required


# ==========================================================================
# 5-6. request building + BUY/SELL execution (spec §8, tests 3-4)
# ==========================================================================
def test_request_building_uses_real_sl_tp_and_documented_constants():
    req = ex.build_market_order_request(
        {"symbol": "XAUUSD", "side": "buy", "volume": 0.02, "price": 2400.20,
         "sl": 2390.0, "tp": 2415.0, "magic": 777123, "comment": "evolab-demo-manual"},
        filling=ex._c("ORDER_FILLING_IOC"), deviation=15)
    assert req["action"] == 1                     # TRADE_ACTION_DEAL
    assert req["type"] == 0                       # ORDER_TYPE_BUY
    assert req["sl"] == 2390.0 and req["tp"] == 2415.0
    assert req["volume"] == 0.02 and req["deviation"] == 15
    assert req["magic"] == 777123 and req["comment"] == "evolab-demo-manual"


def test_buy_execution_full_path(mt5_env, bridge, term, temp_db):
    res = ex.place_demo_order(_payload(side="buy"), bridge=bridge)
    assert res["ok"] is True and res["status"] == "POSITION_OPEN"
    assert res["label"] == "ORDER EXECUTED"
    assert res["order"]["ticket"] == 555001 and res["order"]["deal_ticket"] == 666001
    assert res["execution"]["side"] == "buy"
    # sent request: market BUY priced at the ASK with real SL/TP
    sent = term.sent[0]
    assert sent["type"] == 0 and sent["price"] == pytest.approx(2400.20)
    assert sent["sl"] == 2390.0 and sent["tp"] == 2410.0
    assert res["execution"]["sl_tp_verified"] is True
    assert res["execution"]["sl_broker"] == 2390.0 and res["execution"]["tp_broker"] == 2410.0
    assert res["execution"]["exec_price"] == pytest.approx(2400.10)
    # persisted audit row
    row = temp_db.get_manual_mt5_orders()[0]
    assert row["status"] == "POSITION_OPEN" and row["order_ticket"] == 555001
    assert row["account_login"] == 50123456 and row["sl_tp_verified"] == 1


def test_sell_execution_full_path(mt5_env, bridge, term, temp_db):
    term.default_result = {"retcode": 10009, "order": 555002, "deal": 666002,
                           "price": 2399.90, "volume": 0.01, "comment": "done"}
    res = ex.place_demo_order(_payload(side="sell", sl=2410.0, tp=2390.0), bridge=bridge)
    assert res["status"] == "POSITION_OPEN" and res["ok"] is True
    sent = term.sent[0]
    assert sent["type"] == 1                      # ORDER_TYPE_SELL
    assert sent["price"] == pytest.approx(2400.00)   # sell at the BID
    assert sent["sl"] == 2410.0 and sent["tp"] == 2390.0
    assert res["execution"]["sl_tp_verified"] is True


def test_pending_order_is_not_labelled_as_executed(mt5_env, bridge, term, temp_db):
    term.default_result = {"retcode": 10008, "order": 555003, "deal": 0,
                           "price": 2400.20, "volume": 0.01, "comment": "placed"}
    res = ex.place_demo_order(_payload(), bridge=bridge)
    assert res["status"] == "PENDING_ORDER"
    assert res["label"] == "ORDER ACCEPTED / PENDING"
    assert res["verification"]["position_found"] is False
    assert res["verification"]["pending_found"] is True


def test_done_without_position_is_reported_as_unconfirmed(mt5_env, bridge, term, temp_db):
    term.register_position = False          # broker reports DONE, terminal has no position
    term.default_result = {"retcode": 10009, "order": 999999, "deal": 666999,
                           "price": 2400.10, "volume": 0.01, "comment": "done"}
    res = ex.place_demo_order(_payload(), bridge=bridge)
    assert res["status"] == "EXECUTED_UNCONFIRMED"
    assert "not confirmed" in res["label"].lower()


def test_broker_adjusted_sl_tp_is_surfaced(mt5_env, bridge, term, temp_db):
    term.default_result = {"retcode": 10009, "order": 555004, "deal": 666004,
                           "price": 2400.10, "volume": 0.01, "comment": "done",
                           "sl": 2395.00, "tp": 2405.00}      # broker changed our levels
    res = ex.place_demo_order(_payload(sl=2390.0, tp=2410.0), bridge=bridge)
    assert res["execution"]["sl_tp_verified"] is False
    assert res["execution"]["sl_broker"] == 2395.00
    assert res["execution"]["tp_broker"] == 2405.00
    assert "broker SL/TP differ" in res["verification"]["note"]


# ==========================================================================
# 7-8. MT5 response interpretation + error taxonomy (spec §12, §8)
# ==========================================================================
@pytest.mark.parametrize("retcode,expect", [
    (10009, "EXECUTED"), (10010, "EXECUTED"), (10008, "PENDING"),
    (10004, "REJECTED"), (10006, "REJECTED"), (10016, "REJECTED"),
    (10018, "REJECTED"), (10019, "REJECTED"), (10027, "REJECTED"),
    (10030, "REJECTED"), (10012, "TIMEOUT"), (10031, "TIMEOUT"),
    (54321, "UNKNOWN"), (None, "UNKNOWN"),
])
def test_retcode_categories(retcode, expect):
    assert ex.interpret_retcode(retcode)["category"] == expect


def test_retcode_messages_are_human_readable():
    assert "market is closed" in ex.interpret_retcode(10018)["message"].lower()
    assert "insufficient margin" in ex.interpret_retcode(10019)["message"].lower()
    assert "invalid stops" in ex.interpret_retcode(10016)["message"].lower()
    assert "unrecognised mt5 retcode 54321" in ex.interpret_retcode(54321)["message"].lower()
    assert "broker comment" in ex.interpret_retcode(10006, "Trade disabled")["message"].lower()


def test_timeout_never_auto_retries_and_warns():
    t = ex.interpret_retcode(10012)
    assert t["safe_to_retry"] is False
    assert "verify open positions" in t["warning"].lower()
    assert "not resubmit automatically" in t["warning"].lower()


def test_broker_rejection_is_reported_with_retcode(mt5_env, bridge, term, temp_db):
    term.default_result = {"retcode": 10018, "order": 0, "deal": 0, "price": 0.0,
                           "volume": 0.0, "comment": "market closed"}
    res = ex.place_demo_order(_payload(), bridge=bridge)
    assert res["ok"] is False and res["status"] == "REJECTED"
    assert res["broker"]["retcode"] == 10018
    assert "market" in res["broker"]["message"].lower()
    row = temp_db.get_manual_mt5_orders()[0]
    assert row["status"] == "REJECTED" and row["retcode"] == 10018


def test_timeout_result_is_unknown_not_success(mt5_env, bridge, term, temp_db):
    term.default_result = {"retcode": 10012, "order": 0, "deal": 0, "price": 0.0,
                           "volume": 0.0, "comment": "timeout"}
    res = ex.place_demo_order(_payload(), bridge=bridge)
    assert res["ok"] is False and res["status"] == "TIMEOUT_UNKNOWN"
    assert "verify" in res["broker"]["warning"].lower()
    assert len(term.sent) == 1          # exactly one request, no retry


def test_unexpected_bridge_exception_is_surfaced(mt5_env, bridge, term, temp_db):
    def boom(request):
        raise RuntimeError("socket exploded")
    bridge.send_market_order = boom
    with pytest.raises(ex.MT5ExecutionError) as ei:
        ex.place_demo_order(_payload(), bridge=bridge)
    assert ei.value.code == "UNEXPECTED_ERROR"
    assert "socket exploded" in ei.value.message


# ==========================================================================
# 9. duplicate protection (spec §11, test 9)
# ==========================================================================
def test_duplicate_client_order_id_is_rejected(mt5_env, bridge, term, temp_db):
    first = ex.place_demo_order(_payload(client_order_id="dup-1"), bridge=bridge)
    assert first["ok"] is True
    with pytest.raises(ex.MT5ExecutionError) as ei:
        ex.place_demo_order(_payload(client_order_id="dup-1"), bridge=bridge)
    assert ei.value.code == "DUPLICATE_ORDER"
    assert ei.value.http_status == 409
    assert len(term.sent) == 1          # only ONE order ever reached the terminal


def test_in_flight_gate_blocks_second_submission(mt5_env, bridge, term, temp_db):
    assert ex._gate.try_begin({"client_order_id": "inflight", "symbol": "XAUUSD",
                               "side": "buy", "volume": 0.01}) is True
    try:
        with pytest.raises(ex.MT5ExecutionError) as ei:
            ex.place_demo_order(_payload(), bridge=bridge)
        assert ei.value.code == "ORDER_IN_FLIGHT"
        assert ei.value.http_status == 409
        assert term.sent == []
    finally:
        ex._gate.finish({"status": "TEST_DONE"})


def test_gate_state_is_exposed_for_the_ui(mt5_env, bridge, term, temp_db):
    ex.place_demo_order(_payload(), bridge=bridge)
    st = ex.operation_status()
    assert st["in_flight"] is False and st["stage"] == "DONE"
    assert st["last_result"]["status"] == "POSITION_OPEN"


# ==========================================================================
# 10. no execution without explicit action (spec §10, test 10)
# ==========================================================================
def test_confirmation_token_is_required(mt5_env, bridge, term, temp_db):
    with pytest.raises(ex.MT5ExecutionError) as ei:
        ex.place_demo_order(_payload(confirm=""), bridge=bridge)
    assert ei.value.code == "CONFIRMATION_REQUIRED"
    assert ei.value.http_status == 409
    assert term.sent == []
    wrong = _payload(confirm="yes")
    with pytest.raises(ex.MT5ExecutionError):
        ex.place_demo_order(wrong, bridge=bridge)
    assert term.sent == []


def test_state_and_status_never_place_an_order(mt5_env, bridge, term, temp_db):
    ex.execution_state(bridge)
    ex.operation_status()
    ex.validate_order_request(symbol="XAUUSD", side="buy", volume=0.01, sl=2390, tp=2410)
    assert term.sent == []


# ==========================================================================
# 11. legacy isolation + node safety (spec §3/§15, test 11)
# ==========================================================================
def test_legacy_node_is_never_a_trading_candidate():
    # The refusal NAMES WHY: the tradeability predicate refuses any LEGACY_TEST
    # row with LEGACY_NODE_NOT_TRADEABLE (verified unconditionally, without a DB)
    # and a lookup miss with NODE_NOT_FOUND. When the study actually carries
    # legacy rows (the operator's mixed database does), the real row is refused
    # the same way; a user-research-only snapshot has none — the contract is the
    # refusal, never a particular id.
    from app.live_testing.engine import _tradeability
    verdict = _tradeability({"data_source": "LEGACY_TEST", "status": "alive"}, genome={})
    assert verdict["ok"] is False and "LEGACY" in verdict.get("code", "")

    from app.db.database import get_db
    row = get_db().one("SELECT id FROM strategies WHERE data_source='LEGACY_TEST' "
                       "ORDER BY id LIMIT 1")
    if row is not None:
        v = ex.assert_tradeable_strategy(int(row["id"]))
        assert v["ok"] is False and v["code"] == "LEGACY_NODE_NOT_TRADEABLE"
        assert "LEGACY_TEST" in v["detail"]
    missing = ex.assert_tradeable_strategy(2 ** 31 - 1)     # never a real id
    assert missing["ok"] is False and missing["code"] == "NODE_NOT_FOUND"


def test_user_research_node_is_accepted_and_status_untouched(temp_db):
    from app.db.database import get_db
    row = get_db().one("SELECT id, status FROM strategies "
                       "WHERE COALESCE(data_source,'USER_RESEARCH')<>'LEGACY_TEST' "
                       "ORDER BY id DESC LIMIT 1")
    assert row is not None
    v = ex.assert_tradeable_strategy(int(row["id"]))
    assert v["ok"] is True
    after = get_db().one("SELECT status FROM strategies WHERE id=?", (row["id"],))
    assert after["status"] == row["status"]      # a check never changes research state


def test_validation_blocks_legacy_strategy_selection(mt5_env):
    r = ex.validate_order_request(symbol="XAUUSD", side="buy", volume=0.01,
                                  sl=2390, tp=2410, strategy_id=1)
    assert r["placement_allowed"] is False
    assert "node_scope" in _fail_ids(r)


def test_manual_order_does_not_touch_node_status(mt5_env, bridge, term, temp_db):
    from app.db.database import get_db
    db = get_db()
    sid = db.one("SELECT id FROM strategies WHERE status='QUALIFIED' "
                 "AND COALESCE(data_source,'USER_RESEARCH')<>'LEGACY_TEST' LIMIT 1")
    if sid is None:
        pytest.skip("no user QUALIFIED node available")
    before = db.one("SELECT status, generation, research_node_num, hash FROM strategies WHERE id=?",
                    (sid["id"],))
    ex.place_demo_order(_payload(strategy_id=int(sid["id"])), bridge=bridge)
    after = db.one("SELECT status, generation, research_node_num, hash FROM strategies WHERE id=?",
                   (sid["id"],))
    assert dict(after) == dict(before)


# ==========================================================================
# 12. HTTP surface + error formatting (spec §12, §13)
# ==========================================================================
@pytest.fixture
def http_client(mt5_env, temp_db, monkeypatch):
    import app.main as main
    return TestClient(main.app)


def test_http_state_endpoint_reports_demo_verification(http_client, term):
    r = http_client.get("/api/mt5-execution/state")
    assert r.status_code == 200
    body = r.json()
    assert body["account_safety"]["demo_verified"] is True
    assert body["execution_allowed"] is True
    assert body["account_safety"]["account"]["login"] == 50123456
    assert term.sent == []


def test_http_place_requires_confirmation_and_returns_structured_error(http_client, term):
    r = http_client.post("/api/mt5-execution/place",
                         json={"symbol": "XAUUSD", "side": "buy", "volume": 0.01})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "CONFIRMATION_REQUIRED"
    assert detail["stage"] == "CONFIRMATION"
    assert "confirm" in detail["message"].lower()
    assert "[object Object]" not in r.text
    assert term.sent == []


def test_http_place_validation_error_is_specific(http_client, term):
    r = http_client.post("/api/mt5-execution/place",
                         json={"symbol": "XAUUSD", "side": "buy", "volume": 0.001,
                               "sl": 2390, "tp": 2410, "confirm": ex.PLACE_CONFIRMATION})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["code"] == "VALIDATION_FAILED"
    assert any("minimum" in e["message"] for e in detail["details"]["errors"])
    assert term.sent == []


def test_http_place_success_and_validate_preview(http_client, term, temp_db):
    prev = http_client.post("/api/mt5-execution/validate",
                            json={"symbol": "XAUUSD", "side": "sell", "volume": 0.01,
                                  "sl": 2410, "tp": 2390})
    assert prev.status_code == 200 and prev.json()["placement_allowed"] is True
    assert term.sent == []                      # preview never sends

    r = http_client.post("/api/mt5-execution/place",
                         json={"symbol": "XAUUSD", "side": "sell", "volume": 0.01,
                               "sl": 2410, "tp": 2390, "confirm": ex.PLACE_CONFIRMATION,
                               "client_order_id": "http-1"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "POSITION_OPEN" and body["ok"] is True
    assert body["order"]["ticket"] == 555001
    assert len(term.sent) == 1
    st = http_client.get("/api/mt5-execution/status").json()
    assert st["recent_orders"][0]["client_order_id"] == "http-1"


def test_http_real_account_is_blocked_with_403(http_client, term):
    term.trade_mode = 2
    r = http_client.post("/api/mt5-execution/place",
                         json={"symbol": "XAUUSD", "side": "buy", "volume": 0.01,
                               "sl": 2390, "tp": 2410, "confirm": ex.PLACE_CONFIRMATION})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "NON_DEMO_ACCOUNT"
    assert term.sent == []


# ==========================================================================
# 13. position close (verification hygiene) + misc
# ==========================================================================
def test_close_position_is_demo_guarded(mt5_env, bridge, term, temp_db):
    res = ex.place_demo_order(_payload(), bridge=bridge)
    ticket = res["order"]["ticket"]
    out = ex.close_demo_position(ticket, bridge=bridge)
    assert out["ok"] is True
    assert bridge.positions_get(ticket=ticket) == []


def test_close_position_blocked_on_real_account(mt5_env, bridge, term):
    term.trade_mode = 2
    with pytest.raises(ex.MT5ExecutionError) as ei:
        ex.close_demo_position(1, bridge=bridge)
    assert ei.value.code == "NON_DEMO_ACCOUNT"


def test_magic_number_is_manual_demo_scoped():
    assert ex._magic_for(None) == 777000
    assert 777000 < ex._magic_for(10815) <= 777899


def test_execution_path_has_no_retry_and_no_background_threads():
    """Exactly one send per submission, and no loops/threads/sleeps in the
    execution path (static check on the AST, so docstrings cannot mask it)."""
    import ast
    import inspect
    src = inspect.getsource(ex.place_demo_order)
    tree = ast.parse(src.replace("def place_demo_order", "def f"))
    sends, loops, calls = 0, 0, set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.While, ast.For, ast.AsyncFor)):
            loops += 1
        if isinstance(node, ast.Call):
            fn = node.func
            name = getattr(fn, "attr", getattr(fn, "id", ""))
            calls.add(name)
            if name == "send_market_order":
                sends += 1
    assert sends == 1, "exactly one send site"
    assert loops == 0, "no retry loops in the execution path"
    assert "sleep" not in calls
    module_src = inspect.getsource(ex)
    assert "Thread(" not in module_src
    assert module_src.count("send_market_order(") == 1
