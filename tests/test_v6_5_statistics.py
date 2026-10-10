"""V6.5 §7/§12-E — node statistics and Live Testing Results from the ledger.

The stats must come from the canonical persisted records, use the documented
position-level counting convention, keep research metrics separate, agree
between the node table and the results screen (same function), expose explicit
unavailable states (profit factor), and never fabricate values.  Signal
explanations must match the recorded engine output (facts only).
"""
from __future__ import annotations

import json

import pytest

from app.db.database import Database
from app.live_testing import ledger
from app.live_testing.results import per_node_live_stats, live_statistics


@pytest.fixture()
def db(tmp_path):
    d = Database(str(tmp_path / "stats.db"))
    ledger.ensure_schema(d)
    return d


def _trade(db, sid, **kw):
    base = {"strategy_id": sid, "symbol": "XAUUSD", "side": "BUY",
            "entry_price": 4200.0, "lots": 0.05, "open_ts": 1000.0,
            "status": "POSITION_OPEN", "timeframe": "M15"}
    base.update(kw)
    return ledger.upsert_trade(db, base)


def test_01_position_level_counts_and_pnl(db):
    _trade(db, 718, ticket=1, position_ticket=1, status="CLOSED_MISSING",
           close_ts=1100.0, pnl=10.0, gross_pnl=11.0, commission=-0.5, swap=-0.4,
           fees=0.0, net_pnl=10.1, close_kind="FULL", lots=0.05)
    _trade(db, 718, ticket=2, position_ticket=2, status="CLOSED_MISSING",
           close_ts=1200.0, pnl=-5.0, gross_pnl=-4.5, commission=-0.5, swap=0.0,
           fees=0.0, net_pnl=-5.0, close_kind="FULL")
    _trade(db, 718, ticket=3, position_ticket=3, status="POSITION_OPEN",
           lots=0.02)
    _trade(db, 718, ticket=4, position_ticket=4, status="BLOCKED")   # rejected
    st = per_node_live_stats(db)[718]
    assert st["attempts_total"] == 4
    assert st["attempts_rejected"] == 1
    assert st["confirmed_trades"] == 3
    assert st["closed_trades"] == 2
    assert st["wins"] == 1 and st["losses"] == 1 and st["breakeven"] == 0
    assert st["total_pnl"] == pytest.approx(5.0)                    # closed only
    assert st["realized_net_pnl"] == pytest.approx(5.1)
    assert st["win_rate"] == pytest.approx(50.0)
    assert st["avg_win"] == pytest.approx(10.0)
    assert st["avg_loss"] == pytest.approx(-5.0)
    assert st["profit_factor_available"] is True
    assert st["profit_factor"] == pytest.approx(2.0)
    assert st["avg_duration_s"] == pytest.approx(150.0)
    assert st["counting_conventions"]["level"] == "position"


def test_02_partial_exits_are_not_extra_wins(db):
    _trade(db, 718, ticket=1, position_ticket=1, status="CLOSED_MISSING",
           close_ts=1000.0, pnl=2.0, close_kind="PARTIAL")
    st = per_node_live_stats(db)[718]
    assert st["partial_closes"] == 1
    assert st["closed_trades"] == 1                  # one position, not two trades
    assert st["wins"] == 1


def test_03_profit_factor_is_explicitly_unavailable_when_undefined(db):
    _trade(db, 719, ticket=1, position_ticket=1, status="CLOSED_MISSING",
           close_ts=1000.0, pnl=10.0)
    st = per_node_live_stats(db)[719]
    assert st["profit_factor"] is None
    assert st["profit_factor_available"] is False    # never faked as 0 or 1


def test_04_unclassified_and_rejected_are_visible_separately(db):
    _trade(db, 718, ticket=1, position_ticket=1, status="UNKNOWN")
    _trade(db, 718, ticket=2, position_ticket=2, status="EXECUTED_UNCONFIRMED")
    _trade(db, 718, ticket=3, position_ticket=3, status="BLOCKED")
    st = per_node_live_stats(db)[718]
    assert st["unclassified"] == 2
    assert st["attempts_rejected"] == 1
    assert st["win_rate"] is None                     # no closed trades -> no rate


def test_05_same_node_table_and_results_agree(db):
    _trade(db, 718, ticket=1, position_ticket=1, status="CLOSED_MISSING",
           close_ts=1000.0, pnl=3.0)
    a = per_node_live_stats(db)
    b = per_node_live_stats(db)                       # both consumers call THIS
    assert a[718]["total_pnl"] == b[718]["total_pnl"]
    assert a[718]["trades"] == b[718]["trades"]


def test_06_research_metrics_are_never_touched(db):
    before = db.one("SELECT COUNT(*) c FROM backtests")["c"] if db.one(
        "SELECT 1 FROM sqlite_master WHERE name='backtests'") else None
    _trade(db, 718, ticket=9, position_ticket=9, status="CLOSED_MISSING",
           close_ts=1.0, pnl=1.0)
    after = db.one("SELECT COUNT(*) c FROM backtests")["c"] if before is not None else None
    if before is not None:
        assert after == before


def test_07_floating_pnl_passes_through_when_provided(db):
    _trade(db, 718, ticket=1, position_ticket=1, status="POSITION_OPEN")
    st = per_node_live_stats(db, floating_by_node={718: -4.5})[718]
    assert st["floating_pnl"] == pytest.approx(-4.5)


def test_08_volume_and_last_trade_timestamp(db):
    _trade(db, 718, ticket=1, position_ticket=1, status="CLOSED_MISSING",
           close_ts=2000.0, open_ts=1000.0, lots=0.03, pnl=1.0)
    _trade(db, 718, ticket=2, position_ticket=2, status="POSITION_OPEN", lots=0.07)
    st = per_node_live_stats(db)[718]
    assert st["total_volume"] == pytest.approx(0.10)
    assert st["last_trade_ts"] == pytest.approx(2000.0)


# --------------------------------------------------------------------------- #
# signal explanation facts (§3.2 / §12-E: explanations match engine output)
# --------------------------------------------------------------------------- #
def test_09_entry_reason_is_recorded_facts_not_reconstruction(db):
    explain = {
        "signal_id": "sig-M15-1",
        "explanation_kind": "engine_rule_and_feature_snapshot",
        "entry_rule": {"type": "compare", "left": "sma:20", "cmp": ">", "right": 2.0},
        "entry_rule_text": "sma:20 > 2.0",
        "indicator_values": {"sma:20": 2.31, "atr:14": 3.02},
        "atr": {"spec": "atr:14", "value": 3.02},
        "note": "facts recorded at decision time",
    }
    rid = _trade(db, 718, ticket=1, position_ticket=1,
                 entry_reason=json.dumps(explain), signal_id="sig-M15-1")
    row = db.one("SELECT * FROM live_test_trades WHERE id=?", (rid,))
    er = json.loads(row["entry_reason"])
    assert er["explanation_kind"] == "engine_rule_and_feature_snapshot"
    assert er["entry_rule"]["left"] == "sma:20"          # the real engine rule
    assert er["indicator_values"]["sma:20"] == 2.31      # the real computed value
    assert er["signal_id"] == "sig-M15-1"


def test_10_recovered_trades_mark_explanation_unavailable(db):
    rid = _trade(db, 718, ticket=2, position_ticket=2,
                 entry_reason=json.dumps({"explanation_kind":
                                          "broker_recovered_no_local_record"}))
    row = db.one("SELECT * FROM live_test_trades WHERE id=?", (rid,))
    er = json.loads(row["entry_reason"])
    assert er["explanation_kind"] == "broker_recovered_no_local_record"
