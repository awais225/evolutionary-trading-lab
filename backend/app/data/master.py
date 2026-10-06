"""
Master market-data store (spec §4, §5, §6) — the V2 incremental data engine.

One persistent master dataset per (symbol, timeframe, source, broker, server):

    DATA/{SYMBOL}/raw/{TF}/{profile}/YYYYMM.parquet     monthly partitions
    DATA/{SYMBOL}/metadata/{TF}__{profile}.json         partition hashes etc.
    DATA/{SYMBOL}/quarantine/                           integrity rejects

Registry mirror in SQLite `master_datasets`: first_ts, last_ts, rows,
version (bumped on every data change), data schema version, content
fingerprint, conflict count.

Sync protocol (`MasterStore.sync`):
  1. read LAST STORED TIMESTAMP from the registry/metadata
  2. request ONLY [last_ts - overlap, now] from the bridge
  3. integrity-validate the incoming batch (data/integrity.py)
  4. dedupe against stored timestamps
       - identical duplicates      -> discarded safely (counted)
       - same ts, different values -> NEVER silently overwritten: recorded in
         `data_conflicts` (ts, old, new, source, broker/server, detected_at),
         old value kept, master flagged
  5. append accepted rows to monthly partitions (atomic temp+replace)
  6. bump version, recompute fingerprint, update registry

Experiments consume (start_ts, end_ts) VIEWS of a master (see DataEngine), so
a view id stays stable while `dataset_version` tracks data growth — the
experiment fingerprint (spec §9) includes the version.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .. import paths as P
from ..db.database import get_db
from ..versions import DATA_SCHEMA_VERSION
from . import integrity
from .storage import COLUMNS

log = logging.getLogger("data.master")

TF_SECONDS = integrity.TF_SECONDS


def month_key(ts: float) -> str:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y%m")


def _file_sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:32]


class MasterStore:
    """Persistent incremental store for one (symbol, timeframe, profile)."""

    def __init__(self, symbol: str, timeframe: str, profile: Dict[str, str]):
        self.symbol = symbol
        self.timeframe = timeframe
        self.profile = profile
        self.profile_key = f"{profile.get('source','?')}|{profile.get('broker','')}|{profile.get('server','')}"
        safe = self.profile_key.replace("|", "_").replace("/", "-")
        dirs = P.symbol_dirs(symbol)
        self.raw_dir: Path = dirs["raw"] / timeframe / safe
        self.quarantine_dir: Path = dirs["quarantine"]
        self.meta_path: Path = dirs["metadata"] / f"{timeframe}__{safe}.json"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._meta: Dict = self._load_meta()

    # ---------- metadata ----------
    def _load_meta(self) -> Dict:
        if self.meta_path.exists():
            try:
                return json.loads(self.meta_path.read_text())
            except Exception as e:
                log.error("master metadata corrupt (%s): %s — rebuilding from partitions",
                          self.meta_path, e)
        return self._rebuild_meta()

    def _rebuild_meta(self) -> Dict:
        parts: Dict[str, Dict] = {}
        rows = 0
        first_ts = last_ts = None
        for f in sorted(self.raw_dir.glob("*.parquet")):
            try:
                df = pd.read_parquet(f, columns=["ts"])
            except Exception:
                continue
            if df.empty:
                continue
            parts[f.stem] = {"rows": int(len(df)),
                             "first_ts": float(df["ts"].iloc[0]),
                             "last_ts": float(df["ts"].iloc[-1]),
                             "sha256": _file_sha256(f)}
            rows += len(df)
            first_ts = float(df["ts"].iloc[0]) if first_ts is None else min(first_ts, float(df["ts"].iloc[0]))
            last_ts = float(df["ts"].iloc[-1]) if last_ts is None else max(last_ts, float(df["ts"].iloc[-1]))
        meta = {"symbol": self.symbol, "timeframe": self.timeframe,
                "profile": self.profile, "profile_key": self.profile_key,
                "schema_version": DATA_SCHEMA_VERSION,
                "partitions": parts, "rows": rows,
                "first_ts": first_ts, "last_ts": last_ts,
                "version": 0, "conflicts": 0}
        meta["fingerprint"] = self._fingerprint(meta)
        self._save_meta(meta)
        return meta

    def _fingerprint(self, meta: Dict) -> str:
        h = hashlib.sha256()
        h.update(f"{meta['symbol']}|{meta['timeframe']}|{meta['profile_key']}|"
                 f"{meta['schema_version']}|{meta['rows']}|{meta['first_ts']}|"
                 f"{meta['last_ts']}|{meta['version']}".encode())
        for name in sorted(meta["partitions"]):
            h.update(f"{name}:{meta['partitions'][name]['sha256']}".encode())
        return h.hexdigest()[:32]

    def _save_meta(self, meta: Dict) -> None:
        tmp = self.meta_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(meta, indent=1))
        os.replace(tmp, self.meta_path)
        self._meta = meta
        self._update_registry(meta)

    def _update_registry(self, meta: Dict) -> None:
        db = get_db()
        db.x("""INSERT INTO master_datasets
                (symbol,timeframe,source,broker,server,profile,first_ts,last_ts,
                 rows,version,schema_version,fingerprint,meta_path,conflicts,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(symbol,timeframe,profile) DO UPDATE SET
                 first_ts=excluded.first_ts, last_ts=excluded.last_ts,
                 rows=excluded.rows, version=excluded.version,
                 schema_version=excluded.schema_version,
                 fingerprint=excluded.fingerprint, meta_path=excluded.meta_path,
                 conflicts=excluded.conflicts, updated_at=excluded.updated_at""",
             (self.symbol, self.timeframe, self.profile.get("source", ""),
              self.profile.get("broker", ""), self.profile.get("server", ""),
              self.profile_key, meta.get("first_ts"), meta.get("last_ts"),
              int(meta.get("rows", 0)), int(meta.get("version", 0)),
              DATA_SCHEMA_VERSION, meta.get("fingerprint"), str(self.meta_path),
              int(meta.get("conflicts", 0)), time.time()))

    # ---------- public state ----------
    @property
    def rows(self) -> int:
        return int(self._meta.get("rows", 0))

    @property
    def last_ts(self) -> Optional[float]:
        return self._meta.get("last_ts")

    @property
    def first_ts(self) -> Optional[float]:
        return self._meta.get("first_ts")

    @property
    def version(self) -> int:
        return int(self._meta.get("version", 0))

    @property
    def fingerprint(self) -> str:
        return str(self._meta.get("fingerprint", ""))

    def info(self) -> Dict:
        m = self._meta
        return {"symbol": self.symbol, "timeframe": self.timeframe,
                "profile_key": self.profile_key, "rows": m.get("rows", 0),
                "first_ts": m.get("first_ts"), "last_ts": m.get("last_ts"),
                "version": m.get("version", 0), "fingerprint": m.get("fingerprint"),
                "schema_version": m.get("schema_version"),
                "conflicts": m.get("conflicts", 0),
                "partitions": len(m.get("partitions", {})),
                "meta_path": str(self.meta_path)}

    # ---------- reading ----------
    def partitions_for(self, start_ts: Optional[float], end_ts: Optional[float]) -> List[Path]:
        names = sorted(self._meta.get("partitions", {}))
        out = []
        for n in names:
            pm = self._meta["partitions"][n]
            if start_ts is not None and pm["last_ts"] < start_ts:
                continue
            if end_ts is not None and pm["first_ts"] > end_ts:
                continue
            out.append(self.raw_dir / f"{n}.parquet")
        return out

    def read_range(self, start_ts: Optional[float] = None,
                   end_ts: Optional[float] = None,
                   columns: Optional[List[str]] = None) -> pd.DataFrame:
        files = self.partitions_for(start_ts, end_ts)
        if not files:
            return pd.DataFrame(columns=COLUMNS)
        cols = columns or COLUMNS
        frames = []
        for f in files:
            try:
                df = pd.read_parquet(f)
            except Exception as e:
                log.error("partition unreadable %s: %s", f, e)
                continue
            frames.append(df)
        if not frames:
            return pd.DataFrame(columns=COLUMNS)
        out = pd.concat(frames, ignore_index=True)
        for c in cols:
            if c not in out.columns:
                out[c] = np.nan
        out = out[cols]
        if start_ts is not None:
            out = out[out["ts"] >= start_ts]
        if end_ts is not None:
            out = out[out["ts"] <= end_ts]
        return out.reset_index(drop=True)

    def all_timestamps(self) -> np.ndarray:
        """Cheap ts-column read across partitions (dedupe support)."""
        frames = []
        for f in self.partitions_for(None, None):
            try:
                frames.append(pd.read_parquet(f, columns=["ts"])["ts"].to_numpy())
            except Exception:
                continue
        if not frames:
            return np.array([], dtype=np.float64)
        return np.concatenate(frames)

    # ---------- writing ----------
    def _write_partition_atomic(self, name: str, df: pd.DataFrame) -> None:
        tmp = self.raw_dir / f"{name}.parquet.tmp"
        df.to_parquet(tmp, index=False)
        os.replace(tmp, self.raw_dir / f"{name}.parquet")   # safe write (spec §12)

    def append(self, df_new: pd.DataFrame, source_info: Optional[Dict] = None) -> Dict:
        """Dedupe + conflict-detect + append accepted rows. Returns a report."""
        report = {"accepted": 0, "duplicates_identical": 0, "conflicts": 0,
                  "out_of_range": 0, "version": self.version}
        if df_new is None or df_new.empty:
            return report
        with self._lock:
            df_new = df_new.sort_values("ts").reset_index(drop=True)
            existing_ts = self.all_timestamps()
            if len(existing_ts):
                known = set(existing_ts.tolist())
                dup_mask = df_new["ts"].isin(known)
                if dup_mask.any():
                    dups = df_new[dup_mask]
                    # identical vs conflicting: compare against stored values
                    stored = self.read_range(float(dups["ts"].min()),
                                             float(dups["ts"].max()))
                    stored_idx = {float(r["ts"]): r for _, r in stored.iterrows()}
                    conflicts = []
                    identical = 0
                    for _, r in dups.iterrows():
                        old = stored_idx.get(float(r["ts"]))
                        if old is None:
                            continue
                        same = all(abs(float(old[c]) - float(r[c])) < 1e-9
                                   for c in ("open", "high", "low", "close")
                                   if pd.notna(old.get(c)) and pd.notna(r.get(c)))
                        if same:
                            identical += 1
                        else:
                            conflicts.append((r, old))
                    report["duplicates_identical"] = identical
                    report["conflicts"] = len(conflicts)
                    if conflicts:
                        self._record_conflicts(conflicts, source_info or {})
                    df_new = df_new[~dup_mask].reset_index(drop=True)
            if df_new.empty:
                return report

            # drop anything not extending the timeline (defensive)
            if self.last_ts is not None:
                df_new = df_new[df_new["ts"] > self.last_ts - 1e-9]
            if df_new.empty:
                return report

            # partition by month and merge with existing partition files
            df_new["_mk"] = [month_key(t) for t in df_new["ts"]]
            touched: Dict[str, pd.DataFrame] = {}
            for mk, group in df_new.groupby("_mk"):
                group = group.drop(columns=["_mk"])
                pf = self.raw_dir / f"{mk}.parquet"
                if pf.exists():
                    old = pd.read_parquet(pf)
                    merged = pd.concat([old, group], ignore_index=True)
                    merged = merged.drop_duplicates(subset=["ts"], keep="first")
                    touched[mk] = merged.sort_values("ts").reset_index(drop=True)
                else:
                    touched[mk] = group.sort_values("ts").reset_index(drop=True)

            for mk, merged in touched.items():
                self._write_partition_atomic(mk, merged)
                pm_ts = merged["ts"]
                self._meta["partitions"][mk] = {
                    "rows": int(len(merged)),
                    "first_ts": float(pm_ts.iloc[0]),
                    "last_ts": float(pm_ts.iloc[-1]),
                    "sha256": _file_sha256(self.raw_dir / f"{mk}.parquet"),
                }
                report["accepted"] += int(len(merged))

            # recompute totals from partition metadata
            rows = sum(p["rows"] for p in self._meta["partitions"].values())
            firsts = [p["first_ts"] for p in self._meta["partitions"].values()]
            lasts = [p["last_ts"] for p in self._meta["partitions"].values()]
            report["accepted"] = rows - int(self._meta.get("rows", 0))
            self._meta["rows"] = rows
            self._meta["first_ts"] = min(firsts) if firsts else None
            self._meta["last_ts"] = max(lasts) if lasts else None
            if report["accepted"] > 0 or report["conflicts"]:
                self._meta["version"] = int(self._meta.get("version", 0)) + 1
            self._meta["conflicts"] = int(self._meta.get("conflicts", 0)) + report["conflicts"]
            self._meta["schema_version"] = DATA_SCHEMA_VERSION
            self._meta["fingerprint"] = self._fingerprint(self._meta)
            self._save_meta(self._meta)
            report["version"] = self.version
            return report

    def _record_conflicts(self, conflicts: List[Tuple], source_info: Dict) -> None:
        """Same timestamp, different values — keep old, record discrepancy (§5)."""
        db = get_db()
        now = time.time()
        rows = []
        for new_r, old_r in conflicts[:500]:
            rows.append((float(new_r["ts"]), now, self.symbol, self.timeframe,
                         self.profile_key,
                         json.dumps({c: (None if pd.isna(old_r.get(c)) else float(old_r[c]))
                                     for c in ("open", "high", "low", "close")}),
                         json.dumps({c: (None if pd.isna(new_r.get(c)) else float(new_r[c]))
                                     for c in ("open", "high", "low", "close")}),
                         source_info.get("source", self.profile.get("source", "")),
                         source_info.get("broker", self.profile.get("broker", "")),
                         source_info.get("server", self.profile.get("server", "")),
                         "kept_old"))
        db.xm("""INSERT INTO data_conflicts
                 (ts,detected_at,symbol,timeframe,profile,old_values,new_values,
                  source,broker,server,resolution) VALUES (?,?,?,?,?,?,?,?,?,?,?)""", rows)
        log.warning("DATA CONFLICT %s %s: %d timestamp(s) returned different values "
                    "(old kept, discrepancy recorded)", self.symbol, self.timeframe,
                    len(rows))


def get_master(symbol: str, timeframe: str, profile: Dict[str, str]) -> MasterStore:
    return MasterStore(symbol, timeframe, profile)
