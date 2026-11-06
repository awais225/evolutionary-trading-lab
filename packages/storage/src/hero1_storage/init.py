"""Database initialization and health checks.

Read-only by default; pass ``create=True`` to create directories, run Alembic
migrations to ``head`` and seed the three instruments (idempotent).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Sequence

from sqlalchemy import inspect, text

from .engine import (
    INSTRUMENT_SUBDIRS, KNOWN_INSTRUMENTS, ROOT_SUBDIRS,
    data_root, ensure_outside_repository, get_engine,
    instrument_db_path, instrument_url, registry_db_path, registry_url,
    reset_engine_caches, validate_symbol,
)

log = logging.getLogger("hero1.storage.init")


@dataclass
class InitializationReport:
    data_root: str
    created: bool
    registry: Dict = field(default_factory=dict)
    instruments: Dict[str, Dict] = field(default_factory=dict)
    ok: bool = False

    def to_dict(self) -> dict:
        return {
            "data_root": self.data_root,
            "created": self.created,
            "ok": self.ok,
            "registry": self.registry,
            "instruments": self.instruments,
        }


def _alembic_upgrade(script_dir: Path, url: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(script_dir))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")


def _table_names(url: str) -> list[str]:
    engine = get_engine(url)
    with engine.connect() as conn:
        return sorted(inspect(conn).get_table_names())


def _alembic_dir(which: str) -> Path:
    return Path(__file__).resolve().parent / "alembic" / which


def _seed_instruments(root: Path) -> None:
    from . import repositories as repos
    from .engine import get_session_factory

    SEED = (
        ("XAUUSD", "Gold Spot US Dollar", "metal"),
        ("EURUSD", "Euro US Dollar", "fx"),
        ("BTCUSD", "Bitcoin US Dollar", "crypto"),
    )
    factory = get_session_factory(registry_url(root))
    with factory() as s:
        r = repos.InstrumentRepository(s)
        for symbol, display, asset_class in SEED:
            if r.get(symbol) is None:
                from .registry_models import Instrument
                s.add(Instrument(symbol=symbol, display_name=display, asset_class=asset_class,
                                 enabled=True, default_timeframe="M15"))
        s.commit()


def initialize_databases(root: Optional[Path] = None, *, create: bool = False,
                         instruments: Sequence[str] = KNOWN_INSTRUMENTS) -> InitializationReport:
    reset_engine_caches()
    root = ensure_outside_repository(Path(root).expanduser().resolve()) if root else data_root()
    syms = [validate_symbol(s) for s in instruments]
    report = InitializationReport(data_root=str(root), created=create)

    if create:
        (root / "DATABASE").mkdir(parents=True, exist_ok=True)
        for sub in ROOT_SUBDIRS:
            (root / sub).mkdir(parents=True, exist_ok=True)
        for sym in syms:
            for sub in INSTRUMENT_SUBDIRS:
                (root / sym / sub).mkdir(parents=True, exist_ok=True)
        _alembic_upgrade(_alembic_dir("registry"), registry_url(root))
        _seed_instruments(root)
        for sym in syms:
            (root / sym / "DATABASE").mkdir(parents=True, exist_ok=True)
            _alembic_upgrade(_alembic_dir("instrument"), instrument_url(sym, root))
        reset_engine_caches()

    # ---- read-only verification (also used by `doctor`) ----
    try:
        tables = _table_names(registry_url(root))
        report.registry = {
            "status": "healthy",
            "path": str(registry_db_path(root)),
            "tables": tables,
        }
    except Exception as exc:  # noqa: BLE001 - report, never raise from doctor path
        report.registry = {"status": "unavailable" if not registry_db_path(root).exists() else "error",
                           "path": str(registry_db_path(root)), "detail": str(exc)}

    ok = report.registry.get("status") == "healthy"
    for sym in syms:
        path = instrument_db_path(sym, root)
        try:
            tables = _table_names(instrument_url(sym, root))
            report.instruments[sym] = {"status": "healthy", "path": str(path), "tables": tables}
        except Exception as exc:  # noqa: BLE001
            report.instruments[sym] = {"status": "unavailable" if not path.exists() else "error",
                                       "path": str(path), "detail": str(exc)}
        ok = ok and report.instruments[sym]["status"] == "healthy"

    report.ok = ok
    return report
