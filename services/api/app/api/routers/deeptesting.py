"""DeepTesting API (Phase 3): runs, trades, scenarios, ranking, fine-tune.

Every run executes the Phase 2 compiled spec through the ONE shared
engine under explicit capital/risk/max-active constraints, gated by the
ISSUE-060 readiness verdict for the exact instrument/timeframe/range.
Failed runs persist as FAILED with the exact reason - never as results.
"""
from __future__ import annotations

import json
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from hero1_storage import instrument_models as im
from hero1_storage.repositories import NodeRepository

from ... import deeptesting_service as dts
from ... import spec_service, task_service
from ...deps import (get_instrument_context, get_instrument_session,
                     get_registry_session)

router = APIRouter(prefix="/api/v1/instruments/{instrument}/deeptesting", tags=["deeptesting"])


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    node_ids: List[int] = Field(min_length=1)
    config: dict = Field(default_factory=dict)


@router.post("/runs")
def create_runs(body: RunRequest, instrument: str = Depends(get_instrument_context),
                session: Session = Depends(get_instrument_session),
                registry: Session = Depends(get_registry_session),
                async_mode: bool = False):
    if not task_service.accepting_jobs():
        raise HTTPException(503,"HERO1 is stopping background work; retry after process status is clear")
    if async_mode:
        repo0 = NodeRepository(session, instrument)
        entries = []
        for nid in body.node_ids:
            n0 = repo0.get(nid)
            entries.append({"node_id": nid, "name": n0.name if n0 else f"Node {nid}",
                            "status": "QUEUED", "trades": 0, "progress_pct": None,
                            "run_id": None, "error": None})
        task = task_service.create_task(
            registry, category="DEEPTESTING", instrument=instrument,
            label=f"DeepTesting {len(body.node_ids)} node(s) [{instrument}]",
            total_units=len(body.node_ids),
            payload={"nodes": entries, "config_mode": body.config.get("mode")})
        dts.run_batch_async(instrument=instrument, node_ids=list(body.node_ids),
                            config=dict(body.config), task_id=task.id)
        return {"task_id": task.id, "async": True, "runs": []}
    repo = NodeRepository(session, instrument)
    out = []
    for node_id in body.node_ids:
        node = repo.get(node_id)
        if node is None:
            out.append({"node_id": node_id, "status": "FAILED",
                        "error": f"NODE_NOT_FOUND: node {node_id} not found for {instrument}"})
            continue
        run = dts.execute_run(session, instrument=instrument, node=node,
                              config=dict(body.config),
                              scenario_id=body.config.get("scenario_id"))
        out.append(dts.run_summary(run))
    return {"runs": out}


@router.get("/runs")
def list_runs(node_id: Optional[int] = None, status: Optional[str] = None,
              instrument: str = Depends(get_instrument_context),
              session: Session = Depends(get_instrument_session)):
    q = session.query(im.DeepTestRun).filter_by(instrument_id=instrument.upper())
    if node_id:
        q = q.filter_by(node_id=node_id)
    if status:
        q = q.filter_by(status=status)
    rows = q.order_by(im.DeepTestRun.id.desc()).limit(200).all()
    return {"runs": [dts.run_summary(r) for r in rows]}


@router.get("/runs/{run_id}")
def get_run(run_id: int, instrument: str = Depends(get_instrument_context),
            session: Session = Depends(get_instrument_session)):
    run = session.query(im.DeepTestRun).filter_by(id=run_id, instrument_id=instrument.upper()).first()
    if run is None:
        raise HTTPException(404, "run not found")
    out = dts.run_summary(run)
    out["data_quality"] = json.loads(run.data_quality_json) if run.data_quality_json else None
    out["assumptions"] = json.loads(run.assumptions_json) if run.assumptions_json else None
    out["equity_curve"] = json.loads(run.equity_json) if run.equity_json else []
    out["skipped_signals"] = json.loads(run.skipped_json) if run.skipped_json else []
    return out


@router.get("/runs/{run_id}/trades")
def get_trades(run_id: int, filters: Optional[str] = None,
               page: Optional[int] = None, page_size: str = "25",
               sort: str = "entry_ts", dir: str = "asc", cells: Optional[str] = None,
               instrument: str = Depends(get_instrument_context),
               session: Session = Depends(get_instrument_session)):
    rows = session.query(im.DeepTestRunTrade).filter_by(run_id=run_id).order_by(im.DeepTestRunTrade.entry_ts).all()
    trades = [{**(json.loads(r.detail_json) if r.detail_json else {}), "id": r.id}
              for r in rows]
    if filters:
        try:
            trades = dts.apply_trade_filters(trades, json.loads(filters))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(422, f"invalid filters: {exc}") from exc
    if page is not None:
        cell_list = None
        if cells:
            try:
                cell_list = list(json.loads(cells))
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(422, f"invalid cells: {exc}") from exc
        structured = None
        if filters:
            try:
                parsed_filters = json.loads(filters)
                if isinstance(parsed_filters, dict) and "conditions" not in parsed_filters:
                    structured = parsed_filters
            except Exception:  # noqa: BLE001 - legacy path already validated above
                structured = None
        return dts.paginate_trades(trades, page=page,
                                   page_size=("all" if page_size == "all" else int(page_size)),
                                   sort=sort, direction=dir, cells=cell_list,
                                   filters=structured)
    return {"trades": trades, "count": len(trades)}


@router.get("/runs/{run_id}/audit-export")
def audit_export(run_id: int, scope: str = "full", filters: Optional[str] = None,
                 cells: Optional[str] = None,
                 instrument: str = Depends(get_instrument_context),
                 session: Session = Depends(get_instrument_session)):
    """EXPORT TRADING DATA: complete original-run audit ZIP (Section 7).
    scope=filtered exports only the currently filtered trade subset and
    says so in the manifest; the default is the complete run."""
    from fastapi import Response
    from ... import audit_export as ax
    run = session.get(im.DeepTestRun, run_id)
    if run is None or run.instrument_id != instrument.upper():
        raise HTTPException(404, f"run {run_id} not found for {instrument}")
    node = session.get(im.Node, run.node_id)
    rows = session.query(im.DeepTestRunTrade).filter_by(run_id=run_id).order_by(
        im.DeepTestRunTrade.entry_ts).all()
    trades = [{**(json.loads(r.detail_json) if r.detail_json else {}), "id": r.id}
              for r in rows]
    label = "FULL_RUN"
    if scope == "filtered":
        label = "FILTERED_SUBSET"
        cell_list = None
        if cells:
            try:
                cell_list = list(json.loads(cells))
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(422, f"invalid cells: {exc}") from exc
        structured = None
        if filters:
            try:
                parsed = json.loads(filters)
                structured = parsed if isinstance(parsed, dict) else None
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(422, f"invalid filters: {exc}") from exc
        page = dts.paginate_trades(trades, page=0, page_size="all",
                                   cells=cell_list, filters=structured)
        trades = page["trades"]
    from app.decision_ledger import load_all_events
    ledger_events = load_all_events(session, run_id)
    data = ax.build_audit_zip(run=run, node=node, trades=trades, scope=label,
                              decision_events=ledger_events)
    return Response(content=data, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="hero1_audit_run{run_id}_{label.lower()}.zip"'})


@router.post("/runs/{run_id}/rerun")
def rerun(run_id: int, instrument: str = Depends(get_instrument_context),
          session: Session = Depends(get_instrument_session)):
    old = session.query(im.DeepTestRun).filter_by(id=run_id).first()
    if old is None:
        raise HTTPException(404, "run not found")
    repo = NodeRepository(session, instrument)
    node = repo.get(old.node_id)
    if node is None:
        raise HTTPException(404, "node no longer exists; historical run is preserved")
    config = json.loads(old.config_json) if old.config_json else {}
    run = dts.execute_run(session, instrument=instrument, node=node, config=config,
                          scenario_id=old.scenario_id)
    return dts.run_summary(run)


class RankRequest(BaseModel):
    run_ids: List[int]
    weights: dict = Field(default_factory=lambda: {"net_profit": 1.0, "profit_factor": 1.0,
                                                   "max_drawdown_pct": 1.0, "expectancy": 1.0})
    min_trades: int = 0
    max_dd_pct: Optional[float] = None


@router.post("/rankings")
def rankings(body: RankRequest, instrument: str = Depends(get_instrument_context),
             session: Session = Depends(get_instrument_session)):
    rows = session.query(im.DeepTestRun).filter(im.DeepTestRun.id.in_(body.run_ids)).all()
    items = [{"run_id": r.id, "node_id": r.node_id,
              "metrics": json.loads(r.metrics_json) if r.metrics_json else {}}
             for r in rows if r.status == "DONE"]
    return {"ranking": dts.rank_runs(items, body.weights, body.min_trades, body.max_dd_pct)}


# ------------------------------------------------------------------ scenarios
class ScenarioBody(BaseModel):
    name: str
    folder: Optional[str] = None
    config: dict


@router.get("/scenarios")
def list_scenarios(instrument: str = Depends(get_instrument_context),
                   session: Session = Depends(get_instrument_session)):
    rows = session.query(im.DeepTestScenarioDef).filter_by(
        instrument_id=instrument.upper()).order_by(im.DeepTestScenarioDef.id.desc()).all()
    return {"scenarios": [{"id": r.id, "name": r.name, "folder": r.folder,
                           "version": r.version, "parent_scenario_id": r.parent_scenario_id,
                           "node_id": r.node_id, "node_version": r.node_version,
                           "source_run_id": r.source_run_id,
                           "filters": json.loads(r.filters_json) if r.filters_json else {},
                           "stats": json.loads(r.stats_json) if r.stats_json else {},
                           "trades": json.loads(r.trades_json) if r.trades_json else [],
                           "config": json.loads(r.config_json), "config_hash": r.config_hash,
                           "created_at": r.created_at} for r in rows]}


@router.get("/scenarios/{scenario_id}")
def get_scenario(scenario_id: int, instrument: str = Depends(get_instrument_context),
                 session: Session = Depends(get_instrument_session)):
    """Stable scenario reference for future modules (e.g. Live Testing):
    retrieve one saved DeepTesting scenario, with its full immutable
    configuration, by its persistent ID. No execution happens here."""
    r = session.query(im.DeepTestScenarioDef).filter_by(id=scenario_id).first()
    if r is None:
        raise HTTPException(404, "scenario not found")
    cfg = json.loads(r.config_json)
    return {"id": r.id, "name": r.name, "folder": r.folder, "version": r.version,
            "parent_scenario_id": r.parent_scenario_id,
            "node_id": r.node_id or cfg.get("node_id") or cfg.get("nodeId"),
            "node_version": r.node_version or cfg.get("node_version") or cfg.get("nodeVersion"),
            "run_id": r.source_run_id or cfg.get("source_run_id") or cfg.get("runId"),
            "filters": json.loads(r.filters_json) if r.filters_json else cfg.get("filters", {}),
            "stats": json.loads(r.stats_json) if r.stats_json else cfg.get("filtered_metrics", {}),
            "trades": json.loads(r.trades_json) if r.trades_json else cfg.get("filtered_trades", []),
            "config": cfg, "config_hash": r.config_hash,
            "created_at": r.created_at, "instrument": r.instrument_id,
            "handoff": "deeptesting-scenario/v1"}


def _scenario_trade_stats(trades: list[dict], initial_capital: float) -> dict:
    ordered = sorted(trades, key=lambda t: float(t.get("exit_ts") or t.get("entry_ts") or 0))
    pnls = [float(t.get("net_pnl") or 0) for t in ordered]
    wins = [p for p in pnls if p > 1e-9]
    losses = [p for p in pnls if p < -1e-9]
    gross_profit, gross_loss = sum(wins), sum(losses)
    equity = peak = float(initial_capital)
    max_dd = max_dd_pct = 0.0
    max_loss_streak = current_loss_streak = 0
    max_loss_streak_amount = current_loss_streak_amount = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        drawdown = peak - equity
        if drawdown > max_dd:
            max_dd = drawdown
            max_dd_pct = drawdown / peak * 100 if peak > 0 else 0.0
        if pnl < -1e-9:
            current_loss_streak += 1
            current_loss_streak_amount += abs(pnl)
            if current_loss_streak > max_loss_streak:
                max_loss_streak = current_loss_streak
                max_loss_streak_amount = current_loss_streak_amount
        else:
            current_loss_streak = 0
            current_loss_streak_amount = 0.0
    return {"trades": len(pnls), "total_trades": len(pnls), "wins": len(wins),
            "winning_trades": len(wins), "losses": len(losses), "losing_trades": len(losses),
            "net": sum(pnls), "net_profit": sum(pnls), "expectancy": sum(pnls) / len(pnls) if pnls else None,
            "avg_win": sum(wins) / len(wins) if wins else None,
            "average_win": sum(wins) / len(wins) if wins else None,
            "avg_loss": sum(losses) / len(losses) if losses else None,
            "average_loss": sum(losses) / len(losses) if losses else None,
            "max_consecutive_losses": max_loss_streak,
            "max_consecutive_loss_amount": max_loss_streak_amount if max_loss_streak else None,
            "max_consecutive_loss_pct": max_loss_streak_amount / initial_capital * 100 if max_loss_streak and initial_capital > 0 else None,
            "winRate": len(wins) / len(pnls) if pnls else None,
            "win_rate": len(wins) / len(pnls) if pnls else None,
            "pf": gross_profit / abs(gross_loss) if gross_loss < 0 else None,
            "profit_factor": gross_profit / abs(gross_loss) if gross_loss < 0 else None,
            "gross_profit": gross_profit, "gross_loss": gross_loss,
            "initial_capital": initial_capital, "final_balance": equity,
            "returnPct": ((equity / initial_capital - 1) * 100) if initial_capital > 0 else None,
            "return_pct": ((equity / initial_capital - 1) * 100) if initial_capital > 0 else None,
            "maxDd": max_dd if pnls else None, "max_drawdown_amount": max_dd if pnls else None,
            "max_drawdown_pct": max_dd_pct if pnls else None,
            "recovery_factor": sum(pnls) / max_dd if max_dd > 0 else None}


@router.post("/scenarios")
def create_scenario(body: ScenarioBody, instrument: str = Depends(get_instrument_context),
                    session: Session = Depends(get_instrument_session)):
    cfg = dict(body.config)
    node_ids = cfg.get("node_ids") or ([cfg.get("node_id")] if cfg.get("node_id") is not None else [])
    if len(node_ids) > 1:
        raise HTTPException(422, "A saved inspection scenario must identify exactly one parent node")
    node_id = int(node_ids[0]) if node_ids else None
    source = None
    selected_trades = []
    filters = cfg.get("filters", {"day_selections": cfg.get("day_selections"),
                                   "session_selections": cfg.get("session_selections"),
                                   "trade_filters": cfg.get("trade_filters", {})})
    stats = cfg.get("filtered_metrics", {})
    if node_id is not None:
        node = session.query(im.Node).filter_by(id=node_id, instrument_id=instrument.upper()).first()
        if node is None:
            raise HTTPException(404, "scenario parent node not found")
        source_run_id = cfg.get("source_run_id") or cfg.get("runId")
        source = session.query(im.DeepTestRun).filter_by(id=source_run_id, node_id=node_id,
            instrument_id=instrument.upper(), status="DONE").first() if source_run_id is not None else None
        if source is None:
            raise HTTPException(422, "scenario must reference its completed source DeepTesting run")
        rows = session.query(im.DeepTestRunTrade).filter_by(run_id=source.id, instrument_id=instrument.upper()).all()
        by_id = {t.id: t for t in rows}
        selected_ids = cfg.get("filtered_trade_ids", [])
        if not isinstance(selected_ids, list) or any(int(i) not in by_id for i in selected_ids):
            raise HTTPException(422, "scenario trade selection must be a subset of the source run ledger")
        selected_trades = [{**(json.loads(by_id[int(i)].detail_json) if by_id[int(i)].detail_json else {}),
                            "id": int(i), "net_pnl": by_id[int(i)].net_pnl,
                            "entry_ts": by_id[int(i)].entry_ts, "exit_ts": by_id[int(i)].exit_ts}
                           for i in selected_ids]
        source_cfg = json.loads(source.config_json or "{}")
        stats = _scenario_trade_stats(selected_trades, float(source_cfg.get("initial_capital") or 0))
        cfg.update({"node_id": node_id, "node_version": source.node_version,
                    "source_run_id": source.id, "filtered_trades": selected_trades})
    row = im.DeepTestScenarioDef(instrument_id=instrument.upper(), name=body.name,
                                 folder=body.folder, version=1, node_id=node_id,
                                 node_version=source.node_version if source else None,
                                 source_run_id=source.id if source else None,
                                 filters_json=json.dumps(filters), stats_json=json.dumps(stats),
                                 trades_json=json.dumps(selected_trades),
                                 config_json=json.dumps(cfg),
                                 config_hash=dts.scenario_hash(cfg))
    session.add(row)
    session.commit()
    session.refresh(row)
    return {"id": row.id, "version": 1, "node_id": row.node_id,
            "source_run_id": row.source_run_id}


@router.put("/scenarios/{scenario_id}")
def edit_scenario(scenario_id: int, body: ScenarioBody,
                  instrument: str = Depends(get_instrument_context),
                  session: Session = Depends(get_instrument_session)):
    old = session.query(im.DeepTestScenarioDef).filter_by(id=scenario_id).first()
    if old is None:
        raise HTTPException(404, "scenario not found")
    cfg = dict(body.config)
    cfg.setdefault("node_id", old.node_id)
    cfg.setdefault("node_version", old.node_version)
    cfg.setdefault("source_run_id", old.source_run_id)
    row = im.DeepTestScenarioDef(instrument_id=instrument.upper(), name=body.name,
                                 folder=body.folder if body.folder is not None else old.folder,
                                 version=old.version + 1, parent_scenario_id=old.id,
                                 node_id=old.node_id, node_version=old.node_version,
                                 source_run_id=old.source_run_id, filters_json=old.filters_json,
                                 stats_json=old.stats_json, trades_json=old.trades_json,
                                 config_json=json.dumps(cfg),
                                 config_hash=dts.scenario_hash(cfg))
    session.add(row)
    session.commit()
    session.refresh(row)
    return {"id": row.id, "version": row.version, "parent_scenario_id": old.id}


@router.delete("/scenarios/{scenario_id}")
def delete_scenario(scenario_id: int, instrument: str = Depends(get_instrument_context),
                    session: Session = Depends(get_instrument_session)):
    row = session.query(im.DeepTestScenarioDef).filter_by(id=scenario_id).first()
    if row is None:
        raise HTTPException(404, "scenario not found")
    session.delete(row)
    session.commit()
    return {"deleted": scenario_id, "note": "past runs keep their full config snapshots"}


class GenerateBody(BaseModel):
    base_config: dict
    dimensions: dict
    max_count: int = 200
    save: bool = False
    folder: Optional[str] = None


@router.post("/scenarios/generate")
def generate(body: GenerateBody, instrument: str = Depends(get_instrument_context),
             session: Session = Depends(get_instrument_session)):
    try:
        scenarios = dts.generate_scenarios(body.dimensions, body.base_config, body.max_count)
    except dts.DeepTestError as exc:
        raise HTTPException(exc.status_code, f"{exc.code}: {exc}") from exc
    ids = []
    if body.save:
        for sc in scenarios:
            row = im.DeepTestScenarioDef(instrument_id=instrument.upper(), name=sc["name"],
                                         folder=body.folder, version=1,
                                         config_json=json.dumps(sc["config"]),
                                         config_hash=dts.scenario_hash(sc["config"]))
            session.add(row)
            session.flush()
            ids.append(row.id)
        session.commit()
    return {"count": len(scenarios), "scenarios": scenarios, "saved_ids": ids}


# ------------------------------------------------------------------ fine-tune
@router.get("/runs/{run_id}/fine-tune-suggestions")
def suggestions(run_id: int, instrument: str = Depends(get_instrument_context),
                session: Session = Depends(get_instrument_session)):
    run = session.query(im.DeepTestRun).filter_by(id=run_id).first()
    if run is None:
        raise HTTPException(404, "run not found")
    return {"suggestions": dts.fine_tune_suggestions(session, run)}


class FineTuneApply(BaseModel):
    suggestion: dict
    name: Optional[str] = None


@router.post("/runs/{run_id}/fine-tune-apply")
def fine_tune_apply(run_id: int, body: FineTuneApply,
                    instrument: str = Depends(get_instrument_context),
                    session: Session = Depends(get_instrument_session)):
    """Create a FINE_TUNED child node from one suggestion. Parent untouched,
    lineage recorded, child spec re-validated and compiled server-side."""
    run = session.query(im.DeepTestRun).filter_by(id=run_id).first()
    if run is None:
        raise HTTPException(404, "run not found")
    repo = NodeRepository(session, instrument)
    parent = repo.get(run.node_id)
    if parent is None or not parent.spec_json:
        raise HTTPException(404, "parent node/spec not found")
    spec = json.loads(parent.spec_json)
    patch = (body.suggestion.get("patch") or {}).get("spec") or {}
    if "sl_multiplier" in patch:
        sl = (spec.get("exits") or {}).get("stop_loss") or {}
        dist = sl.get("distance") or {}
        if not dist:
            raise HTTPException(422, "parent has no stop-loss distance to scale")
        dist["value"] = round(float(dist["value"]) * float(patch["sl_multiplier"]), 6)
        sl["distance"] = dist
        spec["exits"]["stop_loss"] = sl
    elif "add_stepped_trailing" in patch:
        st = patch["add_stepped_trailing"]
        spec["management"] = {"stepped_trailing": {
            "start": "after_activation",
            "activation": {"value": st["activation_pips"], "unit": "pips"},
            "initial_locked_profit": {"value": 0, "unit": "pips"},
            "step": {"value": st["step_pips"], "unit": "pips"},
            "advance_per_step": {"value": st["advance_pips"], "unit": "pips"},
            "first_step_to_breakeven": True, "applies_to": "BOTH"}}
    else:
        raise HTTPException(422, "suggestion carries no applicable spec patch "
                                 "(session suggestions are scenario filters, applied at run time)")
    spec.setdefault("identity", {})["origin"] = "FINE_TUNED"
    from .nodes import _alloc_sequential_names
    _, compiled = spec_service_compile(spec, instrument)
    version = spec_service.next_version(session, instrument, parent)
    child = repo.create(
        origin="FINE_TUNED", name=body.name or _alloc_sequential_names(repo.existing_names(), 1)[0],
        description=f"Fine-tuned from node #{parent.id} via DeepTesting run #{run_id}: "
                    f"{body.suggestion.get('rationale', '')}"[:400],
        spec_json=compiled.canonical_json, spec_hash=compiled.fingerprint,
        parent_node_id=parent.id, created_by="deeptesting", version=version,
        timeframe=(spec.get("identity") or {}).get("timeframe"))
    session.add(im.NodeLineage(instrument_id=instrument.upper(), child_node_id=child.id,
                               parent_node_id=parent.id, relation="fine_tuned_from"))
    session.commit()
    return {"child_node_id": child.id, "version": version, "parent_node_id": parent.id,
            "fingerprint": compiled.fingerprint}


def spec_service_compile(spec: dict, instrument: str):
    _r, compiled = spec_service.compile_or_raise(spec, instrument, None)
    return None, compiled
