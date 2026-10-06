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
from typing import Any, Dict, List, Optional

from .. import paths as P
from ..config import get_config
from ..db.snapshot import sqlite_snapshot
from ..versions import manifest

log = logging.getLogger("backup")


def _archive_name(path: Path) -> str:
    """Archive member name for a file, whatever layout DATA lives in (V4.7).

    DATA_ROOT / RESEARCH_DIR can legitimately sit outside the repository root
    (the documented ``EVOLUTIONARY_LAB_DATA_ROOT`` deployment). Deriving the
    member name with ``relative_to(ROOT_DIR)`` used to raise ValueError there and
    failed the whole backup, so the name is derived from the DATA root when
    possible and falls back to the file name.
    """
    for base in (P.DATA_DIR, P.ROOT_DIR):
        try:
            return str(path.relative_to(base))
        except ValueError:
            continue
    return path.name


def create_backup(progress=None) -> Dict[str, Any]:
    """Execute consistent backup and return summary (spec §40).

    ``progress`` is an optional ``callable(stage: str, detail: str, pct: float)``
    used by the background job path so the dashboard can report real progress.
    """
    def _report(stage: str, detail: str, pct: float) -> None:
        if progress is None:
            return
        try:
            progress(stage, detail, pct)
        except Exception:  # progress reporting must never break the backup
            log.debug("backup progress callback failed", exc_info=True)

    P.ensure_layout()
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = P.BACKUPS_DIR / f"lab_backup_{timestamp_str}.zip"
    temp_db_path = P.BACKUPS_DIR / f"temp_db_{timestamp_str}.db"

    cfg = get_config()
    db_file = Path(cfg.database_path)

    # 1. Safe SQLite snapshot (online backup API: consistent, includes WAL)
    _report("SNAPSHOT", "creating a consistent database snapshot", 5.0)
    try:
        sqlite_snapshot(db_file, temp_db_path)
    except Exception as e:
        log.error("database backup failed: %s", e)
        return {"ok": False, "stage": "SNAPSHOT", "filename": None,
                "error": f"database snapshot failed: {type(e).__name__}: {e}"}

    # 2. Package everything into ZIP
    files_added = 0
    skipped: List[str] = []
    _report("PACKAGE", "packaging database, config and research summaries", 25.0)
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
                    zf.write(meta_f, _archive_name(meta_f))
                    files_added += 1

            # Add RESEARCH summaries/JSONs (a single unreadable file must not
            # invalidate the whole archive any more)
            if P.RESEARCH_DIR.exists():
                research_files = list(P.RESEARCH_DIR.glob("**/*.json"))
                for i, rf in enumerate(research_files):
                    try:
                        zf.write(rf, _archive_name(rf))
                        files_added += 1
                    except Exception as e:
                        skipped.append(f"{rf.name}: {e}")
                    if research_files and (i % 50 == 0):
                        _report("PACKAGE", f"packaging research files ({i}/{len(research_files)})",
                                50.0 + 45.0 * (i / max(1, len(research_files))))

        size_bytes = zip_path.stat().st_size
        log.info("backup created: %s (%d files, %d bytes, %d skipped)",
                 zip_path.name, files_added, size_bytes, len(skipped))
    except Exception as e:
        log.error("backup packaging failed: %s", e)
        try:
            if zip_path.exists():
                zip_path.unlink()   # never leave a truncated archive behind
        except Exception:
            pass
        return {"ok": False, "stage": "PACKAGE", "filename": None,
                "error": f"backup packaging failed: {type(e).__name__}: {e}"}
    finally:
        if temp_db_path.exists():
            temp_db_path.unlink()

    _report("DONE", "backup complete", 100.0)
    return {
        "ok": True,
        "filename": zip_path.name,
        "path": str(zip_path),
        "size_bytes": size_bytes,
        "size_mb": round(size_bytes / (1024 * 1024), 2),
        "files_count": files_added,
        "skipped_files": skipped[:10],
        "skipped_count": len(skipped),
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
