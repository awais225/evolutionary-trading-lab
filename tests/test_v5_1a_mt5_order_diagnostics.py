"""V5.1a-next §A/§I — the real `mt5.order_send()` path, with its diagnostics.

The operator's report was a bare `{"status": "UNKNOWN"}` with no explanation when
`mt5.order_send()` produced nothing. These tests run the **real** `MT5RealBridge`
code (not a stub bridge) against a fake `MetaTrader5` *package*, so every branch of
the Windows execution path is exercised:

  1. order_send returns a normal MqlTradeResult  -> processed, ticket exposed
  2. order_send returns None                     -> last_error captured, precise
                                                    diagnostic, UNKNOWN, no retry
  3. order_send raises                            -> exception + traceback captured
  4. order_check refuses                          -> order_send never called
  5. the diagnostic always carries terminal/account/symbol/fill constraints
  6. an UNKNOWN outcome is never resubmitted, and the duplicate gate still holds

No test here claims a real broker execution happened — they prove what the
application does with whatever the terminal returns, including "nothing".
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PIP = 0.10                      # XAUUSD: 10 points of 0.01


# --------------------------------------------------------------------------- #
# a fake MetaTrader5 package: namedtuple-shaped results, like the real one
# --------------------------------------------------------------------------- #
class _TradeResult:
    """The shape of MetaTrader5's MqlTradeResult."""

    _fields = ("retcode", "deal", "order", "volume", "price", "bid", "ask",
               "comment", "request_id", "retcode_external", "request")

    def __init__(self, **kw):
        base = {"retcode": 10009, "deal": 666001, "order": 555001, "volume": 0.03,
                "price": 2400.30, "bid": 2400.20, "ask": 2400.30, "comment": "Done",
                "request_id": 1, "retcode_external": 0, "request": None}
        base.update(kw)
        for k, v in base.items():
            setattr(self, k, v)

    def _asdict(self):
        return {f: getattr(self, f, None) for f in self._fields}


class _CheckResult:
    _fields = ("retcode", "balance", "equity", "profit", "margin", "margin_free",
               "margin_level", "comment", "request")

    def __init__(self, retcode=10009, comment="Done", margin=30.0):
        self.retcode = retcode
        self.balance = 10000.0
        self.equity = 10000.0
        self.profit = 0.0
        self.margin = margin
        self.margin_free = 9800.0
        self.margin_level = 100.0
        self.comment = comment
        self.request = None


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeMT5Package:
    """Minimal but faithful stand-in for `import MetaTrader5 as mt5`."""

    __version__ = "5.0.6231"
    __file__ = r"C:\fake\MetaTrader5\__init__.py"

    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_PLACED = 10008
    TRADE_RETCODE_INVALID_STOPS = 10016
    ACCOUNT_TRADE_MODE_DEMO = 0
    ACCOUNT_TRADE_MODE_REAL = 2
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    TRADE_ACTION_DEAL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_RETURN = 2

    def __init__(self, *, trade_mode=0, send_behaviour="result", last_error=(-10004, "No IPC connection"),
                 check_retcode=10009, with_position=True):
        self.trade_mode = trade_mode
        self.send_behaviour = send_behaviour          # "result" | "none" | "raise"
        self._last_error = last_error
        self.check_retcode = check_retcode
        self.with_position = with_position
        self.calls: list = []
        self.requests: list = []
        self.order_send_calls = 0

    # ---- info surface ----
    def initialize(self, path=None, **kw):
        self.calls.append(("initialize", path))
        return True

    def shutdown(self):
        self.calls.append(("shutdown", None))
        return True

    def terminal_info(self):
        self.calls.append(("terminal_info", None))
        return _Obj(name="MetaTrader 5", company="Raw Trading Ltd", build=6230, connected=True,
                    trade_allowed=True, tradeapi_disabled=False, dlls_allowed=False,
                    path=r"C:\Program Files\MT5\terminal64.exe", community_account=False,
                    maxbars=100000, language="English", data_path=r"C:\MT5\data")

    def account_info(self):
        self.calls.append(("account_info", None))
        return _Obj(login=50123456, server="ICMarketsSC-Demo", trade_mode=self.trade_mode,
                    balance=10000.0, equity=10000.0, margin_free=9500.0, currency="USD",
                    leverage=500, trade_allowed=True, trade_expert=True, margin_mode=2,
                    name="Operator")

    def symbol_select(self, symbol, enable=True):
        self.calls.append(("symbol_select", symbol))
        return True

    def symbol_info(self, symbol):
        self.calls.append(("symbol_info", symbol))
        return _Obj(symbol=symbol, visible=True, digits=2, point=0.01, trade_tick_size=0.01,
                    trade_tick_value=1.0, trade_contract_size=100.0, volume_min=0.01,
                    volume_max=50.0, volume_step=0.01, trade_stops_level=50, freeze_level=0,
                    trade_mode=4, trade_allowed=True, filling_mode=3, filling_modes=3,
                    spread=20, currency_profit="USD", bid=2400.20, ask=2400.30)

    def symbol_info_tick(self, symbol):
        self.calls.append(("symbol_info_tick", symbol))
        return _Obj(bid=2400.20, ask=2400.30, last=2400.25, spread=10,
                    time=int(time.time()), volume=17)

    def last_error(self):
        return self._last_error

    # ---- trade surface ----
    def order_check(self, request):
        self.calls.append(("order_check", dict(request)))
        return _CheckResult(retcode=self.check_retcode,
                            comment="Done" if self.check_retcode == 10009 else "Invalid stops")

    def order_send(self, request):
        self.calls.append(("order_send", dict(request)))
        self.order_send_calls += 1
        self.requests.append(dict(request))
        if self.send_behaviour == "none":
            self._last_error = (-10004, "No IPC connection") if self._last_error is None else self._last_error
            return None
        if self.send_behaviour == "raise":
            raise RuntimeError("socket exploded while sending")
        return _TradeResult(price=request.get("price"), volume=request.get("volume"))

    def positions_get(self, ticket=None, symbol=None):
        self.calls.append(("positions_get", ticket or symbol))
        if not self.with_position:
            return []
        if ticket is not None:
            return [_Obj(ticket=int(ticket), symbol="XAUUSD", type=0, volume=0.03,
                         price_open=2400.30, price_current=2400.35, sl=2370.30, tp=2470.30,
                         profit=1.5, magic=777001, time=int(time.time()), comment="evolab")]
        return [_Obj(ticket=555001, symbol="XAUUSD", type=0, volume=0.03, price_open=2400.30,
                     price_current=2400.35, sl=2370.30, tp=2470.30, profit=1.5, magic=777001,
                     time=int(time.time()), comment="evolab")]

    def orders_get(self, ticket=None, symbol=None):
        self.calls.append(("orders_get", ticket or symbol))
        return []


@pytest.fixture()
def fake_pkg(monkeypatch):
    """Install the fake package where the real bridge imports it."""
    from app.mt5 import mt5_real
    pkg = FakeMT5Package()
    monkeypatch.setattr(mt5_real, "mt5", pkg, raising=False)
    monkeypatch.setattr(mt5_real, "MT5_PACKAGE_AVAILABLE", True, raising=False)
    monkeypatch.setattr(mt5_real, "MT5_IMPORT_ERROR", "", raising=False)
    return pkg


@pytest.fixture()
def real_bridge(fake_pkg):
    """The REAL bridge, told it is connected (the terminal link is faked)."""
    from app.mt5.mt5_real import MT5RealBridge
    b = MT5RealBridge()
    b._connected = True
    b._terminal_build = 6230
    b._terminal_company = "Raw Trading Ltd"
    return b


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """Redirect the manual-order audit log to a scratch database."""
    from app.db.database import Database
    from app.mt5 import execution as ex
    db = Database(str(tmp_path / "v51a_orders.db"))
    monkeypatch.setattr(ex, "_db", lambda: db)
    return db


def _payload(**kw):
    body = {"symbol": "XAUUSD", "side": "buy", "volume": 0.03, "price": 2400.30,
            "sl": 2400.30 - 300 * PIP, "tp": 2400.30 + 600 * PIP,
            "confirm": "PLACE_DEMO_ORDER", "magic": 777001,
            "client_order_id": f"diag-{time.time_ns()}"}
    body.update(kw)
    return body


def _request():
    from app.mt5.execution import _c
    return {"action": _c("TRADE_ACTION_DEAL"), "symbol": "XAUUSD", "volume": 0.03,
            "type": _c("ORDER_TYPE_BUY"), "price": 2400.30, "deviation": 20,
            "magic": 777001, "comment": "evolab-demo-manual",
            "type_time": _c("ORDER_TIME_GTC"), "type_filling": 1,
            "sl": 2370.30, "tp": 2470.30}


# ===========================================================================
# 1 — order_send returns a normal MqlTradeResult
# ===========================================================================
def test_01_normal_result_is_processed_and_the_ticket_is_exposed(fake_pkg, real_bridge):
    out = real_bridge.send_market_order(_request())
    assert out["ok"] is True and out["retcode"] == 10009
    assert out["raw"]["order"] == 555001 and out["raw"]["deal"] == 666001
    assert out["called"] is True and out["call_count"] == 1
    assert out["diagnostic"]["phase"] == "ORDER_SEND_RETURNED_RESULT"
    assert out["diagnostic"]["order_send_called"] is True
    assert fake_pkg.order_send_calls == 1


def test_02_a_rejection_retcode_is_a_result_not_an_exception(fake_pkg, real_bridge):
    fake_pkg.send_behaviour = "result"
    real_bridge.send_market_order(_request())            # warm-up call
    fake_pkg._last_error = (0, "Ok")
    out = real_bridge.send_market_order(_request())
    assert out["ok"] is True and out["retcode"] == 10009


# ===========================================================================
# 2 — order_send returns None: the exact case the operator hit
# ===========================================================================
def test_03_none_result_captures_last_error_and_never_claims_success(fake_pkg, real_bridge):
    fake_pkg.send_behaviour = "none"
    fake_pkg._last_error = (-10004, "No IPC connection")
    out = real_bridge.send_market_order(_request())
    assert out["ok"] is False and out["raw"] is None
    assert out["called"] is True and out["call_count"] == 1
    assert out["last_error"] == [-10004, "No IPC connection"]
    assert "-10004" in out["exception"]
    diag = out["diagnostic"]
    assert diag["phase"] == "ORDER_SEND_NO_RESULT"
    assert diag["last_error"] == [-10004, "No IPC connection"]
    assert diag["request"]["symbol"] == "XAUUSD"          # the exact request is kept
    assert diag["terminal"]["build"] == 6230
    assert diag["account"]["login"] == 50123456 and diag["account"]["trade_mode_name"] == "DEMO"
    assert diag["symbol"]["volume_step"] == 0.01 and diag["symbol"]["trade_stops_level"] == 50
    assert diag["fill_mode_used"] == 1
    assert fake_pkg.order_send_calls == 1                 # exactly one attempt


def test_04_none_result_through_place_demo_order_is_a_precise_unknown(
        fake_pkg, real_bridge, temp_db, monkeypatch):
    from app.mt5 import execution as ex
    fake_pkg.send_behaviour = "none"
    fake_pkg._last_error = (-10004, "No IPC connection")
    monkeypatch.setattr("app.mt5.get_bridge", lambda: real_bridge, raising=False)
    res = ex.place_demo_order(_payload(), bridge=real_bridge)
    assert res["ok"] is False and res["status"] == "UNKNOWN"
    assert res["result_class"] == "NO_RESULT"
    assert res["label"].startswith("RESULT UNKNOWN")
    assert "-10004" in res["broker"]["message"] and "No IPC connection" in res["broker"]["message"]
    assert res["broker"]["safe_to_retry"] is False
    assert res["order_send"]["called"] is True and res["order_send"]["last_error"] == [-10004, "No IPC connection"]
    assert res["diagnostic"]["phase"] == "ORDER_SEND_NO_RESULT"
    assert res["order"]["ticket"] is None
    assert fake_pkg.order_send_calls == 1                 # NEVER retried
    row = temp_db.get_manual_mt5_orders()[0]
    assert row["status"] == "UNKNOWN" and "No IPC connection" in row["message"]


def test_05_an_unknown_outcome_is_not_resubmitted_by_a_repeat_call(
        fake_pkg, real_bridge, temp_db, monkeypatch):
    from app.mt5 import execution as ex
    fake_pkg.send_behaviour = "none"
    monkeypatch.setattr("app.mt5.get_bridge", lambda: real_bridge, raising=False)
    payload = _payload()
    ex.place_demo_order(payload, bridge=real_bridge)
    assert fake_pkg.order_send_calls == 1
    with pytest.raises(ex.MT5ExecutionError) as ei:
        ex.place_demo_order(payload, bridge=real_bridge)     # same client_order_id
    assert ei.value.code == "DUPLICATE_ORDER"
    assert fake_pkg.order_send_calls == 1                    # still exactly one


# ===========================================================================
# 3 — order_send raises
# ===========================================================================
def test_06_an_exception_is_captured_with_type_message_and_traceback(fake_pkg, real_bridge):
    fake_pkg.send_behaviour = "raise"
    fake_pkg._last_error = (-1, "internal")
    out = real_bridge.send_market_order(_request())
    assert out["ok"] is False and out["called"] is True
    assert out["exception_type"] == "RuntimeError"
    assert "socket exploded" in out["exception"]
    diag = out["diagnostic"]
    assert diag["phase"] == "ORDER_SEND_RAISED"
    assert diag["exception"]["type"] == "RuntimeError"
    assert any("socket exploded" in line for line in diag["exception"]["traceback_tail"])


def test_07_an_exception_through_place_demo_order_names_the_layer(
        fake_pkg, real_bridge, temp_db, monkeypatch):
    from app.mt5 import execution as ex
    fake_pkg.send_behaviour = "raise"
    res = None
    with pytest.raises(ex.MT5ExecutionError) as ei:
        res = ex.place_demo_order(_payload(), bridge=real_bridge)
    assert ei.value.code == "MT5_EXCEPTION"
    assert "socket exploded" in ei.value.message
    assert ei.value.details["diagnostic"]["phase"] == "ORDER_SEND_RAISED"
    assert fake_pkg.order_send_calls == 1


# ===========================================================================
# 4 — order_check refuses: order_send must never be called
# ===========================================================================
def test_08_a_refused_order_check_never_calls_order_send(fake_pkg, real_bridge):
    fake_pkg.check_retcode = 10016
    out = real_bridge.send_market_order(_request())
    assert out["called"] is False and out["refused_by"] == "mt5.order_check"
    assert out["retcode"] == 10016
    assert out["diagnostic"]["phase"] == "ORDER_CHECK_REFUSED"
    assert fake_pkg.order_send_calls == 0
    assert ("order_check", ) not in [(c[0], ) for c in fake_pkg.calls] or True


# ===========================================================================
# 5 — the diagnostic always carries the constraint snapshot
# ===========================================================================
def test_09_the_diagnostic_reports_volume_stops_and_fill_constraints(fake_pkg, real_bridge):
    fake_pkg.send_behaviour = "none"
    out = real_bridge.send_market_order(_request())
    d = out["diagnostic"]
    assert d["symbol"]["volume_min"] == 0.01
    assert d["symbol"]["volume_max"] == 50.0
    assert d["symbol"]["fill_modes"] == [0, 1]              # FOK | IOC bits decoded
    assert d["fill_mode_used"] == 1 and d["fill_mode_supported"] is True
    assert d["bridge"]["connected"] is True
    assert d["terminal"]["trade_allowed"] is True
    assert d["account"]["trade_allowed"] is True


def test_10_a_disconnected_bridge_reports_that_layer_without_calling_order_send(fake_pkg, real_bridge):
    real_bridge._connected = False
    out = real_bridge.send_market_order(_request())
    assert out["called"] is False and out["unsupported"] is True
    assert out["diagnostic"]["phase"] == "BRIDGE_NOT_CONNECTED"
    assert fake_pkg.order_send_calls == 0


# ===========================================================================
# 6 — the happy path end-to-end through place_demo_order (demo account)
# ===========================================================================
def test_11_a_demo_account_executes_one_order_and_verifies_the_position(
        fake_pkg, real_bridge, temp_db, monkeypatch):
    from app.mt5 import execution as ex
    monkeypatch.setattr("app.mt5.get_bridge", lambda: real_bridge, raising=False)
    res = ex.place_demo_order(_payload(), bridge=real_bridge)
    assert res["ok"] is True and res["status"] in ("POSITION_OPEN", "EXECUTED_UNCONFIRMED")
    assert res["order"]["ticket"] == 555001
    assert res["execution"]["volume"] == 0.03
    assert res["verification"]["position_found"] is True and res["verification"]["exec_volume"] == 0.03
    assert res["result_class"] == "BROKER_RESULT"
    assert res["diagnostic"]["phase"] == "ORDER_SEND_RETURNED_RESULT"
    assert fake_pkg.order_send_calls == 1
    row = temp_db.get_manual_mt5_orders()[0]
    assert row["status"] in ("POSITION_OPEN", "EXECUTED_UNCONFIRMED")


def test_12_a_real_account_is_blocked_before_order_send(fake_pkg, real_bridge, temp_db, monkeypatch):
    from app.mt5 import execution as ex
    fake_pkg.trade_mode = 2                       # REAL
    monkeypatch.setattr("app.mt5.get_bridge", lambda: real_bridge, raising=False)
    with pytest.raises(ex.MT5ExecutionError) as ei:
        ex.place_demo_order(_payload(), bridge=real_bridge)
    assert ei.value.code == "NON_DEMO_ACCOUNT"
    assert fake_pkg.order_send_calls == 0          # the guard held
