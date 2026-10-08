"""V5.1a-next §B — MT5 account discovery and explicit account selection.

The operator may have several terminals installed (an official MetaTrader 5 and
one or more broker-branded copies), each logged into a different account.  This
module answers, read-only and without ever placing an order:

  * which terminals exist on this machine            -> ``terminals_from_config()``
  * which account each one is actually logged into   -> ``probe_terminal()``
  * which one the running backend currently uses     -> ``active_account()``
  * and it performs the switch when asked            -> ``select_account()``

Honesty rules (same as §5 of the Windows diagnostic):

  * nothing is probed when the MetaTrader5 package is not importable — the
    response says so instead of showing an invented account list;
  * an account whose ``trade_mode`` is not DEMO is reported as REAL/CONTEST and
    stays blocked by the execution guard (`demo_account_guard`);
  * account numbers/servers are shown; passwords are never read from or written
    to the config (``mt5.config`` strips sensitive keys).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("mt5.accounts")

# trade_mode -> label (MetaTrader5 ACCOUNT_TRADE_MODE_* constants)
_TRADE_MODE_NAMES = {0: "DEMO", 1: "CONTEST", 2: "REAL"}


def _mt5_module():
    """The MetaTrader5 module, or None when it is not importable here."""
    try:
        from .mt5_real import MT5_PACKAGE_AVAILABLE, MT5_IMPORT_ERROR, mt5  # type: ignore
    except Exception:  # pragma: no cover - import guard
        return None, "", "the MetaTrader5 package could not be imported"
    if not MT5_PACKAGE_AVAILABLE or mt5 is None:
        return None, str(MT5_IMPORT_ERROR or "not installed"), (
            "the MetaTrader5 package is not importable in this interpreter, so no "
            "terminal can be probed")
    return mt5, "", ""


def terminals_from_config() -> List[Dict[str, str]]:
    """Terminal executables worth probing: the saved one first, then discovery.

    Never raises; a machine with no terminals (or a non-Windows host) yields an
    empty list, which callers must report as "none found", not as an error.
    """
    out: List[Dict[str, str]] = []
    seen: set = set()

    def add(name: str, path: str, origin: str) -> None:
        p = str(path or "").strip()
        if not p:
            return
        key = str(Path(p)).lower()
        if key in seen:
            return
        seen.add(key)
        out.append({"name": name or Path(p).parent.name, "path": p, "origin": origin})

    try:
        from .config import load_mt5_config
        cfg = load_mt5_config() or {}
        saved = str(cfg.get("terminal_path") or "").strip()
        if saved:
            add(str(cfg.get("last_connected_terminal") or "Saved terminal"), saved, "config")
    except Exception as e:  # pragma: no cover - defensive
        log.debug("saved terminal path unavailable: %s", e)

    try:
        from .discovery import discover_terminals
        for term in discover_terminals() or []:
            add(str(term.get("name") or ""), str(term.get("path") or ""), "discovery")
    except Exception as e:  # pragma: no cover - defensive
        log.debug("terminal discovery failed: %s", e)

    return out


def _trade_mode_name(mode: Optional[int]) -> str:
    if mode is None:
        return "UNKNOWN"
    return _TRADE_MODE_NAMES.get(int(mode), f"UNKNOWN({mode})")


def probe_terminal(path: str, *, timeout_ms: int = 60000) -> Dict[str, Any]:
    """Initialize a terminal **just long enough to read its account**, read-only.

    Returns a dict describing what was found; it never sends anything to the
    broker.  ``initialized`` says whether ``mt5.initialize(path)`` succeeded.

    V5.3 §2.1 — this probe USED to call ``mt5.shutdown()`` at the end without
    restoring anything.  That destroyed the IPC session the *order path* uses
    while ``MT5RealBridge._connected`` still said True, so the next ``order_send``
    was issued against a dead session and answered ``None`` — indistinguishable
    from a broker refusal. Every probe now runs under the session lock and the
    live session is re-established before the lock is released.
    """
    from .mt5_real import SESSION_LOCK, session_snapshot, restore_session_from_snapshot
    with SESSION_LOCK:
        live = session_snapshot()
        row = _probe_terminal_locked(path, timeout_ms=timeout_ms)
        row["session_before_probe"] = {
            "terminal_info_available": live.get("terminal_info_available"),
            "account_login": live.get("account_login"),
            "path": live.get("path"), "generation": live.get("generation")}
        row["session_restored"] = False
        row["session_after_probe"] = None
        if live.get("terminal_info_available"):
            # put the trading session back exactly as it was before the probe
            restored = restore_session_from_snapshot(live)
            row["session_restored"] = bool(restored.get("restored"))
            row["session_after_probe"] = restored
            if not restored.get("restored"):
                log.error("[V5.3] the live MT5 session could NOT be restored after probing "
                          "%s: %s", path, restored.get("reason"))
        return row


def _probe_terminal_locked(path: str, *, timeout_ms: int = 60000) -> Dict[str, Any]:
    """The actual probe. Called with SESSION_LOCK held (never directly)."""
    mt5, reason, _ = _mt5_module()
    row: Dict[str, Any] = {"path": path, "initialized": False, "connected": False,
                           "login": None, "server": None, "company": None,
                           "terminal": None, "build": None, "trade_mode": None,
                           "trade_mode_name": "UNKNOWN", "account_kind": "UNKNOWN",
                           "demo": None, "trade_allowed": None, "trade_expert": None,
                           "balance": None, "currency": None, "last_error": None,
                           "reason": ""}
    if mt5 is None:
        row["reason"] = reason or "MetaTrader5 package not importable"
        return row
    try:
        ok = mt5.initialize(path=str(path), timeout=timeout_ms)
    except Exception as e:
        row["reason"] = f"initialize raised: {e}"
        return row
    row["initialized"] = bool(ok)
    if not ok:
        try:
            row["last_error"] = list(mt5.last_error()) if mt5.last_error() else None
        except Exception:
            pass
        row["reason"] = "mt5.initialize refused this terminal"
        return row
    try:
        ti = mt5.terminal_info()
        if ti is not None:
            row["terminal"] = str(getattr(ti, "name", "") or "")
            row["company"] = str(getattr(ti, "company", "") or "")
            row["connected"] = bool(getattr(ti, "connected", False))
            row["trade_allowed"] = bool(getattr(ti, "trade_allowed", False))
        ai = mt5.account_info()
        if ai is None:
            row["reason"] = ("the terminal is running but no account is logged in "
                             "(account_info() returned None)")
        else:
            mode = int(getattr(ai, "trade_mode", -1))
            row.update({"login": int(getattr(ai, "login", 0) or 0) or None,
                        "server": str(getattr(ai, "server", "") or ""),
                        "currency": str(getattr(ai, "currency", "") or ""),
                        "balance": float(getattr(ai, "balance", 0.0) or 0.0),
                        "trade_mode": mode,
                        "trade_mode_name": _trade_mode_name(mode),
                        "account_kind": _trade_mode_name(mode),
                        "demo": mode == 0,
                        "trade_expert": bool(getattr(ai, "trade_expert", False))})
            if mode != 0:
                row["reason"] = (f"account {row['login']} on '{row['server']}' is a "
                                 f"{row['trade_mode_name']} account — demo orders are refused")
    except Exception as e:  # pragma: no cover - terminal-specific
        row["reason"] = f"probe failed: {e}"
    finally:
        try:
            mt5.shutdown()
            from .mt5_real import _session_mark
            _session_mark(initialized=False, by="accounts.probe_terminal")
        except Exception:
            pass
    try:
        from .mt5_real import mt5 as _m
        row["build"] = None
        if _m is not None and row["initialized"]:
            ver = _m.version()
            if ver and len(ver) > 1:
                row["build"] = int(ver[1])
    except Exception:
        pass
    return row


def probe_all(terminals: Optional[List[Dict[str, str]]] = None) -> List[Dict[str, Any]]:
    """Probe every known terminal and return one row per account found."""
    rows: List[Dict[str, Any]] = []
    for term in (terminals if terminals is not None else terminals_from_config()):
        row = probe_terminal(str(term.get("path") or ""))
        row["name"] = term.get("name") or Path(str(term.get("path") or "")).name
        row["origin"] = term.get("origin") or "discovery"
        rows.append(row)
    return rows


def active_account() -> Dict[str, Any]:
    """What the *running* bridge is connected to right now (read-only)."""
    try:
        from .factory import get_bridge
        from .config import load_mt5_config
        b = get_bridge()
        st = b.status() or {}
        cfg = load_mt5_config() or {}
        source = str(getattr(b, "source", "") or "")
        mode_raw = st.get("account_trade_mode")
        if mode_raw is None:
            # status() exposes "demo" for the real bridge; map it back.
            demo = st.get("demo")
            mode_raw = None if demo is None else (0 if demo else 2)
        login = st.get("account")
        return {
            "active_bridge": str(getattr(b, "name", "") or ""),
            "source": source,
            "is_simulated": source == "SIMULATOR",
            "connected": bool(st.get("connected")),
            "login": login,
            "server": st.get("server"),
            "login_matches_active": (str(login) == str(cfg.get("selected_login"))
                                     if cfg.get("selected_login") else None),
            "terminal_path": st.get("terminal_path") or cfg.get("terminal_path"),
            "account_kind": _trade_mode_name(mode_raw),
            "demo": st.get("demo"),
            "reason": st.get("last_error") or "",
        }
    except Exception as e:  # pragma: no cover - defensive
        return {"active_bridge": "", "source": "", "is_simulated": None,
                "connected": False, "login": None, "server": None,
                "account_kind": "UNKNOWN", "demo": None, "reason": str(e)}


def list_accounts(*, probe: bool = True) -> Dict[str, Any]:
    """The §B account list for the UI selector.

    ``probe=False`` skips the (slow, terminal-starting) probe and returns only the
    terminals on disk — used by fast smoke checks.
    """
    mt5, reason, human = _mt5_module()
    terminals = terminals_from_config()
    accounts: List[Dict[str, Any]] = []
    if probe and mt5 is not None:
        accounts = probe_all(terminals)
    active = active_account()
    for row in accounts:
        row["active"] = bool(
            row.get("login") and active.get("login")
            and str(row["login"]) == str(active["login"]))
    return {
        "probed": bool(probe and mt5 is not None),
        "package_available": mt5 is not None,
        "reason": "" if mt5 is not None else (human or reason),
        "terminals": [{"name": t["name"], "path": t["path"], "origin": t["origin"]}
                      for t in terminals],
        "accounts": accounts,
        "active": active,
    }


def select_account(path: str, *, login: int = 0, password: str = "",
                   server: str = "") -> Dict[str, Any]:
    """Switch the running backend onto a specific terminal/account (§B).

    Uses ``MT5RealBridge.switch_to_path`` (shutdown -> initialize(path) -> probe)
    and persists the choice in CONFIG/mt5_config.json **without** the password.
    Returns the probe of the newly active account, or the exact refusal reason.
    """
    from .discovery import validate_terminal_path
    from .config import set_saved_terminal_path, load_mt5_config, save_mt5_config

    valid, why = validate_terminal_path(path)
    if not valid:
        return {"ok": False, "error": "INVALID_TERMINAL_PATH", "reason": why, "path": path}

    mt5, reason, human = _mt5_module()
    if mt5 is None:
        return {"ok": False, "error": "MT5_PACKAGE_MISSING",
                "reason": human or reason, "path": path}

    from .mt5_real import MT5RealBridge
    bridge = MT5RealBridge(login=login, password=password, server=server, path=path)
    try:
        report = bridge.switch_to_path(path)
    finally:
        # keep the bridge reachable for the factory; the factory rebuilds from
        # config, so dropping this instance is safe and leaves no half-state.
        try:
            bridge.disconnect()
        except Exception:
            pass

    if report.get("connected"):
        set_saved_terminal_path(path, auto_discover=False)
        cfg = load_mt5_config() or {}
        cfg["selected_login"] = report.get("login")
        cfg["selected_server"] = report.get("server") or cfg.get("selected_server")
        if not cfg.get("terminal_path"):
            cfg["terminal_path"] = path
        save_mt5_config(cfg)                      # passwords are stripped here
        try:
            from .factory import reset_bridge
            reset_bridge(explicit_path=path)
        except Exception as e:  # pragma: no cover - defensive
            log.warning("bridge reset after account switch failed: %s", e)

    report["ok"] = bool(report.get("connected"))
    report.setdefault("path", path)
    return report
