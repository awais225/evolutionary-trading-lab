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
    """Stored snapshot view (read-only; live-server staleness annotated)."""
    return metadata_service.annotate_staleness(metadata_service.available_view(instrument))


@router.post("/refresh")
def refresh_metadata(instrument: str = Depends(get_instrument_context)) -> dict:
    """Pull symbol_info() + symbol_info_tick() NOW via the MT5 terminal (Windows only)."""
    try:
        saved = metadata_service.refresh_from_terminal(instrument)
    except metadata_service.MetadataRefreshError as exc:
        raise HTTPException(503, detail={"code": exc.code, "message": exc.message,
                                         "platform": __import__("platform").system()}) from None
    get_event_publisher().publish(
        category="SYSTEM", severity="INFO", subsystem="mt5",
        message=f"broker metadata refreshed [{instrument}] {saved.get('broker_server')}",
        payload={"fingerprint": saved.get("fingerprint"), "source": saved.get("source"),
                 "automatic": False})
    return {**metadata_service.available_view(instrument), "saved": saved}


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
