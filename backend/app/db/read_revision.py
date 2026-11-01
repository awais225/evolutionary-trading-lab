"""Coherent, conservative read-cache identity; no schema writes/triggers.

All token components and the protected reads are captured in ONE savepoint on
the existing locked connection. Bypass caches inside caller-owned transactions:
SQLite total_changes does not rewind on rollback. External commits, schema
changes, local writes and connection replacement invalidate committed entries.
"""
from contextlib import contextmanager


def token(db, version):
    return (db.conn, db.one('PRAGMA data_version')['data_version'],
            db.conn.total_changes, db.one('PRAGMA schema_version')['schema_version'], version)


@contextmanager
def revision_snapshot(db, version):
    # Check the outer transaction before our own SAVEPOINT starts one.
    from ..read_diagnostics import phase
    with phase('db.lock_wait'):
        db._lock.acquire()
    try:
        cacheable = not db.conn.in_transaction
        with db.read_snapshot():
            # Establish a MAIN-database read transaction before sampling any
            # token components. A concurrent WAL commit must not pair an old
            # data_version with newer header/aggregate rows.
            db.one('SELECT rootpage FROM sqlite_master LIMIT 1')
            yield token(db, version) if cacheable else None
    finally:
        db._lock.release()
