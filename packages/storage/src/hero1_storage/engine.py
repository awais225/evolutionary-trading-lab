"""Engine/session factory and DATA-root resolution.

Rules (enforced, not aspirational):
  * Canonical DATA root (owner decision 2026-11-06): ``<repo>/DATA`` — absolute,
    git-ignored, inside the project root. ``HERO1_DATA_ROOT`` may override it.
  * Any OTHER path inside a Git repository checkout is rejected.
  * When unset, the default is ``<repo>/DATA`` (walk-up for ``.git``), falling
    back to ``~/.hero1/data`` only when no repository root can be located.
  * DATA contents are never created silently beyond the documented subdirs and
    are never committed (``DATA/`` is hard-ignored).
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Dict, Tuple

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

log = logging.getLogger("hero1.storage.engine")

INSTRUMENT_SUBDIRS = (
    "MARKET_DATA/M1", "MARKET_DATA/M5", "MARKET_DATA/M10", "MARKET_DATA/M15",
    "MARKET_DATA/M30", "MARKET_DATA/H1", "MARKET_DATA/H4", "MARKET_DATA/D1",
    "NODES/CUSTOM", "NODES/WWW", "NODES/INTERACTIVE", "NODES/FINE_TUNED",
    "DEEPTEST", "LIVE_TESTING", "RESEARCH", "DATABASE",
)
ROOT_SUBDIRS = (
    "DATABASE", "LOGS", "BACKUPS", "CACHE", "MARKET_DATA", "RESEARCH", "ARTIFACTS",
    "MT5/accounts", "MT5/symbols", "MT5/connection_logs",
)

KNOWN_INSTRUMENTS = ("XAUUSD", "EURUSD", "BTCUSD")


class DataRootError(RuntimeError):
    pass


class InstrumentContextError(RuntimeError):
    pass


def _repo_root_candidates() -> list[Path]:
    here = Path(__file__).resolve()
    candidates = [Path.cwd().resolve()]
    env_root = os.environ.get("HERO1_REPO_ROOT")
    if env_root:
        candidates.append(Path(env_root).resolve())
    # walk up from this file looking for a .git marker (handles src/ and tests/ layouts)
    for parent in here.parents:
        if (parent / ".git").exists():
            candidates.append(parent)
            break
    return candidates


def _is_canonical_data_dir(root: Path, repo: Path) -> bool:
    """The owner-mandated canonical DATA location is ``<repo>/DATA`` itself.

    It is git-ignored by the repository's hard rules, so research data there is
    never committed. Anything ELSE inside the checkout remains forbidden.
    """
    try:
        rel = root.relative_to(repo)
    except ValueError:
        return False
    return len(rel.parts) >= 1 and rel.parts[0].lower() == "data"


def ensure_outside_repository(root: Path) -> Path:
    root = root.expanduser().resolve()
    for repo in _repo_root_candidates():
        if repo == Path("/"):  # paranoia: never treat filesystem root as a repo boundary
            continue
        if _is_canonical_data_dir(root, repo):
            continue
        try:
            root.relative_to(repo)
        except ValueError:
            continue
        raise DataRootError(
            f"HERO1_DATA_ROOT resolves inside a repository checkout but is not the "
            f"canonical, git-ignored <repo>/DATA directory: {root} (inside {repo}). "
            "Point it at an external directory or at <repo>/DATA."
        )
    return root


def _canonical_default_root() -> Path:
    """Default DATA root when HERO1_DATA_ROOT is unset: <repo>/DATA when a
    repository checkout is detectable, else ~/.hero1/data."""
    env_root = os.environ.get("HERO1_REPO_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve() / "DATA"
    for repo in _repo_root_candidates():
        if repo != Path.cwd().resolve() or (repo / ".git").exists():
            if (repo / ".git").exists() or repo == Path.cwd().resolve():
                return repo / "DATA"
    return Path.home() / ".hero1" / "data"


def data_root() -> Path:
    configured = os.environ.get("HERO1_DATA_ROOT", "").strip()
    if configured:
        root = Path(configured).expanduser()
        if not root.is_absolute():
            raise DataRootError(f"HERO1_DATA_ROOT must be absolute, got: {configured!r}")
        return ensure_outside_repository(root)
    default = _canonical_default_root()
    log.warning(
        "HERO1_DATA_ROOT is not set; using default DATA root: %s", default
    )
    return default


def registry_db_path(root: Path | None = None) -> Path:
    return (root or data_root()) / "DATABASE" / "hero1_registry.db"


def instrument_db_path(symbol: str, root: Path | None = None) -> Path:
    symbol = validate_symbol(symbol)
    return (root or data_root()) / symbol / "DATABASE" / "hero1.db"


def registry_url(root: Path | None = None) -> str:
    return f"sqlite:///{registry_db_path(root).as_posix()}"


def instrument_url(symbol: str, root: Path | None = None) -> str:
    return f"sqlite:///{instrument_db_path(symbol, root).as_posix()}"


def validate_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    if not s or len(s) > 16 or not all(ch.isalnum() or ch in "_-" for ch in s):
        raise InstrumentContextError(f"invalid instrument identifier: {symbol!r}")
    return s


def _configure_sqlite(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
        except Exception:  # in-memory or read-only — fine
            pass
        cursor.close()


_engines: Dict[str, Engine] = {}
_sessionmakers: Dict[str, sessionmaker] = {}
_lock = threading.RLock()


def get_engine(url: str) -> Engine:
    with _lock:
        eng = _engines.get(url)
        if eng is None:
            eng = create_engine(url, future=True, pool_pre_ping=True)
            if url.startswith("sqlite"):
                _configure_sqlite(eng)
            _engines[url] = eng
        return eng


def get_session_factory(url: str) -> sessionmaker:
    with _lock:
        factory = _sessionmakers.get(url)
        if factory is None:
            engine = get_engine(url)
            factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
            _sessionmakers[url] = factory
        return factory


def reset_engine_caches() -> None:
    with _lock:
        for eng in _engines.values():
            eng.dispose()
        _engines.clear()
        _sessionmakers.clear()


def registry_session() -> Session:
    return get_session_factory(registry_url())()


def instrument_session(symbol: str) -> Session:
    return get_session_factory(instrument_url(symbol))()
