"""V6 — the MT5 bridge contract from ``MT5_BRIDGE_DETAILS.txt``.

Everything here is verified on Linux WITHOUT the MetaTrader5 package or a
terminal: the import guard, the bridge architecture and source labelling, the
order request construction (SL/TP keys omitted when absent), the proven
IOC -> FOK -> RETURN filling fallback with REAL ``mt5.order_send()`` calls,
honest handling of None/exception/other retcodes, the fact that ``order_check``
is NOT a hard execution gate, the manual-order endpoint's demo safety gates,
and risk/lot sizing from the handoff (floor to step, never round up past the
intended risk, pip size derived from the symbol's digits).

Real Windows MT5 execution is NOT claimed here — see the release report.
"""
from __future__ import annotations

import sys
import time

import pytest


# --------------------------------------------------------------------------- #
# a strict fake MetaTrader5 package: the broker ENFORCES filling modes, so the
# IOC -> FOK -> RETURN chain is actually exercised
# --------------------------------------------------------------------------- #
class _Res:
    def __init__(self, retcode, comment="Done", order=555001, deal=666001, price=2400.30,
                 volume=0.03):
        self.retcode = retcode
        self.comment = comment
        self.order = order
        self.deal = deal
        self.price = price
        self.volume = volume
        self.request = None

    def _asdict(self):
        return {"retcode": self.retcode, "comment": self.comment, "order": self.order,
                "deal": self.deal, "price": self.price, "volume": self.volume}


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class StrictFakeMT5:
    """order_send enforces the accepted filling mode like a real broker."""

    __version__ = "5.0.6231"
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_INVALID_ORDER = 10029
    TRADE_RETCODE_INVALID_FILL = 10030
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    TRADE_ACTION_DEAL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_RETURN = 2
    ACCOUNT_TRADE_MODE_DEMO = 0
    ACCOUNT_TRADE_MODE_REAL = 2

    def __init__(self, *, accept_filling=2, fail_code=None, fail_comment="No money",
                 send_behaviour="result", trade_mode=0, digits=2, point=0.01):
        self.accept_filling = accept_filling        # the only mode this broker takes
        self.fail_code = fail_code                  # if set: every send returns this code
        self.fail_comment = fail_comment
        self.send_behaviour = send_behaviour        # "result" | "none" | "raise"
        self.trade_mode = trade_mode
        self.digits = digits
        self.point = point
        self.sent = []                              # every order_send request, in order
        self.order_send_calls = 0
        self.order_check_calls = 0
        self.selected = []

    def initialize(self, path=None, **kw):
        return True

    def shutdown(self):
        return True

    def version(self):
        return (500, 6230, "2026-10-01")

    def terminal_info(self):
        return _Obj(name="MetaTrader 5", company="Raw Trading Ltd", build=6230, connected=True,
                    trade_allowed=True, tradeapi_disabled=False, path=r"C:\MT5\terminal64.exe")

    def account_info(self):
        return _Obj(login=53071066, server="ICMarketsSC-Demo", trade_mode=self.trade_mode,
                    balance=200509.92, equity=200509.92, currency="USD", leverage=500,
                    trade_allowed=True, trade_expert=True, name="Operator")

    def symbol_select(self, symbol, enable=True):
        self.selected.append(symbol)
        return True

    def symbol_info(self, symbol):
        return _Obj(symbol=symbol, visible=True, digits=self.digits, point=self.point,
                    trade_tick_size=0.01, trade_tick_value=1.0, trade_contract_size=100.0,
                    volume_min=0.01, volume_max=100.0, volume_step=0.01,
                    trade_stops_level=0, freeze_level=0, trade_mode=4, trade_allowed=True,
                    filling_mode=0, spread=19, currency_profit="USD",
                    bid=2400.20, ask=2400.30)

    def symbol_info_tick(self, symbol):
        return _Obj(bid=2400.20, ask=2400.30, last=2400.25, spread=19,
                    time=int(time.time()), volume=42)

    def last_error(self):
        return (0, "Ok")

    def order_check(self, request):
        self.order_check_calls += 1
        return _Obj(retcode=0, comment="Done", margin=8.17)

    def order_send(self, request):
        self.order_send_calls += 1
        self.sent.append(dict(request))
        if self.send_behaviour == "none":
            return None
        if self.send_behaviour == "raise":
            raise RuntimeError("socket exploded while sending")
        if self.fail_code is not None:
            return _Res(self.fail_code, self.fail_comment)
        fill = int(request.get("type_filling", -1))
        if fill != self.accept_filling:
            return _Res(10030, "Unsupported filling mode")
        return _Res(10009, "Done")


@pytest.fixture()
def strict_pkg(monkeypatch):
    from app.mt5 import mt5_real
    pkg = StrictFakeMT5()
    monkeypatch.setattr(mt5_real, "mt5", pkg, raising=False)
    monkeypatch.setattr(mt5_real, "MT5_PACKAGE_AVAILABLE", True, raising=False)
    monkeypatch.setattr(mt5_real, "MT5_IMPORT_ERROR", "", raising=False)
    return pkg


@pytest.fixture()
def bridge(strict_pkg):
    from app.mt5.mt5_real import MT5RealBridge
    b = MT5RealBridge()
    b._connected = True
    b._terminal_build = 6230
    b._terminal_company = "Raw Trading Ltd"
    return b


# =========================================================================== #
# 1 — import guard: the app lives on Linux without MetaTrader5
# =========================================================================== #
def test_01_the_import_guard_keeps_the_app_importable_on_linux():
    import app.mt5.mt5_real as real
    import app.main                     # noqa: F401 — importing must never crash
    if "MetaTrader5" not in sys.modules:
        assert real.MT5_PACKAGE_AVAILABLE is False
        assert real.MT5_IMPORT_ERROR, "the import failure must be recorded, not swallowed"


def test_02_real_mode_never_silently_substitutes_the_simulator():
    from app.mt5.factory import build_bridge

    class _Cfg:
        mode = "real"
        path = ""
        login = 0
        password = ""
        server = ""
        timeout_ms = 1000

    b = build_bridge(cfg=_Cfg())
    # even when unusable, an explicit REAL request returns the real bridge,
    # failed, with the reason — never a simulator pretending to be MT5
    assert b.source == "MT5"
    assert b.available() in (True, False)
    if not b.available():
        st = b.status()
        assert st.get("last_error"), "the failure reason must be exposed"


# =========================================================================== #
# 2 — source labelling: a simulated fill is never a real MT5 execution
# =========================================================================== #
def test_03_simulator_results_are_labelled_and_never_real():
    from app.mt5.simulator import SimulatorBridge
    sim = SimulatorBridge()
    res = sim.place_order("XAUUSD", "buy", 0.01, sl_price=2390.0)
    assert res.source == "SIMULATOR"
    assert res.ok is True
    assert "simulated fill" in res.comment
    assert res.order_send_called is False      # no broker call was ever made


def test_04_real_bridge_results_carry_the_mt5_source(bridge, strict_pkg):
    res = bridge.place_order("XAUUSD", "buy", 0.03, sl_price=2370.30, tp_price=2470.30)
    assert res.source == "MT5"
    assert res.ok is True and res.retcode == 10009


# =========================================================================== #
# 3 — order construction: sl/tp keys OMITTED when absent (never 0.0)
# =========================================================================== #
def test_05_sl_tp_keys_are_omitted_when_absent(bridge, strict_pkg):
    res = bridge.place_order("XAUUSD", "buy", 0.03)          # no SL / no TP
    assert res.ok is True
    sent = strict_pkg.sent[-1]
    assert "sl" not in sent and "tp" not in sent, "sl/tp must be OMITTED, never sent as 0.0"
    assert sent["action"] == 1 and sent["symbol"] == "XAUUSD" and sent["volume"] == 0.03
    assert sent["type_time"] == 0                             # ORDER_TIME_GTC
    assert sent["comment"]


def test_06_sl_tp_keys_are_present_when_provided(bridge, strict_pkg):
    bridge.place_order("XAUUSD", "sell", 0.03, sl_price=2430.30, tp_price=2370.30)
    sent = strict_pkg.sent[-1]
    assert sent["sl"] == pytest.approx(2430.30) and sent["tp"] == pytest.approx(2370.30)
    assert sent["type"] == 1                                  # ORDER_TYPE_SELL


# =========================================================================== #
# 4 — the proven filling fallback: REAL order_send per mode, IOC -> FOK -> RETURN
# =========================================================================== #
def test_07_the_chain_sends_each_mode_for_real_until_one_is_accepted(bridge, strict_pkg):
    strict_pkg.accept_filling = 2                             # broker takes RETURN only
    res = bridge.place_order("XAUUSD", "buy", 0.03)
    assert res.ok is True and res.retcode == 10009
    assert [s["type_filling"] for s in strict_pkg.sent] == [1, 0, 2]   # IOC, FOK, RETURN
    assert res.call_count == 3 and len(res.attempts) == 3
    assert [a["filling"] for a in res.attempts] == ["IOC", "FOK", "RETURN"]
    assert res.attempts[0]["retcode"] == 10030 and res.attempts[1]["retcode"] == 10030
    assert res.attempts[2]["retcode"] == 10009
    assert res.order_send_called is True


def test_08_retcode_10029_also_advances_to_the_next_mode(bridge, strict_pkg):
    calls = {"n": 0}
    real_send = strict_pkg.order_send

    def send(req):
        calls["n"] += 1
        if calls["n"] == 1:
            strict_pkg.sent.append(dict(req))
            strict_pkg.order_send_calls += 1
            return _Res(10029, "Invalid order")
        return real_send(req)

    strict_pkg.order_send = send
    res = bridge.place_order("XAUUSD", "buy", 0.03)
    assert res.ok is True
    assert res.attempts[0]["retcode"] == 10029
    assert [s["type_filling"] for s in strict_pkg.sent] == [1, 0, 2]


def test_09_another_broker_error_stops_the_chain_immediately(bridge, strict_pkg):
    strict_pkg.fail_code = 10019
    strict_pkg.fail_comment = "No money"
    res = bridge.place_order("XAUUSD", "buy", 0.03)
    assert res.ok is False and res.retcode == 10019
    assert res.comment == "No money", "the broker's verbatim comment must travel back"
    assert strict_pkg.order_send_calls == 1, "arbitrary broker errors are NOT retried"
    assert res.call_count == 1


def test_10_a_none_result_is_honest_and_never_resubmitted(bridge, strict_pkg):
    strict_pkg.send_behaviour = "none"
    res = bridge.place_order("XAUUSD", "buy", 0.03)
    assert res.ok is False and res.retcode is None
    assert "returned None" in res.comment
    assert strict_pkg.order_send_calls == 1 and res.call_count == 1
    assert res.attempts[0]["outcome"] == "ORDER_SEND_RETURNED_NONE"


def test_11_an_exception_is_reported_verbatim(bridge, strict_pkg):
    strict_pkg.send_behaviour = "raise"
    res = bridge.place_order("XAUUSD", "buy", 0.03)
    assert res.ok is False and "exception" in res.comment
    assert strict_pkg.order_send_calls == 1


# =========================================================================== #
# 5 — order_check is NOT a hard execution gate
# =========================================================================== #
def test_12_place_order_never_consults_order_check(bridge, strict_pkg):
    res = bridge.place_order("XAUUSD", "buy", 0.03)
    assert res.ok is True
    assert strict_pkg.order_check_calls == 0, "the proven path sends; order_check is not involved"
    assert strict_pkg.order_send_calls >= 1


def test_13_send_market_order_sends_even_when_order_check_would_refuse(bridge, strict_pkg):
    def refused_check(request):
        strict_pkg.order_check_calls += 1
        return _Obj(retcode=10016, comment="Invalid stops", margin=0.0)

    strict_pkg.order_check = refused_check
    out = bridge.send_market_order({
        "action": 1, "symbol": "XAUUSD", "volume": 0.03, "type": 0, "price": 2400.30,
        "deviation": 20, "magic": 777000, "comment": "t", "type_time": 0,
        "type_filling": 2})
    assert strict_pkg.order_send_calls >= 1, "order_check must NEVER block order_send"
    assert out["called"] is True
    assert out.get("refused_by") != "mt5.order_check"
    assert out["check"]["ok"] is False            # diagnostic travels honestly
    assert out["check"].get("gate") is False


# =========================================================================== #
# 6 — manual-order endpoint: demo safety + MT5RealBridge + forensics
# =========================================================================== #
class _StubRealBridge:
    """A connected MT5RealBridge stand-in that records the place_order call."""

    name = "mt5_real"
    source = "MT5"

    def __init__(self, *, result=None, trade_mode=0, digits=2, point=0.01):
        self._connected = True
        self.placed = []
        self.result = result
        self.trade_mode = trade_mode
        self.digits = digits
        self.point = point

    def account_info(self):
        from app.mt5.bridge import AccountInfo
        return AccountInfo(login=53071066, server="ICMarketsSC-Demo", balance=10000.0,
                           equity=10000.0, is_demo=(self.trade_mode == 0), source=self.source)

    def latest_tick(self, symbol):
        from app.mt5.bridge import Tick
        return Tick(ts=time.time(), bid=2400.20, ask=2400.30, source=self.source)

    def symbol_info(self, symbol):
        from app.mt5.bridge import SymbolInfo
        return SymbolInfo(symbol=symbol, digits=self.digits, point=self.point,
                          trade_contract_size=100.0, source=self.source)

    def place_order(self, symbol, side, lots, sl_price=None, tp_price=None,
                    comment="", position_ticket=None):
        self.placed.append({"symbol": symbol, "side": side, "lots": lots,
                            "sl_price": sl_price, "tp_price": tp_price, "comment": comment})
        return self.result


def _manual_result(**kw):
    from app.mt5.bridge import OrderResult
    base = dict(ok=True, retcode=10009, comment="Done", order_id=555001, deal_id=666001,
                exec_price=2400.30, source="MT5", order_send_called=True, call_count=1,
                attempts=[{"filling": "IOC", "filling_mode": 1, "outcome":
                           "ORDER_SEND_RETURNED_RESULT", "retcode": 10009}],
                filling="ORDER_FILLING_IOC")
    base.update(kw)
    return OrderResult(**base)


@pytest.fixture()
def manual_client(client, monkeypatch):
    import app.api.routes as routes
    return client


def test_14_the_manual_endpoint_refuses_the_simulator_with_minus_1(client, monkeypatch):
    import app.api.routes as routes

    class _Sim:
        name = "simulator"
        source = "SIMULATOR"
        _connected = True

    monkeypatch.setattr(routes, "get_bridge", lambda: _Sim())
    res = client.post("/api/mt5/manual_order",
                      json={"symbol": "XAUUSD", "side": "BUY", "lots": 0.01})
    assert res.status_code == 400
    body = res.json()
    assert body["retcode"] == -1 and body["ok"] is False
    assert body["order_send"]["called"] is False


def test_15_the_manual_endpoint_hard_refuses_real_accounts(client, monkeypatch):
    import app.api.routes as routes

    stub = _StubRealBridge(trade_mode=2)
    monkeypatch.setattr(routes, "get_bridge", lambda: stub)
    fake = StrictFakeMT5(trade_mode=2)
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake)
    res = client.post("/api/mt5/manual_order",
                      json={"symbol": "XAUUSD", "side": "BUY", "lots": 0.01,
                            "sl_pips": 300, "confirm_demo": True})
    assert res.status_code == 403
    body = res.json()
    assert body["retcode"] == -2 and "SAFETY LOCK" in body["comment"]
    assert body["trade_mode"] == 2
    assert body["order_send"]["called"] is False
    assert stub.placed == [], "nothing may be sent to a REAL account"


def test_16_the_manual_endpoint_runs_through_mt5realbridge_place_order(client, monkeypatch):
    import app.api.routes as routes
    stub = _StubRealBridge(result=_manual_result())
    monkeypatch.setattr(routes, "get_bridge", lambda: stub)
    res = client.post("/api/mt5/manual_order",
                      json={"symbol": "XAUUSD", "side": "BUY", "lots": 0.03,
                            "sl_pips": 300, "tp_pips": 600, "confirm_demo": True})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True and body["retcode"] == 10009
    assert body["bridge_source"] == "MT5"
    # the ONE real bridge implementation was used
    assert len(stub.placed) == 1
    call = stub.placed[0]
    assert call["symbol"] == "XAUUSD" and call["side"] == "BUY" and call["lots"] == 0.03
    # pips -> prices from the symbol's own digits/point (2-digit XAUUSD: pip=0.01)
    assert call["sl_price"] == pytest.approx(2400.30 - 300 * 0.01)
    assert call["tp_price"] == pytest.approx(2400.30 + 600 * 0.01)
    # V6 forensics travel with the result
    assert body["order_send"]["called"] is True and body["order_send"]["call_count"] >= 1
    assert body["order_send"]["attempts"], "the send attempts must be diagnosable"
    assert body["order"] == 555001 and body["deal"] == 666001


def test_17_risk_sizing_floors_to_the_step_and_never_rounds_up(client, monkeypatch):
    import app.api.routes as routes
    stub = _StubRealBridge(result=_manual_result())
    monkeypatch.setattr(routes, "get_bridge", lambda: stub)
    # amount $10 with a 300-pip (=3.00) stop on 100 contract size:
    #   loss_per_lot = 100 * 3.00 = 300  -> raw_lots = 10/300 = 0.0333...
    #   floored to the 0.01 step => 0.03 lots (never 0.04)
    res = client.post("/api/mt5/manual_order",
                      json={"symbol": "XAUUSD", "side": "BUY", "amount_usd": 10.0,
                            "sl_pips": 300, "confirm_demo": True})
    assert res.status_code == 200, res.text
    body = res.json()
    assert stub.placed[0]["lots"] == pytest.approx(0.03)
    assert body["sizing_mode"] == "RISK"
    assert body["projected_loss_usd"] <= 10.0


def test_18_a_risk_budget_below_broker_minimum_skips_the_order(client, monkeypatch):
    import app.api.routes as routes
    stub = _StubRealBridge(result=_manual_result())
    monkeypatch.setattr(routes, "get_bridge", lambda: stub)
    # $0.10 risk with a 300-pip stop -> raw 0.00033 lots < volume_min 0.01
    res = client.post("/api/mt5/manual_order",
                      json={"symbol": "XAUUSD", "side": "BUY", "amount_usd": 0.10,
                            "sl_pips": 300, "confirm_demo": True})
    assert res.status_code == 400
    body = res.json()
    assert body["order_send"]["called"] is False
    assert stub.placed == [], "below-minimum sizes are skipped, never clamped up"
    assert "below the broker minimum" in body["comment"]


def test_19_pip_size_is_derived_from_digits(client, monkeypatch):
    import app.api.routes as routes
    stub = _StubRealBridge(result=_manual_result(), digits=5, point=0.00001)
    monkeypatch.setattr(routes, "get_bridge", lambda: stub)
    res = client.post("/api/mt5/manual_order",
                      json={"symbol": "EURUSD", "side": "BUY", "lots": 0.10,
                            "sl_pips": 100, "confirm_demo": True})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["pip_size"] == pytest.approx(0.0001)      # 10 * point on a 5-digit feed
    assert stub.placed[0]["sl_price"] == pytest.approx(2400.30 - 100 * 0.0001)
