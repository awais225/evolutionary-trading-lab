"""V5.4 §2 — an `order_check` answer is NEVER an execution (the operator's case).

The failure this suite locks down is the one the operator actually hit:

    mt5.order_check(...)  ->  OrderCheckResult(retcode=0, comment='Done', margin=24.76)
    mt5.order_send(...)   ->  ??? (the bridge reported an ORDER CHECK result here)

and the dashboard then reported "RESULT UNKNOWN — THE BRIDGE DID NOT REPORT AN
ORDER_SEND RESULT", which told the operator nothing: a passing check is a
PREFLIGHT ("this request would be accepted"), never a fill.

These tests drive the real `place_demo_order()` → `MT5RealBridge.send_market_order`
path against a fake MetaTrader5 module whose quote, request, account and
terminal flags are the operator's own values, for two bridge behaviours:

  * a V5.4 bridge that names the phase (`ORDER_SEND_RETURNED_CHECK_RESULT`);
  * a legacy bridge that reports no phase at all — the object's SHAPE must still
    classify it, because that is exactly the build the operator is running.

Both must produce `UNKNOWN_EXECUTION` / `CHECK_RESULT_RETURNED` with a NULL order
retcode, one single `order_send` call, no automatic retry, and every field of the
operator's requested response contract present.
"""
from __future__ import annotations

import importlib.util
import tempfile
import time
import uuid
from collections import namedtuple
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _load(name: str):
    p = Path(__file__).resolve().parent / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_v52 = _load("test_v5_2_mt5_order_semantics.py")
_Obj = _v52._Obj
_install = _v52._install
FakeMT5Terminal = _v52.FakeMT5Terminal


class _MP:
    """Minimal monkeypatch shim so this module can drive `_install` directly."""

    def setattr(self, obj, name, value, raising=True):        # noqa: D102
        setattr(obj, name, value)


# --------------------------------------------------------------------------- #
# the operator's evidence, verbatim
# --------------------------------------------------------------------------- #
Request = namedtuple("TradeRequest", "action magic order symbol volume price stoplimit sl tp "
                                     "deviation type type_filling type_time expiration comment "
                                     "position position_by")
Check = namedtuple("OrderCheckResult", "retcode balance equity profit margin margin_free "
                                       "margin_level comment request")

OPERATOR_REQUEST = Request(action=1, magic=777000, order=0, symbol="XAUUSD", volume=0.03,
                           price=4126.61, stoplimit=0.0, sl=4095.96, tp=4185.96,
                           deviation=60, type=0, type_filling=1, type_time=0, expiration=0,
                           comment="evolab-demo-manual", position=0, position_by=0)

OPERATOR_CHECK = Check(retcode=0, balance=200509.92, equity=200509.92, profit=0.0,
                       margin=24.76, margin_free=200485.16, margin_level=809813.8933764136,
                       comment="Done", request=OPERATOR_REQUEST)


def _operator_terminal(*, phase: str | None):
    """The operator's terminal: check PASSES (retcode 0, 'Done'), send answers with it.

    Mirrors the diagnostic exactly: XAUUSD, bid 4126.42 / ask 4126.61, 0.03 lots,
    deviation 60, filling 1 (IOC — the operator's terminal accepts it), build 6230,
    trade_allowed true, tradeapi_disabled false, demo account 53071066.
    """
    t = FakeMT5Terminal()
    _si = t.symbol_info("XAUUSD")
    _tick = _Obj(bid=4126.42, ask=4126.61, last=4126.5, spread=19, time=int(time.time()),
                 volume=42)
    # the operator's symbol: SYMBOL_FILLING_IOC (2) -> the request's type_filling 1
    t.symbol_info = lambda symbol: _Obj(**{**_si.__dict__, "bid": 4126.42, "ask": 4126.61,
                                           "filling_mode": 2})
    t.symbol_info_tick = lambda symbol: _tick
    # the operator's terminal accepted the request: retcode 0, comment 'Done'
    t.order_check = lambda req: OPERATOR_CHECK

    def send_check_result(req):
        t.order_send_calls += 1
        t.sent_requests.append(dict(req))
        return OPERATOR_CHECK                     # <-- the whole defect, in one line

    t.order_send = send_check_result
    t._phase = phase
    return t


class _OperatorTerminal(_v52.FakeMT5Terminal):
    pass


def _run(payload=None, *, behaviour="check_result", phase="ORDER_SEND_RETURNED_CHECK_RESULT"):
    """Drive the real manual-order service against the operator's terminal."""
    from app.mt5 import execution as ex
    from app.mt5 import mt5_real
    from app.db.database import Database

    tmp = Path(tempfile.mkdtemp())
    db = Database(str(tmp / "v54_order_check.db"))
    ex._db = lambda: db

    term = _operator_terminal(phase=phase)
    if behaviour == "check_result":
        pass                                     # the default above IS the defect
    elif behaviour == "ok":                      # a real execution for comparison
        term = FakeMT5Terminal()
        _si = term.symbol_info("XAUUSD")
        _tick = _Obj(bid=4126.42, ask=4126.61, last=4126.5, spread=19,
                     time=int(time.time()), volume=42)
        term.symbol_info = lambda symbol: _Obj(**{**_si.__dict__, "bid": 4126.42,
                                                  "ask": 4126.61})
        term.symbol_info_tick = lambda symbol: _tick

    _install(term, _MP())
    bridge = mt5_real.MT5RealBridge()
    bridge._connected = True

    body = {"symbol": "XAUUSD", "side": "buy", "volume": 0.03, "price": 4126.61,
            "sl": 4095.96, "tp": 4185.96, "magic": 777000,
            "comment": "evolab-demo-manual", "confirm": "PLACE_DEMO_ORDER",
            "client_order_id": f"XAUUSD-BUY-{time.time_ns()}-{uuid.uuid4().hex[:6]}",
            "strategy_id": 837}
    body.update(payload or {})
    try:
        res = ex.place_demo_order(body, bridge=bridge)
    except ex.MT5ExecutionError as e:              # pragma: no cover - must not happen
        pytest.fail(f"the manual order raised {e.code}: {e.message}")
    res["_terminal"] = term
    return res


# --------------------------------------------------------------------------- #
# 1-3. the defect itself
# --------------------------------------------------------------------------- #
def test_01_a_passing_order_check_is_never_reported_as_an_execution():
    res = _run()
    assert res["ok"] is False
    assert res["status"] == "UNKNOWN"
    assert res["result_class"] == "UNKNOWN_EXECUTION", \
        "an OrderCheckResult from order_send must never be EXECUTED"
    assert res["result_class_detail"] == "CHECK_RESULT_RETURNED"
    assert "ORDER CHECK" in (res["label"] or "").upper()
    # the check's own retcode 0 is a PREFLIGHT PASS — it is NOT the order's retcode
    assert res["broker_retcode"] is None and res["retcode"] is None, \
        "retcode 0 ('Done') is the CHECK's, and must never be published as the order's"
    assert res["ticket"] is None and res["contract"]["deal"] is None
    assert res["safe_to_retry"] is False
    # …while the check's verdict is preserved where it belongs
    check = res["order_check"]
    assert check["called"] is True and check["passed"] is True
    assert check["retcode"] == 0 and check["comment"] == "Done"
    assert check["margin"] == 24.76


def test_02_a_legacy_bridge_is_classified_by_the_shape_of_what_it_returned():
    """The operator's build reports no phase — the object still decides."""
    res = _run(phase=None)
    assert res["result_class"] == "UNKNOWN_EXECUTION"
    assert res["result_class_detail"] == "CHECK_RESULT_RETURNED"
    assert res["broker_retcode"] is None


def test_03_the_phase_names_the_exact_layer_and_the_send_really_happened():
    res = _run()
    term = res["_terminal"]
    assert term.order_send_calls == 1, "exactly ONE order_send — a check is not a send"
    assert term.sent_requests[0]["symbol"] == "XAUUSD"
    assert term.sent_requests[0]["volume"] == 0.03
    assert term.sent_requests[0]["type_filling"] == 1, "the request keeps the operator's mode"
    assert res["order_send_called"] is True
    assert res["order_send_phase"] == "ORDER_SEND_RETURNED_CHECK_RESULT"
    assert res["order_check_called"] is True and res["order_check_passed"] is True
    assert res["nothing_sent"] is False, "an ambiguous outcome must not claim nothing was sent"


def test_04_one_click_is_one_order_send_and_nothing_is_retried():
    res = _run()
    assert res["_terminal"].order_send_calls == 1
    assert res["safe_to_retry"] is False
    # the response must tell the operator to verify in the terminal instead
    actions = res.get("next_actions") or []
    text = " ".join(a if isinstance(a, str) else " ".join(str(v) for v in a.values())
                    for a in actions)
    msg = (res["broker"]["message"] or "") + " " + text
    assert "verify" in msg.lower(), "the ambiguous case must direct the operator to the terminal"


# --------------------------------------------------------------------------- #
# 4-5. the response contract
# --------------------------------------------------------------------------- #
def test_05_the_response_carries_every_field_the_operator_asked_for():
    res = _run()
    for key in ("ok", "status", "result_class", "retcode", "ticket", "fill_price",
                "volume", "requested_sl", "broker_sl", "requested_tp", "broker_tp",
                "broker_comment", "broker_message", "client_order_id",
                "order_check_called", "order_send_called"):
        assert key in res, f"the response contract is missing {key!r}"
    # the requested names order/deal/position travel in the dedicated block,
    # alongside result_class_detail — and never clobber the structured order object
    for key in ("ok", "status", "result_class", "result_class_detail", "retcode",
                "ticket", "order", "deal", "position", "fill_price", "volume",
                "requested_sl", "broker_sl", "requested_tp", "broker_tp",
                "broker_comment", "broker_message", "client_order_id",
                "order_check_called", "order_send_called", "order_send_phase"):
        assert key in res["contract"], f"the contract block is missing {key!r}"
    assert isinstance(res["order"], dict) and "ticket" in res["order"]
    # …with the requested values stated (never invented)
    assert res["requested_sl"] == 4095.96 and res["requested_tp"] == 4185.96
    assert res["broker_sl"] is None and res["broker_tp"] is None    # nothing was filled
    assert res["volume"] == 0.03 and res["execution"]["executed_volume"] is None
    assert (res["client_order_id"] or "").startswith("XAUUSD-BUY-")


def test_06_a_real_execution_still_reports_the_real_retcode_and_tickets():
    """The same service, a terminal that actually fills — the contract in full."""
    res = _run(behaviour="ok")
    assert res["ok"] is True and res["result_class"] == "EXECUTED"
    assert res["retcode"] == 10009 and res["broker_retcode"] == 10009
    assert res["ticket"] == 555001 and res["order"]["ticket"] == 555001
    assert res["contract"]["order"] == 555001 and res["contract"]["deal"] == 666001
    assert res["fill_price"] is not None and res["volume"] == 0.03
    assert res["order_check_called"] is True and res["order_check_passed"] is True
    assert res["order_send_called"] is True
    assert res["order_send_phase"] == "ORDER_SEND_RETURNED_RESULT"
    assert res["_terminal"].order_send_calls == 1


# --------------------------------------------------------------------------- #
# 6. ONE send site in the whole backend
# --------------------------------------------------------------------------- #
def _order_send_call_sites():
    """Every *executable* ``mt5.order_send(...)`` call under ``backend/app``."""
    import ast

    sites = []
    for path in sorted((REPO / "backend" / "app").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if isinstance(fn, ast.Attribute) and fn.attr == "order_send":
                # ``mt5.order_send`` (or ``self._mt5.order_send`` …) — the binding call
                sites.append((path.relative_to(REPO).as_posix(), node.lineno))
    return sites


def test_07_the_backend_has_exactly_one_order_send_call_site():
    sites = _order_send_call_sites()
    assert len(sites) == 1, (
        "an order may leave the process through exactly ONE instrumented call "
        f"site; found {sites}")
    assert sites[0][0] == "backend/app/mt5/mt5_real.py", sites[0]


def test_08_the_legacy_helper_routes_through_the_instrumented_path(monkeypatch):
    """`real_market_order` must not be a second, un-instrumented send site."""
    from app.mt5 import mt5_real

    calls = {}

    class _Bridge(mt5_real.MT5RealBridge):
        def send_market_order(self, request):                # noqa: D102
            calls["request"] = dict(request)
            return {"ok": True, "retcode": 10009, "phase": "ORDER_SEND_RETURNED_RESULT",
                    "raw": {"retcode": 10009, "order": 42, "price": 4126.61, "comment": "Done"}}

    b = _Bridge()
    b._connected = True
    b.latest_tick = lambda symbol: _Obj(bid=4126.42, ask=4126.61, time=int(time.time()))
    out = b.real_market_order("XAUUSD", "buy", 0.01)
    assert calls.get("request", {}).get("symbol") == "XAUUSD", \
        "real_market_order bypassed send_market_order()"
    assert out.ok is True and out.order_id == 42 and out.retcode == 10009
