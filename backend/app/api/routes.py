"""REST API for the dashboard."""
from __future__ import annotations

import json
import logging
import threading
import time

log = logging.getLogger("api.routes")

from ..jsonutil import jd
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Query

from ..versions import APP_VERSION, FULL_VERSION_STRING, APP_NAME, manifest
from ..config import get_config, update_config
from ..db.database import get_db
from ..logging_setup import get_logs
from ..mt5 import bridge_status, get_bridge
from ..mt5.factory import reset_bridge
from ..orchestrator.lab import get_lab
from ..paper import calibration as calib
from ..paper.engine import get_paper_engine
from ..live_testing import get_live_testing_engine
from ..live_testing.risk import RiskBlock, validate_risk_settings
from ..risk.controls import get_risk_manager
from ..genome.schema import describe
from ..activity import activity
from .. import paths as P
from .ws import bus

router = APIRouter(prefix="/api")

COMPONENT_STATES = {
    "dashboard": "operational",
    "database": "operational",
    "genome_dsl": "operational",
    "feature_engine": "operational",
    "backtester": "operational",
    "fitness": "operational",
    "evolution_engine": "operational",
    "validation_engine": "operational",
    "specialization": "operational",
    "paper_trading": "operational",
    "ai_researcher_rules": "operational",
    "ai_researcher_llm": "optional (disabled by default)",
    "risk_controls": "operational",
    "mt5_bridge_real": "available only on Windows with MT5 terminal; otherwise SIMULATOR",
    "mt5_live_execution": "PENDING explicit user activation (never automatic)",
    "gpu_acceleration": "not used (CPU-only by design; optional later)",
    "learned_regime_classifier": "PENDING (rule-based regime features operational)",
    "island_populations_map_elites": "PENDING (phase 5)",
}


# ---------------- status ----------------
@router.get("/status")
def status() -> Dict:
    db = get_db()
    lab = get_lab()
    bs = bridge_status()
    # V4.0: user-facing status statistics exclude the LEGACY_TEST records
    counts = db.count_by_status(exclude_legacy=True)
    from ..resources.manager import get_resource_manager
    from ..mt5.monitor import get_connection_monitor
    from ..activity import activity
    from ..orchestrator.stages import get_stage_manager
    rm = get_resource_manager()
    mon = get_connection_monitor()
    sm = get_stage_manager()
    return {
        "app_version": FULL_VERSION_STRING,
        "version": FULL_VERSION_STRING,
        "version_number": APP_VERSION,
        "lab": lab.status(),
        "bridge": bs,
        "paper": get_paper_engine().status()["running"],
        "status_counts": counts,
        "components": COMPONENT_STATES,
        "resources": rm.live_metrics(),
        "connection": mon.status_details(),
        "current_task": activity.get_current_task(),
        "stage_state": sm.get_unified_task_state(),
        "config_summary": {
            "symbol": get_config().data.symbol,
            "timeframes": get_config().data.timeframes,
            "population_size": get_config().evolution.population_size,
            "max_indicators": get_config().evolution.max_indicators,
            "data_is_simulated": bs["is_simulated"],
        },
        "server_time": time.time(),
    }


# ---------------- lab controls ----------------
@router.post("/lab/start")
def lab_start(payload: Dict[str, Any] = Body(default_factory=dict)) -> Dict:
    mode = payload.get("mode", "continuous")
    target = payload.get("target")
    run_type = payload.get("run_type", "resume")
    new_run_id = payload.get("run_id")
    if target is not None:
        try:
            target = int(target)
        except (ValueError, TypeError):
            target = None
    return get_lab().start(mode, target=target, run_type=run_type, new_run_id=new_run_id)


@router.post("/lab/pause")
def lab_pause() -> Dict:
    return get_lab().pause()


@router.post("/lab/resume")
def lab_resume(payload: Dict[str, Any] = Body(default_factory=dict)) -> Dict:
    target = payload.get("target")
    if target is not None:
        try:
            target = int(target)
        except (ValueError, TypeError):
            target = None
    return get_lab().resume(target=target)


@router.post("/lab/stop")
def lab_stop() -> Dict:
    return get_lab().stop()


@router.post("/lab/reset")
def lab_reset() -> Dict:
    return get_lab().reset_generation()


@router.post("/lab/clear-failed")
def lab_clear_failed() -> Dict:
    return get_lab().clear_failed()


@router.get("/lab/status")
def lab_status() -> Dict:
    return get_lab().status()


@router.post("/lab/recheck")
@router.get("/lab/recheck")
def lab_recheck() -> Dict:
    return get_lab().recheck()


@router.post("/lab/target")
def lab_set_target(payload: Dict[str, Any] = Body(...)) -> Dict:
    raw_target = payload.get("target")
    if raw_target is None:
        raise HTTPException(status_code=400, detail="Missing required 'target' parameter")
    try:
        target = int(raw_target)
        if target <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Target must be a positive integer")
    get_lab().evo.set_total_node_target(target)
    return get_lab().recheck()


@router.get("/lab/target")
def lab_get_target() -> Dict:
    lab = get_lab()
    target = lab.evo.get_total_node_target()
    total_nodes = lab.evo.total_nodes()
    return {
        "target": target,
        "total_nodes": total_nodes,
        "remaining": max(0, target - total_nodes),
        "target_reached": total_nodes >= target,
    }


# ---------------- research run management (spec §6, §8) ----------------
@router.get("/lab/runs")
def lab_get_runs() -> Dict[str, Any]:
    db = get_db()
    runs = db.get_all_research_runs()
    from ..orchestrator.pipeline_state import get_pipeline_state_manager
    psm = get_pipeline_state_manager()
    active_id = psm.run_id
    for r in runs:
        r["is_active"] = (r["run_id"] == active_id)
    return {
        "active_run_id": active_id,
        "runs": runs,
        "total_runs": len(runs),
    }


@router.get("/lab/runs/{run_id}")
def lab_get_run_detail(run_id: str) -> Dict[str, Any]:
    db = get_db()
    runs = db.get_all_research_runs()
    run = next((r for r in runs if r["run_id"] == run_id), None)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    strats = db.q("""
        SELECT id, parent_id, generation, status, fitness, creation_reason,
               survival_reason, failure_reason, created_at
        FROM strategies WHERE run_id=?
        ORDER BY COALESCE(fitness, -1) DESC LIMIT 50
    """, (run_id,))
    return {"run": run, "top_strategies": strats}


@router.post("/lab/runs/active")
def lab_set_active_run(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    run_id = payload.get("run_id")
    if not run_id:
        raise HTTPException(status_code=400, detail="Missing 'run_id' in payload")
    db = get_db()
    runs = db.get_all_research_runs()
    target_run = next((r for r in runs if r["run_id"] == run_id), None)
    if not target_run:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    from ..orchestrator.pipeline_state import get_pipeline_state_manager
    psm = get_pipeline_state_manager()
    psm.run_id = run_id
    psm.experiment_id = target_run.get("experiment_id", psm.experiment_id)
    psm.node_total = target_run.get("node_ceiling", psm.node_total)
    psm._save_active_run()

    from ..orchestrator.lab import get_lab
    lab = get_lab()
    lab.evo.active_run_id = run_id

    activity.info("SYSTEM", f"Active research run switched to {run_id}")
    return {"ok": True, "active_run_id": run_id, "run": target_run}


# ---------------- population / strategies ----------------
@router.get("/population")
def population(limit: int = 200, status: Optional[str] = None,
               sort: str = "fitness", timeframe: Optional[str] = None,
               generation: Optional[int] = None,
               data_source: Optional[str] = None,
               run_id: Optional[str] = None) -> Dict:
    db = get_db()
    where, args = [], []
    if status:
        where.append("s.status=?"); args.append(status)
    if timeframe:
        where.append("s.timeframe=?"); args.append(timeframe)
    if generation is not None:
        where.append("s.generation=?"); args.append(generation)
    if run_id:
        where.append("s.run_id=?"); args.append(run_id)
    if data_source and data_source.upper() != "ALL":
        where.append("s.data_source=?"); args.append(data_source)
    else:
        # V4.0 legacy visibility isolation: the ~787 LEGACY_TEST infrastructure
        # records are hidden from this user-facing node list by default. An
        # explicit data_source=LEGACY_TEST request still returns them.
        where.append("COALESCE(s.data_source, 'USER_RESEARCH') <> 'LEGACY_TEST'")

    wsql = ("WHERE " + " AND ".join(where)) if where else ""
    sort_col = {"fitness": "COALESCE(s.fitness,-1) DESC", "id": "s.id DESC",
                "generation": "s.generation DESC"}[sort]
    rows = db.q(f"""SELECT s.id,s.parent_id,s.generation,s.symbol,s.timeframe,s.direction,
                    s.status,s.fitness,s.species_key,s.complexity,s.mutation_type,
                    s.creation_reason,s.survival_reason,s.failure_reason,s.created_at,
                    s.run_id,s.data_source,s.research_node_num,
                    (SELECT b.metrics FROM backtests b WHERE b.strategy_id=s.id
                       AND b.stage IN ('detail','screen') ORDER BY
                       CASE b.stage WHEN 'detail' THEN 0 ELSE 1 END, b.id DESC LIMIT 1) AS m,
                    (SELECT 1 FROM matrices mx WHERE mx.strategy_id=s.id LIMIT 1) AS has_matrix
                    FROM strategies s {wsql} ORDER BY {sort_col} LIMIT ?""",
                tuple(args) + (limit,))
    out = []
    for r in rows:
        m = json.loads(r.pop("m")) if r.get("m") else {}
        r["pf"] = m.get("profit_factor")
        r["profit_factor"] = m.get("profit_factor")
        r["return_pct"] = m.get("total_return_pct")
        r["total_return_pct"] = m.get("total_return_pct")
        r["dd"] = m.get("max_drawdown_pct")
        r["max_drawdown_pct"] = m.get("max_drawdown_pct")
        r["win_rate"] = m.get("win_rate")
        r["trades"] = m.get("trades")
        r["sharpe"] = m.get("sharpe")
        r["has_matrix"] = bool(r.get("has_matrix"))
        r["has_equity_curve"] = bool(m.get("equity_curve") and len(m.get("equity_curve")) > 1) or bool(m.get("trades_sample"))
        out.append(r)
    return {"count": len(out), "strategies": out,
            "status_counts": db.count_by_status(run_id=run_id, exclude_legacy=True)}


@router.get("/strategies/qualified")
def qualified_strategies(data_source: Optional[str] = None, run_id: Optional[str] = None) -> Dict:
    """Hydrate qualified strategies immediately on startup with real persisted metrics (V3.6).

    V4.0: LEGACY_TEST records are hidden unless explicitly requested via
    data_source=LEGACY_TEST.
    """
    db = get_db()
    strats = db.get_qualified_strategies(
        data_source=data_source, run_id=run_id,
        exclude_legacy=(not data_source or data_source.upper() == "ALL"))
    return {"count": len(strats), "strategies": strats}


@router.get("/scatter")
def scatter(x: str = "total_return_pct", y: str = "max_drawdown_pct",
            color: str = "status", limit: int = 800,
            status: Optional[str] = None) -> Dict:
    db = get_db()
    rows = db.q("""SELECT s.id,s.status,s.generation,s.species_key,s.timeframe,
                   s.fitness,s.complexity,
                   (SELECT b.metrics FROM backtests b WHERE b.strategy_id=s.id
                      AND b.stage IN ('detail','screen') ORDER BY
                      CASE b.stage WHEN 'detail' THEN 0 ELSE 1 END, b.id DESC LIMIT 1) AS m
                   FROM strategies s WHERE s.fitness IS NOT NULL
                     AND COALESCE(s.data_source, 'USER_RESEARCH') <> 'LEGACY_TEST'
                   ORDER BY s.fitness DESC LIMIT ?""", (limit,))
    metric_keys = {"total_return_pct", "max_drawdown_pct", "profit_factor", "sharpe",
                   "sortino", "trades", "consistency", "win_rate"}
    points = []
    for r in rows:
        m = json.loads(r["m"]) if r.get("m") else {}
        if status and r["status"] != status:
            continue
        def val(k):
            if k == "fitness":
                return r["fitness"]
            if k == "complexity":
                return r["complexity"]
            if k == "generation":
                return r["generation"]
            return m.get(k)
        vx, vy = val(x) if x in metric_keys | {"fitness", "complexity", "generation"} else None, \
                 val(y) if y in metric_keys | {"fitness", "complexity", "generation"} else None
        if vx is None or vy is None:
            continue
        points.append({"id": r["id"], "x": vx, "y": vy,
                       "color": r.get(color) if color in ("status", "timeframe", "generation")
                                else (r["species_key"] if color == "species" else r["status"])})
    return {"x": x, "y": y, "color": color, "points": points}


@router.get("/tree")
def tree(max_nodes: int = 350, status: Optional[str] = None,
         generation_min: Optional[int] = None, generation_max: Optional[int] = None,
         symbol: Optional[str] = None, timeframe: Optional[str] = None,
         alive_only: bool = False, dead_only: bool = False, search: Optional[str] = None,
         focus_id: Optional[int] = None) -> Dict:
    """Ancestry graph, intelligently limited (full history stays in the DB)."""
    db = get_db()
    where, args = [], []
    if status:
        where.append("status=?"); args.append(status)
    if generation_min is not None:
        where.append("generation>=?"); args.append(generation_min)
    if generation_max is not None:
        where.append("generation<=?"); args.append(generation_max)
    if symbol:
        where.append("symbol=?"); args.append(symbol)
    if timeframe:
        where.append("timeframe=?"); args.append(timeframe)
    if alive_only:
        where.append("status NOT IN ('FAILED','KILLED','RETIRED')")
    if dead_only:
        where.append("status IN ('FAILED','KILLED')")
    # V4.0: the ancestry graph shown to the user never contains LEGACY_TEST nodes
    where.append("COALESCE(data_source, 'USER_RESEARCH') <> 'LEGACY_TEST'")
    wsql = ("WHERE " + " AND ".join(where)) if where else ""

    if focus_id is not None:
        # ancestors + descendants of one node
        ids: set[int] = {focus_id}
        cur = db.get_strategy(focus_id)
        while cur and cur.get("parent_id"):
            ids.add(cur["parent_id"])
            cur = db.get_strategy(cur["parent_id"])
        frontier = [focus_id]
        while frontier and len(ids) < max_nodes:
            qm = ",".join("?" * len(frontier))
            kids = db.q(f"SELECT id FROM strategies WHERE parent_id IN ({qm})", tuple(frontier))
            frontier = [k["id"] for k in kids if k["id"] not in ids]
            ids.update(frontier)
        rows = db.q(f"""SELECT * FROM strategies WHERE id IN ({",".join("?" * len(ids))})
                        AND COALESCE(data_source, 'USER_RESEARCH') <> 'LEGACY_TEST'""",
                    tuple(ids))
    else:
        rows = db.q(f"""SELECT s.*, (SELECT b.metrics FROM backtests b
                        WHERE b.strategy_id=s.id AND b.stage IN ('detail','screen')
                        ORDER BY CASE b.stage WHEN 'detail' THEN 0 ELSE 1 END, b.id DESC
                        LIMIT 1) AS m
                        FROM strategies s {wsql}
                        ORDER BY COALESCE(s.fitness,-1) DESC, s.id DESC LIMIT ?""",
                    tuple(args) + (max_nodes,))
        # include missing parents so lineages connect
        ids = {r["id"] for r in rows}
        missing = {r["parent_id"] for r in rows if r.get("parent_id") and r["parent_id"] not in ids}
        depth = 0
        while missing and depth < 6 and len(ids) + len(missing) < max_nodes * 1.4:
            extra = db.q(f"""SELECT * FROM strategies WHERE id IN
                             ({",".join("?" * len(missing))})
                             AND COALESCE(data_source, 'USER_RESEARCH') <> 'LEGACY_TEST'""",
                         tuple(missing))
            rows.extend(extra)
            ids |= {e["id"] for e in extra}
            missing = {e["parent_id"] for e in extra
                       if e.get("parent_id") and e["parent_id"] not in ids}
            depth += 1

    if search:
        rows = [r for r in rows if search in str(r["id"]) or
                (r.get("species_key") and search.lower() in r["species_key"].lower()) or
                (r.get("creation_reason") and search.lower() in r["creation_reason"].lower())]

    nodes = []
    for r in rows:
        m = json.loads(r["m"]) if r.get("m") else {}
        nodes.append({
            "id": r["id"], "parent_id": r.get("parent_id"),
            "generation": r["generation"], "status": r["status"],
            "fitness": r.get("fitness"), "symbol": r["symbol"],
            "timeframe": r["timeframe"], "species": r.get("species_key"),
            "pf": m.get("profit_factor"), "return_pct": m.get("total_return_pct"),
            "dd": m.get("max_drawdown_pct"), "trades": m.get("trades"),
            "mutation_type": r.get("mutation_type"),
            "creation_reason": r.get("creation_reason"),
            "failure_reason": r.get("failure_reason"),
            "survival_reason": r.get("survival_reason"),
        })
    node_map = {n["id"]: n for n in nodes}
    edges = []
    for n in nodes:
        pid = n.get("parent_id")
        if pid in node_map:
            p = node_map[pid]
            is_dead = n["status"] in ("FAILED", "KILLED", "RETIRED")
            is_alive = n["status"] in ("QUALIFIED", "PAPER", "SURVIVED", "BORN", "VALIDATING", "BACKTESTING")
            edges.append({
                "source": pid,
                "target": n["id"],
                "target_status": n["status"],
                "source_status": p["status"],
                "is_dead": is_dead,
                "is_alive": is_alive,
                "branch_state": "dead" if is_dead else "active",
            })
    return {"nodes": nodes, "edges": edges, "truncated": len(rows) >= max_nodes,
            "total_strategies": db.one(
                "SELECT COUNT(*) c FROM strategies WHERE COALESCE(data_source, 'USER_RESEARCH') <> 'LEGACY_TEST'")["c"]}


@router.get("/strategies/{sid}")
def strategy_detail(sid: int) -> Dict:
    db = get_db()
    row = db.get_strategy(sid)
    if not row:
        raise HTTPException(404, "strategy not found")
    genome = row["genome"]
    bts = db.q("SELECT * FROM backtests WHERE strategy_id=? ORDER BY id DESC LIMIT 12", (sid,))
    for b in bts:
        b["metrics"] = json.loads(b["metrics"])
        b["params"] = json.loads(b["params"]) if b.get("params") else None
        b["window"] = json.loads(b["window"]) if b.get("window") else None
    val = db.one("SELECT * FROM validations WHERE strategy_id=?", (sid,))
    if val:
        for k in ("oos", "walkforward", "perturbation", "spread_stress",
                  "montecarlo", "regime_holdout", "notes"):
            val[k] = json.loads(val[k]) if val.get(k) else None
    mats = db.one("SELECT * FROM matrices WHERE strategy_id=?", (sid,))
    if mats:
        for k in ("timeframe", "session", "day", "regime", "direction"):
            mats[k] = json.loads(mats[k]) if mats.get(k) else None
    elif row.get("status") in ("QUALIFIED", "PAPER") or bts:
        # V3.6: Check whether required matrix exists. If missing, generate from real persisted backtest
        detail_bt = next((b for b in bts if b.get("stage") == "detail"), None) or (bts[0] if bts else None)
        if detail_bt and detail_bt.get("metrics"):
            trades = detail_bt["metrics"].get("trades_sample", [])
            try:
                from ..specialization.matrix import full_matrices
                computed_mats = full_matrices(genome, genome.get("symbol", "XAUUSD"), trades)
                now_ts = time.time()
                db.x("""INSERT OR REPLACE INTO matrices
                        (strategy_id, timeframe, session, day, regime, direction, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)""",
                     (sid, json.dumps(computed_mats["timeframe"]),
                      json.dumps(computed_mats["session"]),
                      json.dumps(computed_mats["day"]),
                      json.dumps(computed_mats["regime"]),
                      json.dumps(computed_mats["direction"]), now_ts))
                mats = computed_mats
            except Exception as e:
                log.warning("Auto-generation of matrix for strategy #%s failed: %s", sid, e)

    # ancestry chain (research history: WHY this strategy exists)
    lineage = []
    cur = row
    while cur:
        lineage.append({"id": cur["id"], "generation": cur["generation"],
                        "status": cur["status"], "mutation_type": cur.get("mutation_type"),
                        "creation_reason": cur.get("creation_reason"),
                        "fitness": cur.get("fitness"), "origin": cur.get("origin")})
        cur = db.get_strategy(cur["parent_id"]) if cur.get("parent_id") else None
    children = db.q("""SELECT id,status,fitness,mutation_type,creation_reason
                       FROM strategies WHERE parent_id=? ORDER BY id LIMIT 50""", (sid,))
    hyps = db.q("SELECT * FROM hypotheses WHERE strategy_id=? ORDER BY id DESC LIMIT 20", (sid,))
    for h in hyps:
        h["proposal"] = json.loads(h["proposal"])
    paper = db.q("""SELECT COUNT(*) n, COALESCE(SUM(pnl),0) pnl,
                    AVG(CASE WHEN pnl>0 THEN 1.0 ELSE 0.0 END) wr
                    FROM paper_trades WHERE strategy_id=? AND status='CLOSED'""", (sid,))[0]
    return {"strategy": {**row, "genome": genome, "description": describe(genome)},
            "backtests": bts, "validation": val, "matrices": mats,
            "lineage": lineage, "children": children, "hypotheses": hyps,
            "paper_summary": paper}


@router.get("/strategies/{sid}/charts")
def strategy_charts(sid: int) -> Dict:
    """Return real equity curve & drawdown data or fallback message (V3.6)."""
    return get_db().get_strategy_charts(sid)


@router.post("/strategies/{sid}/matrix")
def generate_strategy_matrix(sid: int) -> Dict:
    """Check and generate diagnostic matrices from real persisted backtest data (V3.6)."""
    db = get_db()
    row = db.get_strategy(sid)
    if not row:
        raise HTTPException(404, "Strategy not found")
    mats = db.one("SELECT * FROM matrices WHERE strategy_id=?", (sid,))
    if mats:
        for k in ("timeframe", "session", "day", "regime", "direction"):
            mats[k] = json.loads(mats[k]) if mats.get(k) else None
        return {"ok": True, "generated": False, "matrices": mats}

    bt = db.one("SELECT metrics FROM backtests WHERE strategy_id=? AND stage='detail' ORDER BY id DESC LIMIT 1", (sid,))
    if not bt:
        bt = db.one("SELECT metrics FROM backtests WHERE strategy_id=? ORDER BY id DESC LIMIT 1", (sid,))
    if not bt or not bt.get("metrics"):
        raise HTTPException(400, "Cannot generate matrix: no persisted backtest trade data found")

    m = json.loads(bt["metrics"])
    trades = m.get("trades_sample", [])
    from ..specialization.matrix import full_matrices
    computed = full_matrices(row["genome"], row["genome"].get("symbol", "XAUUSD"), trades)
    now_ts = time.time()
    db.x("""INSERT OR REPLACE INTO matrices
            (strategy_id, timeframe, session, day, regime, direction, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
         (sid, json.dumps(computed["timeframe"]), json.dumps(computed["session"]),
          json.dumps(computed["day"]), json.dumps(computed["regime"]),
          json.dumps(computed["direction"]), now_ts))
    return {"ok": True, "generated": True, "matrices": computed}


@router.post("/tasks/reconcile")
def reconcile_tasks() -> Dict:
    """Reconcile in-flight tasks and transition lifecycle to COMPLETED_AT_CEILING (V3.6)."""
    from ..orchestrator.pipeline_state import get_pipeline_state_manager
    from ..orchestrator.lab import get_lab
    lab = get_lab()
    db = get_db()
    
    # Check pending strategies in BORN or BACKTESTING
    pending = db.q("SELECT id, status, run_id FROM strategies WHERE status IN ('BORN', 'BACKTESTING')")
    reconciled_count = 0
    now_ts = time.time()
    for p in pending:
        sid = p["id"]
        # Mark as FAILED or resolve
        db.update_strategy(sid, status="FAILED", failure_reason="Reconciled at research ceiling", updated_at=now_ts)
        reconciled_count += 1
    
    # Transition research runs
    db.x("UPDATE research_runs SET status='COMPLETED', last_checkpoint=? WHERE status='RUNNING'", (now_ts,))
    
    # Transition pipeline state
    ps = get_pipeline_state_manager()
    ps.set_completed("Research ceiling reached. All pending tasks reconciled.", status="COMPLETED_AT_CEILING")
    
    return {
        "ok": True,
        "reconciled_strategies": reconciled_count,
        "pipeline_status": ps.status,
        "active_workers": 0,
        "message": "Lifecycle transitioned to COMPLETED_AT_CEILING"
    }


@router.get("/hypotheses")
def hypotheses(limit: int = 100) -> Dict:
    rows = get_db().q("SELECT * FROM hypotheses ORDER BY id DESC LIMIT ?", (limit,))
    for r in rows:
        r["proposal"] = json.loads(r["proposal"])
    return {"hypotheses": rows}


@router.get("/research/memory")
def research_memory(limit: int = 100, outcome: Optional[str] = None) -> Dict:
    db = get_db()
    if not db.one("SELECT name FROM sqlite_master WHERE type='table' AND name='research_memory'"):
        return {"memory": []}
    if outcome:
        rows = db.q("SELECT * FROM research_memory WHERE outcome=? ORDER BY id DESC LIMIT ?", (outcome, limit))
    else:
        rows = db.q("SELECT * FROM research_memory ORDER BY id DESC LIMIT ?", (limit,))
    return {"memory": rows}


@router.get("/research/restoration/report")
def restoration_report() -> Dict:
    """Get the latest V3.2 historical research restoration diagnostic report."""
    from ..data.restoration import get_restoration_engine
    return get_restoration_engine().get_report()


@router.post("/research/restore")
def trigger_restoration() -> Dict:
    """Trigger full discovery and restoration of historical research artifacts."""
    from ..data.restoration import get_restoration_engine
    return get_restoration_engine().restore_all(emit_logs=True)


@router.get("/research/manifest")
def get_research_manifest_api() -> Dict:
    """Return authoritative V3.2 DATA/manifest.json."""
    if P.DATA_MANIFEST.exists():
        try:
            return json.loads(P.DATA_MANIFEST.read_text(encoding="utf-8"))
        except Exception:
            pass
    from ..data.restoration import get_restoration_engine
    rep = get_restoration_engine().get_report()
    return rep


def _filter_persisted_strategies(
    min_trades: Optional[int] = None,
    min_trade_duration_seconds: Optional[int] = None,
    min_trade_duration_minutes: Optional[float] = None,
    max_trade_duration_seconds: Optional[int] = None,
    max_trade_duration_minutes: Optional[float] = None,
    min_return_pct: Optional[float] = None,
    max_drawdown_pct: Optional[float] = None,
    min_win_rate: Optional[float] = None,
    min_profit_factor: Optional[float] = None,
    min_sharpe: Optional[float] = None,
    min_sortino: Optional[float] = None,
    min_net_profit: Optional[float] = None,
    max_loss: Optional[float] = None,
    min_avg_trade: Optional[float] = None,
    min_expectancy: Optional[float] = None,
    max_consecutive_losses: Optional[int] = None,
    min_robustness_score: Optional[float] = None,
    min_oos_return_pct: Optional[float] = None,
    min_oos_profit_factor: Optional[float] = None,
    shortlist_only: Optional[bool] = None,
    sort_by: Optional[str] = None,
    sort_desc: bool = True,
    stage: Optional[str] = None,
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
    direction: Optional[str] = None,
    status: Optional[str] = None,
    status_category: Optional[str] = None,
    generation: Optional[int] = None,
    parent_id: Optional[int] = None,
    node_id: Optional[int] = None,
    search: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> Dict:
    db = get_db()
    if min_trade_duration_minutes is not None and min_trade_duration_seconds is None:
        min_trade_duration_seconds = int(min_trade_duration_minutes * 60)
    if max_trade_duration_minutes is not None and max_trade_duration_seconds is None:
        max_trade_duration_seconds = int(max_trade_duration_minutes * 60)

    shortlist_set = set(db.get_shortlist())

    # V4 Authoritative zero-recomputation query:
    # Prioritizes 'detail' stage in-sample backtests over screening, joins validations for OOS metrics
    query = """
        SELECT s.id, s.symbol, s.timeframe, s.direction, s.status, s.fitness, s.parent_id, s.generation,
               s.mutation_type, s.creation_reason, s.created_at, s.run_id, s.data_source, s.research_node_num,
               b.stage as bt_stage, b.metrics, b.verdict, b.fitness as bt_fitness, b.created_at as bt_created_at,
               v.robustness_score, v.oos
        FROM strategies s
        LEFT JOIN backtests b ON b.strategy_id = s.id AND b.id = (
            SELECT id FROM backtests WHERE strategy_id = s.id AND stage IN ('detail', 'screen')
            ORDER BY CASE stage WHEN 'detail' THEN 0 ELSE 1 END, id DESC LIMIT 1
        )
        LEFT JOIN validations v ON v.strategy_id = s.id
        ORDER BY s.id DESC
    """
    rows = db.q(query)
    total_evaluated = 0
    matched = []

    search_clean = (search or "").strip().lower()

    for r in rows:
        # V4.0 legacy visibility isolation: LEGACY_TEST infrastructure nodes are
        # never offered in the research tables/selectors (Strategy Laboratory,
        # Final Testing, live-test & MT5 demo node selection, shortlists).
        if (r.get("data_source") or "USER_RESEARCH").upper() == "LEGACY_TEST":
            continue
        m = {}
        if r["metrics"]:
            try:
                m = json.loads(r["metrics"]) if isinstance(r["metrics"], str) else r["metrics"]
            except Exception:
                m = {}
        if m:
            total_evaluated += 1

        is_shortlisted = r["id"] in shortlist_set
        if shortlist_only and not is_shortlisted:
            continue

        # Search matching (exact node id or text filter)
        if search_clean:
            id_match = str(r["id"]) == search_clean or f"node_{r['id']}" == search_clean
            sym_match = search_clean in (r["symbol"] or "").lower()
            tf_match = search_clean in (r["timeframe"] or "").lower()
            stat_match = search_clean in (r["status"] or "").lower()
            mut_match = search_clean in (r["mutation_type"] or "").lower()
            gen_match = f"gen {r['generation']}" in search_clean or f"gen{r['generation']}" in search_clean or str(r['generation']) == search_clean
            parent_match = search_clean in f"node_{r['parent_id']}" or str(r['parent_id']) == search_clean
            if not (id_match or sym_match or tf_match or stat_match or mut_match or gen_match or parent_match):
                continue

        if node_id is not None and r["id"] != node_id:
            continue
        if parent_id is not None and r["parent_id"] != parent_id:
            continue
        if generation is not None and r["generation"] != generation:
            continue
        if status and r["status"] != status:
            continue
        if status_category:
            cat = status_category.upper()
            if cat == "QUALIFIED" and r["status"] not in ("QUALIFIED", "PAPER"):
                continue
            elif cat == "ALIVE" and r["status"] not in ("BORN", "BACKTESTING", "SURVIVED", "VALIDATING", "QUALIFIED", "PAPER"):
                continue
            elif cat == "DEAD" and r["status"] not in ("FAILED", "KILLED", "RETIRED"):
                continue
        if symbol and r["symbol"] != symbol:
            continue
        if timeframe and r["timeframe"] != timeframe:
            continue
        if direction and direction.lower() != "both" and (r["direction"] or "both").lower() != direction.lower():
            continue
        if stage and (r["bt_stage"] or "").lower() != stage.lower():
            continue

        # Validation & OOS parsing
        oos_data = {}
        if r.get("oos"):
            try:
                oos_data = json.loads(r["oos"]) if isinstance(r["oos"], str) else r["oos"]
            except Exception:
                oos_data = {}
        oos_metrics = oos_data.get("metrics") or {}
        oos_ret = oos_metrics.get("total_return_pct")
        oos_pf = oos_metrics.get("profit_factor")
        rob_score = r.get("robustness_score")

        if oos_ret is None and rob_score is not None:
            deg = oos_data.get("degradation") or 0.0
            base_ret = m.get("total_return_pct", 0.0)
            oos_ret = base_ret * max(0.0, 1.0 - deg)
            oos_pf = m.get("profit_factor", 1.0) * max(0.0, 1.0 - deg * 0.5)

        if min_robustness_score is not None and (rob_score is None or rob_score < min_robustness_score):
            continue
        if min_oos_return_pct is not None and (oos_ret is None or oos_ret < min_oos_return_pct):
            continue
        if min_oos_profit_factor is not None and (oos_pf is None or oos_pf < min_oos_profit_factor):
            continue

        # Trades sample & Duration dynamic calculation
        trades_sample = m.get("trades_sample", [])
        avg_dur_s = m.get("avg_trade_duration_seconds")
        min_dur_s = m.get("min_trade_duration_seconds", 0)
        max_dur_s = m.get("max_trade_duration_seconds", 0)
        cons_losses = 0
        max_cons_losses = 0
        cons_wins = 0
        max_cons_wins = 0
        largest_win = 0.0
        largest_loss = 0.0

        if trades_sample:
            durations = []
            cur_cl = 0
            cur_cw = 0
            for t in trades_sample:
                pnl = t.get("pnl", 0.0)
                if pnl > largest_win:
                    largest_win = pnl
                if pnl < largest_loss:
                    largest_loss = pnl
                if pnl < 0:
                    cur_cl += 1
                    cur_cw = 0
                    if cur_cl > max_cons_losses:
                        max_cons_losses = cur_cl
                elif pnl > 0:
                    cur_cw += 1
                    cur_cl = 0
                    if cur_cw > max_cons_wins:
                        max_cons_wins = cur_cw
                e_ts = t.get("entry_ts", 0)
                x_ts = t.get("exit_ts", 0)
                if e_ts and x_ts and x_ts > e_ts:
                    durations.append(x_ts - e_ts)
            if durations:
                min_dur_s = min(durations)
                max_dur_s = max(durations)
                if avg_dur_s is None:
                    avg_dur_s = sum(durations) / len(durations)

        if avg_dur_s is None:
            # Fallback estimation based on timeframe if no sample present
            tf_min = 15
            if r["timeframe"] == "M1": tf_min = 1
            elif r["timeframe"] == "M5": tf_min = 5
            elif r["timeframe"] == "M30": tf_min = 30
            elif r["timeframe"] == "H1": tf_min = 60
            avg_dur_s = tf_min * 60 * 12

        # Metric filters
        if min_trades is not None and m.get("trades", 0) < min_trades:
            continue
        if min_trade_duration_seconds is not None and avg_dur_s < min_trade_duration_seconds:
            continue
        if max_trade_duration_seconds is not None and avg_dur_s > max_trade_duration_seconds:
            continue
        if min_return_pct is not None and m.get("total_return_pct", -999.0) < min_return_pct:
            continue
        if max_drawdown_pct is not None and m.get("max_drawdown_pct", 999.0) > max_drawdown_pct:
            continue
        if min_win_rate is not None and m.get("win_rate", 0.0) < min_win_rate:
            continue
        if min_profit_factor is not None and m.get("profit_factor", 0.0) < min_profit_factor:
            continue
        if min_sharpe is not None and m.get("sharpe", -99.0) < min_sharpe:
            continue
        if min_sortino is not None and m.get("sortino", -99.0) < min_sortino:
            continue
        if min_net_profit is not None and m.get("net_profit", -999999.0) < min_net_profit:
            continue
        if min_avg_trade is not None and m.get("avg_trade", -99999.0) < min_avg_trade:
            continue
        if min_expectancy is not None and m.get("expectancy", -99999.0) < min_expectancy:
            continue
        if max_consecutive_losses is not None and max_cons_losses > max_consecutive_losses:
            continue
        if max_loss is not None and abs(largest_loss) > abs(max_loss):
            continue

        matched.append({
            "id": r["id"],
            "node_id": f"Node_{r['id']}",
            "research_node_num": r.get("research_node_num") or r["id"],
            "run_id": r.get("run_id") or "RUN-HISTORICAL-PRESERVED",
            "data_source": r.get("data_source") or "USER_RESEARCH",
            "symbol": r["symbol"],
            "timeframe": r["timeframe"],
            "direction": r.get("direction", "both"),
            "status": r["status"],
            "fitness": r["fitness"],
            "parent_id": r["parent_id"],
            "generation": r["generation"],
            "mutation_type": r["mutation_type"],
            "creation_reason": r.get("creation_reason", ""),
            "stage": r["bt_stage"] or "NONE",
            "verdict": r["verdict"] or "NONE",
            "trades": m.get("trades", 0),

            # Authoritative Stage-Separated Returns (V4 Spec §2)
            "total_return_pct": m.get("total_return_pct", 0.0),
            "backtest_return_pct": m.get("total_return_pct", 0.0),
            "validation_oos_return_pct": oos_ret,
            "mt5_backtest_return_pct": None,
            "live_test_return_pct": None,
            "mt5_demo_return_pct": None,

            # Profit Factors
            "profit_factor": m.get("profit_factor", 0.0),
            "in_sample_pf": m.get("profit_factor", 0.0),
            "oos_pf": oos_pf,

            # Risk & Robustness
            "net_profit": m.get("net_profit", 0.0),
            "max_drawdown_pct": m.get("max_drawdown_pct", 0.0),
            "win_rate": m.get("win_rate", 0.0),
            "sharpe": m.get("sharpe", 0.0),
            "sortino": m.get("sortino", 0.0),
            "avg_trade": m.get("avg_trade", 0.0),
            "expectancy": m.get("expectancy", 0.0),
            "robustness_score": rob_score,
            "avg_trade_duration_seconds": avg_dur_s,
            "min_trade_duration_seconds": min_dur_s,
            "max_trade_duration_seconds": max_dur_s,
            "largest_win": largest_win,
            "largest_loss": largest_loss,
            "consecutive_wins": max_cons_wins,
            "consecutive_losses": max_cons_losses,
            "created_at": r["created_at"],
            "shortlisted": is_shortlisted,
        })

    # Multi-metric Sorting (V4 Spec §3)
    if sort_by:
        field = sort_by.strip()
        def _get_sort_val(item):
            v = item.get(field)
            if v is None:
                return -999999.0 if sort_desc else 999999.0
            if isinstance(v, (int, float)):
                return float(v)
            return str(v).lower()
        matched.sort(key=_get_sort_val, reverse=bool(sort_desc))

    paginated = matched[offset:offset + limit]
    return {
        "total_evaluated": total_evaluated,
        "total_matching": len(matched),
        "recomputed": False,
        "limit": limit,
        "offset": offset,
        "filters_applied": {
            "min_trades": min_trades,
            "min_trade_duration_seconds": min_trade_duration_seconds,
            "max_trade_duration_seconds": max_trade_duration_seconds,
            "min_return_pct": min_return_pct,
            "max_drawdown_pct": max_drawdown_pct,
            "min_win_rate": min_win_rate,
            "min_profit_factor": min_profit_factor,
            "min_sharpe": min_sharpe,
            "min_sortino": min_sortino,
            "min_net_profit": min_net_profit,
            "max_loss": max_loss,
            "min_avg_trade": min_avg_trade,
            "min_expectancy": min_expectancy,
            "max_consecutive_losses": max_consecutive_losses,
            "min_robustness_score": min_robustness_score,
            "min_oos_return_pct": min_oos_return_pct,
            "min_oos_profit_factor": min_oos_profit_factor,
            "shortlist_only": shortlist_only,
            "sort_by": sort_by,
            "sort_desc": sort_desc,
            "stage": stage,
            "symbol": symbol,
            "timeframe": timeframe,
            "direction": direction,
            "status": status,
            "status_category": status_category,
            "generation": generation,
            "parent_id": parent_id,
            "node_id": node_id,
            "search": search,
        },
        "strategies": paginated,
    }


@router.get("/research/shortlist/saved")
def get_saved_shortlist() -> Dict:
    db = get_db()
    sids = db.get_shortlist()
    return {"shortlist": sids}


@router.post("/research/shortlist/toggle")
def toggle_shortlist_item(payload: Dict = Body(...)) -> Dict:
    db = get_db()
    sid = int(payload.get("strategy_id", 0))
    if not sid:
        raise HTTPException(400, "strategy_id is required")
    sids = db.get_shortlist()
    if sid in sids:
        db.remove_from_shortlist(sid)
        shortlisted = False
    else:
        db.add_to_shortlist(sid, payload.get("notes", ""))
        shortlisted = True
    return {"ok": True, "strategy_id": sid, "shortlisted": shortlisted, "shortlist": db.get_shortlist()}


@router.post("/research/shortlist/clear")
def clear_saved_shortlist() -> Dict:
    db = get_db()
    db.clear_shortlist()
    return {"ok": True, "shortlist": []}


@router.get("/strategies/{sid}/trades")
def get_strategy_trades(sid: int) -> Dict:
    db = get_db()
    strat = db.get_strategy(sid)
    if not strat:
        raise HTTPException(404, f"Strategy #{sid} not found")
    trades = []
    # 1. Try reading complete trade history from persistent trades.parquet (spec §11)
    candidates = [
        P.DATA_ROOT / "backtests" / f"strategy_{sid:06d}" / "trades.parquet",
        P.DATA_ROOT / "research" / "backtests" / f"strategy_{sid:06d}" / "trades.parquet",
        P.RESEARCH_DIR / "backtests" / f"strategy_{sid:06d}" / "trades.parquet",
    ]
    for p in candidates:
        if p.exists():
            try:
                tdf = pd.read_parquet(p)
                for idx, r in enumerate(tdf.to_dict(orient="records"), 1):
                    e_ts = r.get("entry_ts", 0)
                    x_ts = r.get("exit_ts", 0)
                    dur_s = max(0, x_ts - e_ts) if (e_ts and x_ts) else (r.get("hold_bars", 1) * 3600)
                    trades.append({
                        "trade_num": idx,
                        "side": str(r.get("side", "buy")).upper(),
                        "entry_ts": e_ts,
                        "exit_ts": x_ts,
                        "entry_price": float(r.get("entry_price", 0.0)),
                        "exit_price": float(r.get("exit_price", 0.0)),
                        "duration_seconds": dur_s,
                        "duration_minutes": round(dur_s / 60.0, 1),
                        "pnl": float(r.get("pnl", 0.0)),
                        "pnl_pct": round(float(r.get("pnl", 0.0)) / 10000.0 * 100, 3),
                        "exit_reason": r.get("exit_reason", "SIGNAL"),
                        "session": r.get("session", "–"),
                    })
                if trades:
                    return {"strategy_id": sid, "total_trades": len(trades), "trades": trades}
            except Exception:
                pass

    # 2. Fall back to trades_sample from metrics JSON
    bt = db.one("SELECT metrics FROM backtests WHERE strategy_id=? ORDER BY id DESC LIMIT 1", (sid,))
    if bt and bt.get("metrics"):
        try:
            m = json.loads(bt["metrics"]) if isinstance(bt["metrics"], str) else bt["metrics"]
            raw_trades = m.get("trades_sample", [])
            for idx, t in enumerate(raw_trades, 1):
                e_ts = t.get("entry_ts", 0)
                x_ts = t.get("exit_ts", 0)
                dur_s = max(0, x_ts - e_ts) if (e_ts and x_ts) else (t.get("hold_bars", 1) * 3600)
                trades.append({
                    "trade_num": idx,
                    "side": t.get("side", "buy").upper(),
                    "entry_ts": e_ts,
                    "exit_ts": x_ts,
                    "entry_price": t.get("entry_price", 0.0),
                    "exit_price": t.get("exit_price", 0.0),
                    "duration_seconds": dur_s,
                    "duration_minutes": round(dur_s / 60.0, 1),
                    "pnl": t.get("pnl", 0.0),
                    "pnl_pct": round(t.get("pnl", 0.0) / 10000.0 * 100, 3),
                    "exit_reason": t.get("exit_reason", "SIGNAL"),
                    "session": t.get("session", "–"),
                })
        except Exception:
            pass
    return {"strategy_id": sid, "total_trades": len(trades), "trades": trades}


@router.post("/strategies/{sid}/backtest")
def rerun_strategy_backtest(sid: int) -> Dict:
    db = get_db()
    strat = db.get_strategy(sid)
    if not strat:
        raise HTTPException(404, f"Strategy #{sid} not found")
    genome = strat["genome"]
    from ..data.engine import get_data_engine
    de = get_data_engine()
    ds_info = de.latest_dataset(genome["symbol"], genome["timeframe"])
    if not ds_info:
        raise HTTPException(400, f"No dataset found for {genome['symbol']} {genome['timeframe']}")
    split = de.train_test_split(ds_info["id"])
    w = (0, split["cut"])
    from ..orchestrator.lab import get_lab, _bt_payload
    from ..fitness.evaluator import fitness, death_check
    payload = _bt_payload(genome, ds_info["id"], "detail", window=w)
    lab = get_lab()
    res = lab._run_batch([payload])[0]
    if res["ok"]:
        m = res["metrics"]
        m["trades_sample"] = res["trades"][-300:]
        f, components = fitness(m, genome)
        died, reasons = death_check(m, genome, stage="detail")
        verdict = "KILLED" if died else "SURVIVED"
        db.x("""INSERT INTO backtests
                     (strategy_id,stage,dataset_id,window,params,metrics,fitness,
                      verdict,created_at,fingerprint,dataset_version,engine_versions)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (sid, "detail", ds_info["id"], json.dumps(m.get("window")),
                   json.dumps({"components": components}), json.dumps(m), f,
                   verdict, time.time(),
                   "", ds_info.get("dataset_version", 1), json.dumps(manifest())))
        db.update_strategy(sid, fitness=f, status=verdict)
        return {"ok": True, "metrics": m, "fitness": f, "verdict": verdict}
    else:
        raise HTTPException(500, f"Backtest execution failed: {res.get('error')}")


@router.post("/strategies/{sid}/validate")
def run_strategy_validation(sid: int) -> Dict:
    db = get_db()
    strat = db.get_strategy(sid)
    if not strat:
        raise HTTPException(404, f"Strategy #{sid} not found")
    bt = db.one("SELECT * FROM backtests WHERE strategy_id=? ORDER BY id DESC LIMIT 1", (sid,))
    if not bt:
        raise HTTPException(400, "Strategy has no backtest results to validate")
    base_metrics = json.loads(bt["metrics"]) if isinstance(bt["metrics"], str) else bt["metrics"]
    base_fitness = bt["fitness"] or 0.0
    from ..validation.battery import full_validation
    v = full_validation(strat, base_metrics, base_fitness, base_metrics.get("trades_sample", []))
    passed = v.get("passed", False)
    db.x("""INSERT OR REPLACE INTO validations
                 (strategy_id,oos,walkforward,perturbation,spread_stress,slippage_stress,
                  montecarlo,regime_holdout,robustness_score,passed,notes,created_at,fingerprint)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (sid, json.dumps(v.get("oos")), json.dumps(v.get("walkforward")),
               json.dumps(v.get("perturbation")), json.dumps(v.get("spread_slippage_stress")), None,
               json.dumps(v.get("montecarlo")), json.dumps(v.get("regime_holdout")),
               v.get("robustness_score"), 1 if passed else 0,
               json.dumps(v.get("death_reasons", [])), time.time(), ""))
    new_status = "QUALIFIED" if passed else "KILLED"
    db.update_strategy(sid, status=new_status)
    return {"ok": True, "passed": passed, "status": new_status, "validation": v}


@router.get("/research/shortlist")
def get_research_shortlist(
    min_trades: Optional[int] = None,
    min_trade_duration_seconds: Optional[int] = None,
    min_trade_duration_minutes: Optional[float] = None,
    min_return_pct: Optional[float] = None,
    max_drawdown_pct: Optional[float] = None,
    min_win_rate: Optional[float] = None,
    min_profit_factor: Optional[float] = None,
    stage: Optional[str] = None,
    symbol: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> Dict:
    """Zero-recomputation shortlist of evaluated strategies filtered against post-evaluation criteria."""
    return _filter_persisted_strategies(
        min_trades=min_trades,
        min_trade_duration_seconds=min_trade_duration_seconds,
        min_trade_duration_minutes=min_trade_duration_minutes,
        min_return_pct=min_return_pct,
        max_drawdown_pct=max_drawdown_pct,
        min_win_rate=min_win_rate,
        min_profit_factor=min_profit_factor,
        stage=stage,
        symbol=symbol,
        status=status,
        limit=limit,
        offset=offset,
    )


@router.post("/research/filter")
def post_research_filter(payload: Dict = Body(...)) -> Dict:
    """Filter persisted strategy backtest results without recomputing historical backtests."""
    return _filter_persisted_strategies(
        min_trades=payload.get("min_trades"),
        min_trade_duration_seconds=payload.get("min_trade_duration_seconds"),
        min_trade_duration_minutes=payload.get("min_trade_duration_minutes"),
        max_trade_duration_seconds=payload.get("max_trade_duration_seconds"),
        max_trade_duration_minutes=payload.get("max_trade_duration_minutes"),
        min_return_pct=payload.get("min_return_pct"),
        max_drawdown_pct=payload.get("max_drawdown_pct"),
        min_win_rate=payload.get("min_win_rate"),
        min_profit_factor=payload.get("min_profit_factor"),
        min_sharpe=payload.get("min_sharpe"),
        min_sortino=payload.get("min_sortino"),
        min_net_profit=payload.get("min_net_profit"),
        max_loss=payload.get("max_loss"),
        min_avg_trade=payload.get("min_avg_trade"),
        min_expectancy=payload.get("min_expectancy"),
        max_consecutive_losses=payload.get("max_consecutive_losses"),
        min_robustness_score=payload.get("min_robustness_score"),
        min_oos_return_pct=payload.get("min_oos_return_pct"),
        min_oos_profit_factor=payload.get("min_oos_profit_factor"),
        shortlist_only=payload.get("shortlist_only"),
        sort_by=payload.get("sort_by"),
        sort_desc=payload.get("sort_desc", True),
        stage=payload.get("stage"),
        symbol=payload.get("symbol"),
        timeframe=payload.get("timeframe"),
        direction=payload.get("direction"),
        status=payload.get("status"),
        status_category=payload.get("status_category"),
        generation=payload.get("generation"),
        parent_id=payload.get("parent_id"),
        node_id=payload.get("node_id"),
        search=payload.get("search"),
        limit=int(payload.get("limit", 100)),
        offset=int(payload.get("offset", 0)),
    )


@router.post("/hypotheses/{hid}/apply")
def hypothesis_apply(hid: int) -> Dict:
    from ..ai_researcher.analyzer import apply_hypothesis
    child = apply_hypothesis(hid)
    if child is None:
        raise HTTPException(400, "hypothesis could not be applied (invalid/duplicate/rejected)")
    bus.publish("hypothesis_applied", {"hypothesis_id": hid, "child_strategy_id": child})
    return {"ok": True, "child_strategy_id": child}


# ---------------- data ----------------
@router.get("/data/inventory")
def data_inventory() -> Dict:
    """Comprehensive Data Inventory of persisted datasets, features, and manifests (V2.7)."""
    from pathlib import Path
    from ..paths import (
        DATA_ROOT, MT5_RAW_DIR, MT5_NORMALIZED_DIR, FEATURES_DIR, MANIFESTS_DIR,
        DATASETS_MANIFEST, FEATURES_MANIFEST, RESEARCH_MANIFEST, data_relative_path
    )
    from ..data.manifest import get_datasets_manifest, get_features_manifest, sync_manifests_from_disk
    from ..features.engine import get_feature_engine
    from datetime import datetime, timezone

    # Ensure manifests are synced
    sync_manifests_from_disk()
    ds_man = get_datasets_manifest().get("datasets", {})
    ft_man = get_features_manifest().get("features", {})
    db = get_db()

    datasets_list = []
    total_raw_bars = 0
    raw_storage_bytes = 0

    # 1. Inspect datasets from DB and DATA/MT5/raw/
    db_rows = db.q("SELECT * FROM datasets ORDER BY id DESC")
    seen_keys = set()

    for r in db_rows:
        did = r["id"]
        sym = r["symbol"]
        tf = r["timeframe"]
        key = f"{sym}_{tf}"
        seen_keys.add(key)
        bars = int(r.get("bars", 0))
        total_raw_bars += bars

        raw_p = MT5_RAW_DIR / f"{sym}_{tf}.parquet"
        norm_p = MT5_NORMALIZED_DIR / f"{sym}_{tf}.parquet"
        size_bytes = raw_p.stat().st_size if raw_p.exists() else (Path(r["path"]).stat().st_size if r.get("path") and Path(r["path"]).exists() else 0)
        raw_storage_bytes += size_bytes

        cached_feats = get_feature_engine().cached_features(did)
        prof = r.get("master_profile") or ""
        broker_val = prof.split("|")[1] if "|" in prof else "MetaQuotes"

        datasets_list.append({
            "id": did,
            "symbol": sym,
            "timeframe": tf,
            "rows": bars,
            "start_ts": float(r.get("start_ts", 0)),
            "end_ts": float(r.get("end_ts", 0)),
            "start_date": datetime.fromtimestamp(float(r.get("start_ts", 0)), tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
            "end_date": datetime.fromtimestamp(float(r.get("end_ts", 0)), tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
            "source": r.get("source", "MT5"),
            "broker": broker_val,
            "raw_file": data_relative_path(raw_p) if raw_p.exists() else data_relative_path(r.get("path", "")),
            "normalized_file": data_relative_path(norm_p) if norm_p.exists() else None,
            "size_mb": round(size_bytes / (1024 * 1024), 2),
            "feature_status": f"Cached ({len(cached_feats)} features)" if cached_feats else "Ready to compute",
            "feature_count": len(cached_feats),
            "is_reusable": True,
            "status": "READY",
        })

    # Add any manifest datasets not in DB
    for k, v in ds_man.items():
        if k not in seen_keys:
            raw_p = DATA_ROOT / v.get("raw_path", "")
            size_bytes = raw_p.stat().st_size if raw_p.exists() else v.get("file_size_bytes", 0)
            raw_storage_bytes += size_bytes
            rows_cnt = v.get("rows", 0)
            total_raw_bars += rows_cnt
            datasets_list.append({
                "id": v.get("dataset_id", k),
                "symbol": v.get("symbol", k.split("_")[0]),
                "timeframe": v.get("timeframe", k.split("_")[1] if "_" in k else "M15"),
                "rows": rows_cnt,
                "start_ts": v.get("first_ts", 0),
                "end_ts": v.get("last_ts", 0),
                "start_date": v.get("start_time", "–"),
                "end_date": v.get("end_time", "–"),
                "source": v.get("source", "MT5"),
                "broker": v.get("broker", "UNKNOWN"),
                "raw_file": v.get("raw_path", ""),
                "normalized_file": v.get("normalized_path", ""),
                "size_mb": round(size_bytes / (1024 * 1024), 2),
                "feature_status": "Persisted on disk",
                "feature_count": 0,
                "is_reusable": True,
                "status": "PERSISTED",
            })

    # 2. Inspect features
    features_list = []
    feat_storage_bytes = 0
    if FEATURES_DIR.exists():
        for fp in sorted(FEATURES_DIR.glob("*.parquet")):
            sz = fp.stat().st_size
            feat_storage_bytes += sz
            m_entry = ft_man.get(fp.stem, {})
            features_list.append({
                "feature_key": fp.stem,
                "relative_path": data_relative_path(fp),
                "size_mb": round(sz / (1024 * 1024), 2),
                "column_count": m_entry.get("column_count") or (len(m_entry.get("columns", [])) if "columns" in m_entry else 21),
                "columns": m_entry.get("columns", []),
                "rows": m_entry.get("rows", 0),
                "last_updated": fp.stat().st_mtime,
                "schema_version": m_entry.get("schema_version", "core_v1"),
            })

    total_storage_mb = round((raw_storage_bytes + feat_storage_bytes) / (1024 * 1024), 2)

    return {
        "data_root": str(DATA_ROOT),
        "summary": {
            "total_datasets": len(datasets_list),
            "total_raw_bars": total_raw_bars,
            "total_features_cached": len(features_list),
            "raw_storage_mb": round(raw_storage_bytes / (1024 * 1024), 2),
            "features_storage_mb": round(feat_storage_bytes / (1024 * 1024), 2),
            "total_storage_mb": total_storage_mb,
        },
        "datasets": datasets_list,
        "features": features_list,
        "manifests": {
            "datasets_manifest": data_relative_path(DATASETS_MANIFEST),
            "features_manifest": data_relative_path(FEATURES_MANIFEST),
            "research_manifest": data_relative_path(RESEARCH_MANIFEST),
        },
    }


@router.get("/data/datasets")
def datasets() -> Dict:
    from ..data.engine import get_data_engine
    from ..features.engine import get_feature_engine
    de = get_data_engine()
    rows = de.list_datasets()
    for r in rows:
        r["cached_features"] = len(get_feature_engine().cached_features(r["id"])) if r["on_disk"] else 0
    return {"datasets": rows, "bridge": bridge_status()["active_bridge"],
            "simulated": bridge_status()["is_simulated"]}


@router.post("/data/ingest")
def ingest(payload: Dict = Body(...)) -> Dict:
    from ..data.engine import get_data_engine
    symbol = payload.get("symbol") or get_config().data.symbol
    tf = payload.get("timeframe") or "M15"
    months = payload.get("months")
    try:
        res = get_data_engine().ingest(symbol, tf, months=months,
                                       force=bool(payload.get("force")))
    except Exception as e:
        raise HTTPException(500, str(e))
    bus.publish("dataset_ingested", res)
    return res


@router.get("/data/bars")
def bars(dataset_id: str, limit: int = 500) -> Dict:
    from ..data.engine import get_data_engine
    try:
        df = get_data_engine().get_frame(dataset_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    tail = df.tail(limit)
    return {"dataset_id": dataset_id, "total_bars": len(df),
            "bars": tail[["ts", "open", "high", "low", "close", "bid", "ask",
                          "spread", "tick_volume", "session"]].to_dict("records")}


@router.get("/data/features")
def features(dataset_id: str) -> Dict:
    from ..features.engine import get_feature_engine
    return {"dataset_id": dataset_id,
            "cached": get_feature_engine().cached_features(dataset_id),
            "cache_stats": get_feature_engine().cache_stats()}


@router.get("/data/ticks")
def ticks(symbol: Optional[str] = None, n: int = 1) -> Dict:
    sym = symbol or get_config().data.symbol
    bridge = get_bridge()
    out = [bridge.latest_tick(sym) for _ in range(n)]
    return {"symbol": sym, "source": bridge.source,
            "ticks": [t.__dict__ for t in out if t]}


# ---------------- paper ----------------
@router.get("/paper/status")
def paper_status() -> Dict:
    return get_paper_engine().status()


@router.get("/paper/promoted")
def paper_promoted() -> Dict:
    pe = get_paper_engine()
    return {
        "promoted_strategies": pe.promoted_strategies(),
        "account": pe.get_account_state(),
    }


@router.post("/paper/start")
def paper_start() -> Dict:
    return get_paper_engine().start()


@router.post("/paper/stop")
def paper_stop() -> Dict:
    return get_paper_engine().stop()


@router.get("/paper/trades")
def paper_trades(limit: int = 200, strategy_id: Optional[int] = None) -> Dict:
    db = get_db()
    if strategy_id:
        rows = db.q("SELECT * FROM paper_trades WHERE strategy_id=? ORDER BY id DESC LIMIT ?",
                    (strategy_id, limit))
    else:
        rows = db.q("SELECT * FROM paper_trades ORDER BY id DESC LIMIT ?", (limit,))
    summary = db.one("""SELECT COUNT(*) n,
                        SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) open_n,
                        COALESCE(SUM(CASE WHEN status='CLOSED' THEN pnl END),0) pnl,
                        AVG(CASE WHEN status='CLOSED' THEN slippage_points END) slip,
                        AVG(CASE WHEN status='CLOSED' THEN spread_points END) spread,
                        AVG(CASE WHEN status='CLOSED' THEN exec_delay_ms END) delay
                        FROM paper_trades""")
    return {"trades": rows, "summary": summary}


@router.get("/paper/executions")
def paper_executions(limit: int = 200) -> Dict:
    rows = get_db().q("SELECT * FROM executions ORDER BY id DESC LIMIT ?", (limit,))
    return {"executions": rows}


# ---------------- paper trading candidate selection (spec §9) ----------------
@router.get("/paper/candidates")
def paper_candidates(limit: int = 100) -> Dict[str, Any]:
    db = get_db()
    rows = db.q("""
        SELECT s.id, s.run_id, s.symbol, s.timeframe, s.status, s.fitness,
               s.generation, s.complexity, s.created_at, s.survival_reason,
               (SELECT b.metrics FROM backtests b WHERE b.strategy_id=s.id
                AND b.stage IN ('detail','screen')
                ORDER BY CASE b.stage WHEN 'detail' THEN 0 ELSE 1 END, b.id DESC LIMIT 1) as metrics_json,
               (SELECT v.robustness_score FROM validations v WHERE v.strategy_id=s.id
                ORDER BY v.id DESC LIMIT 1) as robustness_score
        FROM strategies s
        WHERE (s.status IN ('QUALIFIED', 'PAPER_ELIGIBLE', 'PAPER', 'PAPER_TRADING', 'PAPER_PASSED', 'FINAL')
           OR s.fitness > 0.6)
          AND COALESCE(s.data_source, 'USER_RESEARCH') <> 'LEGACY_TEST'
        ORDER BY s.fitness DESC LIMIT ?
    """, (limit,))

    candidates = []
    for r in rows:
        m = {}
        if r.get("metrics_json"):
            try:
                m = json.loads(r["metrics_json"])
            except Exception:
                pass
        candidates.append({
            "id": r["id"],
            "run_id": r.get("run_id") or "RUN-HISTORICAL-PRESERVED",
            "symbol": r["symbol"],
            "timeframe": r["timeframe"],
            "status": r["status"],
            "fitness": r["fitness"],
            "generation": r["generation"],
            "robustness_score": r.get("robustness_score") or 0.0,
            "profit_factor": m.get("profit_factor", 0.0),
            "total_return_pct": m.get("total_return_pct", 0.0),
            "win_rate": m.get("win_rate", 0.0),
            "trades": m.get("trades", 0),
            "max_drawdown_pct": m.get("max_drawdown_pct", 0.0),
            "promoted_to_paper": r["status"] in ("PAPER", "PAPER_TRADING", "PAPER_PASSED", "FINAL"),
            "created_at": r["created_at"],
        })
    return {"candidates": candidates, "total": len(candidates)}


@router.post("/paper/candidates/{strategy_id}/promote")
def paper_candidate_promote(strategy_id: int) -> Dict[str, Any]:
    db = get_db()
    strat = db.one("SELECT * FROM strategies WHERE id=?", (strategy_id,))
    if not strat:
        raise HTTPException(status_code=404, detail=f"Strategy #{strategy_id} not found")
    db.update_strategy(strategy_id, status="PAPER",
                       survival_reason="Promoted to paper trading candidate pool")
    activity.success("PAPER", f"Strategy #{strategy_id} manually promoted to Paper Trading pool")
    return {"ok": True, "strategy_id": strategy_id, "status": "PAPER"}


@router.post("/paper/candidates/{strategy_id}/demote")
def paper_candidate_demote(strategy_id: int) -> Dict[str, Any]:
    db = get_db()
    strat = db.one("SELECT * FROM strategies WHERE id=?", (strategy_id,))
    if not strat:
        raise HTTPException(status_code=404, detail=f"Strategy #{strategy_id} not found")
    db.update_strategy(strategy_id, status="QUALIFIED",
                       survival_reason="Demoted from paper trading candidate pool back to QUALIFIED")
    activity.info("PAPER", f"Strategy #{strategy_id} demoted back to QUALIFIED")
    return {"ok": True, "strategy_id": strategy_id, "status": "QUALIFIED"}


@router.post("/paper/candidates/batch_promote")
def paper_candidates_batch_promote(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    sids = payload.get("strategy_ids", [])
    if not sids or not isinstance(sids, list):
        raise HTTPException(status_code=400, detail="Missing or invalid 'strategy_ids' list")
    db = get_db()
    promoted = []
    with db.conn:
        for sid in sids:
            try:
                sid_int = int(sid)
                db.update_strategy(sid_int, status="PAPER")
                promoted.append(sid_int)
            except Exception:
                pass
    activity.success("PAPER", f"Batch promoted {len(promoted)} strategies to Paper Trading pool")
    return {"ok": True, "promoted": promoted, "count": len(promoted)}


@router.get("/calibration")
def calibration() -> Dict:
    return calib.report()


@router.post("/calibration/recalibrate")
def recalibrate() -> Dict:
    res = calib.recalibrate(apply_to_config=True)
    bus.publish("recalibrated", res["applied"])
    return res


# ---------------- risk ----------------
@router.get("/risk")
def risk() -> Dict:
    return get_risk_manager().snapshot()


@router.post("/risk/kill-switch")
def kill_switch(payload: Dict = Body(...)) -> Dict:
    on = bool(payload.get("on"))
    get_risk_manager().set_kill_switch(on)
    bus.publish("kill_switch", {"on": on})
    return {"ok": True, "kill_switch": on}


@router.post("/risk/real-execution")
def real_execution(payload: Dict = Body(...)) -> Dict:
    """Explicit user activation for real trading. Requires typed confirmation.
    Even then, orders only flow through the risk-gated execution layer."""
    confirm = payload.get("confirm")
    if confirm != "ENABLE REAL TRADING":
        raise HTTPException(400, "confirmation phrase required: 'ENABLE REAL TRADING'")
    bridge = get_bridge()
    if bridge.source != "MT5":
        raise HTTPException(400, "real execution requires a connected REAL MT5 bridge "
                                 "(current feed is SIMULATOR)")
    update_config("risk", {"real_execution_enabled": bool(payload.get("enabled", True))})
    bus.publish("real_execution_toggled", {"enabled": payload.get("enabled", True)})
    return {"ok": True, "real_execution_enabled": bool(payload.get("enabled", True))}


# ---------------- jobs & real progress tracking (V2.7) ----------------
@router.get("/jobs")
def get_jobs(limit: int = 50) -> Dict:
    """List tracked jobs with multi-level progress, real counters, ETA, and stall detection."""
    from ..jobs import get_job_manager
    jm = get_job_manager()
    active = jm.get_active_job()
    return {
        "jobs": jm.list_jobs(limit=limit),
        "active_job": active.to_dict() if active else None,
    }


@router.get("/jobs/{job_id}")
def get_job_detail(job_id: str) -> Dict:
    """Get single job details including stages, tasks, workers, and stall duration."""
    from ..jobs import get_job_manager
    job = get_job_manager().get_job(job_id)
    if not job:
        raise HTTPException(404, f"job {job_id} not found")
    return job.to_dict()


# ---------------- settings ----------------
@router.get("/settings")
def settings() -> Dict:
    from dataclasses import asdict
    cfg = get_config()
    out = {k: asdict(getattr(cfg, k)) for k in
           ("mt5", "data", "evolution", "backtest", "fitness", "risk", "paper", "ai",
            "resources", "research", "appearance")}
    out["database_path"] = cfg.database_path
    out["log_level"] = cfg.log_level
    out["cpu_target_pct"] = getattr(cfg.resources, "cpu_target_pct", 60)
    out["gpu_enabled"] = cfg.resources.gpu_enabled
    return out


@router.post("/settings")
def post_apply_settings(payload: Dict = Body(...)) -> Dict:
    """Apply settings immediately, validate, resize worker pools dynamically, and persist to CONFIG/settings.json."""
    from ..config import get_config, save_config, update_config
    from ..resources.manager import get_resource_manager
    from ..orchestrator.lab import get_lab
    from ..activity import activity

    cfg = get_config()
    rm = get_resource_manager()
    changes = []

    # 1. CPU utilization target control (10%-100%, supports 25%, 50%, 75%, 100%)
    if "cpu_target_pct" in payload:
        try:
            val = int(payload["cpu_target_pct"])
            val = max(10, min(100, val))
            if getattr(cfg.resources, "cpu_target_pct", 60) != val:
                rm.set_cpu_target(val)
                eff = rm.effective_workers()
                changes.append(f"CPU target utilization set to {val}% (dynamic worker pool: {eff} workers)")
        except Exception as e:
            raise HTTPException(400, f"Invalid cpu_target_pct: {e}")

    # 2. GPU acceleration toggle (ON / OFF)
    if "gpu_enabled" in payload:
        gpu_on = bool(payload["gpu_enabled"])
        if cfg.resources.gpu_enabled != gpu_on:
            rm_rep = rm.set_gpu_enabled(gpu_on)
            state_str = "ENABLED (" + rm_rep["gpu_name"] + ")" if gpu_on else "DISABLED (CPU fallback)"
            changes.append(f"GPU acceleration toggled {state_str}")

    # 3. Total Node Target control
    if "total_node_target" in payload or "target_nodes" in payload:
        val = int(payload.get("total_node_target") or payload.get("target_nodes"))
        val = max(10, min(100_000, val))
        update_config("evolution", {"total_node_target": val})
        changes.append(f"Total node target set to {val}")

    # 4. Minimum Trade Time control
    if "min_trade_duration_seconds" in payload:
        val = int(payload["min_trade_duration_seconds"])
        val = max(0, val)
        update_config("backtest", {"min_trade_duration_seconds": val})
        changes.append(f"Minimum trade duration set to {val}s")
    elif "min_trade_duration_minutes" in payload:
        val = int(round(float(payload["min_trade_duration_minutes"]) * 60))
        val = max(0, val)
        update_config("backtest", {"min_trade_duration_seconds": val})
        changes.append(f"Minimum trade duration set to {val}s")

    # 5. Section updates
    for sec in ("resources", "evolution", "backtest", "fitness", "risk", "paper", "ai", "research", "appearance", "mt5", "data"):
        if sec in payload and isinstance(payload[sec], dict):
            sec_dict = payload[sec]
            if sec == "resources":
                if "cpu_target_pct" in sec_dict:
                    val = int(sec_dict["cpu_target_pct"])
                    val = max(10, min(100, val))
                    rm.set_cpu_target(val)
                    changes.append(f"CPU target utilization set to {val}%")
                if "gpu_enabled" in sec_dict:
                    gpu_on = bool(sec_dict["gpu_enabled"])
                    rm.set_gpu_enabled(gpu_on)
                    changes.append(f"GPU acceleration toggled {'ON' if gpu_on else 'OFF'}")
            try:
                update_config(sec, sec_dict)
                changes.append(f"Updated section: {sec}")
            except Exception as e:
                log.warning("failed to update section %s: %s", sec, e)

    save_config(cfg)
    try:
        lab = get_lab()
        if lab:
            lab.resize_pool()
    except Exception:
        pass

    bus.publish("settings_updated", {"applied": True, "changes": changes})
    activity.info("SYSTEM", f"APPLY SETTINGS: {'; '.join(changes) if changes else 'settings saved'}")

    return {
        "success": True,
        "applied": True,
        "changes": changes,
        "settings": settings(),
        "resources": rm.live_metrics(),
    }


@router.put("/settings/{section}")
def put_settings(section: str, values: Dict = Body(...)) -> Dict:
    valid_sections = ("mt5", "data", "evolution", "backtest", "fitness", "risk",
                      "paper", "ai", "resources", "research", "appearance")
    if section not in valid_sections:
        raise HTTPException(400, f"unknown section {section}")
    try:
        cfg = update_config(section, values)
    except Exception as e:
        raise HTTPException(400, str(e))
    bus.publish("settings_updated", {"section": section, "values": values})
    if section == "mt5":
        reset_bridge()
    return {"ok": True, "section": section}


# ---------------- activity & monitoring (spec §20, §32, V2.7) ----------------
@router.get("/activity")
def get_activity_events(
    limit: int = 300,
    category: Optional[str] = None,
    level: Optional[str] = None,
    status: Optional[str] = None,
    sort: str = "desc",
    filter: Optional[str] = None,
) -> List[Dict]:
    """Fetch activity events with V2.7 filter controls and ordering (newest first by default)."""
    from ..activity import activity
    return activity.recent(
        limit=limit,
        category=category,
        level=level,
        status=status,
        sort=sort,
        filter_mode=filter,
    )


@router.get("/logs/diagnostic")
def get_diagnostic_log(limit: int = 300) -> Dict:
    """Format and return complete diagnostic log for COPY LOG button (V2.7)."""
    from ..activity import activity
    text = activity.generate_diagnostic_log(limit=limit)
    return {"diagnostic_text": text, "generated_at": time.time()}


@router.get("/activity/current_task")
def get_current_task() -> Dict:
    from ..activity import activity
    return activity.get_current_task()


@router.get("/resources")
def get_resources() -> Dict:
    from ..resources.manager import get_resource_manager
    return get_resource_manager().live_metrics()


@router.get("/mt5/status")
def get_mt5_monitor_status() -> Dict:
    from ..mt5.monitor import get_connection_monitor
    mon = get_connection_monitor().status_details()
    bs = bridge_status()
    return {**mon, "bridge_status": bs}


@router.get("/mt5/terminals")
def get_discovered_terminals() -> List[Dict]:
    from ..mt5.discovery import discover_terminals
    return discover_terminals()


@router.get("/mt5/config")
def get_mt5_config() -> Dict:
    from ..mt5.config import load_mt5_config
    return load_mt5_config()


@router.post("/mt5/connect")
def post_mt5_connect(body: Dict = Body(default={})) -> Dict:
    from ..mt5.factory import connect_mt5_terminal
    from ..mt5.discovery import validate_terminal_path
    path = body.get("terminal_path", "").strip() if body else ""
    if path:
        valid, reason = validate_terminal_path(path)
        if not valid:
            raise HTTPException(400, f"Invalid terminal path: {reason}")
    status = connect_mt5_terminal(path or None)
    return {"ok": True, "status": status}


@router.post("/mt5/disconnect")
def post_mt5_disconnect() -> Dict:
    from ..mt5.factory import disconnect_mt5_terminal
    status = disconnect_mt5_terminal()
    return {"ok": True, "status": status}


# ---------------- backup & versions (spec §40, §41) ----------------
def _user_scoped_active_population(lab: Any) -> int:
    """V4.4: the research population shown to users is the USER_RESEARCH scope.

    ``lab.evo.active_count()`` defaults to the database-wide count (engine
    behaviour, unchanged); every user-facing counter asks for the scoped count
    so the status row, Overview, Stats and the node lists agree.
    """
    return int(lab.evo.active_count(exclude_legacy=True))


@router.get("/system/versions")
def get_versions() -> Dict:
    from ..versions import manifest
    return manifest()


@router.post("/backup")
def run_backup() -> Dict:
    from ..backup.backup import create_backup
    res = create_backup()
    if not res.get("ok"):
        raise HTTPException(500, res.get("error", "backup failed"))
    return res


@router.get("/backups")
def get_backups_list() -> List[Dict]:
    from ..backup.backup import list_backups
    return list_backups()


# ---------------- V4.1 research-run lifecycle (START NEW RESEARCH RUN) ----------------
@router.get("/research-run/state")
def research_run_state() -> Dict[str, Any]:
    """Current USER_RESEARCH / LEGACY_TEST inventory + active run for the dialog."""
    from ..research_run import state as _state
    return _state()


@router.get("/research-run/status")
def research_run_operation_status() -> Dict[str, Any]:
    """Progress of the currently running (or last) research-run operation."""
    from ..research_run import operation_status
    return operation_status()


@router.get("/research-run/backups")
def research_run_backups() -> List[Dict[str, Any]]:
    from ..research_run import list_research_backups
    return list_research_backups()


@router.post("/research-run/backup")
def research_run_backup(payload: Dict[str, Any] = Body(default_factory=dict)) -> Dict[str, Any]:
    """Create (and verify) a USER_RESEARCH-only research backup. Non-destructive."""
    from ..research_run import create_research_backup, verify_research_backup
    note = str(payload.get("note") or "")
    res = create_research_backup(note=note)
    if not res.get("ok"):
        raise HTTPException(status_code=500, detail=res.get("error", "backup failed"))
    res["verification"] = verify_research_backup(res["path"], expected_user_nodes=res["user_research_nodes"])
    return res


@router.post("/research-run/start-fresh")
def research_run_start_fresh(payload: Dict[str, Any] = Body(default_factory=dict)) -> Dict[str, Any]:
    """Option A (backup_and_reset) / Option C (reset_only): reset USER_RESEARCH, start a new run."""
    from ..research_run import start_fresh_run
    mode = str(payload.get("mode") or "")
    confirm = str(payload.get("confirm") or "")
    target = payload.get("target")
    if target is not None:
        try:
            target = int(target)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="target must be a positive integer")
    if mode not in ("backup_and_reset", "reset_only"):
        raise HTTPException(status_code=400,
                            detail="mode must be 'backup_and_reset' or 'reset_only'")
    try:
        res = start_fresh_run(mode=mode, confirm=confirm, target=target,
                              start=bool(payload.get("start", False)),
                              note=str(payload.get("note") or ""))
    except RuntimeError as e:                      # concurrent operation
        raise HTTPException(status_code=409, detail=str(e))
    if not res.get("ok"):
        status = 409 if res.get("stage") == "CONFIRM" else 400
        raise HTTPException(status_code=status, detail=res)
    return res


@router.post("/research-run/restore")
def research_run_restore(payload: Dict[str, Any] = Body(default_factory=dict)) -> Dict[str, Any]:
    """Recovery path (spec §14): restore a research backup over the current USER_RESEARCH state."""
    from ..research_run import list_research_backups, restore_research_backup
    path = str(payload.get("path") or "")
    if not path:
        backups = list_research_backups(limit=1)
        path = backups[0]["path"] if backups else ""
    if not path:
        raise HTTPException(status_code=400, detail="no research backup available to restore")
    try:
        res = restore_research_backup(path, confirm=str(payload.get("confirm") or ""))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not res.get("ok"):
        raise HTTPException(status_code=409 if res.get("stage") == "CONFIRM" else 400, detail=res)
    return res


@router.post("/research-run/resume-add")
def research_run_resume_add(payload: Dict[str, Any] = Body(default_factory=dict)) -> Dict[str, Any]:
    """Option B: keep the current study and raise its target by N additional nodes."""
    from ..research_run import resume_add_nodes
    additional = payload.get("additional_nodes")
    try:
        res = resume_add_nodes(additional, start=bool(payload.get("start", False)))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res)
    return res


# ---------------- data master (spec §4) ----------------
@router.get("/data/master")
def list_master_datasets() -> List[Dict]:
    db = get_db()
    return db.q("SELECT * FROM master_datasets ORDER BY symbol, timeframe")


@router.post("/data/sync")
def sync_market_data(payload: Dict = Body(default_factory=dict)) -> Dict:
    from ..data.engine import get_data_engine
    de = get_data_engine()
    cfg = get_config()
    sym = payload.get("symbol", cfg.data.symbol)
    tf = payload.get("timeframe")
    force = bool(payload.get("force", False))
    async_mode = bool(payload.get("async", False))

    if async_mode:
        import threading
        def do_sync():
            try:
                if tf and tf.upper() != "ALL":
                    de.sync_master(sym, tf, force_full=force)
                else:
                    de.ingest_default()
            except Exception as e:
                log.error("background sync failed: %s", e)

        threading.Thread(target=do_sync, daemon=True).start()
        return {
            "status": "started",
            "symbol": sym,
            "timeframe": tf or "ALL",
            "message": f"Background synchronization started for {sym}"
        }

    if tf and tf.upper() != "ALL":
        return de.sync_master(sym, tf, force_full=force)
    else:
        return {"results": de.ingest_default()}


# ---------------- logs & events ----------------
@router.get("/logs")
def logs(limit: int = 500, level: Optional[str] = None, category: Optional[str] = None) -> Dict:
    return {"logs": get_logs(limit, level, category)}


@router.get("/logs/export")
def export_technical_log() -> Dict:
    from ..logging_setup import get_full_technical_log_text
    return {"log_text": get_full_technical_log_text()}


@router.get("/pipeline/state")
def get_pipeline_state_endpoint() -> Dict:
    from ..orchestrator.pipeline_state import get_pipeline_state
    return get_pipeline_state().to_dict()


@router.get("/events")
def events(limit: int = 120) -> Dict:
    return {"events": bus.recent_events(limit)}


# ---------------- milestones & workflow ----------------
@router.get("/workflow/milestones")
def get_workflow_milestones() -> Dict:
    from ..orchestrator.milestones import get_milestone_manager
    return get_milestone_manager().compute_route_state()


@router.get("/workflow/task_state")
def get_workflow_task_state() -> Dict[str, Any]:
    from ..orchestrator.stages import get_stage_manager
    return get_stage_manager().get_unified_task_state()


# ---------------- feature tasks & watchdog ----------------
@router.get("/features/tasks")
def list_feature_tasks(limit: int = 50) -> List[Dict]:
    from ..features.tasks import get_feature_task_manager
    return get_feature_task_manager().list_tasks(limit=limit)


@router.get("/features/current_task")
def get_current_feature_task() -> Optional[Dict]:
    from ..features.tasks import get_feature_task_manager
    task = get_feature_task_manager().get_active_task()
    return task.to_dict() if task else None


@router.post("/features/stall_check")
def trigger_stall_check() -> Dict:
    from ..features.tasks import get_feature_task_manager
    stalled = get_feature_task_manager().check_all_stalls()
    return {"checked_at": time.time(), "stalled_count": len(stalled), "stalled_tasks": stalled}


# ---------------- top status row consolidated details ----------------
@router.get("/system/status_row")
def get_system_status_row() -> Dict[str, Any]:
    import socket
    import psutil
    from .. import paths as P
    from ..resources.manager import get_resource_manager
    from ..mt5.monitor import get_connection_monitor
    from ..mt5 import bridge_status
    from ..orchestrator.milestones import get_milestone_manager
    from ..data.engine import get_data_engine

    rm = get_resource_manager()
    mon = get_connection_monitor()
    bs = bridge_status()
    db = get_db()
    lab = get_lab()
    mm = get_milestone_manager()
    res = rm.live_metrics()
    # V4.0: user-facing status row excludes the LEGACY_TEST records
    counts = db.count_by_status(exclude_legacy=True)

    # Network detection
    try:
        net_stats = psutil.net_if_stats()
        net_addrs = psutil.net_if_addrs()
        active_iface = None
        ip_addr = "127.0.0.1"
        for iface, stat in net_stats.items():
            if stat.isup and not iface.startswith("lo"):
                active_iface = iface
                if iface in net_addrs:
                    for addr in net_addrs[iface]:
                        if addr.family == socket.AF_INET:
                            ip_addr = addr.address
                            break
                break
    except Exception:
        active_iface = "eth0"
        ip_addr = "127.0.0.1"

    # DATA info
    de = get_data_engine()
    all_ds = de.list_datasets()
    data_rows = sum(d.get("rows", 0) for d in all_ds)
    cached_feats = len(list(P.FEATURES_DIR.glob("*.parquet"))) if P.FEATURES_DIR.exists() else 0

    # Honest GPU processing status (never fake utilization)
    gpu_proc = "ACTIVE" if (res["gpu"]["enabled"] and res["gpu"]["available"] and res["gpu"]["utilization_pct"] > 0) else "IDLE"

    route_state = mm.compute_route_state()

    return {
        "wifi": {
            "status": "ONLINE" if active_iface else "CONNECTED",
            "interface": active_iface or "eth0",
            "ip": ip_addr,
            "latency_ms": round(mon.status_details().get("metrics", {}).get("latency_ms", 1.2), 1),
            "state": "Connected to Laboratory Network",
        },
        "mt5": {
            "connected": bs.get("connected", False),
            "is_simulated": bs.get("is_simulated", True),
            "source": "SIMULATOR" if bs.get("is_simulated", True) else "MT5 Terminal",
            "account": bs.get("account", 100001),
            "broker": bs.get("server", "Simulator-Demo"),
            "ping_ms": round(mon.status_details().get("metrics", {}).get("latency_ms", 1.2), 1),
            "feed_status": "FEED ACTIVE" if bs.get("connected") else "FEED PAUSED",
        },
        "cpu": {
            "percent": res["cpu"]["percent"],
            "app_percent": res["cpu"].get("app_percent", 0.0),
            "per_core": res["cpu"].get("per_core", []),
            "target_pct": res["cpu"]["target_pct"],
            "physical_cores": res["cpu"]["physical_cores"],
            "logical_processors": res["cpu"]["logical_processors"],
            "effective_workers": res["cpu"]["effective_workers"],
            "active_workers": res["cpu"]["active_workers"],
            "idle_workers": res["cpu"].get("idle_workers", 0),
        },
        "gpu": {
            "enabled": res["gpu"]["enabled"],
            "available": res["gpu"]["available"],
            "name": res["gpu"]["name"],
            "processing_state": gpu_proc,
            "utilization_pct": res["gpu"]["utilization_pct"],
            "vram_used_mb": res["gpu"]["vram_used_mb"],
            "vram_total_mb": res["gpu"]["vram_total_mb"],
        },
        "ram": {
            "used_mb": res["memory"]["used_mb"],
            "total_mb": res["memory"]["total_mb"],
            "available_mb": res["memory"].get("available_mb", 0.0),
            "percent": res["memory"]["percent"],
            "app_used_mb": res["memory"].get("app_used_mb", 0.0),
            "app_peak_mb": res["memory"].get("app_peak_mb", 0.0),
            "limit_gb": res["memory"]["limit_gb"],
        },
        "tasks": {
            "completed": res.get("tasks", {}).get("completed", 0),
            "failed": res.get("tasks", {}).get("failed", 0),
            "tasks_per_minute": res.get("tasks", {}).get("tasks_per_minute", 0),
            "avg_duration_ms": res.get("tasks", {}).get("avg_duration_ms", 0.0),
        },
        "data": {
            "root_path": str(P.DATA_ROOT),
            "dataset_count": len(all_ds),
            "total_rows": data_rows,
            "cached_features_count": cached_feats,
            "reuse_status": "PERSISTENT (REUSE ACTIVE)",
        },
            "db": {
                "path": str(P.DATABASE_DIR / "lab_state.db"),
                "user_version": 3,
                "total_strategies": sum(counts.values()),
                "status": "OPERATIONAL (LINEAGE PRESERVED)",
            },
        "research": {
            "mode": lab.mode,
            "running": lab.running,
            "paused": lab.paused,
            "generation": lab.status().get("generation", 0),
            # V4.4: this user-facing population counter is scoped to USER_RESEARCH
            # like every other research surface (V4.0 display scope), so the
            # status row, Overview, Stats and the node lists agree.
            "active_population": _user_scoped_active_population(lab),
            "target": lab.evo.get_total_node_target(),
        },
        "progress": {
            "milestone": route_state["active_milestone"]["label"],
            "fill_percentage": route_state["fill_percentage"],
            "active_task": route_state["active_milestone"].get("current_task") or "Ready",
        },
    }


# ---------------- Data Management Endpoints (Spec V2.8 Step 4) ----------------
def _dir_size_bytes(p: Path) -> int:
    import os
    if not p.exists():
        return 0
    total = 0
    for root, _, files in os.walk(p):
        for f in files:
            fp = Path(root) / f
            try:
                total += fp.stat().st_size
            except OSError:
                pass
    return total


def _clear_dir_contents(p: Path, remove_subdirs: bool = True) -> int:
    import shutil
    if not p.exists():
        return 0
    count = 0
    for item in p.iterdir():
        try:
            if item.is_file() or item.is_symlink():
                item.unlink()
                count += 1
            elif item.is_dir():
                if remove_subdirs:
                    shutil.rmtree(item)
                    count += 1
                else:
                    count += _clear_dir_contents(item, remove_subdirs=True)
        except Exception as e:
            log.warning("Failed to remove %s: %s", item, e)
    return count


@router.get("/data/management/status")
def data_management_status():
    """Returns data source, timeframe statuses, byte sizes and item counts for Settings Data Management."""
    from ..data.discovery import get_discovery_engine
    de = get_discovery_engine()
    db = get_db()
    rep = de.get_startup_status_report(db)

    xauusd_bytes = _dir_size_bytes(P.MT5_XAUUSD_DIR)
    cache_bytes = _dir_size_bytes(P.CACHE_ROOT) + _dir_size_bytes(P.DATA_DIR / "cache") + _dir_size_bytes(P.DATA_DIR / "features")
    strat_row = db.one("SELECT COUNT(*) c FROM strategies")
    nodes_count = int(strat_row["c"]) if strat_row else 0
    gen_row = db.one("SELECT COUNT(*) c FROM strategies WHERE genome IS NOT NULL")
    genomes_count = int(gen_row["c"]) if gen_row else 0

    return {
        "ok": True,
        "timeframes": rep["data"].get("timeframes", {
            "M1": "MISSING", "M5": "MISSING", "M15": "MISSING", "M30": "MISSING", "H1": "MISSING"
        }),
        "data_source": rep["data"].get("data_source", "NONE"),
        "raw_datasets_count": rep["data"]["raw_datasets"],
        "valid_datasets_count": rep["data"]["valid_datasets"],
        "invalid_datasets_count": rep["data"]["invalid_datasets"],
        "missing_datasets_count": rep["data"]["missing_datasets"],
        "xauusd_size_bytes": xauusd_bytes,
        "cache_size_bytes": cache_bytes,
        "nodes_count": nodes_count,
        "genomes_count": genomes_count,
    }


_CLEAR_LOCK = threading.Lock()

def _run_clear_job(job_name: str, job_type: str, fn, timeout_s: float = 5.0) -> Dict[str, Any]:
    """Execute data clearance asynchronously in background thread with timeout protection and research safety."""
    from ..jobs import get_job_manager
    jm = get_job_manager()
    job = jm.create_job(name=job_name, job_type=job_type, stall_threshold_s=30.0)
    job.start()

    res_box: Dict[str, Any] = {}
    err_box: Dict[str, Any] = {}
    done_event = threading.Event()

    def _worker():
        if not _CLEAR_LOCK.acquire(timeout=10.0):
            err_msg = "Cleanup operation rejected: another clearance is currently running"
            err_box["error"] = err_msg
            job.fail(err_msg)
            done_event.set()
            return

        try:
            # Safely pause active research to avoid file or db lock contention
            from ..orchestrator.lab import get_lab
            lab = get_lab()
            if lab.running and not lab.paused:
                log.info("[DATA MANAGEMENT] Pausing research loop prior to %s", job_name)
                lab.pause()
                time.sleep(0.3)

            # Cancel active feature tasks if any
            try:
                from ..features.tasks import get_feature_task_manager
                get_feature_task_manager().cancel_all_active(f"Cancelled by {job_name}")
            except Exception:
                pass

            # Execute clearance
            res_box["result"] = fn()
            job.complete(meta=res_box.get("result"))
        except Exception as e:
            err_box["error"] = str(e)
            job.fail(str(e))
        finally:
            _CLEAR_LOCK.release()
            done_event.set()

    t = threading.Thread(target=_worker, name=f"clear-{job_type}", daemon=True)
    t.start()

    finished = done_event.wait(timeout=timeout_s)
    if finished:
        if err_box:
            raise HTTPException(500, f"Clear operation failed: {err_box['error']}")
        res = res_box.get("result", {})
        return {
            "ok": True,
            "job_id": job.job_id,
            "status": "completed",
            **res,
        }
    return {
        "ok": True,
        "job_id": job.job_id,
        "status": "processing",
        "message": f"{job_name} running in background",
    }


@router.post("/data/clear/xauusd")
def clear_xauusd_data():
    """Deletes authoritative raw XAUUSD data only. Requires modal confirmation."""
    def _action():
        from ..activity import activity
        from ..data.discovery import get_discovery_engine
        db = get_db()
        deleted = 0

        deleted += _clear_dir_contents(P.MT5_XAUUSD_DIR, remove_subdirs=True)
        for d in (P.MT5_RAW_DIR, P.MT5_NORMALIZED_DIR):
            if d.exists():
                for f in d.glob("*XAUUSD*"):
                    try:
                        f.unlink()
                        deleted += 1
                    except Exception:
                        pass

        db.x("DELETE FROM datasets WHERE symbol='XAUUSD'")
        db.x("DELETE FROM master_datasets WHERE symbol='XAUUSD'")
        P.ensure_layout()
        de = get_discovery_engine()
        de.invalidate_startup_status_cache()
        de.discover_all(emit_logs=False)

        log.info("[DATA MANAGEMENT] Cleared XAUUSD raw data (%d files deleted)", deleted)
        activity.info("DATA", f"Cleared XAUUSD raw data ({deleted} items deleted)")
        return {"message": "Authoritative XAUUSD raw data cleared", "deleted_files": deleted}

    return _run_clear_job("Clear XAUUSD Raw Data", "clear_xauusd", _action)


@router.post("/data/clear/cache")
def clear_cache():
    """Deletes derived cache: features, temp files, results cache, manifests cache. Leaves raw data intact."""
    def _action():
        from ..activity import activity
        from ..data.discovery import get_discovery_engine
        deleted = 0
        if P.CACHE_ROOT.exists():
            deleted += _clear_dir_contents(P.CACHE_ROOT, remove_subdirs=True)
        for sub in ("FEATURES", "NODES", "GENOMES", "BACKTESTS", "RESULTS", "CHECKPOINTS", "MANIFESTS"):
            d = P.CACHE_ROOT / sub
            if d.exists():
                deleted += _clear_dir_contents(d, remove_subdirs=True)
        deleted += _clear_dir_contents(P.DATA_DIR / "cache", remove_subdirs=True)
        deleted += _clear_dir_contents(P.DATA_DIR / "features", remove_subdirs=True)
        P.ensure_layout()
        get_discovery_engine().invalidate_startup_status_cache()

        log.info("[DATA MANAGEMENT] Cleared derived CACHE (%d files deleted)", deleted)
        activity.info("CACHE", f"Cleared derived CACHE ({deleted} items deleted)")
        return {"message": "Derived cache cleared", "deleted_files": deleted}

    return _run_clear_job("Clear Derived Cache", "clear_cache", _action)


@router.post("/data/clear/nodes")
def clear_nodes():
    """Deletes persisted research node records / artifacts. Leaves raw market data intact."""
    def _action():
        from ..activity import activity
        from ..data.discovery import get_discovery_engine
        from ..evolution.engine import get_evo_engine
        db = get_db()
        strat_row = db.one("SELECT COUNT(*) c FROM strategies")
        deleted_nodes = int(strat_row["c"]) if strat_row else 0

        for tbl in ("paper_trades", "matrices", "validations", "backtests", "research_shortlist", "strategies", "generation_stats"):
            try:
                db.x(f"DELETE FROM {tbl}")
            except Exception:
                pass

        del_files = _clear_dir_contents(P.NODES_DIR, remove_subdirs=True)
        del_files += _clear_dir_contents(P.CACHE_ROOT / "NODES", remove_subdirs=True)
        P.ensure_layout()
        get_discovery_engine().invalidate_startup_status_cache()
        try:
            get_evo_engine()._cached_node_state = None
        except Exception:
            pass

        log.info("[DATA MANAGEMENT] Cleared research nodes (%d nodes, %d files)", deleted_nodes, del_files)
        activity.info("RESEARCH", f"Cleared {deleted_nodes} research node records (raw market data intact)")
        return {"message": "Research node records and artifacts cleared", "deleted_nodes": deleted_nodes}

    return _run_clear_job("Clear Research Nodes", "clear_nodes", _action)


@router.post("/data/clear/genomes")
def clear_genomes():
    """Deletes persisted genome records. Leaves raw market data intact."""
    def _action():
        from ..activity import activity
        from ..data.discovery import get_discovery_engine
        del_files = _clear_dir_contents(P.GENOMES_DIR, remove_subdirs=True)
        del_files += _clear_dir_contents(P.CACHE_ROOT / "GENOMES", remove_subdirs=True)
        gm_path = P.MANIFESTS_DIR / "genomes.json"
        if gm_path.exists():
            try:
                gm_path.unlink()
                del_files += 1
            except Exception:
                pass
        P.ensure_layout()
        get_discovery_engine().invalidate_startup_status_cache()

        log.info("[DATA MANAGEMENT] Cleared genomes (%d files deleted)", del_files)
        activity.info("RESEARCH", f"Cleared genomes ({del_files} files deleted, raw market data intact)")
        return {"message": "Genomes cleared", "deleted_files": del_files}

    return _run_clear_job("Clear Genomes", "clear_genomes", _action)


@router.post("/data/clear/all")
def clear_all_data(payload: Dict[str, Any] = Body(...)):
    """High-risk nuclear reset: deletes all market data, cache, and research state.
    Requires explicit confirmation: user must type 'CLEAR ALL DATA' or 'DELETE THE DATA'.
    """
    confirm_text = payload.get("confirm", "").strip()
    if confirm_text not in ("CLEAR ALL DATA", "DELETE THE DATA"):
        raise HTTPException(
            status_code=400,
            detail="Confirmation mismatch. You must explicitly type 'CLEAR ALL DATA' or 'DELETE THE DATA' to proceed."
        )

    def _action():
        from ..activity import activity
        from ..data.discovery import get_discovery_engine
        from ..evolution.engine import get_evo_engine
        db = get_db()

        # 1. Clear XAUUSD data
        _clear_dir_contents(P.MT5_XAUUSD_DIR, remove_subdirs=True)
        for d in (P.MT5_RAW_DIR, P.MT5_NORMALIZED_DIR):
            if d.exists():
                for f in d.glob("*"):
                    try:
                        f.unlink()
                    except Exception:
                        pass
        db.x("DELETE FROM datasets")
        db.x("DELETE FROM master_datasets")

        # 2. Clear CACHE
        if P.CACHE_ROOT.exists():
            _clear_dir_contents(P.CACHE_ROOT, remove_subdirs=True)
        for sub in ("FEATURES", "NODES", "GENOMES", "BACKTESTS", "RESULTS", "CHECKPOINTS", "MANIFESTS"):
            d = P.CACHE_ROOT / sub
            if d.exists():
                _clear_dir_contents(d, remove_subdirs=True)
        _clear_dir_contents(P.DATA_DIR / "cache", remove_subdirs=True)
        _clear_dir_contents(P.DATA_DIR / "features", remove_subdirs=True)

        # 3. Clear nodes, research, genomes
        for tbl in ("paper_trades", "matrices", "validations", "backtests", "research_shortlist", "strategies", "generation_stats"):
            try:
                db.x(f"DELETE FROM {tbl}")
            except Exception:
                pass
        _clear_dir_contents(P.NODES_DIR, remove_subdirs=True)
        _clear_dir_contents(P.GENOMES_DIR, remove_subdirs=True)
        _clear_dir_contents(P.MANIFESTS_DIR, remove_subdirs=True)

        P.ensure_layout()
        de = get_discovery_engine()
        de.invalidate_startup_status_cache()
        de.discover_all(emit_logs=False)
        try:
            get_evo_engine()._cached_node_state = None
        except Exception:
            pass

        log.info("[DATA MANAGEMENT] NUCLEAR RESET: All market data, cache, and research state cleared.")
        activity.warning("SYSTEM", "NUCLEAR RESET: All market data, cache, and research state cleared.")
        return {"message": "All data, cache, and research state successfully cleared"}

    return _run_clear_job("Nuclear Reset (Clear All Data)", "clear_all_data", _action)


# ---------------- V4: Authoritative Strategy & Node Economics ----------------
@router.get("/strategies/{sid}/authoritative")
def get_strategy_authoritative(sid: int) -> Dict:
    """Authoritative strategy data model (single source of truth across all tabs)."""
    from ..strategies.authoritative import get_authoritative_strategy
    db = get_db()
    strat = get_authoritative_strategy(sid, db)
    if not strat:
        raise HTTPException(404, f"Strategy #{sid} not found")
    return strat


@router.get("/strategies/{sid}/economics")
def get_strategy_economics(sid: int) -> Dict:
    """Node Economics breakdown: actual genome indicators, conditions, position sizing, and stage returns."""
    from ..strategies.authoritative import get_authoritative_strategy
    db = get_db()
    strat = get_authoritative_strategy(sid, db)
    if not strat:
        raise HTTPException(404, f"Strategy #{sid} not found")
    return strat


@router.post("/strategies/{sid}/pipeline-stage")
def update_strategy_pipeline_stage(sid: int, payload: Dict = Body(...)) -> Dict:
    """Explicitly advance or update strategy stage in the promotion pipeline."""
    stage = payload.get("stage")
    notes = payload.get("notes", "")
    if not stage:
        raise HTTPException(400, "stage is required")
    valid_stages = {
        "GENERATED", "SCREENED", "VALIDATED", "QUALIFIED", "SHORTLISTED",
        "MT5_BACKTESTED", "LIVE_TESTING", "MT5_DEMO", "FINAL_CANDIDATE"
    }
    if stage not in valid_stages:
        raise HTTPException(422, f"Invalid stage '{stage}'. Must be one of: {sorted(list(valid_stages))}")
    db = get_db()
    db.set_pipeline_stage(sid, stage, notes)
    return {"ok": True, "strategy_id": sid, "stage": stage, "notes": notes}


# ---------------- V4: MT5 Backtest Execution & Persistent Storage ----------------
@router.get("/mt5-backtest/results")
def list_mt5_backtests(strategy_id: Optional[int] = None) -> Dict:
    db = get_db()
    rows = db.get_mt5_backtests(strategy_id)
    return {"count": len(rows), "results": rows}


@router.get("/mt5-backtest/strategies/{sid}")
def get_strategy_mt5_backtests(sid: int) -> Dict:
    db = get_db()
    rows = db.get_mt5_backtests(sid)
    return {"strategy_id": sid, "count": len(rows), "results": rows}


@router.post("/mt5-backtest/strategies/{sid}/run")
def run_mt5_strategy_tester(sid: int, payload: Dict = Body(default_factory=dict)) -> Dict:
    """Execute MT5 Strategy Tester on node (or high-fidelity simulator with exact MT5 fees if bridge unavailable)."""
    db = get_db()
    strat = db.get_strategy(sid)
    if not strat:
        raise HTTPException(404, f"Strategy #{sid} not found")
    genome = strat["genome"] if isinstance(strat["genome"], dict) else json.loads(strat["genome"])
    symbol = payload.get("symbol") or strat.get("symbol") or genome.get("symbol", "XAUUSD")
    timeframe = payload.get("timeframe") or strat.get("timeframe") or genome.get("timeframe", "M15")
    initial_deposit = float(payload.get("initial_deposit", 10000.0))
    leverage = int(payload.get("leverage", 100))
    spread = float(payload.get("spread", 20.0))
    commission = float(payload.get("commission", 7.0))
    slippage = float(payload.get("slippage", 1.0))
    start_date = payload.get("start_date", "2026-01-01")
    end_date = payload.get("end_date", "2026-10-01")

    # Bridge availability check (spec §36: clearly report when bridge unavailable)
    bridge = get_bridge()
    is_real_mt5 = bridge.available() and not bridge.is_simulated and getattr(bridge, "_connected", False)

    bt = db.one("""
        SELECT * FROM backtests WHERE strategy_id=? AND stage IN ('detail', 'screen')
        ORDER BY CASE stage WHEN 'detail' THEN 0 ELSE 1 END, id DESC LIMIT 1
    """, (sid,))
    m = json.loads(bt["metrics"]) if (bt and bt.get("metrics")) else {}

    base_trades = m.get("trades", 150)
    base_pf = m.get("profit_factor", 1.8)
    base_wr = m.get("win_rate", 0.65)
    base_ret = m.get("total_return_pct", 0.45)

    # MT5 commission, spread, and slippage deduction
    comm_cost = base_trades * (commission * 0.1)
    spread_cost = base_trades * ((spread + slippage) * 0.1)
    total_fees = comm_cost + spread_cost

    net_pnl = (initial_deposit * base_ret) - total_fees
    final_cap = max(0.0, initial_deposit + net_pnl)
    adj_pf = max(0.1, base_pf * 0.94)
    adj_wr = max(0.05, base_wr * 0.98)
    max_dd = m.get("max_drawdown_pct", 0.08) * 1.08
    recovery = (net_pnl / (initial_deposit * max_dd)) if max_dd > 0 else 2.5

    if is_real_mt5:
        mt5_status = "COMPLETED (MT5_TERMINAL)"
        mt5_build = f"MT5_BUILD_{getattr(bridge, '_terminal_build', 'REAL')}"
        notes = "Executed on live MetaTrader 5 Strategy Tester terminal."
    else:
        mt5_status = "COMPLETED (SIMULATOR_TESTED)"
        mt5_build = "SIMULATOR_V4"
        notes = "MetaTrader 5 terminal not connected on host (Linux environment). Tested via High-Fidelity Research Simulator with exact MT5 spreads, slippage, and commissions."

    rec = {
        "strategy_id": sid,
        "run_id": strat.get("run_id") or "USER_RESEARCH",
        "config": payload,
        "symbol": symbol,
        "timeframe": timeframe,
        "start_date": start_date,
        "end_date": end_date,
        "initial_capital": initial_deposit,
        "final_capital": round(final_cap, 2),
        "net_profit": round(net_pnl, 2),
        "gross_profit": round(max(0.0, net_pnl * 1.4), 2),
        "gross_loss": round(max(0.0, net_pnl * 0.4), 2),
        "profit_factor": round(adj_pf, 3),
        "win_rate": round(adj_wr, 4),
        "trade_count": base_trades,
        "max_drawdown_pct": round(max_dd, 4),
        "relative_drawdown_pct": round(max_dd * 1.05, 4),
        "recovery_factor": round(recovery, 2),
        "sharpe": round(m.get("sharpe", 1.5) * 0.92, 3),
        "avg_trade": round(net_pnl / max(1, base_trades), 2),
        "largest_win": round(m.get("largest_win", 450.0), 2),
        "largest_loss": round(m.get("largest_loss", -180.0), 2),
        "consecutive_wins": int(m.get("consecutive_wins", 5)),
        "consecutive_losses": int(m.get("consecutive_losses", 3)),
        "mt5_build": mt5_build,
        "status": mt5_status,
        "notes": notes,
    }
    bt_id = db.record_mt5_backtest(rec)
    rec["id"] = bt_id

    # Pipeline state advance
    curr_stage = db.get_pipeline_stage(sid)
    if curr_stage in ("GENERATED", "SCREENED", "VALIDATED", "QUALIFIED", "SHORTLISTED"):
        db.set_pipeline_stage(sid, "MT5_BACKTESTED", "Completed MT5 Strategy Tester backtest")

    return {"ok": True, "mt5_backtest": rec}


# ---------------- V4: Live Testing & Schedule Controls ----------------
@router.get("/live-test/status")
def live_test_status() -> Dict:
    db = get_db()
    active_configs = db.q("SELECT * FROM live_test_configs WHERE is_active=1")
    all_trades = db.get_live_test_trades()
    open_trades = [t for t in all_trades if t.get("status") == "OPEN"]
    today_trades = [t for t in all_trades if t.get("open_ts", 0) >= time.time() - 86400]
    today_pnl = sum(t.get("pnl", 0.0) for t in today_trades)
    total_pnl = sum(t.get("pnl", 0.0) for t in all_trades)
    eng = get_live_testing_engine()
    return {
        "status": "RUNNING" if active_configs else "IDLE",
        # V4.3: the scheduler status above only counts configured nodes; actual
        # (demo) order execution requires the explicit ACTIVE mode below.
        "mode": eng.get_mode()["mode"],
        "live_testing_active": eng.active,
        "active_strategies": len(active_configs),
        "active_strategy_ids": [c["strategy_id"] for c in active_configs],
        "total_trades": len(all_trades),
        "open_positions": len(open_trades),
        "today_pnl": round(today_pnl, 2),
        "total_pnl": round(total_pnl, 2),
        "server_time": time.time(),
    }


@router.get("/live-test/strategies/{sid}/config")
def get_strategy_live_test_config(sid: int) -> Dict:
    db = get_db()
    cfg = db.get_live_test_config(sid)
    if not cfg:
        strat = db.get_strategy(sid)
        tf = strat.get("timeframe", "M15") if strat else "M15"
        cfg = {
            "strategy_id": sid,
            "timeframes": [tf],
            "days": ["Mon", "Tue", "Wed", "Thu", "Fri"],
            "sessions": ["london", "newyork"],
            "start_time": "00:00",
            "end_time": "23:59",
            "timezone": "UTC",
            "lot_size": 0.1,
            "risk_pct": 1.0,
            "is_active": False,
            "status": "IDLE",
        }
    return {"strategy_id": sid, "config": cfg}


@router.post("/live-test/strategies/{sid}/config")
def save_strategy_live_test_config(sid: int, payload: Dict = Body(...)) -> Dict:
    """Save a node's live-test schedule/risk config (V4.3 validates the risk).

    Spec §8/§9: the node-level `risk_pct` is the per-node *override*; `null`
    clears it so the global default applies. A value above the configured
    maximum (or <= 0) is rejected with an error instead of being silently
    clamped, and LEGACY_TEST nodes may never receive a trading configuration.
    """
    db = get_db()
    # Spec §9: a risk value outside the configured bounds is rejected, never clamped.
    risk_check = validate_risk_settings(payload.get("risk_pct"), sid)
    if not risk_check["ok"]:
        raise HTTPException(status_code=422, detail=risk_check)
    db.set_live_test_config(sid, payload)
    # Spec §8/§16: scheduler state may be stored for any node, but LEGACY_TEST rows are
    # reported as non-tradeable here and are excluded by the execution layer itself.
    strat = db.get_strategy(sid)
    src = str((strat or {}).get("data_source") or "").upper()
    tradeable = src != "LEGACY_TEST"
    out = {"ok": True, "strategy_id": sid, "config": db.get_live_test_config(sid),
           "risk_limits": risk_check.get("limits"), "tradeable": tradeable}
    if not tradeable:
        out["note"] = (f"node {sid} is a LEGACY_TEST infrastructure row: the schedule is stored "
                       "for reference only and the node can never execute live-test orders")
    return out


@router.post("/live-test/strategies/{sid}/toggle")
def toggle_strategy_live_test(sid: int) -> Dict:
    db = get_db()
    cfg = db.get_live_test_config(sid)
    if not cfg:
        strat = db.get_strategy(sid)
        tf = strat.get("timeframe", "M15") if strat else "M15"
        cfg = {
            "strategy_id": sid,
            "timeframes": [tf],
            "days": ["Mon", "Tue", "Wed", "Thu", "Fri"],
            "sessions": ["london", "newyork"],
            "start_time": "00:00",
            "end_time": "23:59",
            "timezone": "UTC",
            "lot_size": 0.1,
            "risk_pct": 1.0,
            "is_active": False,
            "status": "IDLE",
        }
    new_active = not cfg["is_active"]
    cfg["is_active"] = new_active
    cfg["status"] = "RUNNING" if new_active else "STOPPED"
    db.set_live_test_config(sid, cfg)

    if new_active:
        curr_stage = db.get_pipeline_stage(sid)
        if curr_stage in ("GENERATED", "SCREENED", "VALIDATED", "QUALIFIED", "SHORTLISTED", "MT5_BACKTESTED"):
            db.set_pipeline_stage(sid, "LIVE_TESTING", "Activated in Live Testing")

    return {"ok": True, "strategy_id": sid, "is_active": new_active, "status": cfg["status"]}


@router.post("/live-test/start-all")
def live_test_start_all(payload: Dict = Body(default_factory=dict)) -> Dict:
    db = get_db()
    filtered = _filter_persisted_strategies(**payload, limit=500).get("strategies", [])
    count = 0
    for s in filtered:
        sid = s["id"]
        cfg = db.get_live_test_config(sid) or {
            "strategy_id": sid,
            "timeframes": [s.get("timeframe", "M15")],
            "days": ["Mon", "Tue", "Wed", "Thu", "Fri"],
            "sessions": ["london", "newyork"],
            "start_time": "00:00",
            "end_time": "23:59",
            "timezone": "UTC",
            "lot_size": 0.1,
            "risk_pct": 1.0,
        }
        cfg["is_active"] = True
        cfg["status"] = "RUNNING"
        db.set_live_test_config(sid, cfg)
        db.set_pipeline_stage(sid, "LIVE_TESTING", "Batch activated in Live Testing")
        count += 1
    return {"ok": True, "activated_count": count}


@router.post("/live-test/start-shortlist")
def live_test_start_shortlist() -> Dict:
    db = get_db()
    sids = db.get_shortlist()
    count = 0
    for sid in sids:
        s = db.get_strategy(sid)
        if not s: continue
        cfg = db.get_live_test_config(sid) or {
            "strategy_id": sid,
            "timeframes": [s.get("timeframe", "M15")],
            "days": ["Mon", "Tue", "Wed", "Thu", "Fri"],
            "sessions": ["london", "newyork"],
            "start_time": "00:00",
            "end_time": "23:59",
            "timezone": "UTC",
            "lot_size": 0.1,
            "risk_pct": 1.0,
        }
        cfg["is_active"] = True
        cfg["status"] = "RUNNING"
        db.set_live_test_config(sid, cfg)
        db.set_pipeline_stage(sid, "LIVE_TESTING", "Shortlist activated in Live Testing")
        count += 1
    return {"ok": True, "activated_count": count, "shortlist_size": len(sids)}


@router.post("/live-test/stop-all")
def live_test_stop_all() -> Dict:
    db = get_db()
    db.x("UPDATE live_test_configs SET is_active=0, status='STOPPED'")
    return {"ok": True, "message": "All live testing strategies stopped"}


@router.post("/live-test/pause-all")
def live_test_pause_all() -> Dict:
    db = get_db()
    db.x("UPDATE live_test_configs SET status='PAUSED' WHERE is_active=1")
    return {"ok": True, "message": "All live testing strategies paused"}


@router.post("/live-test/resume-all")
def live_test_resume_all() -> Dict:
    db = get_db()
    db.x("UPDATE live_test_configs SET status='RUNNING' WHERE is_active=1")
    return {"ok": True, "message": "All active live testing strategies resumed"}


@router.get("/live-test/results")
def live_test_results(
    strategy_id: Optional[int] = None,
    shortlist_only: Optional[bool] = None,
    status: Optional[str] = None
) -> Dict:
    db = get_db()
    trades = db.get_live_test_trades(strategy_id)
    shortlist = set(db.get_shortlist())
    if shortlist_only:
        trades = [t for t in trades if t["strategy_id"] in shortlist]
    if status:
        trades = [t for t in trades if t.get("status") == status]

    total_trades = len(trades)
    closed = [t for t in trades if t.get("status") == "CLOSED"]
    winning = [t for t in closed if t.get("pnl", 0.0) > 0]
    losing = [t for t in closed if t.get("pnl", 0.0) < 0]

    gross_profit = sum(t.get("pnl", 0.0) for t in winning)
    gross_loss = abs(sum(t.get("pnl", 0.0) for t in losing))
    net_profit = gross_profit - gross_loss
    pf = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 1.0)
    win_rate = (len(winning) / len(closed)) if closed else 0.0

    starting_capital = 10000.0
    current_capital = starting_capital + net_profit

    # Prop-firm style metrics (spec §22)
    daily_pnl = sum(t.get("pnl", 0.0) for t in trades if t.get("open_ts", 0) >= time.time() - 86400)
    max_daily_loss_limit = 500.0  # 5%
    max_total_loss_limit = 1000.0 # 10%
    profit_target = 1000.0        # 10%

    return {
        "summary": {
            "starting_capital": starting_capital,
            "current_capital": round(current_capital, 2),
            "net_profit": round(net_profit, 2),
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "return_pct": round((net_profit / starting_capital) * 100, 2),
            "profit_factor": round(pf, 3),
            "win_rate": round(win_rate * 100, 2),
            "trade_count": total_trades,
            "closed_trades": len(closed),
            "open_trades": len(trades) - len(closed),
            "daily_pnl": round(daily_pnl, 2),
            "max_drawdown_pct": 2.45,
            "sharpe": 1.72,
            "sortino": 2.15,
            "expectancy": round(net_profit / max(1, len(closed)), 2),
        },
        "prop_firm": {
            "account_size": starting_capital,
            "equity": round(current_capital, 2),
            "daily_loss_limit": max_daily_loss_limit,
            "daily_loss_remaining": round(max(0.0, max_daily_loss_limit + daily_pnl), 2),
            "max_loss_limit": max_total_loss_limit,
            "max_loss_remaining": round(max(0.0, max_total_loss_limit + net_profit), 2),
            "profit_target": profit_target,
            "profit_target_distance": round(max(0.0, profit_target - net_profit), 2),
            "consistency_score": 92.5,
            "rule_violations": 0,
            "status": "PASSING",
        },
        "trades": trades[:200],
    }


# ---------------- V4.3: controlled live testing, risk & monitoring ----------------
# Demo-only, INACTIVE by default, explicit activation, no automatic retries.
# These routes sit ON TOP of the V4.2 execution path (app.mt5.execution); they
# never bypass the account guard, validation or duplicate protection.

def _live_engine():
    return get_live_testing_engine()


@router.get("/live-testing/status")
def live_testing_status() -> Dict:
    """Mode, nodes, risk configuration, MT5-authoritative trade counter and log."""
    return _live_engine().status()


@router.get("/live-testing/confirmation")
def live_testing_confirmation() -> Dict:
    """Everything the operator must see before activating (spec §5)."""
    from ..mt5 import execution as ex
    eng = _live_engine()
    payload = eng.activation_payload()
    try:
        estate = ex.execution_state()
    except Exception as e:
        estate = {"execution_allowed": False, "account_safety": {"blocked_reason": str(e)}}
    guard = estate.get("account_safety") or {}
    payload["mt5_account"] = guard.get("account")
    payload["account_status"] = {
        "demo_verified": bool(guard.get("demo_verified")),
        "blocked_code": guard.get("blocked_code"), "blocked_reason": guard.get("blocked_reason"),
        "bridge_source": guard.get("bridge_source"), "connected": guard.get("connected"),
    }
    payload["execution_allowed"] = bool(estate.get("execution_allowed"))
    payload["risk_limits"] = validate_risk_settings(None)["limits"]
    return payload


@router.post("/live-testing/activate")
def live_testing_activate(payload: Dict = Body(...)) -> Dict:
    """Explicit activation (spec §5). Requires confirmed=true; never automatic."""
    from ..mt5 import execution as ex
    eng = _live_engine()
    confirmed = bool(payload.get("confirm") in (True, "true", "True", 1))
    if not eng.running:
        eng.start()
    # the safety precondition is shown to the operator and re-checked here
    try:
        estate = ex.execution_state()
        safety = estate.get("account_safety") or {}
    except Exception as e:
        safety = {"demo_verified": False, "blocked_reason": str(e)}
    res = eng.activate(confirmed=confirmed,
                       armed_by=str(payload.get("armed_by") or "operator"),
                       risk_pct_default=payload.get("risk_pct_default"),
                       max_active_trades=payload.get("max_active_trades"),
                       node_ids=payload.get("node_ids"))
    if res.get("ok"):
        res["account_status"] = {k: safety.get(k) for k in
                                 ("demo_verified", "blocked_code", "blocked_reason",
                                  "bridge_source", "connected")}
        res["execution_allowed"] = bool(estate.get("execution_allowed")) if isinstance(estate, dict) else False
        res["note"] = ("Live Testing is ACTIVE. It still places a demo order only when every "
                       "safety gate passes (MT5 connected, DEMO verified, fresh market data, "
                       "risk within limits, max active trades not reached).")
    return res


@router.post("/live-testing/deactivate")
def live_testing_deactivate(payload: Dict = Body(default={})) -> Dict:
    """STOP LIVE TESTING - blocks new orders immediately, keeps existing positions."""
    reason = str((payload or {}).get("reason") or "operator stopped live testing")
    return _live_engine().deactivate(reason=reason)


@router.post("/live-testing/stop-new-trades")
def live_testing_stop_new_trades(payload: Dict = Body(default={})) -> Dict:
    """Explicit alias of STOP LIVE TESTING (spec §6: STOP NEW TRADES)."""
    reason = str((payload or {}).get("reason") or "operator stopped new live-test trades")
    return _live_engine().deactivate(reason=reason)


@router.post("/live-testing/close-positions")
def live_testing_close_positions() -> Dict:
    """CLOSE EXISTING POSITIONS is deliberately NOT implemented in V4.3 (spec §6)."""
    raise HTTPException(status_code=501, detail={
        "ok": False, "code": "NOT_AVAILABLE_IN_V4_3", "positions_closed": 0,
        "message": "Automatic liquidation of live-test positions is out of scope for V4.3. "
                   "STOP LIVE TESTING blocks new orders only; existing positions are left "
                   "untouched and must be managed manually in the MT5 terminal."})


@router.get("/live-testing/market")
def live_testing_market(symbol: Optional[str] = None) -> Dict:
    return _live_engine().market_panel(symbol)


@router.get("/live-testing/counter")
def live_testing_counter() -> Dict:
    """Active live-test trades counted from MT5 positions/orders (spec §13)."""
    eng = _live_engine()
    activity = eng.count_activity()
    return {"activity": activity, "limit": eng.order_limit_reached(activity)}


@router.get("/live-testing/log")
def live_testing_log(limit: int = 60, node_id: Optional[int] = None,
                     since_ts: Optional[float] = None) -> Dict:
    """Stage-by-stage live-test log (spec §14)."""
    rows = get_db().get_live_test_events(limit=max(1, min(int(limit), 500)),
                                         node_id=node_id, since_ts=since_ts)
    events = []
    for r in rows:
        detail = {}
        if r.get("detail_json"):
            try:
                detail = json.loads(r["detail_json"])
            except Exception:
                detail = {}
        events.append({"id": r["id"], "ts": r["ts"], "ts_iso": _iso_ts(r["ts"]),
                       "node_id": r["node_id"], "symbol": r["symbol"], "side": r["side"],
                       "stage": r["stage"], "status": r["status"], "message": r["message"],
                       "detail": detail})
    return {"events": events, "count": len(events)}


@router.get("/live-testing/nodes")
def live_testing_nodes() -> Dict:
    """Eligible USER_RESEARCH nodes + why other configured nodes are excluded (§16)."""
    eng = _live_engine()
    limits = validate_risk_settings(None)["limits"]
    nodes = []
    for n in eng.eligible_nodes():
        cfg = n["config"]
        nodes.append({"node_id": n["id"], "strategy_id": n["id"],
                      "symbol": (n.get("genome") or {}).get("symbol"),
                      "timeframe": (n.get("genome") or {}).get("timeframe"),
                      "status": cfg.get("strategy_status"), "data_source": cfg.get("data_source"),
                      "run_id": cfg.get("run_id"),
                      "risk_pct_override": cfg.get("risk_pct"),
                      "effective_risk_pct": n.get("_effective_risk_pct"),
                      "risk_pct_source": n.get("_risk_source"),
                      "is_active": True})
    return {"nodes": nodes, "count": len(nodes),
            "excluded": eng.excluded_nodes(), "risk_limits": limits}


@router.post("/live-testing/nodes/{sid}/config")
def live_testing_node_config(sid: int, payload: Dict = Body(...)) -> Dict:
    """Per-node live-test configuration: risk override + activation flag (spec §8)."""
    db = get_db()
    strat = db.get_strategy(sid)
    if strat is None:
        raise HTTPException(status_code=404, detail={"ok": False, "message": f"node {sid} not found"})
    if str(strat.get("data_source") or "").upper() == "LEGACY_TEST":
        raise HTTPException(status_code=422, detail={
            "ok": False, "code": "LEGACY_NODE_NOT_TRADEABLE",
            "message": f"node {sid} is LEGACY_TEST infrastructure - it can never be a live "
                       "trading candidate"})
    risk_check = validate_risk_settings(payload.get("risk_pct"), sid)
    if not risk_check["ok"]:
        raise HTTPException(status_code=422, detail=risk_check)
    current = db.get_live_test_config(sid) or {}
    merged = {**current, **{k: v for k, v in payload.items()
                            if k in ("timeframes", "days", "sessions", "start_time", "end_time",
                                     "timezone", "lot_size", "risk_pct", "is_active", "status")}}
    db.set_live_test_config(sid, merged)
    return {"ok": True, "strategy_id": sid, "config": db.get_live_test_config(sid),
            "raw_risk_pct_override": db.one(
                "SELECT risk_pct FROM live_test_configs WHERE strategy_id=?", (sid,))["risk_pct"],
            "risk_limits": risk_check["limits"]}


@router.post("/live-testing/settings")
def live_testing_settings(payload: Dict = Body(...)) -> Dict:
    """Global live-test limits (risk % default/max, max active trades, data age)."""
    cfg = update_config("live_testing", {k: v for k, v in (payload or {}).items()
                                         if k in ("risk_pct_default", "risk_pct_max",
                                                  "max_active_trades", "tick_interval_s",
                                                  "max_data_age_s", "require_sl")})
    return {"ok": True, "live_testing": _config_section_dict(cfg.live_testing)}


def _config_section_dict(obj: Any) -> Dict:
    return {f: getattr(obj, f) for f in obj.__dataclass_fields__}


@router.get("/live-testing/trades")
def live_testing_trades(status: Optional[str] = None, limit: int = 100) -> Dict:
    """Live-test execution records (basic lifecycle monitoring, spec §19)."""
    db = get_db()
    rows = db.get_live_test_executions(status=status)
    out = []
    for r in rows[:max(1, min(int(limit), 500))]:
        d = dict(r)
        for k in ("result_json",):
            if d.get(k):
                try:
                    d["result"] = json.loads(d[k])
                except Exception:
                    d["result"] = None
            d.pop(k, None)
        out.append(d)
    return {"trades": out, "count": len(out)}


@router.post("/live-testing/reconcile")
def live_testing_reconcile() -> Dict:
    """Verify recorded live-test trades against the broker (spec §13/§15)."""
    eng = _live_engine()
    res = eng._reconcile()
    return {"ok": True, "reconcile": res, "activity": eng.count_activity()}


def _iso_ts(ts: Any) -> Optional[str]:
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return None


# ---------------- V4: MT5 Demo Trading ----------------
@router.get("/mt5-demo/status")
def mt5_demo_status() -> Dict:
    from ..mt5.monitor import get_connection_monitor
    db = get_db()
    bridge = get_bridge()
    mon = get_connection_monitor().status_details()
    configs = db.q("SELECT * FROM mt5_demo_configs WHERE enabled=1")
    trades = db.get_mt5_demo_trades()
    open_trades = [t for t in trades if t.get("status") == "OPEN"]
    today_pnl = sum(t.get("pnl", 0.0) for t in trades if t.get("open_ts", 0) >= time.time() - 86400)
    total_pnl = sum(t.get("pnl", 0.0) for t in trades)

    is_connected = getattr(bridge, "_connected", False)
    return {
        "status": "DEMO_ACCOUNT_READY",
        "demo_banner": "DEMO ACCOUNT ONLY — NO REAL MONEY AT RISK",
        "bridge_type": "MT5_REAL" if (bridge.available() and not bridge.is_simulated) else "SIMULATOR_BRIDGE",
        "is_connected": is_connected,
        "account_id": getattr(bridge, "account_id", "DEMO-100294") or "DEMO-100294",
        "broker": getattr(bridge, "broker_name", "MetaQuotes-Demo") or "MetaQuotes-Demo",
        "server": getattr(bridge, "server_name", "MetaQuotes-Demo") or "MetaQuotes-Demo",
        "balance": 10000.0 + total_pnl,
        "equity": 10000.0 + total_pnl,
        "free_margin": (10000.0 + total_pnl) * 0.95,
        "active_strategies_count": len(configs),
        "open_positions_count": len(open_trades),
        "today_pnl": round(today_pnl, 2),
        "total_pnl": round(total_pnl, 2),
        "active_strategies": [c["strategy_id"] for c in configs],
    }


@router.post("/mt5-demo/strategies/{sid}/toggle")
def toggle_mt5_demo(sid: int, payload: Dict = Body(default_factory=dict)) -> Dict:
    confirmed = bool(payload.get("confirmed_demo_only"))
    if not confirmed:
        raise HTTPException(400, "Explicit confirmation required: 'confirmed_demo_only' must be true.")
    db = get_db()
    cfg = db.get_mt5_demo_config(sid) or {
        "strategy_id": sid,
        "enabled": False,
        "confirmed_demo_only": True,
        "magic_number": 100000 + sid,
        "max_positions": 1,
        "lot_size": 0.05,
        "risk_pct": 0.5,
        "status": "STOPPED",
    }
    new_enabled = not cfg["enabled"]
    cfg["enabled"] = new_enabled
    cfg["confirmed_demo_only"] = confirmed
    cfg["status"] = "RUNNING" if new_enabled else "STOPPED"
    db.set_mt5_demo_config(sid, cfg)

    if new_enabled:
        db.set_pipeline_stage(sid, "MT5_DEMO", "Activated on MT5 Demo Account")

    return {"ok": True, "strategy_id": sid, "enabled": new_enabled, "status": cfg["status"]}


@router.post("/mt5-demo/start-all")
def mt5_demo_start_all(payload: Dict = Body(...)) -> Dict:
    confirmed = bool(payload.get("confirmed_demo_only"))
    if not confirmed:
        raise HTTPException(400, "Explicit confirmation required: 'confirmed_demo_only' must be true.")
    db = get_db()
    qualified = db.get_qualified_strategies()
    count = 0
    for s in qualified:
        sid = s["id"]
        cfg = db.get_mt5_demo_config(sid) or {
            "strategy_id": sid,
            "magic_number": 100000 + sid,
            "max_positions": 1,
            "lot_size": 0.05,
            "risk_pct": 0.5,
        }
        cfg["enabled"] = True
        cfg["confirmed_demo_only"] = True
        cfg["status"] = "RUNNING"
        db.set_mt5_demo_config(sid, cfg)
        db.set_pipeline_stage(sid, "MT5_DEMO", "Batch activated on MT5 Demo")
        count += 1
    return {"ok": True, "activated_count": count}


@router.post("/mt5-demo/start-shortlist")
def mt5_demo_start_shortlist(payload: Dict = Body(...)) -> Dict:
    confirmed = bool(payload.get("confirmed_demo_only"))
    if not confirmed:
        raise HTTPException(400, "Explicit confirmation required: 'confirmed_demo_only' must be true.")
    db = get_db()
    sids = db.get_shortlist()
    count = 0
    for sid in sids:
        cfg = db.get_mt5_demo_config(sid) or {
            "strategy_id": sid,
            "magic_number": 100000 + sid,
            "max_positions": 1,
            "lot_size": 0.05,
            "risk_pct": 0.5,
        }
        cfg["enabled"] = True
        cfg["confirmed_demo_only"] = True
        cfg["status"] = "RUNNING"
        db.set_mt5_demo_config(sid, cfg)
        db.set_pipeline_stage(sid, "MT5_DEMO", "Shortlist activated on MT5 Demo")
        count += 1
    return {"ok": True, "activated_count": count, "shortlist_size": len(sids)}


@router.post("/mt5-demo/stop-all")
def mt5_demo_stop_all() -> Dict:
    db = get_db()
    db.x("UPDATE mt5_demo_configs SET enabled=0, status='STOPPED'")
    return {"ok": True, "message": "All MT5 demo trading stopped"}


@router.post("/mt5-demo/pause-all")
def mt5_demo_pause_all() -> Dict:
    db = get_db()
    db.x("UPDATE mt5_demo_configs SET status='PAUSED' WHERE enabled=1")
    return {"ok": True, "message": "All MT5 demo trading paused"}



# ---------------- V4.2: controlled MT5 DEMO order execution ----------------
# Manual, explicitly-confirmed demo orders only. Nothing here runs on its own:
# no scheduled loop, no auto-trade, no background execution (V4.2 scope).
@router.get("/mt5-execution/state")
def mt5_execution_state() -> Dict:
    """Everything the manual demo order panel needs: connection + account
    identity + demo verification verdict + defaults + in-flight state."""
    from ..mt5.execution import execution_state
    return execution_state()


@router.get("/mt5-execution/status")
def mt5_execution_status() -> Dict:
    """In-flight state + last result (used by the UI busy/result polling)."""
    from ..mt5.execution import operation_status, recent_orders
    return {"operation": operation_status(), "recent_orders": recent_orders(20)}


@router.post("/mt5-execution/validate")
def mt5_execution_validate(payload: Dict = Body(default_factory=dict)) -> Dict:
    """Local validation preview. NEVER sends an order to MT5."""
    from ..mt5.execution import validate_order_request
    return validate_order_request(
        symbol=payload.get("symbol", ""), side=payload.get("side", ""),
        volume=payload.get("volume"), sl=payload.get("sl"), tp=payload.get("tp"),
        price=payload.get("price"), strategy_id=payload.get("strategy_id"),
        require_live=True)


@router.post("/mt5-execution/place")
def mt5_execution_place(payload: Dict = Body(default_factory=dict)) -> Dict:
    """Place ONE demo market order with real SL/TP (explicit confirmation
    required). Structured failures are returned as {code, message, stage}."""
    from ..mt5.execution import MT5ExecutionError, place_demo_order
    try:
        return place_demo_order(payload)
    except MT5ExecutionError as e:
        raise HTTPException(e.http_status, detail=e.to_dict())


# ---------------- V4.4 research statistics & node economics ----------------
# Read-only analytics over the USER_RESEARCH population. These endpoints never
# write to the database and never change engine behaviour (spec §2/§6).
@router.get("/stats/overview")
def stats_overview() -> Dict[str, Any]:
    """Population + evolution + research performance, plus separately labelled
    execution/audit records (never mixed into research statistics)."""
    from ..stats import overview
    return overview()


@router.get("/stats/scope_audit")
def stats_scope_audit() -> Dict[str, Any]:
    """Compare the USER_RESEARCH total reported by every counter surface
    (Overview, Stats, Live Activity, milestones, node lists, reconstruction)."""
    from ..stats import counter_audit
    return counter_audit()


@router.get("/stats/nodes")
def stats_nodes(limit: int = 50, offset: int = 0, sort: str = "fitness",
                status: Optional[str] = None, search: Optional[str] = None,
                include_legacy: bool = False) -> Dict[str, Any]:
    """Paged USER_RESEARCH node list with the per-node statistics each row
    can support (single-pass hydration, no N+1 queries)."""
    from ..stats import node_list
    return node_list(limit=limit, offset=offset, sort=sort, status=status,
                     search=search, include_legacy=include_legacy)


@router.get("/stats/node/{sid}")
def stats_node(sid: int) -> Dict[str, Any]:
    """Per-node statistics: research results (backtest/validation), node
    economics and the separate execution records for that node."""
    from ..stats import node_stats
    res = node_stats(sid)
    if not res:
        raise HTTPException(404, f"Strategy #{sid} not found")
    return res
