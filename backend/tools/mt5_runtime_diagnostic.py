#!/usr/bin/env python3
"""V5.1a §8-§10 — print the real MT5 runtime diagnosis.

Usage (from the repository root, with the launcher's interpreter):

    .venv\\Scripts\\python.exe backend\\tools\\mt5_runtime_diagnostic.py
    .venv\\Scripts\\python.exe backend\\tools\\mt5_runtime_diagnostic.py --json

Answers, with evidence: which Python is running, which Python the launcher uses,
whether `MetaTrader5` imports in each of them, whether a terminal is installed or
connected, and which failing check blocks real execution. Exit codes:
    0  real MT5 is ready
    10 no MT5: the report explains exactly which fact is missing
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.mt5.runtime_report import format_report, mt5_runtime_report  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-probe", action="store_true",
                    help="skip the subprocess probe of the launcher's interpreter")
    ap.add_argument("--preflight", action="store_true",
                    help="four quick lines for the launcher (no subprocess probe, always exits 0)")
    args = ap.parse_args()

    if args.preflight:
        rep = mt5_runtime_report(Path(args.root), probe_launcher=False)
        p = rep["python"]
        pkg = rep["metatrader5_package"]
        term = rep["terminal"]
        print(f"[PREFLIGHT] Python {p['version']} ({p['bits']}-bit, "
              f"{'venv' if p['is_venv'] else 'system'}, {p['platform']}) :: {p['executable']}")
        print(f"[PREFLIGHT] MetaTrader5: "
              + (f"importable ({pkg.get('version')})" if pkg.get("importable")
                 else f"NOT importable - {pkg.get('error')}"))
        print(f"[PREFLIGHT] Terminals found: {term.get('discovered_count', 0)} "
              f"(saved path: {term.get('saved_path') or 'none'})")
        print(f"[PREFLIGHT] MT5 verdict: {rep['verdict']} - {rep['reason']}")
        print("[PREFLIGHT] The dashboard reports the same value on /api/mt5/runtime.")
        return 0

    rep = mt5_runtime_report(Path(args.root), probe_launcher=not args.no_probe)
    if args.json:
        print(json.dumps(rep, indent=2, default=str))
    else:
        print(format_report(rep))
    return 0 if rep.get("verdict") == "REAL_MT5_READY" else 10


if __name__ == "__main__":
    sys.exit(main())
