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
        "name": d.get("name"),
        "server": d.get("server"),
        "company": d.get("company"),
        "currency": d.get("currency"),
        "balance": d.get("balance"),
        "equity": d.get("equity"),
        "profit": d.get("profit"),
        "margin": d.get("margin"),
        "margin_free": d.get("margin_free"),
        "margin_level": d.get("margin_level"),
        "leverage": d.get("leverage"),
        "trade_allowed": d.get("trade_allowed"),
        "trade_expert": d.get("trade_expert"),
        "trade_mode": trade_mode,
        "trade_mode_name": trade_mode_name,
        "limit_orders": d.get("limit_orders"),
        # every raw key the terminal reported, unmodified:
        "raw": d,
    }


# Connection lifecycle states (dashboard contract - never blur them):
#   DISCOVERED     terminal exe found on disk (says nothing about running)
#   RUNNING        terminal process exists (says nothing about API/account)
#   INITIALIZING   terminal launched by us; mt5.initialize() not yet answering
#   CONNECTED      mt5.initialize() OK + terminal_info() present, but NO
#                  account logged in (user must log in inside the terminal)
#   ACCOUNT_LOADED connected AND account_info() returned an account
#   FAILED         a step failed; the payload names which and why
STATE_DISCOVERED = "DISCOVERED"
STATE_RUNNING = "RUNNING"
STATE_INITIALIZING = "INITIALIZING"
STATE_CONNECTED = "CONNECTED"
STATE_ACCOUNT_LOADED = "ACCOUNT_LOADED"
STATE_FAILED = "FAILED"
STATE_UNAVAILABLE = "UNAVAILABLE"
STATE_NOT_CONNECTED = "NOT_CONNECTED"


def _os_name() -> str:
    """Platform name seam (tests simulate Windows launch paths on Linux)."""
    return platform.system()


def _terminal_process_running(exe_path: str) -> Optional[bool]:
    """Is a terminal process running? Windows: tasklist image query.

    Returns True/False on Windows, None when the answer is unknowable
    (other platforms, query failure). The image-name query cannot prove
    WHICH install is running when several exist - initialize(path=...) is
    the authority; this only decides whether a launch attempt is needed.
    """
    if _os_name() != "Windows" or not exe_path:
        return None
    import csv
    import io
    import subprocess
    from pathlib import Path

    image = Path(exe_path).name.lower()
    if image not in COMMON_EXE_NAMES:
        image = "terminal64.exe"
    try:
        proc = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {image}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=15)
    except Exception:  # noqa: BLE001
        return None
    if proc.returncode != 0:
        return None
    for row in csv.reader(io.StringIO(proc.stdout or "")):
        if row and row[0].strip().lower() == image:
            return True
    return False


def terminal_running(exe_path: str) -> Optional[bool]:
    return _terminal_process_running(exe_path)


def _popen_detached(exe_path: str) -> Any:
    """Launch the terminal GUI detached (it must outlive this request)."""
    import subprocess
    from pathlib import Path

    kwargs: Dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                              "stderr": subprocess.DEVNULL, "close_fds": True}
    if _os_name() == "Windows":
        kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return subprocess.Popen([exe_path], cwd=str(Path(exe_path).parent), **kwargs)


def launch_terminal(exe_path: str) -> Dict[str, Any]:
    """Launch the terminal executable if it is not already running.

    Discovery is not connection: this only starts the process. The caller
    must still initialize the API and verify terminal/account info.
    """
    from pathlib import Path

    out: Dict[str, Any] = {"launched": False, "already_running": False,
                           "state": STATE_DISCOVERED, "error": None, "path": exe_path}
    if _os_name() != "Windows":
        out["error"] = "terminal launch is Windows-only"
        out["state"] = STATE_FAILED
        return out
    if not exe_path or not Path(exe_path).is_file():
        out["error"] = f"terminal executable not found: {exe_path!r}"
        out["state"] = STATE_FAILED
        return out
    running = _terminal_process_running(exe_path)
    if running:
        out["already_running"] = True
        out["state"] = STATE_RUNNING
        return out
    try:
        _popen_detached(exe_path)
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"launch failed: {type(exc).__name__}: {exc}"
        out["state"] = STATE_FAILED
        return out
    out["launched"] = True
    out["state"] = STATE_INITIALIZING
    return out


def _api_version() -> Optional[List[Any]]:
    if _MT5 is None:
        return None
    try:
        v = _MT5.version()
        return list(v) if isinstance(v, (list, tuple)) else [v]
    except Exception:  # noqa: BLE001
        return None


def connect(terminal_path: Optional[str] = None, *, timeout_seconds: float = 45.0,
            launch: bool = True) -> Dict[str, Any]:
    """Initialize MT5 (optionally with an explicit terminal path). Read-only.

    Lifecycle (the `state` field is the contract; `ok` means ACCOUNT_LOADED):
      package missing -> FAILED/MT5_UNAVAILABLE (names this interpreter + fix)
      exe missing     -> FAILED/MT5_TERMINAL_NOT_FOUND
      not running     -> launch it, then poll initialize() until the deadline
      initialize OK but account_info() is None -> CONNECTED (login_required) -
        the terminal is up; the USER must log in inside the terminal. We say
        so instead of retrying blindly.
      account present -> ACCOUNT_LOADED, ok=True.
    Never places, checks or modifies an order; never touches credentials.
    """
    import time

    with SESSION_LOCK:
        out: Dict[str, Any] = {
            "ok": False, "state": STATE_FAILED, "checked_at": _iso_now(),
            "platform": platform.system(),
            "package_available": MT5_PACKAGE_AVAILABLE, "error_code": None, "error": None,
            "terminal": None, "account": None, "last_error": None, "path_note": None,
            "launched": False, "login_required": False, "api_version": None,
        }
        if _MT5 is None:
            import sys
            out["error_code"] = "MT5_UNAVAILABLE"
            out["error"] = (f"MetaTrader5 package not importable in the backend interpreter "
                            f"{sys.executable}: {MT5_IMPORT_ERROR or 'not installed'}. "
                            f"Fix: \"{sys.executable}\" -m pip install MetaTrader5 "
                            f"(or run scripts/check_mt5_env.py with that interpreter).")
            _SESSION["last_connection_error"] = out["error"]
            return out
        out["api_version"] = _api_version()

        path, note = (normalize_terminal_path(terminal_path) if terminal_path else ("", None))
        out["path_note"] = note
        if terminal_path and note and "does not exist" in note:
            out["error_code"] = "MT5_TERMINAL_NOT_FOUND"
            out["error"] = f"terminal path unusable: {terminal_path!r} ({note})"
            _SESSION["last_connection_error"] = out["error"]
            return out

        def _try_initialize() -> bool:
            try:
                return bool(_MT5.initialize(**({"path": path} if path else {})))
            except Exception as exc:  # noqa: BLE001
                out["error"] = f"initialize raised {type(exc).__name__}: {exc}"
                return False

        ok = _try_initialize()
        if not ok and launch and path:
            lr = launch_terminal(path)
            out["launched"] = bool(lr.get("launched"))
            out["launch"] = lr
            if lr.get("state") != STATE_FAILED:
                deadline = time.monotonic() + max(0.0, timeout_seconds)
                while not ok and time.monotonic() < deadline:
                    time.sleep(1.0)
                    ok = _try_initialize()
            elif lr.get("error"):
                out["error"] = (out["error"] or "") + f" launch: {lr['error']}"

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
            "terminal": _terminal_view(ti, path),
            "account": _account_view(ai) if ai is not None else None,
            "last_error": _last_error(),
        })
        if ai is None:
            out["state"] = STATE_CONNECTED
            out["login_required"] = True
            out["warning"] = ("terminal initialized but account_info() is None - no broker account "
                              "is logged in on that terminal. Log in inside the MT5 terminal "
                              "(File > Login to Trade Account), then press Connect again. "
                              "Hero1 never asks for or stores account passwords.")
        else:
            out["state"] = STATE_ACCOUNT_LOADED
            out["ok"] = True
        if ti is not None and not bool(as_dict(ti).get("connected", True)):
            out["warning"] = ((out.get("warning") or "") +
                              " terminal reports connected=false - check the terminal's "
                              "connection to the broker (bottom-right status in MT5).").strip()
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
        "ok": False, "state": STATE_UNAVAILABLE, "checked_at": _iso_now(),
        "platform": platform.system(),
        "package_available": MT5_PACKAGE_AVAILABLE, "import_error": MT5_IMPORT_ERROR or None,
        "api_version": None,
        "session": dict(_SESSION), "terminal": None, "account": None,
    }
    if _MT5 is None:
        return out
    out["api_version"] = _api_version()
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
    if out["account"] is not None:
        out["state"] = STATE_ACCOUNT_LOADED
    elif out["terminal"] is not None:
        out["state"] = STATE_CONNECTED
    else:
        out["state"] = STATE_NOT_CONNECTED
    out["ok"] = out["account"] is not None
    out["login_required"] = out["terminal"] is not None and out["account"] is None
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


# ------------------------------------------------- automatic connection
def decide_auto_connect(terminals: List[Dict[str, Any]],
                        saved_terminal_path: Optional[str] = None) -> Dict[str, Any]:
    """Decide whether an automatic startup connection is UNAMBIGUOUS.

    Rules (ISSUE-056): never launch a terminal automatically; never choose
    arbitrarily between multiple RUNNING terminals; a previously saved
    (user-chosen) terminal wins when it is the one running. Returns
    {action: 'connect'|'none', path, reason, running: [...]}.
    """
    running = [t for t in terminals if t.get("running")]
    paths = [str(t.get("path")) for t in running]
    if saved_terminal_path:
        sp = str(saved_terminal_path)
        match = next((p for p in paths if p.lower() == sp.lower()), None)
        if match:
            return {"action": "connect", "path": match, "running": paths,
                    "reason": "saved terminal is running"}
        if len(running) == 0:
            return {"action": "none", "path": None, "running": paths,
                    "reason": "saved terminal is not running; not launching it automatically"}
    if len(running) == 1:
        return {"action": "connect", "path": paths[0], "running": paths,
                "reason": "exactly one running terminal"}
    if len(running) == 0:
        return {"action": "none", "path": None, "running": paths,
                "reason": "no MT5 terminal is running"}
    return {"action": "none", "path": None, "running": paths,
            "reason": f"{len(running)} terminals are running - ambiguous, user selects"}

