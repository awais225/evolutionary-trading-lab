"""V5.1a-next §D-§F — one authoritative population counter for the dashboard.

The operator asked for a single place that answers *"how many nodes are there,
really?"* and for the Deep Backtest page to select from a **union**, not from a
narrower accidental subset.

This module is that place.  It reads the strategy rows once and derives every
number with :func:`app.status.node_bucket` (the same authority the node filters
and the live engine use), so:

    total      every stored strategy row, legacy infrastructure included
    alive      not a dead end (status.is_alive) — never inflated by demoting
               infrastructure failures into "failed" or into "alive"
    qualified  cleared the research gates (bucket "qualified")
    final      qualified *and* at the furthest research stage (final candidate /
               live-completed / MT5-demo)
    deep       the deep-backtest universe: the UNION of everything the operator
               can legitimately deep-test (qualified + live-eligible + already
               deep-tested) — see :func:`deep_universe`
    live       §10-eligible: nodes the live layer can actually act on — the
               qualified nodes that PASS the engine's own tradeability predicate
               (:mod:`app.live_testing.eligibility`: not LEGACY_TEST, not
               dead/blocked, genome with a symbol and at least one entry rule).
               "Eligible" means CAPABILITY, not enrolment.
    live_active the nodes the live layer is ACTUALLY running right now: enrolled
               (an active live-test config, an enabled MT5-demo config or
               LIVE_TESTING status) AND accepted by the same tradeability
               predicate — i.e. exactly the set the engine would trade.  Kept as
               a SEPARATE number: before V5.2.2 the enrolment count was reported
               under the word "eligible", which made a lab where nobody had
               pressed START look like a lab where nothing qualifies.  Enrolled
               rows the engine refuses stay visible in ``detail.live_inputs``.

Nothing here writes to the database and nothing here invents rows: a value that
cannot be computed is reported as 0 with a reason in ``notes``.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Set

from ..status import (ALIVE_STATUSES, STATUS_LABELS, is_alive, node_bucket, v5_status)

log = logging.getLogger("research.populations")

#: word-for-word definitions, shipped with the numbers so the UI can show them
POPULATION_DEFINITIONS: Dict[str, str] = {
    "total": "Every strategy row in the authoritative database (legacy infrastructure rows included).",
    "alive": "Nodes that are not a dead end: they are still in research, valid, or already "
             "promoted to the live layers. Infrastructure failures are NOT counted as alive.",
    "qualified": "Nodes that cleared the research gates against real data "
                 "(status.node_bucket == 'qualified').",
    "final": "Qualified nodes at the furthest research stage: final candidates, live-completed "
             "or MT5-demo nodes.",
    "deep": "The deep-backtest universe: the union of qualified nodes, live-eligible nodes and "
            "nodes that already have a deep (mt5-historical) run.",
    "live": "Live-testing ELIGIBLE (capability): qualified nodes that pass the engine's own "
            "tradeability predicate (not LEGACY_TEST, not dead/blocked, genome with a symbol and "
            "at least one entry rule) — the nodes the START path can enrol.",
    "live_active": "Live-testing ACTIVE (enrolment): nodes the live layer is actually running right "
                   "now — enrolled (active live-test config, enabled MT5-demo configuration or "
                   "LIVE_TESTING status) AND accepted by the tradeability predicate. Enrolled rows "
                   "the engine refuses are reported separately, never counted here.",
    # V5.4 §1 — the rest of the partition, defined where the numbers are made
    "dead": "Every USER_RESEARCH node that is not alive: the failed, infrastructure-blocked and "
            "unrecognised-status rows (LEGACY_TEST rows are NOT part of DEAD — they are reported "
            "separately as legacy_excluded). DEAD = failed + blocked + unknown.",
    "failed": "Nodes that were genuinely judged against real data and did not clear the gates "
              "(status.node_bucket == 'failed'). A strategy failure, never an infrastructure one.",
    "blocked": "Nodes blocked BEFORE the strategy was judged (DATA_UNAVAILABLE / DATA_CORRUPT / "
               "BACKTEST_ERROR). Reported separately so an infrastructure problem is never shown "
               "as a strategy failure.",
    "unknown": "Nodes whose stored status is not part of the known vocabulary — reported as-is "
               "for investigation, never silently promoted to alive or failed.",
    "legacy_excluded": "LEGACY_TEST infrastructure rows that live in the same authoritative table. "
                       "They are part of TOTAL (nothing is hidden) and excluded from every "
                       "research number.",
    "user_research": "The current research experiment's nodes (TOTAL minus the LEGACY_TEST rows). "
                     "The research counters and the node lists work on this scope.",
    "in_flight": "Nodes currently being worked on (stored status BACKTESTING / TESTING / "
                 "VALIDATING). A subset of ALIVE — in flight is NOT dead.",
    "generation": "Highest generation recorded in the research population; 0 when nothing was "
                  "generated yet.",
    "validating": "Nodes the derived classifier reports as VALIDATING (the same classifier the node "
                  "filters use, so the count cannot disagree with the filter results).",
    "target": "The CURRENT EXPERIMENT's node ceiling (the configured target of the running "
              "experiment), never the whole stored population.",
    "remaining": "max(0, target - experiment nodes): how many nodes the current experiment may "
                 "still generate before its ceiling is reached.",
}

#: research stages that count as "final"
_FINAL_STAGES = {"FINAL_CANDIDATE", "LIVE_TESTED", "MT5_DEMO", "LIVE_COMPLETED", "FINAL", "APPROVED"}
_FINAL_STATUSES = {"LIVE_COMPLETED", "MT5_DEMO"}

#: the select list; one query, reused by every counter. ``pipeline_stage`` and
#: ``shortlisted`` exist only in databases that were migrated to the later
#: schema, so the query degrades to the columns this DATA actually has instead
#: of reporting 0 nodes (which would look like an empty lab).
_ROW_SQLS = (
    """SELECT id, status, data_source, pipeline_stage, symbol, timeframe, genome,
              shortlisted, failure_reason, creation_reason, survival_reason, generation,
              run_id, research_node_num
       FROM strategies""",
    """SELECT id, status, data_source, symbol, timeframe, genome,
              failure_reason, creation_reason, survival_reason, generation
       FROM strategies""",
    """SELECT id, status, data_source, symbol, timeframe, genome, generation FROM strategies""",
    """SELECT id, status, data_source, symbol, timeframe, genome FROM strategies""",
)


def load_rows(db: Any = None) -> List[Dict[str, Any]]:
    """All strategy rows (read-only). A DB error yields an empty list + a note."""
    if db is None:
        from ..db.database import get_db
        db = get_db()
    last_err = None
    for sql in _ROW_SQLS:
        try:
            rows = db.q(sql) or []
            return [dict(r) for r in rows]
        except Exception as e:
            last_err = e
    log.warning("population read failed: %s", last_err)
    return []


def _eligible_ids(db: Any) -> Set[int]:
    """The live engine's own eligibility verdict, when it can be computed."""
    try:
        from ..live_testing.engine import get_engine
        nodes = get_engine().eligible_nodes() or []
        return {int(n.get("id")) for n in nodes if n.get("id") is not None}
    except Exception as e:                       # pragma: no cover - optional
        log.debug("eligible-node computation unavailable: %s", e)
        return set()


def _deep_tested_ids(db: Any) -> Set[int]:
    try:
        rows = db.q("SELECT DISTINCT strategy_id FROM mt5_historical_runs") or []
    except Exception:
        return set()
    return {int(r["strategy_id"]) for r in rows if r.get("strategy_id") is not None}


def _live_ids(db: Any) -> Set[int]:
    out: Set[int] = set()
    for table, col in (("live_test_configs", "is_active"), ("mt5_demo_configs", "enabled")):
        try:
            rows = db.q(f"SELECT strategy_id FROM {table} WHERE COALESCE({col},0)=1") or []
            out |= {int(r["strategy_id"]) for r in rows if r.get("strategy_id") is not None}
        except Exception:
            continue
    return out


def _is_final(row: Dict[str, Any]) -> bool:
    stage = str(row.get("pipeline_stage") or "").strip().upper()
    if stage in _FINAL_STAGES:
        return True
    return v5_status(row) in _FINAL_STATUSES


def is_alive_user_node(row: Dict[str, Any]) -> bool:
    """§D — "alive" for a USER_RESEARCH node, without ever inflating the count.

    ``status.is_alive()`` translates an *unrecognised* stored status to NOT_TESTED
    (deliberately: an unknown value must not be called "failed") — which would
    count it as alive. For a population number that is the wrong direction: an
    unknown status must count as neither alive nor failed. The bucket decides
    that (``unknown``/``excluded`` are excluded here), and the count is therefore
    never inflated by rows the vocabulary cannot explain.
    """
    bucket = node_bucket(row)["bucket"]
    if bucket in ("unknown", "excluded"):
        return False
    return is_alive(row)


def has_genome_and_entry(row: Dict[str, Any]) -> bool:
    """Does this row carry a usable genome with at least one entry condition?"""
    import json
    g = row.get("genome")
    if not g:
        return False
    if isinstance(g, str):
        try:
            g = json.loads(g)
        except Exception:
            return False
    if not isinstance(g, dict):
        return False
    if not (g.get("symbol") and g.get("timeframe")):
        return False
    return bool(g.get("entry_long") or g.get("entry_short"))


def deep_universe(db: Any = None, rows: Optional[Iterable[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """§E — the union the Deep Backtest picker must offer.

    A node belongs to the union when ANY of these hold:

      * it is qualified by research (bucket ``qualified``);
      * the live engine currently accepts it as a candidate (``eligible_nodes``);
      * it already has a deep (mt5-historical) run, so re-running it is legitimate.

    LEGACY_TEST rows are never included (they are infrastructure records), and the
    ``why`` field records which rule(s) put the node in the union, so the UI can
    explain the selection instead of implying one single source.
    """
    rows = [dict(r) for r in (rows if rows is not None else load_rows(db))]
    if db is None:
        from ..db.database import get_db
        db = get_db()
    eligible = _eligible_ids(db)
    deep_done = _deep_tested_ids(db)

    union: List[Dict[str, Any]] = []
    for row in rows:
        src = str(row.get("data_source") or "").strip().upper()
        if src == "LEGACY_TEST":
            continue
        why: List[str] = []
        if node_bucket(row)["bucket"] == "qualified":
            why.append("qualified")
        if int(row.get("id") or -1) in eligible:
            why.append("live-eligible")
        if int(row.get("id") or -1) in deep_done:
            why.append("already deep-tested")
        if not why:
            continue
        union.append({
            "id": int(row["id"]),
            "symbol": row.get("symbol"),
            "timeframe": row.get("timeframe"),
            "status": v5_status(row),
            "status_label": STATUS_LABELS.get(v5_status(row), v5_status(row)),
            "bucket": node_bucket(row)["bucket"],
            "why": why,
            "has_genome": has_genome_and_entry(row),
        })
    union.sort(key=lambda r: r["id"])
    return union


def live_eligibility(rows: Optional[Iterable[Dict[str, Any]]] = None,
                     db: Any = None) -> Dict[str, Any]:
    """§10/V5.2.2 — how many nodes are LIVE-TESTING ELIGIBLE, and why not.

    Eligibility is CAPABILITY: a node is eligible when it is *qualified by
    research* AND the live engine's own tradeability predicate would accept it
    (:func:`app.live_testing.eligibility.tradeability`).  A qualified node does
    not need to be enrolled first — pressing START on the Live Testing page is
    what enrols it — so a fresh lab reports a real, non-zero candidate count
    instead of the enrolment count dressed up as eligibility.

    Returns the eligible ids plus a per-reason histogram of the qualified nodes
    that were rejected, so the UI can explain the number instead of printing it.
    """
    from ..live_testing.eligibility import tradeability

    rows = [dict(r) for r in (rows if rows is not None else load_rows(db))]
    eligible: List[Dict[str, Any]] = []
    rejected: Dict[str, int] = {}
    for row in rows:
        if str(row.get("data_source") or "").strip().upper() == "LEGACY_TEST":
            continue                                   # infrastructure, never a candidate
        if node_bucket(row)["bucket"] != "qualified":
            continue                                   # research gate not cleared yet
        verdict = tradeability(row)
        if verdict["ok"]:
            eligible.append({"id": int(row["id"]), "symbol": row.get("symbol"),
                             "timeframe": row.get("timeframe"), "status": v5_status(row)})
        else:
            key = verdict["reason"] or verdict["code"]
            rejected[key] = rejected.get(key, 0) + 1
    eligible.sort(key=lambda r: r["id"])
    return {"eligible": eligible, "count": len(eligible), "rejected": rejected}


#: §10 — the explicit lifecycle names every page must agree on. They are the
#: SAME six numbers as ``populations()``; the longer names exist so no page has to
#: invent its own meaning for "alive"/"eligible"/"final".
POPULATION_STATE_KEYS: Dict[str, str] = {
    "TOTAL": "total",
    "ALIVE": "alive",
    "QUALIFIED": "qualified",
    "FINAL_TESTING_ELIGIBLE": "final",
    "DEEP_TESTING_ELIGIBLE": "deep",
    "LIVE_TESTING_ELIGIBLE": "live",
    "LIVE_TESTING_ACTIVE": "live_active",
}

POPULATION_STATE_DEFINITIONS: Dict[str, str] = {
    "TOTAL": POPULATION_DEFINITIONS["total"],
    "ALIVE": POPULATION_DEFINITIONS["alive"],
    "QUALIFIED": POPULATION_DEFINITIONS["qualified"],
    "FINAL_TESTING_ELIGIBLE": (
        "Qualified nodes at the furthest research stage — the nodes Final Testing may "
        "select (final candidate / live-completed / MT5-demo)."),
    "DEEP_TESTING_ELIGIBLE": (
        "The deep-testing union: qualified ∪ live-eligible ∪ already deep-tested nodes "
        "(LEGACY_TEST infrastructure excluded)."),
    "LIVE_TESTING_ELIGIBLE": (
        "Nodes the live layer CAN act on (capability): qualified nodes that pass the engine's "
        "own tradeability predicate — not LEGACY_TEST, not dead/blocked, with a genome carrying "
        "a symbol and at least one entry rule. These are the nodes the Live Testing START path "
        "can enrol; being eligible does not mean the node is running."),
    "LIVE_TESTING_ACTIVE": (
        "Nodes the live layer is ACTUALLY running RIGHT NOW (enrolment + acceptance): an active "
        "live-test config, an enabled MT5-demo configuration or LIVE_TESTING status, AND accepted "
        "by the same tradeability predicate — exactly the nodes the engine would trade. A fresh "
        "lab is legitimately 0 here; enrolled rows the engine refuses are reported separately as "
        "excluded."),
}


#: V5.4 §1 follow-up — the LEGACY alias names a dashboard widget may still read
#: (`lab.dead_nodes`, `stage_state.qualified_nodes`, …). They were filled by the
#: engine's own raw-status formula, which is a DIFFERENT derivation from the
#: authority: on the live research database it reported DEAD 9991 / QUALIFIED 5
#: while Overview, Stats and Live Testing printed 9996 / 41 from the snapshot —
#: i.e. the exact "two panels, two numbers" defect V5.4 exists to remove. Every
#: alias below is therefore resolved from the ONE snapshot, and the engine's raw
#: value stays available, clearly named, for the diagnostics that want it.
AUTHORITATIVE_ALIASES: Dict[str, str] = {
    "alive_nodes": "ALIVE",
    "dead_nodes": "DEAD",
    "qualified_nodes": "QUALIFIED",
    "backtesting_nodes": "BACKTESTING",
    "validating_nodes": "VALIDATING",
    "target_nodes": "TARGET",
    "remaining_nodes": "REMAINING",
    "generation_number": "CURRENT_GENERATION",
}


def authoritative_aliases(state: Any) -> Dict[str, int]:
    """The legacy alias keys, resolved from a snapshot's ``state`` block.

    A missing snapshot never invents a number: an unknown alias is simply absent
    from the returned mapping, so the caller keeps whatever it had.
    """
    state = state if isinstance(state, dict) else {}
    out: Dict[str, int] = {}
    for alias, name in AUTHORITATIVE_ALIASES.items():
        value = state.get(name)
        if isinstance(value, int):
            out[alias] = value
    return out


#: V5.4 §1 — the COMPLETE state vocabulary. Every panel (Overview, PROGRESS, the
#: NODES strip, Deep Testing, Live Testing, summary cards) reads these names; the
#: six population keys are a subset kept for backwards compatibility.
NODE_STATE_KEYS: Dict[str, str] = {
    "TOTAL": "total",
    "ALIVE": "alive",
    "DEAD": "dead",
    "BACKTESTING": "in_flight",
    "VALIDATING": "validating",
    "QUALIFIED": "qualified",
    "FINAL_TESTING_ELIGIBLE": "final",
    "DEEP_TESTING_ELIGIBLE": "deep",
    "LIVE_TESTING_ELIGIBLE": "live",
    "LIVE_TESTING_ACTIVE": "live_active",
    "CURRENT_GENERATION": "generation",
    "TARGET": "target",
    "REMAINING": "remaining",
}

NODE_STATE_DEFINITIONS: Dict[str, str] = {
    "TOTAL": POPULATION_DEFINITIONS["total"],
    "ALIVE": POPULATION_DEFINITIONS["alive"],
    "DEAD": ("Nodes that are NOT alive and are not infrastructure-labelled: the research "
             "population minus ALIVE. Reported as the sum of FAILED (judged and did not make "
             "it), BLOCKED (could not be judged — data/infrastructure, never shown as a "
             "strategy failure) and UNKNOWN (a status the vocabulary cannot explain)."),
    "BACKTESTING": "Nodes in flight in the backtest stage right now (status BACKTESTING/TESTING).",
    "VALIDATING": "Nodes in flight in the walk-forward validation stage right now.",
    "QUALIFIED": POPULATION_DEFINITIONS["qualified"],
    "FINAL_TESTING_ELIGIBLE": POPULATION_STATE_DEFINITIONS["FINAL_TESTING_ELIGIBLE"],
    "DEEP_TESTING_ELIGIBLE": POPULATION_STATE_DEFINITIONS["DEEP_TESTING_ELIGIBLE"],
    "LIVE_TESTING_ELIGIBLE": POPULATION_STATE_DEFINITIONS["LIVE_TESTING_ELIGIBLE"],
    "LIVE_TESTING_ACTIVE": POPULATION_STATE_DEFINITIONS["LIVE_TESTING_ACTIVE"],
    "CURRENT_GENERATION": ("The highest generation number in the research population "
                           "(the engine's own generation counter, read from the same rows)."),
    "TARGET": "The node ceiling of the CURRENT experiment (operator setting, not a node count).",
    "REMAINING": "Nodes the current experiment may still generate before it reaches TARGET.",
}


def _experiment_scope(db: Any, engine: Any, counts: Dict[str, Any],
                      notes: List[str]) -> Dict[str, Any]:
    """V5.4 §1 — the CURRENT EXPERIMENT's target / run id / node count.

    Read-only and side-effect free: a snapshot for database X must never re-bind
    a process-wide engine (doing so made one caller's database describe every
    later caller, which is how a scratch database ended up "having" the live
    run's id). When the engine that owns the run is handed in, its own values
    are used; otherwise the values are read from the database itself and a run
    id is only adopted when that database actually holds rows for it.
    """
    target = 0
    run_id = None
    nodes = int((counts or {}).get("user_research") or 0)
    source = "database scope"

    def _configured_target() -> int:
        try:
            from ..config import get_config
            return int(get_config().evolution.total_node_target or 0)
        except Exception:                                   # pragma: no cover - defensive
            return 0

    if engine is not None:
        source = "engine (run scope)"
        try:
            target = int(engine.get_total_node_target() or 0)
        except Exception as e:                              # pragma: no cover - defensive
            notes.append(f"the engine could not report its node target: {e}")
            target = _configured_target()
        run_id = getattr(engine, "active_run_id", None)
        if run_id:
            try:
                nodes = int(engine.run_nodes() or 0)
            except Exception as e:                          # pragma: no cover - defensive
                notes.append(f"the engine could not count its run's nodes ({e}) — "
                             f"the database scope is reported instead")
                run_id = None
                nodes = int((counts or {}).get("user_research") or 0)
        return {"target": target, "run_id": run_id, "nodes": nodes, "source": source}

    d = db
    if d is None:                                           # the same database the counts came from
        try:
            from ..db.database import get_db
            d = get_db()
        except Exception:                                   # pragma: no cover - defensive
            d = None
    try:
        if d is not None:
            target = int(d.get_meta("total_node_target") or 0)
    except Exception:                                       # pragma: no cover - defensive
        target = 0
    if target <= 0:
        target = _configured_target()

    candidate = None
    try:
        from ..orchestrator.pipeline_state import get_pipeline_state_manager
        candidate = get_pipeline_state_manager().run_id
    except Exception:                                       # pragma: no cover - defensive
        candidate = None
    if candidate and d is not None:
        run_count = None
        try:
            run_count = int(d.total_strategies_count(run_id=candidate))
        except Exception:                                   # pragma: no cover - defensive
            run_count = None
        if run_count:                                       # only adopt a run this DB knows
            run_id, nodes, source = candidate, run_count, "database run scope"
        elif run_count == 0:
            notes.append(f"run {candidate} has no rows in this database — the current "
                         f"experiment is reported from the database's own research scope")
    return {"target": target, "run_id": run_id, "nodes": nodes, "source": source}


def node_state_snapshot(db: Any = None, engine: Any = None) -> Dict[str, Any]:
    """V5.4 §1 — THE node-state snapshot every page consumes.

    One function, one read of the strategy rows, one classifier: the counts and the
    progress figures cannot disagree with each other because they are derived
    together. Callers must NOT re-derive a number from their own query — that is
    how Overview ended up showing a different QUALIFIED than the NODES strip.

    ``state``     explicit names (NODE_STATE_KEYS) — the vocabulary the UI prints
    ``counts``    the same numbers under their short keys (backwards compatible)
    ``progress``  the CURRENT EXPERIMENT's ceiling: target / nodes / remaining / pct
    ``invariants`` the partition checks, measured (never assumed)
    ``authority`` which module/classifier produced the numbers
    """
    data = populations(db=db)
    counts = dict(data.get("counts") or {})
    notes: List[str] = list(data.get("notes") or [])

    # ---------------------------------------------------------------- #
    # the current experiment's own progress — read from the DATABASE this
    # snapshot was asked about, never from a process-wide singleton (V5.4 §1):
    # re-binding the global engine on a read-only analytics call leaked one
    # caller's database into every later caller.
    # ---------------------------------------------------------------- #
    scope = _experiment_scope(db, engine, counts, notes)
    target = int(scope["target"])
    run_id = scope["run_id"]
    experiment_nodes = int(scope["nodes"])
    remaining = max(0, target - experiment_nodes) if target else 0
    pct = round((experiment_nodes / target) * 100.0, 1) if target else None
    # VALIDATING is reported by populations() from the same single read of the
    # rows — this function must NOT open a second read (that is how two numbers
    # start to drift apart).
    values = dict(counts)
    values["validating"] = int(counts.get("validating") or 0)
    values["target"] = target
    values["remaining"] = remaining

    state = {name: int(values.get(key) or 0) for name, key in NODE_STATE_KEYS.items()}

    # measured invariants — shipped with the numbers so a mismatch is visible
    alive, dead, legacy = state["ALIVE"], state["DEAD"], int(counts.get("legacy_excluded") or 0)
    total = state["TOTAL"]
    invariants = {
        "total_equals_alive_dead_legacy": (alive + dead + legacy) == total,
        "alive_plus_dead_plus_legacy": alive + dead + legacy,
        "qualified_within_alive": state["QUALIFIED"] <= alive,
        "dead_split_reported": (int(counts.get("failed") or 0) + int(counts.get("blocked") or 0)
                                + int(counts.get("unknown") or 0)) == dead,
        "rule": ("TOTAL = ALIVE + DEAD + LEGACY (infrastructure-labelled rows); "
                 "DEAD = FAILED + BLOCKED + UNKNOWN; QUALIFIED ⊆ ALIVE; "
                 "BACKTESTING/VALIDATING ⊆ ALIVE (in flight, not dead)"),
    }
    if not invariants["total_equals_alive_dead_legacy"] or not invariants["dead_split_reported"]:
        notes.append("the node partition does not add up — the numbers are reported as "
                     "measured, the mismatch is NOT hidden")

    return {
        "ok": bool(data.get("ok")),
        "authority": "app.research.populations.node_state_snapshot",
        "classifier": data.get("authority"),
        "state": state,
        "state_order": list(NODE_STATE_KEYS.keys()),
        "population_state": {name: state[name] for name in POPULATION_STATE_KEYS},
        "counts": {**counts, "validating": values["validating"], "target": target,
                   "remaining": remaining},
        "progress": {
            "scope": "current experiment",
            "source": scope["source"],
            "run_id": run_id,
            "nodes": experiment_nodes,
            "target": target,
            "remaining": remaining,
            "pct": pct,
            "ceiling_reached": bool(target and experiment_nodes >= target),
            "note": ("the ceiling counts the CURRENT EXPERIMENT's nodes; TOTAL counts every "
                     "stored node, legacy infrastructure rows included"),
        },
        "invariants": invariants,
        "definitions": {**data.get("definitions", {}), **NODE_STATE_DEFINITIONS},
        "detail": data.get("detail"),
        "notes": notes,
    }


def classified_ids(db: Any = None, *, ids: Any = None) -> Dict[str, Set[int]]:
    """V5.4 §1 — id sets per bucket, produced by the ONE classifier.

    Downstream analytics that need per-node buckets (e.g. the evolution rates) call
    this instead of re-implementing the rule in SQL: a second implementation is how
    two panels start reporting different "qualified" numbers. ``ids`` narrows the
    work to a known set of node ids when the caller only cares about those.
    """
    rows = load_rows(db)
    wanted: Optional[Set[int]] = None
    if ids is not None:
        wanted = {int(i) for i in ids}
    out: Dict[str, Set[int]] = {}
    for r in rows:
        try:
            rid = int(r["id"])
        except Exception:                                   # pragma: no cover - defensive
            continue
        if wanted is not None and rid not in wanted:
            continue
        bucket = node_bucket(r)["bucket"]
        out.setdefault(bucket, set()).add(rid)
    return out


def population_state(db: Any = None) -> Dict[str, Any]:
    """§10 — the single authoritative status model, under explicit names.

    Returns a flat dict of the six counts plus the definitions, the authority and
    the notes, so the Dashboard, MT5 Demo Trading, Final Testing and Deep Testing
    all read the same numbers from one place. ``counts`` keeps its original keys
    for backwards compatibility; ``state`` carries the explicit names.
    """
    data = populations(db=db)
    counts = data.get("counts") or {}
    state = {name: int(counts.get(key) or 0) for name, key in POPULATION_STATE_KEYS.items()}
    return {
        "ok": bool(data.get("ok")),
        "state": state,
        "state_order": list(POPULATION_STATE_KEYS.keys()),
        "definitions": POPULATION_STATE_DEFINITIONS,
        "counts": counts,
        "detail": data.get("detail"),
        "authority": data.get("authority"),
        "notes": data.get("notes"),
    }


def populations(db: Any = None) -> Dict[str, Any]:
    """§D — the authoritative counts, with their definitions attached."""
    rows = load_rows(db)
    if db is None:
        from ..db.database import get_db
        db = get_db()
    notes: List[str] = []
    if not rows:
        notes.append("no strategy rows could be read — counts are reported as 0, not estimated")

    user_rows = [r for r in rows if str(r.get("data_source") or "").upper() != "LEGACY_TEST"]
    legacy = len(rows) - len(user_rows)

    buckets: Dict[str, int] = {}
    for r in rows:
        b = node_bucket(r)["bucket"]
        buckets[b] = buckets.get(b, 0) + 1

    alive = sum(1 for r in user_rows if is_alive_user_node(r))
    qualified = buckets.get("qualified", 0)
    final = sum(1 for r in user_rows if node_bucket(r)["bucket"] == "qualified" and _is_final(r))

    try:
        deep = len(deep_universe(db, rows=rows))
    except Exception as e:                       # pragma: no cover - defensive
        notes.append(f"deep universe could not be computed: {e}")
        deep = 0

    # ``live`` (V5.2.2) = CAPABILITY: qualified nodes the engine's own predicate
    # accepts — i.e. exactly the nodes the Live Testing START path can enrol.
    try:
        elig = live_eligibility(rows=rows)
    except Exception as e:                         # pragma: no cover - defensive
        notes.append(f"live eligibility could not be computed: {e}")
        elig = {"eligible": [], "count": 0, "rejected": {}}
    live = int(elig["count"])

    # ``live_active`` = ENROLMENT blocked through the SAME predicate: the nodes the
    # live layer is actually running (enrolled AND accepted by the engine's rule).
    # Only ever counted over nodes that were actually read: a stray config row for a
    # node that is not in this database must not inflate it.
    from ..live_testing.eligibility import tradeability

    row_by_id = {int(r["id"]): r for r in user_rows}
    present = set(row_by_id)
    active_configs = _live_ids(db)
    live_configured = active_configs & present
    live_direct = {int(r["id"]) for r in user_rows if v5_status(r) == "LIVE_TESTING"}
    enrolled = live_configured | live_direct
    active_rejected: Dict[str, int] = {}
    live_active_ids: Set[int] = set()
    for sid in sorted(enrolled):
        verdict = tradeability(row_by_id[sid])
        if verdict["ok"]:
            live_active_ids.add(sid)
        else:
            key = verdict["reason"] or verdict["code"]
            active_rejected[key] = active_rejected.get(key, 0) + 1
    live_active = len(live_active_ids)

    by_source: Dict[str, int] = {}
    for r in rows:
        key = str(r.get("data_source") or "UNKNOWN")
        by_source[key] = by_source.get(key, 0) + 1

    # ------------------------------------------------------------------ #
    # V5.4 §1 — ONE source of truth: the same rows and the same classifier
    # produce the whole partition, so no page has to derive its own number.
    #   TOTAL = ALIVE + DEAD + LEGACY   (DEAD = not alive, not infrastructure-labelled)
    #   DEAD  = FAILED + BLOCKED + UNKNOWN, reported separately because an
    #           infrastructure-blocked node is NOT a failed strategy.
    # ------------------------------------------------------------------ #
    failed = int(buckets.get("failed", 0))
    blocked = int(buckets.get("blocked", 0))
    unknown = int(buckets.get("unknown", 0))
    user_total = len(user_rows)
    dead = user_total - alive
    if dead != failed + blocked + unknown:            # pragma: no cover - invariant guard
        notes.append(f"partition mismatch: dead={dead} but failed+blocked+unknown="
                     f"{failed + blocked + unknown} — reported as measured")
    in_flight = 0
    for r in user_rows:
        if str(r.get("status") or "").strip().upper() in ("BACKTESTING", "TESTING", "VALIDATING"):
            in_flight += 1
    # VALIDATING uses the SAME derived classifier the node filters use (v5_status),
    # so nobody has to open a second read of the rows to count it (V5.4 §1).
    try:
        validating = sum(1 for r in rows
                         if str(v5_status(r)).strip().upper() == "VALIDATING")
    except Exception as e:                                  # pragma: no cover - defensive
        notes.append(f"VALIDATING could not be derived: {e}")
        validating = 0
    generations = []
    for r in user_rows:
        try:
            g = r.get("generation")
            if g is not None:
                generations.append(int(g))
        except Exception:
            continue
    generation = max(generations) if generations else 0

    return {
        "ok": True,
        "counts": {"total": len(rows), "alive": alive, "qualified": qualified,
                   "final": final, "deep": deep, "live": live,
                   "live_active": live_active,
                   # V5.4 §1 additions — the rest of the same partition
                   "dead": dead, "failed": failed, "blocked": blocked, "unknown": unknown,
                   "legacy_excluded": legacy, "user_research": user_total,
                   "in_flight": in_flight, "generation": generation,
                   "validating": validating},
        "definitions": POPULATION_DEFINITIONS,
        "detail": {
            "user_research_total": len(user_rows),
            "legacy_excluded": legacy,
            "buckets": buckets,
            "alive_statuses": list(ALIVE_STATUSES),
            "live_inputs": {
                # capability (LIVE_TESTING_ELIGIBLE)
                "eligible": live,
                "eligible_detail": elig,
                # enrolment (LIVE_TESTING_ACTIVE) — kept separate on purpose
                "active": live_active,
                "active_ids": sorted(live_active_ids),
                "enrolled_total": len(enrolled),
                "enrolled_rejected": active_rejected,
                "active_configs_total": len(active_configs),
                "configured": len(live_configured),
                "live_testing_status": len(live_direct),
            },
            "by_data_source": by_source,
        },
        "authority": "app.research.populations (the ONE node-state source; classifier "
                     "app.status.node_bucket, the same one the node filters use)",
        "notes": notes,
    }


# --------------------------------------------------------------------------- #
# §F — deep-run progress: how many bars a run must cover, and what it has done
# --------------------------------------------------------------------------- #
#: seconds per bar for the timeframes the lab stores
_TF_SECONDS: Dict[str, int] = {
    "M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400,
    "D1": 86400, "W1": 604800, "MN1": 2592000,
}


def timeframe_seconds(timeframe: str) -> Optional[int]:
    return _TF_SECONDS.get(str(timeframe or "").strip().upper())


def estimate_bars(timeframe: str, start_ts: float, end_ts: float) -> Dict[str, Any]:
    """Bars a deep run should cover — the honest progress denominator.

    Returns ``{"bars": int|None, "reason": str}``: ``None`` means the estimate
    cannot be made (unknown timeframe or an inverted window) and the caller must
    show "unknown", never a fabricated number.
    """
    sec = timeframe_seconds(timeframe)
    if sec is None:
        return {"bars": None, "reason": f"unknown timeframe {timeframe!r} — no bar estimate"}
    try:
        start, end = float(start_ts), float(end_ts)
    except (TypeError, ValueError):
        return {"bars": None, "reason": "window is not numeric — no bar estimate"}
    if end <= start:
        return {"bars": None, "reason": "window is empty (end <= start) — no bar estimate"}
    return {"bars": int((end - start) // sec), "reason": ""}


def run_progress(run: Dict[str, Any], *, processed_bars: Optional[int] = None,
                 tf_seconds: Optional[int] = None) -> Dict[str, Any]:
    """§F — one run's progress shape, shared by API and UI.

    ``pct`` is ``None`` (and the UI must show "unknown") whenever the expected bar
    count is unknown; it is never invented from the elapsed wall clock.
    """
    run = run or {}
    bars = run.get("bars")
    tf = run.get("timeframe") or ""
    expected = None
    reason = ""
    if isinstance(bars, int) and bars > 0:
        expected = bars
    else:
        est = estimate_bars(tf, run.get("start_ts") or 0, run.get("end_ts") or 0)
        expected = est["bars"]
        reason = est["reason"]
    done = processed_bars if processed_bars is not None else 0
    pct = None
    if expected:
        pct = max(0.0, min(100.0, round(100.0 * float(done) / float(expected), 2)))
    return {
        "run_id": run.get("run_id"),
        "status": run.get("status"),
        "timeframe": tf,
        "bars_expected": expected,
        "bars_processed": done,
        "pct": pct,
        "reason": reason,
        "source": "bars column" if (isinstance(bars, int) and bars > 0) else "estimate_bars(timeframe, window)",
    }
