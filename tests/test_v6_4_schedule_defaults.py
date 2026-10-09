"""V6.4 #6 — schedule defaults come from provenance; nothing is invented.

The schedule shown before START must come from the node's original generation
config (genome) or its stored live-testing configuration. Days, sessions,
timeframes and windows that were never part of the node's research must NEVER
appear as defaults. The timezone is always stated. The schedule is editable and
persistable before START; START submits the selected schedule and the backend
enforces (persists for the engine's schedule evaluator) exactly what was
submitted — displayed == submitted == enforced. When provenance is unavailable
the START is refused with the reason and a deliberate selection is required.
"""
from __future__ import annotations

import json
import time

import pytest


def _add_node(db, node_id, genome):
    db.x("""INSERT OR REPLACE INTO strategies
            (id, hash, parent_id, generation, symbol, timeframe, direction, status,
             genome, complexity, fitness, created_at, updated_at, origin, run_id,
             data_source, research_node_num)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
         (node_id, f"sched-{node_id}", None, 2, "XAUUSD", "M15", "long", "QUALIFIED",
          json.dumps(genome), 3, 0.5, time.time(), time.time(), "research",
          "RUN-SCHED-TEST", "USER_RESEARCH", node_id))


FULL_GENOME = {"symbol": "XAUUSD", "timeframe": "M15",
               "entry_long": {"type": "compare", "left": "close", "cmp": ">", "right": 0},
               "days": [0, 2, 4], "sessions": ["london", "newyork"]}
BARE_GENOME = {"symbol": "XAUUSD", "timeframe": "M15",
               "entry_long": {"type": "compare", "left": "close", "cmp": ">", "right": 0}}


@pytest.fixture()
def db(tmp_path):
    from app.db.database import Database
    d = Database(str(tmp_path / "sched.db"))
    d.get_shortlist()
    d.x("DELETE FROM strategies")
    d.x("DELETE FROM live_test_configs")
    _add_node(d, 401, FULL_GENOME)     # genome provenance: days [0,2,4], london+newyork
    _add_node(d, 402, BARE_GENOME)     # no schedule provenance at all
    # 403: stored-config provenance (like the real lab's node 6658)
    _add_node(d, 403, BARE_GENOME)
    d.set_live_test_config(403, {"timeframes": ["M15"], "days": [0, 1, 2, 3, 4],
                                 "sessions": ["asia", "london"], "timezone": "UTC",
                                 "conditions": {"entry_long": True}, "enabled": True})
    return d


@pytest.fixture()
def api(monkeypatch, db):
    import app.api.routes as routes
    import app.db.database as database_mod
    import app.live_testing.engine as eng_mod
    import app.mt5.factory as factory
    from fastapi.testclient import TestClient
    from app.main import app

    class _Sim:
        is_simulated = True
        source = "SIMULATOR"

    class _Eng:
        def eligible_nodes(self):
            return [{"id": sid, "config": db.get_live_test_config(sid) or {}}
                    for sid in (401, 402, 403)]

        def get_mode(self):
            return {"active": True}

        def activate(self, **kw):
            return {"ok": True, "already_active": True}

        def schedule_state(self, node):
            return {"ok": True, "node_id": (node or {}).get("id")}

    monkeypatch.setattr(database_mod, "get_db", lambda: db)
    monkeypatch.setattr(routes, "get_db", lambda: db)
    monkeypatch.setattr(eng_mod, "get_db", lambda: db)
    monkeypatch.setattr(eng_mod, "get_live_testing_engine", lambda: _Eng())
    monkeypatch.setattr(factory, "get_bridge", lambda: _Sim())
    monkeypatch.setattr(routes, "get_bridge", lambda: _Sim())
    client = TestClient(app)
    yield client
    from app.live_testing.workers import get_worker_manager
    get_worker_manager().stop_all(timeout=5.0)


# --------------------------------------------------------------------------- #
# provenance: where defaults come from
# --------------------------------------------------------------------------- #
def test_01_defaults_come_from_the_genome_when_no_config_exists(api):
    d = api.get("/api/live-testing/nodes/401/schedule-provenance").json()
    assert d["ok"] is True
    prov = d["provenance"]
    assert prov["available"] is True
    assert prov["config"] is None, "no stored config for 401"
    assert prov["genome"]["days"] == [0, 2, 4]
    assert prov["genome"]["sessions"] == ["london", "newyork"]
    # the defaults shown are exactly the provenance — nothing invented
    assert d["defaults"]["days"] == [0, 2, 4]
    assert d["defaults"]["sessions"] == ["london", "newyork"]
    assert d["defaults"]["timeframes"] == ["M15"]          # the node's own timeframe
    assert d["timezone"] == "UTC"                          # timezone is always stated


def test_02_stored_config_provenance_wins_over_an_empty_genome(api):
    d = api.get("/api/live-testing/nodes/403/schedule-provenance").json()
    prov = d["provenance"]
    assert prov["config"]["sessions"] == ["asia", "london"]
    assert d["defaults"]["days"] == [0, 1, 2, 3, 4]
    assert d["defaults"]["sessions"] == ["asia", "london"]
    assert d["timezone"] == "UTC"


def test_03_without_provenance_the_defaults_state_the_gap(api):
    d = api.get("/api/live-testing/nodes/402/schedule-provenance").json()
    assert d["provenance"]["available"] is False
    assert d["provenance"]["config"] is None
    assert d["provenance"]["genome_schedule"] is None   # no days/sessions/windows anywhere
    # the node's own timeframe may still be shown as a fact — but it is NOT a schedule
    assert (d["provenance"]["genome"] or {}).get("timeframes") == ["M15"]
    assert "deliberate" in d["provenance"]["note"]


# --------------------------------------------------------------------------- #
# START: displayed == submitted == enforced
# --------------------------------------------------------------------------- #
def test_04_start_persists_exactly_what_was_submitted(api, db):
    submitted = {"days": [1, 3], "sessions": ["asia"], "timeframes": ["M15"],
                 "timezone": "UTC"}
    res = api.post("/api/live-testing/nodes/401/start",
                   json={"confirmed": True, "schedule": submitted}).json()
    assert res["ok"] is True
    # what the response says will be enforced == what was submitted
    assert res["schedule_resolved"]["days"] == [1, 3]
    assert res["schedule_resolved"]["sessions"] == ["asia"]
    assert res["schedule_timezone"] == "UTC"
    assert "operator selection" in res["schedule_source"]
    # and what the engine will enforce (the stored config) is the same object
    cfg = db.get_live_test_config(401)
    assert cfg["days"] == [1, 3]
    assert cfg["sessions"] == ["asia"]
    assert cfg["timezone"] == "UTC"


def test_05_start_without_a_schedule_uses_the_nodes_own_provenance(api, db):
    res = api.post("/api/live-testing/nodes/403/start", json={"confirmed": True}).json()
    assert res["ok"] is True
    assert "provenance" in res["schedule_source"]
    assert res["schedule_resolved"]["sessions"] == ["asia", "london"]   # NOT london+newyork
    assert res["schedule_timezone"] == "UTC"


def test_06_without_any_provenance_start_requires_a_deliberate_selection(api, db):
    r = api.post("/api/live-testing/nodes/402/start", json={"confirmed": True})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "SCHEDULE_REQUIRED"
    assert detail["provenance"]["available"] is False
    assert "deliberate" in detail["message"]
    # nothing was enrolled by the refusal
    assert not (db.get_live_test_config(402) or {}).get("is_active")


def test_07_the_documented_product_default_needs_an_explicit_confirmation(api, db):
    res = api.post("/api/live-testing/nodes/402/start",
                   json={"confirmed": True,
                         "schedule": {"confirm_product_default": True}}).json()
    assert res["ok"] is True
    assert "product default" in res["schedule_source"]
    assert res["schedule_resolved"]["timezone"] == "UTC"
    cfg = db.get_live_test_config(402)
    # the DOCUMENTED default (Mon-Fri), whatever day representation round-trips
    days = cfg.get("days") or []
    days_norm = sorted(("Mon", "Tue", "Wed", "Thu", "Fri").index(d) if isinstance(d, str) else d
                       for d in days)
    assert days_norm == [0, 1, 2, 3, 4]
    # "no restriction" means ALL sessions (the schedule module's documented
    # default) — never the invented [london, newyork] two-session fallback
    from app.live_testing.schedule import SUPPORTED_SESSIONS
    sess = list(cfg.get("sessions") or [])
    assert set(sess) <= set(SUPPORTED_SESSIONS)     # only real session names
    assert sess != ["london", "newyork"]              # never the invented two-session default
    assert len(sess) != 2 or set(sess) != {"london", "newyork"}


def test_08_no_invented_london_newyork_default_anywhere(api, db):
    """The pre-V6.4 fallback invented sessions [london, newyork] for unconfigured
    nodes — it must never appear again unless the node's provenance says so."""
    r = api.post("/api/live-testing/nodes/402/start", json={"confirmed": True})
    assert r.status_code == 409
    cfg = db.get_live_test_config(402) or {}
    assert cfg.get("sessions") not in (["london", "newyork"],)
    # and the schedule writer/reader agree the empty list is "no restriction"
    from app.live_testing import schedule as sched
    normalized = sched.normalize_config(cfg or {"strategy_id": 402})
    assert normalized.get("sessions") in (None, [])


def test_09_schedule_description_states_the_timezone(api):
    from app.live_testing import schedule as sched
    d = sched.normalize_config({"days": [0, 2], "sessions": ["london"], "timezone": "UTC"})
    text = sched.describe(d)
    assert "UTC" in text
