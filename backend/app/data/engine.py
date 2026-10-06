"""
Data Engine — incremental ingestion, normalization, integrity, and caching (V2).

Pipeline:
  1. Master sync: requests ONLY missing range from MT5/Simulator (spec §4)
  2. Data integrity validation (spec §7): flag, log, quarantine invalid rows
  3. Deduplication & conflict detection (spec §5)
  4. DST-aware session labeling (spec §43)
  5. Persistent master store per (symbol, timeframe, broker, server) (spec §6)
  6. Materialized dataset views for reproducible experiment runs

V1 datasets are recognized and reused without re-download (spec §3, §45).
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .. import paths as P
from ..activity import activity
from ..config import get_config
from ..db.database import get_db
from ..jobs import get_job_manager
from ..mt5 import get_bridge
from ..versions import DATA_SCHEMA_VERSION
from . import integrity, manifest, master, sessions, storage

log = logging.getLogger("data.engine")

TF_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}


def make_dataset_id(symbol: str, timeframe: str, start_ts: float, end_ts: float, source: str) -> str:
    s = datetime.fromtimestamp(start_ts, tz=timezone.utc).strftime("%Y%m%d")
    e = datetime.fromtimestamp(end_ts, tz=timezone.utc).strftime("%Y%m%d")
    return f"{symbol}_{timeframe}_{s}_{e}_{source}"


def make_master_dataset_id(symbol: str, timeframe: str, profile_key: str, version: int) -> str:
    safe_profile = profile_key.replace("|", "_").replace("/", "-")
    return f"{symbol}_{timeframe}_{safe_profile}_v{version}"


class DataEngine:
    def __init__(self):
        self._lock = threading.RLock()
        self._frames: Dict[str, pd.DataFrame] = {}   # in-memory dataset cache (dataset_id -> DataFrame)

    # ---------- master incremental sync (spec §4, §5, §6, §7) ----------
    def sync_master(self, symbol: str, timeframe: str, force_full: bool = False) -> Dict:
        """Incrementally sync the master dataset from the active bridge.

        Reads last stored timestamp. Requests ONLY the missing range from MT5.
        Deduplicates incoming bars against master. Discards identical duplicates,
        flags and quarantines conflicts/corrupted bars.
        """
        cfg = get_config()
        bridge = get_bridge()
        profile = bridge.profile()
        mstore = master.get_master(symbol, timeframe, profile)

        tf_min = TF_MINUTES.get(timeframe, 15)
        step_s = tf_min * 60
        now = time.time()
        months = cfg.data.history_months.get(timeframe, 6)
        default_start_ts = now - months * 30 * 86400

        last_stored = mstore.last_ts
        first_stored = mstore.first_ts

        # Check if existing data was discovered in DATA_ROOT (spec §2, §3, §5)
        if (last_stored is None or mstore.rows == 0) and not force_full:
            from .discovery import get_discovery_engine
            disc = get_discovery_engine()
            reuse_dec = disc.check_dataset_reuse(symbol, timeframe, emit_logs=True)
            if reuse_dec.get("action") == "REUSE":
                ds_path = reuse_dec["dataset"]["path"]
                try:
                    df_existing = pd.read_parquet(ds_path)
                    if mstore.rows == 0:
                        mstore.append(df_existing)
                    did = self._materialize_dataset_view(mstore)
                    return {
                        "symbol": symbol, "timeframe": timeframe, "fetch_mode": "reuse_existing",
                        "new_bars": 0, "total_bars": len(df_existing), "version": mstore.version,
                        "dataset_id": did, "action": "REUSING EXISTING DATA",
                    }
                except Exception as e:
                    log.warning("failed to reuse discovered dataset %s: %s", ds_path, e)
            elif reuse_dec.get("action") == "EXTEND":
                ds_path = reuse_dec["dataset"]["path"]
                try:
                    df_existing = pd.read_parquet(ds_path)
                    if mstore.rows == 0:
                        mstore.append(df_existing)
                    last_stored = mstore.last_ts
                except Exception as e:
                    log.warning("failed to seed mstore from %s: %s", ds_path, e)

        is_initial = (last_stored is None or force_full or mstore.rows == 0)

        # Check if data can be reused without network request
        if not is_initial and not force_full and (now - last_stored) < step_s:
            reuse_msg = f"ACTION: REUSING EXISTING DATA for {symbol} {timeframe} ({mstore.rows:,} bars up to date)"
            log.info(reuse_msg)
            activity.info("DATA", reuse_msg)
            did = self._materialize_dataset_view(mstore)
            return {
                "symbol": symbol, "timeframe": timeframe, "fetch_mode": "reuse_existing",
                "new_bars": 0, "total_bars": mstore.rows, "version": mstore.version,
                "dataset_id": did, "action": "REUSING EXISTING DATA",
            }

        # Multi-level job tracking (V2.7)
        jm = get_job_manager()
        job = jm.create_job(name=f"Sync {symbol} {timeframe}", job_type="ingest")
        job.start()
        job.add_stage("fetch", "Fetch Bars")
        job.add_stage("validate", "Validate Integrity")
        job.add_stage("normalize", "Normalize & Sessionize")
        job.add_stage("persist", "Persist & Index")

        if is_initial:
            fetch_start = default_start_ts
            fetch_end = now
            fetch_mode = "initial_bootstrap"
        else:
            # Request only missing range with 2-bar overlap to ensure closed-bar continuity
            fetch_start = max(last_stored - (step_s * 2), default_start_ts)
            fetch_end = now
            fetch_mode = "incremental_delta"
            log.info("ACTION: INCREMENTAL FETCH for %s %s: requesting missing delta", symbol, timeframe)

        n_bars_needed = max(10, int((fetch_end - fetch_start) // step_s) + 5)
        job.add_task("fetch", "request_bars", "Request bars from bridge", total_units=n_bars_needed, unit_name="bars")

        # Fetch from bridge
        op_id = f"sync_{symbol.lower()}_{timeframe.lower()}"
        activity.started("DATA", f"Starting {symbol} {timeframe} synchronization ({fetch_mode})",
                         operation_id=op_id, progress=10.0)

        db = get_db()
        if not is_initial:
            last_dt_str = datetime.fromtimestamp(last_stored, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            activity.info("DATA", f"Checking {symbol} {timeframe}: last stored bar {last_dt_str}",
                          operation_id=op_id, progress=20.0)
        else:
            activity.info("DATA", f"Initial bootstrap for {symbol} {timeframe}",
                          operation_id=op_id, progress=20.0)

        activity.running("DATA", f"Requesting historical data from {bridge.source} for {symbol} {timeframe}",
                         operation_id=op_id, progress=35.0)

        bars = bridge.copy_rates_range(symbol, timeframe, fetch_start, fetch_end)
        if bars is None or len(bars) == 0:
            activity.info("DATA", f"No range bars returned for {symbol} {timeframe}, requesting recent {n_bars_needed} bars",
                          operation_id=op_id, progress=45.0)
            bars = bridge.copy_rates(symbol, timeframe, n_bars_needed)

        if bars is None or len(bars) == 0:
            msg = f"No bars returned for {symbol} {timeframe} from {bridge.source}"
            activity.warning("DATA", msg, operation_id=op_id)
            job.fail(msg)
            return {
                "symbol": symbol, "timeframe": timeframe, "fetch_mode": fetch_mode,
                "new_bars": 0, "total_bars": mstore.rows, "version": mstore.version,
                "status": "no_data_from_bridge",
            }

        job.update_task_progress("fetch", "request_bars", len(bars), max(len(bars), n_bars_needed))
        job.complete_stage("fetch")
        job.heartbeat(f"Received {len(bars):,} bars from {bridge.source}")

        activity.success("DATA", f"Received {len(bars):,} {timeframe} bars from {bridge.source}",
                         operation_id=op_id, progress=60.0)
        activity.running("DATA", f"Validating timestamps & data integrity for {symbol} {timeframe}",
                         operation_id=op_id, progress=75.0)

        df_raw = pd.DataFrame([b.to_dict() for b in bars])

        # Filter simulator weekend bars if needed
        if bridge.source == "SIMULATOR":
            dt = pd.to_datetime(df_raw["ts"], unit="s", utc=True)
            df_raw = df_raw[dt.dt.dayofweek < 5].reset_index(drop=True)

        # 1. Data Integrity Validation & Quarantine (spec §7)
        job.add_task("validate", "integrity_check", "Check monotonicity and OHLC consistency", total_units=len(df_raw), unit_name="bars")
        quarantine_dir = P.symbol_dirs(symbol)["quarantine"]
        df_accepted, integrity_rep = integrity.validate_batch(
            df_raw, timeframe=timeframe, quarantine_dir=quarantine_dir,
            tag=f"{symbol}_{timeframe}_{bridge.source}"
        )
        job.update_task_progress("validate", "integrity_check", len(df_accepted), len(df_raw))
        job.complete_stage("validate")

        # Record integrity report in SQLite
        db.x("""INSERT INTO integrity_reports
                (ts, symbol, timeframe, profile, stage, report, quarantined, quarantine_path)
                VALUES (?,?,?,?,?,?,?,?)""",
             (time.time(), symbol, timeframe, mstore.profile_key, fetch_mode,
              str(integrity_rep.as_dict()), integrity_rep.rows_quarantined,
              integrity_rep.quarantine_path))

        if integrity_rep.rows_quarantined > 0:
            activity.warning("DATA",
                             f"Integrity check quarantined {integrity_rep.rows_quarantined} bars for {symbol} {timeframe}",
                             operation_id=op_id)
        else:
            activity.info("DATA", f"Data integrity check passed for {symbol} {timeframe}",
                          operation_id=op_id)

        if df_accepted.empty:
            did = self._materialize_dataset_view(mstore) if mstore.rows > 0 else ""
            activity.warning("DATA", f"All fetched bars quarantined/rejected for {symbol} {timeframe}",
                             operation_id=op_id)
            return {
                "symbol": symbol, "timeframe": timeframe, "fetch_mode": fetch_mode,
                "new_bars": 0, "total_bars": mstore.rows, "version": mstore.version,
                "dataset_id": did,
                "integrity": integrity_rep.as_dict(),
            }

        # 2. Normalize (DST sessions, numeric types, spread)
        activity.running("DATA", f"Normalizing & updating master cache for {symbol} {timeframe}",
                         operation_id=op_id, progress=85.0)
        job.add_task("normalize", "apply_sessions", "Label trading sessions with zoneinfo", total_units=len(df_accepted), unit_name="bars")
        df_norm = self._normalize(df_accepted)
        job.update_task_progress("normalize", "apply_sessions", len(df_norm), len(df_accepted))
        job.complete_stage("normalize")

        # 3. Master append with deduplication & conflict detection (spec §5)
        job.add_task("persist", "append_partitions", "Append and update master store", total_units=len(df_norm), unit_name="bars")
        append_rep = mstore.append(df_norm, source_info=profile)
        job.update_task_progress("persist", "append_partitions", append_rep["accepted"], len(df_norm))

        # Materialize / update latest dataset view in registry so pipeline can query it
        did = self._materialize_dataset_view(mstore)
        job.complete_stage("persist")
        job.complete({
            "symbol": symbol, "timeframe": timeframe, "new_bars": append_rep["accepted"],
            "total_bars": mstore.rows, "dataset_id": did,
        })

        activity.success("DATA",
                         f"{symbol} {timeframe} synchronization complete: "
                         f"{append_rep['accepted']} new bars appended (total: {mstore.rows:,})",
                         operation_id=op_id, progress=100.0)

        return {
            "symbol": symbol, "timeframe": timeframe, "fetch_mode": fetch_mode,
            "new_bars": append_rep["accepted"],
            "duplicates_identical": append_rep["duplicates_identical"],
            "conflicts": append_rep["conflicts"],
            "total_bars": mstore.rows,
            "master_version": mstore.version,
            "dataset_id": did,
            "integrity": integrity_rep.as_dict(),
        }

    def _materialize_dataset_view(self, mstore: master.MasterStore) -> str:
        """Create/update a view dataset in `datasets` table pointing to the master store."""
        if mstore.rows == 0:
            return ""

        cfg = get_config()
        did = make_master_dataset_id(mstore.symbol, mstore.timeframe,
                                     mstore.profile_key, mstore.version)
        db = get_db()
        existing = db.one("SELECT id, bars FROM datasets WHERE id=?", (did,))
        if existing:
            return did

        # Write dataset snapshot to cache_dir so existing fast paths / worker pools
        # can read single parquet files with zero architectural break
        df = mstore.read_range()
        p = storage.write_bars(cfg.data.cache_dir, did, df)

        source_name = mstore.profile.get("source", "UNKNOWN")

        # Spec V2.8 Isolation: Simulator data must NEVER enter DATA/MT5/XAUUSD
        if source_name == "SIMULATOR" or "SIMULATOR" in str(did).upper():
            try:
                sim_sym_dir = P.SIMULATOR_DATA_DIR / mstore.symbol
                sim_sym_dir.mkdir(parents=True, exist_ok=True)
                sim_path = sim_sym_dir / f"{mstore.timeframe}.parquet"
                df.to_parquet(sim_path, index=False)
                log.info("[DATA ISOLATION] Simulator dataset saved to TEST_DATA: %s", sim_path)
            except Exception as e:
                log.warning("Failed to write simulator dataset to TEST_DATA: %s", e)
        else:
            # V2.8 Authoritative Real Data: DATA/MT5/{symbol}/{timeframe}.parquet
            try:
                sym_dir = P.MT5_DIR / mstore.symbol
                sym_dir.mkdir(parents=True, exist_ok=True)
                sym_tf_path = sym_dir / f"{mstore.timeframe}.parquet"
                df.to_parquet(sym_tf_path, index=False)

                raw_path = P.MT5_RAW_DIR / f"{mstore.symbol}_{mstore.timeframe}.parquet"
                norm_path = P.MT5_NORMALIZED_DIR / f"{mstore.symbol}_{mstore.timeframe}.parquet"
                df.to_parquet(raw_path, index=False)
                df.to_parquet(norm_path, index=False)
                manifest.update_dataset_entry(f"{mstore.symbol}_{mstore.timeframe}", {
                    "symbol": mstore.symbol,
                    "timeframe": mstore.timeframe,
                    "dataset_id": did,
                    "first_ts": float(mstore.first_ts or 0),
                    "last_ts": float(mstore.last_ts or 0),
                    "start_time": datetime.fromtimestamp(float(mstore.first_ts or 0), tz=timezone.utc).isoformat(),
                    "end_time": datetime.fromtimestamp(float(mstore.last_ts or 0), tz=timezone.utc).isoformat(),
                    "rows": len(df),
                    "source": source_name,
                    "broker": mstore.profile.get("broker", "UNKNOWN"),
                    "server": mstore.profile.get("server", "UNKNOWN"),
                    "raw_path": P.data_relative_path(sym_tf_path),
                    "normalized_path": P.data_relative_path(norm_path),
                    "file_size_bytes": sym_tf_path.stat().st_size if sym_tf_path.exists() else 0,
                })
            except Exception as e:
                log.warning("Failed to write V2.8 MT5 authoritative parquet: %s", e)

        db.x("""INSERT OR REPLACE INTO datasets
                (id, symbol, timeframe, start_ts, end_ts, bars, source, path,
                 feature_cache, created_at, kind, master_profile, dataset_version, fingerprint)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
             (did, mstore.symbol, mstore.timeframe, float(mstore.first_ts or 0),
              float(mstore.last_ts or 0), len(df), source_name,
              str(p), "[]", time.time(), "master_view", mstore.profile_key,
              mstore.version, mstore.fingerprint))

        with self._lock:
            self._frames[did] = df

        return did

    # ---------- legacy & backward-compatible ingest ----------
    def ingest(self, symbol: str, timeframe: str, months: Optional[float] = None,
               force: bool = False) -> Dict:
        """Unified ingest method: delegates to incremental master sync."""
        rep = self.sync_master(symbol, timeframe, force_full=force)
        did = rep.get("dataset_id")
        bridge = get_bridge()
        if not did:
            # Fallback ONLY to existing dataset matching active bridge mode (NEVER silently substitute SIMULATOR when real)
            latest = self.latest_dataset(symbol, timeframe, require_real=(not bridge.is_simulated))
            if latest:
                did = latest["id"]
        return {
            "dataset_id": did,
            "cached": rep.get("new_bars", 0) == 0,
            "bars": rep.get("total_bars", 0),
            "source": bridge.source,
            "new_bars": rep.get("new_bars", 0),
            "master_version": rep.get("master_version", 0),
            "error": rep.get("error"),
        }

    def ingest_default(self) -> List[Dict]:
        """Incremental synchronization for all configured symbols & timeframes."""
        cfg = get_config()
        bridge = get_bridge()
        from ..activity import activity
        out = []
        activity.started("DATA", f"Starting data synchronization for {cfg.data.symbol} ({bridge.source})",
                         operation_id=f"sync_{cfg.data.symbol.lower()}_all")

        for tf in cfg.data.timeframes:
            op_id = f"sync_{cfg.data.symbol.lower()}_{tf.lower()}"
            try:
                rep = self.sync_master(cfg.data.symbol, tf)
                out.append(rep)
            except Exception as e:
                log.error("sync %s %s failed: %s", cfg.data.symbol, tf, e, exc_info=True)
                activity.failed(
                    "DATA",
                    f"{cfg.data.symbol} {tf} synchronization failed",
                    operation_id=op_id,
                    reason=str(e),
                    platform=f"MT5 REAL ({bridge.source})" if not bridge.is_simulated else "SIMULATOR",
                    next_action="Synchronization stopped for this timeframe.",
                )
                out.append({"dataset_id": None, "symbol": cfg.data.symbol, "timeframe": tf,
                            "error": str(e)})

        succeeded = sum(1 for r in out if r.get("dataset_id"))
        failed = len(out) - succeeded
        if failed == 0:
            activity.success("DATA", f"Market data synchronization complete: all {len(out)} timeframes synchronized ({bridge.source})",
                             operation_id=f"sync_{cfg.data.symbol.lower()}_all", progress=100.0)
        else:
            activity.warning("DATA", f"Market data synchronization finished with {failed} failure(s) out of {len(out)} timeframes ({bridge.source})",
                             operation_id=f"sync_{cfg.data.symbol.lower()}_all")
        return out

    # ---------- normalization (DST sessions, numeric types, spread) ----------
    def _normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        work = df.copy()
        ts_arr = work["ts"].to_numpy(dtype=np.float64)
        dt = pd.to_datetime(ts_arr, unit="s", utc=True)
        work["hour"] = dt.hour.astype(np.int8)
        work["minute"] = dt.minute.astype(np.int8)
        work["dow"] = dt.dayofweek.astype(np.int8)      # 0=Mon
        work["dom"] = dt.day.astype(np.int8)

        # DST-aware session labeling (spec §43)
        work["session"] = sessions.session_labels(ts_arr).astype(str)

        for c in ("open", "high", "low", "close", "bid", "ask"):
            if c in work.columns:
                work[c] = pd.to_numeric(work[c], errors="coerce").astype(np.float64)
            else:
                work[c] = np.nan

        # Bid/Ask synthesis if missing
        if work["bid"].isna().all() and not work["close"].isna().all():
            work["bid"] = work["close"]
        if work["ask"].isna().all() and not work["close"].isna().all():
            work["ask"] = work["close"]

        if "spread" not in work or work["spread"].isna().any():
            work["spread"] = ((work["ask"] - work["bid"]) / 0.01).fillna(18.0)

        if "tick_volume" not in work:
            work["tick_volume"] = 0
        work["tick_volume"] = work["tick_volume"].fillna(0).astype(np.int32)

        return work[storage.COLUMNS]

    # ---------- access & retrieval ----------
    def get_frame(self, dataset_id: str) -> pd.DataFrame:
        """Retrieve dataset DataFrame, checking memory -> disk -> master store -> legacy."""
        with self._lock:
            if dataset_id in self._frames:
                df = self._frames[dataset_id]
                df["session"] = sessions.session_labels(df["ts"].to_numpy())
                return df

        cfg = get_config()
        # 1. Try V2.8 XAUUSD directory, then V2.7 raw / datasets directory
        df = None
        parts = dataset_id.split("_")
        if len(parts) >= 2:
            sym, tf = parts[0], parts[1]
            if sym.upper() == "XAUUSD":
                xau_path = P.MT5_XAUUSD_DIR / f"{tf}.parquet"
                if xau_path.exists() and xau_path.stat().st_size > 0:
                    try:
                        df = pd.read_parquet(xau_path)
                    except Exception:
                        df = None
            if df is None:
                mt5_raw = P.MT5_RAW_DIR / f"{sym}_{tf}.parquet"
                if mt5_raw.exists():
                    try:
                        df = pd.read_parquet(mt5_raw)
                    except Exception:
                        df = None

        # 2. Try storage (checks cache_dir + DATA/.../legacy/)
        if df is None:
            df = storage.read_dataset(cfg.data.cache_dir, dataset_id)

        # 3. If not found as a snapshot file, check database registry and fallback artifact paths
        if df is None:
            artifact_path = self.find_dataset_artifact_path(dataset_id)
            if artifact_path and artifact_path.exists() and artifact_path.stat().st_size > 0:
                try:
                    df = pd.read_parquet(artifact_path)
                except Exception:
                    df = None

        if df is None:
            db = get_db()
            row = db.one("SELECT * FROM datasets WHERE id=?", (dataset_id,))
            if row and row["path"] and Path(row["path"]).exists():
                df = pd.read_parquet(row["path"])

        # 4. If still not found, check master datasets
        if df is None:
            parts = dataset_id.split("_")
            if len(parts) >= 2:
                sym, tf = parts[0], parts[1]
                bridge = get_bridge()
                mstore = master.get_master(sym, tf, bridge.profile())
                if mstore.rows > 0:
                    df = mstore.read_range()

        if df is None:
            raise KeyError(f"dataset not found on disk or master store: {dataset_id}")

        msg = f"ACTION: REUSING EXISTING DATA for {dataset_id} ({len(df):,} bars)"
        log.info(msg)
        activity.info("DATA", msg)

        # Derived session columns are recomputed at load time (spec §43)
        df["session"] = sessions.session_labels(df["ts"].to_numpy())
        with self._lock:
            if len(self._frames) > 12:
                self._frames.clear()
            self._frames[dataset_id] = df
        return df

    def find_dataset_artifact_path(self, dataset_id: str) -> Optional[Path]:
        """Locate physical parquet artifact on disk for a dataset ID without loading it."""
        cfg = get_config()
        parts = dataset_id.split("_")
        if len(parts) >= 2:
            sym, tf = parts[0], parts[1]
            if sym.upper() == "XAUUSD":
                xau_path = P.MT5_XAUUSD_DIR / f"{tf}.parquet"
                if xau_path.exists() and xau_path.stat().st_size > 0:
                    return xau_path
            mt5_raw = P.MT5_RAW_DIR / f"{sym}_{tf}.parquet"
            if mt5_raw.exists() and mt5_raw.stat().st_size > 0:
                return mt5_raw

        storage_path = storage.dataset_path(cfg.data.cache_dir, dataset_id)
        if storage_path.exists() and storage_path.stat().st_size > 0:
            return storage_path

        db = get_db()
        row = db.one("SELECT path FROM datasets WHERE id=?", (dataset_id,))
        if row and row["path"]:
            p = Path(row["path"])
            if p.exists() and p.stat().st_size > 0:
                return p
            # Check relative to DATA_ROOT or DATA_CACHE_DIR only if filename matches dataset_id
            if p.name == f"{dataset_id}.parquet":
                p_alt = P.DATA_CACHE_DIR / p.name
                if p_alt.exists() and p_alt.stat().st_size > 0:
                    return p_alt
                if len(parts) >= 1:
                    p_sim = P.SIMULATOR_DATA_DIR / parts[0] / p.name
                    if p_sim.exists() and p_sim.stat().st_size > 0:
                        return p_sim
                    p_test = P.TEST_DATA_DIR / "SIMULATOR_DATA" / parts[0] / p.name
                    if p_test.exists() and p_test.stat().st_size > 0:
                        return p_test
            return None

        # Check DATA_CACHE_DIR fallback
        cache_fallback = P.DATA_CACHE_DIR / f"{dataset_id}.parquet"
        if cache_fallback.exists() and cache_fallback.stat().st_size > 0:
            return cache_fallback

        # Check SIMULATOR_DATA_DIR / TEST_DATA fallback
        if len(parts) >= 1:
            sym_clean = parts[0]
            sim_path = P.SIMULATOR_DATA_DIR / sym_clean / f"{dataset_id}.parquet"
            if sim_path.exists() and sim_path.stat().st_size > 0:
                return sim_path
            test_path = P.TEST_DATA_DIR / "SIMULATOR_DATA" / sym_clean / f"{dataset_id}.parquet"
            if test_path.exists() and test_path.stat().st_size > 0:
                return test_path

        return None

    def is_dataset_eligible_for_research(
        self,
        dataset_id: str,
        require_features: bool = True
    ) -> Tuple[bool, str]:
        """Dataset Eligibility Contract (Spec V2.71):
        A dataset may enter research only when the actual runtime can prove:
        1. Physical dataset artifact exists in DATA_ROOT and has size > 0.
        2. Can be loaded into DataFrame without error.
        3. Schema has valid OHLC price and time series.
        4. Row count > 0 (meets minimum bar threshold).
        5. Source/type compatible with current research run (no simulator in MT5 REAL mode).
        6. Feature artifact exists and is valid on disk when require_features is True.
        """
        path = self.find_dataset_artifact_path(dataset_id)
        if not path:
            return False, f"Physical dataset artifact not found on disk: {dataset_id}"

        bridge = get_bridge()
        if not bridge.is_simulated and "SIMULATOR" in dataset_id.upper():
            return False, f"Dataset source SIMULATOR incompatible with active MT5 REAL bridge"

        try:
            df = pd.read_parquet(path)
            if len(df) == 0:
                return False, f"Dataset parquet on disk has 0 rows: {dataset_id}"
            cols = set(df.columns)
            has_price = bool({"close", "price", "bid", "ask"} & cols)
            has_time = bool({"ts", "time", "date"} & cols)
            if not has_price or not has_time:
                return False, f"Dataset schema invalid (missing price or time columns): {dataset_id}"
        except Exception as e:
            return False, f"Failed to load dataset artifact from disk: {dataset_id} ({e})"

        if require_features:
            cfg = get_config()
            feat_simple = P.FEATURES_DIR / f"{dataset_id}.parquet"
            feat_v27 = P.FEATURES_DIR / f"{dataset_id}__core_v1__schema_v2.parquet"
            feat_cache = Path(cfg.data.cache_dir) / "features" / f"{dataset_id}.parquet"

            target_feat = None
            for fp in (feat_v27, feat_simple, feat_cache):
                if fp.exists() and fp.stat().st_size > 0:
                    target_feat = fp
                    break

            if not target_feat:
                return False, f"Feature artifact missing on disk for dataset: {dataset_id}"

            try:
                df_f = pd.read_parquet(target_feat)
                if len(df_f) == 0:
                    return False, f"Feature artifact has 0 rows: {dataset_id}"
                f_cols = set(df_f.columns)
                has_feat_price = "price" in f_cols or "close" in f_cols
                if not has_feat_price:
                    return False, f"Feature artifact missing price/close column: {dataset_id}"
            except Exception as e:
                return False, f"Feature artifact unreadable for {dataset_id}: {e}"

        return True, "ELIGIBLE"

    def get_available_timeframes(self, symbol: str = "XAUUSD") -> List[str]:
        """Return list of timeframes that physically exist and are research-eligible."""
        eligible_tfs = []
        for tf in ("M1", "M5", "M15", "M30", "H1"):
            ds = self.latest_dataset(symbol, tf, require_eligible=True)
            if ds is not None:
                eligible_tfs.append(tf)
        tfs = getattr(get_config().data, "timeframes", ["M15"])
        return eligible_tfs or (tfs if isinstance(tfs, list) else [tfs]) or ["M15"]

    def latest_dataset(
        self,
        symbol: str,
        timeframe: str,
        source: Optional[str] = None,
        require_real: bool = False,
        require_eligible: bool = True
    ) -> Optional[Dict]:
        """Find the latest dataset (master view or legacy) in the database.
        When require_eligible is True (default), enforces the V2.71 Dataset Eligibility Contract:
        verifies that the physical artifact actually exists on disk and is valid.
        Stale database references missing on disk are rejected and ignored.
        """
        db = get_db()
        bridge = get_bridge()
        filter_real = require_real or (not bridge.is_simulated)

        # 1. Authoritative check: MT5_XAUUSD_DIR for XAUUSD
        if symbol.upper() == "XAUUSD":
            xau_path = P.MT5_XAUUSD_DIR / f"{timeframe}.parquet"
            if xau_path.exists() and xau_path.stat().st_size > 0:
                ds_id = f"XAUUSD_{timeframe}"
                is_elig, _ = self.is_dataset_eligible_for_research(ds_id, require_features=False)
                if is_elig:
                    return {
                        "id": ds_id,
                        "symbol": "XAUUSD",
                        "timeframe": timeframe,
                        "source": "MT5" if filter_real else "SIMULATOR",
                        "kind": "master_view",
                        "path": str(xau_path),
                        "created_at": xau_path.stat().st_mtime,
                        "fingerprint": "VALID",
                        "dataset_version": 1,
                    }

        # 2. Database queries
        candidates = []
        if source:
            rows = db.q(
                """SELECT * FROM datasets
                   WHERE symbol=? AND timeframe=? AND source=?
                   ORDER BY created_at DESC""",
                (symbol, timeframe, source))
            candidates.extend(rows)

        if filter_real:
            rows = db.q(
                """SELECT * FROM datasets
                   WHERE symbol=? AND timeframe=? AND source!='SIMULATOR'
                   ORDER BY created_at DESC""",
                (symbol, timeframe))
            candidates.extend(rows)
            if not require_real and not candidates:
                rows = db.q(
                    """SELECT * FROM datasets
                       WHERE symbol=? AND timeframe=?
                       ORDER BY created_at DESC""",
                    (symbol, timeframe))
                candidates.extend(rows)
        else:
            rows = db.q(
                """SELECT * FROM datasets
                   WHERE symbol=? AND timeframe=?
                   ORDER BY created_at DESC""",
                (symbol, timeframe))
            candidates.extend(rows)

        # 3. Filter candidates through Dataset Eligibility Contract
        seen = set()
        for c in candidates:
            cid = c["id"]
            if cid in seen:
                continue
            seen.add(cid)
            if require_eligible:
                is_elig, reason = self.is_dataset_eligible_for_research(cid, require_features=False)
                if not is_elig:
                    log.debug("[DATASET ELIGIBILITY] Stale/missing DB dataset %s rejected: %s", cid, reason)
                    continue
            return c

        return None

    def list_datasets(self) -> List[Dict]:
        db = get_db()
        rows = db.q("SELECT * FROM datasets ORDER BY symbol, timeframe, created_at DESC")
        if not rows:
            from .discovery import get_discovery_engine
            disc = get_discovery_engine()
            disc.reconcile_with_database(db=db, emit_logs=False)
            rows = db.q("SELECT * FROM datasets ORDER BY symbol, timeframe, created_at DESC")
        for r in rows:
            p = storage.dataset_path(get_config().data.cache_dir, r["id"])
            r["on_disk"] = p.exists() or (r.get("path") and Path(r["path"]).exists())
        return rows

    def train_test_split(self, dataset_id: str) -> Dict[str, slice]:
        df = self.get_frame(dataset_id)
        n = len(df)
        cut = int(n * get_config().backtest.train_fraction)
        return {"train": slice(0, cut), "test": slice(cut, n), "cut": cut, "n": n}


_engine: Optional[DataEngine] = None


def get_data_engine() -> DataEngine:
    global _engine
    if _engine is None:
        _engine = DataEngine()
    return _engine
