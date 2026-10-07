"""V5.2 §16 — the lab's schedule system applied to MT5 Demo Trading.

The operator asked for the *existing* schedule system (days, sessions, trading
windows with a timezone, regime filters, per-day trade caps, cooldowns, spread
limits) on the MT5 Demo Trading page as well: persisted, surviving a restart,
visible and editable, and actually affecting eligibility/execution.

This module is a thin adapter and nothing more:

  * **one evaluator** — everything is validated and evaluated by
    :mod:`app.live_testing.schedule` (``normalize_config``, ``validate_config``,
    ``evaluate``), the same code the Live Testing engine enforces. There is no
    second scheduler and no second regime vocabulary.
  * **persistence** — the normalized JSON is stored on the row itself
    (``mt5_demo_configs.schedule_json``), so it survives a backend restart and is
    read back verbatim.
  * **enforcement** — :func:`gate` is called before a demo node is allowed to be
    activated/started and before a schedule-aware demo execution runs. A node
    outside its schedule is reported as ``SCHEDULE_BLOCKED`` with the evaluator's
    own reason; it is never silently enabled.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional, Tuple

log = logging.getLogger("mt5.demo_schedule")

#: the regime labels the ONE regime classifier can report (never a UI vocabulary)
def supported_regimes():
    from ..live_testing.schedule import supported_regimes as _r
    return _r()


def supported_timeframes():
    from ..live_testing.schedule import supported_timeframes as _t
    return _t()


def _db(db: Any = None):
    if db is not None:
        return db
    from ..db.database import get_db
    return get_db()


def _ensure_column(db: Any) -> None:
    """Add ``schedule_json`` to ``mt5_demo_configs`` once (migration-safe)."""
    try:
        cols = [r[1] for r in db._conn.execute("PRAGMA table_info(mt5_demo_configs)").fetchall()]
    except Exception:
        return
    if "schedule_json" not in cols:
        try:
            db.x("ALTER TABLE mt5_demo_configs ADD COLUMN schedule_json TEXT")
            log.info("mt5_demo_configs.schedule_json added")
        except Exception as e:                                  # pragma: no cover - defensive
            log.warning("could not add mt5_demo_configs.schedule_json: %s", e)


def _genome_rows(db: Any) -> Dict[int, Dict[str, Any]]:
    out: Dict[int, Dict[str, Any]] = {}
    try:
        for r in db.q("SELECT id, genome FROM strategies") or []:
            g = r.get("genome")
            if isinstance(g, str):
                try:
                    g = json.loads(g)
                except Exception:
                    g = {}
            out[int(r["id"])] = g if isinstance(g, dict) else {}
    except Exception:
        pass
    return out


def get_schedule(sid: int, db: Any = None) -> Dict[str, Any]:
    """The stored (normalized) schedule of a demo node, plus its evaluation."""
    from ..live_testing.schedule import describe, evaluate, normalize_config
    d = _db(db)
    _ensure_column(d)
    row = d.one("SELECT schedule_json FROM mt5_demo_configs WHERE strategy_id=?", (sid,)) or {}
    raw = row.get("schedule_json")
    sched = None
    if raw:
        try:
            sched = json.loads(raw)
        except Exception:
            sched = None
    if sched is None:
        return {"ok": True, "strategy_id": sid, "schedule": None, "configured": False,
                "description": describe(None), "evaluation": None,
                "note": "no schedule saved — the product default applies (Mon–Fri, all sessions)"}
    norm = normalize_config(sched)
    ev = evaluate(norm, now=time.time())
    return {"ok": True, "strategy_id": sid, "schedule": norm, "configured": True,
            "description": describe(norm), "evaluation": ev}


def save_schedule(sid: int, payload: Dict[str, Any], db: Any = None) -> Dict[str, Any]:
    """Validate + persist a demo node's schedule (same rules the engine enforces)."""
    from ..live_testing.schedule import (describe, evaluate, normalize_config,
                                         supported_options, validate_config)
    d = _db(db)
    _ensure_column(d)
    genome = _genome_rows(d).get(int(sid)) or {}
    body = dict(payload or {})
    norm = normalize_config(body)
    errors = validate_config(norm, genome=genome) or []
    if errors:
        return {"ok": False, "strategy_id": sid, "errors": errors,
                "error": "the schedule was not saved — fix the reported field(s) first",
                "options": supported_options(genome),
                "authority": "app.live_testing.schedule (the same evaluator Live Testing enforces)"}
    d.x("UPDATE mt5_demo_configs SET schedule_json=?, updated_at=? WHERE strategy_id=?",
        (json.dumps(norm, sort_keys=True), time.time(), sid))
    ev = evaluate(norm, now=time.time())
    return {"ok": True, "strategy_id": sid, "schedule": norm, "configured": True,
            "description": describe(norm), "evaluation": ev,
            "options": supported_options(genome),
            "authority": "app.live_testing.schedule (the same evaluator Live Testing enforces)"}


def clear_schedule(sid: int, db: Any = None) -> Dict[str, Any]:
    d = _db(db)
    _ensure_column(d)
    d.x("UPDATE mt5_demo_configs SET schedule_json=NULL, updated_at=? WHERE strategy_id=?",
        (time.time(), sid))
    return {"ok": True, "strategy_id": sid, "configured": False,
            "description": "no schedule saved — the product default applies (Mon–Fri, all sessions)"}


def gate(sid: int, *, spread_points: Optional[float] = None,
         regime: Optional[str] = None, open_positions: Optional[int] = None,
         db: Any = None) -> Dict[str, Any]:
    """May this demo node act *now*? The one enforcement point (§16).

    Returns the evaluator's own verdict. ``allowed`` is True when no schedule is
    configured (the product default) or when the schedule admits this moment.
    """
    from ..live_testing.schedule import evaluate, normalize_config
    d = _db(db)
    _ensure_column(d)
    row = d.one("SELECT schedule_json FROM mt5_demo_configs WHERE strategy_id=?", (sid,)) or {}
    raw = row.get("schedule_json")
    if not raw:
        return {"allowed": True, "reason": "no demo schedule configured — product default applies",
                "configured": False, "rules": {}, "schedule": None}
    try:
        sched = normalize_config(json.loads(raw))
    except Exception as e:
        # an unreadable schedule must never widen trading: refuse and say why
        return {"allowed": False, "configured": True, "schedule": None,
                "reason": f"the stored demo schedule could not be read ({type(e).__name__}) — "
                          "refusing to trade until it is re-saved",
                "rules": {}}
    kwargs: Dict[str, Any] = {"now": time.time(), "spread_points": spread_points,
                              "regime": regime}
    if open_positions is not None:
        kwargs["open_positions"] = int(open_positions)
    ev = evaluate(sched, **kwargs)
    return {**ev, "configured": True, "schedule": sched}


def status_rows(db: Any = None) -> Dict[int, Dict[str, Any]]:
    """Per-node schedule + live evaluation, for the MT5 Demo Trading page."""
    d = _db(db)
    _ensure_column(d)
    out: Dict[int, Dict[str, Any]] = {}
    try:
        rows = d.q("SELECT strategy_id, schedule_json FROM mt5_demo_configs")
    except Exception:
        return out
    from ..live_testing.schedule import describe
    for r in rows or []:
        sid = int(r["strategy_id"])
        raw = r.get("schedule_json")
        sched = None
        if raw:
            try:
                sched = json.loads(raw)
            except Exception:
                sched = None
        g = gate(sid, db=d)
        out[sid] = {"strategy_id": sid, "configured": bool(sched),
                    "schedule": g.get("schedule") if g.get("configured") else sched,
                    "description": (describe(sched) if sched else describe(None)),
                    "allowed": g.get("allowed"), "reason": g.get("reason"),
                    "rules": g.get("rules")}
    return out
