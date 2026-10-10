"""V6.5.1 §8/§11 — the two risk modes and position sizing.

Deterministic pure-calculation tests (no bridge, no orders): Mode A (percent of
the explicitly named capital basis) and Mode B (constant monetary risk), volume
normalization to the broker's step (never rounded up past the limit), rejection
of invalid/missing stop distances and instrument metadata, config validation
and persistence, and per-node isolation of the saved mode.

Documented calculation assumptions (also in live_testing/risk.py): the stop is
assumed to fill at its price (no gap/slippage/swap/commission modelling), and no
account-currency conversion is applied to the instrument's tick value.
"""
from __future__ import annotations

import pytest

from app.live_testing.risk import (
    RiskBlock,
    compute_risk,
    compute_volume,
    resolve_risk_mode,
    validate_risk_settings,
)


class Spec:
    """A generic broker symbol spec (object form, as bridge.symbol_info returns)."""

    def __init__(self, tick_size=0.01, tick_value=1.0, vmin=0.01, vmax=100.0,
                 vstep=0.01, digits=2, contract=100.0):
        self.trade_tick_size = tick_size
        self.trade_tick_value = tick_value
        self.volume_min = vmin
        self.volume_max = vmax
        self.volume_step = vstep
        self.digits = digits
        self.trade_contract_size = contract


# --------------------------------------------------------------------------- #
# Mode A — percentage of capital
# --------------------------------------------------------------------------- #
def test_01_mode_a_equity_is_the_default_and_unchanged():
    t = compute_risk(node_id=1, strategy_id=1, symbol="XAUUSD", side="BUY",
                     entry=2400.0, sl=2398.0, equity=10000.0,
                     global_pct=1.0, override_pct=None, spec=Spec())
    assert t["risk_mode"] == "PERCENT"
    assert t["capital_basis"] == "EQUITY"
    assert t["capital"] == 10000.0
    assert t["risk_amount"] == 100.0            # 1% of 10,000
    assert t["volume"] == 0.5                   # 100 / 200-per-lot
    assert t["risk_pct"] == 1.0


def test_02_mode_a_balance_basis_uses_balance_not_equity():
    t = compute_risk(node_id=1, strategy_id=1, symbol="XAUUSD", side="BUY",
                     entry=2400.0, sl=2398.0, equity=10000.0, balance=20000.0,
                     global_pct=1.0, override_pct=None, spec=Spec(),
                     capital_basis="BALANCE")
    assert t["capital_basis"] == "BALANCE"
    assert t["capital"] == 20000.0
    assert t["risk_amount"] == 200.0            # 1% of BALANCE (not equity)
    assert t["volume"] == 1.0


def test_03_mode_a_balance_basis_refuses_when_balance_unavailable():
    with pytest.raises(RiskBlock) as e:
        compute_risk(node_id=1, strategy_id=1, symbol="XAUUSD", side="BUY",
                     entry=2400.0, sl=2398.0, equity=10000.0, balance=None,
                     global_pct=1.0, override_pct=None, spec=Spec(),
                     capital_basis="BALANCE")
    assert e.value.code == "ACCOUNT_BALANCE_UNAVAILABLE"


def test_04_mode_a_node_override_still_wins_over_global():
    t = compute_risk(node_id=1, strategy_id=1, symbol="XAUUSD", side="BUY",
                     entry=2400.0, sl=2398.0, equity=10000.0,
                     global_pct=1.0, override_pct=2.0, spec=Spec())
    assert t["risk_pct"] == 2.0
    assert t["risk_pct_source"] == "node_override"
    assert t["risk_amount"] == 200.0


# --------------------------------------------------------------------------- #
# Mode B — constant monetary risk
# --------------------------------------------------------------------------- #
def test_05_mode_b_sizes_from_the_money_amount_only():
    t = compute_risk(node_id=1, strategy_id=1, symbol="XAUUSD", side="BUY",
                     entry=2400.0, sl=2398.0, equity=10000.0,
                     global_pct=1.0, override_pct=None, spec=Spec(),
                     risk_mode="AMOUNT", risk_amount=50.0)
    assert t["risk_mode"] == "AMOUNT"
    assert t["risk_amount"] == 50.0
    assert t["volume"] == 0.25                  # 50 / 200-per-lot
    assert t["risk_pct"] is None                # pct does not configure this mode
    assert t["risk_pct_configured"] == 1.0      # still visible for the trace


def test_06_mode_b_requires_a_positive_amount():
    for bad in (None, 0, -5, "xx"):
        with pytest.raises(RiskBlock) as e:
            compute_risk(node_id=1, strategy_id=1, symbol="XAUUSD", side="BUY",
                         entry=2400.0, sl=2398.0, equity=10000.0,
                         global_pct=1.0, override_pct=None, spec=Spec(),
                         risk_mode="AMOUNT", risk_amount=bad)
        assert e.value.code in ("RISK_AMOUNT_INVALID", "RISK_AMOUNT_ABOVE_MAXIMUM")


def test_07_mode_b_amount_above_configured_maximum_is_blocked_not_clamped():
    from app.live_testing.risk import risk_limits
    cap = risk_limits()["risk_amount_max"]
    with pytest.raises(RiskBlock) as e:
        compute_risk(node_id=1, strategy_id=1, symbol="XAUUSD", side="BUY",
                     entry=2400.0, sl=2398.0, equity=10000.0,
                     global_pct=1.0, override_pct=None, spec=Spec(),
                     risk_mode="AMOUNT", risk_amount=cap + 1)
    assert e.value.code == "RISK_AMOUNT_ABOVE_MAXIMUM"


def test_08_invalid_mode_and_basis_are_refused_never_coerced():
    with pytest.raises(RiskBlock) as e:
        compute_risk(node_id=1, strategy_id=1, symbol="XAUUSD", side="BUY",
                     entry=2400.0, sl=2398.0, equity=10000.0,
                     global_pct=1.0, override_pct=None, spec=Spec(),
                     risk_mode="LOTS")
    assert e.value.code == "RISK_MODE_INVALID"
    with pytest.raises(RiskBlock) as e:
        compute_risk(node_id=1, strategy_id=1, symbol="XAUUSD", side="BUY",
                     entry=2400.0, sl=2398.0, equity=10000.0,
                     global_pct=1.0, override_pct=None, spec=Spec(),
                     capital_basis="FREE MARGIN")
    assert e.value.code == "RISK_CAPITAL_BASIS_INVALID"
    assert resolve_risk_mode({})["risk_mode"] == "PERCENT"
    assert resolve_risk_mode({})["capital_basis"] == "EQUITY"


# --------------------------------------------------------------------------- #
# volume normalization and instrument metadata
# --------------------------------------------------------------------------- #
def test_09_volume_rounds_down_to_the_step_and_never_exceeds_the_risk():
    # risk 100; risk_per_lot = (3.0 / 0.5) * 2.5 = 15 -> raw 6.6666; step 0.25
    spec = Spec(tick_size=0.5, tick_value=2.5, vmin=0.25, vmax=100.0, vstep=0.25)
    out = compute_volume(symbol="TEST", side="BUY", entry=100.0, sl=97.0,
                         risk_amount=100.0, spec=spec)
    assert out["volume"] == 6.5                 # 6.75 would EXCEED 100; 6.5 <= 100
    assert out["actual_risk"] <= 100.0 + 1e-9
    assert out["volume"] <= out["raw_volume"] + 1e-9


def test_10_uneven_step_normalizes_down_and_respects_min():
    spec = Spec(tick_size=0.01, tick_value=1.0, vmin=0.01, vmax=10.0, vstep=0.03)
    out = compute_volume(symbol="TEST", side="SELL", entry=2400.0, sl=2402.0,
                         risk_amount=30.0, spec=spec)   # raw = 30/200 = 0.15
    assert abs(out["volume"] / 0.03 - round(out["volume"] / 0.03)) < 1e-6
    assert out["volume"] <= 0.15 + 1e-9
    assert out["actual_risk"] <= 30.0 + 1e-6


def test_11_worst_case_stop_loss_risk_does_not_exceed_the_request():
    # representative instrument: different tick sizes/values/steps
    for spec, dist, amount in (
        (Spec(tick_size=0.01, tick_value=1.0, vstep=0.01), 2.0, 100.0),
        (Spec(tick_size=0.00001, tick_value=0.1, vmin=0.01, vstep=0.01), 0.0015, 25.0),
        (Spec(tick_size=0.25, tick_value=12.5, vmin=0.01, vstep=0.01), 7.5, 500.0),
    ):
        out = compute_volume(symbol="TEST", side="BUY", entry=100.0, sl=100.0 - dist,
                             risk_amount=amount, spec=spec)
        assert out["actual_risk"] <= amount + 1e-6, out


def test_12_missing_or_invalid_metadata_is_refused():
    with pytest.raises(RiskBlock) as e:
        compute_volume(symbol="TEST", side="BUY", entry=1.0, sl=0.9,
                       risk_amount=10.0, spec=None)
    assert e.value.code == "INVALID_SYMBOL_DATA"
    with pytest.raises(RiskBlock) as e:
        compute_volume(symbol="TEST", side="BUY", entry=1.0, sl=0.9,
                       risk_amount=10.0, spec=Spec(tick_size=0))
    assert e.value.code == "INVALID_SYMBOL_DATA"
    with pytest.raises(RiskBlock) as e:
        compute_volume(symbol="TEST", side="BUY", entry=1.0, sl=0.9,
                       risk_amount=10.0, spec=Spec(vmin=1.0, vmax=0.5))
    assert e.value.code == "INVALID_SYMBOL_DATA"


def test_13_invalid_or_zero_sl_distance_is_refused():
    for sl in (None, 0, -1, 2400.0):            # sl == entry -> zero distance
        with pytest.raises(RiskBlock) as e:
            compute_volume(symbol="TEST", side="BUY", entry=2400.0, sl=sl,
                           risk_amount=10.0, spec=Spec())
        assert e.value.code in ("SL_MISSING", "SL_DISTANCE_ZERO", "INVALID_ENTRY_PRICE")


def test_14_below_minimum_volume_is_refused_not_silently_raised():
    # risk 1 needs 0.005 lots; the broker minimum is 0.01 -> refuse
    with pytest.raises(RiskBlock) as e:
        compute_volume(symbol="TEST", side="BUY", entry=2400.0, sl=2398.0,
                       risk_amount=1.0, spec=Spec())
    assert e.value.code == "VOLUME_BELOW_MINIMUM"


# --------------------------------------------------------------------------- #
# config validation + persistence (the settings the Nodes table writes)
# --------------------------------------------------------------------------- #
def test_15_config_validation_refuses_bad_modes_and_accepts_good_ones():
    ok = validate_risk_settings(None, 1, risk_mode="AMOUNT", risk_amount=25.0)
    assert ok["ok"] is True
    bad = validate_risk_settings(None, 1, risk_mode="AMOUNT")
    assert bad["ok"] is False and bad["code"] == "RISK_AMOUNT_INVALID"
    bad = validate_risk_settings(None, 1, risk_mode="PERCENT", capital_basis="TELEPHONE")
    assert bad["ok"] is False and bad["code"] == "RISK_CAPITAL_BASIS_INVALID"
    from app.live_testing.risk import risk_limits
    over = validate_risk_settings(None, 1, risk_mode="AMOUNT",
                                  risk_amount=risk_limits()["risk_amount_max"] + 1)
    assert over["ok"] is False and over["code"] == "RISK_AMOUNT_ABOVE_MAXIMUM"
    # a payload with no mode fields keeps the historical pct-only validation
    assert validate_risk_settings(0.5, 1)["ok"] is True
    assert validate_risk_settings(-1, 1)["ok"] is False


def test_16_saved_mode_reaches_the_sizing_call_and_is_per_node(client):
    """The persisted settings must reach the intended execution configuration:
    read the config exactly as the engine does and feed compute_risk with it."""
    from app.api.routes import get_db
    r = client.get("/api/nodes", params={"filter": "all", "limit": 0})
    rows = [x for x in (r.json().get("nodes") or []) if int(x.get("node_id") or 0) > 0]
    if len(rows) < 2:
        pytest.skip("need two nodes in this DATA snapshot")
    sid_a = int(rows[0]["node_id"])
    sid_b = int(rows[1]["node_id"])

    r = client.post(f"/api/live-testing/nodes/{sid_a}/config",
                    json={"risk_mode": "AMOUNT", "risk_amount": 37.5})
    assert r.status_code == 200, r.text
    cfg_a = get_db().get_live_test_config(sid_a)
    assert cfg_a["risk_mode"] == "AMOUNT" and cfg_a["risk_amount"] == 37.5

    r = client.post(f"/api/live-testing/nodes/{sid_b}/config",
                    json={"risk_mode": "PERCENT", "risk_pct": 0.5,
                          "risk_capital_basis": "BALANCE"})
    assert r.status_code == 200, r.text
    cfg_b = get_db().get_live_test_config(sid_b)
    assert cfg_b["risk_mode"] == "PERCENT" and cfg_b["risk_capital_basis"] == "BALANCE"

    # node B's write must leave node A's risk settings exactly as they were
    cfg_a2 = get_db().get_live_test_config(sid_a)
    assert cfg_a2["risk_mode"] == cfg_a["risk_mode"]
    assert cfg_a2["risk_amount"] == cfg_a["risk_amount"]
    assert cfg_a2["risk_capital_basis"] == cfg_a["risk_capital_basis"]

    # exactly what engine.py passes to compute_risk:
    t = compute_risk(node_id=sid_a, strategy_id=sid_a, symbol="XAUUSD", side="BUY",
                     entry=2400.0, sl=2398.0, equity=10000.0, balance=10000.0,
                     global_pct=1.0, override_pct=cfg_a.get("risk_pct"), spec=Spec(),
                     risk_mode=cfg_a.get("risk_mode"),
                     risk_amount=cfg_a.get("risk_amount"),
                     capital_basis=cfg_a.get("risk_capital_basis"))
    assert t["risk_mode"] == "AMOUNT" and t["risk_amount"] == 37.5
    # 37.5 / 200-per-lot = 0.1875 raw -> rounded DOWN to the 0.01 step -> 0.18
    assert t["volume"] == 0.18
    assert t["sizing"]["actual_risk"] == 36.0 <= 37.5 + 1e-6

    # settings survive a config re-read (persistence)
    cfg_a2 = get_db().get_live_test_config(sid_a)
    assert cfg_a2["risk_mode"] == "AMOUNT" and cfg_a2["risk_amount"] == 37.5
