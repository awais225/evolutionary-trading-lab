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
from .api.json_safety import SafeJSONResponse
from .api.routes import router
from .api.ws import bus
from .config import load_config
from .db.database import get_db
from .lifecycle import get_lifecycle
from .logging_setup import setup_logging
from .mt5 import bridge_status
from .mt5.monitor import get_connection_monitor
from .resources.manager import get_resource_manager
from .versions import APP_VERSION, FULL_VERSION_STRING, APP_NAME, manifest

log = logging.getLogger("main")

ROOT = P.ROOT_DIR
FRONTEND_DIST = ROOT / "frontend" / "dist"

app = FastAPI(title="Evolutionary Trading Research Lab", version=APP_VERSION,
              # V4.7: no handler can ever return invalid JSON / NaN / a raw object
              default_response_class=SafeJSONResponse)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False,
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(router)


@app.on_event("startup")
async def startup() -> None:
    """Fast, safety-critical startup; everything expensive is deferred (V4.7).

    The application must announce itself (``/health``) as early as safely
    possible. What stays here is cheap and safety-relevant; what moves to the
    background bootstrap is the DATA scanning work that used to delay the
    listening socket by over a second.
    """
    # 0. Single-Instance PID Lease & Pre-Bind Check (spec V2.9)
    from .lease import get_lease_manager
    lease_mgr = get_lease_manager()
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        ok, msg = lease_mgr.acquire()
        if not ok:
            log.warning("[STARTUP] PID lease acquisition notice: %s", msg)

    # 1. Directory layout & V1 migration (spec §1, §3) - required before any
    #    request handler touches paths, and measured in tens of milliseconds.
    P.run_all_migrations()

    # 2. Config & Logging
    cfg = load_config()
    setup_logging(cfg.log_level)

    # 3. Attach event bus loop (so activity events reach the dashboard WS)
    bus.attach_loop(asyncio.get_running_loop())

    # 4. Database open + schema/migration (cheap: opens lazily, applies the
    #    additive schema). Doing it here keeps "database initialization" on the
    #    startup path as before.
    db = get_db()

    # 5. MT5 detection: cheap and safety relevant (bridge honesty), stays here.
    from .activity import activity
    st = bridge_status()
    log.info("Market bridge: %s (simulated=%s)", st["active_bridge"], st["is_simulated"])

    # 6. Monitors: connection monitor + V4.3 Live-Testing engine. The Live
    #    Testing engine always comes up INACTIVE - no persisted "active" flag.
    get_connection_monitor().start()
    from .live_testing import get_live_testing_engine
    get_live_testing_engine().start()

    # 7. Everything expensive is registered as a bounded background step.
    def _data_discovery() -> dict:
        from .data.discovery import get_discovery_engine
        disc = get_discovery_engine()
        disc.discover_all(emit_logs=True)
        return {"datasets": len(disc.datasets) if hasattr(disc, "datasets") else None}

    def _data_restoration() -> dict:
        from .data.restoration import get_restoration_engine
        get_restoration_engine().restore_all(emit_logs=True)
        return {"ok": True}

    def _reconcile() -> dict:
        from .data.discovery import get_discovery_engine
        out = get_discovery_engine().reconcile_with_database(db=get_db(), emit_logs=True)
        return out if isinstance(out, dict) else {"ok": True}

    def _diagnostics() -> dict:
        import sys
        from .orchestrator.pipeline_state import get_pipeline_state_manager
        psm = get_pipeline_state_manager()
        psm.print_startup_diagnostic()
        strat_row = get_db().one("SELECT count(*) c FROM strategies")
        strat_count = strat_row["c"] if strat_row else 0
        u_ver_row = get_db().one("PRAGMA user_version")
        u_ver = u_ver_row.get("user_version", 3) if isinstance(u_ver_row, dict) else 3
        py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        activity.started("SYSTEM", f"Starting {FULL_VERSION_STRING}...", operation_id="startup")
        activity.success("SYSTEM", f"Python environment verified (v{py_ver})", operation_id="startup")
        activity.success("DATA", "Persistent DATA architecture ready at DATA_ROOT (manifests synchronized)", operation_id="startup")
        activity.success("DATABASE", f"Database loaded ({strat_count} strategies, schema v{u_ver})", operation_id="startup")
        activity.success("SYSTEM", f"Backend initialized (FastAPI - {FULL_VERSION_STRING})", operation_id="startup")
        if st.get("is_simulated"):
            activity.info("MT5", "Market bridge: SIMULATOR (synthetic research feed active)", operation_id="startup")
        else:
            company = st.get("terminal_company") or st.get("last_connected_company") or "MT5 Real"
            activity.success("MT5", f"Market bridge: MT5 REAL ({company})", operation_id="startup")
        activity.info("LIVE-TESTING", "Live Testing engine initialised — INACTIVE (activation required)",
                      operation_id="startup")
        activity.success("SYSTEM", "Dashboard ready — listening on http://127.0.0.1:8787", operation_id="startup")
        return {"strategies": strat_count, "schema_version": u_ver}

    lifecycle = get_lifecycle()
    lifecycle.register("data_discovery", "Scan DATA root & register datasets", _data_discovery,
                       timeout_s=90.0, critical=True)
    lifecycle.register("data_restoration", "Restore/verify persisted research & DATA mirrors", _data_restoration,
                       timeout_s=180.0, critical=True)
    lifecycle.register("database_reconcile", "Reconcile datasets with the database", _reconcile,
                       timeout_s=60.0, critical=True)
    lifecycle.register("diagnostics", "Startup diagnostics & activity trail", _diagnostics,
                       timeout_s=30.0, critical=False)

    if os.environ.get("EVOLUTIONARY_LAB_SKIP_BOOTSTRAP", "").lower() in ("1", "true", "yes"):
        log.warning("startup bootstrap skipped by EVOLUTIONARY_LAB_SKIP_BOOTSTRAP")
    else:
        lifecycle.run()

    if os.environ.get("LAB_AUTOSTART", "").lower() in ("1", "true", "yes"):
        mode = os.environ.get("LAB_AUTOSTART_MODE", "continuous")
        threading_start(mode)


def threading_start(mode: str) -> None:
    from .orchestrator.lab import get_lab
    get_lab().start(mode)


@app.on_event("shutdown")
async def shutdown() -> None:
    # V4.3: shut the live-testing loop down and drop the in-memory ACTIVE flag so a
    # restart can never resume trading by itself (spec §21).
    try:
        from .live_testing import get_live_testing_engine
        get_live_testing_engine().stop(reason="backend shutdown - Live Testing stays INACTIVE")
    except Exception as e:  # never block shutdown
        log.warning("live testing shutdown notice: %s", e)
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
def _health_payload(api_style: bool = False) -> tuple[dict, int]:
    """Shared health body. Reports the startup state honestly (V4.7).

    HTTP 200 while healthy or still initialising, 503 when a critical startup
    step failed - a genuine backend failure is never hidden behind "ok".
    """
    bs = bridge_status()
    mon = get_connection_monitor().status_details()
    life = get_lifecycle()
    snap = life.snapshot()
    if snap["state"] == "failed":
        status = "error"
        http_code = 503
    elif snap["state"] == "degraded":
        status = "degraded"
        http_code = 200
    elif snap["state"] in ("starting", "not_started"):
        status = "starting"
        http_code = 200
    else:
        status = "ok"
        http_code = 200
    body = {
        "status": status,
        "ready": snap["ready"],
        "startup": snap,
    }
    if api_style:
        body.update({
            "database": "connected" if snap["state"] != "not_started" else "initialising",
            "mt5_api": "available" if not bs.get("is_simulated") else "simulator",
        })
    body.update({
        "app_version": FULL_VERSION_STRING,
        "version": FULL_VERSION_STRING,
        "version_number": APP_VERSION,
        "bridge": bs["active_bridge"],
        "internet": mon["internet"],
        "mt5": mon["mt5"],
        "data_feed": mon["data_feed"],
    })
    return body, http_code


@app.get("/health")
def root_health():
    body, code = _health_payload()
    return JSONResponse(body, status_code=code)


@app.get("/api/ready")
def api_ready():
    """Strict readiness: 200 only when every startup step completed."""
    life = get_lifecycle()
    body = life.readiness_response()
    return JSONResponse(body, status_code=200 if body["code"] in ("ready", "degraded") else 503)


@app.get("/api/lifecycle")
def api_lifecycle():
    """Startup diagnostics: per-step status, durations and failure reasons."""
    return get_lifecycle().snapshot()


@app.get("/api/health")
def api_health():
    body, code = _health_payload(api_style=True)
    return JSONResponse(body, status_code=code)


@app.get("/api/health/legacy")
def api_health_legacy():
    """Previous flat shape, kept for any external monitor that expects it."""
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
