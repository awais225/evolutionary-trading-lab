#!/usr/bin/env python3
"""Re-hash every stored node fingerprint after the semantic-fingerprint change
(compiler 1.0.0 → 1.1.0: identity.name/description are no longer hashed).

SAFE BY DEFAULT:
  * no arguments          → dry-run report (reads; writes nothing)
  * --apply               → backs up every instrument DB to
                            <root>/_backups/fingerprint-<ts>/ first, then
                            rewrites spec_hash in per-DB transactions
  * collision anywhere    → NOTHING is written anywhere (exit 1), both nodes
                            of each colliding pair are listed

Never deletes, renames or reorders anything. Idempotent.

Usage:
  python scripts/migrate_fingerprints.py [--root DATA] [--apply]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for pkg in ("storage", "strategy_spec"):
    sys.path.insert(0, str(REPO_ROOT / "packages" / pkg / "src"))

from hero1_storage.fingerprint_migrate import apply_plans, collect_plans  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(REPO_ROOT / "DATA"))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    if not root.exists():
        print(f"data root not found: {root}", file=sys.stderr)
        return 2

    plans = collect_plans(root)
    print(f"root: {root}")
    for db in plans["dbs"]:
        if db.get("error"):
            print(f"  [{db['symbol']}] ERROR: {db['error']}")
            continue
        print(f"  [{db['symbol']}] scanned={db['scanned']} to-rewrite={len(db['updated'])} "
              f"already-current={db['already']} unhashable={len(db['unhashable'])}")
        for u in db["unhashable"]:
            print(f"      unhashable node #{u['id']}: {u['error']}")
    if plans["collisions"]:
        print("\nCOLLISIONS — identical strategy at the same version persisted as multiple nodes:")
        for c in plans["collisions"]:
            print(f"  [{c['symbol']}] version={c['version']} hash={c['spec_hash'][:16]}… nodes={c['node_ids']}")
        print("Nothing was written. Resolve manually (rename/merge is a human decision).")
        return 1

    if not args.apply:
        print("\nDry-run only. Re-run with --apply to write (backs up every DB first).")
        return 0

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = root / "_backups" / f"fingerprint-{stamp}"
    print(f"\nBacking up to {backup_dir} and applying…")
    result = apply_plans(plans, backup_dir=backup_dir)
    for db in result["dbs"]:
        if db.get("error"):
            continue
        print(f"  [{db['symbol']}] written={db.get('written', 0)} backup={db.get('backup')}")
    print("Done. Verify with the app + pytest before deleting the _backups folder.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
