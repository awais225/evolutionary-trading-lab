"""V6.5 §5/§12-C — global and per-node trade limits at the execution boundary.

Deterministic bridge stubs only.  Covers: the global cap, the per-node default
of ONE, an explicit node override of TWO (other nodes keep the default),
concurrent signals across nodes and within one node, a reduced cap that never
closes existing positions, uncertain (timeout) outcomes that keep their slot,
verified rejections that release it, restart reconciliation from broker state,
and exclusion of unrelated positions.  Admission FAILS SAFE when broker state
is unreadable.
"""
from __future__ import annotations

import threading

import pytest

from app.live_testing.admission import TradeAdmission, get_admission
from app.live_testing.risk import resolve_per_node_limit


class FakeBridge:
    source = "MT5"

    def __init__(self, positions=None, orders=None, fail=False):
        self.positions = positions or []
        self.orders = orders or []
        self.fail = fail
        self.closed = []

    def positions_get(self, ticket=None, symbol=None):
        if self.fail:
            raise RuntimeError("disconnected")
        return list(self.positions)

    def orders_get(self, ticket=None, symbol=None):
        if self.fail:
            raise RuntimeError("disconnected")
        return list(self.orders)


def pos(ticket, magic, symbol="XAUUSD"):
    return {"ticket": ticket, "magic": magic, "symbol": symbol, "volume": 0.05}


BASE = 778000
M718 = BASE + 718
M719 = BASE + 719


def _admit(gate, bridge, node_id, magic, per_node=1, global_limit=2):
    return gate.admit(node_id=node_id, magic=magic, symbol="XAUUSD", bridge=bridge,
                      magic_lo=BASE, magic_hi=BASE + 1000,
                      global_limit=global_limit, per_node_limit=per_node)


# --------------------------------------------------------------------------- #
# defaults and overrides (§5.1/§5.2)
# --------------------------------------------------------------------------- #
def test_01_default_per_node_limit_is_one():
    r = resolve_per_node_limit({"config": {}})
    assert r == {"effective": 1, "source": "default", "default": 1}


def test_02_explicit_override_is_isolated_per_node():
    r718 = resolve_per_node_limit({"config": {"max_positions": 2}})
    r719 = resolve_per_node_limit({"config": {"max_positions": None}})
    assert r718["effective"] == 2 and r718["source"] == "override"
    assert r719["effective"] == 1 and r719["source"] == "default"


# --------------------------------------------------------------------------- #
# enforcement (§5.3)
# --------------------------------------------------------------------------- #
def test_03_global_cap_blocks_with_no_new_reservation():
    gate = TradeAdmission()
    bridge = FakeBridge(positions=[pos(1, M718), pos(2, M719)])
    res = _admit(gate, bridge, 720, BASE + 720, per_node=1, global_limit=2)
    assert not res["ok"] and res["code"] == "GLOBAL_LIMIT_REACHED"


def test_04_per_node_cap_blocks_only_that_node():
    gate = TradeAdmission()
    bridge = FakeBridge(positions=[pos(1, M718)])
    r718 = _admit(gate, bridge, 718, M718, per_node=1, global_limit=10)
    r719 = _admit(gate, bridge, 719, M719, per_node=1, global_limit=10)
    assert not r718["ok"] and r718["code"] == "NODE_LIMIT_REACHED"
    assert r719["ok"]


def test_05_override_two_allows_two_for_that_node_only():
    gate = TradeAdmission()
    bridge = FakeBridge(positions=[pos(1, M718)])
    r1 = _admit(gate, bridge, 718, M718, per_node=2, global_limit=10)   # 1 open < 2
    assert r1["ok"]
    gate.settle(r1["reservation"], "EXECUTED")
    r2 = _admit(gate, bridge, 718, M718, per_node=2, global_limit=10)   # still 1 open
    assert r2["ok"]
    gate.settle(r2["reservation"], "EXECUTED")
    bridge.positions.append(pos(2, M718))                                # 2nd fills
    r3 = _admit(gate, bridge, 718, M718, per_node=2, global_limit=10)   # 2 >= 2
    assert not r3["ok"] and r3["code"] == "NODE_LIMIT_REACHED"
    # a different node is untouched by 718's override: 719 has no position,
    # so its inherited default of 1 admits exactly one
    r719 = _admit(gate, bridge, 719, M719, per_node=1, global_limit=10)
    assert r719["ok"]
    gate.settle(r719["reservation"], "EXECUTED")
    bridge.positions.append(pos(9, M719))
    r719b = _admit(gate, bridge, 719, M719, per_node=1, global_limit=10)
    assert not r719b["ok"] and r719b["code"] == "NODE_LIMIT_REACHED"


def test_06_concurrent_signals_cannot_jointly_exceed_a_cap():
    gate = TradeAdmission()
    bridge = FakeBridge()                  # nothing open at the broker yet
    results = []
    lock = threading.Lock()

    def worker():
        r = _admit(gate, bridge, 718, M718, per_node=1, global_limit=1)
        with lock:
            results.append(r)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(1 for r in results if r["ok"]) == 1      # exactly one admission
    assert sum(1 for r in results if not r["ok"]) == 7


def test_07_concurrent_two_nodes_cannot_exceed_global_cap():
    gate = TradeAdmission()
    bridge = FakeBridge()
    results = []
    lock = threading.Lock()

    def worker(nid, magic):
        r = _admit(gate, bridge, nid, magic, per_node=1, global_limit=2)
        with lock:
            results.append(r)

    threads = [threading.Thread(target=worker, args=(718 + i, BASE + 718 + i))
               for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(1 for r in results if r["ok"]) == 2


def test_08_uncertain_outcome_keeps_its_slot_until_reconciled():
    gate = TradeAdmission()
    bridge = FakeBridge()
    r1 = _admit(gate, bridge, 718, M718, per_node=1, global_limit=1)
    assert r1["ok"]
    gate.settle(r1["reservation"], "UNCERTAIN")          # timeout: status unknown
    r2 = _admit(gate, bridge, 719, M719, per_node=1, global_limit=1)
    assert not r2["ok"]                 # the uncertain slot is NOT reusable
    # reconciliation proves nothing exists -> the slot is released
    out = gate.reconcile(bridge, BASE, BASE + 1000)
    assert out["released"] >= 1
    r3 = _admit(gate, bridge, 719, M719, per_node=1, global_limit=1)
    assert r3["ok"]


def test_09_verified_rejection_releases_the_slot():
    gate = TradeAdmission()
    bridge = FakeBridge()
    r1 = _admit(gate, bridge, 718, M718, per_node=1, global_limit=1)
    assert r1["ok"]
    gate.settle(r1["reservation"], "REJECTED")
    r2 = _admit(gate, bridge, 719, M719, per_node=1, global_limit=1)
    assert r2["ok"]


def test_10_reduced_cap_never_closes_existing_positions():
    gate = TradeAdmission()
    bridge = FakeBridge(positions=[pos(1, M718), pos(2, M718), pos(3, M719)])
    res = _admit(gate, bridge, 720, BASE + 720, per_node=1, global_limit=1)
    assert not res["ok"]                 # blocked only
    assert bridge.closed == []           # nothing was ever closed


def test_11_broker_unreadable_fails_safe():
    gate = TradeAdmission()
    bridge = FakeBridge(fail=True)
    res = _admit(gate, bridge, 718, M718, per_node=5, global_limit=5)
    assert not res["ok"] and res["code"] == "BROKER_STATE_UNAVAILABLE"


def test_12_unrelated_positions_are_not_counted():
    gate = TradeAdmission()
    bridge = FakeBridge(positions=[pos(1, 777000), pos(2, 123), pos(3, M718)])
    res = _admit(gate, bridge, 719, M719, per_node=1, global_limit=5)
    assert res["ok"]                     # only 1 managed position of 3 total
    assert res["active_total"] == 1


def test_13_same_position_counted_once():
    gate = TradeAdmission()
    bridge = FakeBridge(positions=[pos(1, M718), pos(1, M718)])   # two APIs, one ticket
    res = _admit(gate, bridge, 719, M719, per_node=1, global_limit=1)
    assert not res["ok"] and res["code"] == "GLOBAL_LIMIT_REACHED"
    assert res["active_total"] == 1


def test_14_restart_reconciles_counts_from_broker_state():
    bridge = FakeBridge(positions=[pos(1, M718)])       # open when the app restarts
    gate = TradeAdmission()                              # fresh process state
    res = _admit(gate, bridge, 718, M718, per_node=1, global_limit=5)
    assert not res["ok"] and res["code"] == "NODE_LIMIT_REACHED"
    res2 = _admit(gate, bridge, 719, M719, per_node=1, global_limit=5)
    assert res2["ok"]                    # the pre-existing position is counted for 718 only


def test_15_stopped_node_is_never_admitted():
    gate = TradeAdmission()
    bridge = FakeBridge()
    res = gate.admit(node_id=718, magic=M718, symbol="XAUUSD", bridge=bridge,
                     magic_lo=BASE, magic_hi=BASE + 1000,
                     global_limit=5, per_node_limit=5, node_active=False)
    assert not res["ok"] and res["code"] == "NODE_STOPPED"


def test_16_singleton_gate_is_shared():
    assert get_admission() is get_admission()
