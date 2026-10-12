"""V6.6 §7/§9 — persisted historical-coverage inspection and the pre-run
coverage check behind the mandatory incomplete-history confirmation.

Coverage is read from the persisted candle store (``MasterStore``) that the rest
of the lab already writes to. Gap convention matches the existing historical-run
coverage report exactly (``historical_backtest.runs._coverage_report``): two
consecutive bars form a gap when their spacing exceeds 1.5× the timeframe; and
completeness is measured against a continuous 24/7 grid, with the honest note
that session/weekend closures are normal stored data — the gaps are listed for
inspection instead of being hidden behind the percentage.

Nothing here fetches, fabricates or fills bars.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Dict, List, Optional, Tuple

from .periods import label_of

TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600,
              "H4": 14400, "D1": 86400, "W1": 604800}

MAX_LISTED_GAPS = 25

#: §9 — an internal hole at least this long is missing history (PARTIAL);
#: shorter holes (week-end/session closures, up to ~74h) are quality warnings
MATERIAL_GAP_HOURS = 96


def tf_seconds(timeframe: str) -> Optional[int]:
    """Only the candle timeframes the lab itself recognises — an exotic label is
    refused rather than silently interpreted (§ data-integrity: never silently
    substitute or guess a timeframe)."""
    tf = str(timeframe or "").upper()
    return TF_SECONDS.get(tf)


def _iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    try:
        import datetime as dt
        return dt.datetime.fromtimestamp(float(ts), tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None


def _mstore(symbol: str, timeframe: str):
    from ..data.master import get_master
    from ..mt5.factory import get_bridge
    return get_master(str(symbol).upper(), str(timeframe).upper(), get_bridge().profile())


def _scan_gaps(ts_sorted, tf_s: int) -> Dict[str, Any]:
    """Gap scan over a sorted float64 timestamp sequence."""
    import numpy as np
    ts = np.asarray(ts_sorted, dtype="float64")
    out: Dict[str, Any] = {"gap_count": 0, "missing_bars_total": 0,
                           "missing_periods": [], "missing_periods_truncated": False,
                           "duplicate_timestamps": 0, "monotonic_increasing": True}
    if ts.size == 0:
        return out
    out["duplicate_timestamps"] = int(ts.size - np.unique(ts).size)
    out["monotonic_increasing"] = bool(np.all(np.diff(ts) > 0))
    if ts.size > 1:
        step = np.diff(ts)
        idx = np.where(step > 1.5 * tf_s)[0]
        out["gap_count"] = int(idx.size)
        out["missing_bars_total"] = int(sum(int(round(float(step[i]) / tf_s)) - 1 for i in idx))
        out["missing_periods"] = [
            {"after": _iso(float(ts[i])), "before": _iso(float(ts[i + 1])),
             "after_ts": float(ts[i]), "before_ts": float(ts[i + 1]),
             "missing_bars": int(round(float(step[i]) / tf_s)) - 1}
            for i in idx[:MAX_LISTED_GAPS]]
        out["missing_periods_truncated"] = bool(idx.size > MAX_LISTED_GAPS)
    return out


def _span_label(days: float) -> str:
    if days >= 365:
        return f"{days / 365.25:.2f} years ({days:.0f} days)"
    if days >= 60:
        return f"{days / 30.44:.1f} months ({days:.0f} days)"
    return f"{days:.1f} days"


def pair_coverage(symbol: str, timeframe: str,
                  range_start: float | None = None,
                  range_end: float | None = None) -> Dict[str, Any]:
    """The MT5 DATA panel payload for one (symbol, timeframe) cache key.

    V6.8 §7.1 — when a test range is supplied, ``bars_in_range`` is a REAL
    count of the persisted timestamps inside it (never estimated): the range
    bar count can differ from the total stored bars when the cache holds extra
    history, and stays ``None`` (unavailable) when it cannot be counted."""
    import numpy as np
    tf_s = tf_seconds(timeframe)
    rec: Dict[str, Any] = {
        "symbol": str(symbol or "").upper(),
        "timeframe": str(timeframe or "").upper(),
        "bars": 0, "first_ts": None, "last_ts": None,
        "first": None, "last": None,
        "coverage_label": None, "coverage_days": None,
        "source": None, "broker": None, "server": None,
        "fingerprint": None, "version": None,
        "last_fetch": None, "fetch_status": None,
        "quality": {}, "missing_periods": [],
        "bars_in_range": None, "range_start": range_start, "range_end": range_end,
        "warnings": [], "error": None,
        "cache_key": f"{str(symbol or '').upper()}|{str(timeframe or '').upper()}",
        "checked_at": time.time(),
    }
    if not rec["symbol"] or not rec["timeframe"] or tf_s is None:
        rec["error"] = "symbol and a known candle timeframe are required"
        rec["warnings"].append(rec["error"])
        return rec
    try:
        mstore = _mstore(rec["symbol"], rec["timeframe"])
        info = mstore.info() or {}
        rec.update({
            "bars": int(info.get("rows") or 0),
            "first_ts": info.get("first_ts"),
            "last_ts": info.get("last_ts"),
            "version": info.get("version"),
            "fingerprint": info.get("fingerprint"),
            "source": (info.get("profile") or {}).get("source") or info.get("source"),
            "broker": (info.get("profile") or {}).get("broker") or info.get("broker"),
            "server": (info.get("profile") or {}).get("server") or info.get("server"),
        })
        rec["first"] = _iso(rec["first_ts"])
        rec["last"] = _iso(rec["last_ts"])
        if rec["first_ts"] is not None and rec["last_ts"] is not None:
            days = (float(rec["last_ts"]) - float(rec["first_ts"])) / 86400.0
            rec["coverage_days"] = round(days, 2)
            rec["coverage_label"] = _span_label(days)
        # gap/quality scan over the persisted timestamps
        try:
            ts = mstore.all_timestamps()
            if ts is not None and len(ts) > 0:
                arr_ts = np.sort(np.asarray(ts, dtype="float64"))
                if range_start is not None and range_end is not None:
                    # REAL count of persisted bars inside the requested range
                    rec["bars_in_range"] = int(np.sum(
                        (arr_ts >= float(range_start)) & (arr_ts <= float(range_end))))
                g = _scan_gaps(arr_ts, tf_s)
                rec["quality"] = {
                    "bars": int(len(ts)),
                    "monotonic_increasing": g["monotonic_increasing"],
                    "duplicate_timestamps": g["duplicate_timestamps"],
                    "gaps": g["gap_count"],
                    "missing_bars_total": g["missing_bars_total"],
                }
                rec["missing_periods"] = g["missing_periods"]
                rec["missing_periods_truncated"] = g["missing_periods_truncated"]
                if g["gap_count"]:
                    rec["warnings"].append(
                        f"{g['gap_count']} internal gap(s), {g['missing_bars_total']} missing bar(s) "
                        f"— listed for inspection; week-end/session closures are normal stored data")
                if g["duplicate_timestamps"]:
                    rec["warnings"].append(
                        f"{g['duplicate_timestamps']} duplicate timestamp(s) present")
        except Exception as e:
            rec["warnings"].append(f"gap scan unavailable: {type(e).__name__}: {e}")
        # last fetch/import provenance
        try:
            from ..data.engine import get_data_engine
            ds = get_data_engine().latest_dataset(rec["symbol"], rec["timeframe"], require_eligible=False)
            if ds:
                rec.setdefault("dataset_id", ds.get("id"))
                rec["fetch_status"] = "stored dataset materialised"
        except Exception:
            pass
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {e}"
        rec["warnings"].append(f"coverage unavailable: {rec['error']}")
    return rec


def coverage_for_pairs(pairs: List[Tuple[str, str]],
                       range_start: float | None = None,
                       range_end: float | None = None) -> Dict[str, Dict[str, Any]]:
    return {f"{s}|{t}": pair_coverage(s, t, range_start=range_start, range_end=range_end)
            for s, t in pairs}


# --------------------------------------------------------------------------- #
# §9 — requested-vs-available check + confirmation fingerprint
# --------------------------------------------------------------------------- #
def check_period(symbol: str, timeframe: str,
                 requested_start: float, requested_end: float,
                 *, node_id: Any = None, duration_key: str = "",
                 settings_fingerprint: str = "") -> Dict[str, Any]:
    """Compare a requested date range with the persisted, validated history.

    Returns the §9 popup payload: requested vs actual usable range, bar count,
    gaps, source, quality warnings, and ``coverage_status`` ∈
    ``COMPLETE | PARTIAL | NONE``. The confirmation fingerprint binds a later YES
    to EXACTLY this dataset+coverage+node+period+settings (any change invalidates
    it).
    """
    cov = pair_coverage(symbol, timeframe)
    tf_s = tf_seconds(timeframe) or 0
    req_start = float(requested_start)
    req_end = float(requested_end)
    bars_total = 0
    usable_start = cov.get("first_ts")
    usable_end = cov.get("last_ts")
    bars_total = int(cov.get("bars") or 0)

    missing_start = missing_end = False
    if bars_total and tf_s:
        # usable range clipped to the requested window
        if usable_start is not None and float(usable_start) > req_start + tf_s:
            missing_start = True
        if usable_end is not None and float(usable_end) < req_end - tf_s:
            missing_end = True

    internal_gaps = int((cov.get("quality") or {}).get("gaps") or 0)
    # §9 gap policy (documented, deterministic): a gap whose hole is longer than
    # MATERIAL_GAP_HOURS is MISSING HISTORY and makes the range PARTIAL; shorter
    # gaps (week-end / session closures — up to ~74h for metals) are normal
    # stored data and are reported as data-quality warnings, listed for
    # inspection and never filled in.
    gap_rows = cov.get("missing_periods") or []
    material = [g for g in gap_rows
                if (float(g.get("before_ts") or 0) - float(g.get("after_ts") or 0))
                >= MATERIAL_GAP_HOURS * 3600.0]
    closure_gaps = internal_gaps - len(material)
    if bars_total <= 0:
        status = "NONE"
    elif missing_start or missing_end or material:
        status = "PARTIAL"
    else:
        status = "COMPLETE"

    span_days = (req_end - req_start) / 86400.0
    actual_days = None
    if usable_start is not None and usable_end is not None and bars_total:
        actual_days = round((float(usable_end) - float(usable_start)) / 86400.0, 2)

    fingerprint = confirmation_fingerprint(
        symbol=symbol, timeframe=timeframe,
        dataset_fingerprint=str(cov.get("fingerprint") or ""),
        first_ts=cov.get("first_ts"), last_ts=cov.get("last_ts"),
        bars=bars_total, gap_count=internal_gaps,
        requested_start=req_start, requested_end=req_end,
        node_id=node_id, settings_fingerprint=settings_fingerprint,
        duration_key=duration_key)

    return {
        "ok": True,
        "node_id": node_id,
        "symbol": cov["symbol"],
        "timeframe": cov["timeframe"],
        "duration_key": duration_key,
        "duration_label": label_of(duration_key) if duration_key else None,
        "requested": {"start_ts": req_start, "end_ts": req_end,
                      "start": _iso(req_start), "end": _iso(req_end),
                      "span_days": round(span_days, 2)},
        "actual": {"first_ts": usable_start, "last_ts": usable_end,
                   "first": _iso(usable_start), "last": _iso(usable_end),
                   "bars": bars_total, "span_days": actual_days},
        "coverage_status": status,
        "will_be_partial": status != "COMPLETE",
        "missing_start": missing_start,
        "missing_end": missing_end,
        "internal_gaps": internal_gaps,
        "material_gaps": material,
        "session_closure_gaps": max(0, closure_gaps),
        "missing_periods": cov.get("missing_periods") or [],
        "source": cov.get("source"),
        "data_source_label": (f"MT5 / {cov.get('broker') or ''} {cov.get('server') or ''}".strip()
                              if cov.get("source") else "persisted candle store"),
        "fingerprint": cov.get("fingerprint"),
        "warnings": list(cov.get("warnings") or []),
        "confirmation_fingerprint": fingerprint,
        "note": ("complete means the persisted, validated bars cover the requested range "
                 "with no hole longer than 96 hours; week-end/session closures are normal "
                 "stored data (listed as gaps for inspection, never filled in), while a "
                 "hole of 96h or more is missing history and forces PARTIAL"),
    }


def confirmation_fingerprint(*, symbol: str, timeframe: str, dataset_fingerprint: str,
                             first_ts: Any, last_ts: Any, bars: int, gap_count: int,
                             requested_start: float, requested_end: float,
                             node_id: Any, settings_fingerprint: str,
                             duration_key: str = "") -> str:
    """A remembered confirmation is valid ONLY for this exact binding.

    The period side of the binding is the period SELECTION (``duration_key``) for
    the nine presets — their absolute bounds float with the "now" anchor, so
    binding the resolved timestamps would silently invalidate a confirmation
    seconds after it was given. A CUSTOM/explicit window binds its exact bounds.
    """
    key = str(duration_key or "").upper()
    if key and key != "CUSTOM":
        period_bind: Dict[str, Any] = {"duration_key": key}
    else:
        period_bind = {"duration_key": key or "CUSTOM",
                       "requested_start": round(float(requested_start), 3),
                       "requested_end": round(float(requested_end), 3)}
    payload = json.dumps({
        "symbol": str(symbol).upper(), "timeframe": str(timeframe).upper(),
        "dataset_fingerprint": str(dataset_fingerprint or ""),
        "first_ts": first_ts, "last_ts": last_ts, "bars": int(bars),
        "gaps": int(gap_count),
        "period": period_bind,
        "node_id": node_id, "settings": str(settings_fingerprint or ""),
    }, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
