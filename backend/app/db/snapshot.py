"""
Safe SQLite snapshot helper (V4.7 reliability hardening).

Why this module exists
----------------------
The project has historically seen intermittent ``database disk image is
malformed`` errors. One of the ways that can actually be produced by application
code is copying a *live* SQLite database file with ``shutil.copy2``: in WAL mode
the newest committed pages live in ``-wal`` and a plain file copy can capture a
database whose header/sidecars disagree. The correct way to copy a live SQLite
database is the online backup API (or ``VACUUM INTO``), which produces a
transactionally consistent snapshot including any committed WAL content.

Everything in the application that needs to copy a database now goes through
:func:`sqlite_snapshot`.

The helper is deliberately dependency-free and never mutates the source.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, Union

log = logging.getLogger("db.snapshot")

PathLike = Union[str, os.PathLike]


def sqlite_snapshot(src: PathLike, dst: PathLike, *, timeout: float = 30.0,
                    pages: int = 1024) -> Dict[str, Any]:
    """Write a consistent snapshot of database ``src`` to ``dst``.

    The source is opened read-only, so a snapshot can never modify (or create)
    it. The destination is written to ``<dst>.tmp`` first and then moved into
    place, so a failure can never leave a half-written database behind that a
    later open would report as malformed.
    """
    src_p = Path(src)
    dst_p = Path(dst)
    if not src_p.exists():
        raise FileNotFoundError(f"database not found: {src_p}")
    dst_p.parent.mkdir(parents=True, exist_ok=True)
    tmp_p = dst_p.with_suffix(dst_p.suffix + ".tmp")
    if tmp_p.exists():
        tmp_p.unlink()

    src_conn = None
    dst_conn = None
    try:
        src_conn = sqlite3.connect(f"file:{src_p.as_posix()}?mode=ro", uri=True, timeout=timeout)
        src_conn.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
        dst_conn = sqlite3.connect(str(tmp_p))
        # Online backup API: consistent snapshot, includes committed WAL content,
        # copied in bounded pages so a large database does not block readers.
        with dst_conn:
            src_conn.backup(dst_conn, pages=pages)
        dst_conn.close()
        dst_conn = None
        src_conn.close()
        src_conn = None
        os.replace(str(tmp_p), str(dst_p))
        return {
            "ok": True,
            "source": str(src_p),
            "target": str(dst_p),
            "size_bytes": dst_p.stat().st_size,
            "method": "sqlite_backup_api",
        }
    except Exception as e:  # never leave a partial snapshot behind
        log.warning("sqlite snapshot failed (%s -> %s): %s", src_p, dst_p, e)
        for conn in (dst_conn, src_conn):
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass
        if tmp_p.exists():
            try:
                tmp_p.unlink()
            except Exception:
                pass
        raise


def wal_sidecar_status(db_path: PathLike) -> Dict[str, Any]:
    """Describe the on-disk journal state of a database (diagnostics only)."""
    p = Path(db_path)
    out: Dict[str, Any] = {"path": str(p), "exists": p.exists()}
    if p.exists():
        st = p.stat()
        out.update({"size_bytes": st.st_size, "inode": st.st_ino, "mtime": st.st_mtime})
    for suffix in ("-wal", "-shm"):
        side = Path(str(p) + suffix)
        out[suffix] = {"exists": side.exists(),
                       "size_bytes": side.stat().st_size if side.exists() else 0}
    return out
