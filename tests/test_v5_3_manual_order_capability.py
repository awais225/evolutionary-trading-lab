"""V5.3 — the Live Testing table that stayed empty, and the manual order that
came back "UNKNOWN" with nothing to act on.

The operator's Windows report (2026-10-08, build V5.2.3) contained two failures:

1. LIVE TESTING rendered no node table at all and showed

       422: query.starred_only: Input should be a valid boolean, unable to
       interpret input

   while Overview reported 33 live-eligible nodes.  Cause: the dashboard built
   query strings with a raw ``URLSearchParams``, which stringifies a JavaScript
   ``undefined`` into the literal ``"undefined"`` — so an *unset* filter arrived
   as ``?starred_only=undefined`` and the API rejected the whole request.

2. A confirmed 0.03 XAUUSD demo order came back

       {"ok": false, "status": "UNKNOWN", "label": "RESULT UNKNOWN", …}

   with no usable cause: the response collapsed the case "mt5.order_send was
   called and gave back something unreadable" into a generic UNKNOWN, and the
   panel could not even show the account ("Account: no account · DEMO ONLY")
   although MT5 was logged in to the demo account.

These tests pin the fixes at the level the operator experiences them:
the request that leaves the browser, the classification of what MT5 returned,
the read-only pre-flight verdict, and the account the panel displays.
"""
from __future__ import annotations

import importlib.util
import json
import time
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
FakeMT5Terminal = _v52.FakeMT5Terminal
_Obj = _v52._Obj
_install = _v52._install
scratch_db = _v52.scratch_db            # reuse the scratch audit DB fixture
_payload = _v52._payload


# ===========================================================================
# 1. the request that leaves the browser
# ===========================================================================
API_JS = REPO / "frontend" / "src" / "api.js"
LIVE_INDEX = REPO / "frontend" / "src" / "components" / "LiveNodeIndex.jsx"
PANEL = REPO / "frontend" / "src" / "components" / "LiveTestingPanels.jsx"
BAT = REPO / "CHECK_RUNNING_DASHBOARD.bat"


def test_01_no_endpoint_can_send_the_string_undefined():
    """Every query string goes through qs(), which drops unset values."""
    src = API_JS.read_text(encoding="utf-8")
    assert "new URLSearchParams(params)" not in src
    assert "new URLSearchParams(p)" not in src
    # the only URLSearchParams left is INSIDE qs() itself
    assert src.count("new URLSearchParams(") == 1
    assert "function qs(params = {})" in src
    # ... and qs() also refuses a value that was already stringified upstream
    body = src.split("function qs(params = {})", 1)[1].split("\n}", 1)[0]
    assert '"undefined"' in body and '"null"' in body, \
        "qs() must drop the strings 'undefined'/'null', not only the JS values"


def test_02_the_live_testing_table_omits_starred_only_when_it_is_off():
    src = LIVE_INDEX.read_text(encoding="utf-8")
    assert "starred_only: starredOnly || undefined" not in src, \
        "the old idiom is what produced ?starred_only=undefined"
    assert "...(starredOnly ? { starred_only: true } : {})" in src
    assert "limit: 0" in src


def test_03_the_api_answers_a_bad_query_with_a_readable_message(client):
    """The operator must be told the parameter and the culprit, not just pydantic."""
    res = client.get("/api/nodes?filter=qualified&limit=0&starred_only=undefined")
    assert res.status_code == 422
    body = res.json()
    assert body["ok"] is False and body["status"] == "BAD_REQUEST"
    assert "starred_only" in body["message"]
    assert "undefined" in body["message"]
    assert body["hint"] and "undefined" in body["hint"]
    assert body["path"] == "/api/nodes"
    # the machine-readable detail pydantic produced is preserved, not discarded
    assert body["detail"] and isinstance(body["detail"], list)


def test_04_a_valid_query_still_works(client):
    """The readable handler must not change a single successful response."""
    res = client.get("/api/nodes?filter=qualified&limit=0")
    assert res.status_code == 200
    body = res.json()
    assert body.get("all_rows") is True and body.get("truncated") is False


# ===========================================================================
# 2. read-only pre-flight: name the switch instead of guessing
# ===========================================================================
class CapabilityTerminal(FakeMT5Terminal):
    """The V5.2 double with the trade-permission flags the real terminal reports."""

    def __init__(self, *, trade_allowed=True, tradeapi_disabled=False,
                 account_trade_allowed=True, account_trade_expert=True, **kw):
        super().__init__(**kw)
        self.trade_allowed = trade_allowed
        self.tradeapi_disabled = tradeapi_disabled
        self.account_trade_allowed = account_trade_allowed
        self.account_trade_expert = account_trade_expert

    def terminal_info(self):
        ti = super().terminal_info()
        ti.trade_allowed = self.trade_allowed
        ti.tradeapi_disabled = self.tradeapi_disabled
        return ti

    def account_info(self):
        acct = super().account_info()
        acct.trade_allowed = self.account_trade_allowed
        acct.trade_expert = self.account_trade_expert
        return acct


@pytest.fixture()
def capability_terminal(monkeypatch):
    def _make(**kw):
        fake = CapabilityTerminal(**kw)
        _install(fake, monkeypatch)
        return fake
    return _make


def _bridge():
    from app.mt5.mt5_real import MT5RealBridge
    b = MT5RealBridge()
    b._connected = True
    return b


def _place(bridge, monkeypatch):
    from app.mt5 import execution as ex
    monkeypatch.setattr("app.mt5.factory.get_bridge", lambda: bridge, raising=False)
    monkeypatch.setattr(ex, "get_bridge", lambda: bridge, raising=False)
    return ex.place_demo_order(_payload(), bridge=bridge)


def test_05_the_capability_report_reads_the_real_flags(capability_terminal):
    from app.mt5 import execution as ex
    term = capability_terminal()
    cap = ex.trade_capability(_bridge(), symbol="XAUUSD", side="buy")
    assert cap["send_permitted"] is True and cap["blockers"] == []
    assert cap["terminal"]["trade_allowed"] is True
    assert cap["terminal"]["tradeapi_disabled"] is False
    assert cap["account"]["login"] == 53071066
    assert cap["account"]["trade_expert"] is True
    assert cap["symbol"]["trade_mode_name"] == "FULL"
    assert cap["rule"].startswith("a blocker must be a value MT5 reports as off")


def test_06_algo_trading_off_is_named_before_anything_is_sent(
        capability_terminal, scratch_db, monkeypatch):
    """The known cause of a silent order_send: the terminal refuses to trade."""
    from app.mt5 import execution as ex
    term = capability_terminal(trade_allowed=False)
    bridge = _bridge()
    with pytest.raises(ex.MT5ExecutionError) as ei:
        _place(bridge, monkeypatch)
    err = ei.value
    # the V4.2 account guard already owns this case (ALGO_TRADING_DISABLED); the
    # V5.3 capability gate is the second line — either way the operator is told.
    assert err.code in ("ALGO_TRADING_DISABLED", "TERMINAL_ALGO_TRADING_OFF")
    assert err.stage in ("ACCOUNT_SAFETY", "TRADE_CAPABILITY")
    assert "Algo Trading" in err.message
    assert term.order_send_calls == 0, "nothing may be sent when the terminal refuses"
    assert term.order_check_calls == 0, "the request is not even built"
    cap = (err.details.get("trade_capability")
           or (err.details.get("account_safety") or {}).get("trade_allowed"))
    assert cap is not None


def test_07_api_trading_disabled_is_named(capability_terminal, scratch_db, monkeypatch):
    from app.mt5 import execution as ex
    term = capability_terminal(tradeapi_disabled=True)
    with pytest.raises(ex.MT5ExecutionError) as ei:
        _place(_bridge(), monkeypatch)
    assert ei.value.code == "TERMINAL_API_TRADING_DISABLED"
    assert "Expert Advisors" in ei.value.message
    assert term.order_send_calls == 0


def test_08_expert_trading_not_allowed_on_the_account_is_named(
        capability_terminal, scratch_db, monkeypatch):
    from app.mt5 import execution as ex
    term = capability_terminal(account_trade_expert=False)
    with pytest.raises(ex.MT5ExecutionError) as ei:
        _place(_bridge(), monkeypatch)
    assert ei.value.code == "ACCOUNT_TRADE_EXPERT_OFF"
    assert term.order_send_calls == 0


def test_09_unknown_flags_never_block_a_healthy_terminal(
        capability_terminal, scratch_db, monkeypatch):
    """A terminal that cannot report its flags must not be treated as disabled."""
    from app.mt5 import execution as ex
    term = capability_terminal()
    term.terminal_info = lambda: None                    # reports nothing at all
    cap = ex.trade_capability(_bridge(), symbol="XAUUSD", side="buy")
    assert cap["send_permitted"] is True, "ignorance must never block an order"
    assert cap["terminal"] is None
    assert any(w.get("fact") == "terminal_info()" for w in cap["warnings"])
    assert cap["headline"] == "trading capability confirmed"


def test_10_a_symbol_that_only_allows_the_other_side_is_named(
        capability_terminal, scratch_db, monkeypatch):
    from app.mt5 import execution as ex
    term = capability_terminal()
    orig = term.symbol_info

    def short_only(symbol):
        si = orig(symbol)
        si.trade_mode = 2                     # SHORT_ONLY
        return si
    term.symbol_info = short_only
    with pytest.raises(ex.MT5ExecutionError) as ei:
        _place(_bridge(), monkeypatch)
    assert ei.value.code == "SYMBOL_SHORT_ONLY"
    assert term.order_send_calls == 0


# ===========================================================================
# 3. what the terminal returned is classified, never collapsed
# ===========================================================================
class UnreadableResultTerminal(FakeMT5Terminal):
    """order_send answers, but with an object that carries no retcode."""

    def order_send(self, request):
        self.order_send_calls += 1
        self.sent_requests.append(dict(request))
        self._last_error = (0, "Ok")
        return _Obj(order=0, deal=0, volume=0.0, comment="")     # no retcode


def test_11_an_unreadable_result_gets_its_own_phase(monkeypatch):
    from app.mt5 import mt5_real
    fake = UnreadableResultTerminal()
    _install(fake, monkeypatch)
    bridge = mt5_real.MT5RealBridge()
    bridge._connected = True
    out = bridge.send_market_order(_v52._request())
    assert out["ok"] is False and out["called"] is True and out["call_count"] == 1
    assert out["diagnostic"]["phase"] == "ORDER_SEND_UNUSABLE_RESULT"
    assert out["raw"] and "retcode" not in {k for k, v in out["raw"].items() if v is not None}
    assert out["diagnostic"]["terminal_trade_allowed"] is True
    assert fake.order_send_calls == 1


def test_12_an_unreadable_result_through_place_demo_order_is_precise(
        monkeypatch, scratch_db):
    """The exact shape of the operator's failed call must not be a bare UNKNOWN."""
    from app.mt5 import execution as ex
    from app.mt5 import mt5_real
    fake = UnreadableResultTerminal()
    _install(fake, monkeypatch)
    bridge = mt5_real.MT5RealBridge()
    bridge._connected = True
    res = _place(bridge, monkeypatch)
    assert res["ok"] is False and res["status"] == "UNKNOWN"
    assert res["result_class"] == "NO_USABLE_RESULT", \
        "an unreadable answer is not a broker result"
    assert res["label"].startswith("RESULT UNKNOWN")
    assert res["order_send"]["called"] is True
    assert res["order_send"]["raw_result_repr"]
    assert res["broker"]["safe_to_retry"] is False
    assert res["preflight"]["send_permitted"] is True
    assert res["next_actions"] and res["next_actions"][0]["step"] == 1
    assert "MetaTrader 5" in res["next_actions"][0]["action"]
    assert fake.order_send_calls == 1, "one send, never a retry"


def test_13_a_bridge_that_reports_nothing_is_still_not_a_broker_result(monkeypatch, scratch_db):
    """A bridge without the V5.1a diagnostic must not fall into the generic branch."""
    from app.mt5 import execution as ex
    fake = CapabilityTerminal()
    _install(fake, monkeypatch)

    class LegacyBridge:
        name, source = "mt5_real", "MT5"
        _connected = True
        broker_name, server_name, account_id = "Raw Trading Ltd", "ICMarketsSC-Demo", "53071066"

        def account_info(self):
            from app.mt5.mt5_real import MT5RealBridge
            inner = MT5RealBridge()
            inner._connected = True
            return inner.account_info()

        def symbol_info(self, symbol):
            return fake.symbol_info(symbol)

        def latest_tick(self, symbol):
            from app.mt5.mt5_real import MT5RealBridge
            inner = MT5RealBridge()
            inner._connected = True
            return inner.latest_tick(symbol)

        def send_market_order(self, request):
            return {"ok": False, "retcode": None, "raw": None,
                    "error": "no result object", "last_error": [-10004, "No IPC connection"]}

    res = _place(LegacyBridge(), monkeypatch)
    assert res["result_class"] == "NO_RESULT"
    assert res["status"] == "UNKNOWN"
    assert res["order_send"]["last_error"] == [-10004, "No IPC connection"]
    acts = [a["action"].lower() for a in res["next_actions"]]
    assert any("re-open metatrader 5" in a or "restart" in a or "mt5" in a for a in acts), acts


def test_14_the_unknown_result_carries_the_evidence_ladder(monkeypatch, scratch_db):
    from app.mt5 import mt5_real
    fake = UnreadableResultTerminal()
    _install(fake, monkeypatch)
    bridge = mt5_real.MT5RealBridge()
    bridge._connected = True
    res = _place(bridge, monkeypatch)
    diag = res["diagnostic"]
    assert diag["phase"] == "ORDER_SEND_UNUSABLE_RESULT"
    assert diag["terminal"]["build"] == 6230
    assert diag["account"]["login"] == 53071066
    assert diag["symbol"]["volume_step"] == 0.01
    assert diag["request"]["symbol"] == "XAUUSD"
    assert diag["order_send_called"] is True


# ===========================================================================
# 4. the account the panel shows, and the panel's own wiring
# ===========================================================================
def test_15_the_state_endpoint_exposes_the_account_the_panel_prints(
        capability_terminal, monkeypatch):
    from app.mt5 import execution as ex
    term = capability_terminal()
    bridge = _bridge()
    monkeypatch.setattr("app.mt5.factory.get_bridge", lambda: bridge, raising=False)
    monkeypatch.setattr(ex, "get_bridge", lambda: bridge, raising=False)
    st = ex.execution_state(bridge=bridge)
    assert st["account"] and st["account"]["login"] == 53071066
    assert st["account"]["server"] == "ICMarketsSC-Demo"
    assert str(st["account_type"]).upper() == "DEMO"
    cap = st["trade_capability"]
    assert cap["send_permitted"] is True
    assert cap["terminal"]["trade_allowed"] is True


def test_16_the_panel_renders_the_account_and_the_capability_strip():
    src = PANEL.read_text(encoding="utf-8")
    assert "state?.account?.login" in src                    # the account row
    assert 'state?.account_type || state?.account?.trade_mode_name' in src
    assert "state?.trade_capability" in src                  # the pre-flight strip
    assert "Order sending is blocked by the terminal or the account" in src
    assert "result.next_actions" in src                      # what to check next
    assert "result.order_send?.last_error" in src            # the raw last_error
    assert "result.diagnostic?.phase" in src


def test_17_the_launcher_can_never_print_an_empty_git_field():
    raw = BAT.read_bytes()
    assert raw.count(b"\r\n") == raw.count(b"\n"), "the launcher must stay CRLF-only"
    text = raw.decode("ascii")
    assert "if defined GITHEAD (" in text, \
        "the git fields must be guarded: a ZIP checkout must not print blank HEAD lines"
    # the "this checkout is the GitHub main state" claim only exists inside the
    # guarded comparison, never as a bare line
    claim = "[git] HEAD == origin/main  ^(this checkout is the GitHub main state"
    assert text.count(claim) == 1
    guarded = text.split("if defined GITHEAD (", 1)[1]
    assert claim in guarded
    assert "identity by FINGERPRINT" in text


def test_18_a_simulator_bridge_is_never_reported_as_capable(client):
    """Read-only honesty: with no real terminal the check says so instead of
    printing "trading capability confirmed" for a simulated bridge."""
    res = client.get("/api/mt5-execution/state")
    assert res.status_code == 200
    cap = res.json()["trade_capability"]
    assert cap["applicable"] is False
    assert "not applicable" in cap["headline"]
    assert cap["blockers"] == []
