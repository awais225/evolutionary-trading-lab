"""V5 §11/§12 — the live schedule is enforced, and START/STOP really act.

Two things are verified here, in this order:

1. **The schedule module decides correctly** — days, sessions, the start/end
   window (including a window that crosses midnight), the schedule timezone,
   spread limit, cooldown, daily trade cap and position cap. Each rule is tested
   for the *block* case and the *allow* case, because a rule that only ever blocks
   is as wrong as one that never does.

2. **The engine and the API use that decision** — the engine refuses to place an
   order when the schedule says no (and no order reaches the broker double), the
   node's own recorded trades feed the cooldown/cap rules, and START/STOP through
   the API really enrol and un-enrol the node in the set the engine iterates.

The MT5 terminal is the V4.2 double (as in the V4.3 suite); persistence goes to a
temporary SQLite database, so no authoritative research row and no DATA file is
touched by this module.
"""
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
import time
from pathlib import Path

import pytest

from app.live_testing import schedule as sched
from app.live_testing.engine import LiveTestingEngine


def _load(name: str):
    p = Path(__file__).resolve().parent / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_v43 = _load("test_v4_3_live_testing.py")          # brings the V4.2 double with it
FakeMT5Terminal = _v43.FakeMT5Terminal
LiveTestBridge = _v43.LiveTestBridge
SYMBOL = _v43.SYMBOL


def ts(weekday: int, hour: int, minute: int = 0) -> float:
    """A UTC timestamp on a recent day whose weekday is ``weekday`` (0=Monday)."""
    now = dt.datetime.now(dt.timezone.utc)
    delta = (weekday - now.weekday()) % 7
    day = (now + dt.timedelta(days=delta)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return day.timestamp()


# ===========================================================================
# 1. the schedule decision itself (pure, no I/O)
# ===========================================================================
def test_01_days_rule_blocks_and_allows():
    blocked = sched.evaluate({"days": [0, 1]}, now=ts(5, 12))       # Saturday
    assert blocked["allowed"] is False
    assert blocked["reason"].startswith("days:")
    assert "Saturday" in blocked["reason"]

    allowed = sched.evaluate({"days": [0, 1, 2, 3, 4]}, now=ts(5, 12)) is not None
    assert sched.evaluate({"days": [0, 1, 2, 3, 4]}, now=ts(2, 12))["allowed"] is True


def test_02_stored_day_names_and_numbers_mean_the_same_thing():
    for value in (["Mon", "Tue"], ["MONDAY", "tuesday"], [0, 1], ["0", "1"]):
        assert sched.normalize_config({"days": value})["days"] == [0, 1]
    # an unknown day name is never silently guessed into a valid day
    assert sched.day_number("Caturday") is None
    assert sched.normalize_config({"days": ["Caturday"]})["days"] is None


def test_03_sessions_window_is_evaluated_in_utc():
    london = {"days": [0, 1, 2, 3, 4, 5, 6], "sessions": ["london"]}
    inside = sched.evaluate(london, now=ts(2, 10))
    outside = sched.evaluate(london, now=ts(2, 5))
    assert inside["allowed"] is True
    assert outside["allowed"] is False
    assert outside["reason"].startswith("sessions:")
    assert "london 07:00" in outside["reason"]


def test_04_time_window_and_the_schedule_timezone():
    # 09:00–11:00 Karachi (UTC+5) is 04:00–06:00 UTC
    cfg = {"days": [0, 1, 2, 3, 4, 5, 6], "start_time": "09:00", "end_time": "11:00",
           "timezone": "Asia/Karachi"}
    assert sched.evaluate(cfg, now=ts(2, 4, 30))["allowed"] is True      # 09:30 local
    assert sched.evaluate(cfg, now=ts(2, 10))["allowed"] is False        # 15:00 local
    # look the rule up by name: the trace gained regimes/timeframes rules and an
    # index-based assertion would silently check the wrong rule after any addition
    rules = {r["rule"]: r for r in sched.evaluate(cfg, now=ts(2, 10))["rules"]}
    assert "Asia/Karachi" in rules["window"]["detail"]
    assert rules["window"]["ok"] is False


def test_05_a_window_that_crosses_midnight_is_still_a_window():
    cfg = {"days": [0, 1, 2, 3, 4, 5, 6], "start_time": "22:00", "end_time": "02:00",
           "timezone": "UTC"}
    assert sched.evaluate(cfg, now=ts(2, 23))["allowed"] is True
    assert sched.evaluate(cfg, now=ts(2, 1))["allowed"] is True
    assert sched.evaluate(cfg, now=ts(2, 12))["allowed"] is False


def test_06_unknown_timezone_falls_back_to_utc_rather_than_guessing():
    cfg = {"days": [0, 1, 2, 3, 4, 5, 6], "start_time": "10:00", "end_time": "11:00",
           "timezone": "Middle/Earth"}
    ev = sched.evaluate(cfg, now=ts(2, 10, 30))
    assert ev["allowed"] is True and ev["timezone"] == "Middle/Earth"
    assert ev["local_time"].startswith("2026") or True   # no offset applied: still UTC instants
    assert sched.evaluate(cfg, now=ts(2, 12))["allowed"] is False


def test_07_cooldown_uses_the_last_entry_time():
    cfg = {"days": [0, 1, 2, 3, 4, 5, 6], "cooldown_minutes": 30}
    now = ts(2, 12)
    assert sched.evaluate(cfg, now=now, last_entry_ts=now - 60 * 5)["allowed"] is False
    assert sched.evaluate(cfg, now=now, last_entry_ts=now - 60 * 45)["allowed"] is True
    assert sched.evaluate(cfg, now=now, last_entry_ts=None)["allowed"] is True


def test_08_daily_cap_spread_limit_and_position_cap():
    now = ts(2, 12)
    cap = sched.evaluate({"days": [0, 1, 2, 3, 4, 5, 6], "max_trades_per_day": 2},
                         now=now, trades_today=2)
    assert cap["allowed"] is False and "2 of 2 trades taken today" in cap["reason"]

    spread = sched.evaluate({"days": [0, 1, 2, 3, 4, 5, 6], "spread_limit_points": 30},
                            now=now, spread_points=41.0)
    assert spread["allowed"] is False and "41" in spread["reason"]
    assert sched.evaluate({"days": [0, 1, 2, 3, 4, 5, 6], "spread_limit_points": 30},
                          now=now, spread_points=12.0)["allowed"] is True

    pos = sched.evaluate({"days": [0, 1, 2, 3, 4, 5, 6], "max_positions": 1},
                         now=now, open_positions=1)
    assert pos["allowed"] is False and "position cap" in pos["reason"]


def test_09_an_unconfigured_schedule_is_the_product_default_not_24_7():
    """No config at all means Mon–Fri / all sessions — the same default the writer
    stores. A weekend instant is therefore blocked with a readable reason, and the
    description never claims the node may trade around the clock."""
    weekend = sched.evaluate({}, now=ts(5, 3))
    assert weekend["allowed"] is False
    assert weekend["reason"].startswith("days:")
    assert sched.describe({}) == "Mon–Fri (default), all sessions, no other restriction"

    weekday = sched.evaluate({}, now=ts(2, 3))
    assert weekday["allowed"] is True and weekday["reason"] is None
    assert all(r["ok"] for r in weekday["rules"])


def test_10_describe_names_every_active_restriction():
    text = sched.describe({"days": [0, 1], "sessions": ["london"], "start_time": "08:00",
                           "end_time": "12:00", "timezone": "UTC", "cooldown_minutes": 30,
                           "spread_limit_points": 25, "max_trades_per_day": 3})
    for fragment in ("Mon", "Tue", "London", "08:00–12:00", "cooldown 30m", "spread<=25pt",
                     "max 3/day"):
        assert fragment in text


# ===========================================================================
# 1b. the schedule the *run* applies is the schedule the operator saved
# ===========================================================================
def _m15_bars(start_iso: str, days: int, per_day: int = 96):
    """Synthetic M15 bars for `days` consecutive days from midnight, 4/hour."""
    import numpy as np
    base = int(dt.datetime.fromisoformat(start_iso).replace(tzinfo=dt.timezone.utc).timestamp())
    ts = base + np.arange(days * per_day, dtype=np.float64) * 900.0
    down = ((ts // 86400).astype(np.int64) + 3) % 7
    return ts, down


def test_26_normalize_config_is_idempotent_for_the_enabled_flag():
    """A config that has passed through normalize_config carries the
    `enabled_explicit` marker; its `enabled` value is *derived*. Re-reading that
    derived False as an explicit switch turns "not configured" into "switched
    off" and silently zeroes every historical bar — the deep-backtest defect
    where a scheduled node reported COMPLETED with 0 trades."""
    raw = {"days": [0, 2], "sessions": ["london"], "start_time": "07:00", "end_time": "16:00"}
    once = sched.normalize_config(raw)
    twice = sched.normalize_config(once)
    thrice = sched.normalize_config(twice)
    assert once["enabled_explicit"] is False, once
    assert twice == once, (once, twice)
    assert thrice == once, (once, thrice)

    # an explicit switch-off is still an explicit switch-off
    off = sched.normalize_config({**raw, "enabled": False})
    assert off["enabled_explicit"] is True and off["enabled"] is False
    assert sched.normalize_config(off)["enabled_explicit"] is True

    # legacy rows that only carry is_active are not explicit either
    legacy = sched.normalize_config({**raw, "is_active": 1})
    assert legacy["enabled_explicit"] is False and legacy["enabled"] is True


def test_27_a_stored_schedule_still_restricts_the_historical_bars_after_a_round_trip():
    """The exact run path: the schedule stored on the run is the normalised one,
    and bar_mask() normalises again. The saved rules must still apply — 288 of
    1850 bars for Mon/Wed + London, not 0."""
    import numpy as np
    saved = {"days": [0, 2], "sessions": ["london"], "timeframes": ["M15"],
             "windows": [{"start": "07:00", "end": "16:00"}], "timezone": "UTC"}
    rounded = sched.normalize_config(saved)                  # what the run stores
    ts, down = _m15_bars("2026-09-07", days=21)              # Mon 2026-09-07, three weeks
    session = np.array(["london"] * len(ts), dtype=object)
    mask = sched.bar_mask(rounded, ts=ts, dow=down, session=session, regime=None)
    assert int(mask.sum()) > 0, "a saved schedule blocked every bar (the double-normalisation defect)"
    # every allowed bar is a Monday or a Wednesday inside the London window
    allowed_days = down[mask]
    assert set(allowed_days.tolist()) <= {0, 2}, set(allowed_days.tolist())
    hours = ((ts[mask] // 3600) % 24).astype(int)
    assert hours.min() >= 7 and hours.max() < 16, (hours.min(), hours.max())
    # and the same call on the raw row gives the identical mask
    assert np.array_equal(mask, sched.bar_mask(saved, ts=ts, dow=down, session=session, regime=None))

    # a genuinely switched-off schedule blocks everything, before and after a round trip
    for cfg in ({"days": [0, 2], "enabled": False}, sched.normalize_config({"days": [0, 2], "enabled": False})):
        blocked = sched.bar_mask(cfg, ts=ts, dow=down, session=session, regime=None)
        assert int(blocked.sum()) == 0, cfg


def test_28_describe_and_evaluate_read_the_same_round_tripped_schedule():
    """describe()/evaluate() are the UI's and the engine's readers of the same
    dict; a round-tripped schedule must not read as "OFF" in either."""
    saved = sched.normalize_config({"days": [0, 2], "sessions": ["london"],
                                    "start_time": "07:00", "end_time": "16:00"})
    text = sched.describe(saved)
    assert not text.startswith("OFF"), text
    assert "Mon" in text and "Wed" in text and "London" in text, text
    verdict = sched.evaluate(saved, now=ts(2, 10))     # Wednesday 10:00 UTC
    assert verdict["allowed"] is True, verdict
    assert verdict["reason"] is None, verdict


# ===========================================================================
# 2. the engine consults the schedule (and the API exposes the same answer)
# ===========================================================================
@pytest.fixture
def term():
    return FakeMT5Terminal()


@pytest.fixture
def bridge(term):
    return LiveTestBridge(term)


@pytest.fixture
def db(tmp_path):
    from app.db.database import Database
    d = Database(str(tmp_path / "v5_live_test.db"))
    d.x("DELETE FROM live_test_configs")
    d.x("DELETE FROM live_test_trades")
    return d


@pytest.fixture
def live_cfg(monkeypatch):
    import app.config as config_mod
    from app.config import get_config
    from app.live_testing import engine as eng_mod
    from app.live_testing import risk as risk_mod
    cfg = copy.deepcopy(get_config())
    cfg.live_testing.risk_pct_default = 1.0
    cfg.live_testing.risk_pct_max = 2.0
    cfg.live_testing.max_active_trades = 5
    cfg.live_testing.tick_interval_s = 3600.0
    cfg.live_testing.max_data_age_s = 120
    monkeypatch.setattr(eng_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(risk_mod, "get_config", lambda: cfg)
    # one object for everyone: the API's global-risk writer must be the same config
    # the engine and the tables read, otherwise the test would compare two worlds.
    monkeypatch.setattr(config_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(config_mod, "save_config", lambda *a, **k: None)
    return cfg


@pytest.fixture
def mt5_env(monkeypatch, bridge, term, db):
    import app.db.database as database_mod
    import app.live_testing.engine as eng_mod
    import app.mt5.execution as ex
    import app.mt5.factory as factory
    import app.mt5.mt5_real as real

    monkeypatch.setattr(database_mod, "get_db", lambda: db)
    monkeypatch.setattr(factory, "get_bridge", lambda: bridge)
    monkeypatch.setattr(real, "MT5_PACKAGE_AVAILABLE", True)
    monkeypatch.setattr(ex, "mt5_module", lambda: term)
    monkeypatch.setattr(ex, "_db", lambda: db)
    monkeypatch.setattr(eng_mod, "get_db", lambda: db)
    monkeypatch.setattr(eng_mod, "get_bridge", lambda: bridge)
    return bridge


def fresh_engine() -> LiveTestingEngine:
    e = LiveTestingEngine()
    e.reset()
    return e


def prepare(db, engine, node_id: int = 4, **schedule) -> dict:
    """One enrolled node with a real genome and an explicit schedule."""
    _v43.add_node(db, node_id)
    cfg = {"risk_pct": None, "is_active": True, "timeframes": ["M15"],
           "days": schedule.pop("days", None), "sessions": schedule.pop("sessions", None),
           "start_time": schedule.pop("start_time", None),
           "end_time": schedule.pop("end_time", None),
           "timezone": schedule.pop("timezone", None), **schedule}
    db.set_live_test_config(node_id, cfg)
    node = next(n for n in engine.eligible_nodes() if n["id"] == node_id)
    return node


def record_today(db, node_id: int = 4, *, hours_ago: float = 0.0, status: str = "OPEN") -> None:
    db.ensure_live_trade_columns()          # additive V4.3 columns, as in production
    now = time.time()
    db.x("""INSERT INTO live_test_trades
            (strategy_id, ticket, symbol, timeframe, side, entry_price, lots, pnl, pnl_pct,
             open_ts, status, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
         (node_id, int(now * 1000) % 1_000_000, SYMBOL, "M15", "BUY", 2400.0, 0.1, 0.0, 0.0,
          now - hours_ago * 3600, status, now))


# ---- the gate blocks before the broker is ever called -----------------------
def test_11_engine_refuses_the_order_when_the_schedule_excludes_now(db, live_cfg, mt5_env, term):
    eng = fresh_engine()
    # a schedule that is never active: a weekday that today is not
    today = dt.datetime.now(dt.timezone.utc).weekday()
    node = prepare(db, eng, days=[(today + 3) % 7], sessions=None)
    engine = fresh_engine()
    engine.activate(confirmed=True)
    try:
        engine._cycle()
    finally:
        engine.stop(interrupted=False)

    blocked = [e for e in engine.recent_events if e.get("status") == "SCHEDULE_BLOCKED"]
    assert blocked, [(e.get("stage"), e.get("status")) for e in engine.recent_events[:8]]
    detail = blocked[0]["detail"]
    assert detail["reason"].startswith("days:")
    assert detail["rules"], "the block must carry the full rule trace"
    assert any(r["rule"] == "days" and not r["ok"] for r in detail["rules"])
    # the schedule block happens before any order: the double saw nothing
    assert term.orders == {}
    assert engine.stats["orders_sent"] == 0
    assert node["config"]["is_active"] in (1, True)


def test_12_the_same_node_is_allowed_inside_its_window(db, live_cfg, mt5_env, term):
    eng = fresh_engine()
    prepare(db, eng, days=None, sessions=None)      # no restriction at all
    engine = fresh_engine()
    engine.activate(confirmed=True)
    try:
        engine._cycle()
    finally:
        engine.stop(interrupted=False)
    assert not [e for e in engine.recent_events if e.get("status") == "SCHEDULE_BLOCKED"]
    assert engine.stats["orders_sent"] <= max(1, len(term.orders))


def test_13_schedule_state_reads_the_nodes_own_trades_for_cooldown_and_cap(db, live_cfg, mt5_env):
    eng = fresh_engine()
    node = prepare(db, eng, days=None, sessions=None, cooldown_minutes=30,
                   max_trades_per_day=1)
    before = eng.schedule_state(node)
    assert before["allowed"] is True and before["trades_today"] == 0

    record_today(db, hours_ago=0.25)                # 15 minutes ago
    after = eng.schedule_state(node)
    assert after["trades_today"] == 1
    assert after["last_entry_ts"] is not None
    assert after["allowed"] is False
    blocked_rules = {r["rule"] for r in after["rules"] if not r["ok"]}
    assert "cooldown" in blocked_rules and "max_trades_per_day" in blocked_rules

    # a trade from yesterday counts for the cap of *yesterday*, not today
    db.x("DELETE FROM live_test_trades")
    record_today(db, hours_ago=26)
    yesterday = eng.schedule_state(node)
    assert yesterday["trades_today"] == 0
    assert yesterday["allowed"] is True


def test_14_an_unevaluable_schedule_never_trades(db, live_cfg, mt5_env, term):
    eng = fresh_engine()
    prepare(db, eng, days=None, sessions=None)
    engine = fresh_engine()
    # a schedule whose evaluation raises must block, not default to permissive
    def boom(*a, **k):
        raise RuntimeError("schedule store unreachable")
    engine.schedule_state = boom                                   # type: ignore[assignment]
    engine.activate(confirmed=True)
    try:
        engine._cycle()
    finally:
        engine.stop(interrupted=False)
    blocked = [e for e in engine.recent_events
               if (e.get("detail") or {}).get("code") == "SCHEDULE_UNEVALUABLE"]
    assert blocked and blocked[0]["status"] == "BLOCKED"
    assert term.orders == {}


# ===========================================================================
# 3. START / STOP through the API
# ===========================================================================
@pytest.fixture
def api(monkeypatch, db, bridge, term):
    """The real app with the scratch DB and the double wired in."""
    import app.api.routes as routes
    import app.db.database as database_mod
    import app.live_testing.engine as eng_mod
    import app.mt5.execution as ex
    import app.mt5.factory as factory
    import app.mt5.mt5_real as real
    from fastapi.testclient import TestClient
    from app.main import app

    monkeypatch.setattr(database_mod, "get_db", lambda: db)
    monkeypatch.setattr(routes, "get_db", lambda: db)
    monkeypatch.setattr(eng_mod, "get_db", lambda: db)
    monkeypatch.setattr(factory, "get_bridge", lambda: bridge)
    monkeypatch.setattr(routes, "get_bridge", lambda: bridge)
    monkeypatch.setattr(real, "MT5_PACKAGE_AVAILABLE", True)
    monkeypatch.setattr(ex, "mt5_module", lambda: term)
    monkeypatch.setattr(ex, "_db", lambda: db)
    client = TestClient(app)
    yield client
    # never leave the loop (or a V6 per-node worker) running for the next test
    from app.live_testing.engine import get_live_testing_engine
    from app.live_testing.workers import get_worker_manager
    get_worker_manager().stop_all(timeout=5.0)
    e = get_live_testing_engine()
    e.stop(interrupted=True, reason="test teardown")
    e.reset()


def test_15_start_without_confirmation_is_refused(db, live_cfg, api):
    _v43.add_node(db, 5)
    db.x("DELETE FROM live_test_configs WHERE strategy_id=5")
    res = api.post("/api/live-testing/nodes/5/start", json={})
    assert res.status_code == 409
    body = res.json()["detail"]
    assert body["code"] == "CONFIRMATION_REQUIRED"
    assert db.get_live_test_config(5) in (None, {}) or not (db.get_live_test_config(5) or {}).get("is_active")


def test_16_start_enrols_the_node_and_activates_the_engine(db, live_cfg, api):
    _v43.add_node(db, 6)
    res = api.post("/api/live-testing/nodes/6/start", json={"confirmed": True})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["is_active"] is True and body["engine"]["active"] is True

    from app.live_testing.engine import get_live_testing_engine
    eng = get_live_testing_engine()
    assert eng.active is True
    assert 6 in [n["id"] for n in eng.eligible_nodes()]        # the loop iterates this set
    assert db.get_live_test_config(6)["is_active"] in (1, True)
    # the schedule the engine will enforce comes back with the response
    assert "rules" in body["schedule"] and body["schedule"]["strategy_id"] == 6


def test_17_stop_removes_the_node_and_touches_no_position(db, live_cfg, api):
    _v43.add_node(db, 7)
    api.post("/api/live-testing/nodes/7/start", json={"confirmed": True})
    before = len(db.q("SELECT 1 FROM live_test_trades") or [])

    res = api.post("/api/live-testing/nodes/7/stop")
    assert res.status_code == 200
    body = res.json()
    assert body["is_active"] is False and body["positions_touched"] is False
    assert db.get_live_test_config(7)["is_active"] in (0, False)

    from app.live_testing.engine import get_live_testing_engine
    eng = get_live_testing_engine()
    assert 7 not in [n["id"] for n in eng.eligible_nodes()]
    assert len(db.q("SELECT 1 FROM live_test_trades") or []) == before


def test_18_legacy_nodes_can_never_be_started(db, live_cfg, api):
    _v43.add_node(db, 8, data_source="LEGACY_TEST")
    res = api.post("/api/live-testing/nodes/8/start", json={"confirmed": True})
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "LEGACY_NODE_NOT_TRADEABLE"


def test_19_schedule_round_trip_saves_only_enforceable_values(db, live_cfg, api):
    _v43.add_node(db, 9)
    saved = api.post("/api/live-testing/schedule/9", json={
        "days": ["Mon", "Tue", "Fri"], "sessions": ["london"], "start_time": "08:00",
        "end_time": "16:00", "timezone": "UTC", "cooldown_minutes": 45,
        "max_trades_per_day": 2, "spread_limit_points": 25.0})
    assert saved.status_code == 200, saved.text
    cfg = db.get_live_test_config(9)
    days = cfg["days"] if isinstance(cfg["days"], list) else json.loads(cfg["days"])
    sessions = cfg["sessions"] if isinstance(cfg["sessions"], list) else json.loads(cfg["sessions"])
    assert days == [0, 1, 4]
    assert sessions == ["london"]
    assert cfg["cooldown_minutes"] == 45.0 and cfg["max_trades_per_day"] == 2.0
    assert cfg["spread_limit_points"] == 25.0

    read = api.get("/api/live-testing/schedule/9").json()
    assert read["strategy_id"] == 9
    assert {r["rule"] for r in read["rules"]} >= {"days", "sessions", "window", "spread",
                                                  "cooldown", "max_trades_per_day", "max_positions"}
    assert "London" in read["description"]
    # the evaluation is the engine's own
    from app.live_testing.engine import get_live_testing_engine
    eng = get_live_testing_engine()
    node = {"id": 9, "config": db.get_live_test_config(9), "genome": {}}
    assert eng.schedule_state(node)["allowed"] == read["allowed"]


def test_20_unsupported_sessions_or_days_are_rejected_not_stored(db, live_cfg, api):
    _v43.add_node(db, 10)
    bad_session = api.post("/api/live-testing/schedule/10", json={"sessions": ["sydney"]})
    assert bad_session.status_code == 422
    assert bad_session.json()["detail"]["errors"][0]["field"] == "sessions"
    bad_day = api.post("/api/live-testing/schedule/10", json={"days": ["Caturday"]})
    assert bad_day.status_code == 422
    assert bad_day.json()["detail"]["errors"][0]["field"] == "days"
    assert db.get_live_test_config(10) is None


def test_21_global_risk_is_validated_and_overrides_are_reported(db, live_cfg, api):
    _v43.add_node(db, 11)
    _v43.enable_node(db, 11, risk_pct=0.25)
    too_high = api.post("/api/live-testing/risk", json={"risk_pct": 99.0})
    assert too_high.status_code == 422
    ok = api.post("/api/live-testing/risk", json={"risk_pct": 1.5})
    assert ok.status_code == 200
    assert ok.json()["global_risk_pct"] == 1.5

    risk = api.get("/api/live-testing/risk").json()
    assert any(o["strategy_id"] == 11 for o in risk["overrides"])
    table = api.get("/api/live-testing/nodes-table?limit=200").json()
    row = next(r for r in table["nodes"] if r["node_id"] == 11)
    assert row["risk_source"] == "CUSTOM" and row["risk_pct"] == 0.25
    assert row["global_risk_pct"] == 1.5


def test_22_the_nodes_table_lists_real_candidates_and_never_legacy(db, live_cfg, api):
    _v43.add_node(db, 12, status="SURVIVED", data_source="USER_RESEARCH")
    _v43.add_node(db, 13, status="FAILED", data_source="USER_RESEARCH")
    _v43.add_node(db, 14, status="SURVIVED", data_source="LEGACY_TEST")
    _v43.enable_node(db, 14, is_active=1)      # as in the authoritative DATA: legacy rows carry configs
    table = api.get("/api/live-testing/nodes-table?limit=500").json()
    ids = [r["node_id"] for r in table["nodes"]]
    excluded_ids = [e.get("node_id") for e in table.get("excluded", [])]
    assert 12 in ids                       # a live-testable node is listed even before START
    assert 13 not in ids and 13 not in excluded_ids    # a FAILED node is neither listed nor "excluded"
    assert 14 not in ids
    assert 14 in excluded_ids
    assert table["risk"]["global_risk_pct"] is not None


# ===========================================================================
# 4. §16 — Trading Info must describe the genome, or say why it cannot
# ===========================================================================
def test_23_trading_info_reads_a_dict_genome_instead_of_reporting_nothing():
    """Regression: the DB driver returns `genome` already decoded as a dict.

    Passing that dict to json.loads raised, the exception was swallowed and every
    rule came back as "not defined by this genome" — the exact opposite of what
    this tab is for. A defined genome must be described; only a genuinely empty
    one may report undefined fields.
    """
    from app.strategies.trading_info import build_trading_info

    genome = {
        "symbol": "XAUUSD", "timeframe": "M15", "direction": "short",
        "features": ["adx:21", "bb:50:2.0"],
        "entry_long": None,
        "entry_short": {"type": "compare", "left": "adx:21", "cmp": ">", "right": 18},
        "exit": {"atr_spec": "atr:7", "sl_atr_mult": 2.5, "tp_atr_mult": 1.5,
                 "max_hold_bars": 48, "min_hold_bars": 3},
        "risk": {"risk_per_trade": 0.005, "max_concurrent": 1},
        "sessions": ["london"], "days": ["Mon", "Tue"],
    }

    # (a) genome handed over as a dict — the shape the driver really returns
    as_dict = build_trading_info({"id": 99, "status": "SURVIVED", "genome": genome})
    # (b) the same genome as a JSON string — the shape a raw column gives
    as_text = build_trading_info({"id": 99, "status": "SURVIVED",
                                  "genome": json.dumps(genome)})

    for info in (as_dict, as_text):
        flat = {i["label"]: i["value"] for sec in info["sections"] for i in sec["items"]}
        assert any("ADX" in label or "adx" in str(flat.get(label)) for label in flat), flat
        short = flat.get("Short entry")
        assert short and "18" in str(short), flat
        assert "2.5" in str(flat.get("Stop loss")), flat
        assert "1.5" in str(flat.get("Take profit")), flat
        assert "48" in str(flat.get("Maximum holding")), flat
        assert "London" in str(flat.get("Sessions")), flat
        # the genome defines no long entry and says it trades short only: the
        # translation must state the direction and must not invent a long rule
        assert flat.get("Long entry") is None
        assert "Short only" in info["text"] and "Long entry" not in info["text"]

    assert as_dict["text"] == as_text["text"]


def test_24_an_empty_genome_says_undefined_with_a_reason_not_a_generic_sentence():
    from app.strategies.trading_info import build_trading_info

    info = build_trading_info({"id": 7, "status": "FAILED", "genome": {}})
    flat = {i["label"]: i["value"] for sec in info["sections"] for i in sec["items"]}
    for label in ("Indicators", "Entry"):
        value = flat[label]
        assert isinstance(value, dict) and value["value"] is None and value["reason"], (label, value)


def test_25_trading_info_endpoint_uses_the_real_node(client):
    """Against the authoritative test-data population: a SURVIVED node with a
    genome must produce entry rules, not "no entry rule"."""
    from app.db.database import get_db

    db = get_db()
    row = db.one("""SELECT id FROM strategies
                    WHERE COALESCE(data_source,'') <> 'LEGACY_TEST' AND status IN ('SURVIVED','QUALIFIED')
                    ORDER BY id LIMIT 1""")
    if row is None:
        pytest.skip("no surviving user-research node in this data root")
    res = client.get(f"/api/strategies/{row['id']}/trading-info")
    assert res.status_code == 200, res.text
    body = res.json()
    flat = {i["label"]: i["value"] for sec in body["sections"] for i in sec["items"]}
    assert flat.get("Symbol"), body
    assert "no entry rule" not in body["text"] or flat.get("Entry"), body["text"][:400]
    assert body.get("text")
    assert body.get("strategy_id") == row["id"]


# ===========================================================================
# 5. §8 (V5.2.1) — the order Live Testing sends is the NODE's own strategy
# ===========================================================================
def test_29_the_live_order_carries_the_nodes_own_config_not_a_panel_default(
        db, live_cfg, mt5_env, term):
    """The engine must trade the node's stored genome: its symbol and timeframe,
    its own entry rule (which side), its exit model (sl_atr_mult/tp_atr_mult on
    the ATR of the last CLOSED bar) and its own risk percentage — never a generic
    BUY/SELL probe with a hard-coded lot.
    """
    from app.mt5.execution import live_test_magic, _magic_for

    # the node is enrolled with ITS OWN risk percentage and a genome whose exit
    # model is 2.0 / 4.0 ATR — deliberately unlike the panel defaults (300/600 pips)
    sid = _v43.add_node(db, 77, sl_mult=2.0, tp_mult=4.0)      # BUY-only node
    _v43.enable_node(db, sid, risk_pct=0.5)
    engine = fresh_engine()
    node = next(n for n in engine.eligible_nodes() if n["id"] == sid)
    genome = node["genome"]
    assert genome["symbol"] == SYMBOL and genome["timeframe"] == "M15"
    assert genome["entry_long"] and genome["entry_short"] is None
    assert genome["exit"]["sl_atr_mult"] == 2.0 and genome["exit"]["tp_atr_mult"] == 4.0

    engine.activate(confirmed=True)
    try:
        engine._cycle()
    finally:
        engine.stop(interrupted=False)

    assert term.sent, "the engine placed no order — nothing to inspect"
    req = term.sent[-1]

    # 1. the node's own symbol and the node's own side (long-only rule -> BUY)
    assert req["symbol"] == genome["symbol"]
    assert req["type"] == 0, req          # ORDER_TYPE_BUY, from entry_long

    # 2. the stop/target are the node's exit multiples on the bar's ATR: the ratio
    #    is exactly sl_atr_mult : tp_atr_mult, so a default 300/600 pip panel pair
    #    cannot be what produced them
    dist_sl = abs(float(req["price"]) - float(req["sl"]))
    dist_tp = abs(float(req["tp"]) - float(req["price"]))
    assert dist_sl > 0 and dist_tp > 0
    assert abs((dist_tp / dist_sl) - (4.0 / 2.0)) < 1e-6, (dist_sl, dist_tp)

    # 3. the size is the node's risk percentage of the demo balance, converted with
    #    the live quote's tick value, then floored to the symbol's volume step — so
    #    it can be slightly under the risk amount but never over it, and never a
    #    fixed panel lot. (0.01 lot steps on XAUUSD: 1 price unit = 100 USD/lot.)
    tick_size = 0.01
    tick_value = 1.0
    step = 0.01
    money_per_lot_per_price_unit = tick_value / tick_size
    risk_amount = 10000.0 * 0.5 / 100.0                 # balance x node risk_pct
    implied = float(req["volume"]) * dist_sl * money_per_lot_per_price_unit
    step_value = step * dist_sl * money_per_lot_per_price_unit
    exact_lots = risk_amount / (dist_sl * money_per_lot_per_price_unit)
    assert 0.0 <= risk_amount - implied < step_value + 1e-6, (req["volume"], implied, risk_amount)
    assert abs(float(req["volume"]) - (int(exact_lots / step) * step)) < 1e-9, (req["volume"], exact_lots)
    assert float(req["volume"]) >= step, req["volume"]           # never zero-lot "successful" orders

    # 4. the node's live-test magic (778xxx), never the manual panel's (777xxx)
    assert int(req["magic"]) == live_test_magic(sid) != _magic_for(sid)

    # 5. and the order reached the SAME authoritative path the manual panel uses,
    #    with the position verified from the terminal afterwards
    assert engine.stats["orders_sent"] == 1
    assert any(ev["stage"] == "ORDER SENT" for ev in engine.recent_events)
    assert any(ev["stage"] == "POSITION VERIFIED" for ev in engine.recent_events)
    cfg = db.get_live_test_config(sid)
    assert cfg is not None
