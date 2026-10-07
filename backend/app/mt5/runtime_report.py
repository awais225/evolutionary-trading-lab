"""V5.1a §8-§10 — why is real MT5 available or not?

One place that answers, with evidence instead of a guess:

  * which Python interpreter is running the backend (version, bitness, venv?),
  * which interpreter the launcher (`start.bat` -> `scripts/run_backend.bat`)
    would actually use for this checkout,
  * whether the `MetaTrader5` package is importable in *that* interpreter —
    checked in a real subprocess, which is the only way to prove it rather than
    infer it from the running process,
  * whether a terminal is installed/discoverable/connected, and what the account
    and trading permissions are,
  * exactly which of those facts blocks real execution, in order.

Nothing here places an order and nothing here fabricates a result: when MT5 is
not usable the report says so and names the failing step. The simulator is an
explicit, reported mode — never a silent fallback.
"""
from __future__ import annotations

import json
import os
import platform
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# candidate interpreters the launcher looks for, in the launcher's own order
_LAUNCHER_CANDIDATES = (
    ("root/.venv", Path(".venv") / "Scripts" / "python.exe"),
    ("backend/.venv", Path("backend") / ".venv" / "Scripts" / "python.exe"),
    ("root/venv", Path("venv") / "Scripts" / "python.exe"),
    ("root/.venv(posix)", Path(".venv") / "bin" / "python"),
    ("backend/.venv(posix)", Path("backend") / ".venv" / "bin" / "python"),
)

# MetaTrader5 is published as prebuilt Windows wheels only (no sdist). What a
# machine needs is a *64-bit Windows CPython inside the published wheel range of
# the installed package version* — verified against PyPI on 2026-10-07:
# MetaTrader5 5.0.6231 ships cp36..cp314 win_amd64 wheels. Python 3.13 is NOT a
# requirement; any of those interpreters works, and a Python outside the range
# fails at `pip install` (not at import), which is what silently leaves the lab
# in SIMULATOR mode.
MT5_WHEEL_MIN = (3, 6)
MT5_WHEEL_MAX = (3, 14)
MT5_WHEEL_RANGE_TEXT = ("CPython 3.6-3.14, 64-bit, Windows (MetaTrader5 5.0.6231 wheels); "
                        "3.13 is not required")

_PACKAGE_PROBE = (
    "import json,sys\n"
    "try:\n"
    "    import MetaTrader5 as m\n"
    "    print(json.dumps({'importable': True, 'version': getattr(m, '__version__', 'unknown')}))\n"
    "except Exception as e:\n"
    "    print(json.dumps({'importable': False, 'error': f'{type(e).__name__}: {e}'}))\n"
)


def _repo_root(root: Optional[Path] = None) -> Path:
    if root is not None:
        return Path(root)
    # backend/app/mt5/runtime_report.py -> repo root
    return Path(__file__).resolve().parents[3]


def _host_is_windows() -> bool:
    """Single place that asks the host OS — so the platform fact is patchable in
    tests instead of being hidden inside an assertion."""
    return sys.platform.startswith("win")


def _interpreter_facts() -> Dict[str, Any]:
    v = sys.version_info
    wheel_ok = MT5_WHEEL_MIN <= (v.major, v.minor) <= MT5_WHEEL_MAX
    return {
        "executable": sys.executable,
        "version": f"{v.major}.{v.minor}.{v.micro}",
        "version_info": [v.major, v.minor, v.micro],
        "implementation": platform.python_implementation(),
        "bits": struct.calcsize("P") * 8,
        "is_venv": sys.prefix != sys.base_prefix or "venv" in sys.executable.lower(),
        "prefix": sys.prefix,
        "base_prefix": sys.base_prefix,
        "platform": platform.system().lower(),
        "platform_release": platform.release(),
        "wheel_supported": wheel_ok,
        "wheel_range": MT5_WHEEL_RANGE_TEXT,
        "wheel_note": ("this interpreter is inside the published MetaTrader5 wheel range"
                       if wheel_ok else
                       "no MetaTrader5 wheel is published for this Python version — "
                       "pip install would fail and the lab would stay in SIMULATOR mode"),
    }


def _launcher_python(root: Path) -> Dict[str, Any]:
    """The interpreter `start.bat`/`run_backend.bat` would launch for this checkout."""
    for label, rel in _LAUNCHER_CANDIDATES:
        candidate = root / rel
        if candidate.exists():
            return {"found": True, "label": label, "path": str(candidate),
                    "expected": str(candidate.resolve())}
    # no venv yet: the launcher falls back to whatever `python` is on PATH
    import shutil
    on_path = shutil.which("python") or shutil.which("python3") or ""
    return {"found": False, "label": "PATH", "path": on_path,
            "expected": str(Path(on_path).resolve()) if on_path else "",
            "note": "no .venv found in this checkout — the launcher would fall back to PATH"}


def _package_probe(python: str) -> Dict[str, Any]:
    """Actually run `import MetaTrader5` in the given interpreter."""
    if not python:
        return {"checked": False, "reason": "no interpreter available to probe"}
    exe = Path(python)
    if not exe.exists():
        return {"checked": False, "reason": f"interpreter not found: {python}"}
    try:
        proc = subprocess.run([str(exe), "-c", _PACKAGE_PROBE], capture_output=True,
                              text=True, timeout=30)
    except Exception as e:  # subprocess failed for OS reasons, not an import
        return {"checked": False, "reason": f"probe failed: {type(e).__name__}: {e}"}
    out = (proc.stdout or "").strip().splitlines()
    payload = {}
    for line in reversed(out):
        try:
            payload = json.loads(line)
            break
        except Exception:
            continue
    if not payload:
        return {"checked": True, "importable": False, "interpreter": str(exe),
                "error": (proc.stderr or "").strip()[:400] or "probe produced no result"}
    payload["checked"] = True
    payload["interpreter"] = str(exe)
    return payload


def _current_package_facts() -> Dict[str, Any]:
    try:
        from .mt5_real import MT5_PACKAGE_AVAILABLE, MT5_IMPORT_ERROR
    except Exception as e:  # pragma: no cover - import machinery failure
        return {"importable": False, "error": f"{type(e).__name__}: {e}"}
    facts: Dict[str, Any] = {"importable": bool(MT5_PACKAGE_AVAILABLE),
                             "error": "" if MT5_PACKAGE_AVAILABLE else (MT5_IMPORT_ERROR or "not importable")}
    if MT5_PACKAGE_AVAILABLE:
        try:
            import MetaTrader5 as mt5  # type: ignore
            facts["version"] = getattr(mt5, "__version__", "unknown")
        except Exception as e:  # pragma: no cover
            facts["importable"] = False
            facts["error"] = f"{type(e).__name__}: {e}"
    return facts


def _terminal_facts() -> Dict[str, Any]:
    facts: Dict[str, Any] = {"supported_platform": sys.platform.startswith("win")}
    try:
        from .discovery import discover_terminals
        terms = discover_terminals() or []
        facts["discovered"] = [{"name": t.get("name"), "path": t.get("path")} for t in terms]
        facts["discovered_count"] = len(terms)
    except Exception as e:
        facts["discovered"] = []
        facts["discovered_count"] = 0
        facts["discovery_error"] = f"{type(e).__name__}: {e}"
    try:
        from .config import get_saved_terminal_path, load_mt5_config
        cfg = load_mt5_config() or {}
        facts["saved_path"] = get_saved_terminal_path() or cfg.get("terminal_path") or ""
        facts["last_connected_company"] = cfg.get("last_connected_company") or ""
        facts["last_connected_build"] = cfg.get("last_connected_build") or ""
    except Exception as e:
        facts["config_error"] = f"{type(e).__name__}: {e}"
    return facts


def _bridge_facts() -> Dict[str, Any]:
    try:
        from .factory import bridge_status
        st = bridge_status() or {}
    except Exception as e:
        return {"source": "unavailable", "error": f"{type(e).__name__}: {e}"}
    return {
        "active_bridge": st.get("active_bridge"),
        "source": st.get("source"),
        "is_simulated": st.get("is_simulated"),
        "connected": st.get("connected"),
        "terminal_company": st.get("terminal_company"),
        "terminal_build": st.get("terminal_build"),
        "terminal_path": st.get("terminal_path"),
        "account": st.get("account") or {},
        "trade_allowed": st.get("trade_allowed"),
        "blocked_code": st.get("blocked_code"),
        "blocked_reason": st.get("blocked_reason"),
    }


def _check(checks: List[Dict[str, Any]], cid: str, label: str, ok: Any,
           detail: str = "", code: str = "") -> bool:
    passed = bool(ok)
    checks.append({"id": cid, "label": label, "ok": passed, "detail": detail, "code": code})
    return passed


def mt5_runtime_report(root: Optional[Path] = None, *, probe_launcher: bool = True) -> Dict[str, Any]:
    """Machine-readable answer to 'can this installation trade real MT5, and if not why?'"""
    root_p = _repo_root(root)
    interp = _interpreter_facts()
    launcher = _launcher_python(root_p)
    package = _current_package_facts()
    terminal = _terminal_facts()
    bridge = _bridge_facts()

    launcher_probe: Dict[str, Any] = {"checked": False, "reason": "not requested"}
    if probe_launcher:
        # the decisive §9 question: can the interpreter the LAUNCHER picks import MT5?
        same = bool(launcher.get("expected")) and \
            os.path.realpath(launcher["expected"]) == os.path.realpath(interp["executable"])
        launcher_probe = _package_probe(launcher.get("expected") or "")
        launcher_probe["same_as_running"] = same

    checks: List[Dict[str, Any]] = []
    _check(checks, "platform_supported", "MetaTrader5 requires Windows",
           _host_is_windows(), f"host platform: {interp['platform']}", "PLATFORM_UNSUPPORTED")
    _check(checks, "wheel_for_this_python", "MetaTrader5 wheel exists for this Python version",
           interp.get("wheel_supported"), interp.get("wheel_range", ""), "MT5_WHEEL_UNAVAILABLE")
    _check(checks, "package_importable", "MetaTrader5 package importable in the running backend",
           package.get("importable"), package.get("error") or f"version {package.get('version')}",
           "MT5_UNAVAILABLE")
    _check(checks, "terminal_discovered", "MetaTrader5 terminal discovered",
           terminal.get("discovered_count"), f"{terminal.get('discovered_count', 0)} terminal(s) found",
           "TERMINAL_NOT_FOUND")
    _check(checks, "bridge_real", "Active bridge is the real MT5 bridge (not the simulator)",
           bridge.get("source") == "MT5", f"active bridge: {bridge.get('active_bridge')} / {bridge.get('source')}",
           "MT5_UNAVAILABLE")
    # the simulator bridge reports connected=True for its own purposes; a *real*
    # connection requires the real bridge, or the check would be a false positive
    _check(checks, "connected", "Terminal connected (real bridge)",
           bridge.get("source") == "MT5" and bridge.get("connected"),
           f"company: {bridge.get('terminal_company') or '—'}", "MT5_NOT_CONNECTED")
    _check(checks, "account_available", "Trading account available",
           bool(bridge.get("account")), "login/server reported by the terminal" if bridge.get("account") else "",
           "ACCOUNT_UNAVAILABLE")
    _check(checks, "trade_allowed", "Terminal allows algorithmic trading",
           bridge.get("trade_allowed") is not False, "", "ALGO_TRADING_DISABLED")

    failed = [c for c in checks if not c["ok"]]
    # `platform` failing on Linux/macOS is not a defect of the installation: the
    # package simply does not exist there, so it is reported as the reason.
    if not failed:
        verdict, reason = "REAL_MT5_READY", "real MT5 is available: the package imports, the terminal is connected and the account is identified"
    else:
        top = failed[0]
        verdict = "SIMULATOR_ONLY"
        reason = f"{top['code']}: {top['label']} — {top['detail'] or 'failed'}"
        if top["id"] == "package_importable" and launcher_probe.get("importable") \
                and not launcher_probe.get("same_as_running"):
            reason += (f"; the package IS importable in the interpreter the launcher would use "
                       f"({launcher_probe.get('interpreter')}) — the backend is running a different "
                       f"interpreter than the launcher picks")

    # V5.1a-next §5 — the exact failing *layer* (spec vocabulary), computed from
    # the same measured facts. `verdict`/`reason` above stay unchanged for
    # compatibility; this is the finer-grained answer, never a guess: facts this
    # report did not measure stay None and cannot produce a failure.
    from .windows_diagnostic import classify_layers
    pkg_elsewhere = ""
    if launcher_probe.get("importable") and not launcher_probe.get("same_as_running"):
        pkg_elsewhere = str(launcher_probe.get("interpreter") or "")
    layer = classify_layers({
        "platform_supported": _host_is_windows(),
        "python_found": True,
        "wheel_supported": interp.get("wheel_supported"),
        "package_importable": bool(package.get("importable")),
        "package_importable_somewhere": pkg_elsewhere,
        "package_error": package.get("error") or "",
        "terminals_found": terminal.get("discovered_count", 0),
        "terminal_process_running": None,          # not measured by this report
        "initialized": bridge.get("source") == "MT5" and bool(bridge.get("connected")),
        "account_available": bool(bridge.get("account")) if bridge.get("source") == "MT5" else False,
        "symbol_available": None,                  # not measured by this report
        "market_data_available": None,             # not measured by this report
        "trading_permissions_ok": bridge.get("trade_allowed"),
        "order_check_attempted": False,
    })

    return {
        "verdict": verdict,
        "reason": reason,
        "environment_class": layer["environment"],
        "environment_reason": layer["reason"],
        "python": interp,
        "launcher_python": launcher,
        "launcher_package_probe": launcher_probe,
        "metatrader5_package": package,
        "terminal": terminal,
        "bridge": bridge,
        "checks": checks,
        "failed_checks": [c["id"] for c in failed],
        "simulator_available": True,
        "note": ("The simulator is a supported, explicitly reported mode. It is never used to "
                 "represent a real broker execution."),
    }


def format_report(rep: Dict[str, Any]) -> str:
    """Human-readable rendering used by the CLI tool."""
    lines: List[str] = []
    lines.append("MT5 RUNTIME DIAGNOSTIC")
    lines.append("=" * 72)
    lines.append(f"verdict: {rep.get('verdict')}")
    lines.append(f"reason:  {rep.get('reason')}")
    p = rep.get("python", {})
    lines.append("")
    lines.append("Python actually running the backend")
    lines.append(f"  executable : {p.get('executable')}")
    lines.append(f"  version    : {p.get('version')} ({p.get('implementation')}, {p.get('bits')}-bit)")
    lines.append(f"  virtualenv : {'yes' if p.get('is_venv') else 'no'}")
    lines.append(f"  platform   : {p.get('platform')} {p.get('platform_release')}")
    lines.append(f"  MT5 wheels : {p.get('wheel_range')}"
                 f" -> {'OK' if p.get('wheel_supported') else 'NOT SUPPORTED'}")
    l = rep.get("launcher_python", {})
    lines.append("")
    lines.append("Python the launcher (start.bat) would use")
    lines.append(f"  source     : {l.get('label')}")
    lines.append(f"  path       : {l.get('path') or '(none found — PATH fallback)'}")
    if l.get("note"):
        lines.append(f"  note       : {l['note']}")
    lp = rep.get("launcher_package_probe", {})
    if lp.get("checked"):
        lines.append(f"  MetaTrader5 importable via that interpreter: "
                     f"{'YES' if lp.get('importable') else 'NO'}"
                     f"{' (same interpreter as the running backend)' if lp.get('same_as_running') else ' (DIFFERENT from the running backend)'}")
        if not lp.get("importable"):
            lines.append(f"    reason: {lp.get('error')}")
    pkg = rep.get("metatrader5_package", {})
    lines.append("")
    lines.append("MetaTrader5 package (running interpreter)")
    lines.append(f"  importable : {'yes' if pkg.get('importable') else 'no'}")
    lines.append(f"  version    : {pkg.get('version') or '—'}")
    if pkg.get("error"):
        lines.append(f"  error      : {pkg['error']}")
    t = rep.get("terminal", {})
    lines.append("")
    lines.append("Terminal")
    lines.append(f"  discovered : {t.get('discovered_count', 0)}")
    for term in (t.get("discovered") or [])[:10]:
        lines.append(f"    - {term.get('name')} :: {term.get('path')}")
    lines.append(f"  saved path : {t.get('saved_path') or '—'}")
    lines.append(f"  last seen  : {t.get('last_connected_company') or '—'} build {t.get('last_connected_build') or '—'}")
    b = rep.get("bridge", {})
    lines.append("")
    lines.append("Active bridge")
    lines.append(f"  bridge     : {b.get('active_bridge')} ({b.get('source')})")
    lines.append(f"  connected  : {b.get('connected')}")
    lines.append(f"  account    : {b.get('account') or '—'}")
    lines.append(f"  trade ok   : {b.get('trade_allowed')}")
    if b.get("blocked_code"):
        lines.append(f"  blocked    : {b['blocked_code']} — {b.get('blocked_reason')}")
    lines.append("")
    lines.append("Checks")
    for c in rep.get("checks", []):
        lines.append(f"  [{'PASS' if c['ok'] else 'FAIL'}] {c['label']}"
                     + (f" — {c['detail']}" if c["detail"] else ""))
    lines.append("")
    lines.append(rep.get("note", ""))
    return "\n".join(lines)
