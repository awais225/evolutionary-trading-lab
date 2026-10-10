"""V6.5.1 §7/§11 — GET /api/mt5/account (balance / equity / free margin).

Truthfulness contracts: live figures come from the active bridge's account_info()
only; a simulator's numbers are labelled SIMULATED (never presented as live);
disconnected / missing-field / error cases return null numbers plus an explicit
reason — never 0, never a fabricated value; no credential ever appears in the
payload. Deterministic bridge stubs only; no terminal, no orders.
"""
from __future__ import annotations

import pytest


class FakeAccount:
    def __init__(self, balance=10000.0, equity=10250.5, margin_free=9800.25,
                 login=51041234, server="Demo-Server", currency="USD",
                 leverage=100, trade_mode=0):
        self.balance = balance
        self.equity = equity
        self.margin_free = margin_free
        self.login = login
        self.server = server
        self.currency = currency
        self.leverage = leverage
        self.trade_mode = trade_mode


class FakeBridge:
    name = "mt5_real"
    source = "MT5"
    is_simulated = False
    _connected = True
    password = "super-secret-must-never-leak"

    def __init__(self, acct=None, fail=False):
        self._acct = acct
        self.fail = fail

    def account_info(self):
        if self.fail:
            raise RuntimeError("terminal disconnected")
        return self._acct


@pytest.fixture(autouse=True)
def _stub_guard(monkeypatch):
    """Route the endpoint's guard at the bridge object it actually inspects."""
    import app.api.routes as _routes
    real = _routes.get_bridge

    def _fake():
        b = getattr(_fake, "bridge", None)
        return b if b is not None else real()

    monkeypatch.setattr(_routes, "get_bridge", _fake)

    def set_bridge(b):
        _fake.bridge = b

    _stub_guard.set_bridge = set_bridge
    yield set_bridge
    _fake.bridge = None


def test_01_connected_account_reports_balance_equity_free_margin(client, _stub_guard):
    _stub_guard(FakeBridge(FakeAccount()))
    r = client.get("/api/mt5/account")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["available"] is True
    assert body["connected"] is True
    assert body["data_kind"] == "LIVE_ACCOUNT"
    acct = body["account"]
    assert acct["balance"] == 10000.0
    assert acct["equity"] == 10250.5
    assert acct["margin_free"] == 9800.25
    assert acct["currency"] == "USD"
    assert body["checked_at"] > 0                     # freshness timestamp


def test_02_simulator_values_are_labelled_simulated_never_live(client, _stub_guard):
    b = FakeBridge(FakeAccount())
    b.source = "SIMULATOR"
    b.is_simulated = True
    _stub_guard(b)
    body = client.get("/api/mt5/account").json()
    assert body["available"] is True
    assert body["is_simulated"] is True
    assert body["data_kind"] == "SIMULATED"
    assert body["account"]["balance"] == 10000.0


def test_03_no_account_returns_explicit_unavailable_not_zeros(client, _stub_guard):
    _stub_guard(FakeBridge(None))
    body = client.get("/api/mt5/account").json()
    assert body["available"] is False
    assert body["data_kind"] == "UNAVAILABLE"
    assert body["account"] in (None, {}) or body["account"].get("balance") is None
    assert body["unavailable_reason"]
    assert "balance" not in (body.get("account") or {}) or \
        (body["account"] or {}).get("balance") is None


def test_04_missing_fields_are_listed_and_marked_unavailable(client, _stub_guard):
    acct = FakeAccount()
    acct.equity = None
    acct.margin_free = None
    _stub_guard(FakeBridge(acct))
    body = client.get("/api/mt5/account").json()
    assert body["available"] is False
    assert "equity" in body["missing_fields"]
    assert "margin_free" in body["missing_fields"]
    assert body["unavailable_reason"]


def test_05_bridge_exception_becomes_a_reason_not_a_crash(client, _stub_guard):
    _stub_guard(FakeBridge(fail=True))
    body = client.get("/api/mt5/account").json()
    assert body["ok"] is True
    assert body["available"] is False
    assert body["data_kind"] == "UNAVAILABLE"
    assert "disconnected" in body["unavailable_reason"].lower() or \
        "failed" in body["unavailable_reason"].lower()


def test_06_no_credentials_in_the_payload(client, _stub_guard):
    _stub_guard(FakeBridge(FakeAccount()))
    raw = client.get("/api/mt5/account").text
    assert "super-secret-must-never-leak" not in raw
    assert "password" not in raw.lower()
