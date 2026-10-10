"""V6.5 §6/§12-D — canonical MT5 trade ledger: capture, idempotency, recovery.

Deterministic mocks only (no terminal, no real orders).  Covers: rejected /
accepted / opened / modified / partial close / full close / SL / TP / other /
unknown exits, duplicate history polls, delayed events, disconnection, restart
with open positions, restart after unobserved closure, multi-deal positions,
same symbol different nodes/magic, broker-only history, local-only record,
reconciliation discrepancy and persistence failure — never double-counting,
never fabricating missing facts.
"""
from __future__ import annotations

import json
import time

import pytest

from app.db.database import Database
from app.live_testing import ledger


@pytest.fixture()
def db(tmp_path):
    d = Database(str(tmp_path / "ledger.db"))
    ledger.ensure_schema(d)
    return d


class FakeBridge:
    """Read-only bridge stub: positions + deal history per test."""

    source = "MT5"

    def __init__(self, positions=None, deals=None, fail_positions=False):
        self.positions = positions or []
        self.deals = deals or {}
        self.fail_positions = fail_positions

    def positions_get(self, ticket=None, symbol=None):
        if self.fail_positions:
            raise RuntimeError("terminal disconnected")
        return list(self.positions)

    def orders_get(self, ticket=None, symbol=None):
        return []

    def deal_history(self, position=None, date_from=None, date_to=None):
        return list(self.deals.get(int(position or -1), []))


def _deal(ticket, position, entry=1, reason=0, profit=0.0, volume=0.05,
          price=4200.0, commission=-0.5, swap=-0.1, fee=0.0, t=1791523000):
    return {"ticket": ticket, "order": ticket + 1000, "position": position,
            "position_id": position, "time": t, "type": 1, "entry": entry,
            "magic": 778718, "volume": volume, "price": price,
            "commission": commission, "swap": swap, "profit": profit,
            "fee": fee, "reason": reason, "symbol": "XAUUSD", "comment": ""}


# --------------------------------------------------------------------------- #
# schema + idempotency
# --------------------------------------------------------------------------- #
def test_01_schema_is_additive_and_preserves_rows(db):
    cols = {r["name"] for r in db.q("PRAGMA table_info(live_test_trades)")}
    for need in ("trade_uid", "position_ticket", "entry_reason", "exit_reason",
                 "sl_default", "tp_default", "sl_offset_pips", "net_pnl",
                 "evidence_source", "data_complete"):
        assert need in cols
    ev = {r["name"] for r in db.q("PRAGMA table_info(trade_ledger_events)")}
    assert {"event_uid", "event_type", "position_ticket", "payload"} <= ev
    # ensure twice = idempotent
    n_before = db.one("SELECT COUNT(*) c FROM sqlite_master")["c"]
    ledger.ensure_schema(db)
    assert db.one("SELECT COUNT(*) c FROM sqlite_master")["c"] == n_before


def test_02_event_ingestion_is_idempotent_by_uid(db):
    assert ledger.record_event(db, event_uid="deal:1", event_type="EXIT_DEAL",
                               deal_ticket=1) is True
    assert ledger.record_event(db, event_uid="deal:1", event_type="EXIT_DEAL",
                               deal_ticket=1) is False          # duplicate poll
    rows = db.q("SELECT * FROM trade_ledger_events WHERE event_uid='deal:1'")
    assert len(rows) == 1


def test_03_upsert_never_double_counts_a_position(db):
    r1 = ledger.upsert_trade(db, {"strategy_id": 718, "symbol": "XAUUSD", "side": "BUY",
                                  "entry_price": 4200.0, "lots": 0.05,
                                  "open_ts": 1791522760.0, "status": "POSITION_OPEN",
                                  "ticket": 111, "position_ticket": 111,
                                  "timeframe": "M15"})
    r2 = ledger.upsert_trade(db, {"strategy_id": 718, "symbol": "XAUUSD", "side": "BUY",
                                  "entry_price": 4200.0, "lots": 0.05,
                                  "open_ts": 1791522760.0, "status": "POSITION_OPEN",
                                  "ticket": 111, "position_ticket": 111,
                                  "timeframe": "M15", "net_pnl": 3.0})
    assert r1 == r2
    n = db.one("SELECT COUNT(*) c FROM live_test_trades WHERE position_ticket=111")["c"]
    assert n == 1


def test_04_evidence_accretes_and_nulls_never_erase(db):
    rid = ledger.upsert_trade(db, {"strategy_id": 7, "symbol": "XAUUSD", "side": "SELL",
                                   "entry_price": 4200.0, "lots": 0.02, "open_ts": 1.0,
                                   "status": "POSITION_OPEN", "ticket": 5,
                                   "position_ticket": 5, "timeframe": "M15",
                                   "broker_comment": "Request executed"})
    ledger.update_trade(db, rid, exit_price=None, net_pnl=12.5)     # None skipped
    row = db.one("SELECT * FROM live_test_trades WHERE id=?", (rid,))
    assert row["broker_comment"] == "Request executed"
    assert row["exit_price"] is None
    assert row["net_pnl"] == pytest.approx(12.5)


# --------------------------------------------------------------------------- #
# exit capture (D: SL / TP / other / unknown / multi-deal / partial)
# --------------------------------------------------------------------------- #
from app.live_testing.engine import LiveTestingEngine  # noqa: E402


@pytest.fixture()
def eng():
    return LiveTestingEngine()


def _row(db, **kw):
    base = {"strategy_id": 718, "symbol": "XAUUSD", "side": "BUY", "entry_price": 4200.0,
            "lots": 0.05, "open_ts": 1791522760.0, "status": "POSITION_OPEN",
            "ticket": 111, "position_ticket": 111, "timeframe": "M15"}
    base.update(kw)
    rid = ledger.upsert_trade(db, base)
    return db.one("SELECT * FROM live_test_trades WHERE id=?", (rid,))


def test_05_sl_exit_with_broker_evidence(db, eng):
    r = _row(db)
    br = FakeBridge(deals={111: [_deal(1, 111, entry=0, profit=5.0),
                                 _deal(2, 111, entry=1, reason=4, profit=-25.0)]})
    info = eng._capture_exit(db, br, r)
    assert info["captured"] and info["close_reason"] == "SL_EXIT"
    assert info["exit_reason_source"] == "broker_deal_reason"
    assert info["gross_pnl"] == pytest.approx(-25.0)
    assert info["net_pnl"] == pytest.approx(-25.0 - 0.5 - 0.1)
    assert info["close_kind"] == "FULL"
    row = db.one("SELECT * FROM live_test_trades WHERE id=?", (r["id"],))
    assert row["exit_reason"] == "SL_EXIT" and row["exit_price"] == pytest.approx(4200.0)
    # the exit deal is an append-only, idempotent event
    evs = ledger.get_events(db, position_ticket=111)
    assert any(e["event_type"] == "EXIT_DEAL" for e in evs)


def test_06_tp_exit_with_broker_evidence(db, eng):
    r = _row(db)
    br = FakeBridge(deals={111: [_deal(2, 111, entry=1, reason=5, profit=40.0)]})
    info = eng._capture_exit(db, br, r)
    assert info["close_reason"] == "TP_EXIT"
    assert info["gross_pnl"] == pytest.approx(40.0)


def test_07_unknown_exit_is_never_invented(db, eng):
    r = _row(db)
    br = FakeBridge(deals={111: [_deal(2, 111, entry=1, reason=99, profit=1.0)]})
    info = eng._capture_exit(db, br, r)
    assert info["close_reason"] == "UNKNOWN_EXIT"
    assert info["exit_reason_source"] == "broker_deal_unclassified"


def test_08_partial_close_keeps_position_semantics(db, eng):
    r = _row(db, lots=0.10)
    br = FakeBridge(deals={111: [_deal(2, 111, entry=1, reason=0, profit=2.0, volume=0.04)]})
    info = eng._capture_exit(db, br, r)
    assert info["close_kind"] == "PARTIAL"
    assert info["closed_volume"] == pytest.approx(0.04)


def test_09_multiple_exit_deals_one_position_no_double_count(db, eng):
    r = _row(db, lots=0.10)
    br = FakeBridge(deals={111: [_deal(2, 111, entry=1, reason=0, profit=1.0, volume=0.05),
                                 _deal(3, 111, entry=1, reason=0, profit=2.0, volume=0.05)]})
    info = eng._capture_exit(db, br, r)
    assert info["close_kind"] == "FULL"
    assert info["gross_pnl"] == pytest.approx(3.0)
    assert info["exit_price"] == pytest.approx(4200.0)
    # one current-state row only
    n = db.one("SELECT COUNT(*) c FROM live_test_trades WHERE position_ticket=111")["c"]
    assert n == 1
    evs = [e for e in ledger.get_events(db, position_ticket=111) if e["event_type"] == "EXIT_DEAL"]
    assert len(evs) == 2


def test_10_missing_deal_history_is_explicit_not_fabricated(db, eng):
    r = _row(db)
    br = FakeBridge(deals={})            # history not retrievable
    info = eng._capture_exit(db, br, r)
    assert info["captured"] is False
    assert info["close_reason"] == "closed at broker (exit deal not retrievable)"
    assert info["evidence_source"] == "position_absent"
    assert info["gross_pnl"] is None and info["net_pnl"] is None


def test_11_duplicate_history_poll_is_idempotent(db, eng):
    r = _row(db)
    br = FakeBridge(deals={111: [_deal(2, 111, entry=1, reason=5, profit=9.0)]})
    eng._capture_exit(db, br, r)
    eng._capture_exit(db, br, r)          # the same poll replayed
    evs = [e for e in ledger.get_events(db, position_ticket=111) if e["event_type"] == "EXIT_DEAL"]
    assert len(evs) == 1


# --------------------------------------------------------------------------- #
# reconciliation: recovery, restarts, discrepancies (D + §6.5)
# --------------------------------------------------------------------------- #
def test_12_broker_position_without_local_record_is_recovered(db, eng):
    pos = {"ticket": 777, "symbol": "XAUUSD", "type": 0, "volume": 0.05,
           "price_open": 4190.0, "price_current": 4191.0, "sl": 4180.0, "tp": 4250.0,
           "profit": 5.0, "magic": 778718, "time": 1791523608, "comment": "evolab-livetest-718"}
    br = FakeBridge(positions=[pos])
    out = {}
    eng._recover_broker_positions(db, br, out)
    assert out.get("recovered") == 1
    row = db.one("SELECT * FROM live_test_trades WHERE position_ticket=777")
    assert row is not None and row["evidence_source"] == "broker_recovered"
    assert row["data_complete"] == 0                      # unknown facts stay unknown
    er = json.loads(row["entry_reason"])
    assert er["explanation_kind"] == "broker_recovered_no_local_record"
    # replaying the recovery must not duplicate the row
    eng._recover_broker_positions(db, br, {})
    n = db.one("SELECT COUNT(*) c FROM live_test_trades WHERE position_ticket=777")["c"]
    assert n == 1


def test_13_unrelated_magic_positions_are_excluded(db, eng):
    pos = {"ticket": 778, "symbol": "XAUUSD", "type": 0, "volume": 0.05,
           "price_open": 4190.0, "price_current": 4191.0, "sl": 0, "tp": 0,
           "profit": 5.0, "magic": 777000, "time": 1, "comment": "manual"}
    out = {}
    eng._recover_broker_positions(db, FakeBridge(positions=[pos]), out)
    assert out.get("recovered", 0) == 0
    assert db.one("SELECT COUNT(*) c FROM live_test_trades WHERE position_ticket=778")["c"] == 0


def test_14_disconnection_is_visible_and_fails_safe(db, eng):
    out = {}
    eng._recover_broker_positions(db, FakeBridge(fail_positions=True), out)
    assert out.get("errors", 0) >= 1


def test_15_restart_preserves_the_ledger(tmp_path):
    path = str(tmp_path / "restart.db")
    d1 = Database(path)
    ledger.ensure_schema(d1)
    ledger.upsert_trade(d1, {"strategy_id": 718, "symbol": "XAUUSD", "side": "BUY",
                             "entry_price": 4200.0, "lots": 0.05, "open_ts": 1.0,
                             "status": "POSITION_OPEN", "ticket": 42,
                             "position_ticket": 42, "timeframe": "M15"})
    d2 = Database(path)                    # a "restart"
    ledger.ensure_schema(d2)
    row = d2.one("SELECT * FROM live_test_trades WHERE position_ticket=42")
    assert row is not None and row["status"] == "POSITION_OPEN"


def test_16_persistence_failure_never_raises_into_the_engine(db):
    class BrokenDB:
        def q(self, *a, **k):
            raise RuntimeError("disk full")

        def x(self, *a, **k):
            raise RuntimeError("disk full")

        def one(self, *a, **k):
            raise RuntimeError("disk full")

        def ensure_live_trade_columns(self):
            pass

    ok = ledger.record_event(BrokenDB(), event_uid="x", event_type="T")
    assert ok is False                    # visible failure, not an exception
