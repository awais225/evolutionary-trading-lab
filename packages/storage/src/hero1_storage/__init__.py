"""Hero1 storage layer.

Two database classes, by design:

* **Registry DB** (``DATA/DATABASE/hero1_registry.db``) — one small global database:
  instruments, application settings, global events.
* **Per-instrument DBs** (``DATA/<SYMBOL>/DATABASE/hero1.db``) — one independent
  SQLite database per instrument (XAUUSD / EURUSD / BTCUSD): nodes, lineage,
  backtests, deep tests, research, live testing, approvals, activity events.

Isolation rules enforced here:

1. Instrument-specific queries ALWAYS carry an explicit instrument scope
   (repository constructed with a validated instrument context).
2. No query may silently default to a symbol.
3. Foreign keys are enforced (``PRAGMA foreign_keys=ON``) on every connection.
4. DATA never lives inside the Git repository (enforced by ``engine.data_root``).
"""

from .enums import (
    ApprovalState, EventCategory, EventSeverity, NodeOrigin, NodeStatus,
)
from .engine import (
    DataRootError, InstrumentContextError, data_root, registry_url, instrument_url,
    instrument_db_path, registry_db_path, reset_engine_caches,
    ensure_outside_repository, INSTRUMENT_SUBDIRS,
)
from .init import initialize_databases, InitializationReport

__all__ = [
    "ApprovalState", "EventCategory", "EventSeverity", "NodeOrigin", "NodeStatus",
    "DataRootError", "InstrumentContextError", "data_root", "registry_url",
    "instrument_url", "instrument_db_path", "registry_db_path",
    "reset_engine_caches", "ensure_outside_repository", "INSTRUMENT_SUBDIRS",
    "initialize_databases", "InitializationReport",
]
