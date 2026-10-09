"""V5 §13/§14 — the manual order panel's backend contract.

§13 requires the panel to work in *pips* as well as price (default 300-pip stop,
default $10 risk, everything editable), to show the broker's own constraints, and
to convert money-at-risk <-> lots in both directions. §14 requires a real
execution result afterwards: retcode, ticket, fill price, volume, SL/TP and the
broker's message — never a fabricated success.

The tests use the same MT5 double as the V4.x suites (the product code path runs
unchanged) and a scratch database, so no authoritative row and no DATA file is
touched.
"""
from __future__ import annotations

import importlib.util
import time
from pathlib import Path

import pytest


def _load(name: str):
    p = Path(__file__).resolve().parent / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_v42 = _load("test_v4_2_mt5_execution.py")
_v48 = _load("test_v4_8_dashboard.py")
FakeMT5Terminal = _v42.FakeMT5Terminal
FakeMT5Bridge = _v42.FakeMT5Bridge
SYMBOL = "XAUUSD"
# V6.4 — the digits rule (MT5_BRIDGE_DETAILS.txt): XAUUSD on a 2-digit feed
# (digits=2, point=0.01) has pip_size = 0.01, so 300 pips is a $3.00 price
# distance. The V5-era constant here (0.10 = "10 points of 0.01") made every
# gold stop ten times too far and is what V6.4 corrected.
PIP = 0.01


@pytest.fixture()
def read_only_bridge(monkeypatch):
    """The read-only stub from the V4.8 suite (specs + quote, nothing else)."""
    from app import mt5
    bridge = _v48._Bridge()
    monkeypatch.setattr(mt5, "get_bridge", lambda: bridge)
    return bridge


# ===========================================================================
# pip <-> price conversion (§13)
# ===========================================================================
def test_01_pips_convert_to_a_price_for_both_directions(client, read_only_bridge):
    buy = client.post("/api/mt5-execution/preview", json={
        "symbol": SYMBOL, "side": "BUY", "entry": 2450.0, "sl_pips": 300, "risk_amount": 10.0})
    assert buy.status_code == 200, buy.text
    body = buy.json()
    assert body["levels"]["pip_size"] == pytest.approx(PIP)
    assert body["levels"]["sl"] == pytest.approx(2450.0 - 300 * PIP)      # 2420.00
    assert body["levels"]["sl_from_pips"] is True
    assert body["levels"]["sl_pips"] == pytest.approx(300.0)

    sell = client.post("/api/mt5-execution/preview", json={
        "symbol": SYMBOL, "side": "SELL", "entry": 2450.0, "sl_pips": 300, "risk_amount": 10.0})
    assert sell.json()["levels"]["sl"] == pytest.approx(2450.0 + 300 * PIP)   # stop above


def test_02_take_profit_pips_and_reward_risk(client, read_only_bridge):
    res = client.post("/api/mt5-execution/preview", json={
        "symbol": SYMBOL, "side": "BUY", "entry": 2450.0,
        "sl_pips": 300, "tp_pips": 600, "risk_amount": 100.0})
    body = res.json()
    assert body["levels"]["tp"] == pytest.approx(2450.0 + 600 * PIP)
    assert body["estimate"]["reward_risk"] == pytest.approx(2.0, rel=0.02)
    # the money estimate uses the broker tick value, so it matches MT5
    assert body["estimate"]["per_point_value"] is not None
    assert body["estimate"]["loss_at_sl"] is not None


def test_03_a_price_in_currency_is_reported_back_in_pips(client, read_only_bridge):
    body = client.post("/api/mt5-execution/preview", json={
        "symbol": SYMBOL, "side": "BUY", "entry": 2450.0, "sl": 2440.0,
        "volume": 0.01}).json()
    # a $10.00 price distance under the corrected XAUUSD pip (0.01) is 1000 pips
    assert body["levels"]["sl_pips"] == pytest.approx(10.0 / PIP)
    assert body["mode"] == "lot_to_risk"


def test_04_a_stop_on_the_wrong_side_is_flagged_not_silently_used(client, read_only_bridge):
    above = client.post("/api/mt5-execution/preview", json={
        "symbol": SYMBOL, "side": "BUY", "entry": 2450.0, "sl": 2460.0, "risk_amount": 10.0}).json()
    assert any("wrong side" in w for w in above["levels"]["warnings"])
    below = client.post("/api/mt5-execution/preview", json={
        "symbol": SYMBOL, "side": "BUY", "entry": 2450.0, "tp": 2440.0, "risk_amount": 10.0}).json()
    assert any("wrong side" in w for w in below["levels"]["warnings"])


def test_05_the_panel_defaults_come_from_config(client, read_only_bridge):
    body = client.post("/api/mt5-execution/preview", json={
        "symbol": SYMBOL, "side": "BUY", "entry": 2450.0, "sl_pips": 300,
        "risk_amount": 10.0}).json()
    from app.config import get_config
    lt = get_config().live_testing
    assert body["defaults"]["sl_pips"] == pytest.approx(lt.manual_sl_pips_default)
    assert body["defaults"]["risk_amount"] == pytest.approx(lt.manual_risk_amount_default)
    assert body["defaults"]["sl_pips"] == pytest.approx(300.0)      # spec §13 default
    assert body["defaults"]["risk_amount"] == pytest.approx(10.0)
    assert body["defaults"]["symbol"] == "XAUUSD"
    assert body["symbol_info"]["volume_min"] is not None            # broker constraints shown


def test_06_risk_money_and_lots_agree_in_both_directions(client, read_only_bridge):
    """$100 at risk with a 300-pip gold stop -> a lot size whose loss is that $100."""
    res = client.post("/api/mt5-execution/preview", json={
        "symbol": SYMBOL, "side": "BUY", "entry": 2450.0, "sl_pips": 300,
        "risk_amount": 100.0})
    body = res.json()
    lots = body["sizing"]["volume"]
    back = client.post("/api/mt5-execution/preview", json={
        "symbol": SYMBOL, "side": "BUY", "entry": 2450.0, "sl_pips": 300,
        "volume": lots}).json()
    assert back["sizing"]["actual_risk"] == pytest.approx(body["estimate"]["loss_at_sl"], rel=0.05)
    # sizing rounds DOWN to the broker step, so the loss can never exceed the request
    assert body["estimate"]["loss_at_sl"] <= 100.0 + 1e-9


def test_06b_an_amount_below_the_broker_minimum_is_refused_not_rounded_up(
        client, read_only_bridge):
    """V6.4 — with the corrected XAUUSD pip (0.01), a 300-pip stop is a $3.00
    price distance ($300 of risk per 1.0 lot), so the $10 default now sizes to
    0.03 lots. An amount no whole step can buy must still be BLOCKED rather
    than silently rounded up — the operator decides what to do.
    """
    body = client.post("/api/mt5-execution/preview", json={
        "symbol": SYMBOL, "side": "BUY", "entry": 2450.0, "sl_pips": 300,
        "risk_amount": 10.0}).json()
    assert body["ok"] is True
    assert body["sizing"]["volume"] == pytest.approx(0.03)     # floored to the 0.01 step
    tiny = client.post("/api/mt5-execution/preview", json={
        "symbol": SYMBOL, "side": "BUY", "entry": 2450.0, "sl_pips": 300,
        "risk_amount": 1.0}).json()
    assert tiny["ok"] is False
    assert tiny["blocked"]["code"] == "VOLUME_BELOW_MINIMUM"
    assert tiny["blocked"]["detail"].get("volume_min") == pytest.approx(0.01)
    assert tiny["estimate"]["loss_at_sl"] is None      # no size -> no money estimate


def test_07_preview_still_places_nothing(client, read_only_bridge):
    from app.mt5.execution import recent_orders
    before = len(recent_orders(50))
    client.post("/api/mt5-execution/preview", json={
        "symbol": SYMBOL, "side": "BUY", "entry": 2450.0, "sl_pips": 300, "risk_amount": 10.0})
    assert len(recent_orders(50)) == before


# ===========================================================================
# §14 — a placed order reports the broker's real answer
# ===========================================================================
@pytest.fixture()
def order_env(monkeypatch, tmp_path):
    """The V4.2 double wired into the real execution path + scratch audit DB."""
    import app.db.database as database_mod
    import app.mt5.execution as ex
    import app.mt5.factory as factory
    import app.mt5.mt5_real as real
    from app.db.database import Database

    term = FakeMT5Terminal()
    bridge = FakeMT5Bridge(term)
    db = Database(str(tmp_path / "v5_manual.db"))
    monkeypatch.setattr(database_mod, "get_db", lambda: db)
    monkeypatch.setattr(ex, "_db", lambda: db)
    monkeypatch.setattr(ex, "mt5_module", lambda: term)
    monkeypatch.setattr(factory, "get_bridge", lambda: bridge)
    monkeypatch.setattr(real, "MT5_PACKAGE_AVAILABLE", True)
    return {"term": term, "bridge": bridge, "db": db}


def _payload(**over):
    """Priced against the double's quote (ask 2400.20), as the V4.2 suite does."""
    from app.mt5 import execution as ex
    p = {"symbol": SYMBOL, "side": "buy", "volume": 0.02, "sl": 2390.00, "tp": 2410.00,
         "confirm": ex.PLACE_CONFIRMATION, "client_order_id": "ord-" + str(time.time_ns())}
    p.update(over)
    return p


def test_08_a_confirmed_order_reports_retcode_ticket_fill_and_protection(order_env):
    from app.mt5 import execution as ex
    res = ex.place_demo_order(_payload())
    assert res["ok"] is True, res

    broker = res["broker"]
    assert broker["retcode"] is not None and broker["message"]
    order = res["order"]
    assert order["ticket"] and order["ticket"] != 0
    execution = res["execution"]
    assert execution["volume"] == pytest.approx(0.02)
    assert execution["exec_price"] is not None
    assert execution["sl_requested"] == pytest.approx(2390.00)
    assert execution["tp_requested"] == pytest.approx(2410.00)
    assert execution["sl_broker"] is not None        # what the broker actually accepted
    assert execution["sl_tp_verified"] is True


def test_09_the_same_result_is_persisted_for_the_read_back(order_env):
    from app.mt5 import execution as ex
    cid = "ord-" + str(time.time_ns())
    res = ex.place_demo_order(_payload(client_order_id=cid))
    row = order_env["db"].get_manual_mt5_order(cid)
    assert row is not None
    assert row["status"] == "POSITION_OPEN"
    assert row["retcode"] == res["broker"]["retcode"]
    assert row["order_ticket"] == res["order"]["ticket"]
    assert row["broker_sl"] == res["execution"]["sl_broker"]
    assert row["account_login"] == res["account"]["login"]


def test_10_a_rejection_is_reported_as_a_failure_with_its_retcode(order_env):
    from app.mt5 import execution as ex
    order_env["term"].default_result = {
        "retcode": 10016, "order": 0, "deal": 0, "price": 0.0, "volume": 0.0,
        "comment": "invalid stops"}
    res = ex.place_demo_order(_payload(client_order_id="ord-" + str(time.time_ns())))
    assert res["ok"] is False
    assert res["broker"]["retcode"] == 10016
    assert res["broker"]["message"]
    assert res["order"]["ticket"] in (0, None)     # no ticket is invented


def test_11_broker_state_is_never_assumed_when_the_terminal_is_silent(order_env):
    """No position after DONE -> unconfirmed, never reported as executed."""
    from app.mt5 import execution as ex
    term = order_env["term"]
    term.register_position = False
    term.default_result = {"retcode": 10009, "order": 999999, "deal": 666999,
                           "price": 2400.10, "volume": 0.02, "comment": "done"}
    res = ex.place_demo_order(_payload(client_order_id="ord-" + str(time.time_ns())))
    assert res["status"] == "EXECUTED_UNCONFIRMED"
    assert res["verification"]["position_found"] is False
    assert "not confirmed" in res["label"].lower()


def test_12_placement_requires_the_backend_confirmation_phrase(client, read_only_bridge):
    res = client.post("/api/mt5-execution/place", json={
        "symbol": SYMBOL, "side": "BUY", "volume": 0.01, "sl": 2420.0})
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "CONFIRMATION_REQUIRED"
