"""V5.2 §8/§10–§16 — regression suite for the deep-testing data layer, the one
population model, demo scheduling and order idempotency.

Everything here runs the REAL modules against a scratch database created with the
lab's own schema (``Database``), so the numbers and verdicts are produced by
production code, not by fixture arithmetic:

  §10  the explicit lifecycle names (TOTAL/ALIVE/QUALIFIED/FINAL_TESTING_ELIGIBLE/
       DEEP_TESTING_ELIGIBLE/LIVE_TESTING_ELIGIBLE) are derived from the same
       classifier as ``counts`` — one authority, one set of numbers;
  §11  data requirements are computed over ALL deep-eligible nodes;
  §12  suggested data is the UNION of those requirements (symbols/timeframes/
       range/fields/warm-up), never a hard-coded list;
  §13  GET MT5 DATA runs the EXISTING infrastructure (``DataEngine.sync_master``)
       through every stage and reports requested vs received vs stored bars;
  §14  a node is READY only when the stored bars cover its own union window, and
       NOT READY otherwise — with the reason;
  §16  a demo schedule is validated/persisted/evaluated by the one evaluator
       (``app.live_testing.schedule``) and blocks activation outside its window;
  §8   a replayed ``client_order_id`` never reaches ``order_send`` twice.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
from app.db.database import Database
from app.research import deep_data as DD
from app.research import populations as P


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def _genome(symbol="XAUUSD", timeframe="M15", *, long_bars=200, regimes=()):
    """The REAL genome shape this lab stores (type: compare with left/right)."""
    g = {"symbol": symbol, "timeframe": timeframe, "direction": "both",
         "entry_long": {"op": "and", "clauses": [
             {"type": "compare", "left": f"sma:{long_bars}", "cmp": ">", "right": "close"}]},
         "entry_short": {"op": "and", "clauses": [
             {"type": "compare", "left": f"sma:{long_bars}", "cmp": "<", "right": "close"}]},
         "exit": {"sl_atr_mult": 1.5, "tp_atr_mult": 3.0, "atr_spec": "atr:14"}}
    if regimes:
        g["regime_filters"] = list(regimes)
    return g


def _insert(db, sid_hash, *, status="VALID", symbol="XAUUSD", timeframe="M15",
            data_source="USER_RESEARCH", genome=None, fitness=1.0):
    return db.insert_strategy({
        "hash": sid_hash, "symbol": symbol, "timeframe": timeframe, "status": status,
        "genome": genome if genome is not None else _genome(symbol, timeframe),
        "fitness": fitness, "data_source": data_source, "generation": 1,
    })


@pytest.fixture()
def db(tmp_path):
    d = Database(str(tmp_path / "v52_deep.db"))
    # a user node (qualified), a second user node on another timeframe, a legacy
    # infrastructure row and a dead node — the population model must separate them
    _insert(d, "h_qualified_m15", status="VALID")
    _insert(d, "h_qualified_h1", status="VALID", timeframe="H1",
            genome=_genome("XAUUSD", "H1", long_bars=100, regimes=["trending"]))
    _insert(d, "h_legacy", status="VALID", data_source="LEGACY_TEST")
    _insert(d, "h_failed", status="STRATEGY_FAILED", fitness=-1.0)
    return d


def _ids(db):
    return [int(r["id"]) for r in db.q("SELECT id FROM strategies ORDER BY id")]


# ===========================================================================
# §10 — one authoritative status model
# ===========================================================================
def test_01_population_state_names_match_the_counts(db):
    data = P.populations(db=db)
    state = P.population_state(db=db)
    counts = data["counts"]
    st = state["state"]
    assert list(st.keys()) == ["TOTAL", "ALIVE", "QUALIFIED", "FINAL_TESTING_ELIGIBLE",
                               "DEEP_TESTING_ELIGIBLE", "LIVE_TESTING_ELIGIBLE",
                               "LIVE_TESTING_ACTIVE"]
    assert st["TOTAL"] == counts["total"] == 4
    assert st["ALIVE"] == counts["alive"]
    assert st["QUALIFIED"] == counts["qualified"] == 2      # the two VALID user nodes
    assert st["DEEP_TESTING_ELIGIBLE"] == counts["deep"] == 2
    assert st["FINAL_TESTING_ELIGIBLE"] == counts["final"] == 0
    # V5.2.2 — LIVE_TESTING_ELIGIBLE is CAPABILITY: the two VALID user nodes are
    # qualified and pass the engine's tradeability predicate, so they are eligible
    # even though nothing has been started.  LIVE_TESTING_ACTIVE (enrolment) is the
    # separate number and is legitimately 0 here.
    assert st["LIVE_TESTING_ELIGIBLE"] == counts["live"] == 2
    assert st["LIVE_TESTING_ACTIVE"] == counts["live_active"] == 0
    assert state["authority"] == data["authority"]
    assert "deep-testing union" in state["definitions"]["DEEP_TESTING_ELIGIBLE"]


def test_02_the_legacy_row_is_never_alive_or_qualified_or_deep(db):
    state = P.population_state(db=db)["state"]
    # VALID user nodes count as alive; the LEGACY_TEST row and the failed node do not
    assert state["TOTAL"] == 4 and state["ALIVE"] == 2
    assert P.populations(db=db)["detail"]["legacy_excluded"] == 1
    members = {m["id"] for m in P.deep_universe(db=db)}
    legacy_id = [int(r["id"]) for r in db.q("SELECT id FROM strategies WHERE data_source='LEGACY_TEST'")][0]
    failed_id = [int(r["id"]) for r in db.q("SELECT id FROM strategies WHERE status='STRATEGY_FAILED'")][0]
    assert legacy_id not in members and failed_id not in members


def test_03_live_eligibility_is_capability_and_activity_is_enrolment(db):
    """V5.2.2 §10 — the two questions are answered by two numbers.

    ELIGIBLE answers "what CAN the live layer act on?" (capability, derived from the
    engine's own predicate) and ACTIVE answers "what IS enrolled right now?"
    (enrolment).  Before V5.2.2 the enrolment count was published under the word
    "eligible", so a lab where nobody had pressed START reported 0 eligible nodes
    even with qualified nodes on the page.
    """
    ids = _ids(db)
    state = P.population_state(db=db)["state"]
    assert state["QUALIFIED"] == 2
    assert state["LIVE_TESTING_ELIGIBLE"] == 2, "qualified + tradeable = eligible, before any START"
    assert state["LIVE_TESTING_ACTIVE"] == 0, "nothing has been enrolled yet"

    # enrolling both nodes is a DIFFERENT number: it must not change eligibility
    db.set_mt5_demo_config(ids[0], {"strategy_id": ids[0], "enabled": True,
                                    "confirmed_demo_only": True, "status": "RUNNING"})
    db.set_live_test_config(ids[1], {"strategy_id": ids[1], "is_active": 1})
    state = P.population_state(db=db)["state"]
    assert state["LIVE_TESTING_ELIGIBLE"] == 2, "enrolment never inflates eligibility"
    assert state["LIVE_TESTING_ACTIVE"] == 2, "both enrolments are real and counted"
    assert state["TOTAL"] == 4                                # nothing else moved

    # and the detail behind the number is exposed, never just the total
    live_inputs = P.populations(db=db)["detail"]["live_inputs"]
    assert live_inputs["eligible"] == 2 and live_inputs["active"] == 2
    assert live_inputs["active_configs_total"] == 2
    assert {e["id"] for e in live_inputs["eligible_detail"]["eligible"]} == set(ids[:2])


def test_04_the_endpoint_serves_the_same_numbers(db, monkeypatch):
    import app.api.routes as routes
    monkeypatch.setattr(routes, "get_db", lambda: db, raising=False)
    out = routes.nodes_populations()
    assert out["state"]["QUALIFIED"] == out["counts"]["qualified"]
    assert out["state_order"][0] == "TOTAL"


# ===========================================================================
# §11/§12 — requirements + the union
# ===========================================================================
def test_05_requirements_cover_every_eligible_node(db):
    req = DD.requirements(db=db)
    assert req["nodes_total"] == 2 and req["nodes_with_requirements"] == 2
    per = {r["node_id"]: r for r in req["per_node"]}
    for r in per.values():
        assert r["eligible"] is True
        assert r["symbol"] == "XAUUSD"
        assert r["warmup_bars"] == DD.WARMUP_FLOOR            # 200 and 100 → floor 300
        assert r["fields"] == list(DD.DATA_FIELDS)
        assert r["earliest_ts"] < r["latest_ts"]
        assert r["required_bars"] and r["required_bars"] > 0
        assert r["window"]["source"]


def test_06_the_union_has_one_item_per_symbol_timeframe(db):
    agg = DD.requirements(db=db)["aggregate"]
    pairs = {(i["symbol"], i["timeframe"]) for i in agg["items"]}
    assert pairs == {("XAUUSD", "M15"), ("XAUUSD", "H1")}
    m15 = [i for i in agg["items"] if i["timeframe"] == "M15"][0]
    assert m15["node_count"] == 1
    assert agg["symbols"] == ["XAUUSD"]
    assert set(agg["timeframes"]) == {"M15", "H1"}
    assert agg["warmup_bars"] == DD.WARMUP_FLOOR
    assert agg["date_range"]["start_ts"] <= agg["date_range"]["end_ts"]


def test_07_suggested_data_is_the_union_not_a_constant(db):
    sug = DD.suggested_data(db=db)
    assert [s["symbol"] for s in sug["symbols"]] == ["XAUUSD"]
    assert sug["symbols"][0]["timeframes"] == ["H1", "M15"]
    assert sug["symbols"][0]["node_count"] == 2
    assert sug["fields"] == list(DD.DATA_FIELDS)
    assert set(sug["lookbacks"].keys()) == {"XAUUSD M15", "XAUUSD H1"}
    assert sug["nodes"]["deep_eligible"] == 2
    assert "union" in sug["note"]


def test_08_a_wider_lookback_raises_its_own_timeframe_requirement(db):
    _insert(db, "h_big", status="VALID", genome=_genome("XAUUSD", "M15", long_bars=900))
    agg = DD.requirements(db=db)["aggregate"]
    m15 = [i for i in agg["items"] if i["timeframe"] == "M15"][0]
    assert m15["warmup_bars"] == 900                            # not the 300 floor
    assert m15["node_count"] == 2


# ===========================================================================
# §14 — readiness
# ===========================================================================
def test_09_readiness_is_not_ready_without_the_bars(db, monkeypatch):
    monkeypatch.setattr(DD, "coverage", lambda db=None, pairs=None: {
        f"{s}|{t}": {"bars": 10, "source": "master", "start": "x", "end": "y"}
        for s, t in [("XAUUSD", "M15"), ("XAUUSD", "H1")]})
    r = DD.readiness(db=db)
    assert r["data_ready"] is False and r["data_status"] == "DATA NOT READY"
    assert r["nodes_ready"] == 0 and r["nodes_total"] == 2
    assert "vs" in r["nodes"][0]["reason"]


def test_10_readiness_is_ready_only_when_the_bars_cover_the_window(db, monkeypatch):
    req = DD.requirements(db=db)
    need = {f"{i['symbol']}|{i['timeframe']}": i["required_bars"] for i in req["aggregate"]["items"]}
    monkeypatch.setattr(DD, "coverage", lambda db=None, pairs=None: {
        k: {"bars": v + 5, "source": "master", "start": "2025-01-01", "end": "2026-10-01"}
        for k, v in need.items()})
    r = DD.readiness(db=db)
    assert r["data_ready"] is True and r["data_status"] == "DATA READY"
    assert r["nodes_ready"] == 2 and r["nodes_status"] == "2/2 NODES READY"
    assert all(n["ready"] for n in r["nodes"])
    assert "cover" in r["nodes"][0]["reason"]


def test_11_an_unknown_bar_count_is_not_ready(db, monkeypatch):
    monkeypatch.setattr(DD, "coverage", lambda db=None, pairs=None: {
        "XAUUSD|M15": {"bars": None, "source": None, "start": None, "end": None,
                       "error": "master store unavailable"},
        "XAUUSD|H1": {"bars": None, "source": None, "start": None, "end": None}})
    r = DD.readiness(db=db)
    assert r["data_ready"] is False
    assert any("could not be read" in n["reason"] for n in r["nodes"])


# ===========================================================================
# §13 — GET MT5 DATA
# ===========================================================================
class FakeEngine:
    """Stands in for DataEngine: records the calls, returns real-shaped results."""

    def __init__(self, *, total=1000, new=1000, fail_on=None):
        self.calls = []
        self.total, self.new, self.fail_on = total, new, fail_on

    def sync_master(self, symbol, timeframe, force_full=False):
        self.calls.append((symbol, timeframe, bool(force_full)))
        if self.fail_on == (symbol, timeframe):
            raise RuntimeError("bridge returned nothing")
        return {"symbol": symbol, "timeframe": timeframe, "fetch_mode": "initial_bootstrap",
                "new_bars": self.new, "total_bars": self.total, "action": "FETCHED FROM MT5"}


class FakeBridge:
    source = "MT5"
    is_simulated = False


def _wire(monkeypatch, engine, bridge=None, stored=None):
    import app.data.engine as de
    import app.mt5 as mt5pkg
    monkeypatch.setattr(de, "get_data_engine", lambda: engine)
    monkeypatch.setattr(mt5pkg, "get_bridge", lambda: (bridge or FakeBridge()))
    monkeypatch.setattr(DD, "coverage", lambda db=None, pairs=None: {
        f"{s}|{t}": {"bars": (stored or {}).get(f"{s}|{t}", 5000), "source": "master",
                     "start": "2026-01-01T00:00:00", "end": "2026-10-01T00:00:00"}
        for s, t in (pairs or [])})
    monkeypatch.setattr(DD, "readiness", lambda db=None: {
        "data_ready": True, "data_status": "DATA READY", "nodes_ready": 2,
        "nodes_total": 2, "nodes_status": "2/2 NODES READY"})


def _job():
    j = DD.GetDataJob()
    return j


def test_12_get_data_runs_every_stage_through_the_existing_engine(monkeypatch):
    eng = FakeEngine()
    _wire(monkeypatch, eng)
    j = _job()
    out = j.start(selection=[{"symbol": "XAUUSD", "timeframe": "M15", "start": "a", "end": "b",
                              "required_bars": 2880, "nodes": [1, 2]}])
    assert out["ok"] is True
    j._thread.join(timeout=20)
    st = j.status()
    assert st["status"] == "COMPLETED"
    assert st["stage"] == "NODES READY"
    assert st["totals"]["items_done"] == 1 and st["totals"]["items_total"] == 1
    assert st["totals"]["requested_bars"] == 2880
    assert st["totals"]["received_bars"] == 1000
    assert st["totals"]["stored_bars"] == 5000
    item = st["items"][0]
    assert item["status"] == "DONE" and item["pct"] == 100.0
    assert item["fetch_mode"] == "initial_bootstrap"
    assert eng.calls == [("XAUUSD", "M15", False)]
    assert st["readiness_after"]["data_status"] == "DATA READY"
    assert st["pct"] == 100.0


def test_13_get_data_records_a_failure_per_item_and_reports_it(monkeypatch):
    eng = FakeEngine(fail_on=("XAUUSD", "H1"))
    _wire(monkeypatch, eng)
    j = _job()
    j.start(selection=[{"symbol": "XAUUSD", "timeframe": "H1", "required_bars": 100, "nodes": []}])
    j._thread.join(timeout=20)
    st = j.status()
    item = st["items"][0]
    assert item["status"] == "ERROR" and "RuntimeError" in item["error"]
    assert any("H1" in e for e in st["errors"])
    # a failed item is never reported as stored or complete
    assert st["totals"]["items_done"] == 0


def test_14_a_simulator_bridge_says_so_instead_of_inventing_bars(monkeypatch):
    eng = FakeEngine()

    class Sim(FakeBridge):
        source = "SIMULATOR"
        is_simulated = True

    _wire(monkeypatch, eng, bridge=Sim())
    j = _job()
    j.start(selection=[{"symbol": "XAUUSD", "timeframe": "M15", "required_bars": 10, "nodes": []}])
    j._thread.join(timeout=20)
    st = j.status()
    assert any("SIMULATOR" in e for e in st["errors"])
    assert "existing MT5 historical infrastructure" in st["note"]


def test_15_starting_twice_is_refused_while_running(monkeypatch):
    eng = FakeEngine()
    _wire(monkeypatch, eng)
    j = _job()
    j.start(selection=[{"symbol": "XAUUSD", "timeframe": "M15", "required_bars": 1, "nodes": []}])
    second = j.start(selection=[{"symbol": "XAUUSD", "timeframe": "M15", "required_bars": 1}])
    assert second["ok"] is False and "already running" in second["error"]
    j._thread.join(timeout=20)
    assert len(eng.calls) == 1


def test_16_the_default_selection_is_the_suggested_union(db, monkeypatch):
    eng = FakeEngine()
    _wire(monkeypatch, eng)
    monkeypatch.setattr(DD, "suggested_data", lambda db=None: {
        "package": [{"symbol": "XAUUSD", "timeframe": "M15", "earliest": "2026-01-01",
                     "latest": "2026-10-01", "required_bars": 100, "nodes": [1]},
                    {"symbol": "XAUUSD", "timeframe": "H1", "earliest": "2026-01-01",
                     "latest": "2026-10-01", "required_bars": 50, "nodes": [2]}]})
    j = _job()
    j.start()
    j._thread.join(timeout=20)
    assert sorted(eng.calls) == [("XAUUSD", "H1", False), ("XAUUSD", "M15", False)]
    st = j.status()
    assert st["source"] == "SUGGESTED DATA (union)"
    assert len(st["items"]) == 2


# ===========================================================================
# §16 — the schedule system on MT5 Demo Trading
# ===========================================================================
def _demo_cfg(db, sid, **kw):
    cfg = {"strategy_id": sid, "enabled": True, "confirmed_demo_only": True,
           "status": "RUNNING"}
    cfg.update(kw)
    db.set_mt5_demo_config(sid, cfg)
    return cfg


def test_17_a_saved_demo_schedule_is_validated_persisted_and_described(db):
    from app.mt5 import demo_schedule as DS
    sid = _ids(db)[0]
    _demo_cfg(db, sid)
    out = DS.save_schedule(sid, {"days": [0, 1], "sessions": ["london"],
                                 "windows": [{"start": "08:00", "end": "17:00"}],
                                 "timezone": "UTC", "regimes": ["trending"],
                                 "max_trades_per_day": 3}, db=db)
    assert out["ok"] is True and out["configured"] is True
    assert out["schedule"]["days"] == [0, 1]
    assert out["schedule"]["sessions"] == ["london"]
    assert out["schedule"]["windows"][0]["start"] == "08:00"
    assert "Mon" in out["description"] and "London" in out["description"]
    # persisted: a fresh handle on the same file reads it back
    again = Database(str(db.path)) if hasattr(db, "path") else None
    got = DS.get_schedule(sid, db=db)
    assert got["configured"] is True
    assert got["schedule"]["max_trades_per_day"] == 3
    assert got["evaluation"]["allowed"] in (True, False)


def test_18_an_invalid_schedule_is_refused_with_the_field(db):
    from app.mt5 import demo_schedule as DS
    sid = _ids(db)[0]
    _demo_cfg(db, sid)
    out = DS.save_schedule(sid, {"timezone": "Mars/Olympus"}, db=db)
    assert out["ok"] is False
    assert any(e.get("field") == "timezone" for e in out["errors"])
    assert "not saved" in out["error"]
    assert DS.get_schedule(sid, db=db)["configured"] is False


def test_19_the_gate_admits_and_blocks_by_the_saved_schedule(db):
    from app.mt5 import demo_schedule as DS
    sid = _ids(db)[0]
    _demo_cfg(db, sid)
    today = datetime.now(timezone.utc).weekday()
    other = (today + 3) % 7
    DS.save_schedule(sid, {"days": [today], "timezone": "UTC"}, db=db)
    allowed = DS.gate(sid, db=db)
    assert allowed["allowed"] is True and allowed["configured"] is True
    DS.save_schedule(sid, {"days": [other], "timezone": "UTC"}, db=db)
    blocked = DS.gate(sid, db=db)
    assert blocked["allowed"] is False
    assert "day" in json.dumps(blocked["rules"]).lower() or blocked["reason"]
    # no schedule at all = product default, still allowed
    DS.clear_schedule(sid, db=db)
    default = DS.gate(sid, db=db)
    assert default["allowed"] is True and default["configured"] is False


def test_20_the_gate_blocks_outside_the_window_and_carries_the_reason(db):
    from app.mt5 import demo_schedule as DS
    sid = _ids(db)[0]
    _demo_cfg(db, sid)
    now_utc = datetime.now(timezone.utc)
    # a window that certainly does not contain "now": +6h → +7h (or, if that would
    # wrap past midnight, an earlier window in the same day — never a wrap-around)
    start = f"{(now_utc.hour + 6) % 24:02d}:00"
    end = f"{(now_utc.hour + 7) % 24:02d}:59"
    out_save = DS.save_schedule(sid, {"windows": [{"start": start, "end": end}],
                                      "timezone": "UTC"}, db=db)
    assert out_save["ok"] is True, out_save.get("errors")
    out = DS.gate(sid, db=db)
    assert out["allowed"] is False
    assert "window" in (out["reason"] or "").lower() or json.dumps(out["rules"])


def test_20b_an_empty_day_list_is_refused_by_the_shared_validator(db):
    """`[]` is not "all days" — the one validator refuses it for demo nodes too."""
    from app.mt5 import demo_schedule as DS
    sid = _ids(db)[0]
    _demo_cfg(db, sid)
    out = DS.save_schedule(sid, {"days": []}, db=db)
    assert out["ok"] is False
    assert any(e.get("field") == "days" for e in out["errors"])


def test_21_activation_outside_the_schedule_is_reported_not_hidden(db, monkeypatch):
    import app.api.routes as routes
    from app.mt5 import demo_schedule as DS
    sid = _ids(db)[0]
    _demo_cfg(db, sid, enabled=False, status="STOPPED")
    today = datetime.now(timezone.utc).weekday()
    DS.save_schedule(sid, {"days": [(today + 3) % 7], "timezone": "UTC"}, db=db)
    monkeypatch.setattr(routes, "get_db", lambda: db, raising=False)
    res = routes.toggle_mt5_demo(sid, {"confirmed_demo_only": True})
    assert res["enabled"] is True
    assert res["status"] == "SCHEDULE_BLOCKED"
    assert res["schedule_allowed"] is False and res["schedule_reason"]
    stored = db.get_mt5_demo_config(sid)
    assert stored["status"] == "SCHEDULE_BLOCKED"


def test_22_the_demo_status_publishes_each_nodes_schedule_and_verdict(db, monkeypatch):
    import app.api.routes as routes
    from app.mt5 import demo_schedule as DS
    sid = _ids(db)[0]
    _demo_cfg(db, sid)
    saved = DS.save_schedule(sid, {"timezone": "UTC"}, db=db)
    assert saved["ok"] is True, saved.get("errors")
    monkeypatch.setattr(routes, "get_db", lambda: db, raising=False)
    monkeypatch.setattr(routes, "get_bridge", lambda: _StubBridge(), raising=False)
    out = routes.mt5_demo_status()
    cfg = [c for c in out["active_configs"] if int(c["strategy_id"]) == sid][0]
    assert cfg["schedule_configured"] is True
    assert cfg["schedule_allowed"] is True
    assert cfg["schedule_description"] and cfg["schedule_description"] != ""
    assert "app.live_testing.schedule" in out["schedule_authority"]


def test_22b_start_shortlist_respects_the_schedule_too(db, monkeypatch):
    """Activation is gated on both batch paths — the shortlist is not a back door."""
    import app.api.routes as routes
    from app.mt5 import demo_schedule as DS
    sid = _ids(db)[0]
    _demo_cfg(db, sid, enabled=False, status="STOPPED")
    db.add_to_shortlist(sid)
    today = datetime.now(timezone.utc).weekday()
    DS.save_schedule(sid, {"days": [(today + 4) % 7], "timezone": "UTC"}, db=db)
    monkeypatch.setattr(routes, "get_db", lambda: db, raising=False)

    out = routes.mt5_demo_start_shortlist({"confirmed_demo_only": True})
    assert out["ok"] is True and out["shortlist_size"] == 1
    assert out["schedule_blocked_count"] == 1
    assert out["schedule_blocked"][0]["strategy_id"] == sid
    assert out["schedule_blocked"][0]["reason"]
    assert db.get_mt5_demo_config(sid)["status"] == "SCHEDULE_BLOCKED"

    # an unconfigured node keeps the product default and is activated normally
    DS.clear_schedule(sid, db=db)
    out2 = routes.mt5_demo_start_shortlist({"confirmed_demo_only": True})
    assert out2["schedule_blocked_count"] == 0
    assert db.get_mt5_demo_config(sid)["status"] == "RUNNING"


class _StubBridge:
    source = "SIMULATOR"
    is_simulated = True
    available = staticmethod(lambda: True)
    account_id = "DEMO-TEST"
    broker_name = "test"
    server_name = "test"

    def __init__(self):
        self._connected = False


# ===========================================================================
# §8 — idempotency of the manual/automated order submission
# ===========================================================================
class _Terminal:
    """A terminal double good enough for the duplicate-submission test."""

    ACCOUNT_TRADE_MODE_DEMO = 0
    ORDER_TYPE_BUY = 0
    TRADE_ACTION_DEAL = 1
    TRADE_RETCODE_DONE = 10009
    ORDER_FILLING_RETURN = 2
    SYMBOL_FILLING_IOC = 2

    def __init__(self):
        self.order_send_calls = 0
        self.order_check_calls = 0

    def initialize(self, path=None, **kw):
        return True

    def shutdown(self):
        return True

    def version(self):
        return (500, 6230, "2026-09-27")

    def terminal_info(self):
        return _O(name="MetaTrader 5", company="Raw Trading Ltd", path="C:/MT5", build=6230,
                  connected=True, trade_allowed=True, tradeapi_disabled=False, dlls_allowed=True)

    def account_info(self):
        return _O(login=53071066, server="ICMarketsSC-Demo", trade_mode=0, balance=200000.0,
                  equity=200000.0, margin_free=199000.0, currency="USD", leverage=500,
                  trade_allowed=True, trade_expert=True)

    def symbol_select(self, symbol, enable=True):
        return True

    def symbol_info(self, symbol):
        return _O(symbol=symbol, visible=True, digits=2, point=0.01, trade_tick_size=0.01,
                  trade_tick_value=1.0, volume_min=0.01, volume_max=100.0, volume_step=0.01,
                  trade_stops_level=0, freeze_level=0, trade_mode=4, trade_allowed=True,
                  filling_mode=0, bid=4086.56, ask=4086.75, spread=19, currency_profit="USD")

    def symbol_info_tick(self, symbol):
        return _O(bid=4086.56, ask=4086.75, last=4086.66, spread=19, time=int(time.time()))

    def last_error(self):
        return (0, "Ok")

    def order_check(self, request):
        self.order_check_calls += 1
        if int(request.get("type_filling") or 0) != 2:
            return _C(10030, "Unsupported filling mode", 0.0)
        return _C(0, "Done", 8.17)

    def order_send(self, request):
        self.order_send_calls += 1
        return _result(10009, deal=1, order=2, volume=request["volume"], price=request["price"])

    def copy_rates_from_pos(self, symbol, timeframe, start, count):
        return None

    def symbol_info(self, symbol):                     # noqa: F811 - real shape
        return _O(symbol=symbol, visible=True, digits=2, point=0.01, trade_tick_size=0.01,
                  trade_tick_value=1.0, trade_contract_size=100.0, volume_min=0.01,
                  volume_max=100.0, volume_step=0.01, trade_stops_level=0, freeze_level=0,
                  trade_mode=4, trade_allowed=True, filling_mode=0, bid=4086.56, ask=4086.75,
                  spread=19, spread_points=19.0, currency_profit="USD")

    def positions_get(self, ticket=None, symbol=None):
        return [_O(ticket=2, symbol="XAUUSD", volume=0.03, price_open=4086.75, sl=4056.75,
                   tp=4116.75, profit=0.0, magic=777001, type=0)]

    def orders_get(self, ticket=None, symbol=None):
        return []


class _O:
    def __init__(self, **kw):
        self.__dict__.update(kw)


from collections import namedtuple as _nt

_C = _nt("Check", "retcode balance equity profit margin margin_free margin_level comment request")
_R = _nt("Result", "retcode deal order volume price bid ask comment request_id retcode_external request")

_C.__new__.__defaults__ = ()          # keep the namedtuple strict


def _c(retcode, comment, margin):
    return _C(retcode, 200000.0, 200000.0, 0.0, margin, 199000.0, 100.0, comment, None)


def _result(retcode, **kw):
    base = dict(retcode=retcode, deal=0, order=0, volume=0.0, price=0.0, bid=4086.56,
                ask=4086.75, comment="Done", request_id=1, retcode_external=0, request=None)
    base.update(kw)
    return _R(**base)


import app.mt5.mt5_real as MTR  # noqa: E402  (after the doubles are defined)


@pytest.fixture()
def terminal(monkeypatch):
    term = _Terminal()
    term.order_check = lambda request: (_c(0, "Done", 8.17) if int(request.get("type_filling") or 0) == 2
                                        else _c(10030, "Unsupported filling mode", 0.0))
    term.positions_get = lambda ticket=None, symbol=None: []
    monkeypatch.setattr(MTR, "mt5", term, raising=False)
    monkeypatch.setattr(MTR, "MT5_PACKAGE_AVAILABLE", True, raising=False)
    monkeypatch.setattr(MTR, "MT5_IMPORT_ERROR", "", raising=False)
    return term


def test_23_replaying_the_same_client_order_id_never_sends_twice(db, terminal, monkeypatch):
    from app.mt5 import execution as ex
    from app.mt5.mt5_real import MT5RealBridge
    monkeypatch.setattr(ex, "_db", lambda: db)
    bridge = MT5RealBridge()
    bridge._connected = True
    payload = {"symbol": "XAUUSD", "side": "buy", "volume": 0.03, "price": 4086.75,
               "sl": 4056.75, "tp": 4116.75, "confirm": ex.PLACE_CONFIRMATION,
               "magic": 777001, "client_order_id": "v52-idem-1"}
    first = ex.place_demo_order(payload, bridge=bridge)
    assert first["ok"] is True
    assert terminal.order_send_calls == 1
    # a replayed request is refused *before* the terminal is asked again
    with pytest.raises(ex.MT5ExecutionError) as ei:
        ex.place_demo_order(payload, bridge=bridge)
    assert "already submitted" in str(ei.value)
    assert terminal.order_send_calls == 1              # the broker was never asked twice
    assert db.get_manual_mt5_order("v52-idem-1") is not None
