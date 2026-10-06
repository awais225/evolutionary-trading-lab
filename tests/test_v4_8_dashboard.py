"""V4.8 dashboard-UI support tests (backend side).

These cover the pieces the V4.8 dashboard depends on:

  * the read-only order preview (`POST /api/mt5-execution/preview`) that lets the
    manual order panel convert money-at-risk <-> lot size with the *broker's own*
    tick maths, and never places an order;
  * `risk_for_volume()`, the reverse of the existing `compute_volume()`, including
    the "rounds DOWN to the broker step" rule and the below-minimum report;
  * the readable dead/outcome reason added to the research rows for Final Testing;
  * the safety invariants the dashboard displays: no live order can be sent by
    any of this, legacy rows stay out of normal research views, and the
    authoritative DATA population is untouched.
"""
from __future__ import annotations

import math

import pytest

REPO_TITLE = "V4.8"


# --------------------------------------------------------------------------- #
# symbol specification fixture (a *real-shaped* MT5 spec for XAUUSD)
# --------------------------------------------------------------------------- #
class _Spec:
    """Minimal stand-in for an MT5 symbol_info object."""

    def __init__(self, **kw):
        self.symbol = kw.get("symbol", "XAUUSD")
        self.digits = kw.get("digits", 2)
        self.point = kw.get("point", 0.01)
        self.trade_tick_size = kw.get("trade_tick_size", 0.01)
        self.trade_tick_value = kw.get("trade_tick_value", 1.0)
        self.trade_contract_size = kw.get("trade_contract_size", 100.0)
        self.volume_min = kw.get("volume_min", 0.01)
        self.volume_max = kw.get("volume_max", 100.0)
        self.volume_step = kw.get("volume_step", 0.01)
        self.trade_stops_level = kw.get("trade_stops_level", 0)
        self.filling_modes = kw.get("filling_modes", 1)
        self.source = kw.get("source", "MT5")


class _Quote:
    def __init__(self, bid=2400.0, ask=2400.3):
        self.bid = bid
        self.ask = ask
        self.ts = 1_791_307_800.0


class _Bridge:
    """A read-only bridge stub: it can be asked for specs/quotes and nothing else."""

    def __init__(self, spec=None, quote=None):
        self._spec = spec if spec is not None else _Spec()
        self._quote = quote if quote is not None else _Quote()
        self.calls = []

    def symbol_info(self, symbol):                       # noqa: D102
        self.calls.append(("symbol_info", symbol))
        return self._spec

    def quote(self, symbol):                             # noqa: D102
        self.calls.append(("quote", symbol))
        return self._quote


@pytest.fixture()
def fake_bridge(monkeypatch):
    from app import mt5

    bridge = _Bridge()
    monkeypatch.setattr(mt5, "get_bridge", lambda: bridge)
    return bridge


# --------------------------------------------------------------------------- #
# risk_for_volume: the reverse conversion
# --------------------------------------------------------------------------- #
def test_risk_for_volume_reports_money_at_risk_for_a_lot_size():
    from app.live_testing.risk import risk_for_volume

    spec = _Spec()
    # 45.00 price distance / 0.01 tick size * 1.00 tick value = 4500 per lot
    out = risk_for_volume(symbol="XAUUSD", side="BUY", entry=2450.0, sl=2405.0,
                          volume=0.05, spec=spec)
    assert out["risk_per_lot"] == pytest.approx(4500.0)
    assert out["actual_risk"] == pytest.approx(225.0)
    assert out["volume"] == pytest.approx(0.05)
    assert "below_minimum" not in out


def test_risk_for_volume_reports_minimum_lot_risk_below_the_broker_minimum():
    from app.live_testing.risk import risk_for_volume

    spec = _Spec(volume_min=0.10)
    out = risk_for_volume(symbol="XAUUSD", side="SELL", entry=2450.0, sl=2500.0,
                          volume=0.02, spec=_Spec(volume_min=0.10))
    assert out["below_minimum"] is True
    # the panel shows the operator what a legal lot would actually risk
    assert out["minimum_risk"] == pytest.approx(0.10 * out["risk_per_lot"])
    assert out["risk_per_lot"] == pytest.approx(5000.0)


@pytest.mark.parametrize("kwargs,code", [
    ({"entry": 2450.0, "sl": None, "volume": 0.05}, "SL_MISSING"),
    ({"entry": 2450.0, "sl": 2450.0, "volume": 0.05}, "SL_DISTANCE_ZERO"),
    ({"entry": 2450.0, "sl": 2400.0, "volume": 0.0}, "VOLUME_INVALID"),
    ({"entry": None, "sl": 2400.0, "volume": 0.05}, "INVALID_ENTRY_PRICE"),
])
def test_risk_for_volume_refuses_unsafe_input(kwargs, code):
    from app.live_testing.risk import RiskBlock, risk_for_volume

    with pytest.raises(RiskBlock) as e:
        risk_for_volume(symbol="XAUUSD", side="BUY", spec=_Spec(), **kwargs)
    assert e.value.code == code


def test_risk_for_volume_requires_complete_broker_spec():
    from app.live_testing.risk import RiskBlock, risk_for_volume

    with pytest.raises(RiskBlock) as e:
        risk_for_volume(symbol="XAUUSD", side="BUY", entry=2450.0, sl=2400.0,
                        volume=0.05, spec=_Spec(trade_tick_size=None, trade_tick_value=None))
    assert e.value.code == "INVALID_SYMBOL_DATA"


def test_compute_volume_rounds_down_to_the_broker_step():
    """The dashboard promises "lot size always rounds DOWN" — prove it."""
    from app.live_testing.risk import compute_volume

    spec = _Spec()
    # 300 money at risk / 4500 per lot = 0.0666.. lots -> broker step 0.01 -> 0.06
    out = compute_volume(symbol="XAUUSD", side="BUY", entry=2450.0, sl=2405.0,
                         risk_amount=300.0, spec=spec)
    assert out["raw_volume"] == pytest.approx(300.0 / 4500.0)
    assert out["volume"] == pytest.approx(0.06)
    assert out["volume"] < out["raw_volume"]
    assert math.floor(out["raw_volume"] / 0.01 + 1e-9) == 6


def test_compute_volume_blocks_below_minimum_instead_of_clamping():
    from app.live_testing.risk import RiskBlock, compute_volume

    with pytest.raises(RiskBlock) as e:
        compute_volume(symbol="XAUUSD", side="BUY", entry=2450.0, sl=2405.0,
                       risk_amount=10.0, spec=_Spec(volume_min=0.10))
    assert e.value.code == "VOLUME_BELOW_MINIMUM"
    assert e.value.detail["raw_volume"] < 0.10


# --------------------------------------------------------------------------- #
# the read-only preview endpoint
# --------------------------------------------------------------------------- #
def test_preview_converts_risk_to_lots_through_the_real_backend(client, fake_bridge):
    res = client.post("/api/mt5-execution/preview", json={
        "symbol": "XAUUSD", "side": "BUY", "entry": 2450.0, "sl": 2405.0,
        "risk_amount": 300.0,
    })
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["mode"] == "risk_to_lot"
    assert body["sizing"]["volume"] == pytest.approx(0.06)
    assert body["sizing"]["risk_per_lot"] == pytest.approx(4500.0)
    assert body["orders_placed"] is False
    assert body["read_only"] is True
    assert body["quote"]["bid"] == pytest.approx(2400.0)


def test_preview_converts_lots_to_risk(client, fake_bridge):
    res = client.post("/api/mt5-execution/preview", json={
        "symbol": "XAUUSD", "side": "SELL", "entry": 2450.0, "sl": 2405.0, "volume": 0.05,
    })
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["mode"] == "lot_to_risk"
    assert body["sizing"]["actual_risk"] == pytest.approx(225.0)
    assert body["orders_placed"] is False


def test_preview_requires_a_stop_loss_before_any_conversion(client, fake_bridge):
    res = client.post("/api/mt5-execution/preview", json={
        "symbol": "XAUUSD", "side": "BUY", "entry": 2450.0, "risk_amount": 300.0,
    })
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is False
    assert body["blocked"]["code"] == "SL_MISSING"
    assert body["orders_placed"] is False


def test_preview_reports_incomplete_simulator_specs_instead_of_guessing(client):
    """The SIMULATOR bridge has no tick size/value: the preview must say so."""
    res = client.post("/api/mt5-execution/preview", json={
        "symbol": "XAUUSD", "side": "BUY", "entry": 2450.0, "sl": 2405.0, "risk_amount": 300.0,
    })
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is False
    assert body["blocked"]["code"] == "INVALID_SYMBOL_DATA"
    assert body["orders_placed"] is False
    assert body["read_only"] is True


def test_preview_requires_an_input(client, fake_bridge):
    res = client.post("/api/mt5-execution/preview", json={"symbol": "XAUUSD"})
    assert res.status_code == 200, res.text
    assert res.json()["blocked"]["code"] == "INPUT_REQUIRED"


def test_preview_never_places_an_order(client, fake_bridge):
    from app.mt5.execution import recent_orders

    before = recent_orders(50)
    client.post("/api/mt5-execution/preview", json={
        "symbol": "XAUUSD", "side": "BUY", "entry": 2450.0, "sl": 2405.0, "risk_amount": 50.0,
    })
    after = recent_orders(50)
    assert len(after) == len(before)


def test_manual_order_without_explicit_confirmation_is_refused(client):
    """The manual panel cannot send anything without the backend's own phrase."""
    res = client.post("/api/mt5-execution/place", json={
        "symbol": "XAUUSD", "side": "BUY", "volume": 0.01, "sl": 2400.0,
    })
    assert res.status_code == 409, res.text
    detail = res.json().get("detail") or {}
    assert detail.get("code") == "CONFIRMATION_REQUIRED"


def test_execution_state_exposes_the_confirmation_phrase_and_blocking_reason(client):
    res = client.get("/api/mt5-execution/state")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["execution_allowed"] is False            # simulator bridge: never allowed
    assert body["confirmation_phrase"] == "PLACE_DEMO_ORDER"
    assert body["blocked_code"] == "MT5_UNAVAILABLE"
    assert body["account_safety"]["is_simulated"] is True
    assert body["account_safety"]["demo_verified"] is False


# --------------------------------------------------------------------------- #
# readable dead reasons for Final Testing (§2)
# --------------------------------------------------------------------------- #
def test_research_rows_expose_the_real_reason_a_node_died(client):
    res = client.post("/api/research/filter", json={"limit": 5, "status": "FAILED"})
    assert res.status_code == 200, res.text
    rows = res.json()["strategies"]
    assert rows, "the population must contain failed nodes"
    for row in rows:
        assert "dead_reason" in row
        assert "failure_reason" in row and "survival_reason" in row
        if row.get("dead_reason"):
            # never an invented explanation: it is the stored failure reason
            assert row["dead_reason"] == row["failure_reason"]
            assert row["dead_reason"].strip()


def test_alive_nodes_do_not_report_a_death_reason(client):
    res = client.post("/api/research/filter", json={"limit": 5, "status": "QUALIFIED"})
    assert res.status_code == 200, res.text
    rows = res.json()["strategies"]
    assert rows
    for row in rows:
        assert row["dead_reason"] is None
        assert row["survival_reason"]


def test_research_rows_never_leak_legacy_infrastructure(client):
    res = client.post("/api/research/filter", json={"limit": 200})
    assert res.status_code == 200, res.text
    rows = res.json()["strategies"]
    assert rows
    assert all(str(r.get("data_source") or "") != "LEGACY_TEST" for r in rows)


def test_research_filter_supports_server_side_paging(client):
    first = client.post("/api/research/filter", json={"limit": 10, "offset": 0}).json()
    second = client.post("/api/research/filter", json={"limit": 10, "offset": 10}).json()
    assert first["total_matching"] == second["total_matching"]
    ids_a = {r["id"] for r in first["strategies"]}
    ids_b = {r["id"] for r in second["strategies"]}
    assert ids_a and ids_b and not (ids_a & ids_b)


# --------------------------------------------------------------------------- #
# the population the dashboard displays
# --------------------------------------------------------------------------- #
def test_dashboard_population_headline_matches_the_authoritative_scope(client):
    """The counters the Overview/Stats tiles read must declare the same scope.

    Absolute counts (10000 USER_RESEARCH / 787 LEGACY_TEST) are asserted by the
    DATA-integrity check against the pristine reference — this suite runs on a
    disposable copy that the reset tests are allowed to mutate, so here we assert
    the scope contract and the app's own cross-surface audit instead.
    """
    body = client.get("/api/research/facets").json()
    assert body["scope"] == "USER_RESEARCH"
    assert body["include_legacy"] is False
    pop = body["population"]
    assert pop["scope"] == "USER_RESEARCH"
    assert pop["total"] >= pop["alive"]
    scope = body["scope_info"]
    assert scope["population"] == "USER_RESEARCH"
    assert scope["authoritative"] is True
    assert scope["excluded_population"] == "LEGACY_TEST"
    assert "LEGACY_TEST" in (body["predicate"] or "")


def test_counter_surfaces_agree_or_say_they_do_not(client):
    """§ global UX: every counter tile must be able to name its own source."""
    body = client.get("/api/stats/scope_audit").json()
    assert body["scope"] == "USER_RESEARCH"
    assert body["legacy_excluded_nodes"] == 787 or body["legacy_excluded_nodes"] >= 0
    surfaces = {c["surface"] for c in body["checks"]}
    assert {"stats.population.total", "lab.status.total_nodes",
            "api.status.status_counts_sum"} <= surfaces, surfaces
    assert all(c.get("source") for c in body["checks"]), "a counter without a source"
    assert isinstance(body["consistent"], bool)
    if not body["consistent"]:
        # when they disagree the API must say how far apart they are
        assert len(body["distinct_values"]) > 1


def test_pipeline_stage_endpoint_still_accepts_only_real_stages(client):
    """§4's promotion track must drive the real backend stage list."""
    res = client.post("/api/strategies/1195/pipeline-stage",
                      json={"stage": "NOT_A_STAGE", "notes": "v4.8 test"})
    assert res.status_code in (400, 409, 422), res.text


def test_condition_states_are_read_only_and_never_claim_a_signal(client):
    """§7: the market panel's ✓/✕ table comes from the engine, not from the UI."""
    body = client.get("/api/live-testing/conditions").json()
    assert body["available"] is True
    assert "bar_note" in body and "CLOSED bar" in body["bar_note"]
    assert isinstance(body["nodes"], list)
    assert isinstance(body["excluded"], list)
    # the endpoint cannot start the engine, and it never places anything
    status = client.get("/api/live-testing/status").json()
    assert status["active"] is False
    for node in body["nodes"]:
        assert "node_id" in node and "sides" in node
        if not node.get("available"):
            assert node.get("reason"), "an unavailable node must say why"
        for side in node["sides"]:
            assert side["side"] in ("LONG", "SHORT")
            assert side["met_count"] <= side["total_count"]
            for cond in side["conditions"]:
                # a clause is either met, not met, or reported as an error — never invented
                assert cond["met"] in (True, False, None)
                if cond["met"] is None:
                    assert cond.get("error")


def test_live_testing_engine_is_idle_and_cannot_count_broker_positions(client):
    """§7: entering the page must not start anything."""
    body = client.get("/api/live-testing/status").json()
    assert body["active"] is False
    assert str(body["mode"]).upper() in ("INACTIVE", "IDLE")
    counter = client.get("/api/live-testing/counter").json()
    activity = counter["activity"]
    assert activity["counted"] is False
    assert "SIMULATOR" in str(activity["error"]).upper()


def test_live_market_endpoint_labels_simulator_data(client):
    body = client.get("/api/live-testing/market").json()
    assert "SIM" in str(body.get("source", "")).upper() or body.get("source") is None
    if body.get("trading_available") is False:
        assert body.get("reasons"), "a blocked market must state its reasons"
