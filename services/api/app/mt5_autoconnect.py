"""Automatic MT5 connection + broker-metadata refresh (ISSUE-056).

Startup policy (never blocks the API, never launches a terminal, never
guesses between multiple running terminals):

  * inspect discovered terminals and which are RUNNING;
  * exactly one unambiguous candidate (or the user's saved terminal is the
    running one) -> connect to it (attach only, launch=False) and verify
    via terminal_info()/account_info() inside connection.connect();
  * zero or multiple running -> stay disconnected and say why; the existing
    dropdown/manual Connect remains the way in;
  * failure -> the real reason is published to LOG and kept for /mt5/status;
  * bounded retries with backoff (the terminal may still be starting);
    a manual connection at any point stops the retry loop.

After ANY successful connection (automatic or manual), broker metadata for
every registered instrument is refreshed from the ACTUAL connected account
in a background thread - deduplicated, so account switching cannot pile up
concurrent refreshes.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional

from hero1_eventbus import get_event_publisher
from hero1_mt5_adapter import connection as mt5_connection

_RETRY_DELAYS_S = (0, 3, 8, 20, 45)  # bounded: ~76s worst case, in background
_started = threading.Event()
_meta_lock = threading.Lock()
_meta_running = False


def _publish(severity: str, message: str, payload: Optional[dict] = None) -> None:
    try:
        get_event_publisher().publish(
            category="SYSTEM", severity=severity, subsystem="mt5",
            message=message[:500], payload=payload or {})
    except Exception:  # noqa: BLE001 - eventing must never break connection logic
        pass


def _saved_terminal_path() -> Optional[str]:
    try:
        from hero1_storage.engine import registry_session
        from hero1_storage.repositories import SettingsRepository
        s = registry_session()
        try:
            return SettingsRepository(s).get("mt5.active_terminal_path")
        finally:
            s.close()
    except Exception:  # noqa: BLE001 - no registry yet -> no saved choice
        return None


def _registered_instruments() -> List[str]:
    try:
        from sqlalchemy import select
        from hero1_storage.engine import registry_session
        from hero1_storage import registry_models as rm
        s = registry_session()
        try:
            rows = s.execute(select(rm.Instrument.symbol).where(
                rm.Instrument.enabled.is_(True))).scalars()
            return [str(x).upper() for x in rows]
        finally:
            s.close()
    except Exception:  # noqa: BLE001
        return []


def refresh_metadata_after_connect(reason: str,
                                   refresh_fn: Optional[Callable[[str], Any]] = None,
                                   instruments: Optional[List[str]] = None) -> bool:
    """Refresh broker metadata for all registered instruments, once.

    Deduplicated: if a refresh is already running, this returns False and
    does NOT start a competing one. Runs on a daemon thread.
    """
    global _meta_running
    with _meta_lock:
        if _meta_running:
            return False
        _meta_running = True

    def _run() -> None:
        global _meta_running
        try:
            from app import metadata_service
            fn = refresh_fn or metadata_service.refresh_from_terminal
            targets = instruments if instruments is not None else _registered_instruments()
            for sym in targets:
                try:
                    saved = fn(sym)
                    _publish("INFO",
                             f"broker metadata auto-refreshed [{sym}] "
                             f"{(saved or {}).get('broker_server')} ({reason})",
                             {"instrument": sym, "automatic": True, "reason": reason,
                              "broker_server": (saved or {}).get("broker_server"),
                              "retrieved_at": (saved or {}).get("retrieved_at")})
                except Exception as exc:  # noqa: BLE001 - per-symbol failure is reported, not fatal
                    _publish("WARNING",
                             f"broker metadata auto-refresh failed [{sym}]: {exc}",
                             {"instrument": sym, "automatic": True, "reason": reason,
                              "error": str(exc)[:300]})
        finally:
            with _meta_lock:
                _meta_running = False

    threading.Thread(target=_run, name="mt5-metadata-refresh", daemon=True).start()
    return True


def run_auto_connect(*, saved_path: Optional[str] = None,
                     discover_fn: Optional[Callable[[], List[Dict[str, Any]]]] = None,
                     running_fn: Optional[Callable[[str], Optional[bool]]] = None,
                     connect_fn: Optional[Callable[..., Dict[str, Any]]] = None) -> Dict[str, Any]:
    """One automatic-connection attempt. Fully injectable for tests."""
    discover = discover_fn or mt5_connection.discover_terminals
    is_running = running_fn or mt5_connection.terminal_running
    connect = connect_fn or mt5_connection.connect

    if not mt5_connection.package_available():
        return {"action": "none", "state": mt5_connection.STATE_UNAVAILABLE,
                "reason": f"MetaTrader5 package not importable: {mt5_connection.import_error() or 'not installed'}"}
    terminals = []
    for t in discover():
        entry = dict(t)
        entry["running"] = bool(is_running(str(t.get("path"))))
        terminals.append(entry)
    decision = mt5_connection.decide_auto_connect(
        terminals, saved_terminal_path if saved_path is not None else _saved_terminal_path())
    if decision["action"] != "connect":
        return {"action": "none", "state": mt5_connection.STATE_NOT_CONNECTED,
                "reason": decision["reason"], "running": decision["running"]}
    result = connect(decision["path"], launch=False)
    out = {"action": "connect", "path": decision["path"],
           "state": result.get("state"), "ok": bool(result.get("ok")),
           "account": result.get("account"), "terminal": result.get("terminal"),
           "error": result.get("error"), "error_code": result.get("error_code"),
           "reason": decision["reason"]}
    if result.get("ok"):
        refresh_metadata_after_connect("auto-connect")
    return out


def _auto_connect_worker() -> None:
    for attempt, delay in enumerate(_RETRY_DELAYS_S, start=1):
        if delay:
            time.sleep(delay)
        try:
            if (mt5_connection.status().get("session") or {}).get("initialized"):
                return  # user connected manually meanwhile; never compete
        except Exception:  # noqa: BLE001
            pass
        try:
            out = run_auto_connect()
        except Exception as exc:  # noqa: BLE001
            _publish("WARNING", f"MT5 auto-connect attempt {attempt} raised: {exc}",
                     {"attempt": attempt, "error": str(exc)[:300]})
            continue
        if out.get("ok"):
            acct = out.get("account") or {}
            _publish("INFO",
                     f"MT5 auto-connected [{acct.get('server') or 'unknown server'}] "
                     f"login {acct.get('login')} via {out.get('path')}",
                     {"attempt": attempt, "path": out.get("path"),
                      "login": acct.get("login"), "server": acct.get("server")})
            return
        _publish("INFO" if attempt == 1 else "WARNING",
                 f"MT5 auto-connect attempt {attempt}: {out.get('reason') or out.get('error') or out.get('state')}",
                 {"attempt": attempt, "state": out.get("state"),
                  "error": out.get("error"), "running": out.get("running")})
        if out.get("action") == "none" and "ambiguous" in str(out.get("reason", "")):
            return  # waiting for the user to choose; retrying cannot help
    _publish("WARNING",
             "MT5 auto-connect gave up after bounded retries; use the terminal dropdown to connect manually",
             {"attempts": len(_RETRY_DELAYS_S)})


def start_auto_connect() -> bool:
    """Start the background auto-connect worker exactly once per process."""
    if _started.is_set():
        return False
    _started.set()
    threading.Thread(target=_auto_connect_worker, name="mt5-auto-connect", daemon=True).start()
    return True
