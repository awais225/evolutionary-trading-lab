"""Explicit, category-scoped destructive operations under the configured data root.

No repository-relative paths are accepted. Filesystem deletion is restricted to
known managed paths beneath hero1_storage.engine.data_root()."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from sqlalchemy import delete, func
from hero1_storage.engine import data_root, instrument_session, instrument_db_path, registry_session
from hero1_storage import instrument_models as im
from hero1_storage.registry_models import ApplicationSetting, DashboardTask, GlobalEvent, Instrument
from app import task_service

CATEGORIES = ("NODES", "DEEPTESTING", "MONTE_CARLO", "MARKET_DATA", "LOGS", "ALL")


def _safe_managed_path(path: Path, root: Path) -> Path:
    root = root.resolve(); resolved = path.resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise ValueError(f"managed path escaped configured data root: {path}")
    if path.is_symlink():
        raise ValueError(f"refusing to traverse managed symlink: {path}")
    return path


def _files(path: Path, root: Path) -> list[Path]:
    if not path.exists(): return []
    _safe_managed_path(path, root)
    if path.is_file(): return [path]
    return [p for p in path.rglob("*") if p.is_file() and not p.is_symlink() and _safe_managed_path(p, root)]


def _instruments(reg) -> list[str]:
    # Registry entries may exist before their per-instrument database is initialized.
    return [r.symbol for r in reg.query(Instrument).order_by(Instrument.symbol).all() if instrument_db_path(r.symbol).exists()]


def _count(session, model) -> int:
    return int(session.query(func.count()).select_from(model).scalar() or 0)


def _category_counts(symbol: str, session, category: str) -> dict[str, int]:
    models = {
      "NODES": [im.Node,im.NodeLineage,im.BacktestRun,im.DeepTestScenario,im.DeepTestTrade,im.DeepTestRun,im.DeepTestDecisionEvent,im.DeepTestRunTrade,im.DeepTestScenarioDef,im.MonteCarloRun,im.LiveConfig,im.LiveTrade,im.Approval,im.ActivityEvent],
      "DEEPTESTING": [im.DeepTestScenario,im.DeepTestTrade,im.DeepTestRun,im.DeepTestDecisionEvent,im.DeepTestRunTrade,im.DeepTestScenarioDef],
      "MONTE_CARLO": [im.MonteCarloRun],
      "LOGS": [im.ActivityEvent],
    }.get(category, [])
    return {m.__tablename__: _count(session,m) for m in models}


def preview(category: str) -> dict[str, Any]:
    cat=category.upper()
    if cat not in CATEGORIES: raise ValueError("unknown deletion category")
    root=data_root().resolve(); registry=registry_session()
    try:
        active=registry.query(DashboardTask).filter(DashboardTask.status.in_(task_service.ACTIVE)).count()
        symbols=_instruments(registry)
        running_records={}
        details={}
        for symbol in symbols:
            db=instrument_session(symbol)
            try:
                details[symbol]=_category_counts(symbol,db,cat if cat!="ALL" else "NODES") if cat in ("NODES","ALL") else _category_counts(symbol,db,cat)
                running_records[symbol]={"deeptesting":db.query(im.DeepTestRun).filter_by(status="RUNNING").count(),"monte_carlo":db.query(im.MonteCarloRun).filter_by(status="RUNNING").count()}
            finally: db.close()
        files={}
        if cat in ("MARKET_DATA","ALL"):
            mroot=root/"MARKET_DATA"
            for p in (mroot/"market_data.db",mroot/"market_data.db-wal",mroot/"market_data.db-shm",mroot/"import_manifest.json",mroot/"IMPORTS"):
                files[str(p)]=len(_files(p,root))
        if cat in ("LOGS","ALL"):
            p=root/"LOGS";files[str(p)]=len(_files(p,root))
        registry_counts={}
        task_categories={"NODES":["DEEPTESTING","MONTE_CARLO","NODE_GENERATION","VALIDATION"],"DEEPTESTING":["DEEPTESTING"],"MONTE_CARLO":["MONTE_CARLO"],"MARKET_DATA":["DATA_FETCHING"],"ALL":None}.get(cat,[])
        if task_categories is None: registry_counts["dashboard_tasks:all"]=registry.query(DashboardTask).count()
        else:
            for task_cat in task_categories: registry_counts[f"dashboard_tasks:{task_cat}"]=registry.query(DashboardTask).filter_by(category=task_cat).count()
        if cat in ("LOGS","ALL"):
            registry_counts["global_events"]=_count(registry,GlobalEvent)
        if cat=="ALL":
            for symbol in symbols:
                db=instrument_session(symbol)
                try:
                    details[symbol].update(_category_counts(symbol,db,"DEEPTESTING"))
                    details[symbol].update(_category_counts(symbol,db,"MONTE_CARLO"))
                    details[symbol].update(_category_counts(symbol,db,"LOGS"))
                finally: db.close()
        active_records=sum(v["deeptesting"]+v["monte_carlo"] for v in running_records.values())
        return {"category":cat,"data_root":str(root),"instrument_records":details,"registry_records":registry_counts,"files":files,"active_jobs":active,"active_run_records":running_records,"can_delete":active==0 and active_records==0,"included":_included(cat)}
    finally: registry.close()


def _included(category:str)->list[str]:
    sets={"NODES":["nodes and every FK/logically node-owned record, including dependent DeepTesting scenarios/runs and Monte Carlo studies (avoids orphan records)"],"DEEPTESTING":["DeepTesting runs, trade ledgers, decision events, legacy inspections, and saved scenario definitions; dependent Monte Carlo study results are preserved and detached from their deleted source run"],"MONTE_CARLO":["Monte Carlo run/result rows and Monte Carlo dashboard-task history"],"MARKET_DATA":["MARKET_DATA/market_data.db, import_manifest.json, and user-managed MARKET_DATA/IMPORTS files beneath the configured data root"],"LOGS":["registry global_events, per-instrument activity_events, and files beneath the exact LOGS directory"],"ALL":["all listed node, DeepTesting, Monte Carlo, market-data, and log categories; instrument registry/settings, MARKET_METADATA, credentials, source files, launchers, and runtime dependencies are preserved"]}
    return sets[category]


def _delete_deep_data(session):
    run_ids=[r[0] for r in session.query(im.DeepTestRun.id).all()]
    # Preserve Monte Carlo evidence, while replacing its FK with a stable source ID snapshot.
    for study in session.query(im.MonteCarloRun).filter(im.MonteCarloRun.deeptest_run_id.is_not(None)).all():
        cfg=json.loads(study.config_json or "{}")
        cfg["source_run_id_snapshot"]=study.deeptest_run_id
        cfg["source_run_deleted"]=True
        study.config_json=json.dumps(cfg)
        study.deeptest_run_id=None
    if run_ids:
        session.query(im.DeepTestDecisionEvent).filter(im.DeepTestDecisionEvent.run_id.in_(run_ids)).delete(synchronize_session=False)
        session.query(im.DeepTestRunTrade).filter(im.DeepTestRunTrade.run_id.in_(run_ids)).delete(synchronize_session=False)
    legacy_ids=[r[0] for r in session.query(im.DeepTestScenario.id).all()]
    if legacy_ids: session.query(im.DeepTestTrade).filter(im.DeepTestTrade.scenario_id.in_(legacy_ids)).delete(synchronize_session=False)
    session.query(im.DeepTestTrade).delete(synchronize_session=False)
    session.query(im.DeepTestScenario).delete(synchronize_session=False)
    session.query(im.DeepTestScenarioDef).delete(synchronize_session=False)
    session.query(im.DeepTestRun).delete(synchronize_session=False)


def _delete_nodes(session):
    node_ids=[r[0] for r in session.query(im.Node.id).all()]
    if not node_ids: return
    _delete_deep_data(session)
    session.query(im.MonteCarloRun).filter(im.MonteCarloRun.node_id.in_(node_ids)).delete(synchronize_session=False)
    session.query(im.BacktestRun).filter(im.BacktestRun.node_id.in_(node_ids)).delete(synchronize_session=False)
    session.query(im.NodeLineage).filter((im.NodeLineage.child_node_id.in_(node_ids))|(im.NodeLineage.parent_node_id.in_(node_ids))).delete(synchronize_session=False)
    for model in (im.LiveTrade,im.LiveConfig,im.Approval,im.ActivityEvent):
        session.query(model).filter(model.node_id.in_(node_ids)).delete(synchronize_session=False)
    session.query(im.Node).delete(synchronize_session=False)


def _write_marker(reg, key: str):
    row=reg.get(ApplicationSetting,key)
    if row is None: reg.add(ApplicationSetting(key=key,value="true",updated_by="data-management"))
    else: row.value="true";row.updated_by="data-management"


def execute(category: str) -> dict[str, Any]:
    cat=category.upper(); plan=preview(cat)
    if plan["active_jobs"] or any(v["deeptesting"] or v["monte_carlo"] for v in plan.get("active_run_records",{}).values()): raise RuntimeError("stop or reconcile active HERO1 jobs before deleting persistent data")
    root=data_root().resolve(); failures=[]; deleted={}
    reg=registry_session()
    try:
        symbols=_instruments(reg)
        if cat in ("NODES","ALL"):
            for symbol in symbols:
                db=instrument_session(symbol)
                try: _delete_nodes(db);db.commit();deleted[symbol]="node-owned records"
                except Exception: db.rollback();failures.append(f"{symbol}: node data transaction failed");raise
                finally: db.close()
        elif cat=="DEEPTESTING":
            for symbol in symbols:
                db=instrument_session(symbol)
                try: _delete_deep_data(db);db.commit();deleted[symbol]="DeepTesting records; Monte Carlo detached/preserved"
                except Exception: db.rollback();raise
                finally: db.close()
        elif cat=="MONTE_CARLO":
            for symbol in symbols:
                db=instrument_session(symbol)
                try: db.query(im.MonteCarloRun).delete(synchronize_session=False);db.commit();deleted[symbol]="Monte Carlo results"
                except Exception: db.rollback();raise
                finally: db.close()
        task_categories={"NODES":["DEEPTESTING","MONTE_CARLO","NODE_GENERATION","VALIDATION"],"DEEPTESTING":["DEEPTESTING"],"MONTE_CARLO":["MONTE_CARLO"],"MARKET_DATA":["DATA_FETCHING"],"ALL":None}.get(cat,[])
        if task_categories is None: reg.query(DashboardTask).delete(synchronize_session=False)
        elif task_categories: reg.query(DashboardTask).filter(DashboardTask.category.in_(task_categories)).delete(synchronize_session=False)
        if cat in ("LOGS","ALL"):
            for symbol in symbols:
                db=instrument_session(symbol)
                try: db.query(im.ActivityEvent).delete(synchronize_session=False);db.commit()
                finally: db.close()
            reg.query(GlobalEvent).delete(synchronize_session=False)
        if cat in ("NODES","ALL"):_write_marker(reg,"startup.seed_nodes_disabled")
        if cat in ("MARKET_DATA","ALL"):_write_marker(reg,"startup.market_baseline_disabled")
        reg.commit()
    except Exception:
        reg.rollback();raise
    finally: reg.close()
    # Remove files only after all database transactions succeeded.
    files_removed=[]
    if cat in ("MARKET_DATA","ALL"):
        mroot=root/"MARKET_DATA"
        for path in (mroot/"market_data.db",mroot/"market_data.db-wal",mroot/"market_data.db-shm",mroot/"import_manifest.json",mroot/"IMPORTS"):
            _safe_managed_path(path,root)
            if path.is_symlink(): raise RuntimeError(f"refusing to remove symlink {path}")
            if path.is_file(): path.unlink();files_removed.append(str(path))
            elif path.is_dir():
                # Remove only enumerated regular files, then prune empty dirs under this managed IMPORTS subtree.
                for child in sorted(_files(path,root),key=lambda p:len(p.parts),reverse=True): child.unlink();files_removed.append(str(child))
                for directory in sorted((p for p in path.rglob("*") if p.is_dir() and not p.is_symlink()),key=lambda p:len(p.parts),reverse=True): directory.rmdir()
                path.rmdir();files_removed.append(str(path))
        if mroot.is_dir() and not any(mroot.iterdir()): mroot.rmdir();files_removed.append(str(mroot))
    if cat in ("LOGS","ALL"):
        path=root/"LOGS";_safe_managed_path(path,root)
        if path.is_dir() and not path.is_symlink():
            for child in sorted(_files(path,root),key=lambda p:len(p.parts),reverse=True): child.unlink();files_removed.append(str(child))
            for directory in sorted((p for p in path.rglob("*") if p.is_dir() and not p.is_symlink()),key=lambda p:len(p.parts),reverse=True): directory.rmdir()
            path.rmdir();files_removed.append(str(path))
    return {"category":cat,"deleted_records":deleted,"deleted_files":files_removed,"market_metadata":"preserved","success":not failures,"failures":failures}
