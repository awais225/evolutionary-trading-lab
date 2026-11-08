from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app import __version__
from app.config import get_settings
from app.logging_setup import configure_logging, get_logger
from app.api import routers
from hero1_eventbus import get_event_bus
from hero1_storage.engine import registry_session
from hero1_storage.enums import EventCategory, EventSeverity
from hero1_storage.repositories import EventRepository, events_to_dicts

log = get_logger("hero1.api.main")

# Local-first app: the frontend is served by Vite dev server or any static host.
# Credentials are never involved; CORS is restricted to localhost dev origins.
_DEV_ORIGINS = [
    "http://localhost:5173", "http://127.0.0.1:5173",
    "http://localhost:4173", "http://127.0.0.1:4173",
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = get_settings()
    configure_logging(cfg.log_level)
    get_event_bus().attach_loop(asyncio.get_running_loop())
    log.info("hero1-api starting", extra={"version": __version__})
    try:  # permanent CSV baseline -> this installation's canonical store (ISSUE-049)
        from app import market_data_service
        _baseline = market_data_service.ensure_baseline()
        log.info("market-data baseline check", extra={"baseline": str(_baseline)[:500]})
    except Exception:
        log.exception("market-data baseline check failed (continuing without it)")
    try:  # automatic MT5 connection (ISSUE-056): background, bounded retries
        from app import mt5_autoconnect
        mt5_autoconnect.start_auto_connect()
    except Exception:
        log.exception("MT5 auto-connect worker failed to start (manual Connect still available)")
    yield
    log.info("hero1-api shutting down")


def create_app() -> FastAPI:
    cfg = get_settings()
    configure_logging(cfg.log_level)
    app = FastAPI(title="Hero1 API", version=__version__,
                  description="Hero1 trading research platform — Phase 1 foundation",
                  lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=_DEV_ORIGINS,
                       allow_methods=["GET", "POST", "PUT", "PATCH"], allow_headers=["*"])

    @app.exception_handler(Exception)
    async def unhandled(request, exc):  # noqa: ANN001
        log.exception("unhandled error", extra={"path": str(request.url.path)})
        try:  # the LOG tab must expose the real exception, not just a 500
            import traceback as _tb
            from hero1_eventbus import get_event_publisher
            get_event_publisher().publish(
                category="ERROR", severity="ERROR", subsystem="api",
                message=f"unhandled exception at {request.url.path}: {type(exc).__name__}: {exc}",
                payload={"stage": "UNHANDLED_EXCEPTION", "path": str(request.url.path),
                         "exception_type": type(exc).__name__, "exception_message": str(exc),
                         "traceback": _tb.format_exc()[-6000:]})
        except Exception:  # noqa: BLE001 - never mask the original error
            pass
        return JSONResponse(status_code=500, content={"error": {"code": "internal_error", "message": "internal server error"}})

    @app.exception_handler(KeyError)
    async def key_error(_request, exc):  # noqa: ANN001
        return JSONResponse(status_code=404, content={"error": {"code": "not_found", "message": str(exc)}})

    app.include_router(routers.health.router)
    app.include_router(routers.instruments.router)
    app.include_router(routers.settings.router)
    app.include_router(routers.events.router)
    app.include_router(routers.nodes.router)
    app.include_router(routers.mt5.router)
    app.include_router(routers.metadata.router)
    app.include_router(routers.market_data.router)
    app.include_router(routers.deeptesting.router)

    @app.websocket("/ws/v1/events")
    async def ws_events(ws: WebSocket) -> None:
        await ws.accept()
        params = ws.query_params
        instrument = (params.get("instrument") or "").strip().upper() or None
        category = (params.get("category") or "").strip().upper() or None
        severity = (params.get("severity") or "").strip().upper() or None
        if category and category not in {c.value for c in EventCategory}:
            await ws.close(code=4400, reason="unknown category")
            return
        if severity and severity not in {s.value for s in EventSeverity}:
            await ws.close(code=4400, reason="unknown severity")
            return
        try:
            since_id = int(params.get("since_id") or 0)
        except ValueError:
            since_id = 0

        def matches(ev: dict) -> bool:
            if instrument and (ev.get("instrument") or "") != instrument:
                return False
            if category and ev.get("category") != category:
                return False
            if severity and ev.get("severity") != severity:
                return False
            return True

        # 1) replay missed events (no polling — one indexed read)
        with registry_session() as s:
            rows = EventRepository(s).list(instrument=instrument, category=category,
                                           severity=severity, since_id=since_id, limit=200)
            replay = events_to_dicts(rows)
        await ws.send_json({"type": "replay", "count": len(replay), "events": replay})

        # 2) live push
        bus = get_event_bus()
        queue = bus.subscribe()
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=25.0)
                    if matches(ev):
                        await ws.send_json({"type": "event", "event": ev})
                except asyncio.TimeoutError:
                    await ws.send_json({"type": "heartbeat", "ts": time.time()})
        except WebSocketDisconnect:
            pass
        finally:
            bus.unsubscribe(queue)

    return app


app = create_app()
