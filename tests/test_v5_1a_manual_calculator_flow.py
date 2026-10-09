"""V5.1a-next §7-§11 — the manual trade calculator's exact user flow.

The brief asks for the *behaviour*, not a claim:

    form opens -> $10 -> 300 pips -> live quote -> calculated lot
    $10 -> $20            -> the lot follows
    lot size edited       -> the money at risk follows (bidirectional)
    300 pips -> 500 pips  -> the dependent values follow
    BUY -> live ASK       SELL -> live BID
    no market data        -> nothing is invented, the backend's reason is returned

Every number here is computed by the project's own sizing code
(`app/live_testing/risk.py`) against the *bridge's* symbol specification and the
*bridge's* quote — the same call the dashboard makes (`POST /api/mt5-execution/preview`).
The scratch bridge below is deliberately given unusual broker specs, so a
hard-coded pip/point/tick value anywhere in the path would fail these tests.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load(name: str):
    p = Path(__file__).resolve().parent / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_v48 = _load("test_v4_8_dashboard.py")
SYMBOL = "XAUUSD"
# V6.4 — the digits rule (MT5_BRIDGE_DETAILS.txt): XAUUSD (2-digit feed) has
# pip_size 0.01, so 300 pips is a $3.00 price distance ($300 of risk per lot).
# The V5-era constant here (0.10) made every gold stop ten times too far.
PIP = 0.01


@pytest.fixture()
def bridge(monkeypatch):
    """The read-only bridge stub, with the broker's own (deliberately odd) specs."""
    from app import mt5
    spec = _v48._Spec(digits=2, point=0.01, trade_tick_size=0.01, trade_tick_value=1.0,
                      volume_min=0.01, volume_max=50.0, volume_step=0.01)
    quote = _v48._Quote(bid=2400.0, ask=2400.3)
    b = _v48._Bridge(spec=spec, quote=quote)
    monkeypatch.setattr(mt5, "get_bridge", lambda: b)
    return b


def _preview(client, **payload):
    body = {"symbol": SYMBOL, "side": "BUY", "sl_pips": 300,
            "risk_amount": 10, "entry": None}
    body.update(payload)
    r = client.post("/api/mt5-execution/preview", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# ===========================================================================
# §7 — the form opens with $10 / 300 pips / live entry / calculated lot
# ===========================================================================
def test_01_defaults_are_10_dollars_and_300_pips_and_the_panel_says_what_they_mean(client, bridge):
    """$1 at a 300-pip XAUUSD stop (corrected pip 0.01: $300 of risk per lot)
    is 0.0033 lots — below the broker's 0.01 minimum.

    The product's answer is the honest one: the calculation runs, and because the
    money is *less* than one minimum lot can risk, the panel reports the block and
    the minimum required risk instead of silently rounding the risk up to $3.
    """
    out = _preview(client, risk_amount=1)
    assert out["defaults"]["risk_amount"] == 10.0
    assert out["defaults"]["sl_pips"] == 300.0
    assert out["read_only"] is True and out["orders_placed"] is False
    assert out["mode"] == "risk_to_lot"
    # the calculation itself ran with the broker's own maths
    assert out["blocked"]["code"] == "VOLUME_BELOW_MINIMUM"
    assert out["blocked"]["detail"]["raw_volume"] == pytest.approx(1.0 / 300.0, abs=1e-6)
    assert out["blocked"]["detail"]["volume_min"] == pytest.approx(0.01)
    assert out["blocked"]["detail"]["risk_per_lot"] == pytest.approx(300.0)
    assert out["sizing"] is None
    # the panel shows the operator what a legal lot would risk (0.01 x 3000 = $30)
    assert out["blocked"]["detail"]["volume_min"] * out["blocked"]["detail"]["risk_per_lot"] == pytest.approx(3.0)


def test_01b_a_viable_amount_produces_the_lot_automatically(client, bridge):
    out = _preview(client, risk_amount=300)          # $300 at 300 pips = 1.00 lots
    assert out["ok"] is True and out["blocked"] is None
    assert out["sizing"]["volume"] == pytest.approx(1.00)
    assert out["sizing"]["actual_risk"] == pytest.approx(300.0)


def test_02_the_quoted_entry_is_the_live_ask_for_a_buy_and_the_bid_for_a_sell(client, bridge):
    buy = _preview(client, side="BUY")
    sell = _preview(client, side="SELL")
    assert buy["levels"]["entry"] == pytest.approx(2400.3)      # live ASK, from the bridge
    assert sell["levels"]["entry"] == pytest.approx(2400.0)     # live BID, from the bridge
    # the stop is placed against the entry, from the pip distance
    assert buy["levels"]["sl"] == pytest.approx(2400.3 - 300 * PIP)
    assert sell["levels"]["sl"] == pytest.approx(2400.0 + 300 * PIP)
    assert buy["levels"]["sl_from_pips"] is True and sell["levels"]["sl_from_pips"] is True


def test_03_the_lot_is_computed_by_the_broker_spec_not_by_a_hard_coded_pip_value(client, bridge):
    out = _preview(client, risk_amount=300)
    # V6.4: 300 pips = 3.00 price units / tick size 0.01 * tick value 1.0 = 300 per lot
    assert out["sizing"]["risk_per_lot"] == pytest.approx(300.0)
    assert out["sizing"]["volume"] == pytest.approx(300.0 / 300.0)
    assert out["symbol_info"]["volume_step"] == 0.01
    # and an amount that no whole step can buy is refused, never rounded up
    flat = _preview(client, risk_amount=2)
    assert flat["ok"] is False and flat["blocked"]["code"] == "VOLUME_BELOW_MINIMUM"


def test_04_doubling_the_amount_doubles_the_lot(client, bridge):
    a = _preview(client, risk_amount=300)
    b = _preview(client, risk_amount=600)
    assert a["sizing"]["volume"] == pytest.approx(1.00)
    assert b["sizing"]["volume"] == pytest.approx(2.00)
    assert b["sizing"]["volume"] == pytest.approx(2 * a["sizing"]["volume"], rel=1e-6)
    assert b["mode"] == "risk_to_lot"


# ===========================================================================
# §8 B — lot -> amount (the reverse direction uses the same broker maths)
# ===========================================================================
def test_05_editing_the_lot_updates_the_money_at_risk_immediately(client, bridge):
    small = _preview(client, volume=0.10)
    big = _preview(client, volume=0.20)
    assert small["mode"] == "lot_to_risk" and big["mode"] == "lot_to_risk"
    assert small["sizing"]["actual_risk"] == pytest.approx(30.0)       # 0.10 lot x 300/lot
    assert big["sizing"]["actual_risk"] == pytest.approx(60.0)
    assert big["sizing"]["actual_risk"] == pytest.approx(2 * small["sizing"]["actual_risk"], rel=1e-6)


def test_06_the_two_directions_agree_round_trip(client, bridge):
    r2l = _preview(client, risk_amount=300.30)
    volume = r2l["sizing"]["volume"]
    l2r = _preview(client, volume=volume)
    # the same broker maths, both ways: the money the panel shows back is the money asked for
    # (within one broker step, because a lot is rounded DOWN to the step)
    assert l2r["sizing"]["actual_risk"] == pytest.approx(l2r["sizing"]["risk_per_lot"] * volume, rel=1e-9)
    step_risk = l2r["sizing"]["risk_per_lot"] * 0.01
    assert abs(l2r["sizing"]["actual_risk"] - 300.30) <= step_risk


# ===========================================================================
# §8 C — the stop distance drives the dependent values
# ===========================================================================
def test_07_a_wider_stop_shrinks_the_lot_for_the_same_money(client, bridge):
    near = _preview(client, sl_pips=300, risk_amount=300)
    far = _preview(client, sl_pips=500, risk_amount=300)
    assert near["sizing"]["volume"] == pytest.approx(1.00)
    assert far["sizing"]["risk_per_lot"] > near["sizing"]["risk_per_lot"]
    assert far["sizing"]["volume"] == pytest.approx(near["sizing"]["volume"] * 300 / 500, rel=1e-6)
    assert far["levels"]["sl"] == pytest.approx(2400.3 - 500 * PIP)


def test_08_a_wider_stop_raises_the_risk_for_a_fixed_lot(client, bridge):
    near = _preview(client, volume=0.10, sl_pips=300)
    far = _preview(client, volume=0.10, sl_pips=500)
    assert far["sizing"]["actual_risk"] == pytest.approx(near["sizing"]["actual_risk"] * 500 / 300, rel=1e-6)


def test_09_reward_risk_follows_the_target_distance(client, bridge):
    out = _preview(client, sl_pips=300, tp_pips=600, risk_amount=300)
    assert out["levels"]["tp"] == pytest.approx(2400.3 + 600 * PIP)
    assert out["estimate"]["reward_risk"] == pytest.approx(2.0, rel=1e-3)
    assert out["estimate"]["loss_at_sl"] is not None and out["estimate"]["profit_at_tp"] is not None


# ===========================================================================
# §9 — the broker specification is the source of truth (nothing duplicated)
# ===========================================================================
def test_10_a_different_broker_spec_changes_every_derived_number(client, monkeypatch):
    from app import mt5
    # same sizes, different broker: 0.05 step and a 2.5 tick value
    odd = _v48._Bridge(spec=_v48._Spec(digits=2, point=0.01, trade_tick_size=0.01,
                                       trade_tick_value=2.5, volume_min=0.05, volume_step=0.05),
                       quote=_v48._Quote(bid=2400.0, ask=2400.3))
    monkeypatch.setattr(mt5, "get_bridge", lambda: odd)
    out = _preview(client, risk_amount=750)
    assert out["symbol_info"]["volume_step"] == 0.05
    assert out["sizing"]["risk_per_lot"] == pytest.approx(750.0)        # 300 * 2.5
    assert out["sizing"]["volume"] == pytest.approx(1.00)
    # an exact broker step (float-modulo on 0.05 is not exact at 1.0 lots)
    assert abs(out["sizing"]["volume"] / 0.05 - round(out["sizing"]["volume"] / 0.05)) < 1e-9


def test_11_the_panel_receives_the_spec_it_must_display(client, bridge):
    out = _preview(client)
    si = out["symbol_info"]
    for key in ("digits", "point", "volume_min", "volume_max", "volume_step",
                "tick_size", "tick_value", "source"):
        assert key in si, f"the panel cannot show {key} without it"
    assert si["tick_value"] == 1.0 and si["tick_size"] == 0.01


# ===========================================================================
# §11 — unavailable market data: no fake quote is ever used
# ===========================================================================
def test_12_without_a_quote_nothing_is_invented_and_the_reason_is_returned(client, monkeypatch):
    from app import mt5
    blind = _v48._Bridge(spec=_v48._Spec(), quote=None)
    blind._quote = None
    monkeypatch.setattr(mt5, "get_bridge", lambda: blind)
    out = _preview(client)
    assert out["ok"] is False
    assert out["blocked"]["code"] in ("INVALID_ENTRY_PRICE", "MT5_UNAVAILABLE")
    assert out["levels"]["entry"] is None                     # no fabricated price
    assert out["sizing"] is None
    assert ("quote" in out and out["quote"] is None) or out.get("quote") is None
    assert out["orders_placed"] is False
    # the bridge really was asked — the refusal is a measurement, not an assumption
    assert ("quote", SYMBOL) in blind.calls


def test_13_a_typed_entry_is_used_when_the_market_is_silent(client, monkeypatch):
    from app import mt5
    blind = _v48._Bridge(spec=_v48._Spec(), quote=None)
    blind._quote = None
    monkeypatch.setattr(mt5, "get_bridge", lambda: blind)
    out = _preview(client, entry=2400.5, risk_amount=300)
    assert out["ok"] is True
    assert out["levels"]["entry"] == pytest.approx(2400.5)      # the operator's price, not a quote
    assert out["quote"] is None                                 # the market gave nothing
    assert out["sizing"]["volume"] == pytest.approx(1.00)


def test_14_a_wrong_side_stop_is_flagged_not_silently_used(client, bridge):
    out = _preview(client, side="SELL", sl_pips=300, tp_pips=600)
    # SELL stop must sit ABOVE the bid; the computed level is on the correct side
    assert out["levels"]["sl"] > out["levels"]["entry"]
    bad = _preview(client, side="BUY", sl=2400.3 + 5.0)      # a stop above a BUY entry
    assert any("wrong side" in w for w in bad["levels"]["warnings"])


# ===========================================================================
# §10 — the Recalculate button performs the same valid calculation (no dead button)
# ===========================================================================
def test_15_the_manual_refresh_is_the_same_valid_calculation(client, bridge):
    auto = _preview(client, risk_amount=10, sl_pips=300)
    manual = _preview(client, risk_amount=10, sl_pips=300)   # exactly what the button sends
    assert auto == manual, "a manual refresh must return the identical, valid calculation"
    assert auto["orders_placed"] is False                    # and it never places anything


def test_16_recalculating_never_places_an_order_and_never_changes_data(client, bridge):
    from app.mt5.execution import recent_orders
    before = len(recent_orders(50))
    for _ in range(3):
        _preview(client)
    assert len(recent_orders(50)) == before
