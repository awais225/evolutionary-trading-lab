"""
Market-data integrity gate (spec §7).

Every batch of bars coming from a bridge (MT5 or SIMULATOR) is validated
BEFORE it is merged into a master dataset:

  timestamp ordering / duplicates / timeframe spacing
  OHLC consistency, positive prices, null checks
  bid/ask consistency, spread sanity
  unexpected gap detection (informational — market closures are normal)

Bad rows are FLAGGED, LOGGED and QUARANTINED to
DATA/{symbol}/quarantine/{tf}_{profile}_{ts}.parquet — never silently
deleted. A report row goes into the SQLite `integrity_reports` table and an
activity event is emitted.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

log = logging.getLogger("data.integrity")

TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800,
              "H1": 3600, "H4": 14400, "D1": 86400}


@dataclass
class IntegrityReport:
    ok: bool = True
    rows_in: int = 0
    rows_accepted: int = 0
    rows_quarantined: int = 0
    issues: Dict[str, int] = field(default_factory=dict)
    duplicate_ts: int = 0
    gaps: List[Tuple[float, float]] = field(default_factory=list)  # (prev_ts, next_ts)
    quarantine_path: str = ""

    def as_dict(self) -> Dict:
        return {"ok": self.ok, "rows_in": self.rows_in,
                "rows_accepted": self.rows_accepted,
                "rows_quarantined": self.rows_quarantined,
                "issues": dict(self.issues), "duplicate_ts": self.duplicate_ts,
                "gaps": self.gaps[:20], "gap_count": len(self.gaps),
                "quarantine_path": self.quarantine_path}


def validate_batch(df: pd.DataFrame, timeframe: str,
                   quarantine_dir: Optional[Path] = None,
                   tag: str = "") -> Tuple[pd.DataFrame, IntegrityReport]:
    """Split `df` into (accepted, quarantined) and produce a report.

    The accepted frame is timestamp-ordered, duplicate-free and passes all
    row-level checks. Nothing is deleted: rejected rows are written to the
    quarantine directory when one is provided.
    """
    rep = IntegrityReport(rows_in=len(df))
    if df is None or df.empty:
        rep.ok = False
        rep.issues["empty_batch"] = 1
        return df, rep

    work = df.copy()
    bad = pd.Series(False, index=work.index)

    def flag(mask: pd.Series, name: str) -> None:
        nonlocal bad
        n = int(mask.sum())
        if n:
            rep.issues[name] = rep.issues.get(name, 0) + n
            bad = bad | mask.fillna(False)

    # --- nulls / non-finite on required numeric columns ---
    required = ["ts", "open", "high", "low", "close"]
    for c in required:
        if c not in work.columns:
            rep.ok = False
            rep.issues[f"missing_column:{c}"] = 1
            return work.iloc[0:0], rep
        vals = pd.to_numeric(work[c], errors="coerce")
        flag(vals.isna(), f"null_{c}")

    # --- positive prices ---
    for c in ("open", "high", "low", "close"):
        vals = pd.to_numeric(work[c], errors="coerce")
        flag(~(vals > 0), f"nonpositive_{c}")

    # --- OHLC consistency ---
    o = pd.to_numeric(work["open"], errors="coerce")
    h = pd.to_numeric(work["high"], errors="coerce")
    l = pd.to_numeric(work["low"], errors="coerce")
    c = pd.to_numeric(work["close"], errors="coerce")
    flag(h < l, "high_below_low")
    flag(h < o.combine(c, max) - 1e-9, "high_below_body")
    flag(l > o.combine(c, min) + 1e-9, "low_above_body")

    # --- bid/ask consistency (when present) ---
    if "bid" in work.columns and "ask" in work.columns:
        bid = pd.to_numeric(work["bid"], errors="coerce")
        ask = pd.to_numeric(work["ask"], errors="coerce")
        both = bid.notna() & ask.notna() & (bid > 0) & (ask > 0)
        flag(both & (bid > ask), "bid_above_ask")
        if "spread" in work.columns:
            sp = pd.to_numeric(work["spread"], errors="coerce")
            flag(both & (sp.notna()) & (sp < 0), "negative_spread")
            # impossible spread: > 2% of price
            px = c.where(c > 0, 1.0)
            flag(both & sp.notna() & (sp * 0.01 > 0.02 * px), "impossible_spread")

    # --- timestamps: ordering & duplicates ---
    ts = pd.to_numeric(work["ts"], errors="coerce")
    dup_mask = ts.duplicated(keep="first")
    rep.duplicate_ts = int(dup_mask.sum())
    flag(dup_mask, "duplicate_ts")
    unsorted = int((ts.diff().dropna() < 0).sum())
    if unsorted:
        rep.issues["out_of_order"] = unsorted   # fixed by sorting, not rejected

    accepted = work[~bad].copy()
    accepted = accepted.sort_values("ts").reset_index(drop=True)
    quarantined = work[bad]

    # --- gap detection on the accepted stream (informational) ---
    step = TF_SECONDS.get(timeframe, 900)
    if len(accepted) > 1:
        ats = accepted["ts"].to_numpy(dtype=np.float64)
        d = np.diff(ats)
        gap_idx = np.where(d > step * 1.5)[0]
        for gi in gap_idx[:50]:
            prev_ts, next_ts = float(ats[gi]), float(ats[gi + 1])
            # weekends/closures are normal: only count weekday-internal gaps
            wd = pd.Timestamp(prev_ts, unit="s", tz="UTC").dayofweek
            if wd < 4 or (wd == 4 and pd.Timestamp(prev_ts, unit="s", tz="UTC").hour < 20):
                rep.gaps.append((prev_ts, next_ts))

    rep.rows_accepted = len(accepted)
    rep.rows_quarantined = len(quarantined)
    rep.ok = rep.rows_quarantined == 0 and not rep.issues.get("missing_column")

    if len(quarantined) and quarantine_dir is not None:
        try:
            quarantine_dir.mkdir(parents=True, exist_ok=True)
            qp = quarantine_dir / f"quarantine_{tag}_{int(time.time())}.parquet"
            quarantined.to_parquet(qp, index=False)
            rep.quarantine_path = str(qp)
            log.warning("integrity: quarantined %d rows -> %s (issues=%s)",
                        len(quarantined), qp, rep.issues)
        except Exception as e:
            log.error("integrity: quarantine write failed: %s", e)

    if rep.issues:
        log.warning("integrity report %s: %s", tag or "-", rep.as_dict())
    return accepted, rep
