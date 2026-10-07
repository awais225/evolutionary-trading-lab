"""V5.2 §1/§2/§3/§20 — MT5 filling modes and order_check semantics, on the EXACT
facts the Windows diagnostic reported.

The fake `MetaTrader5` package below reproduces the operator's terminal:

* XAUUSD, digits 2, point 0.01, tick size 0.01, tick value 1.0, volume 0.01…100 step 0.01
* ``symbol_info().filling_mode == 0``  → the SYMBOL_FILLING_MODE bitmask has neither
  SYMBOL_FILLING_FOK (1) nor SYMBOL_FILLING_IOC (2), so market orders use the
  Return fill policy (``ORDER_FILLING_RETURN == 2``)
* ``order_check`` behaves like the real terminal: retcode **0** with comment
  ``'Done'`` and a computed margin for a valid request; retcode ``10030``
  ("Unsupported filling mode") for the IOC value the project used to send
* ``order_send`` returns a normal MqlTradeResult (Done, tickets) for the accepted
  request, and ``None`` when the terminal has no result to give

Everything here runs the REAL bridge (`MT5RealBridge`) and the REAL executor
(`execution.place_demo_order`); only the terminal is a double. No claim is made
that a broker executed anything — the point is that the *project* now sends a
request this terminal can accept, resolves the filling mode from metadata instead
of guessing, reads a passing check as a pass, and captures `last_error` when the
terminal returns nothing.
"""
from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

from app.mt5 import order_semantics as sem


# ===========================================================================
# 1. the two filling enumerations
# ===========================================================================
class Spec:
    """A symbol object shaped like the project's SymbolInfo / the MT5 tuple."""

    def __init__(self, *, bitmask=None, decoded=None, spread_points=19.0):
        if bitmask is not None:
            self.filling_mode = bitmask
        if decoded is not None:
            self.filling_modes = decoded
        self.spread_points = spread_points


def test_01_the_two_enumerations_do_not_share_values():
    assert (sem.ORDER_FILLING_FOK, sem.ORDER_FILLING_IOC, sem.ORDER_FILLING_RETURN) == (0, 1, 2)
    assert (sem.SYMBOL_FILLING_FOK, sem.SYMBOL_FILLING_IOC) == (1, 2)
    # the trap: bit 1 (SYMBOL_FILLING_FOK) is ORDER_FILLING_IOC, not FOK
    assert sem.SYMBOL_FILLING_FOK != sem.ORDER_FILLING_FOK


def test_02_a_zero_bitmask_means_the_return_fill_policy():
    """The operator's XAUUSD: 'filling modes: ORDER_FILLING_RETURN'."""
    cands = sem.filling_candidates(Spec(bitmask=0))
    assert [c["value"] for c in cands] == [sem.ORDER_FILLING_RETURN]
    assert "bitmask is 0" in cands[0]["why"]
    assert sem.pick_filling(Spec(bitmask=0)) == 2


def test_03_a_fok_ioc_bitmask_is_translated_not_reused():
    assert [c["value"] for c in sem.filling_candidates(Spec(bitmask=sem.SYMBOL_FILLING_FOK))] == \
        [sem.ORDER_FILLING_FOK, sem.ORDER_FILLING_RETURN]
    ioc_first = [c["value"] for c in sem.filling_candidates(Spec(bitmask=sem.SYMBOL_FILLING_IOC))]
    assert ioc_first[0] == sem.ORDER_FILLING_IOC
    both = [c["value"] for c in sem.filling_candidates(Spec(bitmask=3))]
    assert both[0] == sem.ORDER_FILLING_IOC and sem.ORDER_FILLING_FOK in both
    assert both[-1] == sem.ORDER_FILLING_RETURN          # kept as a probe candidate


def test_04_a_decoded_list_from_the_bridge_wins():
    cands = sem.filling_candidates(Spec(decoded=[2]))
    assert [c["value"] for c in cands] == [2]
    assert "decoded" in cands[0]["why"]


def test_05_metadata_consistency_check():
    assert sem.allowed_filling_by_bitmask(Spec(bitmask=0), 2) is True
    assert sem.allowed_filling_by_bitmask(Spec(bitmask=0), 1) is False   # IOC refused
    assert sem.allowed_filling_by_bitmask(Spec(bitmask=3), 1) is True
    assert sem.allowed_filling_by_bitmask(Spec(bitmask=None, decoded=[2]), 1) is False
    assert sem.allowed_filling_by_bitmask(Spec(bitmask=None), 1) is True  # unknown -> keep


def test_06_the_bridge_decoder_maps_the_bitmask_correctly():
    from app.mt5.mt5_real import _filling_modes

    class T:  # the raw MT5 tuple shape
        def __init__(self, m):
            self.filling_mode = m

    assert _filling_modes(T(0)) == [2]          # was [2, 1] (RETURN, IOC) — the old bug
    assert _filling_modes(T(2)) == [1]
    assert _filling_modes(T(1)) == [0]
    assert _filling_modes(T(3)) == [1, 0]


# ===========================================================================
# 2. order_check semantics (the operator's retcode 0 / 'Done')
# ===========================================================================
def test_07_retcode_zero_with_done_is_a_pass():
    v = sem.interpret_check_result({"retcode": 0, "comment": "Done", "margin": 8.17,
                                    "margin_free": 200501.75, "balance": 200509.92})
    assert v["ok"] is True
    assert v["retcode"] == 0 and v["retcode_name"] == "TRADE_RETCODE_OK/UNDEFINED (0)"
    assert "PASSING MqlTradeCheckResult" in v["rule"]
    assert v["margin"] == 8.17                      # the computed margin proves it ran


def test_08_an_error_retcode_is_a_refusal_with_its_name():
    for code, name in ((10030, "TRADE_RETCODE_INVALID_FILL"),
                       (10013, "TRADE_RETCODE_INVALID"),
                       (10014, "TRADE_RETCODE_INVALID_VOLUME"),
                       (10016, "TRADE_RETCODE_INVALID_STOPS"),
                       (10019, "TRADE_RETCODE_NO_MONEY")):
        v = sem.interpret_check_result({"retcode": code, "comment": "no"})
        assert v["ok"] is False, code
        assert name in v["retcode_name"]


def test_09_a_pass_code_with_a_refusing_comment_is_not_a_pass():
    v = sem.interpret_check_result({"retcode": 0, "comment": "Unsupported filling mode"})
    assert v["ok"] is False
    assert "refuses the request" in v["rule"]


def test_10_an_explicit_done_or_placed_check_code_passes():
    assert sem.interpret_check_result({"retcode": 10009, "comment": "Done"})["ok"] is True
    assert sem.interpret_check_result({"retcode": 10008, "comment": "Placed"})["ok"] is True


def test_11_no_result_object_is_not_a_pass():
    v = sem.interpret_check_result(None)
    assert v["ok"] is False and v["retcode"] is None


def test_12_namedtuple_results_are_understood():
    """Mql* results arrive as namedtuples from the package, dicts from doubles."""
    Check = __import__("collections").namedtuple(
        "Check", "retcode balance equity profit margin margin_free margin_level comment request")
    v = sem.interpret_check_result(Check(0, 200509.92, 200509.92, 0.0, 8.17, 200501.75,
                                         100.0, "Done", None))
    assert v["ok"] is True and v["margin"] == 8.17


def test_13_last_error_codes_are_named():
    assert "no IPC connection" in sem.last_error_name([-10004, "No IPC connection"])
    assert "-10003" in sem.last_error_name([-10003, "IPC initialize failed, Process create failed"])
    assert "unavailable" in sem.last_error_name(None)


# ===========================================================================
# 3. dynamic resolution against the terminal's own order_check
# ===========================================================================
def test_14_resolve_filling_probes_until_the_terminal_accepts():
    """Mirrors the operator's case: IOC refused (10030), RETURN accepted."""
    seen = []

    def check(req):
        seen.append(req["type_filling"])
        if req["type_filling"] == 2:
            return {"retcode": 0, "comment": "Done", "margin": 8.17}
        return {"retcode": 10030, "comment": "Unsupported filling mode"}

    out = sem.resolve_filling(Spec(bitmask=0), None, {"symbol": "XAUUSD", "type_filling": 1},
                              check_fn=check)
    assert out["ok"] is True and out["filling"] == 2
    assert seen == [1, 2]                                  # the caller's value first, then RETURN
    refused = out["probes"][0]
    assert refused["retcode"] == 10030 and refused["ok"] is False
    assert "INVALID_FILL" in refused["retcode_name"]


def test_15_resolve_filling_reports_when_nothing_is_accepted():
    out = sem.resolve_filling(Spec(bitmask=0), None, {"type_filling": 2},
                              check_fn=lambda req: {"retcode": 10016, "comment": "Invalid stops"})
    assert out["ok"] is False
    assert "must not be sent" in out["resolution"]
    assert out["probes"][0]["ok"] is False


def test_16_resolve_filling_without_order_check_keeps_metadata_order():
    class NoCheck:
        pass

    out = sem.resolve_filling(Spec(bitmask=0), NoCheck(), {"type_filling": 1})
    assert out["ok"] is False and out["filling"] == 2
    assert out["probes"][0]["checked"] is False


# ===========================================================================
# 4. deviation is in points (2.0 USD on this symbol)
# ===========================================================================
def test_17_deviation_is_floored_at_three_spreads():
    d = sem.effective_deviation(Spec(bitmask=0, spread_points=19.0), 20)
    assert d["value"] == 57 and d["floor_from_spread"] == 57
    big = sem.effective_deviation(Spec(bitmask=0, spread_points=19.0), 300)
    assert big["value"] == 300
    wide = sem.effective_deviation(Spec(bitmask=0, spread_points=500.0), 20)
    assert wide["value"] == 200                            # capped


# ===========================================================================
# 5. the real bridge against the operator's terminal
# ===========================================================================
class FakeMT5Terminal:
    """Reproduces the Windows diagnostic: demo account, XAUUSD, RETURN-only fill."""

    __version__ = "5.0.6231"
    ACCOUNT_TRADE_MODE_DEMO = 0
    ACCOUNT_TRADE_MODE_REAL = 2
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    TRADE_ACTION_DEAL = 1
    ORDER_TIME_GTC = 0
    TRADE_RETCODE_DONE = 10009
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_RETURN = 2
    SYMBOL_FILLING_FOK = 1
    SYMBOL_FILLING_IOC = 2

    def __init__(self, *, trade_mode=0, send_behaviour="result"):
        self.trade_mode = trade_mode
        self.send_behaviour = send_behaviour
        self.order_send_calls = 0
        self.order_check_calls = 0
        self.checked_fillings = []
        self.sent_requests = []
        self._last_error = (0, "Ok")
        self.positions: dict = {}

    # --- info ---
    def initialize(self, path=None, **kw):
        return True

    def shutdown(self):
        return True

    def version(self):
        return (500, 6230, "2026-09-27")

    def terminal_info(self):
        return _Obj(name="MetaTrader 5 IC Markets Global", company="Raw Trading Ltd",
                    path=r"C:\Program Files\MetaTrader 5 IC Markets Global", build=6230,
                    connected=True, trade_allowed=True, tradeapi_disabled=False,
                    dlls_allowed=True, maxbars=100000, language="English")

    def account_info(self):
        return _Obj(login=53071066, server="ICMarketsSC-Demo", trade_mode=self.trade_mode,
                    balance=200509.92, equity=200509.92, margin_free=200501.75,
                    currency="USD", leverage=500, trade_allowed=True, trade_expert=True,
                    margin_mode=2, name="Operator")

    def symbol_select(self, symbol, enable=True):
        return True

    def symbol_info(self, symbol):
        # exactly the diagnostic's XAUUSD line, including filling_mode = 0
        return _Obj(symbol=symbol, visible=True, digits=2, point=0.01, trade_tick_size=0.01,
                    trade_tick_value=1.0, trade_contract_size=100.0, volume_min=0.01,
                    volume_max=100.0, volume_step=0.01, trade_stops_level=0, freeze_level=0,
                    trade_mode=4, trade_allowed=True, filling_mode=0, spread=19,
                    currency_profit="USD", bid=4086.56, ask=4086.75, spread_points=19.0)

    def symbol_info_tick(self, symbol):
        return _Obj(bid=4086.56, ask=4086.75, last=4086.66, spread=19,
                    time=int(time.time()), volume=42)

    def last_error(self):
        return self._last_error

    # --- trading surface ---
    def order_check(self, request):
        self.order_check_calls += 1
        fill = int(request.get("type_filling"))
        self.checked_fillings.append(fill)
        if fill != 2:                       # this symbol accepts Return only
            return _check(10030, "Unsupported filling mode", 0.0)
        return _check(0, "Done", 8.17)

    def order_send(self, request):
        self.order_send_calls += 1
        self.sent_requests.append(dict(request))
        if self.send_behaviour == "none":
            self._last_error = (-10004, "No IPC connection")
            return None
        if self.send_behaviour == "raise":
            raise RuntimeError("terminal socket closed")
        self._last_error = (0, "Ok")
        ticket = 555001
        self.positions[ticket] = _Obj(ticket=ticket, symbol=request["symbol"],
                                      type=request.get("type", 0),
                                      volume=request["volume"],
                                      price_open=request["price"],
                                      price_current=request["price"],
                                      sl=request.get("sl"), tp=request.get("tp"),
                                      profit=0.0, magic=request.get("magic", 0),
                                      time=int(time.time()), comment="done")
        return _result(10009, deal=666001, order=ticket,
                       volume=request["volume"], price=request["price"],
                       comment="Done")

    def positions_get(self, ticket=None, symbol=None):
        vals = list(self.positions.values())
        if ticket is not None:
            return [p for p in vals if int(p.ticket) == int(ticket)]
        if symbol:
            return [p for p in vals if p.symbol == symbol]
        return vals

    def orders_get(self, ticket=None, symbol=None):
        return []


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


from collections import namedtuple as _nt

_Check = _nt("Check", "retcode balance equity profit margin margin_free margin_level comment request")
_Result = _nt("Result", "retcode deal order volume price bid ask comment request_id "
                        "retcode_external request")


def _check(retcode, comment, margin, margin_free=200501.75):
    """A full MqlTradeCheckResult, the way the package returns it."""
    return _Check(retcode=retcode, balance=200509.92, equity=200509.92, profit=0.0,
                  margin=margin, margin_free=margin_free, margin_level=100.0,
                  comment=comment, request=None)


def _result(retcode, **kw):
    """A full MqlTradeResult, the way the package returns it."""
    base = dict(retcode=retcode, deal=0, order=0, volume=0.0, price=0.0, bid=4086.56,
                ask=4086.75, comment="", request_id=1, retcode_external=0, request=None)
    base.update(kw)
    return _Result(**base)


def _install(fake, monkeypatch):
    from app.mt5 import mt5_real
    monkeypatch.setattr(mt5_real, "mt5", fake, raising=False)
    monkeypatch.setattr(mt5_real, "MT5_PACKAGE_AVAILABLE", True, raising=False)
    monkeypatch.setattr(mt5_real, "MT5_IMPORT_ERROR", "", raising=False)


@pytest.fixture()
def terminal(monkeypatch):
    fake = FakeMT5Terminal()
    _install(fake, monkeypatch)
    return fake


@pytest.fixture()
def bridge(terminal):
    from app.mt5.mt5_real import MT5RealBridge
    b = MT5RealBridge()
    b._connected = True
    return b


def _request(filling=1):
    """The project's own request shape, with the mode the old code chose (IOC)."""
    return {"action": 1, "symbol": "XAUUSD", "volume": 0.03, "type": 0, "price": 4086.75,
            "deviation": 20, "magic": 777000, "comment": "evolab-demo-manual",
            "type_time": 0, "type_filling": filling, "sl": 4056.75, "tp": 4116.75}


def test_18_the_bridge_rewrites_ioc_to_the_mode_the_terminal_accepts(terminal, bridge):
    out = bridge.send_market_order(_request(filling=1))
    assert terminal.order_send_calls == 1                 # the order really went out
    sent = terminal.sent_requests[0]
    assert sent["type_filling"] == 2                      # ORDER_FILLING_RETURN
    assert out["ok"] is True and out["retcode"] == 10009
    res = out["filling_resolution"]
    assert res["applied"] is True and res["name"] == "ORDER_FILLING_RETURN"
    assert res["symbol_filling_mode"] == 0
    assert res["probes"][0]["retcode"] == 10030           # IOC probe refused, recorded
    assert "accepted by the terminal" in res["resolution"]


def test_19_a_request_already_using_the_right_mode_is_not_probed(bridge, terminal):
    out = bridge.send_market_order(_request(filling=2))
    assert out["ok"] is True
    assert out["filling_resolution"]["applied"] is False
    assert "already uses the filling mode derived" in out["filling_resolution"]["resolution"]
    # one check (inside the send) only — no extra probing
    assert terminal.order_check_calls == 1


def test_20_a_passing_check_is_not_a_refusal(terminal, bridge):
    out = bridge.send_market_order(_request(filling=2))
    check = out["check"]
    assert check["ok"] is True
    assert check["verdict"]["retcode"] == 0 and check["verdict"]["margin"] == 8.17
    assert "PASSING" in check["verdict"]["rule"]
    assert out["diagnostic"]["check"]["ok"] is True
    assert "0" in out["diagnostic"]["check"]["retcode_name"]


def test_21_when_the_terminal_returns_nothing_last_error_is_captured(terminal, bridge):
    terminal.send_behaviour = "none"
    out = bridge.send_market_order(_request(filling=2))
    assert out["ok"] is False and out["called"] is True and out["call_count"] == 1
    assert out["last_error"] == [-10004, "No IPC connection"]
    diag = out["diagnostic"]
    assert diag["phase"] == "ORDER_SEND_NO_RESULT"
    assert diag["last_error_name"] and "no IPC connection" in diag["last_error_name"]
    assert diag["check"]["ok"] is True                    # the check that preceded it
    assert diag["filling_resolution"]["name"] == "ORDER_FILLING_RETURN"
    assert diag["fill_mode_used"] == 2
    assert terminal.order_send_calls == 1                 # never retried


def test_22_an_exception_is_named_with_its_type(terminal, bridge):
    terminal.send_behaviour = "raise"
    out = bridge.send_market_order(_request(filling=2))
    assert out["exception_type"] == "RuntimeError"
    assert out["diagnostic"]["phase"] == "ORDER_SEND_RAISED"
    assert any("socket closed" in ln for ln in out["diagnostic"]["exception"]["traceback_tail"])


# ===========================================================================
# 6. the whole manual path: place_demo_order on the operator's terminal
# ===========================================================================
@pytest.fixture()
def scratch_db(tmp_path, monkeypatch):
    from app.db.database import Database
    from app.mt5 import execution as ex
    db = Database(str(tmp_path / "v52_orders.db"))
    monkeypatch.setattr(ex, "_db", lambda: db)
    return db


def _payload():
    return {"symbol": "XAUUSD", "side": "buy", "volume": 0.03, "price": 4086.75,
            "sl": 4056.75, "tp": 4116.75, "confirm": "PLACE_DEMO_ORDER",
            "magic": 777001, "client_order_id": f"v52-{time.time_ns()}",
            "comment": "evolab-demo-manual"}


def test_23_a_manual_demo_order_is_accepted_and_the_position_is_verified(
        terminal, bridge, scratch_db, monkeypatch):
    from app.mt5 import execution as ex
    monkeypatch.setattr("app.mt5.factory.get_bridge", lambda: bridge, raising=False)
    monkeypatch.setattr(ex, "get_bridge", lambda: bridge, raising=False)
    res = ex.place_demo_order(_payload(), bridge=bridge)
    assert res["ok"] is True and res["status"] in ("POSITION_OPEN", "EXECUTED_UNCONFIRMED")
    assert res["order"]["ticket"] == 555001 and res["order"]["deal_ticket"] == 666001
    assert res["execution"]["volume"] == 0.03
    assert res["broker"]["retcode"] == 10009
    # the request that left the process used the terminal's own filling mode
    assert terminal.sent_requests[0]["type_filling"] == 2
    assert res["filling_resolution"]["name"] == "ORDER_FILLING_RETURN"
    # deviation was widened from 20 points (2.0 USD) to the spread-derived floor
    assert res["deviation"]["value"] >= 20
    assert terminal.order_send_calls == 1
    row = scratch_db.get_manual_mt5_orders()[0]
    assert row["status"] in ("POSITION_OPEN", "EXECUTED_UNCONFIRMED")
    assert row["retcode"] == 10009


def test_24_no_result_is_a_precise_unknown_and_never_a_second_send(
        terminal, bridge, scratch_db, monkeypatch):
    from app.mt5 import execution as ex
    terminal.send_behaviour = "none"
    monkeypatch.setattr("app.mt5.factory.get_bridge", lambda: bridge, raising=False)
    monkeypatch.setattr(ex, "get_bridge", lambda: bridge, raising=False)
    res = ex.place_demo_order(_payload(), bridge=bridge)
    assert res["ok"] is False and res["status"] == "UNKNOWN"
    assert res["result_class"] == "NO_RESULT"
    assert res["broker"]["safe_to_retry"] is False
    assert res["order_send"]["called"] is True
    assert res["diagnostic"]["phase"] == "ORDER_SEND_NO_RESULT"
    assert "No IPC connection" in res["broker"]["message"]
    assert terminal.order_send_calls == 1


def test_25_a_real_account_is_still_refused_before_anything_is_built(monkeypatch, scratch_db):
    fake = FakeMT5Terminal(trade_mode=2)                   # REAL
    _install(fake, monkeypatch)
    from app.mt5.mt5_real import MT5RealBridge
    from app.mt5 import execution as ex
    b = MT5RealBridge()
    b._connected = True
    with pytest.raises(ex.MT5ExecutionError) as ei:
        ex.place_demo_order(_payload(), bridge=b)
    assert ei.value.code == "NON_DEMO_ACCOUNT"
    assert fake.order_send_calls == 0 and fake.order_check_calls == 0


# ===========================================================================
# 7. the diagnostic tool must classify this terminal as READY
# ===========================================================================
def test_26_the_diagnostic_probe_accepts_the_projects_request(terminal):
    from app.mt5 import windows_diagnostic as wd
    sym = {"found": True, "visible": True, "digits": 2, "point": 0.01,
           "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01,
           "trade_stops_level": 0, "trade_mode": 4, "filling_modes": ["ORDER_FILLING_RETURN"],
           "filling_plan": wd._filling_plan(terminal.symbol_info("XAUUSD"), terminal)}
    tick = {"available": True, "bid": 4086.56, "ask": 4086.75}
    probe = wd._probe_order_check(terminal, "XAUUSD", sym, tick)
    assert probe["attempted"] is True
    assert probe["ok"] is True, probe
    assert probe["retcode"] == 0 and probe["retcode_name"].startswith("TRADE_RETCODE_OK")
    assert probe["margin"] == 8.17
    assert "PASSING" in probe["rule"]
    assert terminal.order_send_calls == 0                  # order_send is NEVER called
    # …and the probe's own request uses the Return policy, not IOC
    assert probe["request"]["type_filling"] == 2


def test_27_the_diagnostic_repairs_an_unsupported_filling_and_rechecks(monkeypatch):
    """If some other caller hands the tool an IOC request, the tool finds RETURN."""
    from app.mt5 import windows_diagnostic as wd
    from app.mt5 import execution as ex
    term = FakeMT5Terminal()
    sym = {"found": True, "visible": True, "digits": 2, "point": 0.01,
           "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01,
           "trade_stops_level": 0, "trade_mode": 4,
           "filling_plan": wd._filling_plan(term.symbol_info("XAUUSD"), term)}
    tick = {"available": True, "bid": 4086.56, "ask": 4086.75}
    # force a wrong first choice: the IOC constant, exactly as the old code did
    monkeypatch.setattr("app.mt5.execution.pick_filling",
                        lambda spec, mt5=None: ex._c("ORDER_FILLING_IOC"))
    probe = wd._probe_order_check(term, "XAUUSD", sym, tick)
    assert probe["ok"] is True
    assert probe["filling_resolution"]["ok"] is True
    assert probe["filling_resolution"]["filling"] == 2
    assert probe["request"]["type_filling"] == 2
    assert "after switching type_filling" in probe["rule"]
    assert term.order_send_calls == 0


def test_28_classification_is_ready_for_a_passing_check(terminal):
    from app.mt5 import windows_diagnostic as wd
    probe = {"_package_importable": True, "initialized": True,
             "account_info": {"login": 53071066},
             "symbol": {"found": True}, "tick": {"available": True},
             "permissions": {"ok": True},
             "order_check": {"attempted": True, "ok": True, "retcode": 0,
                             "retcode_name": "TRADE_RETCODE_OK/UNDEFINED (0)",
                             "comment": "Done", "rule": "…PASSING…"}}
    facts = wd._facts_from_probe(True, {"running_backend_python": {"executable": "python.exe"}},
                                 probe, {"discovered_count": 1, "running_count": 1}, True)
    verdict = wd.classify_layers(facts)
    assert verdict["environment"] == wd.MT5_READY, verdict


def test_29_classification_names_a_genuine_refusal(terminal):
    from app.mt5 import windows_diagnostic as wd
    probe = {"_package_importable": True, "initialized": True,
             "account_info": {"login": 53071066},
             "symbol": {"found": True}, "tick": {"available": True},
             "permissions": {"ok": True},
             "order_check": {"attempted": True, "ok": False, "retcode": 10016,
                             "retcode_name": "TRADE_RETCODE_INVALID_STOPS (10016)",
                             "comment": "Invalid stops", "rule": "retcode 10016 …"}}
    facts = wd._facts_from_probe(True, {"running_backend_python": {"executable": "python.exe"}},
                                 probe, {"discovered_count": 1, "running_count": 1}, True)
    verdict = wd.classify_layers(facts)
    assert verdict["environment"] == wd.MT5_ORDER_VALIDATION_FAILED
    assert "10016" in verdict["reason"] and "Rule applied" in verdict["reason"]


# ===========================================================================
# 8. the folder-path quirk the Windows run exposed
# ===========================================================================
def test_30_a_folder_path_is_resolved_to_the_executable(tmp_path):
    from app.mt5.discovery import normalize_terminal_path
    folder = tmp_path / "MetaTrader 5 IC Markets Global"
    folder.mkdir()
    exe = folder / "terminal64.exe"
    exe.write_bytes(b"MZ")
    resolved, note = normalize_terminal_path(str(folder))
    assert resolved == str(exe) and "folder" in note
    same, note2 = normalize_terminal_path(str(exe))
    assert same == str(exe) and note2 == ""
    missing, note3 = normalize_terminal_path(str(tmp_path / "nope"))
    assert note3 and missing.endswith("nope")
    empty, note4 = normalize_terminal_path("")
    assert empty == "" and note4


def test_31_connect_normalizes_a_saved_folder_path(tmp_path, monkeypatch):
    from app.mt5 import mt5_real
    folder = tmp_path / "MT5"
    folder.mkdir()
    exe = folder / "terminal64.exe"
    exe.write_bytes(b"MZ")
    seen = {}

    class Term(FakeMT5Terminal):
        def initialize(self, path=None, **kw):
            seen["path"] = path
            return True

    _install(Term(), monkeypatch)
    monkeypatch.setattr(mt5_real, "get_saved_terminal_path", lambda: str(folder))
    b = mt5_real.MT5RealBridge()
    assert b.connect() is True
    assert seen["path"] == str(exe)
