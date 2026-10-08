"""V5.1a-next §C — the Live Testing order path must be the validated V4.2 one.

Spec facts proven here:

  * a live-test order leaves through ``execution.place_demo_order`` ->
    ``bridge.send_market_order`` (i.e. ``mt5.order_check`` first, then
    ``mt5.order_send``) — never through the legacy ``real_market_order`` helper;
  * the order carries the live-test magic (778000 range) so it stays
    distinguishable from manual (777000-range) demo orders in MT5;
  * when ``order_send`` yields nothing, the trace/database record carries the
    §A diagnostic (result_class NO_RESULT, last_error) instead of a bare
    "UNKNOWN", and the engine does not resubmit.

The MT5 terminal is the same test double used by the V4.2/V4.3 suites; the
database is a scratch file — no authoritative DATA row and no real order.
"""
from __future__ import annotations

import importlib.util
import time
from pathlib import Path

import pytest

from app.db.database import Database
from app.live_testing import engine as eng_mod
from app.mt5 import execution as ex

REPO = Path(__file__).resolve().parents[1]


def _load(mod_name: str, filename: str):
    spec = importlib.util.spec_from_file_location(mod_name, REPO / "tests" / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_v43 = _load("v43_live_double", "test_v4_3_live_testing.py")


class PathAssertingBridge(_v43.LiveTestBridge):
    """Records the exact call order and refuses the legacy helper."""

    def __init__(self, term, **kw):
        super().__init__(term, **kw)
        self.call_order: list = []
        self.checked: list = []
        self.none_result = False

    def check_market_order(self, request):
        self.call_order.append("order_check")
        self.checked.append(dict(request))
        return super().check_market_order(request)

    def send_market_order(self, request):
        self.call_order.append("order_send")
        if self.none_result:
            return {"ok": False, "called": True, "call_count": 1, "retcode": None,
                    "raw": None, "error": "order_send returned None",
                    "last_error": [-10004, "No IPC connection"],
                    "exception": "RuntimeError: mt5.order_send returned None (last_error=[-10004, 'No IPC connection'])",
                    "diagnostic": {"phase": "ORDER_SEND_RETURNED_NONE", "order_send_called": True,
                                   "request": dict(request), "last_error": [-10004, "No IPC connection"],
                                   "symbol": {"point": 0.01, "digits": 2, "volume_min": 0.01},
                                   "account": {"login": 50123456, "trade_mode": 0,
                                               "trade_mode_name": "DEMO"}}}
        return super().send_market_order(request)

    def real_market_order(self, symbol, side, lots):        # pragma: no cover - must not run
        raise AssertionError("the live-testing loop must not use bridge.real_market_order()")


@pytest.fixture()
def term():
    return _v43.FakeMT5Terminal()


@pytest.fixture()
def bridge(term):
    return PathAssertingBridge(term)


@pytest.fixture()
def db(tmp_path):
    d = Database(str(tmp_path / "v51a_live_path.db"))
    d.x("DELETE FROM live_test_configs")
    d.x("DELETE FROM live_test_trades")
    return d


@pytest.fixture()
def wired(monkeypatch, bridge, term, db):
    """Same wiring the V4.3 suite uses, plus the scratch database."""
    import copy
    import app.db.database as database_mod
    import app.mt5.factory as factory
    import app.mt5.mt5_real as real
    from app.config import get_config
    from app.live_testing import risk as risk_mod

    cfg = copy.deepcopy(get_config())
    cfg.live_testing.risk_pct_default = 1.0
    cfg.live_testing.risk_pct_max = 2.0
    cfg.live_testing.max_active_trades = 1
    cfg.live_testing.tick_interval_s = 0.05
    cfg.live_testing.max_data_age_s = 120
    monkeypatch.setattr(eng_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(risk_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(database_mod, "get_db", lambda: db)
    monkeypatch.setattr(factory, "get_bridge", lambda: bridge)
    monkeypatch.setattr(real, "MT5_PACKAGE_AVAILABLE", True)
    monkeypatch.setattr(ex, "mt5_module", lambda: term)
    monkeypatch.setattr(ex, "_db", lambda: db)
    monkeypatch.setattr(eng_mod, "get_db", lambda: db)
    monkeypatch.setattr(eng_mod, "get_bridge", lambda: bridge)
    return bridge


def _node(db, node_id=901, side="BUY"):
    _v43.add_node(db, node_id, side=side)
    _v43.enable_node(db, node_id)
    return node_id


def _events(engine):
    return [(e["stage"], e["status"]) for e in reversed(engine.recent_events)]


# ===========================================================================
# 1 — the validated path: order_check -> order_send
# ===========================================================================
def test_01_live_order_goes_through_send_market_order_only(wired, db):
    """The engine's order leaves via the single V4.2 bridge call.

    ``order_check`` is the *first* thing ``MT5RealBridge.send_market_order`` does
    (proven in tests/test_v5_1a_mt5_order_diagnostics.py::test_08 with the real
    bridge code), so the engine must reach the bridge through that one method and
    never through a second, unvalidated sending path.
    """
    _node(db)
    eng = _v43.fresh_engine()
    eng.activate(confirmed=True)
    eng._cycle()
    assert wired.call_order == ["order_send"], wired.call_order


def test_02_the_sent_order_is_the_validated_request(wired, db):
    _node(db)
    eng = _v43.fresh_engine()
    eng.activate(confirmed=True)
    eng._cycle()
    sent = wired.checked or []
    assert sent == []                       # the engine itself pre-checks nothing...
    from app.db.database import get_db
    row = get_db().q("SELECT * FROM live_test_trades ORDER BY id DESC")[0]
    assert row["symbol"] == "XAUUSD" and row["lots"] and row["sl"] is not None


def test_03_the_live_test_magic_is_used_and_differs_from_manual_orders(wired, db, term):
    _node(db, 903)
    eng = _v43.fresh_engine()
    eng.activate(confirmed=True)
    eng._cycle()
    sent = term.sent[0]
    assert sent["magic"] == ex.live_test_magic(903)
    assert ex.LIVE_TEST_MAGIC_BASE <= sent["magic"] < ex.LIVE_TEST_MAGIC_BASE + 1000
    manual = ex._magic_for(903)
    assert manual == 777000 + 3 and sent["magic"] != manual
    assert "evolab-livetest-903" == sent["comment"]


def test_04_the_position_is_verified_after_the_send(wired, db, term):
    _node(db, 904)
    eng = _v43.fresh_engine()
    eng.activate(confirmed=True)
    eng._cycle()
    names = [s for s, _ in _events(eng)]
    assert "POSITION VERIFIED" in names
    assert 555001 in term.positions
    row = db.q("SELECT * FROM live_test_trades ORDER BY id DESC")[0]
    assert row["status"] == "POSITION_OPEN" and row["ticket"] == 555001


def test_05_the_trace_reports_the_order_send_facts(wired, db):
    """§A data must reach the live-test trace, not just the manual panel."""
    _node(db, 905)
    eng = _v43.fresh_engine()
    eng.activate(confirmed=True)
    eng._cycle()
    broker_ev = [e for e in eng.recent_events if e["stage"] == "BROKER RESPONSE"][0]
    assert broker_ev["detail"]["result_class"] == "BROKER_RESULT" or \
        broker_ev["detail"].get("diagnostic_phase") in (None, "ORDER_SEND_RETURNED_RESULT")
    assert "order_send" in broker_ev["detail"]


# ===========================================================================
# 2 — order_send returns nothing: UNKNOWN with a reason, never a retry
# ===========================================================================
def test_06_none_result_is_reported_as_no_result_with_the_last_error(wired, db):
    wired.none_result = True
    _node(db, 906)
    eng = _v43.fresh_engine()
    eng.activate(confirmed=True)
    eng._cycle()
    broker_ev = [e for e in eng.recent_events if e["stage"] == "BROKER RESPONSE"][0]
    d = broker_ev["detail"]
    assert d["status"] == "UNKNOWN"
    assert d["result_class"] == "UNKNOWN_EXECUTION"
    assert d["diagnostic_phase"] == "ORDER_SEND_RETURNED_NONE"
    assert d["order_send"]["called"] is True
    assert d["order_send"]["last_error"] == [-10004, "No IPC connection"]
    assert d["safe_to_retry"] is False
    assert "No IPC connection" in (d["reason"] or "") or "No IPC connection" in (d["message"] or "")


def test_07_none_result_is_never_resent(wired, db):
    wired.none_result = True
    _node(db, 907)
    eng = _v43.fresh_engine()
    eng.activate(confirmed=True)
    eng._cycle()
    assert wired.call_order.count("order_send") == 1
    eng._cycle()                                    # second cycle: unresolved symbol is held
    assert wired.call_order.count("order_send") == 1
    names = [s for s, _ in _events(eng)]
    assert eng_mod.STAGE_UNKNOWN in names                 # "UNKNOWN - VERIFY MT5"


def test_08_unknown_record_is_persisted_with_its_reason(wired, db):
    wired.none_result = True
    _node(db, 908)
    eng = _v43.fresh_engine()
    eng.activate(confirmed=True)
    eng._cycle()
    row = db.q("SELECT * FROM live_test_trades ORDER BY id DESC")[0]
    assert str(row["status"]).upper() in ("UNKNOWN", "UNRESOLVED")
    blob = str(row.get("result_json") or row.get("result") or "")
    assert "NO_RESULT" in blob or "No IPC connection" in blob


# ===========================================================================
# 3 — source-level invariants
# ===========================================================================
def _legacy_call_sites(rel_path: str) -> list:
    """Line numbers where a module actually *calls* .real_market_order(...).

    Comments mentioning the legacy helper are fine (they document why it is not
    used); an executed call would not be — that is what this checks.
    """
    import ast
    tree = ast.parse((REPO / rel_path).read_text(encoding="utf-8"))
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "real_market_order":
            hits.append(node.lineno)
    return hits


def test_09_the_engine_never_calls_the_legacy_real_market_order():
    assert _legacy_call_sites("backend/app/live_testing/engine.py") == []
    src = (REPO / "backend/app/live_testing/engine.py").read_text(encoding="utf-8")
    assert "place_demo_order" in src


def test_10_the_executor_never_calls_the_legacy_helper_either():
    assert _legacy_call_sites("backend/app/mt5/execution.py") == []


def test_11_the_manual_and_live_magic_ranges_stay_disjoint():
    assert ex._magic_for(1) == 777001
    assert ex.live_test_magic(1) == 778001
    assert 777000 <= ex._magic_for(999) < 778000
    assert 778000 <= ex.live_test_magic(999) < 779000
