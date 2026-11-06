#!/usr/bin/env python3
"""Hero1 `doctor` — honest environment report. Never writes unless --init is passed.

Distinguishes:  healthy | unavailable | unconfigured | error | not_detectable.
Never reports a simulated or skipped connection as a real MT5 connection.
"""
from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for pkg in ("storage", "eventbus", "ai_orchestrator", "mt5_adapter"):
    sys.path.insert(0, str(ROOT / "packages" / pkg / "src"))

from hero1_storage.init import initialize_databases  # noqa: E402
from hero1_ai_orchestrator import get_ollama_provider  # noqa: E402
from hero1_mt5_adapter import probe_mt5_availability  # noqa: E402


def _line(name: str, state: str, detail: str = "") -> None:
    tag = {"healthy": "[ OK ]", "ok": "[ OK ]", "unconfigured": "[CONF]",
           "unavailable": "[MISS]", "error": "[FAIL]", "not_detectable": "[ N/A]",
           "informational": "[INFO]"}.get(state, "[????]")
    print(f"{tag} {name:<28} {detail}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", action="store_true", help="initialize databases before reporting (creates dirs, migrates, seeds)")
    ap.add_argument("--mt5", action="store_true", help="include deep MT5 probe details (read-only; still never initializes a terminal or logs in)")
    args = ap.parse_args()

    print("=" * 72)
    print("HERO1 DOCTOR")
    print("=" * 72)
    _line("python", "healthy" if sys.version_info >= (3, 11) else "error",
          f"{sys.version.split()[0]} on {platform.platform()}")

    report = initialize_databases(create=args.init)
    _line("data_root", "healthy" if report.data_root else "error",
          f"{report.data_root} (must stay OUTSIDE the git repo)")
    reg = report.registry
    _line("registry_db", reg.get("status", "error"),
          f"{reg.get('path')} tables={len(reg.get('tables', []))} {reg.get('detail', '')}")
    worst = 0
    for sym, info in report.instruments.items():
        state = info.get("status", "error")
        _line(f"instrument_db[{sym}]", state,
              f"tables={len(info.get('tables', []))} {info.get('detail', '')}")
        if state != "healthy":
            worst += 1
    if reg.get("status") != "healthy":
        worst += 1

    ollama = get_ollama_provider().health()
    ostate = ollama.get("status", "error")
    odetail = f"host={ollama.get('host')}"
    if ostate == "healthy":
        odetail += f" version={ollama.get('version')} models={ollama.get('models')}"
    else:
        odetail += f" {ollama.get('detail', '')}"
    _line("ollama", ostate if ostate in {"healthy", "unavailable", "error"} else "informational", odetail)

    mt5 = probe_mt5_availability()
    mstate = mt5.get("status", "error")
    label = {"package_present": "healthy"}.get(mstate, mstate)
    _line("mt5", label, mt5.get("detail", ""))

    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        _line("gpu", "not_detectable", "nvidia-smi absent — no NVIDIA GPU/driver visible from here")
    else:
        try:
            out = subprocess.run([nvidia_smi, "--query-gpu=name,memory.total,driver_version",
                                  "--format=csv,noheader"],
                                 capture_output=True, text=True, timeout=4).stdout.strip()
            _line("gpu", "healthy", out)
        except Exception as exc:  # noqa: BLE001
            _line("gpu", "error", str(exc))

    print("-" * 72)
    if worst:
        print(f"RESULT: {worst} component(s) need attention. Run with --init after reviewing.")
        return 1
    print("RESULT: registry and instrument databases healthy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
