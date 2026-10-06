"""
Persistent Manifest System for V2.7 Data Architecture.

Maintains metadata manifests in DATA/manifests/:
  - datasets.json: All persisted raw and normalized datasets
  - features.json: All precomputed feature caches with schema versioning
  - research.json: Research nodes, genomes, and experiments

All stored paths are relative to DATA_ROOT for complete disk and path portability.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import paths as P

log = logging.getLogger("data.manifest")
_lock = threading.RLock()


def _read_json_safe(path: Path, default_factory) -> Dict[str, Any]:
    if not path.exists():
        return default_factory()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception as e:
        log.warning("Failed to read manifest %s: %s; recreating defaults", path, e)
    return default_factory()


def _write_json_atomic(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    shutil.move(str(tmp), str(path))


def get_datasets_manifest() -> Dict[str, Any]:
    with _lock:
        return _read_json_safe(
            P.DATASETS_MANIFEST,
            lambda: {"version": 1, "updated_at": time.time(), "datasets": {}},
        )


def update_dataset_entry(dataset_key: str, entry: Dict[str, Any]) -> None:
    with _lock:
        manifest = get_datasets_manifest()
        datasets = manifest.setdefault("datasets", {})
        existing = datasets.get(dataset_key, {})
        existing.update(entry)
        existing["last_updated"] = time.time()
        datasets[dataset_key] = existing
        manifest["updated_at"] = time.time()
        _write_json_atomic(P.DATASETS_MANIFEST, manifest)


def get_features_manifest() -> Dict[str, Any]:
    with _lock:
        return _read_json_safe(
            P.FEATURES_MANIFEST,
            lambda: {"version": 1, "updated_at": time.time(), "features": {}},
        )


def update_feature_entry(feature_key: str, entry: Dict[str, Any]) -> None:
    with _lock:
        manifest = get_features_manifest()
        features = manifest.setdefault("features", {})
        existing = features.get(feature_key, {})
        existing.update(entry)
        existing["last_updated"] = time.time()
        features[feature_key] = existing
        manifest["updated_at"] = time.time()
        _write_json_atomic(P.FEATURES_MANIFEST, manifest)


def get_research_manifest() -> Dict[str, Any]:
    with _lock:
        return _read_json_safe(
            P.RESEARCH_MANIFEST,
            lambda: {
                "version": 1,
                "updated_at": time.time(),
                "nodes": {},
                "genomes": {},
                "experiments": {},
            },
        )


def update_research_entry(category: str, key: str, entry: Dict[str, Any]) -> None:
    with _lock:
        manifest = get_research_manifest()
        cat_dict = manifest.setdefault(category, {})
        existing = cat_dict.get(key, {})
        existing.update(entry)
        existing["last_updated"] = time.time()
        cat_dict[key] = existing
        manifest["updated_at"] = time.time()
        _write_json_atomic(P.RESEARCH_MANIFEST, manifest)


def sync_manifests_from_disk() -> Dict[str, int]:
    """Scan disk and register any unindexed datasets or features into manifests."""
    with _lock:
        P.ensure_layout()
        ds_manifest = get_datasets_manifest()
        ft_manifest = get_features_manifest()
        datasets = ds_manifest.setdefault("datasets", {})
        features = ft_manifest.setdefault("features", {})
        ds_count = 0
        ft_count = 0

        # Scan DATA/MT5/raw and DATA/MT5/normalized
        for f in sorted(P.MT5_RAW_DIR.glob("*.parquet")):
            key = f.stem
            rel = P.data_relative_path(f)
            if key not in datasets or "raw_path" not in datasets[key]:
                datasets.setdefault(key, {})["raw_path"] = rel
                datasets[key]["file_size_bytes"] = f.stat().st_size
                datasets[key]["last_updated"] = f.stat().st_mtime
                ds_count += 1

        for f in sorted(P.MT5_NORMALIZED_DIR.glob("*.parquet")):
            key = f.stem
            rel = P.data_relative_path(f)
            datasets.setdefault(key, {})["normalized_path"] = rel
            ds_count += 1

        # Scan DATA/features
        for f in sorted(P.FEATURES_DIR.glob("*.parquet")):
            key = f.stem
            rel = P.data_relative_path(f)
            if key not in features:
                features[key] = {
                    "feature_key": key,
                    "relative_path": rel,
                    "file_size_bytes": f.stat().st_size,
                    "last_updated": f.stat().st_mtime,
                }
                ft_count += 1

        if ds_count > 0:
            ds_manifest["updated_at"] = time.time()
            _write_json_atomic(P.DATASETS_MANIFEST, ds_manifest)
        if ft_count > 0:
            ft_manifest["updated_at"] = time.time()
            _write_json_atomic(P.FEATURES_MANIFEST, ft_manifest)

        return {"indexed_datasets": ds_count, "indexed_features": ft_count}
