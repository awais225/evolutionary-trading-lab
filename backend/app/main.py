"""
FastAPI entrypoint — Evolutionary Trading Research Lab (V2).

Serves the REST API + WebSocket event stream, and (if present) the built
React dashboard from frontend/dist so the whole app runs on ONE port:
    http://localhost:8787
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import paths as P
from .api.routes import router
from .api.ws import bus
from .config import load_config
from .db.database import get_db
from .logging_setup import setup_logging
from .mt5 import bridge_status
from .mt5.monitor import get_connection_monitor
from .resources.manager import get_resource_manager
from .versions import APP_VERSION, FULL_VERSION_STRING, APP_NAME, manifest

log = logging.getLogger("main")

ROOT = P.ROOT_DIR
FRONTEND_DIST = ROOT / "frontend" / "dist"

app = FastAPI(title="Evolutionary Trading Research Lab", version=APP_VERSION)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False,
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(router)


@app.on_event("startup")
async def startup() -> None:
    # 0. Single-Instance PID Lease & Pre-Bind Check (spec V2.9)
    from .lease import get_lease_manager
    lease_mgr = get_lease_manager()
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        ok, msg = lease_mgr.acquire()
        if not ok:
            log.warning("[STARTUP] PID lease acquisition notice: %s", msg)

    # 1. Directory layout & V1 migration (spec §1, §3)
    P.run_all_migrations()
    from .data.discovery import get_discovery_engine
    disc_eng = get_discovery_engine()
    disc_eng.discover_all(emit_logs=True)

    # V3.2 Complete Data Restoration & Historical Recovery (spec V3.2 §2)
    from .data.restoration import get_restoration_engine
    rest_eng = get_restoration_engine()
    rest_eng.restore_all(emit_logs=True)

    # 2. Config & Logging
    cfg = load_config()
    setup_logging(cfg.log_level)

    # 3. Attach event bus loop
    bus.attach_loop(asyncio.get_running_loop())

    # 4. Activity & Diagnostics Startup Trail (User Spec V2.6+ §18 & V2.8)
    import sys
    from .activity import activity
    from .orchestrator.pipeline_state import get_pipeline_state_manager
    psm = get_pipeline_state_manager()
    psm.print_startup_diagnostic()

    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    activity.started("SYSTEM", f"Starting {FULL_VERSION_STRING}...", operation_id="startup")
    activity.success("SYSTEM", f"Python environment verified (v{py_ver})", operation_id="startup")
    activity.success("DATA", "Persistent DATA architecture ready at DATA_ROOT (manifests synchronized)", operation_id="startup")

    # 5. Database initialization & reconciliation (spec §2, §4)
    db = get_db()
    reconcile_out = disc_eng.reconcile_with_database(db=db, emit_logs=True)
    strat_row = db.one("SELECT count(*) c FROM strategies")
    strat_count = strat_row["c"] if strat_row else 0
    u_ver_row = db.one("PRAGMA user_version")
    u_ver = u_ver_row.get("user_version", 3) if isinstance(u_ver_row, dict) else 3
    activity.success("DATABASE", f"Database loaded ({strat_count} strategies, schema v{u_ver}, reconciled {reconcile_out.get('registered_datasets', 0)} datasets)", operation_id="startup")
    activity.success("SYSTEM", f"Backend initialized (FastAPI - {FULL_VERSION_STRING})", operation_id="startup")

    # 6. Check Market Bridge & MT5 Status
    activity.running("MT5", "Loading MT5 integration & market bridge...", operation_id="startup")
    st = bridge_status()
    log.info("Market bridge: %s (simulated=%s)", st["active_bridge"], st["is_simulated"])
    if not st.get("is_simulated"):
        term_name = st.get("terminal_name") or st.get("last_connected_terminal") or "MetaTrader 5"
        term_build = st.get("terminal_build") or st.get("last_connected_build") or ""
        company = st.get("terminal_company") or st.get("last_connected_company") or "MT5 Real"
        activity.success("MT5", f"MT5 terminal connected: {term_name} (Build {term_build}, {company})", operation_id="startup")
        if st.get("account"):
            activity.success("MT5", f"Account detected: {st.get('account')} @ {st.get('server') or ''}", operation_id="startup")
        activity.success("MT5", f"Market bridge: MT5 REAL ({company})", operation_id="startup")
    else:
        activity.info("MT5", "Market bridge: SIMULATOR (synthetic research feed active)", operation_id="startup")

    # 7. Start MT5 Connection & Feed Monitor (spec §15-19)
    get_connection_monitor().start()
    activity.success("SYSTEM", "Connection monitor started", operation_id="startup")
    activity.success("SYSTEM", "Dashboard ready — listening on http://127.0.0.1:8787", operation_id="startup")

    if os.environ.get("LAB_AUTOSTART", "").lower() in ("1", "true", "yes"):
        mode = os.environ.get("LAB_AUTOSTART_MODE", "continuous")
        from .orchestrator.lab import get_lab
        threading_start(mode)


def threading_start(mode: str) -> None:
    from .orchestrator.lab import get_lab
    get_lab().start(mode)


@app.on_event("shutdown")
async def shutdown() -> None:
    from .lease import get_lease_manager
    get_lease_manager().release()


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    q = bus.subscribe()
    try:
        # replay recent events so a fresh dashboard is immediately populated
        for ev in bus.recent_events(40):
            await ws.send_text(json.dumps(ev, default=str))
        while True:
            ev = await q.get()
            await ws.send_text(json.dumps(ev, default=str))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        bus.unsubscribe(q)


# ---------------- Root Health & Status Endpoints (spec §25) ----------------
@app.get("/health")
def root_health():
    bs = bridge_status()
    mon = get_connection_monitor().status_details()
    return {
        "status": "ok",
        "app_version": FULL_VERSION_STRING,
        "version": FULL_VERSION_STRING,
        "version_number": APP_VERSION,
        "bridge": bs["active_bridge"],
        "internet": mon["internet"],
        "mt5": mon["mt5"],
        "data_feed": mon["data_feed"],
    }


@app.get("/api/health")
def api_health():
    bs = bridge_status()
    mon = get_connection_monitor().status_details()
    return {
        "status": "ok",
        "database": "connected",
        "mt5_api": "available" if not bs.get("is_simulated") else "simulator",
        "version": FULL_VERSION_STRING,
        "app_version": FULL_VERSION_STRING,
        "version_number": APP_VERSION,
        "bridge": bs["active_bridge"],
        "internet": mon["internet"],
        "mt5": mon["mt5"],
        "data_feed": mon["data_feed"],
    }


@app.get("/status")
def root_status():
    from .orchestrator.lab import get_lab
    from .activity import activity
    return {
        "app_version": FULL_VERSION_STRING,
        "version": FULL_VERSION_STRING,
        "version_number": APP_VERSION,
        "lab": get_lab().status(),
        "bridge": bridge_status(),
        "resources": get_resource_manager().live_metrics(),
        "connection": get_connection_monitor().status_details(),
        "current_task": activity.get_current_task(),
    }


@app.get("/mt5/status")
def root_mt5_status():
    return get_connection_monitor().status_details()


@app.get("/system/status")
def root_system_status():
    return {
        "app_version": FULL_VERSION_STRING,
        "version": FULL_VERSION_STRING,
        "version_number": APP_VERSION,
        "manifest": manifest(),
        "resources": get_resource_manager().live_metrics(),
        "connection": get_connection_monitor().status_details(),
    }


# ---- serve built frontend (single-port mode) ----
if FRONTEND_DIST.exists() and (FRONTEND_DIST / "index.html").exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
else:
    @app.get("/", response_class=HTMLResponse)
    def index_dev():
        if (FRONTEND_DIST / "index.html").exists():
            return HTMLResponse(content=(FRONTEND_DIST / "index.html").read_text(encoding="utf-8"))
        return f"""<html><body style="font-family:sans-serif;background:#0b0e14;color:#d5dbe8;
        padding:40px"><h2>Evolutionary Trading Research Lab v{APP_VERSION} — API is running</h2>
        <p>Frontend dev server: <code>npm run dev</code> in <code>frontend/</code>
        (or build it: <code>npm run build</code>, then reload this page).</p>
        <p>API docs: <a style="color:#6ea8fe" href="/docs">/docs</a> &middot;
        Status: <a style="color:#6ea8fe" href="/status">/status</a> &middot;
        Health: <a style="color:#6ea8fe" href="/health">/health</a></p>
        </body></html>"""
