"""Windows MT5 diagnostic — which exact layer blocks real execution?

`runtime_report.py` answers "can THIS backend trade real MT5, and if not why?"
from inside the running application. This module goes one level deeper, for the
operator's Windows machine, where the real terminal lives:

  * machine facts (Windows build, architecture, user, working directory),
  * every Python on the machine (executable, version, bitness, ``where``,
    virtualenv, pip) — the interpreter the launcher would pick first,
  * the ``MetaTrader5`` package: importable, version, location, and in *which*
    interpreter (probed in a real subprocess, never inferred),
  * terminals found on disk and terminal processes currently running (read-only:
    nothing is started, nothing is killed),
  * ``mt5.initialize()`` + ``mt5.last_error()``,
  * ``terminal_info()`` / ``account_info()`` safe fields (no credentials are ever
    read or printed),
  * ``symbol_info()`` / ``symbol_info_tick()`` for the traded symbol,
  * trading permissions (terminal / account / symbol),
  * whether the project can reach the ``order_check`` stage — the check runs, the
    order is NEVER sent (this module contains no ``order_send`` call at all).

It ends with ONE classified layer, because "MT5 unavailable" is not a diagnosis:

    MT5 READY | MT5 PACKAGE MISSING | MT5 TERMINAL NOT FOUND |
    MT5 TERMINAL NOT RUNNING | MT5 INITIALIZATION FAILED | MT5 ACCOUNT UNAVAILABLE |
    MT5 SYMBOL UNAVAILABLE | MT5 MARKET DATA UNAVAILABLE | MT5 TRADING DISABLED |
    MT5 ORDER VALIDATION FAILED

On a non-Windows host the blocks above it are re-tested but the package cannot
exist there, so the classifier says so explicitly
(``MT5 PLATFORM UNSUPPORTED``) instead of pretending one of the Windows layers
is the cause. Nothing in this module fabricates a value: every field is either
measured or reported as unknown.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# --------------------------------------------------------------------------- #
# the classification vocabulary (spec §5) — exactly these labels, no others
# --------------------------------------------------------------------------- #
MT5_READY = "MT5 READY"
MT5_PACKAGE_MISSING = "MT5 PACKAGE MISSING"
MT5_TERMINAL_NOT_FOUND = "MT5 TERMINAL NOT FOUND"
MT5_TERMINAL_NOT_RUNNING = "MT5 TERMINAL NOT RUNNING"
MT5_INITIALIZATION_FAILED = "MT5 INITIALIZATION FAILED"
MT5_ACCOUNT_UNAVAILABLE = "MT5 ACCOUNT UNAVAILABLE"
MT5_SYMBOL_UNAVAILABLE = "MT5 SYMBOL UNAVAILABLE"
MT5_MARKET_DATA_UNAVAILABLE = "MT5 MARKET DATA UNAVAILABLE"
MT5_TRADING_DISABLED = "MT5 TRADING DISABLED"
MT5_ORDER_VALIDATION_FAILED = "MT5 ORDER VALIDATION FAILED"
MT5_PLATFORM_UNSUPPORTED = "MT5 PLATFORM UNSUPPORTED"   # non-Windows host only

CLASSIFICATION_LABELS = (
    MT5_READY, MT5_PACKAGE_MISSING, MT5_TERMINAL_NOT_FOUND, MT5_TERMINAL_NOT_RUNNING,
    MT5_INITIALIZATION_FAILED, MT5_ACCOUNT_UNAVAILABLE, MT5_SYMBOL_UNAVAILABLE,
    MT5_MARKET_DATA_UNAVAILABLE, MT5_TRADING_DISABLED, MT5_ORDER_VALIDATION_FAILED,
)

# terminal process names the Windows tasklist is filtered on (read-only)
TERMINAL_PROCESS_NAMES = ("terminal64.exe", "terminal.exe", "metatrader64.exe", "metatrader.exe")

# interpreters the launcher looks for, in the launcher's own order (start.bat)
_LAUNCHER_CANDIDATES = (
    ("root/.venv", Path(".venv") / "Scripts" / "python.exe"),
    ("backend/.venv", Path("backend") / ".venv" / "Scripts" / "python.exe"),
    ("root/venv", Path("venv") / "Scripts" / "python.exe"),
    ("root/.venv(posix)", Path(".venv") / "bin" / "python"),
    ("backend/.venv(posix)", Path("backend") / ".venv" / "bin" / "python"),
)

_PACKAGE_PROBE = (
    "import json\n"
    "try:\n"
    "    import MetaTrader5 as m\n"
    "    print(json.dumps({'importable': True, 'version': getattr(m, '__version__', 'unknown'),\n"
    "                      'location': getattr(m, '__file__', '')}))\n"
    "except Exception as e:\n"
    "    print(json.dumps({'importable': False, 'error': f'{type(e).__name__}: {e}'}))\n"
)


def _host_is_windows() -> bool:
    """Single place that asks the host OS — patchable in tests."""
    return sys.platform.startswith("win")


def _run(cmd: List[str], timeout: int = 25) -> Dict[str, Any]:
    """Run a read-only helper command; never raises, never kills anything."""
    exe = shutil.which(cmd[0]) if not Path(cmd[0]).is_absolute() else cmd[0]
    if not exe:
        return {"cmd": cmd, "available": False, "output": "", "error": "not found"}
    try:
        proc = subprocess.run([exe] + cmd[1:], capture_output=True, text=True, timeout=timeout)
    except Exception as e:                       # subprocess failed for OS reasons
        return {"cmd": cmd, "available": True, "output": "", "error": f"{type(e).__name__}: {e}"}
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return {"cmd": cmd, "available": True, "output": out, "returncode": proc.returncode}


# --------------------------------------------------------------------------- #
# machine
# --------------------------------------------------------------------------- #
def collect_machine(root: Optional[Path] = None) -> Dict[str, Any]:
    facts: Dict[str, Any] = {
        "platform": platform.system(),
        "platform_release": platform.release(),
        "platform_version": platform.version(),
        "platform_platform": platform.platform(),
        "architecture": platform.machine(),
        "processor": platform.processor(),
        "python_bits": struct.calcsize("P") * 8,
        "user": os.environ.get("USERNAME") or os.environ.get("USER") or "",
        "cwd": os.getcwd(),
        "repo_root": str(root) if root else "",
    }
    if _host_is_windows():
        # real Windows build numbers, when the host provides them
        try:
            rel, ver, csd, _ = platform.win32_ver()
            facts["windows_release"] = rel
            facts["windows_version"] = ver
            facts["windows_csd"] = csd
        except Exception:
            pass
        facts["os_caption"] = _run(["wmic", "os", "get", "Caption,Version,BuildNumber"])  # may be absent on Win11
        facts["uac_or_elevation"] = _run(["net", "session"])          # harmless permission probe
    return facts


# --------------------------------------------------------------------------- #
# python environments
# --------------------------------------------------------------------------- #
def _probe_interpreter(python_exe: str) -> Dict[str, Any]:
    """Facts + MetaTrader5 import status of one interpreter, in a subprocess."""
    facts: Dict[str, Any] = {"path": python_exe, "exists": bool(python_exe) and Path(python_exe).exists()}
    if not facts["exists"]:
        facts["available"] = False
        return facts
    facts["available"] = True
    version = _run([python_exe, "-c",
                    "import sys,struct,platform;"
                    "print(sys.version.split()[0]);print(struct.calcsize('P')*8);"
                    "print(platform.python_implementation());"
                    "print(sys.executable);"
                    "print(sys.prefix==sys.base_prefix)"] )
    if version.get("available"):
        lines = (version.get("output") or "").splitlines()
        if len(lines) >= 5:
            facts.update({"version": lines[0].strip(), "bits": lines[1].strip(),
                          "implementation": lines[2].strip(), "executable": lines[3].strip(),
                          "is_system_python": lines[4].strip() == "True"})
    facts["package"] = _probe_package(python_exe)
    return facts


def _probe_package(python_exe: str) -> Dict[str, Any]:
    if not python_exe or not Path(python_exe).exists():
        return {"checked": False, "reason": "no interpreter"}
    try:
        proc = subprocess.run([python_exe, "-c", _PACKAGE_PROBE], capture_output=True,
                              text=True, timeout=40)
    except Exception as e:
        return {"checked": False, "reason": f"{type(e).__name__}: {e}"}
    for line in reversed((proc.stdout or "").strip().splitlines()):
        try:
            payload = json.loads(line)
            payload["checked"] = True
            payload["interpreter"] = python_exe
            return payload
        except Exception:
            continue
    return {"checked": True, "importable": False, "interpreter": python_exe,
            "error": (proc.stderr or "").strip()[:300] or "probe produced no result"}


def collect_python_environment(root: Optional[Path] = None) -> Dict[str, Any]:
    root_p = Path(root) if root else Path.cwd()
    running = {
        "executable": sys.executable,
        "version": platform.python_version(),
        "bits": struct.calcsize("P") * 8,
        "implementation": platform.python_implementation(),
        "is_venv": sys.prefix != sys.base_prefix or "venv" in sys.executable.lower(),
        "prefix": sys.prefix,
        "base_prefix": sys.base_prefix,
    }
    venv_env = os.environ.get("VIRTUAL_ENV", "")
    launcher: Dict[str, Any] = {"found": False}
    for label, rel in _LAUNCHER_CANDIDATES:
        candidate = root_p / rel
        if candidate.exists():
            launcher = {"found": True, "label": label, "path": str(candidate)}
            break
    where = {
        "python": _run(["where", "python"]) if _host_is_windows() else {"available": False, "output": ""},
        "py": _run(["where", "py"]) if _host_is_windows() else {"available": False, "output": ""},
        "pip": _run(["where", "pip"]) if _host_is_windows() else {"available": False, "output": ""},
    }
    pip_info = _run([sys.executable, "-m", "pip", "--version"])
    others: List[Dict[str, Any]] = []
    probed = set()
    for cand in ([launcher.get("path")] if launcher.get("found") else []) + \
            [ln.strip() for ln in (where["python"].get("output") or "").splitlines() if ln.strip()] + \
            [ln.strip() for ln in (where["py"].get("output") or "").splitlines() if ln.strip()]:
        if not cand or cand in probed:
            continue
        probed.add(cand)
        others.append(_probe_interpreter(cand))
        if len(others) >= 4:
            break
    return {
        "running_backend_python": running,
        "launcher_python": launcher,
        "virtual_env": venv_env,
        "where": where,
        "pip": {"executable": f"{sys.executable} -m pip",
                "version": (pip_info.get("output") or "").splitlines()[0] if pip_info.get("output") else "",
                "available": pip_info.get("available", False)},
        "other_interpreters": others,
    }


# --------------------------------------------------------------------------- #
# terminals (discovery + running processes) — read-only
# --------------------------------------------------------------------------- #
def collect_terminals() -> Dict[str, Any]:
    out: Dict[str, Any] = {"discovered": [], "discovered_count": 0, "running": [], "running_count": 0}
    try:
        from .discovery import discover_terminals
        terms = discover_terminals() or []
        out["discovered"] = [{"name": t.get("name"), "path": t.get("path"),
                              "filename": t.get("filename"), "folder": t.get("folder")} for t in terms]
        out["discovered_count"] = len(terms)
    except Exception as e:
        out["discovery_error"] = f"{type(e).__name__}: {e}"
    try:
        from .config import get_saved_terminal_path, load_mt5_config
        cfg = load_mt5_config() or {}
        out["saved_path"] = get_saved_terminal_path() or cfg.get("terminal_path") or ""
        out["last_connected_company"] = cfg.get("last_connected_company") or ""
        out["last_connected_build"] = cfg.get("last_connected_build") or ""
    except Exception as e:
        out["config_error"] = f"{type(e).__name__}: {e}"
    # running processes: filtered tasklist, never killed
    if _host_is_windows():
        proc = _run(["tasklist", "/FO", "CSV", "/NH"])
        rows: List[Dict[str, str]] = []
        for line in (proc.get("output") or "").splitlines():
            parts = [p.strip('"') for p in line.split('","')]
            if not parts:
                continue
            name = parts[0].strip('"').lower()
            if name in TERMINAL_PROCESS_NAMES:
                rows.append({"name": parts[0].strip('"'),
                             "pid": parts[1].strip('"') if len(parts) > 1 else "",
                             "memory": parts[-1].strip('"') if len(parts) > 2 else ""})
        out["running"] = rows
        out["running_count"] = len(rows)
        out["tasklist_available"] = bool(proc.get("available"))
    return out


# --------------------------------------------------------------------------- #
# initialize / terminal / account / symbol / permissions / order_check
# --------------------------------------------------------------------------- #
def _initialize_candidates(root: Optional[Path], term: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The same order the production bridge uses (mt5_real.MT5RealBridge.connect)."""
    cands: List[Dict[str, Any]] = []
    if term.get("saved_path"):
        cands.append({"how": "saved terminal path", "path": term["saved_path"]})
    cands.append({"how": "automatic discovery", "path": ""})
    for t in term.get("discovered") or []:
        cands.append({"how": "discovered terminal", "path": t.get("path") or ""})
    seen = set()
    uniq = []
    for c in cands:
        key = (c["how"], c["path"])
        if key in seen or not (c["path"] or c["how"] == "automatic discovery"):
            continue
        seen.add(key)
        uniq.append(c)
    return uniq


def _mt5_module():
    """The MetaTrader5 module if it imports here, else None (never raises)."""
    try:
        import MetaTrader5 as mt5  # type: ignore
        return mt5
    except Exception:
        return None


def probe_terminal(symbol: str = "XAUUSD", root: Optional[Path] = None,
                   mt5_mod: Any = None, term: Optional[Dict[str, Any]] = None,
                   pip_stop_pips: int = 300) -> Dict[str, Any]:
    """Deep, read-only probe. NEVER sends an order (this module has no order_send)."""
    mt5 = mt5_mod if mt5_mod is not None else _mt5_module()
    term = term if term is not None else collect_terminals()
    out: Dict[str, Any] = {
        "attempted": False, "initialized": False, "initialize_used": None,
        "attempts": [], "last_error": None, "terminal_info": None,
        "account_info": None, "symbol": {}, "tick": {}, "permissions": {},
        "order_check": {"attempted": False, "sent": False},
        "shutdown_called": False,
    }
    if mt5 is None:
        out["error"] = "MetaTrader5 package is not importable in this interpreter"
        return out

    out["attempted"] = True
    kwargs: Dict[str, Any] = {"timeout": 60000}
    for cand in _initialize_candidates(root, term):
        attempt: Dict[str, Any] = {"how": cand["how"], "path": cand["path"] or None}
        try:
            ok = bool(mt5.initialize(path=str(cand["path"]), **kwargs)) if cand["path"] \
                else bool(mt5.initialize(**kwargs))
        except Exception as e:
            attempt.update({"ok": False, "exception": f"{type(e).__name__}: {e}"})
            try:
                attempt["last_error"] = list(mt5.last_error() or [])
            except Exception:
                pass
            out["attempts"].append(attempt)
            continue
        attempt["ok"] = ok
        try:
            attempt["last_error"] = list(mt5.last_error() or [])
        except Exception:
            pass
        out["attempts"].append(attempt)
        if ok:
            out["initialized"] = True
            out["initialize_used"] = {"how": cand["how"], "path": cand["path"] or None}
            break

    if not out["initialized"]:
        try:
            out["last_error"] = list(mt5.last_error() or [])
        except Exception:
            pass
        return out

    # ---- terminal_info -----------------------------------------------------
    try:
        ti = mt5.terminal_info()
    except Exception as e:
        ti = None
        out["terminal_info_error"] = f"{type(e).__name__}: {e}"
    if ti is not None:
        out["terminal_info"] = {
            "name": getattr(ti, "name", None), "company": getattr(ti, "company", None),
            "path": getattr(ti, "path", None), "data_path": getattr(ti, "data_path", None),
            "build": getattr(ti, "build", None), "connected": getattr(ti, "connected", None),
            "trade_allowed": getattr(ti, "trade_allowed", None),
            "tradeapi_disabled": getattr(ti, "tradeapi_disabled", None),
            "dlls_allowed": getattr(ti, "dlls_allowed", None),
            "community_account": getattr(ti, "community_account", None),
            "maxbars": getattr(ti, "maxbars", None),
            "language": getattr(ti, "language", None),
        }

    # ---- account_info (safe fields only — never passwords) -----------------
    try:
        ai = mt5.account_info()
    except Exception as e:
        ai = None
        out["account_info_error"] = f"{type(e).__name__}: {e}"
    if ai is not None:
        account = {
            "login": getattr(ai, "login", None),
            "trade_mode": getattr(ai, "trade_mode", None),
            "trade_mode_name": {0: "DEMO", 1: "CONTEST", 2: "REAL"}.get(getattr(ai, "trade_mode", None), "unknown"),
            "server": getattr(ai, "server", None),
            "currency": getattr(ai, "currency", None),
            "balance": getattr(ai, "balance", None),
            "equity": getattr(ai, "equity", None),
            "margin_free": getattr(ai, "margin_free", None),
            "leverage": getattr(ai, "leverage", None),
            "trade_allowed": getattr(ai, "trade_allowed", None),
            "trade_expert": getattr(ai, "trade_expert", None),
            "margin_mode": getattr(ai, "margin_mode", None),
        }
        name = getattr(ai, "name", "") or ""
        account["name_masked"] = (name[:1] + "***") if name else ""   # never print the holder name
        account["company"] = getattr(ai, "company", None)
        out["account_info"] = account

    # ---- symbol -------------------------------------------------------------
    sym: Dict[str, Any] = {"requested": symbol, "found": False}
    try:
        si = mt5.symbol_info(symbol)
    except Exception as e:
        si = None
        sym["error"] = f"{type(e).__name__}: {e}"
    if si is None:
        # a broker may keep the symbol out of Market Watch: select it and retry
        try:
            sym["selected_now"] = bool(mt5.symbol_select(symbol, True))
            si = mt5.symbol_info(symbol)
        except Exception as e:
            sym["select_error"] = f"{type(e).__name__}: {e}"
    if si is not None:
        sym.update({
            "found": True,
            "visible": getattr(si, "visible", None),
            "digits": getattr(si, "digits", None),
            "point": getattr(si, "point", None),
            "trade_tick_size": getattr(si, "trade_tick_size", None),
            "trade_tick_value": getattr(si, "trade_tick_value", None),
            "trade_contract_size": getattr(si, "trade_contract_size", None),
            "volume_min": getattr(si, "volume_min", None),
            "volume_max": getattr(si, "volume_max", None),
            "volume_step": getattr(si, "volume_step", None),
            "trade_stops_level": getattr(si, "trade_stops_level", None),
            "freeze_level": getattr(si, "freeze_level", None),
            "trade_mode": getattr(si, "trade_mode", None),
            "trade_mode_name": {0: "DISABLED", 1: "LONG_ONLY", 2: "SHORT_ONLY",
                                3: "CLOSE_ONLY", 4: "FULL"}.get(getattr(si, "trade_mode", None), "unknown"),
            "trade_allowed": getattr(si, "trade_allowed", None),
            "filling_modes": _filling_modes_of(si),
            "filling_plan": _filling_plan(si, mt5),
            "currency_profit": getattr(si, "currency_profit", None),
        })
    out["symbol"] = sym

    # ---- tick ---------------------------------------------------------------
    tick: Dict[str, Any] = {"available": False}
    try:
        tk = mt5.symbol_info_tick(symbol)
    except Exception as e:
        tk = None
        tick["error"] = f"{type(e).__name__}: {e}"
    if tk is not None:
        bid = getattr(tk, "bid", None)
        ask = getattr(tk, "ask", None)
        tick.update({"available": bool(bid and ask), "bid": bid, "ask": ask,
                     "last": getattr(tk, "last", None), "spread": getattr(tk, "spread", None),
                     "time": getattr(tk, "time", None), "volume": getattr(tk, "volume", None)})
    out["tick"] = tick

    # ---- permissions --------------------------------------------------------
    ti_ok = (out["terminal_info"] or {}).get("trade_allowed")
    acct_ok = (out["account_info"] or {}).get("trade_allowed")
    expert_ok = (out["account_info"] or {}).get("trade_expert")
    sym_ok = sym.get("trade_allowed")
    sym_mode = sym.get("trade_mode")
    out["permissions"] = {
        "terminal_trade_allowed": ti_ok,
        "terminal_tradeapi_disabled": (out["terminal_info"] or {}).get("tradeapi_disabled"),
        "account_trade_allowed": acct_ok,
        "account_trade_expert": expert_ok,
        "symbol_trade_allowed": sym_ok,
        "symbol_trade_mode": sym_mode,
        "symbol_trade_mode_name": sym.get("trade_mode_name"),
        # the symbol must be at least full trading mode (4 = SYMBOL_TRADE_MODE_FULL)
        "ok": bool(ti_ok is not False and acct_ok is not False and expert_ok is not False
                   and (sym_mode is None or sym_mode == 4) and sym_ok is not False),
    }

    # ---- order_check capability (never order_send) --------------------------
    out["order_check"] = _probe_order_check(mt5, symbol, sym, tick, pip_stop_pips=pip_stop_pips)

    # release the IPC connection (the terminal keeps running — shutdown only
    # closes this process's link, it does not stop the terminal)
    try:
        mt5.shutdown()
        out["shutdown_called"] = True
    except Exception:
        pass
    return out


def _filling_modes_of(si: Any) -> List[str]:
    """``type_filling`` values this symbol accepts, from its own metadata.

    V5.2 §1/§20 — ``symbol_info().filling_mode`` is the SYMBOL_FILLING_MODE
    *bitmask* (SYMBOL_FILLING_FOK=1, SYMBOL_FILLING_IOC=2), which does not share
    values with ENUM_ORDER_TYPE_FILLING (ORDER_FILLING_FOK=0, IOC=1, RETURN=2).
    A bitmask of 0 means "no FOK/IOC restriction": market orders then use the
    Return fill policy. Decoding the bit with the ORDER_FILLING values is exactly
    how this tool previously reported IOC for a symbol that only accepts RETURN.
    """
    try:
        from .order_semantics import filling_candidates
        return [c["name"] for c in filling_candidates(si)]
    except Exception:
        return []


def _filling_plan(si: Any, mt5: Any = None) -> Dict[str, Any]:
    """What the tool knows about the symbol's filling before any order is checked."""
    try:
        from .order_semantics import (decoded_filling_modes, filling_name,
                                      symbol_filling_bitmask)
    except Exception:                                   # pragma: no cover
        return {}
    bitmask = symbol_filling_bitmask(si)
    cands = _filling_modes_of(si)
    return {
        "symbol_filling_mode_bitmask": bitmask,
        "symbol_filling_mode_note": ("0 — no SYMBOL_FILLING_FOK/IOC bit, so market orders use "
                                     "the Return fill policy"
                                     if bitmask == 0 else
                                     f"bitmask {bitmask} (SYMBOL_FILLING_FOK=1, SYMBOL_FILLING_IOC=2)"),
        "decoded_type_filling_values": decoded_filling_modes(si),
        "type_filling_to_use": cands[0] if cands else None,
        "type_filling_to_use_name": (filling_name(decoded_filling_modes(si)[0])
                                     if decoded_filling_modes(si) else None),
        "candidate_names": cands,
    }


def _probe_order_check(mt5: Any, symbol: str, sym: Dict[str, Any], tick: Dict[str, Any],
                       pip_stop_pips: int = 300) -> Dict[str, Any]:
    """Build the project's own market-order request and run mt5.order_check on it.

    The request is built by ``app.mt5.execution.build_market_order_request`` — the
    identical function the manual panel uses — so the diagnostic proves the real
    code path can reach validation. ``order_send`` is never called.
    """
    result: Dict[str, Any] = {"attempted": False, "sent": False,
                              "note": "order_check only — order_send is never called"}
    check = getattr(mt5, "order_check", None)
    if check is None:
        result.update({"unsupported": True, "error": "this MetaTrader5 build exposes no order_check"})
        return result
    if not sym.get("found") or not tick.get("available"):
        result.update({"skipped": True, "error": "symbol or tick unavailable — nothing to validate"})
        return result
    try:
        from .execution import build_market_order_request, pick_filling
    except Exception as e:
        result.update({"error": f"project order builder unavailable: {type(e).__name__}: {e}"})
        return result

    class _Spec:                                    # the shape pick_filling() expects
        filling_modes = [int(x) for x in
                         [getattr(mt5, k, None) for k in
                          ("ORDER_FILLING_IOC", "ORDER_FILLING_FOK", "ORDER_FILLING_RETURN")]
                         if x is not None] or [1]

    # V5.2 §1 — the filling mode comes from the symbol's own metadata, never from
    # a hard-coded constant: symbol_info() in the probe carries the real SYMBOL_*
    # bitmask, so decode it here instead of guessing IOC.
    try:
        from .order_semantics import filling_candidates as _cands, filling_name as _fname
        _si_live = None
        try:
            _si_live = mt5.symbol_info(symbol)
        except Exception:
            _si_live = None
        if _si_live is not None:
            _plan = [c["value"] for c in _cands(_si_live)]
            if _plan:
                _Spec.filling_modes = _plan
                result["filling_source"] = ("symbol_info().filling_mode (SYMBOL_FILLING_MODE "
                                            "bitmask) — decoded, not hard-coded")
                result["filling_candidates"] = [_fname(v) for v in _plan]
    except Exception as e:                              # pragma: no cover - defensive
        result["filling_source_error"] = f"{type(e).__name__}: {e}"

    filling = pick_filling(_Spec())
    volume = float(sym.get("volume_min") or 0.01)
    price = float(tick.get("ask") or 0.0)
    point = float(sym.get("point") or 0.0)
    digits = int(sym.get("digits") or 2)
    # V6.4 — the one digits rule (MT5_BRIDGE_DETAILS.txt): a pip is 10 points on
    # 3/5-digit symbols, 1 point on 2/4-digit symbols. Never "point x 10".
    from ..backtest.symbol_specs import pip_size_from_digits
    pip_size = pip_size_from_digits(digits, point) or 0.0
    sl = round(price - pip_stop_pips * pip_size, digits) if pip_size else None
    try:
        request = build_market_order_request(
            {"symbol": symbol, "side": "buy", "volume": volume, "price": price,
             "sl": sl, "tp": None, "magic": 777000,
             "comment": "evolab-mt5-diagnostic-check"},
            filling=filling, deviation=20)
    except Exception as e:
        result.update({"error": f"build_market_order_request failed: {type(e).__name__}: {e}"})
        return result
    result.update({"attempted": True, "request": request,
                   "volume": volume, "price": price, "sl": sl,
                   "stop_pips": pip_stop_pips,
                   "note": "mt5.order_check on the project's own request — NOT sent"})
    try:
        checked = check(dict(request))
    except Exception as e:
        result.update({"ok": False, "exception": f"{type(e).__name__}: {e}"})
        return result
    if checked is None:
        try:
            err = mt5.last_error()
        except Exception:
            err = None
        result.update({"ok": False, "retcode": None, "last_error": list(err or [])})
        return result
    raw = checked._asdict() if hasattr(checked, "_asdict") else {
        k: getattr(checked, k, None)
        for k in ("retcode", "balance", "equity", "profit", "margin", "margin_free",
                  "margin_level", "comment", "request")
    }
    raw.pop("request", None)                        # keep the report readable
    # V5.2 §3 — MQL5 semantics: a passing MqlTradeCheckResult reports retcode 0
    # (or 10009/10008) with comment 'Done' and a computed margin. Interpreting it
    # as "retcode != 10009 -> refused" is what made this tool report
    # MT5 ORDER VALIDATION FAILED for a perfectly valid request.
    from .order_semantics import interpret_check_result
    verdict = interpret_check_result(checked, mt5)
    result.update({"ok": bool(verdict["ok"]), "retcode": verdict["retcode"],
                   "retcode_name": verdict["retcode_name"],
                   "comment": verdict["comment"], "margin": verdict["margin"],
                   "margin_free": verdict.get("margin_free"),
                   "rule": verdict["rule"], "interpretation": verdict["rule"],
                   "raw": {k: v for k, v in raw.items() if k != "comment"}})

    # when the request's own filling mode is refused, find the one the terminal
    # accepts — read-only, through the terminal's own order_check (never order_send)
    if not verdict["ok"] and int(verdict["retcode"] or 0) == 10030:
        try:
            from .order_semantics import resolve_filling
            sinfo_live = mt5.symbol_info(symbol)
            if sinfo_live is not None:
                resolved = resolve_filling(sinfo_live, mt5, request)
                result["filling_resolution"] = {
                    "ok": resolved["ok"], "filling": resolved["filling"],
                    "name": resolved["name"], "resolution": resolved["resolution"],
                    "probes": resolved["probes"]}
                if resolved["ok"]:
                    request = dict(request)
                    request["type_filling"] = int(resolved["filling"])
                    result["request"] = request
                    again = check(dict(request))
                    v2 = interpret_check_result(again, mt5)
                    result.update({"ok": bool(v2["ok"]), "retcode": v2["retcode"],
                                   "retcode_name": v2["retcode_name"],
                                   "comment": v2["comment"], "margin": v2["margin"],
                                   "rule": (v2["rule"] + " (after switching type_filling to "
                                            f"{resolved['name']})"),
                                   "interpretation": v2["rule"],
                                   "rechecked_with_filling": resolved["name"]})
        except Exception as e:                          # pragma: no cover - defensive
            result["filling_resolution_error"] = f"{type(e).__name__}: {e}"
    return result


# --------------------------------------------------------------------------- #
# classification (spec §5) — one label, never "unavailable"
# --------------------------------------------------------------------------- #
def classify_layers(facts: Dict[str, Any]) -> Dict[str, str]:
    """Map measured layer facts to exactly one classification label + reason.

    `facts` keys (all optional; missing = unknown, handled conservatively):
        platform_supported, python_found, wheel_supported, package_importable,
        package_importable_somewhere, terminal_process_running, terminals_found,
        initialized, initialize_error, account_available, symbol_available,
        market_data_available, trading_permissions_ok, order_check_attempted,
        order_check_ok, order_check_retcode
    """
    f = facts or {}

    def out(label: str, reason: str) -> Dict[str, str]:
        return {"environment": label, "reason": reason}

    if f.get("platform_supported") is False:
        return out(MT5_PLATFORM_UNSUPPORTED,
                   "this host is not Windows — the MetaTrader5 package and its terminal only exist "
                   "on Windows; run CHECK_MT5_WINDOWS.bat on the trading machine")
    if f.get("python_found") is False:
        return out(MT5_PACKAGE_MISSING, "no Python interpreter was found — the package cannot be imported")
    if f.get("wheel_supported") is False and f.get("package_importable") is not True:
        return out(MT5_PACKAGE_MISSING,
                   "the installed Python is outside the published MetaTrader5 wheel range "
                   "(CPython 3.6-3.14, 64-bit Windows) — pip install cannot succeed for it")
    if f.get("package_importable") is not True:
        if f.get("package_importable_somewhere"):
            return out(MT5_PACKAGE_MISSING,
                       f"the package is importable in another interpreter "
                       f"({f.get('package_importable_somewhere')}) but not in the one that runs the "
                       f"lab — install it into the launcher's environment")
        return out(MT5_PACKAGE_MISSING, f.get("package_error") or "MetaTrader5 is not importable")
    if f.get("terminals_found") in (0, None) and not f.get("initialized"):
        if f.get("terminal_process_running"):
            return out(MT5_TERMINAL_NOT_FOUND,
                       "a terminal process is running but no terminal executable was found on disk")
        return out(MT5_TERMINAL_NOT_FOUND,
                   "no MetaTrader 5 terminal was found in the usual install locations or the registry")
    if not f.get("initialized"):
        if f.get("terminal_process_running") is False:
            return out(MT5_TERMINAL_NOT_RUNNING,
                       "mt5.initialize() failed and no terminal process is running — start the terminal "
                       "(or log in once) and run this check again")
        return out(MT5_INITIALIZATION_FAILED,
                   f"mt5.initialize() failed: {f.get('initialize_error') or 'no detail'}")
    # stages below are only *failed* on a measured False: a fact this caller did
    # not measure (None) must not be turned into a failure it did not observe.
    if f.get("account_available") is False:
        return out(MT5_ACCOUNT_UNAVAILABLE,
                   "the terminal is initialized but mt5.account_info() returned nothing — log in to the "
                   "demo account inside the terminal")
    if f.get("symbol_available") is False:
        return out(MT5_SYMBOL_UNAVAILABLE,
                   "the requested symbol does not exist for this account/broker")
    if f.get("market_data_available") is False:
        return out(MT5_MARKET_DATA_UNAVAILABLE,
                   "the symbol exists but has no tick (bid/ask) — the market may be closed or the symbol "
                   "is not in Market Watch")
    if f.get("trading_permissions_ok") is False:
        return out(MT5_TRADING_DISABLED,
                   "the terminal, the account or the symbol does not allow trading "
                   "(check 'Algo Trading' in the terminal and the account's trading permissions)")
    if f.get("order_check_attempted") and f.get("order_check_ok") is False:
        return out(MT5_ORDER_VALIDATION_FAILED,
                   f"mt5.order_check refused the project's own request "
                   f"({f.get('order_check_retcode_name') or 'retcode ' + str(f.get('order_check_retcode'))}"
                   f", comment {f.get('order_check_comment')!r}) — nothing was sent. "
                   f"Rule applied: {f.get('order_check_rule') or 'n/a'}")
    return out(MT5_READY,
               "the package imports, the terminal is connected, the account and symbol are available, "
               "trading is permitted and mt5.order_check accepts the project's own order request")


def _facts_from_probe(machine_ok: bool, py: Dict[str, Any], probe: Dict[str, Any],
                      term: Dict[str, Any], wheel_supported: Optional[bool]) -> Dict[str, Any]:
    running_py = py.get("running_backend_python", {})
    launcher = py.get("launcher_python", {})
    others = py.get("other_interpreters") or []
    pkg_here = probe.get("_package_importable")
    elsewhere = ""
    for o in others:
        if (o.get("package") or {}).get("importable"):
            elsewhere = o.get("path") or ""
            break
    python_found = bool(running_py.get("executable")) or bool(launcher.get("found")) or bool(others)
    oc = probe.get("order_check") or {}
    return {
        "platform_supported": machine_ok,
        "python_found": python_found,
        "wheel_supported": wheel_supported,
        "package_importable": bool(pkg_here),
        "package_importable_somewhere": elsewhere,
        "package_error": probe.get("error") or "",
        "terminals_found": term.get("discovered_count", 0),
        "terminal_process_running": (term.get("running_count", 0) > 0) if machine_ok else None,
        "initialized": bool(probe.get("initialized")),
        "initialize_error": _initialize_error_text(probe),
        "account_available": bool(probe.get("account_info")),
        "symbol_available": bool((probe.get("symbol") or {}).get("found")),
        "market_data_available": bool((probe.get("tick") or {}).get("available")),
        "trading_permissions_ok": (probe.get("permissions") or {}).get("ok"),
        "order_check_attempted": bool(oc.get("attempted")),
        "order_check_ok": oc.get("ok"),
        "order_check_retcode": oc.get("retcode"),
        "order_check_retcode_name": oc.get("retcode_name"),
        "order_check_comment": oc.get("comment"),
        "order_check_rule": oc.get("rule"),
    }


def _initialize_error_text(probe: Dict[str, Any]) -> str:
    err = probe.get("last_error")
    if err:
        return f"mt5.last_error()={err}"
    for attempt in probe.get("attempts") or []:
        if attempt.get("exception"):
            return attempt["exception"]
    return probe.get("error") or ""


def windows_mt5_diagnostic(root: Optional[Path] = None, symbol: str = "XAUUSD",
                           mt5_mod: Any = None, terminal: Optional[Dict[str, Any]] = None,
                           machine: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Full diagnosis. Safe on any OS; deep-probes only when MT5 is importable here."""
    root_p = Path(root) if root else Path.cwd()
    machine_facts = machine if machine is not None else collect_machine(root_p)
    machine_ok = _host_is_windows() if machine is None else bool(machine_facts.get("_is_windows", _host_is_windows()))
    py = collect_python_environment(root_p)
    wheel_supported = _wheel_supported(py.get("running_backend_python", {}).get("version", ""))
    term = terminal if terminal is not None else collect_terminals()
    probe = probe_terminal(symbol=symbol, root=root_p, mt5_mod=mt5_mod, term=term)
    probe["_package_importable"] = (mt5_mod is not None) or _mt5_module() is not None
    facts = _facts_from_probe(machine_ok, py, probe, term, wheel_supported)
    verdict = classify_layers(facts)
    report = {
        "tool": "CHECK_MT5_WINDOWS",
        "safe": {"places_orders": False, "kills_processes": False,
                 "note": "diagnostic only: reads facts, runs mt5.order_check, never order_send"},
        "machine": machine_facts,
        "python": py,
        "terminal": term,
        "mt5": {k: v for k, v in probe.items() if not k.startswith("_")},
        "layer_facts": facts,
        "classification": verdict,
        "environment": verdict["environment"],
        "reason": verdict["reason"],
    }
    return report


def _wheel_supported(version: str) -> Optional[bool]:
    try:
        parts = str(version).split(".")
        major, minor = int(parts[0]), int(parts[1])
    except Exception:
        return None
    from .runtime_report import MT5_WHEEL_MAX, MT5_WHEEL_MIN
    return MT5_WHEEL_MIN <= (major, minor) <= MT5_WHEEL_MAX


# --------------------------------------------------------------------------- #
# human-readable rendering (what the operator pastes back)
# --------------------------------------------------------------------------- #
def format_diagnostic(rep: Dict[str, Any]) -> str:
    L: List[str] = []
    add = L.append
    add("=" * 78)
    add("MT5 WINDOWS DIAGNOSTIC — read-only, no order is ever placed")
    add("=" * 78)
    cls = rep.get("classification", {})
    add(f"CLASSIFICATION: {cls.get('environment')}")
    add(f"REASON        : {cls.get('reason')}")
    add("")

    m = rep.get("machine", {})
    add("## MACHINE")
    add(f"  windows/platform : {m.get('platform')} {m.get('windows_release', '')} "
        f"{m.get('windows_version', '')} ({(m.get('platform_version') or '')[:60]})")
    add(f"  architecture     : {m.get('architecture')} ({m.get('python_bits')}-bit python)")
    add(f"  user             : {m.get('user')}")
    add(f"  working dir      : {m.get('cwd')}")
    if m.get("os_caption", {}).get("output"):
        add(f"  os caption       : {m['os_caption']['output'].replace(chr(10), ' | ')}")
    add("")

    py = rep.get("python", {})
    rp = py.get("running_backend_python", {})
    add("## PYTHON")
    add(f"  running this tool: {rp.get('executable')}")
    add(f"                     version {rp.get('version')} ({rp.get('implementation')}, "
        f"{rp.get('bits')}-bit), venv={rp.get('is_venv')}")
    lp = py.get("launcher_python", {})
    add(f"  launcher python  : {lp.get('path') if lp.get('found') else '(no .venv in this checkout — PATH fallback)'}"
        f"{(' [' + str(lp.get('label')) + ']') if lp.get('found') else ''}")
    add(f"  VIRTUAL_ENV      : {py.get('virtual_env') or '(not set)'}")
    for exe_key, label in (("python", "where python"), ("py", "where py"), ("pip", "where pip")):
        info = (py.get("where") or {}).get(exe_key) or {}
        if info.get("available"):
            add(f"  {label:<17}: {info.get('output', '').replace(chr(10), ' | ') or '(nothing)'}")
    add(f"  pip              : {py.get('pip', {}).get('version') or '(not available)'}")
    for o in py.get("other_interpreters") or []:
        pkg = o.get("package") or {}
        add(f"    - {o.get('path')} :: python {o.get('version')} ({o.get('bits')}-bit)"
            f" :: MetaTrader5 {'importable ' + str(pkg.get('version')) if pkg.get('importable') else 'NOT importable'}")
        if pkg.get("location"):
            add(f"        package location: {pkg.get('location')}")
    add("")

    t = rep.get("terminal", {})
    add("## MT5 TERMINALS")
    add(f"  found on disk    : {t.get('discovered_count', 0)}")
    for term in (t.get("discovered") or [])[:10]:
        add(f"    - {term.get('name')} :: {term.get('path')}")
    if t.get("saved_path"):
        add(f"  saved path (DATA config): {t.get('saved_path')}")
    if t.get("last_connected_company"):
        add(f"  last connected   : {t.get('last_connected_company')} build {t.get('last_connected_build')}")
    if "running_count" in t:
        add(f"  running now      : {t.get('running_count')} "
            f"({', '.join(p.get('name', '') + ' pid ' + p.get('pid', '') for p in (t.get('running') or [])) or 'none'})")
    add("")

    mt5 = rep.get("mt5", {})
    add("## INITIALIZE / TERMINAL / ACCOUNT")
    if not mt5.get("attempted"):
        add(f"  mt5.initialize() : not attempted — {mt5.get('error') or 'the package is not importable here'}")
    else:
        add(f"  mt5.initialize() : {'OK' if mt5.get('initialized') else 'FAILED'}"
            f"{(' via ' + str((mt5.get('initialize_used') or {}).get('how'))) if mt5.get('initialized') else ''}")
    for a in mt5.get("attempts") or []:
        add(f"    - {a.get('how')}: {'ok' if a.get('ok') else 'failed'}"
            f"{(' :: ' + str(a.get('exception'))) if a.get('exception') else ''}"
            f"{(' :: last_error=' + str(a.get('last_error'))) if a.get('last_error') and not a.get('ok') else ''}")
    if mt5.get("last_error"):
        add(f"  mt5.last_error() : {mt5.get('last_error')}")
    if mt5.get("terminal_info"):
        ti = mt5["terminal_info"]
        add(f"  terminal_info    : {ti.get('name')} | {ti.get('company')} | build {ti.get('build')} "
            f"| connected={ti.get('connected')} | trade_allowed={ti.get('trade_allowed')} "
            f"| tradeapi_disabled={ti.get('tradeapi_disabled')} | dlls_allowed={ti.get('dlls_allowed')}")
        add(f"                     path: {ti.get('path')}")
    if mt5.get("account_info"):
        ai = mt5["account_info"]
        add(f"  account_info     : login {ai.get('login')} | {ai.get('trade_mode_name')} | {ai.get('server')} "
            f"| {ai.get('currency')} | balance {ai.get('balance')} | equity {ai.get('equity')} "
            f"| leverage 1:{ai.get('leverage')}")
        add(f"                     trade_allowed={ai.get('trade_allowed')} trade_expert={ai.get('trade_expert')} "
            f"name={ai.get('name_masked')}")
    add("")

    sym = mt5.get("symbol") or {}
    add(f"## SYMBOL {sym.get('requested', 'XAUUSD')}")
    if sym.get("found"):
        add(f"  found={sym.get('found')} visible={sym.get('visible')} "
            f"digits={sym.get('digits')} point={sym.get('point')} "
            f"tick_size={sym.get('trade_tick_size')} tick_value={sym.get('trade_tick_value')}")
        add(f"  volume           : min {sym.get('volume_min')} / max {sym.get('volume_max')} / step {sym.get('volume_step')}")
        add(f"  stops/freeze     : stops_level={sym.get('trade_stops_level')} freeze_level={sym.get('freeze_level')}")
        add(f"  trade_mode       : {sym.get('trade_mode_name')} ({sym.get('trade_mode')}) "
            f"| trade_allowed={sym.get('trade_allowed')}")
        add(f"  filling modes    : {', '.join(sym.get('filling_modes') or []) or '(unknown)'}")
        plan = sym.get("filling_plan") or {}
        if plan:
            add(f"  filling bitmask  : {plan.get('symbol_filling_mode_bitmask')} "
                f"({plan.get('symbol_filling_mode_note')})")
            add(f"  type_filling to use: {plan.get('type_filling_to_use_name') or '(unknown)'} "
                f"— derived from the symbol's own metadata, not hard-coded")
    else:
        add(f"  NOT AVAILABLE — {sym.get('error') or 'symbol_info returned nothing'}")
    tk = mt5.get("tick") or {}
    if tk:
        add(f"  tick             : available={tk.get('available')} bid={tk.get('bid')} ask={tk.get('ask')} "
            f"spread={tk.get('spread')} time={tk.get('time')}")
    add("")

    perm = mt5.get("permissions") or {}
    add("## TRADING PERMISSIONS")
    add(f"  terminal trade_allowed : {perm.get('terminal_trade_allowed')} "
        f"(tradeapi_disabled={perm.get('terminal_tradeapi_disabled')})")
    add(f"  account  trade_allowed : {perm.get('account_trade_allowed')} "
        f"(trade_expert={perm.get('account_trade_expert')})")
    add(f"  symbol   trade_allowed : {perm.get('symbol_trade_allowed')} "
        f"({perm.get('symbol_trade_mode_name')})")
    add(f"  all permissions OK     : {perm.get('ok')}")
    add("")

    oc = mt5.get("order_check") or {}
    add("## ORDER CHECK (order_send is NEVER called)")
    if oc.get("attempted"):
        add(f"  request          : {oc.get('request')}")
        add(f"  retcode          : {oc.get('retcode')} comment={oc.get('comment')!r} "
            f"margin={oc.get('margin')}")
        # V5.2 §11 — MQL5 semantics: a PASSING MqlTradeCheckResult reports retcode 0
        # (or an explicit done/placed code) with comment 'Done' and a computed
        # margin. "retcode != 10009 -> refused" was wrong and mislabelled a healthy
        # request; the verdict is printed as a sentence the operator can act on.
        if oc.get("filling_source"):
            add(f"  filling source   : {oc.get('filling_source')}")
        if oc.get("filling_candidates"):
            add(f"  filling tried    : {oc.get('filling_candidates')}")
        if oc.get("ok"):
            add("")
            add("  ORDER CHECK: PASSED")
            add("  order_check passed basic validation.")
            add("  No order was sent by this diagnostic.")
        else:
            add("")
            add("  ORDER CHECK: FAILED")
            add(f"  the terminal refused the project's own request — "
                f"retcode {oc.get('retcode')} ({oc.get('retcode_name') or 'unnamed'}), "
                f"comment {oc.get('comment')!r}.")
            add(f"  Rule applied: {oc.get('rule') or 'n/a'}")
            add("  No order was sent by this diagnostic.")
    else:
        add(f"  not attempted    : {oc.get('error') or oc.get('note') or mt5.get('error') or 'unknown'}")
        add("")
        add("  ORDER CHECK: NOT ATTEMPTED — order_check could not be run (see the line above).")
        add("  No order was sent by this diagnostic.")
    add("")

    lf = rep.get("layer_facts", {})
    add("## LAYER FACTS")
    for key in ("platform_supported", "python_found", "wheel_supported", "package_importable",
                "package_importable_somewhere", "terminals_found", "terminal_process_running",
                "initialized", "account_available", "symbol_available", "market_data_available",
                "trading_permissions_ok", "order_check_attempted", "order_check_ok",
                "order_check_retcode"):
        if key in lf:
            add(f"  {key:<28}: {lf.get(key)}")
    add("")
    add("NOTE: Python 3.13 is NOT required. The MetaTrader5 package publishes 64-bit Windows")
    add("      wheels for CPython 3.6-3.14; any of those interpreters works. The package only")
    add("      exists on Windows — Linux can only ever be SIMULATOR mode, by design.")
    return "\n".join(L)


def report_json(rep: Dict[str, Any]) -> str:
    return json.dumps(rep, indent=2, default=str)
