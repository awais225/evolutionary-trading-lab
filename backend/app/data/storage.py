"""
Parquet/DuckDB storage for historical datasets and feature caches (V2).

Maintains backward compatibility with V1 flat paths while supporting V2
DATA/{symbol}/ hierarchy and legacy-migrated locations (spec §1, §3).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .. import paths as P

log = logging.getLogger("data.storage")

COLUMNS = ["ts", "open", "high", "low", "close", "bid", "ask", "spread",
           "tick_volume", "session", "dow", "dom", "hour", "minute"]


def dataset_dir(cache_root: str) -> Path:
    p = Path(cache_root)
    p.mkdir(parents=True, exist_ok=True)
    return p


def dataset_path(cache_root: str, dataset_id: str) -> Path:
    # 1. Direct path in cache_root
    direct = dataset_dir(cache_root) / f"{dataset_id}.parquet"
    if direct.exists():
        return direct

    # 2. Check DATA/{symbol}/raw/{tf}/legacy/ (migrated V1 layout)
    parts = dataset_id.split("_")
    if len(parts) >= 2:
        sym, tf = parts[0], parts[1]
        migrated = P.DATA_DIR / sym / "raw" / tf / "legacy" / f"{dataset_id}.parquet"
        if migrated.exists():
            return migrated

    return direct


def feature_path(cache_root: str, dataset_id: str) -> Path:
    # 1. Check DATA/features/ (V2.7 persistent layout)
    v27_p = P.FEATURES_DIR / f"{dataset_id}__core_v1__core_v1.parquet"
    if v27_p.exists():
        return v27_p
    simple_p = P.FEATURES_DIR / f"{dataset_id}.parquet"
    if simple_p.exists():
        return simple_p

    # 2. Direct path in cache_root/features
    direct = dataset_dir(cache_root) / "features" / f"{dataset_id}.parquet"
    if direct.exists():
        return direct

    # 3. Check DATA/{symbol}/features/{tf}/legacy/
    parts = dataset_id.split("_")
    if len(parts) >= 2:
        sym, tf = parts[0], parts[1]
        migrated = P.DATA_DIR / sym / "features" / tf / "legacy" / f"{dataset_id}.parquet"
        if migrated.exists():
            return migrated

    direct.parent.mkdir(parents=True, exist_ok=True)
    return direct


def write_bars(cache_root: str, dataset_id: str, df: pd.DataFrame) -> Path:
    p = dataset_path(cache_root, dataset_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(p)
    log.info("wrote dataset %s (%d bars) -> %s", dataset_id, len(df), p)
    return p


def read_dataset(cache_root: str, dataset_id: str) -> Optional[pd.DataFrame]:
    p = dataset_path(cache_root, dataset_id)
    if not p.exists():
        return None
    try:
        return pd.read_parquet(p)
    except Exception as e:
        log.warning("failed to read dataset %s: %s", p, e)
        return None


def write_feature_cache(cache_root: str, dataset_id: str, feats: dict[str, np.ndarray]) -> Path:
    """Persist precomputed feature arrays (float32) atomically."""
    p = feature_path(cache_root, dataset_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame({k: v.astype(np.float32) for k, v in feats.items()})
    tmp = p.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(p)
    return p


def read_feature_cache(cache_root: str, dataset_id: str) -> Optional[pd.DataFrame]:
    p = feature_path(cache_root, dataset_id)
    if not p.exists():
        return None
    try:
        return pd.read_parquet(p)
    except Exception as e:
        log.warning("feature cache read failed %s: %s", p, e)
        return None


def duckdb_query(sql: str, cache_root: str) -> pd.DataFrame:
    """Ad-hoc analytics over cached parquet via DuckDB."""
    import duckdb
    con = duckdb.connect()
    con.execute(f"CREATE VIEW datasets AS SELECT * FROM read_parquet('{dataset_dir(cache_root)}/*.parquet', union_by_name=true)")
    return con.execute(sql).df()
