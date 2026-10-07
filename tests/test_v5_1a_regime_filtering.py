"""V5.1a §5-§7 — regime filtering on the live schedule.

Verified here, end to end and in the domain layer (never the frontend):

1. **Vocabulary** — the regimes the editor may offer are exactly the ones the
   research engine already defines (`REGIME_NAMES`). Nothing is invented and the
   two cannot drift apart.
2. **Configuration** — multi-select is preserved, names are canonicalised, an
   empty selection keeps the existing "no regime restriction" meaning (it never
   silently blocks everything), and `normalize_config` stays idempotent.
3. **Validation** — an unknown regime is rejected with a field-level error by the
   validator *and* by the save API.
4. **Enforcement** — `evaluate()` allows when the current regime is in the
   selected set and blocks when it is not, with the reason and the rule trace
   naming the regime; the historical `bar_mask()` honours the same rule and
   refuses to pretend when no labels were supplied.
5. **Persistence** — a regime set saved through the API survives a reload and a
   backend restart (fresh handle on the same database), and the engine reads it.

No DATA file is touched: every test writes to a temporary SQLite database.
"""
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

from app.live_testing import schedule as sched


def _load(name: str):
    p = Path(__file__).resolve().parent / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_v5live = _load("test_v5_live_testing.py")
_v43 = _v5live._v43


def ts(weekday: int, hour: int, minute: int = 0) -> float:
    """A UTC timestamp on a recent day whose weekday is ``weekday`` (0=Monday)."""
    now = dt.datetime.now(dt.timezone.utc)
    delta = (weekday - now.weekday()) % 7
    day = (now + dt.timedelta(days=delta)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return day.timestamp()


ALL_DAYS = [0, 1, 2, 3, 4, 5, 6]


# ===========================================================================
# 1. the vocabulary is the engine's, not a new one
# ===========================================================================
def test_01_the_regime_vocabulary_is_the_research_engine_vocabulary():
    from app.backtest.engine import REGIME_NAMES
    assert sched.supported_regimes() == list(REGIME_NAMES)
    opts = sched.supported_options()
    values = [o["value"] if isinstance(o, dict) else o for o in opts["regimes"]]
    assert values == list(REGIME_NAMES), values
    # every option carries a human label for the editor
    assert all((o.get("label") if isinstance(o, dict) else True) for o in opts["regimes"])
    # and it is a real classification, not a placeholder list
    assert len(REGIME_NAMES) >= 5
    assert "trending" in REGIME_NAMES and "ranging" in REGIME_NAMES


def test_02_the_editor_options_come_from_the_backend_not_from_the_component():
    """The schedule modal renders `options.regimes`; that key must be the engine's
    list, so the UI can never offer a label the backend cannot enforce."""
    options = sched.supported_options()
    values = [o["value"] if isinstance(o, dict) else o for o in options["regimes"]]
    assert set(values) == set(sched.supported_regimes())
    assert all(isinstance(r, str) and r == r.lower() for r in values)


# ===========================================================================
# 2. configuration: multi-select, canonical form, empty = unrestricted
# ===========================================================================
def test_03_multi_select_is_preserved_and_canonicalised():
    cfg = sched.normalize_config({"regimes": ["Trending", "HIGH_VOLATILITY", " trending "]})
    assert cfg["regimes"] == ["trending", "high_volatility"]

    # order is the operator's, duplicates are dropped
    assert sched.normalize_config({"regimes": ["ranging", "trending", "ranging"]})["regimes"] == \
        ["ranging", "trending"]


def test_04_an_empty_selection_means_no_restriction_never_block_everything():
    for empty in ([], None, "", "[]"):
        cfg = sched.normalize_config({"days": ALL_DAYS, "regimes": empty})
        assert not cfg["regimes"], empty
        ev = sched.evaluate(cfg, now=ts(2, 10), regime="breakout")
        assert ev["allowed"] is True, (empty, ev)
        trace = {r["rule"]: r for r in ev["rules"]}
        assert trace["regimes"]["ok"] is True
        assert "no regime restriction" in trace["regimes"]["detail"]


def test_05_normalize_config_is_idempotent_with_regimes():
    raw = {"days": [0, 2], "sessions": ["london"], "regimes": ["trending", "ranging"],
           "start_time": "07:00", "end_time": "16:00"}
    once = sched.normalize_config(raw)
    twice = sched.normalize_config(once)
    thrice = sched.normalize_config(twice)
    assert once == twice == thrice, (once, twice)
    assert once["regimes"] == ["trending", "ranging"]


def test_06_the_other_groups_still_work_alongside_regimes():
    """Adding regimes must not disturb days/sessions/window/enabled."""
    cfg = sched.normalize_config({"days": ["Mon", "Tue"], "sessions": ["london"],
                                  "regimes": ["trending"], "start_time": "08:00",
                                  "end_time": "12:00", "timezone": "UTC", "enabled": True})
    assert cfg["days"] == [0, 1] and cfg["sessions"] == ["london"]
    assert cfg["enabled"] is True and cfg["enabled_explicit"] is True
    assert cfg["windows"][0]["start"] == "08:00"
    # a Friday instant is still blocked by days, whatever the regime
    assert sched.evaluate(cfg, now=ts(4, 9), regime="trending")["allowed"] is False


# ===========================================================================
# 3. validation: unknown regimes are rejected, not guessed
# ===========================================================================
def test_07_an_unknown_regime_is_rejected_with_the_supported_list():
    errors = sched.validate_config({"regimes": ["trending", "sideways_unicorn"]})
    assert errors, "an unknown regime was accepted"
    assert any(e.get("field") == "regimes" for e in errors), errors
    msg = " ".join(e.get("error", "") for e in errors)
    assert "sideways_unicorn" in msg and "supported" in msg

    assert sched.validate_config({"regimes": ["trending", "ranging"]}) == []


def test_08_case_and_whitespace_do_not_create_a_false_rejection():
    assert sched.validate_config({"regimes": ["TReNDing", " High_Volatility "]}) == []


# ===========================================================================
# 4. enforcement in the domain layer
# ===========================================================================
def test_09_evaluate_allows_when_the_current_regime_is_selected():
    cfg = {"days": ALL_DAYS, "regimes": ["trending", "breakout"]}
    ev = sched.evaluate(cfg, now=ts(2, 10), regime="trending")
    assert ev["allowed"] is True and ev["reason"] is None
    trace = {r["rule"]: r for r in ev["rules"]}
    assert trace["regimes"]["ok"] is True
    assert "trending" in trace["regimes"]["detail"]


def test_10_evaluate_blocks_when_the_current_regime_is_not_selected():
    cfg = {"days": ALL_DAYS, "regimes": ["trending", "breakout"]}
    ev = sched.evaluate(cfg, now=ts(2, 10), regime="ranging")
    assert ev["allowed"] is False
    assert ev["reason"].startswith("regimes:"), ev["reason"]
    assert "ranging" in ev["reason"]
    trace = {r["rule"]: r for r in ev["rules"]}
    assert trace["regimes"]["ok"] is False


def test_11_any_one_of_the_selected_regimes_is_enough():
    cfg = {"days": ALL_DAYS, "regimes": ["trending", "ranging", "compression"]}
    for regime in ("trending", "ranging", "compression"):
        assert sched.evaluate(cfg, now=ts(2, 10), regime=regime)["allowed"] is True, regime
    assert sched.evaluate(cfg, now=ts(2, 10), regime="expansion")["allowed"] is False


def test_12_a_restricted_schedule_without_a_supplied_regime_says_so_in_the_trace():
    """Existing semantics: the rule is reported as *not evaluated* rather than
    pretended — and the historical path refuses outright (test 14)."""
    cfg = {"days": ALL_DAYS, "regimes": ["trending"]}
    ev = sched.evaluate(cfg, now=ts(2, 10), regime=None)
    trace = {r["rule"]: r for r in ev["rules"]}
    assert "not supplied" in trace["regimes"]["detail"]
    # days/sessions still decide on their own
    assert sched.evaluate({"days": [0, 1, 2, 3, 4], "regimes": ["trending"]},
                          now=ts(5, 10))["allowed"] is False


def test_13_the_regime_rule_is_reported_next_to_the_other_rules():
    cfg = {"days": ALL_DAYS, "regimes": ["trending"]}
    names = [r["rule"] for r in sched.evaluate(cfg, now=ts(2, 10), regime="ranging")["rules"]]
    assert "days" in names and "sessions" in names and "regimes" in names


def test_14_historical_masking_honours_the_regime_and_refuses_to_pretend():
    ts_arr = np.array([ts(2, 10) + i * 900 for i in range(8)], dtype=np.float64)
    down = np.array([2] * 8)
    labels = np.array(["trending", "ranging", "trending", "compression",
                       "breakout", "trending", "ranging", "expansion"], dtype=object)
    cfg = {"days": ALL_DAYS, "regimes": ["trending"]}
    mask = sched.bar_mask(cfg, ts=ts_arr, dow=down, regime=labels)
    assert [bool(x) for x in mask] == [True, False, True, False, False, True, False, False]

    # multi-select
    cfg2 = {"days": ALL_DAYS, "regimes": ["trending", "ranging"]}
    mask2 = sched.bar_mask(cfg2, ts=ts_arr, dow=down, regime=labels)
    assert [bool(x) for x in mask2] == [True, True, True, False, False, True, True, False]

    # restricting regimes with no labels supplied must fail loudly
    with pytest.raises(ValueError):
        sched.bar_mask(cfg, ts=ts_arr, dow=down, regime=None)

    # no regime restriction => every bar survives
    assert int(sched.bar_mask({"days": ALL_DAYS}, ts=ts_arr, dow=down, regime=None).sum()) == 8


# ===========================================================================
# 5. persistence through the API, and after a restart
# ===========================================================================
@pytest.fixture
def db(tmp_path):
    from app.db.database import Database
    d = Database(str(tmp_path / "regime_schedule.db"))
    d.x("DELETE FROM live_test_configs")
    return d


@pytest.fixture
def client(db, monkeypatch):
    import app.db.database as database_mod
    from fastapi.testclient import TestClient
    import app.api.routes as routes
    from app.main import app

    monkeypatch.setattr(database_mod, "get_db", lambda: db)
    monkeypatch.setattr(routes, "get_db", lambda: db)
    _v43.add_node(db, 21)
    return TestClient(app)


def test_15_the_saved_regime_set_is_returned_canonically_by_the_api(client, db):
    payload = {"days": ["Mon", "Tue", "Wed"], "sessions": ["london"],
               "regimes": ["Trending", "high_volatility"], "timeframes": ["M15"],
               "start_time": "07:00", "end_time": "16:00", "timezone": "UTC"}
    res = client.post("/api/live-testing/schedule/21", json=payload)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body.get("ok") is True, body
    assert body["config"]["regimes"] == ["trending", "high_volatility"], body["config"]
    # …and the row itself carries the canonical (lower-case) list
    row = db.get_live_test_config(21)
    assert row["regimes"] == ["trending", "high_volatility"], row

    got = client.get("/api/live-testing/schedule/21").json()
    assert got["config"]["regimes"] == ["trending", "high_volatility"], got


def test_16_an_unknown_regime_is_refused_by_the_api():
    from app.db.database import Database
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(str(Path(tmp) / "reject.db"))
        db.x("DELETE FROM live_test_configs")
        _v43.add_node(db, 22)
        import app.db.database as database_mod
        import app.api.routes as routes
        from fastapi.testclient import TestClient
        from app.main import app
        orig_db, orig_routes = database_mod.get_db, routes.get_db
        database_mod.get_db = lambda: db
        routes.get_db = lambda: db
        try:
            c = TestClient(app)
            res = c.post("/api/live-testing/schedule/22",
                         json={"days": ["Mon"], "regimes": ["trending", "not_a_regime"]})
            assert res.status_code == 422, res.text
            detail = res.json()["detail"]
            assert detail.get("code") in ("SCHEDULE_INVALID", None)
            text = json.dumps(detail)
            assert "not_a_regime" in text
        finally:
            database_mod.get_db, routes.get_db = orig_db, orig_routes


def test_17_a_saved_regime_set_survives_a_backend_restart(client, db):
    """The schedule is persisted in the database, not in process memory: a new
    Database handle (what a restart creates) reads the same regime set, and the
    engine's own reader sees it too."""
    client.post("/api/live-testing/schedule/21",
                json={"days": ["Mon", "Tue", "Wed"], "regimes": ["trending", "expansion"]})

    from app.db.database import Database
    path = getattr(db, "path", None) or getattr(db, "db_path", None) or str(db._path)
    fresh = Database(str(path))                      # what a backend restart creates
    row = fresh.get_live_test_config(21)
    assert row is not None
    cfg = row
    assert cfg["regimes"] == ["trending", "expansion"], cfg

    # and the engine's evaluator accepts the reloaded row unchanged
    sched_cfg = sched.normalize_config(cfg)
    assert sched_cfg["regimes"] == ["trending", "expansion"]
    assert sched.evaluate(sched_cfg, now=ts(2, 10), regime="expansion")["allowed"] is True
    assert sched.evaluate(sched_cfg, now=ts(2, 10), regime="ranging")["allowed"] is False


def test_18_the_engine_uses_the_saved_regimes_not_a_default(db, monkeypatch):
    """The live engine's schedule_state() is the gate evaluated before every
    order; it must apply the regime set the operator saved, with no default of
    its own."""
    import app.db.database as database_mod
    from app.live_testing.engine import LiveTestingEngine

    cfg = {"is_active": True, "timeframes": ["M15"], "days": ALL_DAYS, "regimes": ["trending"]}
    _v43.add_node(db, 23)
    db.set_live_test_config(23, cfg)
    monkeypatch.setattr(database_mod, "get_db", lambda: db)

    engine = LiveTestingEngine()
    engine.reset()
    node = {"id": 23, "config": db.get_live_test_config(23), "symbol": "XAUUSD"}

    allowed = engine.schedule_state(node, spread_points=None, regime="trending", open_positions=0)
    blocked = engine.schedule_state(node, spread_points=None, regime="ranging", open_positions=0)
    assert allowed["allowed"] is True, allowed
    assert blocked["allowed"] is False, blocked
    assert "regime" in (blocked["reason"] or "").lower()
    trace = {r["rule"]: r for r in blocked["rules"]}
    assert trace["regimes"]["ok"] is False
    # the row the operator saved is the row the engine read
    assert node["config"]["regimes"] == ["trending"]

    # and an empty selection on the same node leaves it unrestricted
    db.set_live_test_config(23, {**cfg, "regimes": []})
    node["config"] = db.get_live_test_config(23)
    assert engine.schedule_state(node, regime="ranging", open_positions=0)["allowed"] is True
