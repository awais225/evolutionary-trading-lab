#!/usr/bin/env python3
"""Initialize (or verify) Hero1 databases.

Usage:
  python scripts/init_db.py --check     # read-only report; exits non-zero if unhealthy
  python scripts/init_db.py --create    # create dirs + run Alembic migrations + seed instruments

Never creates anything without --create. The canonical DATA root is <repo>/DATA (git-ignored); any other in-repository path is refused.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packages" / "storage" / "src"))

from hero1_storage.init import initialize_databases  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="read-only health check")
    mode.add_argument("--create", action="store_true", help="create + migrate + seed (idempotent)")
    args = ap.parse_args()

    # rc contract (stable, machine-checked by the launcher):
    #   0 = healthy        (report.ok is True)
    #   1 = check ran but something is unavailable/unhealthy (structured report printed)
    #   2 = UNEXPECTED crash (traceback on stderr) — distinct from "database unhealthy"
    # Every path flushes stdout/stderr before returning so a redirected log always
    # contains the result (block buffering once lost the report on crash).
    try:
        report = initialize_databases(create=args.create)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        sys.stdout.flush(); sys.stderr.flush()
        return 130
    except Exception:  # noqa: BLE001 - rc contract: crashes are rc=2, never swallowed
        import traceback
        traceback.print_exc()
        sys.stdout.flush(); sys.stderr.flush()
        return 2
    import json
    print(json.dumps(report.to_dict(), indent=2))
    sys.stdout.flush()
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
