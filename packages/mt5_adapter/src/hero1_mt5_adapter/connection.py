"""MT5 terminal connection - Windows-targeted, import-safe everywhere.

HONESTY CONTRACT (this package is the ONLY place Hero1 may speak to MT5):
  * the official `MetaTrader5` package is Windows-only; on Linux it is never
    importable and every entry point reports an explicit UNAVAILABLE result -
    never a simulated account, never mocked values presented as real;
  * `connect()` initializes the terminal through `MetaTrader5.initialize` and
    reports exactly what the terminal then returns via `terminal_info()` /
    `account_info()` - missing fields stay missing, they are never guessed;
  * nothing here ever sends, checks or modifies an order;
  * passwords are never requested, stored, logged or echoed - MT5 login is a
    user-controlled procedure performed inside the terminal itself.

Session state is tracked module-internally under ONE lock so concurrent API
calls cannot interleave initialize/shutdown (see SESSION_LOCK notes).
"""
from __future__ import annotations

import platform
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

SESSION_LOCK = threading.RLock()

try:  # official Windows-only package
    import MetaTrader5 as _MT5  # type: ignore
    MT5_PACKAGE_AVAILABLE = True
    MT5_IMPORT_ERROR = ""
except Exception as exc:  # noqa: BLE001 - the failure reason matters to the caller
    _MT5 = None  # type: ignore[assignment]
    MT5_PACKAGE_AVAILABLE = False
    MT5_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"

COMMON_EXE_NAMES = ("terminal64.exe", "terminal.exe", "metatrader64.exe", "metatrader.exe")

_SESSION: Dict[str, Any] = {
    "initialized": False,
    "path": "",
    "generation": 0,
    "last_connection_error": None,
}


def package_available() -> bool:
    return MT5_PACKAGE_AVAILABLE


def mt5_module():
    """The imported MetaTrader5 module, or None. Read-only escape hatch used by
    the API layer for symbol queries; connection lifecycle stays in connect/disconnect."""
    return _MT5


def import_error() -> str:
    return MT5_IMPORT_ERROR


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def as_dict(named: Any) -> Dict[str, Any]:
    """MetaTrader5 returns named tuples; accept dicts too; never raise."""
    if named is None:
        return {}
    if isinstance(named, dict):
        return dict(named)
    asdict = getattr(named, "_asdict", None)
    if callable(asdict):
        try:
            return dict(asdict())
        except Exception:  # noqa: BLE001
            pass
    try:
        return dict(vars(named))
    except Exception:  # noqa: BLE001
        return {}


def _last_error() -> Optional[List[Any]]:
    if _MT5 is None:
        return None
    try:
        err = _MT5.last_error()
        return list(err)
    except Exception:  # noqa: BLE001
        return None


def normalize_terminal_path(path_str: str) -> Tuple[str, str]:
    """Accept either the terminal .exe or its installation folder.

    `initialize(path=...)` needs the executable. A folder path is a known
    real-world misconfiguration; we resolve `<folder>/terminal64.exe` etc.
    instead of failing with an opaque -10003. Returns (usable_path, note).
    """
    from pathlib import Path

    raw = str(path_str or "").strip()
    if not raw:
        return "", "empty path"
    p = Path(raw)
    try:
        if p.is_file():
            return str(p), ""
        if p.is_dir():
            for exe in COMMON_EXE_NAMES:
                cand = p / exe
                if cand.exists():
                    return str(cand), f"the configured path was a folder; using {cand.name} inside it"
            return str(p), "the configured path is a folder with no terminal executable inside it"
    except Exception as exc:  # noqa: BLE001
        return raw, f"path could not be inspected: {exc}"
    return raw, "the configured path does not exist"


def discover_terminals() -> List[Dict[str, str]]:
    """Best-effort scan for MT5 installations. NEVER invents entries.

    Windows: scans Program Files / APPDATA MetaQuotes Terminal folders / local app data
    and the configured path. Other platforms: returns an empty list (reported
    as such). Existing terminal64.exe files only.
    """
    import glob
    import os
    from pathlib import Path

    found: List[Dict[str, str]] = []
    seen = set()

    def add(path: Path, origin: str) -> None:
        try:
            rp = path.resolve()
        except Exception:  # noqa: BLE001
            rp = path
        key = str(rp).lower()
        if key in seen:
            return
        seen.add(key)
        found.append({"name": rp.parent.name or rp.name, "path": str(rp), "origin": origin})

    if platform.system() != "Windows":
        return found

    candidates: List[Tuple[str, str]] = []
    program_roots = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                     os.environ.get("ProgramW6432")]
    for root in program_roots:
        if root:
            candidates.append((os.path.join(root, "MetaTrader*", "terminal64.exe"), "program-files"))
            candidates.append((os.path.join(root, "*MetaTrader*", "terminal64.exe"), "program-files"))
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append((os.path.join(appdata, "MetaQuotes", "Terminal", "*", "terminal64.exe"), "appdata"))
    localapp = os.environ.get("LOCALAPPDATA")
    if localapp:
        candidates.append((os.path.join(localapp, "MetaQuotes", "Terminal", "*", "terminal64.exe"), "localappdata"))

    for pattern, origin in candidates:
        try:
            for match in glob.glob(pattern):
                add(Path(match), origin)
        except Exception:  # noqa: BLE001 - a broken path must not break discovery
            continue
    return found


def _terminal_view(ti: Any, path: str) -> Dict[str, Any]:
    d = as_dict(ti)
    return {
        "path": path,
        "connected": d.get("connected"),
        "build": d.get("build"),
        "company": d.get("company"),
        "name": d.get("name"),
        "language": d.get("language"),
        "path_terminal": d.get("path"),
        "data_path": d.get("data_path"),
        "trade_allowed": d.get("trade_allowed"),
        "trade_allowed_raw": d.get("trade_allowed"),
        "trade_expert": d.get("trade_expert"),
        "community_connection": d.get("community_connection"),
        "raw": d,
    }


def _account_view(ai: Any) -> Dict[str, Any]:
    d = as_dict(ai)
    trade_mode = d.get("trade_mode")
    trade_mode_name = {0: "DEMO", 1: "CONTEST", 2: "REAL"}.get(
        int(trade_mode) if isinstance(trade_mode, (int, float)) else -1, None)
    return {
        "login": d.get("login"),
        "server": d.get("server"),
        "company": d.get("company"),
        "currency": d.get("currency"),
        "balance": d.get("balance"),
        "equity": d.get("equity"),
        "margin": d.get("margin"),
        "margin_free": d.get("margin_free"),
        "margin_level": d.get("margin_level"),
        "leverage": d.get("leverage"),
        "trade_mode": trade_mode,
        "trade_mode_name": trade_mode_name,
        "limit_orders": d.get("limit_orders"),
        # every raw key the terminal reported, unmodified:
        "raw": d,
    }


def connect(terminal_path: Optional[str] = None) -> Dict[str, Any]:
    """Initialize MT5 (optionally with an explicit terminal path). Read-only.

    Never places, checks or modifies an order. Returns a plain dict so the API
    can serialize it as-is. On any failure the payload says exactly why.
    """
    with SESSION_LOCK:
        out: Dict[str, Any] = {
            "ok": False, "checked_at": _iso_now(), "platform": platform.system(),
            "package_available": MT5_PACKAGE_AVAILABLE, "error_code": None, "error": None,
            "terminal": None, "account": None, "last_error": None, "path_note": None,
        }
        if _MT5 is None:
            out["error_code"] = "MT5_UNAVAILABLE"
            out["error"] = (f"MetaTrader5 package not importable: {MT5_IMPORT_ERROR or 'not installed'}. "
                            "On Windows: pip install MetaTrader5 inside the venv, with a running terminal.")
            _SESSION["last_connection_error"] = out["error"]
            return out

        path, note = (normalize_terminal_path(terminal_path) if terminal_path else ("", None))
        out["path_note"] = note
        kwargs = {"path": path} if path else {}
        try:
            ok = bool(_MT5.initialize(**kwargs))
        except Exception as exc:  # noqa: BLE001
            ok = False
            out["error"] = f"initialize raised {type(exc).__name__}: {exc}"

        if not ok:
            err = _last_error()
            out["last_error"] = err
            out["error_code"] = "MT5_INIT_FAILED"
            out["error"] = (out["error"] or
                            f"MetaTrader5.initialize() failed{' (path: ' + path + ')' if path else ''}. "
                            f"last_error={err}. Is the terminal installed, running and logged in?")
            _SESSION["last_connection_error"] = out["error"]
            return out

        ti = _MT5.terminal_info()
        ai = _MT5.account_info()
        _SESSION.update({"initialized": True, "path": path, "generation": _SESSION["generation"] + 1,
                         "last_connection_error": None})
        out.update({
            "ok": True,
            "terminal": _terminal_view(ti, path),
            "account": _account_view(ai),
            "last_error": _last_error(),
        })
        if ai is None:
            out["warning"] = "terminal initialized but account_info() is None - is a broker account logged in on that terminal?"
        if ti is not None and not bool(as_dict(ti).get("connected", True)):
            out["warning"] = (out.get("warning") or "") + ("terminal reports connected=false - check the terminal's connection status." if out.get("warning") else "terminal reports connected=false - check the terminal's connection status.")
        return out


def account_snapshot() -> Dict[str, Any]:
    """Current account as-is from a live session, or ok=False with the reason."""
    with SESSION_LOCK:
        if _MT5 is None:
            return {"ok": False, "error_code": "MT5_UNAVAILABLE",
                    "error": f"MetaTrader5 package not importable: {MT5_IMPORT_ERROR}",
                    "checked_at": _iso_now(), "account": None}
        ai = _MT5.account_info()
        if ai is None:
            return {"ok": False, "error_code": "MT5_NO_ACCOUNT",
                    "error": "account_info() returned None - terminal not initialized or no account logged in.",
                    "checked_at": _iso_now(), "account": None, "last_error": _last_error()}
        return {"ok": True, "checked_at": _iso_now(), "account": _account_view(ai), "last_error": _last_error()}


def status() -> Dict[str, Any]:
    """Non-initializing status: package, platform, live-session view if any."""
    out: Dict[str, Any] = {
        "ok": False, "checked_at": _iso_now(), "platform": platform.system(),
        "package_available": MT5_PACKAGE_AVAILABLE, "import_error": MT5_IMPORT_ERROR or None,
        "session": dict(_SESSION), "terminal": None, "account": None,
    }
    if _MT5 is None:
        return out
    try:
        ti = _MT5.terminal_info()
        if ti is not None:
            out["terminal"] = _terminal_view(ti, _SESSION.get("path", ""))
    except Exception:  # noqa: BLE001
        pass
    try:
        ai = _MT5.account_info()
        if ai is not None:
            out["account"] = _account_view(ai)
    except Exception:  # noqa: BLE001
        pass
    out["ok"] = out["terminal"] is not None
    return out


def disconnect() -> Dict[str, Any]:
    with SESSION_LOCK:
        if _MT5 is None:
            return {"ok": True, "detail": "nothing to disconnect (package unavailable)"}
        try:
            _MT5.shutdown()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error_code": "MT5_SHUTDOWN_FAILED", "error": f"{type(exc).__name__}: {exc}"}
        _SESSION.update({"initialized": False, "generation": _SESSION["generation"] + 1})
        return {"ok": True, "detail": "terminal session released"}


__all__ = [
    "MT5_PACKAGE_AVAILABLE", "MT5_IMPORT_ERROR", "SESSION_LOCK",
    "package_available", "import_error", "as_dict", "mt5_module",
    "normalize_terminal_path", "discover_terminals",
    "connect", "status", "account_snapshot", "disconnect",
]
