"""
Backup Facility (spec §40).

Creates a complete, consistent backup archive:
  BACKUPS/lab_backup_YYYYMMDD_HHMMSS.zip

Archive contents:
  - database: safe SQLite snapshot (VACUUM INTO or sqlite backup API)
  - configuration: CONFIG/ (or config/)
  - version manifest: version_manifest.json (spec §41)
  - dataset metadata: DATA/*/metadata/
  - research files: RESEARCH/ (JSONs, summaries)
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from .. import paths as P
from ..config import get_config
from ..versions import manifest

log = logging.getLogger("backup")


def create_backup() -> Dict[str, Any]:
    """Execute consistent backup and return summary (spec §40)."""
    P.ensure_layout()
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = P.BACKUPS_DIR / f"lab_backup_{timestamp_str}.zip"
    temp_db_path = P.BACKUPS_DIR / f"temp_db_{timestamp_str}.db"

    cfg = get_config()
    db_file = Path(cfg.database_path)

    # 1. Safe SQLite snapshot (VACUUM INTO creates a transactionally consistent copy)
    try:
        conn = sqlite3.connect(db_file)
        # Use sqlite3 VACUUM INTO for non-blocking clean snapshot
        conn.execute(f"VACUUM INTO '{temp_db_path.as_posix()}'")
        conn.close()
    except Exception:
        # Fallback to sqlite backup API
        try:
            src_conn = sqlite3.connect(db_file)
            dst_conn = sqlite3.connect(temp_db_path)
            with dst_conn:
                src_conn.backup(dst_conn)
            dst_conn.close()
            src_conn.close()
        except Exception as e:
            log.error("database backup failed: %s", e)
            return {"ok": False, "error": f"database snapshot failed: {e}"}

    # 2. Package everything into ZIP
    files_added = 0
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # Add database
            zf.write(temp_db_path, "DATABASE/lab_state.db")
            files_added += 1

            # Add version manifest (spec §41)
            manifest_json = json.dumps(manifest(), indent=2)
            zf.writestr("version_manifest.json", manifest_json)
            files_added += 1

            # Add config files
            config_dir = P.CONFIG_DIR if P.CONFIG_DIR.exists() else (P.ROOT_DIR / "config")
            if config_dir.exists():
                for f in config_dir.glob("*.yaml"):
                    zf.write(f, f"CONFIG/{f.name}")
                    files_added += 1

            # Add metadata from DATA/
            if P.DATA_DIR.exists():
                for meta_f in P.DATA_DIR.glob("**/metadata/*.json"):
                    rel = meta_f.relative_to(P.ROOT_DIR)
                    zf.write(meta_f, str(rel))
                    files_added += 1

            # Add RESEARCH summaries/JSONs
            if P.RESEARCH_DIR.exists():
                for rf in P.RESEARCH_DIR.glob("**/*.json"):
                    rel = rf.relative_to(P.ROOT_DIR)
                    zf.write(rf, str(rel))
                    files_added += 1

        size_bytes = zip_path.stat().st_size
        log.info("backup created: %s (%d files, %d bytes)", zip_path.name, files_added, size_bytes)
    finally:
        if temp_db_path.exists():
            temp_db_path.unlink()

    return {
        "ok": True,
        "filename": zip_path.name,
        "path": str(zip_path),
        "size_bytes": size_bytes,
        "size_mb": round(size_bytes / (1024 * 1024), 2),
        "files_count": files_added,
        "created_at": time.time(),
    }


def list_backups() -> list[Dict[str, Any]]:
    """List available backups in BACKUPS/ directory."""
    P.ensure_layout()
    out = []
    for f in sorted(P.BACKUPS_DIR.glob("lab_backup_*.zip"), reverse=True):
        stat = f.stat()
        out.append({
            "filename": f.name,
            "path": str(f),
            "size_bytes": stat.st_size,
            "size_mb": round(stat.st_size / (1024 * 1024), 2),
            "created_at": stat.st_mtime,
        })
    return out
