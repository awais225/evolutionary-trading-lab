#!/usr/bin/env python3
"""V5.1a-next §3-§6 — run the deep Windows MT5 diagnostic and classify the layer.

Usage (from the repository root, with the launcher's interpreter):

    .venv\\Scripts\\python.exe backend\\tools\\mt5_windows_diagnostic.py
    .venv\\Scripts\\python.exe backend\\tools\\mt5_windows_diagnostic.py --symbol XAUUSD --json
    .venv\\Scripts\\python.exe backend\\tools\\mt5_windows_diagnostic.py --save LOGS\\mt5.txt

The operator normally does not call this directly — `CHECK_MT5_WINDOWS.bat`
resolves the interpreter exactly like the launcher does and runs this file.

Safety: the diagnostic reads facts and runs `mt5.order_check` on the project's
own order request. It NEVER calls `order_send`, NEVER starts or kills a process,
and never prints credentials. Exit codes:
    0  MT5 READY
    3  not ready — the report names the exact layer (spec §5)
    4  this host is not Windows (the lab environment; the Windows vocabulary does
       not apply) — the report says so explicitly
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.mt5.windows_diagnostic import (  # noqa: E402
    MT5_PLATFORM_UNSUPPORTED, MT5_READY, format_diagnostic, report_json,
    windows_mt5_diagnostic,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="read-only Windows MT5 diagnostic")
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--save", default="", help="also write the report to this file (the .bat uses this)")
    args = ap.parse_args()

    rep = windows_mt5_diagnostic(Path(args.root), symbol=args.symbol)
    text = report_json(rep) if args.json else format_diagnostic(rep)
    print(text)
    if args.save:
        try:
            out = Path(args.save)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(format_diagnostic(rep) + "\n", encoding="utf-8")
            print(f"\n[saved] {out}")
        except Exception as e:
            print(f"\n[warning] could not write {args.save}: {type(e).__name__}: {e}")

    cls = rep.get("environment")
    if cls == MT5_READY:
        return 0
    if cls == MT5_PLATFORM_UNSUPPORTED:
        return 4
    return 3


if __name__ == "__main__":
    sys.exit(main())
