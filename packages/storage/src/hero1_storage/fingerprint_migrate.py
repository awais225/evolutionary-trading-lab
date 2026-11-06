"""Safe migration of semantic strategy fingerprints after the fingerprint
definition changed (compiler 1.0.0 → 1.1.0, 2026-11-06).

Why: the fingerprint now excludes DISPLAY identity fields (identity.name /
identity.description — presentation only) and therefore differs from every hash
persisted before the change. Strategy semantics are unchanged; this module
recomputes the hashes deterministically from the stored spec so that:

  * every node's spec_hash matches sha256 of the semantic canonical JSON,
  * rows whose stored hash already match are left untouched (idempotent),
  * NOTHING is deleted, renamed or reordered; lineage rows carry no hashes,
  * if ANY collision on (spec_hash, version) would result — i.e. two stored
    nodes are the SAME strategy at the same version — the migration REFUSES to
    write anything and reports both nodes; a human decides.

Usage (CLI wrapper: scripts/migrate_fingerprints.py):
    compute = collect_plans(root)           # read-only, all DBs
    if compute["collisions"]: abort
    apply_plans(compute)                    # after file backups
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_TF_RE = re.compile(r"^[A-Za-z0-9]{1,4}$")


def _instrument_dbs(root: Path) -> Dict[str, Path]:
    """Every instrument DB under <root>/<SYMBOL>/DATABASE/hero1.db (+ registry is skipped)."""
    out: Dict[str, Path] = {}
    root = Path(root)
    for child in sorted(root.iterdir()):
        db = child / "DATABASE" / "hero1.db"
        if child.is_dir() and db.exists() and child.name.isupper():
            out[child.name] = db
    return out


def _new_fingerprint(spec_json: str) -> Tuple[Optional[str], Optional[str]]:
    """(new_hash, error) — recompute exactly like the compiler does."""
    try:
        parsed = json.loads(spec_json)
    except Exception as exc:  # malformed JSON: never invent a hash
        return None, f"unparseable spec_json: {exc}"
    try:
        from hero1_strategy_spec import semantic_canonical
        from hero1_strategy_spec.spec import StrategySpec
        spec = StrategySpec.model_validate(parsed)
        canon = semantic_canonical(spec.model_dump(mode="json"))
    except Exception as exc:  # does not validate as a spec: leave untouched, report
        return None, f"spec failed schema re-validation: {exc}"
    import hashlib
    return hashlib.sha256(canon.encode("utf-8")).hexdigest(), None


def collect_plans(root: Path) -> Dict[str, Any]:
    """Read-only analysis across every instrument DB under root.

    Returns {dbs: [{symbol, path, scanned, updated:[ids], already, unhashable:[ids]}],
             collisions: [{symbol, hash, version, node_ids}...]}
    """
    plans: List[Dict[str, Any]] = []
    collisions: List[Dict[str, Any]] = []
    for symbol, db in _instrument_dbs(root).items():
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT id, version, spec_json, spec_hash FROM nodes").fetchall()
        except sqlite3.OperationalError as exc:  # table missing etc.
            plans.append({"symbol": symbol, "path": str(db), "error": str(exc),
                          "scanned": 0, "updated": [], "already": 0, "unhashable": []})
            con.close()
            continue
        con.close()
        scanned = len(rows)
        updated, already, unhashable = [], 0, []
        new_by_id: Dict[int, str] = {}
        for node_id, _version, spec_json, stored_hash in rows:
            if not spec_json:
                continue  # draft: no semantics to hash
            new_hash, err = _new_fingerprint(spec_json)
            if new_hash is None:
                unhashable.append({"id": node_id, "error": err})
                continue
            new_by_id[node_id] = new_hash
            if stored_hash == new_hash:
                already += 1
            else:
                updated.append(node_id)
        # collision check on (new_hash, version) — only matters for rows we would write
        by_key: Dict[Tuple[str, int], List[int]] = {}
        for node_id, version, _sj, _h in rows:
            nh = new_by_id.get(node_id)
            if nh:
                by_key.setdefault((nh, version), []).append(node_id)
        for (nh, version), ids in by_key.items():
            if len(ids) > 1:
                collisions.append({"symbol": symbol, "spec_hash": nh,
                                   "version": version, "node_ids": ids})
        plans.append({"symbol": symbol, "path": str(db), "scanned": scanned,
                      "updated": updated, "already": already,
                      "unhashable": unhashable})
    return {"root": str(root), "dbs": plans, "collisions": collisions}


def apply_plans(plans: Dict[str, Any], *, backup_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Write phase. Caller MUST ensure plans['collisions'] is empty (checked here too)."""
    if plans.get("collisions"):
        raise RuntimeError(
            "refusing to apply: fingerprint collisions detected — "
            + json.dumps(plans["collisions"], indent=2))
    backup_dir = Path(backup_dir) if backup_dir else None
    applied: List[Dict[str, Any]] = []
    for dbplan in plans["dbs"]:
        if dbplan.get("error"):
            applied.append(dbplan)
            continue
        ids = dbplan["updated"]
        if not ids:
            applied.append({**dbplan, "written": 0, "backup": None})
            continue
        db = Path(dbplan["path"])
        backup = None
        if backup_dir is not None:
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup = backup_dir / f"{dbplan['symbol']}-hero1.db.pre-fingerprint-migration"
            shutil.copy2(db, backup)
        con = sqlite3.connect(f"file:{db}?mode=rw", uri=True)
        try:
            with con:  # single transaction per DB
                rows = con.execute(
                    f"SELECT id, spec_json, version FROM nodes WHERE id IN ({','.join('?' * len(ids))})",
                    ids).fetchall()
                for node_id, spec_json, _version in rows:
                    new_hash, err = _new_fingerprint(spec_json)
                    if new_hash is None:
                        raise RuntimeError(f"node {node_id}: {err}")
                    con.execute("UPDATE nodes SET spec_hash = ? WHERE id = ?",
                                (new_hash, node_id))
        finally:
            con.close()
        applied.append({**dbplan, "written": len(ids),
                        "backup": str(backup) if backup else None})
    return {"root": plans["root"], "dbs": applied, "collisions": []}
