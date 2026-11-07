"""MT5 connection surface: status, terminal discovery, explicit connect/disconnect.

CONTRACT:
  * one terminal session at a time (the official IPC model) - the account
    switch picks a DIFFERENT terminal install; one terminal cannot expose two
    accounts at once, and Hero1 never pretends otherwise;
  * selection affects the Live Testing context only - it is persisted as the
    application_settings key `mt5.active_terminal_path` and NOTHING else reads
    it (nodes, fingerprints, research, market data are untouched);
  * no credentials are ever requested, stored or echoed;
  * connection failures return structured 503s naming the exact reason.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.deps import get_registry_session
from hero1_eventbus import get_event_publisher
from hero1_mt5_adapter import connection as mt5_connection
from hero1_storage.repositories import SettingsRepository

router = APIRouter(prefix="/api/v1/mt5", tags=["mt5"])

_SETTING_TERMINAL = "mt5.active_terminal_path"


class Mt5ConnectRequest(BaseModel):
    terminal_path: str | None = Field(default=None, max_length=512,
                                      description="terminal64.exe or its installation folder; omit to use the previously selected/auto-discovered terminal")


def _saved_terminal_path(registry: Session) -> str | None:
    try:
        value = SettingsRepository(registry).get(_SETTING_TERMINAL)
        return str(value) if value else None
    except Exception:  # noqa: BLE001 - settings unreadable must not mask MT5 status
        return None


@router.get("/status")
def mt5_status(registry: Session = Depends(get_registry_session)) -> dict:
    snap = mt5_connection.status()
    snap["active_terminal_path"] = _saved_terminal_path(registry)
    return snap


@router.get("/terminals")
def mt5_terminals(registry: Session = Depends(get_registry_session)) -> dict:
    terms = mt5_connection.discover_terminals()
    for t in terms:  # DISCOVERED vs RUNNING is factual process state, not a guess
        running = mt5_connection.terminal_running(t["path"])
        t["running"] = running
        t["state"] = "RUNNING" if running else "DISCOVERED"
    saved = _saved_terminal_path(registry)
    return {
        "platform_windows": __import__("platform").system() == "Windows",
        "package_available": mt5_connection.package_available(),
        "import_error": mt5_connection.import_error() or None,
        "saved_terminal_path": saved,
        "terminals": terms,
        "note": ("Discovery is Windows-only and read-only. One terminal can host exactly one "
                 "logged-in account; select the terminal whose login is the account you need."),
    }


@router.post("/connect")
def mt5_connect(body: Mt5ConnectRequest, registry: Session = Depends(get_registry_session)) -> dict:
    path = (body.terminal_path or "").strip() or _saved_terminal_path(registry) or None
    result = mt5_connection.connect(path)
    # ACCOUNT_LOADED is full success; CONNECTED means the terminal is up but
    # the user must log in inside it - that is a displayed state with the
    # selection kept, not a 503. Everything else is a structured 503.
    if result.get("ok") or result.get("state") == mt5_connection.STATE_CONNECTED:
        if path:
            try:
                SettingsRepository(registry).set(_SETTING_TERMINAL, path, updated_by="mt5-topbar")
            except Exception:  # noqa: BLE001 - persistence failure must not hide a live connection
                result["warning"] = (result.get("warning") or "") + " (active terminal path could not be persisted to application_settings)"
        get_event_publisher().publish(
            category="SYSTEM", severity="INFO", subsystem="mt5",
            message=f"MT5 connected [{(result.get('account') or {}).get('server') or 'unknown server'}]",
            payload={"login": (result.get("account") or {}).get("login"),
                     "server": (result.get("account") or {}).get("server"),
                     "trade_mode_name": (result.get("account") or {}).get("trade_mode_name")})
        if result.get("ok"):
            # ISSUE-056: metadata follows the ACTUAL connected account,
            # automatically - no manual Refresh needed after (re)connecting.
            try:
                from app import mt5_autoconnect
                mt5_autoconnect.refresh_metadata_after_connect("connect")
            except Exception:  # noqa: BLE001 - metadata must never break connect
                pass
        result["active_terminal_path"] = path
        return result
    raise HTTPException(status_code=503, detail={
        "code": result.get("error_code") or "MT5_CONNECT_FAILED",
        "message": result.get("error") or "MT5 connection failed",
        "last_error": result.get("last_error"),
        "platform": result.get("platform"),
        "package_available": result.get("package_available"),
    })


@router.post("/disconnect")
def mt5_disconnect() -> dict:
    return mt5_connection.disconnect()
