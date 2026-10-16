"""V6.6 §10 — manual CSV import: parse, validate, preview, safe transactional
merge into the persistent candle store.

Supported inputs
----------------
* **MT5 "Export to CSV"** candle files: ``<DATE> <TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<TICK_VOL>,<VOL>,<SPREAD>``
  (no header, comma or semicolon separated) and the headered variant.
* Common generic candle CSVs whose fields map UNAMBIGUOUSLY (timestamp/date+time,
  open/high/low/close, optional tick volume / real volume / spread).

Rules enforced here (§10):
* ambiguous columns/units are never guessed — the import returns the mapping it
  needs and the user confirms symbol/timeframe/timezone explicitly;
* timestamps keep their original interpretation as provenance and are normalised
  to the lab's canonical UTC seconds; a missing/ambiguous timezone REQUIRES user
  confirmation before commit;
* validation covers types, OHLC consistency, non-positive prices, duplicate and
  unordered timestamps, gaps, overlap with the existing store, and claimed-vs-
  actual coverage (coverage comes from the ROWS, never the filename);
* commit merges ONLY compatible rows into ``MasterStore`` (stored trusted bars
  win over conflicting imports; every conflict is recorded and reported), in a
  single write, and never deletes or replaces valid existing history.

The validator works on the parsed frame; the committer writes it. A failed
import leaves the cache untouched.
"""
from __future__ import annotations

import hashlib
import io
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .coverage import _scan_gaps, _iso, tf_seconds

#: columns the candle store accepts as semantic fields
SEMANTIC_FIELDS = ("ts", "open", "high", "low", "close", "tick_volume", "volume", "spread")

HEADER_ALIASES = {
    "ts": ("ts", "timestamp", "time_stamp", "epoch"),
    "date": ("date", "day", "<date>", "date_time", "datetime"),
    "time": ("time", "<time>"),
    "open": ("open", "o", "<open>", "open_price"),
    "high": ("high", "h", "<high>", "high_price"),
    "low": ("low", "l", "<low>", "low_price"),
    "close": ("close", "c", "<close>", "close_price"),
    "tick_volume": ("tick_vol", "tick_volume", "tickvol", "volume", "vol", "<tickvol>", "real_volume"),
    "volume": ("vol", "volume", "real_volume", "<vol>"),
    "spread": ("spread", "<spread>", "spr"),
}

MAX_PREVIEW_ROWS = 20000
REJECT_LIST_LIMIT = 50


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _norm_header(h: str) -> str:
    return re.sub(r"[^a-z0-9_<>]", "", str(h or "").strip().lower())


def detect_format(sample_text: str) -> Dict[str, Any]:
    """Sniff delimiter/header/MT5-ness from the first lines."""
    lines = [ln for ln in sample_text.splitlines() if ln.strip()][:10]
    if not lines:
        return {"ok": False, "error": "the file is empty"}
    for delim in (",", ";", "\t"):
        if delim in lines[0]:
            break
    else:
        delim = None
    first = [c.strip() for c in (lines[0].split(delim) if delim else [lines[0]])]
    # a HEADER cell is a letter-based token ("Open", "<TICKVOL>", "date"); a data
    # cell never is — "2024-01-02T00:00:00" contains letters but also digits/colons
    headerish = any(re.fullmatch(r"[A-Za-z_<>\[\] ()/#%.\-]+", c) and re.search(r"[A-Za-z]", c)
                    for c in first)
    # MT5 export: "<DATE> <TIME>" in column 0 or date+time split across 1-2 cols
    mt5_like = bool(re.match(r"^\d{4}\.\d{2}\.\d{2}", lines[0].strip())) or \
               (headerish and any(_norm_header(c) in ("date", "<date>") for c in first))
    return {"ok": True, "delimiter": delim or ",", "has_header": headerish,
            "mt5_like": mt5_like, "first_columns": first}


def parse_csv(raw: bytes, *, filename: str = "", delimiter: Optional[str] = None,
              has_header: Optional[bool] = None,
              timestamp_mode: Optional[str] = None,
              timezone_offset_hours: Optional[float] = None, source_timezone: Optional[str] = None) -> Dict[str, Any]:
    """Parse a candle CSV into a raw frame + mapping report. No writes.

    ``timestamp_mode`` ∈ ``auto|epoch_s|epoch_ms|mt5|iso`` — default auto.
    ``timezone_offset_hours`` interprets naive local timestamps as
    ``UTC = local - offset``; when timestamps carry no zone info and no offset is
    given, the result is flagged ``timezone_ambiguous`` and COMMIT IS REFUSED
    until the user confirms (§10).
    """
    text = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return {"ok": False, "error": "the file is not decodable text (tried utf-8/latin-1)"}

    fmt = detect_format(text)
    if not fmt.get("ok"):
        return fmt
    delim = delimiter or fmt["delimiter"]
    header = has_header if has_header is not None else fmt["has_header"]

    try:
        df = pd.read_csv(io.StringIO(text), sep=delim, header=0 if header else None,
                         engine="python")
    except Exception as e:
        return {"ok": False, "error": f"CSV parse failed: {type(e).__name__}: {e}"}
    if not header:
        df.columns = [str(c) for c in df.columns]

    mapping: Dict[str, str] = {}
    cols = list(df.columns)
    norm_cols = {c: _norm_header(c) for c in cols}

    def find_col(*aliases):
        for c, n in norm_cols.items():
            if n in aliases:
                return c
        return None

    # date/time or single timestamp column
    date_col = find_col(*HEADER_ALIASES["date"])
    time_col = find_col(*HEADER_ALIASES["time"])
    ts_col = find_col(*HEADER_ALIASES["ts"])
    for key, aliases in (("open", HEADER_ALIASES["open"]), ("high", HEADER_ALIASES["high"]),
                         ("low", HEADER_ALIASES["low"]), ("close", HEADER_ALIASES["close"]),
                         ("tick_volume", HEADER_ALIASES["tick_volume"]),
                         ("volume", HEADER_ALIASES["volume"]),
                         ("spread", HEADER_ALIASES["spread"])):
        c = find_col(*aliases)
        if c is not None:
            mapping[key] = str(c)
    missing = [k for k in ("open", "high", "low", "close") if k not in mapping]
    if missing:
        # headerless MT5 export — positional. Two shapes exist:
        #   "YYYY.MM.DD HH:MM",O,H,L,C[,TICK_VOL[,VOL]][,SPREAD]   (date+time one field)
        #   "YYYY.MM.DD","HH:MM",O,H,L,C[,...]                     (two fields)
        # OHLC starts right after the leading time column(s); SPREAD is last.
        if not header and df.shape[1] >= 5:
            ncol = df.shape[1]
            c0 = df.iloc[: min(5, len(df)), 0]
            c0_str = c0.astype(str)
            c0_numeric = bool(pd.to_numeric(c0, errors="coerce").notna().all())
            joint_time = bool(c0_str.str.contains(r"\d:\d").any())
            # one leading column when it is a single timestamp (epoch or
            # 'YYYY.MM.DD HH:MM'); two when date and time are separate fields
            ohlc0 = 1 if (c0_numeric or joint_time) else 2
            if ncol >= ohlc0 + 4:
                mapping = {"open": str(ohlc0), "high": str(ohlc0 + 1),
                           "low": str(ohlc0 + 2), "close": str(ohlc0 + 3)}
                remaining = ncol - (ohlc0 + 4)
                if remaining >= 1:
                    mapping["tick_volume"] = str(ohlc0 + 4)
                if remaining >= 2:
                    mapping["spread"] = str(ncol - 1)     # MT5 puts SPREAD last
                ts_col = "0"
                date_col = "0"
                time_col = None if joint_time else "1"
                missing = []
        if missing:
            return {"ok": False, "error": f"could not map required columns: {', '.join(missing)}",
                    "columns": [str(c) for c in cols],
                    "hint": "map the OHLC columns explicitly, or export the file from MT5 "
                            "(Date Time Open High Low Close Tick_Vol Volume Spread)"}

    # ---- timestamps ----
    tmode = (timestamp_mode or "auto").lower()
    ts_values = None
    tz_source = None
    try:
        if ts_col is not None and tmode in ("auto", "epoch_s", "epoch_ms"):
            raw_ts = pd.to_numeric(df[ts_col], errors="coerce")
            if raw_ts.notna().mean() > 0.9:
                med = float(raw_ts.dropna().median()) if raw_ts.notna().any() else 0
                unit = "ms" if (tmode == "epoch_ms" or (tmode == "auto" and med > 1e11)) else "s"
                ts_values = pd.to_datetime(raw_ts, unit=unit, utc=True)
                tz_source = f"epoch_{unit} (absolute UTC — no timezone ambiguity)"
                tmode = f"epoch_{unit}"
        if ts_values is None:
            if date_col is not None and time_col is not None:
                joined = (df[date_col].astype(str).str.strip() + " " +
                          df[time_col].astype(str).str.strip())
                ts_values, had_tz = _parse_dt_series(joined, timezone_offset_hours, source_timezone)
            elif date_col is not None:
                ts_values, had_tz = _parse_dt_series(df[date_col].astype(str), timezone_offset_hours, source_timezone)
            elif ts_col is not None:
                ts_values, had_tz = _parse_dt_series(df[ts_col].astype(str), timezone_offset_hours, source_timezone)
            else:
                return {"ok": False, "error": "no timestamp/date column found"}
            if had_tz:
                tz_source = "explicit timezone/offset information in the file's timestamps"
            if tmode in ("auto", "iso", "mt5"):
                tmode = "mt5_or_iso"
    except Exception as e:
        return {"ok": False, "error": f"timestamp parse failed: {type(e).__name__}: {e}"}

    if ts_values is None or len(ts_values) == 0:
        return {"ok": False, "error": "timestamps could not be parsed"}
    ts_values = pd.to_datetime(ts_values, utc=True)
    # unit-safe epoch seconds (pandas may hold datetime64[us] or [ns])
    epoch_s = ((ts_values - pd.Timestamp("1970-01-01", tz="UTC"))
               / pd.Timedelta(seconds=1)).astype("float64")
    tz_ambiguous = (tz_source is None and timezone_offset_hours is None and not source_timezone)
    if source_timezone:
        tz_source = "explicit source timezone: " + source_timezone
    if tz_source is None:
        if timezone_offset_hours is not None:
            tz_source = f"naive local timestamps interpreted as UTC = local - ({timezone_offset_hours}h)"
        else:
            tz_source = "UNKNOWN — naive timestamps with no timezone information"

    out = pd.DataFrame({
        "ts": np.asarray(epoch_s, dtype="float64"),
        "open": pd.to_numeric(df[mapping["open"]], errors="coerce"),
        "high": pd.to_numeric(df[mapping["high"]], errors="coerce"),
        "low": pd.to_numeric(df[mapping["low"]], errors="coerce"),
        "close": pd.to_numeric(df[mapping["close"]], errors="coerce"),
    })
    if "tick_volume" in mapping:
        out["tick_volume"] = pd.to_numeric(df[mapping["tick_volume"]], errors="coerce").fillna(0)
    if "volume" in mapping:
        out["volume"] = pd.to_numeric(df[mapping["volume"]], errors="coerce").fillna(0)
    if "spread" in mapping:
        out["spread"] = pd.to_numeric(df[mapping["spread"]], errors="coerce")

    return {
        "ok": True, "frame": out, "rows_read": int(len(out)),
        "mapping": mapping, "timestamp_mode": tmode,
        "timezone_source": tz_source,
        "timezone_ambiguous": bool(tz_ambiguous),
        "filename": filename, "file_sha256": _sha256_bytes(raw),
        "delimiter": delim, "has_header": header,
        "source_first": str(df.iloc[0][date_col]) + (" " + str(df.iloc[0][time_col]) if time_col else "") if len(df) and date_col else None,
        "source_last": str(df.iloc[-1][date_col]) + (" " + str(df.iloc[-1][time_col]) if time_col else "") if len(df) and date_col else None,
    }


def _parse_dt_series(series: pd.Series, tz_offset: Optional[float], source_timezone: Optional[str] = None) -> Tuple[pd.Series, bool]:
    """Parse MT5 'YYYY.MM.DD HH:MM[:SS]' or ISO strings; return (tz-aware UTC, had_tz).

    Naive timestamps (no zone info anywhere) are interpreted as
    ``UTC = local - tz_offset`` where ``tz_offset`` comes from the USER (§10);
    ``tz_offset=None`` means the caller still has to confirm — the parse itself
    treats them as UTC and the report flags the ambiguity and refuses commit.
    """
    s = series.str.replace(r"(\d{4})[./-](\d{2})[./-](\d{2})", r"\1-\2-\3", regex=True)
    parsed = pd.to_datetime(s, errors="coerce", format="mixed")
    if parsed.isna().all():
        parsed = pd.to_datetime(s, errors="coerce", dayfirst=False)
    if parsed.isna().all():
        raise ValueError("timestamps are neither MT5 'YYYY.MM.DD HH:MM' nor parseable dates")
    if getattr(parsed.dt, "tz", None) is not None:
        return parsed.dt.tz_convert("UTC"), True
    if source_timezone:
        from ..data.history import localize_source
        return localize_source(parsed, source_timezone), True
    off = 0.0 if tz_offset is None else float(tz_offset)
    return parsed.dt.tz_localize("UTC") - pd.Timedelta(hours=off), False


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
def validate_frame(raw: bytes, *, symbol: str, timeframe: str,
                   filename: str = "", timezone_offset_hours: Optional[float] = None,
                   timestamp_mode: Optional[str] = None, source_timezone: Optional[str] = None,
                   target_first_ts: Optional[float] = None,
                   target_last_ts: Optional[float] = None) -> Dict[str, Any]:
    """Parse + validate a candidate import. Never writes.

    Returns a full §10 report. ``ok`` is True only when the file is importable
    as interpreted (unclear interpretation is reported and refused, never
    guessed).
    """
    import math
    if timezone_offset_hours is not None and (not math.isfinite(float(timezone_offset_hours)) or abs(float(timezone_offset_hours))>14):
        return {"ok":False,"error":"fixed source UTC offset must be finite and within -14..14 hours"}
    parsed = parse_csv(raw, filename=filename, timezone_offset_hours=timezone_offset_hours,
                       timestamp_mode=timestamp_mode, source_timezone=source_timezone)
    if not parsed.get("ok"):
        return parsed
    df = parsed["frame"]
    tf_s = tf_seconds(timeframe)
    warnings: List[str] = []
    rejects: List[Dict[str, Any]] = []

    if parsed.get("timezone_ambiguous"):
        warnings.append(
            "the file's timestamps carry no timezone information — confirm the timezone "
            "(or provide an offset) before the import can be committed")
    if not tf_s:
        return {"ok": False, "error": f"unknown timeframe {timeframe!r}"}

    rows_read = len(df)
    # ---- row-level validation ----
    bad_type = ~np.isfinite(df[["open", "high", "low", "close"]]).all(axis=1)
    non_positive = (df[["open", "high", "low", "close"]] <= 0).any(axis=1)
    bad_ohlc = (df["high"] < df[["open", "close", "low"]].max(axis=1) - 1e-12) | \
               (df["low"] > df[["open", "close", "high"]].min(axis=1) + 1e-12) | \
               (df["high"] < df["low"] - 1e-12)
    bad_ts = ~np.isfinite(df["ts"])

    reject_mask = bad_type | non_positive | bad_ohlc | bad_ts
    for i in df.index[reject_mask][:REJECT_LIST_LIMIT]:
        reason = ("timestamp unparseable" if bad_ts[i] else
                  "non-numeric OHLC" if bad_type[i] else
                  "non-positive price" if non_positive[i] else "OHLC inconsistent "
                  "(high/low do not contain open/close)")
        rejects.append({"row": int(i) + 1, "reason": reason})

    good = df[~reject_mask].copy()
    dup_mask = good.duplicated(subset=["ts"], keep="first")
    n_dupes = int(dup_mask.sum())
    duplicate_prices=good[good.duplicated('ts',keep=False)]
    conflict_timestamps=[]
    if len(duplicate_prices):
        differences=duplicate_prices.groupby('ts')[['open','high','low','close']].nunique().max(axis=1)
        conflict_timestamps=[float(t) for t in differences[differences>1].index]
    good = good[~dup_mask].sort_values("ts").reset_index(drop=True)

    ordered = bool(df.loc[~reject_mask, "ts"].is_monotonic_increasing)

    # §10 — reject a mismapped timeframe outright. The rows' own spacing is the
    # only honest evidence of what timeframe they are: if the tightest bar gap is
    # not the claimed interval, this file does not belong to that store. Never
    # convert, never guess (a gapped fragment whose tightest gap is 2x+ the
    # claimed bar is refused too — the user is told the real spacing and can
    # re-validate under the right timeframe).
    interval_mismatch = None
    if len(good) >= 2:
        diffs = np.diff(good["ts"].to_numpy(np.float64))
        pos = diffs[diffs > 0]
        if len(pos):
            min_diff = float(pos.min())
            if min_diff < 0.8 * tf_s or min_diff > 1.5 * tf_s:
                interval_mismatch = {"tightest_bar_gap_seconds": min_diff,
                                     "claimed_timeframe_seconds": int(tf_s)}

    gap_info = _scan_gaps(good["ts"].to_numpy(np.float64), tf_s) if len(good) else {}
    first_ts = float(good["ts"].iloc[0]) if len(good) else None
    last_ts = float(good["ts"].iloc[-1]) if len(good) else None

    # coverage honesty: what the ROWS cover, never the filename
    span_days = round((last_ts - first_ts) / 86400.0, 2) if first_ts is not None else 0.0

    # overlap with the existing persisted store
    overlap = {"existing_bars": 0, "overlap_bars": 0, "conflicts_possible": False}
    try:
        from .coverage import _mstore
        mstore = _mstore(symbol, timeframe)
        overlap["existing_bars"] = int(mstore.rows or 0)
        if len(good) and overlap["existing_bars"]:
            existing_ts = mstore.all_timestamps()
            if existing_ts is not None and len(existing_ts):
                known = set(np.asarray(existing_ts, dtype="float64").tolist())
                overlap["overlap_bars"] = int(good["ts"].isin(list(known)).sum()) \
                    if len(good) < 500000 else int(good["ts"].isin(known).sum())
                overlap["conflicts_possible"] = overlap["overlap_bars"] > 0
                if overlap["overlap_bars"]:
                    warnings.append(
                        f"{overlap['overlap_bars']} timestamp(s) already exist in the store — "
                        "identical rows are skipped; conflicting rows keep the STORED value and "
                        "are recorded as conflicts (deterministic policy: stored data wins)")
    except Exception as e:
        warnings.append(f"existing-coverage check failed: {type(e).__name__}: {e}")

    report = {
        "ok": bool(len(good)) and not parsed.get("timezone_ambiguous") and not interval_mismatch,
        "needs_timezone_confirmation": bool(parsed.get("timezone_ambiguous")),
        "timeframe_mismatch": interval_mismatch,
        "error": (f"the rows are spaced like {interval_mismatch['tightest_bar_gap_seconds']:g}s "
                  f"bars but this import claims {str(timeframe).upper()} "
                  f"({interval_mismatch['claimed_timeframe_seconds']}s) — refusing a "
                  f"timeframe mismatch; validate again with the correct timeframe"
                  if interval_mismatch else None),
        "filename": filename or parsed.get("filename"),
        "file_sha256": parsed.get("file_sha256"),
        "symbol": str(symbol).upper(),
        "timeframe": str(timeframe).upper(),
        "rows_read": int(rows_read),
        "rows_valid": int(len(good)),
        "rows_rejected": int(reject_mask.sum()),
        "rows_duplicate": n_dupes,
        "within_file_conflicting_timestamps":len(conflict_timestamps),
        "within_file_conflicts_sample":conflict_timestamps[:50],
        "rejects_sample": rejects,
        "rejects_truncated": bool(int(reject_mask.sum()) > len(rejects)),
        "earliest_ts": first_ts, "latest_ts": last_ts,
        "earliest": _iso(first_ts), "latest": _iso(last_ts),
        "actual_coverage_days": span_days,
        "actual_coverage_label": (f"{span_days / 365.25:.2f} years" if span_days >= 365
                                  else f"{span_days / 30.44:.1f} months" if span_days >= 60
                                  else f"{span_days:.1f} days"),
        "ordered": ordered,
        "gap_count": int(gap_info.get("gap_count") or 0),
        "missing_bars_total": int(gap_info.get("missing_bars_total") or 0),
        "missing_periods": gap_info.get("missing_periods") or [],
        "duplicate_timestamps": n_dupes,
        "overlap_with_store": overlap,
        "timezone_source": parsed.get("timezone_source"),
        "timestamp_mode": parsed.get("timestamp_mode"),
        "mapping": parsed.get("mapping"),
        "warnings": warnings,
        "frame": good,
        "frame_truncated": False,
        "preview_truncated": bool(len(good) > MAX_PREVIEW_ROWS),
        "source_first": parsed.get("source_first"), "source_last": parsed.get("source_last"),
        "source_timezone": source_timezone,
        "interval_distribution": {str(int(k)): int(v) for k,v in pd.Series(np.diff(good["ts"])).value_counts().head(12).items()},
        "note": ("coverage above is what the FILE's rows actually cover — never the "
                 "filename or a claimed period"),
    }
    return report


# --------------------------------------------------------------------------- #
# commit
# --------------------------------------------------------------------------- #
def commit_import(report: Dict[str, Any], *, symbol: str, timeframe: str,
                  timezone_confirmed: Optional[str] = None) -> Dict[str, Any]:
    """Merge a validated import into the persistent store (single write).

    Compatible rows only; stored history is preserved; conflicts keep the stored
    value and are recorded with full provenance.
    """
    if not report:
        return {"ok": False, "error": "nothing importable was validated — nothing was written"}
    if report.get("needs_timezone_confirmation") and not timezone_confirmed:
        return {"ok": False, "needs_timezone_confirmation": True,
                "error": "the timezone must be confirmed before committing this file"}
    if not report.get("ok"):
        return {"ok": False, "error": "nothing importable was validated — nothing was written"}
    frame = report.get("frame")
    if frame is None or not len(frame):
        return {"ok": False, "error": "the validated frame is empty — nothing was written"}

    from .coverage import _mstore
    import pandas as pd
    df = frame.copy()
    # normalise through the same engine normaliser the MT5 sync path uses
    # (adds session/hour/dow/bid/ask/spread columns the store schema requires)
    from ..data.engine import get_data_engine
    df = get_data_engine()._normalize(df)          # shared read-only helper, no change

    mstore = _mstore(symbol, timeframe)
    source_info = {
        "source_type": "CSV_IMPORT",
        "filename": report.get("filename"),
        "file_sha256": report.get("file_sha256"),
        "imported_at": time.time(),
        "timezone": report.get("timezone_source"),
        "confirmation": timezone_confirmed,
    }
    before=mstore.rows
    result = mstore.append(df, source_info=source_info)

    # provenance row
    from . import store as S
    from ..db.database import get_db
    db = get_db()
    S.ensure_schema(db)
    S.insert_candle_source(db, {
        "symbol": str(symbol).upper(), "timeframe": str(timeframe).upper(),
        "source_type": "CSV_IMPORT",
        "filename": report.get("filename"), "file_sha256": report.get("file_sha256"),
        "timezone": report.get("timezone_source"),
        "confirmation": timezone_confirmed,
        "rows_read": report.get("rows_read"), "rows_valid": report.get("rows_valid"),
        "rows_rejected": report.get("rows_rejected"), "rows_duplicate": report.get("rows_duplicate"),
        "earliest_ts": report.get("earliest_ts"), "latest_ts": report.get("latest_ts"),
        "fingerprint": mstore.fingerprint,
        "user_mapping": {"mapping": report.get("mapping"),
                         "timestamp_mode": report.get("timestamp_mode"),
                         "timezone_confirmed": timezone_confirmed},
        "notes": (f"merge accepted={result.get('accepted')} "
                  f"identical_dupes={result.get('duplicates_identical')} "
                  f"conflicts_stored_wins={result.get('conflicts')}"),
    })

    from .coverage import pair_coverage
    return {
        "ok": True,
        "merge": result,
        "existing_before":before,"target_profile":mstore.profile_key,
        "symbol": symbol, "timeframe": timeframe,
        "available_unique_bars": mstore.rows, "first_ts": mstore.first_ts, "last_ts": mstore.last_ts,
        "rows_rejected": report.get("rows_rejected"), "timezone": source_info["timezone"],
        "policy": ("stored data wins over conflicting imported candles; identical "
                   "duplicates are skipped; every conflict is recorded"),
        "coverage_after": pair_coverage(symbol, timeframe),
    }
