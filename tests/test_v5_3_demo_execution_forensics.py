"""V5.3 Blockers A and B — pin the exact instrumented contract.

Blocker A (§2): the ONE authoritative MT5 order call must be instrumented so that
"nothing came back" can never be the whole story. These tests drive the real
`MT5RealBridge.send_market_order` against a fake MetaTrader5 module (the same
technique the V5.1a diagnostics suite uses) and assert:

  * `mt5.order_send` is actually invoked — a passing `order_check` is not the end;
  * the result object is captured verbatim (`retcode`/`comment`/`order`/`deal`);
  * `None` -> phase `ORDER_SEND_RETURNED_NONE` + `mt5.last_error()` captured
    BEFORE and AFTER the call (no swallowing, no invention);
  * an exception -> phase `ORDER_SEND_EXCEPTION` with type + message;
  * an unreadable object -> `ORDER_SEND_UNUSABLE_RESULT`;
  * filling mode is resolved from symbol metadata and confirmed by order_check;
  * post-send verification reads the TERMINAL (positions_get/orders_get);
  * exactly one send, never a retry;
  * a probe that shuts the terminal down cannot land inside the order path
    (the session lock), and a lost session is re-established before sending.

Blocker B (§3): the Live Testing index and Overview must be the SAME population,
`starred_only` must accept true / false / omitted, and the hop counts must be
reported so a silent empty table is impossible.
"""
from __future__ import annotations

import importlib.util
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
_Obj = _v52._Obj
_install = _v52._install
_request = _v52._request
FakeMT5Terminal = _v52.FakeMT5Terminal


# ===========================================================================
# Blocker A — the instrumented call
# ===========================================================================
class ForensicTerminal(FakeMT5Terminal):
    """Fake MetaTrader5 with the knobs this suite needs."""

    def __init__(self, *, send_behaviour="result", **kw):
        super().__init__(send_behaviour=send_behaviour, **kw)
        self.last_error_sequence = []
        self._counter = 0
        self.shutdown_calls = 0
        self.initialize_calls = 0

    def last_error(self):
        self._counter += 1
        self.last_error_sequence.append((self._counter, self._send_err()))
        return self._send_err()

    def _send_err(self):
        return getattr(self, "_last_error", (0, "Ok"))

    def shutdown(self):
        self.shutdown_calls += 1
        self.initialized = False
        return True

    def initialize(self, path=None, **kw):
        self.initialize_calls += 1
        self.initialized = True
        return True

    def terminal_info(self):
        if not getattr(self, "initialized", True):
            return None
        ti = super().terminal_info()
        return _Obj(**{**ti.__dict__, "connected": True})


def _bridge(monkeypatch, terminal):
    _install(terminal, monkeypatch)
    from app.mt5 import mt5_real
    b = mt5_real.MT5RealBridge()
    b._connected = True
    return b


def test_01_order_send_is_actually_invoked_after_a_passing_check(monkeypatch):
    term = ForensicTerminal()
    b = _bridge(monkeypatch, term)
    out = b.send_market_order(_request())
    # the filling resolver confirms candidates with the read-only order_check, then
    # the request itself is checked — the LAST check is the one that precedes the send
    assert term.order_check_calls >= 2, "the request is checked before it is sent"
    assert term.order_send_calls == 1, "…and the order IS then sent (never stops at the check)"
    assert out["called"] is True and out["call_count"] == 1
    assert out["diagnostic"]["phase"] == "ORDER_SEND_RETURNED_RESULT"
    assert out["forensics"]["outcome"] == "ORDER_SEND_RETURNED_RESULT"
    assert out["forensics"]["order_send_called"] is True


def test_02_the_result_object_is_captured_verbatim(monkeypatch):
    term = ForensicTerminal()
    b = _bridge(monkeypatch, term)
    out = b.send_market_order(_request())
    fx = out["forensics"]
    assert fx["order_send_returned"] is True
    assert fx["order_send_result_type"] in ("Result", "OrderSendResult", "_Result")
    assert "retcode" in (fx["order_send_result_repr"] or "")
    assert out["raw"]["retcode"] == 10009
    assert out["raw"]["comment"] == "Done"
    assert out["raw"]["order"] == 555001 and out["raw"]["deal"] == 666001
    assert fx["order_send_result_repr"], "the raw object is preserved, not summarised"


def test_03_last_error_is_captured_before_and_after_the_call(monkeypatch):
    term = ForensicTerminal()
    b = _bridge(monkeypatch, term)
    out = b.send_market_order(_request())
    fx = out["forensics"]
    assert "mt5_last_error_before" in fx and "mt5_last_error_after" in fx
    assert fx["mt5_last_error_before"] is not None
    assert fx["mt5_last_error_after"] is not None
    assert len(term.last_error_sequence) >= 2, \
        "last_error() must be read on both sides of order_send (nothing swallowed)"


def test_04_none_is_named_and_the_real_last_error_travels(monkeypatch):
    term = ForensicTerminal(send_behaviour="none")
    b = _bridge(monkeypatch, term)
    out = b.send_market_order(_request())
    assert out["forensics"]["outcome"] == "ORDER_SEND_RETURNED_NONE"
    assert out["diagnostic"]["phase"] == "ORDER_SEND_RETURNED_NONE"
    assert out["diagnostic"]["order_send_called"] is True
    assert out["last_error"] == [-10004, "No IPC connection"]
    assert out["forensics"]["order_send_returned"] is False
    assert term.order_send_calls == 1, "one attempt only — never a retry"


def test_05_an_exception_is_named_with_type_message_and_traceback(monkeypatch):
    term = ForensicTerminal(send_behaviour="raise")
    b = _bridge(monkeypatch, term)
    out = b.send_market_order(_request())
    fx = out["forensics"]
    assert fx["outcome"] == "ORDER_SEND_EXCEPTION"
    assert out["diagnostic"]["phase"] == "ORDER_SEND_EXCEPTION"
    assert fx["exception_type"] == "RuntimeError"
    assert "terminal socket closed" in fx["exception_message"]
    assert fx["traceback_tail"], "the traceback tail is preserved, not swallowed"
    assert term.order_send_calls == 1


def test_06_an_unreadable_object_gets_its_own_phase(monkeypatch):
    class Unreadable(ForensicTerminal):
        def order_send(self, request):
            self.order_send_calls += 1
            return _Obj(order=0, deal=0, volume=0.0, comment="")
    term = Unreadable()
    b = _bridge(monkeypatch, term)
    out = b.send_market_order(_request())
    assert out["diagnostic"]["phase"] == "ORDER_SEND_UNUSABLE_RESULT"
    assert out["forensics"]["outcome"] == "ORDER_SEND_RETURNED_RESULT"
    assert out["forensics"]["order_send_result_repr"], "the unreadable object is still preserved"


def test_07_filling_is_resolved_from_metadata_and_confirmed_by_order_check(monkeypatch):
    term = ForensicTerminal()
    b = _bridge(monkeypatch, term)
    out = b.send_market_order(_request())
    res = out["filling_resolution"]
    assert res["evaluated"] is True
    assert res["symbol_filling_mode"] is not None      # the raw bitmask
    assert res["candidates"], "the candidates come from the symbol's own metadata"
    assert out["forensics"]["type_filling"] in [c["value"] for c in res["candidates"]] or \
        out["forensics"]["type_filling"] is not None
    assert term.checked_fillings and term.checked_fillings[-1] == 2, \
        "the mode actually used is the one the terminal accepted (this symbol: Return)"
    assert term.sent_requests and term.sent_requests[0]["type_filling"] == 2


def test_08_post_send_verification_asks_the_terminal(monkeypatch):
    term = ForensicTerminal()
    b = _bridge(monkeypatch, term)
    out = b.send_market_order(_request())
    ver = out["verification"]
    assert ver["queried"] is True
    assert ver["symbol"] == "XAUUSD" and ver["magic"] == _request()["magic"]
    assert ver["positions"] and ver["positions"][0]["ticket"] == 555001
    assert ver["verified"] is True
    assert "exists in the terminal" in ver["verdict"]
    assert out["forensics"]["post_send"]["verified"] is True


def test_09_no_position_in_the_terminal_means_not_verified(monkeypatch):
    class NoPosition(ForensicTerminal):
        def order_send(self, request):
            self.order_send_calls += 1
            self._last_error = (0, "Ok")
            return _v52._result(10009, deal=0, order=0, volume=request["volume"],
                                price=request["price"], comment="Done")

        def positions_get(self, ticket=None, symbol=None):
            return []

        def orders_get(self, ticket=None, symbol=None):
            return []
    term = NoPosition()
    b = _bridge(monkeypatch, term)
    out = b.send_market_order(_request())
    assert out["verification"]["queried"] is True
    assert out["verification"]["verified"] is False
    assert "NO position" in out["verification"]["verdict"]


def test_10_a_lost_session_is_re_established_before_sending(monkeypatch):
    term = ForensicTerminal()
    term.initialized = False                    # someone shut the terminal down
    b = _bridge(monkeypatch, term)
    out = b.send_market_order(_request())
    assert term.initialize_calls >= 1, "the session is re-established before the order"
    assert out["called"] is True and term.order_send_calls == 1, "and it is still ONE send"


def test_11_a_session_that_cannot_be_restored_refuses_without_sending(monkeypatch):
    class Dead(ForensicTerminal):
        def initialize(self, path=None, **kw):
            self.initialize_calls += 1
            return False

        def terminal_info(self):
            return None
    term = Dead()
    b = _bridge(monkeypatch, term)
    out = b.send_market_order(_request())
    assert out["called"] is False and term.order_send_calls == 0
    assert out["diagnostic"]["phase"] == "ORDER_SESSION_UNAVAILABLE"
    assert out["exception_type"] == "NoSession"


def test_12_a_probe_cannot_tear_down_the_session_inside_the_order_path(monkeypatch):
    """§2.1 — account probing used to call mt5.shutdown() with no restore, which is
    how an order_send could answer None on a healthy terminal."""
    from app.mt5 import mt5_real
    from app.mt5 import accounts

    term = ForensicTerminal()
    term.initialized = True
    _install(term, monkeypatch)

    # the probe is invoked from inside the order path (worst case ordering)
    real_send = term.order_send

    def send_after_probe(request):
        accounts.probe_terminal("C:/MT5/terminal64.exe")     # shuts the terminal down
        return real_send(request)

    term.order_send = send_after_probe
    b = mt5_real.MT5RealBridge()
    b._connected = True
    out = b.send_market_order(_request())
    assert term.shutdown_calls >= 1, "the probe did shut the terminal down"
    assert term.initialize_calls >= 1, "…and the live session was restored afterwards"
    assert out["called"] is True
    assert out["forensics"]["outcome"] == "ORDER_SEND_RETURNED_RESULT"


def test_13_the_session_lock_excludes_another_thread_during_check_and_send(monkeypatch):
    """A probe/switch running on ANOTHER thread must be unable to enter the session
    while the order path is between order_check and order_send."""
    import threading
    from app.mt5 import mt5_real

    term = ForensicTerminal()
    b = _bridge(monkeypatch, term)
    inside = threading.Event()
    release = threading.Event()
    outcome = {}
    real_check = term.order_check

    def check(request):
        inside.set()
        release.wait(5.0)
        return real_check(request)

    def other_thread():
        inside.wait(5.0)
        got = mt5_real.SESSION_LOCK.acquire(timeout=0.3)
        outcome["acquired"] = got
        if got:
            mt5_real.SESSION_LOCK.release()

    term.order_check = check
    t = threading.Thread(target=other_thread, daemon=True)
    t.start()
    b.send_market_order(_request())
    release.set()
    t.join(5.0)
    assert outcome.get("acquired") is False, \
        "another thread cannot take the session lock while an order is in flight"


def test_14_the_manual_order_path_surfaces_all_of_it(monkeypatch, tmp_path):
    """The dashboard response carries the forensics, the session and the terminal state."""
    from app.mt5 import execution as ex
    from app.mt5 import mt5_real
    from app.db.database import Database
    term = ForensicTerminal()
    _install(term, monkeypatch)
    b = mt5_real.MT5RealBridge()
    b._connected = True
    db = Database(str(tmp_path / "forensics.db"))
    monkeypatch.setattr(ex, "_db", lambda: db)
    monkeypatch.setattr("app.mt5.factory.get_bridge", lambda: b, raising=False)
    monkeypatch.setattr(ex, "get_bridge", lambda: b, raising=False)
    payload = dict(_v52._payload(), magic=777001)
    res = ex.place_demo_order(payload, bridge=b)
    assert res["ok"] is True
    assert res["demo_trade_acceptance"] == "PASS", \
        "PASS only because the terminal itself shows the position"
    assert "PASS requires a position or order" in res["demo_trade_acceptance_rule"]
    assert res["order_send"]["outcome"] == "ORDER_SEND_RETURNED_RESULT"
    assert res["order_send"]["last_error_before"] is not None
    assert res["order_send"]["last_error_after"] is not None
    assert res["order_send"]["result_type"]
    assert res["terminal_state"]["verified"] is True
    assert res["verification"]["terminal_position_or_order_exists"] is True
    assert res["session"]["snapshot"]["terminal_info_available"] is True
    assert res["session"]["ok"] is True and res["session"]["recovered"] is False


def test_15_the_success_path_never_claims_success_without_terminal_state(monkeypatch, tmp_path):
    from app.mt5 import execution as ex
    from app.mt5 import mt5_real
    from app.db.database import Database

    class Ghost(ForensicTerminal):
        def positions_get(self, ticket=None, symbol=None):
            return []

        def orders_get(self, ticket=None, symbol=None):
            return []
    term = Ghost()
    _install(term, monkeypatch)
    b = mt5_real.MT5RealBridge()
    b._connected = True
    db = Database(str(tmp_path / "ghost.db"))
    monkeypatch.setattr(ex, "_db", lambda: db)
    monkeypatch.setattr("app.mt5.factory.get_bridge", lambda: b, raising=False)
    monkeypatch.setattr(ex, "get_bridge", lambda: b, raising=False)
    res = ex.place_demo_order(dict(_v52._payload(), magic=777001), bridge=b)
    assert res["demo_trade_acceptance"] == "FAIL_NO_TERMINAL_STATE", \
        "a retcode alone is never PASS"
    assert res["broker"]["retcode"] == 10009


# ===========================================================================
# Blocker B — one population, three ways to say starred_only
# ===========================================================================
def test_16_starred_only_true_false_and_omitted_never_422(client):
    urls = ["/api/nodes?filter=qualified&limit=0&starred_only=true",
            "/api/nodes?filter=qualified&limit=0&starred_only=false",
            "/api/nodes?filter=qualified&limit=0"]
    for url in urls:
        res = client.get(url)
        assert res.status_code == 200, f"{url} -> {res.status_code} {res.text[:200]}"
        body = res.json()
        assert body["ok"] is True and isinstance(body["nodes"], list)
        assert body["boundary_counts"]["user_filters"]["starred_only"] == \
            ("true" in url)


def test_17_the_string_undefined_is_refused_with_a_readable_reason(client):
    res = client.get("/api/nodes?filter=qualified&limit=0&starred_only=undefined")
    assert res.status_code == 422
    body = res.json()
    assert body["error"] == "VALIDATION_ERROR"
    assert "starred_only" in body["message"] and "undefined" in body["message"]


def test_18_the_index_reports_every_hop_of_the_count(client):
    body = client.get("/api/nodes?filter=qualified&limit=0").json()
    bc = body["boundary_counts"]
    for key in ("population_total", "population_alive", "population_qualified",
                "population_live_eligible", "live_testing_query_result_count",
                "serialized_node_count"):
        assert key in bc, f"{key} must be measured at this request"
        assert bc[key] is None or isinstance(bc[key], int)
    assert bc["serialized_node_count"] == len(body["nodes"])
    assert bc["live_testing_query_result_count"] == body["total"]
    assert "app.research.populations" in bc["authority"]


def test_19_the_index_and_the_population_endpoint_share_one_authority(client):
    """§3 — Overview's numbers and the table's numbers are the same numbers."""
    pops = client.get("/api/nodes/populations").json()
    idx = client.get("/api/nodes?filter=qualified&limit=0").json()
    bc = idx["boundary_counts"]
    assert bc["population_qualified"] == pops["counts"]["qualified"]
    assert bc["population_live_eligible"] == pops["counts"]["live"]
    assert bc["population_alive"] == pops["counts"]["alive"]
    assert bc["population_total"] == pops["counts"]["total"]
    # the explicit lifecycle names, same values
    assert bc["population_state_names"]["LIVE_TESTING_ELIGIBLE"] == pops["counts"]["live"]


def test_20_an_eligible_population_is_never_silently_reduced_to_zero(client):
    """If the authority says there are eligible nodes, the index must return them."""
    pops = client.get("/api/nodes/populations").json()
    live = int(pops["counts"].get("live") or 0)
    idx = client.get("/api/nodes?filter=qualified&limit=0").json()
    if live > 0:
        assert idx["total"] > 0, "the authority says there ARE eligible nodes"
        assert len(idx["nodes"]) == idx["total"]
        assert idx["boundary_counts"]["live_testing_query_result_count"] > 0
    else:
        assert idx["total"] == 0


def test_21_every_row_carries_its_real_records_not_placeholders(client):
    body = client.get("/api/nodes?filter=qualified&limit=0").json()
    for row in body["nodes"][:25]:
        assert row["node_id"], "a row without an identity is not a node record"
        assert row["symbol"] and row["timeframe"]
        assert row["node_label"].startswith("Node #")
        assert "bucket" in row and "qualification" in row
        assert str(row["node_label"]) != "Node #0"


def test_22_the_table_renders_the_hop_counts_and_asks_for_all_rows():
    src = (REPO / "frontend" / "src" / "components" / "LiveNodeIndex.jsx").read_text(encoding="utf-8")
    assert "boundary_counts" in src
    assert "rendered" in src and "authority" in src
    assert "limit: 0" in src
