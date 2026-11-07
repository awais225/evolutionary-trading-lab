#!/usr/bin/env python3
"""Verify the MetaTrader5 package in THIS interpreter (the one that runs it).

The Windows failure this exists for: Hero1's dashboard reported
"ModuleNotFoundError: No module named 'MetaTrader5'" because the package had
never been installed into the backend's .venv - it was not declared in any
requirements/pyproject, so no setup step ever installed it. A requirements
entry alone is not proof: this script interrogates the exact interpreter
executing it (sys.executable) and reports facts only.

Run it with the backend interpreter:
    .venv\\Scripts\\python.exe scripts\\check_mt5_env.py [--json]

Exit codes: 0 = MetaTrader5 importable in this interpreter;
            1 = package not installed here;
            2 = package present but the import itself fails.
It never initializes a terminal and never touches an account.
"""
from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import json
import platform
import sys


def probe() -> dict:
    report = {
        "python_executable": sys.executable,
        "python_version": platform.python_version(),
        "platform": platform.system(),
        "package_name": "MetaTrader5",
        "package_installed": False,
        "package_version": None,
        "import_ok": False,
        "import_error": None,
        "api_version": None,
        "api_version_note": None,
    }
    spec = importlib.util.find_spec("MetaTrader5")
    if spec is None:
        report["import_error"] = "ModuleNotFoundError: No module named 'MetaTrader5'"
        return report
    report["package_installed"] = True
    try:
        report["package_version"] = importlib.metadata.version("MetaTrader5")
    except importlib.metadata.PackageNotFoundError:
        report["package_version"] = None
    try:
        mt5 = importlib.import_module("MetaTrader5")
    except Exception as exc:  # noqa: BLE001 - the failure reason IS the report
        report["import_error"] = f"{type(exc).__name__}: {exc}"
        return report
    report["import_ok"] = True
    try:
        version = mt5.version()
        report["api_version"] = list(version) if isinstance(version, (list, tuple)) else str(version)
    except Exception as exc:  # noqa: BLE001
        report["api_version_note"] = (f"mt5.version() not readable without a terminal "
                                      f"session ({type(exc).__name__}: {exc})")
    return report


def main(argv: list[str]) -> int:
    report = probe()
    if "--json" in argv:
        print(json.dumps(report, indent=2))
    else:
        print(f"python executable : {report['python_executable']}")
        print(f"python version    : {report['python_version']} ({report['platform']})")
        if report["import_ok"]:
            print(f"MetaTrader5       : INSTALLED, package version {report['package_version']}")
            print(f"import MetaTrader5: OK")
            if report["api_version"] is not None:
                print(f"MT5 API version   : {report['api_version']}")
            elif report["api_version_note"]:
                print(f"MT5 API version   : {report['api_version_note']}")
        elif report["package_installed"]:
            print(f"MetaTrader5       : present but IMPORT FAILS: {report['import_error']}")
            print(f"fix: \"{report['python_executable']}\" -m pip install --force-reinstall MetaTrader5")
        else:
            print(f"MetaTrader5       : NOT INSTALLED in this interpreter")
            print(f"import error      : {report['import_error']}")
            print(f"fix: \"{report['python_executable']}\" -m pip install MetaTrader5")
    if report["import_ok"]:
        return 0
    return 2 if report["package_installed"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
