"""
Feature Engine — precomputes, caches, and incrementally updates feature arrays (V2).

Strategies consume cached arrays; a strategy NEVER recomputes EMA/RSI/ATR
itself. Cache layers:
  1. in-memory LRU keyed by (dataset_id, spec)
  2. parquet on disk (features/{dataset_id}.parquet) for cold restarts
  3. algorithm-hash & engine-version validation (spec §8): changing an indicator's
     code invalidates only that feature's cache
  4. incremental feature calculation (spec §8): when new bars arrive, only
     the new region plus indicator warm-up lookback is computed.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .. import paths as P
from ..activity import activity
from ..config import get_config
from ..data import manifest, storage
from ..data.engine import get_data_engine
from ..db.database import get_db
from ..versions import FEATURE_ENGINE_VERSION, algo_hash
from . import library

log = logging.getLogger("features.engine")

MAX_CACHE_ENTRIES = 512
DEFAULT_FEATURE_SET_ID = "core_v1"
DEFAULT_SCHEMA_VERSION = "core_v1"


def _warmup_bars_for_spec(spec: str, args: tuple) -> int:
    """Heuristic warmup dependency length in bars for an indicator."""
    nums = []
    for a in args:
        try:
            nums.append(int(float(a)))
        except (ValueError, TypeError):
            pass
    max_param = max(nums) if nums else 20
    # EWM/RMA indicators require ~3-4x span to converge to 99.9%
    return max(150, min(max_param * 4, 1500))


class FeatureEngine:
    def __init__(self):
        self._cache: "OrderedDict[tuple, np.ndarray]" = OrderedDict()
        self._lock = threading.RLock()
        self._disk_loaded: set[str] = set()

    # ---------- core & invalidation (spec §8, V2.7) ----------
    def _load_disk_cache(
        self,
        dataset_id: str,
        feature_set_id: str = DEFAULT_FEATURE_SET_ID,
        schema_version: str = DEFAULT_SCHEMA_VERSION,
    ) -> None:
        if dataset_id in self._disk_loaded:
            return

        cfg = get_config()
        # V2.7 persistent features: check DATA/features/{dataset_id}__{feature_set_id}__{schema_version}.parquet
        v27_feat_path = P.FEATURES_DIR / f"{dataset_id}__{feature_set_id}__{schema_version}.parquet"
        v27_feat_simple = P.FEATURES_DIR / f"{dataset_id}.parquet"

        df = None
        reused_path = None
        if v27_feat_path.exists():
            try:
                df = pd.read_parquet(v27_feat_path)
                reused_path = v27_feat_path
            except Exception as e:
                log.warning("failed to read v2.7 feature parquet %s: %s", v27_feat_path, e)

        if df is None and v27_feat_simple.exists():
            try:
                df = pd.read_parquet(v27_feat_simple)
                reused_path = v27_feat_simple
            except Exception as e:
                log.warning("failed to read v2.7 simple feature parquet %s: %s", v27_feat_simple, e)

        if df is None:
            df = storage.read_feature_cache(cfg.data.cache_dir, dataset_id)
            if df is not None:
                reused_path = Path(cfg.data.cache_dir) / "features" / f"{dataset_id}.parquet"

        if df is not None and not df.empty:
            msg = f"ACTION: REUSING EXISTING FEATURES ({feature_set_id}, {schema_version}) for {dataset_id} ({len(df.columns)} columns, {len(df)} rows)"
            log.info(msg)
            activity.info("FEATURES", msg)

            db = get_db()
            meta_rows = {r["column_name"]: r for r in db.q(
                "SELECT * FROM feature_meta WHERE master_key=?", (dataset_id,))}

            with self._lock:
                for col in df.columns:
                    # Invalidation check: if metadata exists, verify engine version & algo hash
                    m = meta_rows.get(col)
                    if m:
                        if m["engine_version"] != FEATURE_ENGINE_VERSION:
                            log.info("invalidating feature %s for %s: engine version mismatch (%s != %s)",
                                     col, dataset_id, m["engine_version"], FEATURE_ENGINE_VERSION)
                            continue
                        # If algo hash recorded, check current implementation
                        try:
                            fn, _ = library.parse_spec(col)
                            current_hash = algo_hash(fn)
                            if m["algo_hash"] and m["algo_hash"] != current_hash:
                                log.info("invalidating feature %s for %s: algorithm code changed",
                                         col, dataset_id)
                                continue
                        except Exception:
                            pass

                    self._put((dataset_id, col), df[col].to_numpy(np.float64))

                if "close" in df.columns and "price" not in df.columns:
                    self._put((dataset_id, "price"), df["close"].to_numpy(np.float64))
                elif "price" in df.columns and "close" not in df.columns:
                    self._put((dataset_id, "close"), df["price"].to_numpy(np.float64))

            # Update features manifest if not indexed
            feat_key = f"{dataset_id}__{feature_set_id}__{schema_version}"
            manifest.update_feature_entry(feat_key, {
                "dataset_id": dataset_id,
                "feature_set_id": feature_set_id,
                "schema_version": schema_version,
                "columns": list(df.columns),
                "column_count": len(df.columns),
                "rows": len(df),
                "relative_path": P.data_relative_path(reused_path or v27_feat_path),
            })

        self._disk_loaded.add(dataset_id)

    def _put(self, key: tuple, arr: np.ndarray) -> None:
        self._cache[key] = arr
        self._cache.move_to_end(key)
        while len(self._cache) > MAX_CACHE_ENTRIES:
            self._cache.popitem(last=False)

    def get(self, dataset_id: str, spec: str) -> np.ndarray:
        """Get one feature array by output name (e.g. 'rsi:14', 'ema:20')."""
        key = (dataset_id, spec)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]

        self._load_disk_cache(dataset_id)

        with self._lock:
            if key in self._cache:
                return self._cache[key]
            # Price alias fallback
            if spec == "price" and (dataset_id, "close") in self._cache:
                return self._cache[(dataset_id, "close")]
            if spec == "close" and (dataset_id, "price") in self._cache:
                return self._cache[(dataset_id, "price")]

        df = get_data_engine().get_frame(dataset_id)
        produced = self._compute_family(df, dataset_id, library.feature_family(spec))
        if spec not in produced:
            produced = self._compute_spec(df, dataset_id, spec)

        with self._lock:
            if key in self._cache:
                return self._cache[key]
            if spec == "price" and (dataset_id, "close") in self._cache:
                return self._cache[(dataset_id, "close")]
            if spec == "close" and (dataset_id, "price") in self._cache:
                return self._cache[(dataset_id, "price")]
        raise KeyError(f"feature '{spec}' not produced for dataset {dataset_id}")

    def _compute_family(self, df: pd.DataFrame, dataset_id: str, family: str) -> Dict[str, np.ndarray]:
        """Compute the default-parameter version of a family."""
        defaults = {"sma": "sma:20", "ema": "ema:20", "rsi": "rsi:14",
                    "macd": "macd:12:26:9", "roc": "roc:12", "momentum": "momentum:10",
                    "stoch": "stoch:14:3", "cci": "cci:20", "adx": "adx:14",
                    "atr": "atr:14", "bb": "bb:20:2.0", "volatility": "volatility:20",
                    "volume": "volume:20", "vwap": "vwap", "prev_day": "prev_day",
                    "time": "time", "regime": "regime", "sessions": "sessions",
                    "price": "price"}
        spec = defaults.get(family)
        if spec is None:
            raise KeyError(f"unknown feature family {family}")
        return self._compute_spec(df, dataset_id, spec)

    def _compute_spec(self, df: pd.DataFrame, dataset_id: str, spec: str) -> Dict[str, np.ndarray]:
        fn, args = library.parse_spec(spec)
        current_algo_hash = algo_hash(fn)
        n_total = len(df)

        # Check existing cached array on disk for incremental calculation (spec §8)
        cfg = get_config()
        existing_df = storage.read_feature_cache(cfg.data.cache_dir, dataset_id)
        out: Dict[str, np.ndarray] = {}

        # Can we compute incrementally?
        warmup = _warmup_bars_for_spec(spec, args)
        can_incremental = (
            existing_df is not None and
            spec in existing_df.columns and
            len(existing_df) < n_total and
            len(existing_df) > warmup
        )

        if can_incremental:
            n_old = len(existing_df)
            start_idx = max(0, n_old - warmup)
            df_slice = df.iloc[start_idx:].reset_index(drop=True)
            slice_out = fn(df_slice, *args)

            for name, tail_arr in slice_out.items():
                tail_arr = np.asarray(tail_arr, dtype=np.float64)
                new_tail = tail_arr[n_old - start_idx:]
                if name in existing_df.columns:
                    old_prefix = existing_df[name].to_numpy(dtype=np.float64)
                    full_arr = np.concatenate([old_prefix, new_tail])
                else:
                    full_arr = np.concatenate([np.full(n_old, np.nan, dtype=np.float64), new_tail])

                if len(full_arr) != n_total:
                    full_arr = full_arr[-n_total:] if len(full_arr) > n_total else np.pad(
                        full_arr, (n_total - len(full_arr), 0), constant_values=np.nan)
                out[name] = full_arr
            log.debug("incremental feature calc %s: %d old + %d new bars", spec, n_old, n_total - n_old)
        else:
            # Full computation on df
            raw_out = fn(df, *args)
            for name, arr in raw_out.items():
                out[name] = np.asarray(arr, dtype=np.float64)

        with self._lock:
            for name, arr in out.items():
                self._put((dataset_id, name), arr)

        self._persist(dataset_id, out, current_algo_hash)
        return out

    def _persist(self, dataset_id: str, out: Dict[str, np.ndarray], current_algo_hash: str, force: bool = False) -> None:
        """Append/update feature arrays and persist metadata in SQLite & DATA/features/."""
        if not force and os.environ.get("LAB_NO_FEATURE_PERSIST"):
            return   # worker processes must not write the shared cache

        cfg = get_config()
        db = get_db()
        now = time.time()
        try:
            existing = storage.read_feature_cache(cfg.data.cache_dir, dataset_id)
            n_rows = len(out[next(iter(out))])
            new_cols = {k: np.asarray(v, dtype=np.float32) for k, v in out.items()}

            all_cols = {}
            if existing is not None and len(existing) == n_rows:
                for c in existing.columns:
                    all_cols[c] = existing[c].to_numpy()

            # Incorporate memory cached features of this dataset matching n_rows
            with self._lock:
                for (d_id, col), arr in self._cache.items():
                    if d_id == dataset_id and len(arr) == n_rows and col not in all_cols:
                        all_cols[col] = np.asarray(arr, dtype=np.float32)

            all_cols.update(new_cols)

            storage.write_feature_cache(cfg.data.cache_dir, dataset_id, all_cols)
            df_to_save = pd.DataFrame(all_cols)

            # V2.7: Save to DATA/features/ with stable feature_set_id and schema_versioning (core_v1)
            try:
                feat_key = f"{dataset_id}__{DEFAULT_FEATURE_SET_ID}__{DEFAULT_SCHEMA_VERSION}"
                v27_feat_path = P.FEATURES_DIR / f"{feat_key}.parquet"
                v27_feat_simple = P.FEATURES_DIR / f"{dataset_id}.parquet"
                if df_to_save is not None:
                    if "close" in df_to_save.columns and "price" not in df_to_save.columns:
                        df_to_save["price"] = df_to_save["close"]
                    elif "price" in df_to_save.columns and "close" not in df_to_save.columns:
                        df_to_save["close"] = df_to_save["price"]
                    df_to_save.to_parquet(v27_feat_path, index=False)
                    try:
                        if v27_feat_simple.is_symlink():
                            v27_feat_simple.unlink()
                        df_to_save.to_parquet(v27_feat_simple, index=False)
                    except Exception:
                        pass
                    manifest.update_feature_entry(feat_key, {
                        "dataset_id": dataset_id,
                        "feature_set_id": DEFAULT_FEATURE_SET_ID,
                        "schema_version": DEFAULT_SCHEMA_VERSION,
                        "columns": list(df_to_save.columns),
                        "column_count": len(df_to_save.columns),
                        "rows": len(df_to_save),
                        "relative_path": P.data_relative_path(v27_feat_path),
                        "sha256": "",
                    })
            except Exception as e:
                log.warning("failed to write V2.7 persistent feature parquet: %s", e)

            # Record feature metadata (spec §8)
            meta_entries = []
            for name in out:
                meta_entries.append((
                    dataset_id, name, current_algo_hash, FEATURE_ENGINE_VERSION,
                    n_rows, 1, now
                ))
            db.xm("""INSERT OR REPLACE INTO feature_meta
                     (master_key, column_name, algo_hash, engine_version,
                      rows_covered, dataset_version, computed_at)
                     VALUES (?,?,?,?,?,?,?)""", meta_entries)
        except Exception as e:
            log.warning("feature cache persist failed for %s: %s", dataset_id, e)

    def get_many(self, dataset_id: str, specs: List[str]) -> Dict[str, np.ndarray]:
        return {s: self.get(dataset_id, s) for s in specs}

    def cached_features(self, dataset_id: str) -> List[str]:
        self._load_disk_cache(dataset_id)
        with self._lock:
            return sorted(k[1] for k in self._cache if k[0] == dataset_id)

    def has_spec(self, dataset_id: str, spec: str) -> bool:
        cached = set(self.cached_features(dataset_id))
        return library.is_spec_cached(spec, cached)

    def missing_specs(self, dataset_id: str, specs: List[str]) -> List[str]:
        cached = set(self.cached_features(dataset_id))
        return [s for s in specs if not library.is_spec_cached(s, cached)]

    def cache_stats(self) -> Dict:
        with self._lock:
            return {"entries": len(self._cache),
                    "max_entries": MAX_CACHE_ENTRIES,
                    "datasets": sorted({k[0] for k in self._cache})}


_engine: Optional[FeatureEngine] = None


def get_feature_engine() -> FeatureEngine:
    global _engine
    if _engine is None:
        _engine = FeatureEngine()
    return _engine
