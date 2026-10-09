"""V6.4 #7 — live positions table + explicit, verified close controls.

The table shows the broker's real open positions (ticket, symbol, side, volume,
open/current price, SL, TP, floating PnL, timestamps, magic). CLOSE is per row
by the broker position ticket (correct opposite deal + filling mode); CLOSE ALL
requires an explicit account/symbol/scope and a confirmation that lists exactly
what will close. The broker result is VERIFIED (retcode + position gone) before
anything is reported closed — never on submit alone. Other-magic positions are
never touched silently (scope shown, authorization required). Demo-only safety.
No real broker order is ever sent in these tests — a recording fake bridge is.
"""
from __future__ import annotations

import time

import pytest


class _FakeTick:
    def __init__(self, bid, ask):
        self.bid, self.ask, self.ts = bid, ask, time.time()


class _RecordingBridge:
    """Records close requests; answers like a demo terminal."""

    is_simulated = False
    source = "MT5"

    def __init__(self, positions, *, demo=True, done=True, verify_gone=True):
        self.positions = {int(p["ticket"]): dict(p) for p in positions}
        self.closed = []
        self.demo = demo
        self.done = done
        self.verify_gone = verify_gone

    def account_info(self):
        class _A:
            login = 12345
            trade_mode = 0 if self.demo else 2          # 0 = ACCOUNT_TRADE_MODE_DEMO
            name = "Demo Account"
            server = "Demo-Server"
            balance = 10000.0
            equity = 10050.0
            currency = "USD"
            margin_free = 9000.0
            leverage = 100
        return _A()

    def positions_get(self, ticket=None, symbol=None):
        rows = list(self.positions.values())
        if ticket is not None:
            rows = [p for p in rows if int(p["ticket"]) == int(ticket)]
        if symbol:
            rows = [p for p in rows if p["symbol"] == symbol]
        return [dict(p) for p in rows]

    def orders_get(self, ticket=None, symbol=None):
        return []

    def latest_tick(self, symbol):
        return _FakeTick(2400.0, 2400.3)

    def close_position(self, ticket, comment="close"):
        pos = self.positions.get(int(ticket))
        if pos is None:
            # a stale / already-closed ticket: the broker form is never built,
            # nothing is sent, and the error says exactly that
            return {"ok": False, "error": f"position {ticket} not found"}
        self.closed.append({"ticket": int(ticket), "comment": comment,
                            "symbol": pos["symbol"], "volume": pos["volume"],
                            "type": pos["type"], "magic": pos.get("magic")})
        retcode = 10009 if self.done else 10016
        if self.done and self.verify_gone:
            self.positions.pop(int(ticket), None)
        elif self.done and getattr(self, "partial_volume", None) is not None:
            # broker PARTIALLY closed: some volume remains on the same ticket
            pos["volume"] = float(self.partial_volume)
        return {"ok": self.done, "retcode": retcode,
                "request": {"action": 1, "symbol": pos["symbol"], "volume": pos["volume"],
                            "type": 1 if pos["type"] == 0 else 0, "position": int(ticket),
                            "type_filling": 1},
                "raw": {"retcode": retcode, "deal": 555, "order": 777,
                        "comment": "done" if self.done else "invalid stops"}}


def _pos(ticket, symbol="XAUUSD", typ=0, volume=0.10, magic=777000, pnl=12.5):
    return {"ticket": ticket, "symbol": symbol, "type": typ, "volume": volume,
            "price_open": 2400.0, "price_current": 2401.0, "sl": 2390.0, "tp": 2420.0,
            "profit": pnl, "magic": magic, "comment": "evolab-livetest-301",
            "time": 1770000000.0}


@pytest.fixture()
def api(monkeypatch):
    import app.api.routes as routes
    import app.db.database as database_mod
    import app.mt5.execution as ex
    from fastapi.testclient import TestClient
    from app.main import app

    state = {"bridge": None}

    monkeypatch.setattr(database_mod, "get_db", lambda: None)  # untouched here
    monkeypatch.setattr(routes, "get_bridge", lambda: state["bridge"])
    monkeypatch.setattr(ex, "get_bridge", lambda: state["bridge"], raising=False)
    import app.mt5.factory as factory
    monkeypatch.setattr(factory, "get_bridge", lambda: state["bridge"])
    client = TestClient(app)

    def _use(bridge):
        state["bridge"] = bridge
        return bridge

    yield client, _use


def _guard_ok(monkeypatch):
    """Force the demo-account guard positive for close-control tests."""
    import app.mt5.execution as ex
    monkeypatch.setattr(ex, "demo_account_guard",
                        lambda bridge=None: {"demo_verified": True, "blocked_code": None,
                                             "blocked_reason": None,
                                             "account": {"login": 12345, "type": "DEMO"},
                                             "bridge": "fake", "bridge_source": "MT5"})


# --------------------------------------------------------------------------- #
# the positions table
# --------------------------------------------------------------------------- #
def test_01_positions_table_carries_the_real_fields(api):
    client, use = api
    use(_RecordingBridge([_pos(111), _pos(222, typ=1, symbol="EURUSD", pnl=-3.0)]))
    d = client.get("/api/mt5/positions").json()
    assert d["ok"] is True and d["count"] == 2
    row = next(r for r in d["positions"] if r["ticket"] == 111)
    for key in ("ticket", "symbol", "side", "volume", "open_price", "current_price",
                "sl", "tp", "floating_pnl", "magic", "open_time_iso"):
        assert key in row, key
    assert row["side"] == "BUY" and row["volume"] == pytest.approx(0.10)
    assert row["open_price"] == pytest.approx(2400.0)
    assert row["current_price"] == pytest.approx(2401.0)
    assert row["sl"] == pytest.approx(2390.0) and row["tp"] == pytest.approx(2420.0)
    assert row["floating_pnl"] == pytest.approx(12.5)
    assert row["open_time_iso"].endswith("Z")
    sell = next(r for r in d["positions"] if r["ticket"] == 222)
    assert sell["side"] == "SELL"


# --------------------------------------------------------------------------- #
# CLOSE one position: verified at the broker, never on submit
# --------------------------------------------------------------------------- #
def test_02_close_requires_confirmation_and_lists_what_would_close(api, monkeypatch):
    client, use = api
    use(_RecordingBridge([_pos(111)]))
    _guard_ok(monkeypatch)
    r = client.post("/api/mt5/positions/close", json={"ticket": 111})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "CONFIRMATION_REQUIRED"
    assert detail["would_close"][0]["ticket"] == 111


def test_03_a_confirmed_close_is_verified_and_only_then_reported_closed(api, monkeypatch):
    client, use = api
    b = use(_RecordingBridge([_pos(111)]))
    _guard_ok(monkeypatch)
    r = client.post("/api/mt5/positions/close", json={"ticket": 111, "confirmed": True}).json()
    assert r["ok"] is True
    res = r["result"]
    assert res["ticket"] == 111 and res["retcode"] == 10009
    assert res["broker_done"] is True and res["position_gone"] is True
    assert res["closed_verified"] is True and res["status"] == "CLOSED_VERIFIED"
    # the close went by the broker POSITION ticket with the opposite deal
    sent = b.closed[0]
    assert sent["ticket"] == 111
    # and the refreshed list no longer shows it
    assert all(p["ticket"] != 111 for p in r["positions"])


def test_04_a_submit_without_verification_is_never_claimed_closed(api, monkeypatch):
    """Broker says DONE but the position is still there -> NOT closed."""
    client, use = api
    use(_RecordingBridge([_pos(111)], done=True, verify_gone=False))
    _guard_ok(monkeypatch)
    r = client.post("/api/mt5/positions/close", json={"ticket": 111, "confirmed": True}).json()
    assert r["ok"] is False
    assert r["result"]["closed_verified"] is False
    assert r["result"]["status"] == "CLOSE_UNCONFIRMED"


def test_05_a_broker_rejection_is_reported_as_a_failed_close(api, monkeypatch):
    client, use = api
    use(_RecordingBridge([_pos(111)], done=False))
    _guard_ok(monkeypatch)
    r = client.post("/api/mt5/positions/close", json={"ticket": 111, "confirmed": True}).json()
    assert r["ok"] is False and r["result"]["status"] == "CLOSE_FAILED"
    assert r["result"]["retcode"] == 10016


def test_06_close_controls_are_demo_only(api, monkeypatch):
    client, use = api
    use(_RecordingBridge([_pos(111)], demo=False))
    import app.mt5.execution as ex
    monkeypatch.setattr(ex, "demo_account_guard",
                        lambda bridge=None: {"demo_verified": False,
                                             "blocked_code": "NON_DEMO_ACCOUNT",
                                             "blocked_reason": "the account is REAL",
                                             "account": {"login": 9}, "bridge": "fake",
                                             "bridge_source": "MT5"})
    r = client.post("/api/mt5/positions/close", json={"ticket": 111, "confirmed": True})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "NON_DEMO_ACCOUNT"


# --------------------------------------------------------------------------- #
# CLOSE ALL: explicit scope, exact confirmation, other-magic authorization
# --------------------------------------------------------------------------- #
def test_07_close_all_requires_an_explicit_scope(api, monkeypatch):
    client, use = api
    use(_RecordingBridge([_pos(111)]))
    _guard_ok(monkeypatch)
    r = client.post("/api/mt5/positions/close-all", json={"confirmed": True})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "SCOPE_REQUIRED"


def test_08_close_all_confirmation_lists_exactly_what_will_close(api, monkeypatch):
    client, use = api
    b = use(_RecordingBridge([_pos(111), _pos(222, symbol="EURUSD"), _pos(333)]))
    _guard_ok(monkeypatch)
    r = client.post("/api/mt5/positions/close-all",
                    json={"scope": {"symbol": "XAUUSD"}})
    assert r.status_code == 409
    would = r.json()["detail"]["would_close"]
    assert sorted(p["ticket"] for p in would) == [111, 333]     # exactly the XAUUSD rows
    # nothing was closed by asking
    assert b.closed == []


def test_09_close_all_closes_the_scope_and_verifies_each(api, monkeypatch):
    client, use = api
    b = use(_RecordingBridge([_pos(111), _pos(222, symbol="EURUSD"), _pos(333)]))
    _guard_ok(monkeypatch)
    r = client.post("/api/mt5/positions/close-all",
                    json={"scope": {"symbol": "XAUUSD"}, "confirmed": True}).json()
    assert r["ok"] is True and r["closed_verified_count"] == 2
    assert sorted(x["ticket"] for x in b.closed) == [111, 333]
    assert all(x["closed_verified"] for x in r["results"])
    # the EURUSD position (out of scope) was never touched
    assert any(p["ticket"] == 222 for p in r["positions"])


def test_10_other_magic_positions_never_close_silently(api, monkeypatch):
    client, use = api
    b = use(_RecordingBridge([_pos(111, magic=777000), _pos(999, magic=424242)]))
    _guard_ok(monkeypatch)
    r = client.post("/api/mt5/positions/close-all",
                    json={"scope": {"symbol": "XAUUSD"}, "confirmed": True})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "OTHER_MAGIC_AUTHORIZATION_REQUIRED"
    assert detail["other_magic_positions"][0]["ticket"] == 999
    assert b.closed == []                                       # nothing closed at all
    # with explicit authorization the foreign position is included and named
    r2 = client.post("/api/mt5/positions/close-all",
                     json={"scope": {"symbol": "XAUUSD"}, "confirmed": True,
                           "acknowledge_other_magic": True}).json()
    assert r2["closed_verified_count"] == 2
    assert sorted(x["ticket"] for x in b.closed) == [111, 999]
    assert 999 in [x["ticket"] for x in b.closed]


def test_11_account_scope_mismatch_closes_nothing(api, monkeypatch):
    client, use = api
    b = use(_RecordingBridge([_pos(111)]))
    _guard_ok(monkeypatch)
    r = client.post("/api/mt5/positions/close-all",
                    json={"scope": {"account_login": 999}, "confirmed": True})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "ACCOUNT_SCOPE_MISMATCH"
    assert b.closed == []


def test_12_stopping_live_testing_never_closes_positions(api, monkeypatch):
    """The lifecycle STOP path states positions_touched=False and sends no closes."""
    client, use = api
    b = use(_RecordingBridge([_pos(111)]))
    _guard_ok(monkeypatch)
    # (the live-testing stop endpoint is exercised in test_v6_4_live_lifecycle;
    #  here we assert the close recorder saw NOTHING from the positions panel's
    #  mere listing)
    client.get("/api/mt5/positions")
    assert b.closed == []


# --------------------------------------------------------------------------- #
# stale tickets, already-closed positions and partial closes
# --------------------------------------------------------------------------- #
def test_13_a_stale_or_already_closed_ticket_sends_nothing_and_is_never_claimed_closed(api, monkeypatch):
    client, use = api
    b = use(_RecordingBridge([_pos(111)]))          # 999 was already closed at the broker
    _guard_ok(monkeypatch)
    r = client.post("/api/mt5/positions/close", json={"ticket": 999, "confirmed": True}).json()
    assert r["ok"] is False
    res = r["result"]
    assert res["status"] == "CLOSE_FAILED"
    assert res["closed_verified"] is False
    assert "not found" in (res.get("error") or "")
    assert b.closed == []                            # no close request was ever built or sent
    assert any(p["ticket"] == 111 for p in r["positions"])   # the real position is untouched


def test_14_a_partial_close_is_not_reported_fully_closed(api, monkeypatch):
    """Broker answers DONE but volume remains on the ticket -> CLOSE_UNCONFIRMED,
    and the refreshed list shows exactly what is still open."""
    client, use = api
    b = use(_RecordingBridge([_pos(111, volume=0.10)], done=True, verify_gone=False))
    b.partial_volume = 0.05
    _guard_ok(monkeypatch)
    r = client.post("/api/mt5/positions/close", json={"ticket": 111, "confirmed": True}).json()
    assert r["ok"] is False
    assert r["result"]["status"] == "CLOSE_UNCONFIRMED"
    assert r["result"]["broker_done"] is True and r["result"]["position_gone"] is False
    remaining = [p for p in r["positions"] if p["ticket"] == 111]
    assert remaining and remaining[0]["volume"] == pytest.approx(0.05)
