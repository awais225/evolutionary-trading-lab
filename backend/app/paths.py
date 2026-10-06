"""
V3.5 Single Authoritative DATA Architecture (Phase 1).

All persistent application and research data is consolidated under DATA_ROOT:
    <app root>/
      DATA/
        BACKUPS/            compressed backup archives
        CACHE/              derived artifact cache (FEATURES, NODES, BACKTESTS, etc.)
        data_cache/         legacy and intermediate parquet datasets
        DATABASE/           lab_state.db (+ -wal/-shm)
        RESEARCH/           strategies, generations, backtests, validations, paper_trading
        TEST_DATA/          isolated simulator and test fixtures
        tests/              automated test suite
        logs/               persistent execution, diagnostic, and startup logs
        manifests/          datasets, features, and research manifests
        checkpoints/        engine state checkpoints
        metadata/           active run, pipeline state, migrations
        MT5/                authoritative real MT5 tick & bar partitions
        features/           precomputed parquet feature arrays

All persistent paths derive from one central DATA_ROOT. Migration is idempotent,
preserves historical research records, and guarantees complete portability:
copying DATA/ to a fresh installation restores the entire research lab.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Dict, List

log = logging.getLogger("paths")

ROOT_DIR = Path(__file__).resolve().parents[2]

# Central Authoritative DATA_ROOT
_env_data_root = os.environ.get("EVOLUTIONARY_LAB_DATA_ROOT")
if _env_data_root:
    DATA_ROOT = Path(_env_data_root).resolve()
else:
    DATA_ROOT = (ROOT_DIR / "DATA").resolve()

DATA_DIR = DATA_ROOT

# All persistent application & research directories derive strictly from DATA_ROOT
DATABASE_DIR = DATA_ROOT / "DATABASE"
RESEARCH_DIR = DATA_ROOT / "RESEARCH"
BACKUPS_DIR = DATA_ROOT / "BACKUPS"
TEST_DATA_DIR = DATA_ROOT / "TEST_DATA"
TEST_DATA_ROOT = TEST_DATA_DIR
SIMULATOR_DATA_DIR = TEST_DATA_DIR / "SIMULATOR_DATA"
CACHE_ROOT = DATA_ROOT / "CACHE"
DATA_CACHE_DIR = DATA_ROOT / "data_cache"
TESTS_DIR = DATA_ROOT / "tests"
LOGS_DIR = DATA_ROOT / "logs"
DATA_LOGS_DIR = LOGS_DIR

# Root-level configuration & source stay outside DATA
CONFIG_DIR = ROOT_DIR / "CONFIG"

# Cache subdirectories inside DATA_ROOT / CACHE
CACHE_FEATURES_DIR = CACHE_ROOT / "FEATURES"
CACHE_NODES_DIR = CACHE_ROOT / "NODES"
CACHE_GENOMES_DIR = CACHE_ROOT / "GENOMES"
CACHE_BACKTESTS_DIR = CACHE_ROOT / "BACKTESTS"
CACHE_RESULTS_DIR = CACHE_ROOT / "RESULTS"
CACHE_CHECKPOINTS_DIR = CACHE_ROOT / "CHECKPOINTS"
CACHE_MANIFESTS_DIR = CACHE_ROOT / "MANIFESTS"

# Authoritative Real MT5 Data Directories inside DATA
MT5_DIR = DATA_DIR / "MT5"
MT5_XAUUSD_DIR = MT5_DIR / "XAUUSD"
MT5_RAW_DIR = MT5_DIR / "raw"
MT5_NORMALIZED_DIR = MT5_DIR / "normalized"
MT5_DATASETS_DIR = MT5_DIR / "datasets"
MT5_METADATA_DIR = MT5_DIR / "metadata"

NODES_DIR = DATA_DIR / "nodes"
GENOMES_DIR = DATA_DIR / "genomes"
FEATURES_DIR = DATA_DIR / "features"
DATASETS_DIR = DATA_DIR / "datasets"
RESEARCH_DATA_DIR = RESEARCH_DIR
CACHE_DIR = CACHE_ROOT
MANIFESTS_DIR = DATA_DIR / "manifests"
METADATA_DIR = DATA_DIR / "metadata"
RESULTS_DIR = DATA_DIR / "results"
BACKTESTS_DATA_DIR = RESEARCH_DIR / "backtests"
VALIDATIONS_DATA_DIR = RESEARCH_DIR / "validations"
GENERATIONS_DATA_DIR = RESEARCH_DIR / "generations"
DATABASE_DATA_DIR = DATABASE_DIR
CHECKPOINTS_DIR = DATA_DIR / "checkpoints"
EXPORTS_DIR = DATA_DIR / "exports"

# Manifest file paths
DATA_MANIFEST = DATA_DIR / "manifest.json"
DATASETS_MANIFEST = MANIFESTS_DIR / "datasets.json"
FEATURES_MANIFEST = MANIFESTS_DIR / "features.json"
RESEARCH_MANIFEST = MANIFESTS_DIR / "research.json"
LEDGER_FILE = NODES_DIR / "node_ledger.jsonl"

CACHE_SUBDIRS = (
    CACHE_FEATURES_DIR,
    CACHE_NODES_DIR,
    CACHE_GENOMES_DIR,
    CACHE_BACKTESTS_DIR,
    CACHE_RESULTS_DIR,
    CACHE_CHECKPOINTS_DIR,
    CACHE_MANIFESTS_DIR,
)

DATA_V35_SUBDIRS = (
    DATABASE_DIR,
    RESEARCH_DIR,
    TEST_DATA_DIR,
    SIMULATOR_DATA_DIR,
    BACKUPS_DIR,
    CACHE_ROOT,
    DATA_CACHE_DIR,
    TESTS_DIR,
    LOGS_DIR,
    MT5_RAW_DIR,
    MT5_NORMALIZED_DIR,
    MT5_DATASETS_DIR,
    MT5_METADATA_DIR,
    MT5_XAUUSD_DIR,
    NODES_DIR,
    GENOMES_DIR,
    FEATURES_DIR,
    DATASETS_DIR,
    MANIFESTS_DIR,
    METADATA_DIR,
    RESULTS_DIR,
    CHECKPOINTS_DIR,
    EXPORTS_DIR,
)
DATA_V27_SUBDIRS = DATA_V35_SUBDIRS

RESEARCH_SUBDIRS = ("strategies", "generations", "experiments", "backtests",
                    "validations", "paper_trading", "hypotheses")

# V1 locations (legacy fallbacks)
_V1_DB = ROOT_DIR / "lab_state.db"
_V1_LOG = ROOT_DIR / "lab.log"
_V1_CONFIG_DIR = ROOT_DIR / "config"
_V1_CACHE = DATA_CACHE_DIR

_DATASET_ID_RE = re.compile(
    r"^(?P<symbol>[A-Z0-9]+)_(?P<tf>M1|M5|M15|M30|H1|H4|D1)_"
    r"(?P<start>\d{8})_(?P<end>\d{8})_(?P<source>[A-Z0-9_]+)$")


def ensure_layout() -> None:
    """Create the V3.5 directory tree inside DATA_ROOT (idempotent)."""
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    for d in (DATABASE_DIR, RESEARCH_DIR, BACKUPS_DIR, TEST_DATA_DIR, SIMULATOR_DATA_DIR, CACHE_ROOT, DATA_CACHE_DIR, TESTS_DIR, LOGS_DIR, CONFIG_DIR):
        d.mkdir(parents=True, exist_ok=True)
    for sub in RESEARCH_SUBDIRS:
        (RESEARCH_DIR / sub).mkdir(parents=True, exist_ok=True)
    for d in CACHE_SUBDIRS:
        d.mkdir(parents=True, exist_ok=True)
    for d in DATA_V35_SUBDIRS:
        d.mkdir(parents=True, exist_ok=True)


def data_relative_path(path: Path | str) -> str:
    """Return path relative to DATA_ROOT for manifests and portable configs."""
    try:
        p = Path(path).resolve()
        return str(p.relative_to(DATA_ROOT.resolve())).replace(os.sep, "/")
    except (ValueError, OSError):
        return str(path).replace(os.sep, "/")


def resolve_data_path(rel_path: str) -> Path:
    """Resolve a DATA_ROOT-relative path to an absolute Path."""
    p = Path(rel_path)
    return p if p.is_absolute() else (DATA_ROOT / p)


def symbol_dirs(symbol: str) -> Dict[str, Path]:
    base = DATA_DIR / symbol
    out = {"raw": base / "raw", "features": base / "features",
           "metadata": base / "metadata", "quarantine": base / "quarantine"}
    for p in out.values():
        p.mkdir(parents=True, exist_ok=True)
    return out


def _move(src: Path, dst: Path) -> bool:
    if not src.exists() or dst.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return True


def migrate_v1_database() -> List[str]:
    """Move legacy SQLite db (+wal/shm) into DATA/DATABASE/ — never copy fresh."""
    moved = []
    dst_db = DATABASE_DIR / "lab_state.db"
    legacy_root_db = ROOT_DIR / "DATABASE" / "lab_state.db"
    if legacy_root_db.exists() and not dst_db.exists() and legacy_root_db.resolve() != dst_db.resolve():
        for suffix in ("", "-wal", "-shm"):
            src = Path(str(legacy_root_db) + suffix)
            if src.exists():
                _move(src, DATABASE_DIR / src.name)
                moved.append(src.name)
    if _V1_DB.exists() and not dst_db.exists():
        for suffix in ("", "-wal", "-shm"):
            src = Path(str(_V1_DB) + suffix)
            if src.exists():
                _move(src, DATABASE_DIR / src.name)
                moved.append(src.name)
    return moved


def migrate_v1_config() -> List[str]:
    """Move config/lab_config.yaml into CONFIG/ (keeps user settings)."""
    moved = []
    src = _V1_CONFIG_DIR / "lab_config.yaml"
    if src.exists() and not (CONFIG_DIR / "lab_config.yaml").exists():
        _move(src, CONFIG_DIR / "lab_config.yaml")
        moved.append("lab_config.yaml")
    return moved


def config_file() -> Path:
    """Preferred config location: CONFIG/ (V2) then config/ (V1, pre-migration)."""
    v2 = CONFIG_DIR / "lab_config.yaml"
    if v2.exists():
        return v2
    v1 = _V1_CONFIG_DIR / "lab_config.yaml"
    if v1.exists():
        return v1
    return v2


def database_file() -> Path:
    """Preferred db location: DATA/DATABASE/lab_state.db then fallbacks."""
    v35 = DATABASE_DIR / "lab_state.db"
    if v35.exists():
        return v35
    v35_lower = DATA_ROOT / "database" / "lab_state.db"
    if v35_lower.exists():
        return v35_lower
    legacy_root = ROOT_DIR / "DATABASE" / "lab_state.db"
    if legacy_root.exists():
        return legacy_root
    return v35


def log_file(name: str = "lab.log") -> Path:
    return LOGS_DIR / name


def migrate_v1_data(rewrite_registry=None) -> Dict:
    """Move V1 data_cache snapshots/features into DATA/{symbol}/.../legacy/."""
    report = {"moved_bars": 0, "moved_features": 0, "skipped": 0, "renamed": []}
    if not _V1_CACHE.exists():
        return report
    for f in sorted(_V1_CACHE.glob("*.parquet")):
        m = _DATASET_ID_RE.match(f.stem)
        if not m:
            report["skipped"] += 1
            continue
        sym, tf = m.group("symbol"), m.group("tf")
        dst = DATA_DIR / sym / "raw" / tf / "legacy" / f.name
        old = str(f)
        if _move(f, dst):
            report["moved_bars"] += 1
            report["renamed"].append((old, str(dst)))
    feat_dir = _V1_CACHE / "features"
    if feat_dir.exists():
        for f in sorted(feat_dir.glob("*.parquet")):
            m = _DATASET_ID_RE.match(f.stem)
            if not m:
                report["skipped"] += 1
                continue
            sym, tf = m.group("symbol"), m.group("tf")
            dst = DATA_DIR / sym / "features" / tf / "legacy" / f.name
            old = str(f)
            if _move(f, dst):
                report["moved_features"] += 1
                report["renamed"].append((old, str(dst)))
    manifest = DATA_DIR / "metadata_migration_v1.json"
    if report["renamed"] and not manifest.exists():
        manifest.write_text(json.dumps({
            "migrated_at": time.time(), "moves": report["renamed"]}, indent=1))
    return report


def migrate_v1_log() -> List[str]:
    moved = []
    if _V1_LOG.exists() and not (LOGS_DIR / "lab_v1.log").exists():
        _move(_V1_LOG, LOGS_DIR / "lab_v1.log")
        moved.append("lab.log")
    return moved


def run_all_migrations(rewrite_registry=None) -> Dict:
    """Idempotent file-layout migration (safe to call every startup)."""
    ensure_layout()
    out = {
        "database": migrate_v1_database(),
        "config": migrate_v1_config(),
        "log": migrate_v1_log(),
        "data": migrate_v1_data(rewrite_registry=rewrite_registry),
    }
    if any(out[k] for k in ("database", "config", "log")) or out["data"]["moved_bars"]:
        log.info("Layout migration: %s", json.dumps(
            {k: (v if k != "data" else {kk: vv for kk, vv in v.items() if kk != "renamed"})
             for k, v in out.items()}))
    return out
