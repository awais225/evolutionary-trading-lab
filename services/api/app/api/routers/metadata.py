"""Broker-specific instrument metadata endpoints (per broker/server + symbol)."""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import metadata_service
from app.deps import get_instrument_context
from hero1_eventbus import get_event_publisher
from hero1_mt5_adapter import connection as mt5_connection
from hero1_mt5_adapter import build_snapshot

router = APIRouter(prefix="/api/v1/instruments/{instrument}/metadata", tags=["metadata"])


class MetadataImportRequest(BaseModel):
    broker_server: str = Field(min_length=1, max_length=128)
    symbol_info: dict = Field(description="the raw MT5 symbol_info() payload for the symbol (as returned by the terminal)")
    tick: Optional[dict] = Field(default=None, description="optional raw symbol_info_tick() payload")
    terminal_identity: Optional[dict] = None
    account_login: Optional[int] = None
    retrieved_at: Optional[str] = None
    source: str = Field(default="manual-import", pattern=r"^(manual-import|mt5-terminal|import-file)$")


def _write_snapshot_or_503(snapshot: dict, instrument: str, subsystem: str, action: str) -> dict:
    try:
        saved = metadata_service.save_snapshot(snapshot)
    except PermissionError as exc:
        raise HTTPException(503, detail={"code": "METADATA_STORE_READ_ONLY",
                                         "message": f"cannot write the metadata store under HERO1_DATA_ROOT: {exc}"}) from exc
    except OSError as exc:
        raise HTTPException(503, detail={"code": "METADATA_STORE_ERROR",
                                         "message": f"metadata store write failed: {exc}"}) from exc
    get_event_publisher().publish(
        category="SYSTEM", severity="INFO", subsystem=subsystem,
        message=f"broker metadata {action} [{instrument}] {snapshot.get('broker_server')}",
        payload={"fingerprint": snapshot.get("fingerprint"), "source": snapshot.get("source")})
    return {**metadata_service.available_view(instrument), "saved": saved}


@router.get("")
def read_metadata(instrument: str = Depends(get_instrument_context)) -> dict:
    """Stored snapshot view for this instrument (read-only)."""
    return metadata_service.available_view(instrument)


@router.post("/refresh")
def refresh_metadata(instrument: str = Depends(get_instrument_context)) -> dict:
    """Pull symbol_info() + symbol_info_tick() NOW via the MT5 terminal (Windows only)."""
    mt5 = mt5_connection.mt5_module()
    if mt5 is None:
        raise HTTPException(503, detail={
            "code": "MT5_UNAVAILABLE",
            "message": (f"MetaTrader5 package not importable: {mt5_connection.import_error() or 'not installed'}. "
                        "Refresh from the Windows machine that runs the terminal, or POST /metadata/import."),
            "platform": __import__("platform").system(),
        })
    result = mt5_connection.connect(None)  # reuse the live session or initialize default
    if not result.get("ok"):
        raise HTTPException(503, detail={"code": result.get("error_code") or "MT5_CONNECT_FAILED",
                                         "message": result.get("error"),
                                         "last_error": result.get("last_error")})
    with mt5_connection.SESSION_LOCK:
        symbol_info = mt5.symbol_info(instrument)
        tick = mt5.symbol_info_tick(instrument)
        if symbol_info is None:
            raise HTTPException(503, detail={"code": "MT5_SYMBOL_MISSING",
                                             "message": f"symbol_info('{instrument}') returned None - "
                                                        "is the symbol visible/available on this terminal?",
                                             "last_error": mt5_connection.as_dict(mt5.last_error()) if hasattr(mt5, 'last_error') else None})
        account = result.get("account") or {}
        terminal = result.get("terminal") or {}
        server = account.get("server") or terminal.get("company") or "unknown-server"
        snapshot = build_snapshot(
            broker_server=str(server), symbol=instrument,
            raw_symbol=symbol_info, raw_tick=tick,
            terminal_identity={"path": terminal.get("path"), "build": terminal.get("build"),
                               "company": terminal.get("company"), "name": terminal.get("name"),
                               "connected": terminal.get("connected")},
            account_login=(account.get("login") if isinstance(account.get("login"), int) else None),
            source="mt5-terminal")
    return _write_snapshot_or_503(snapshot, instrument, "mt5", "refreshed")


@router.post("/import", status_code=201)
def import_metadata(body: MetadataImportRequest, instrument: str = Depends(get_instrument_context)) -> dict:
    """Store a broker metadata snapshot supplied out-of-band (e.g. captured on Windows).

    The payload is validated field-by-field; unknown keys are preserved under
    raw.* and never silently discarded. This path NEVER invents values."""
    try:
        snapshot = build_snapshot(
            broker_server=body.broker_server, symbol=instrument,
            raw_symbol=body.symbol_info, raw_tick=body.tick,
            terminal_identity=body.terminal_identity,
            account_login=body.account_login,
            source=body.source, retrieved_at=body.retrieved_at)
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "METADATA_IMPORT_INVALID", "message": str(exc)}) from exc
    return _write_snapshot_or_503(snapshot, instrument, "mt5", "imported")


@router.get("/all")
def all_metadata(instrument: str = Depends(get_instrument_context)) -> dict:
    """Every stored snapshot for this instrument (broker-by-broker isolation audit)."""
    return {"instrument": instrument.upper(),
            "snapshots": metadata_service.list_snapshots(instrument)}
