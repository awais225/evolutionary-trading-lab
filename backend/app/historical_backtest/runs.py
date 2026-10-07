"""
V4.6 — MT5 historical backtest runs (research execution, never order placement).

Flow implemented here:

    USER_RESEARCH strategy
      -> configure historical backtest (symbol / timeframe / period / balance /
         risk / cost assumptions)          [validate_request]
      -> execute against stored MT5 market data through the existing engine
                                            [start_run -> _execute -> run_backtest]
      -> persist the run + results          [mt5_historical_runs + RESEARCH parquet]
      -> read it back for the dashboard     [get_run / run_trades / run_equity]

Safety and honesty
------------------
* No order path is imported or called: this module never touches
  `mt5.execution.place_demo_order`, `mt5.execution.close_demo_position`,
  `MarketBridge.send_market_order`, `MarketBridge.close_position`, the V4.3
  live-testing order path, or the order/audit tables (`executions`,
  `paper_trades`, `mt5_demo_trades`, `live_test_trades`, `mt5_backtests`).
* A run is stored exactly once per request (duplicate protection by request
  key) and a repeat run of the same strategy gets its OWN run id; results are
  never overwritten.
* Only engine-produced numbers are reported. Anything the engine does not
  produce is either derived explicitly from the persisted trade list (and
  labelled as such) or reported as `None` with an unavailable reason.
* Real MT5 data is never faked: if no stored MT5 dataset matches the request
  the run is refused, unless the caller explicitly asks for SIMULATOR data, in
  which case every field of the run says SIMULATOR.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from .. import paths as P
from ..config import get_config
from ..db.database import get_db
from ..jsonutil import jd
from ..versions import manifest as engine_manifest

log = logging.getLogger("historical_backtest")

# --------------------------------------------------------------------------- #
# constants
# --------------------------------------------------------------------------- #
RUN_TABLE = "mt5_historical_runs"
LABEL = "HISTORICAL MT5 BACKTEST"
STAGE = "mt5_hist"
STATUSES = ("QUEUED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED")
ACTIVE_STATUSES = ("QUEUED", "RUNNING")

# engine safety floor: Backtester.run() refuses a window smaller than this
MIN_BARS = 300
MAX_QUEUE = 5                      # queued runs accepted on top of the active one
STALE_RUNNING_SECONDS = 900        # a RUNNING row older than this is an orphan
DEFAULT_TRADES_LIMIT = 50
MAX_TRADES_LIMIT = 500
DEFAULT_EQUITY_POINTS = 500
MAX_EQUITY_POINTS = 2000
TRADE_CACHE_SIZE = 4
MT5_SCOPE = "MT5"
SIM_SCOPE = "SIMULATOR"

# cost/execution assumptions that exist in the engine's own config today
COST_KEYS = ("spread_mult", "slippage_mult", "commission_mult")

_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mt5-hist")
#: how far ahead a scheduled deep backtest may be queued
MAX_SCHEDULE_AHEAD_S = 24 * 3600
_SUBMIT_LOCK = threading.RLock()
_CANCELS: set = set()
_TRADE_CACHE: "Dict[str, Tuple[float, pd.DataFrame]]" = {}
_TRADE_CACHE_ORDER: List[str] = []


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _num(value: Any) -> Optional[float]:
    """JSON-safe float: NaN/inf/None -> None (never a fake 0)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _round(value: Any, nd: int = 4) -> Optional[float]:
    f = _num(value)
    return None if f is None else round(f, nd)


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _iso(ts: Any) -> Optional[str]:
    f = _num(ts)
    if f is None:
        return None
    return dt.datetime.fromtimestamp(f, dt.timezone.utc).isoformat()


def _jload(raw: Any, default: Any = None) -> Any:
    if raw is None:
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return default


def _parse_ts(value: Any, *, end_of_day: bool = False) -> Optional[float]:
    """Accept 'YYYY-MM-DD' or a full ISO timestamp (UTC when no zone given)."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return _num(value)
    text = str(value).strip()
    try:
        if len(text) == 10:
            d = dt.datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
            if end_of_day:
                d = d + dt.timedelta(days=1) - dt.timedelta(seconds=1)
            return d.timestamp()
        d = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d.timestamp()
    except Exception:
        return None


def _sha256_file(path: str) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# dataset catalogue (reuses the data engine; never downloads or copies data)
# --------------------------------------------------------------------------- #
# V4.7: the eligibility probe reads the stored parquet of every registered
# dataset, so calling it on each capabilities lookup cost ~0.35s. Results are
# memoised for a short TTL and invalidated automatically when the dataset file
# or its feature artifact changes on disk.
ELIGIBILITY_TTL_S = 20.0
_ELIG_CACHE: "Dict[tuple, Tuple[float, Tuple[bool, str]]]" = {}
_ELIG_LOCK = threading.RLock()


def _eligibility_key(dataset_id: str, db: Any = None) -> tuple:
    """(dataset id, file identity, feature identity) — the cache validity key."""
    try:
        d = db or get_db()
        row = d.one("SELECT id, path, fingerprint, start_ts, end_ts, bars FROM datasets WHERE dataset_id=?",
                    (dataset_id,))
    except Exception:
        row = None
    parts: List[Any] = [dataset_id, getattr(db, "path", None) if db is not None else None]
    if row:
        parts.extend([row.get("path"), row.get("fingerprint"), row.get("bars"),
                      row.get("start_ts"), row.get("end_ts")])
        p = row.get("path")
        if p:
            try:
                st = os.stat(p)
                parts.extend([st.st_size, st.st_mtime_ns])
            except OSError:
                parts.append("missing")
    try:
        feat = P.FEATURES_DIR
        if feat.exists():
            marker = max((f.stat().st_mtime_ns for f in feat.glob(f"{dataset_id}*")), default=0)
            parts.append(marker)
    except Exception:
        pass
    return tuple(parts)


def _eligible(dataset_id: str, db: Any = None, refresh: bool = False) -> Tuple[bool, str]:
    from ..data.engine import get_data_engine
    key = _eligibility_key(dataset_id, db)
    now = time.time()
    if not refresh:
        with _ELIG_LOCK:
            hit = _ELIG_CACHE.get(key)
            if hit and hit[0] > now:
                return hit[1]
    try:
        value = get_data_engine().is_dataset_eligible_for_research(dataset_id, require_features=True)
    except Exception as e:                                  # pragma: no cover - defensive
        value = (False, f"dataset eligibility check failed: {e}")
    with _ELIG_LOCK:
        _ELIG_CACHE[key] = (now + ELIGIBILITY_TTL_S, value)
        if len(_ELIG_CACHE) > 256:                     # bounded: never grows forever
            for k in [k for k, (exp, _v) in _ELIG_CACHE.items() if exp <= now]:
                _ELIG_CACHE.pop(k, None)
    return value


def clear_catalogue_cache() -> None:
    """Drop memoised eligibility/catalogue data (used after a data change)."""
    with _ELIG_LOCK:
        _ELIG_CACHE.clear()


_CAT_CACHE: "Dict[str, Tuple[float, Dict[str, Any]]]" = {}
CATALOGUE_TTL_S = 20.0


def _catalogue_signature(db: Any) -> tuple:
    """Cheap fingerprint of the dataset registration state (cache validity).

    Includes the database identity (path + inode), so a memoised catalogue can
    never be served to a different database - important because a test or a
    second deployment may register completely different datasets.
    """
    identity = (getattr(db, "path", None), getattr(db, "_identity", None))
    try:
        row = db.one("""SELECT COUNT(*) n, COALESCE(MAX(id),0) max_id,
                               COALESCE(SUM(bars),0) bars FROM datasets""")
    except Exception:
        return ("unknown", identity)
    try:
        st = (P.MANIFESTS_DIR / "datasets.json").stat()
        man = (st.st_size, st.st_mtime_ns)
    except OSError:
        man = None
    return (identity, row.get("n"), row.get("max_id"), row.get("bars"), man)


def _master_row(db: Any, symbol: str, timeframe: str, source: str) -> Optional[Dict[str, Any]]:
    return db.one(
        """SELECT * FROM master_datasets
           WHERE symbol=? AND timeframe=? AND source=? ORDER BY version DESC, id DESC LIMIT 1""",
        (symbol, timeframe, source))


def _manifest_datasets() -> Dict[str, Dict[str, Any]]:
    """DATA/manifests/datasets.json — the app's own record of stored market data."""
    path = P.MANIFESTS_DIR / "datasets.json"
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return (json.load(fh) or {}).get("datasets") or {}
    except Exception:
        return {}


def _entry_from(dataset_id: str, row: Optional[Dict[str, Any]], master: Optional[Dict[str, Any]],
                manifest: Optional[Dict[str, Any]], path: Optional[str],
                eligible: bool, reason: str) -> Dict[str, Any]:
    source = (row or {}).get("source") or (manifest or {}).get("source") or (master or {}).get("source")
    first_ts = _num((row or {}).get("start_ts"))
    last_ts = _num((row or {}).get("end_ts"))
    if first_ts is None:
        first_ts = _num((master or {}).get("first_ts"))
    if last_ts is None:
        last_ts = _num((master or {}).get("last_ts"))
    if first_ts is None:
        first_ts = _num((manifest or {}).get("first_ts"))
    if last_ts is None:
        last_ts = _num((manifest or {}).get("last_ts"))
    bars = _as_int((row or {}).get("bars")) or _as_int((master or {}).get("rows")) \
        or _as_int((manifest or {}).get("rows"))
    return {
        "dataset_id": dataset_id,
        "symbol": (row or {}).get("symbol") or (manifest or {}).get("symbol"),
        "timeframe": (row or {}).get("timeframe") or (manifest or {}).get("timeframe"),
        "source": source or "UNKNOWN",
        "broker": (master or {}).get("broker") or "",
        "server": (master or {}).get("server") or "",
        "profile": (master or {}).get("profile") or "",
        "bars": bars,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "start": _iso(first_ts),
        "end": _iso(last_ts),
        "dataset_version": _as_int((row or {}).get("dataset_version")) or _as_int((master or {}).get("version")),
        "fingerprint": (row or {}).get("fingerprint") or (master or {}).get("fingerprint") or "",
        "path": str(path) if path else None,
        "eligible": bool(eligible),
        "eligibility_reason": reason,
    }


def catalogue(db: Any = None, refresh: bool = False) -> Dict[str, Any]:
    """Every dataset the backtest engine can actually load right now, by scope.

    A dataset is offered only when its physical artifact exists and the app's own
    eligibility contract accepts it (same contract the research pipeline uses).
    Nothing is downloaded, regenerated or copied.

    V4.7: the assembled catalogue is memoised for a short TTL keyed on the
    dataset registration state, because resolving it reads the stored parquet of
    every registered dataset (77 of them here). ``refresh=True`` (or any change
    to the datasets table / dataset manifest) bypasses the cache.
    """
    from ..data.engine import get_data_engine
    d = db or get_db()
    de = get_data_engine()
    signature = _catalogue_signature(d)
    now = time.time()
    if refresh:
        # an explicit refresh re-resolves everything: the memoised eligibility
        # probes are dropped too, so a data change is picked up immediately
        clear_catalogue_cache()
    if not refresh:
        with _ELIG_LOCK:
            hit = _CAT_CACHE.get("catalogue")
            if hit and hit[0] > now and hit[1].get("_signature") == signature:
                payload = dict(hit[1])
                payload["cached"] = True
                return payload
    rows = d.q("SELECT * FROM datasets WHERE bars > 0 ORDER BY symbol, timeframe, created_at DESC")
    manifest = _manifest_datasets()
    seen: set = set()
    out: List[Dict[str, Any]] = []

    def consider(dataset_id: str, row: Optional[Dict[str, Any]]) -> None:
        if not dataset_id or dataset_id in seen:
            return
        seen.add(dataset_id)
        path = de.find_dataset_artifact_path(dataset_id)
        ok, reason = _eligible(dataset_id) if path else (False, "physical dataset artifact not found on disk")
        symbol = (row or {}).get("symbol") or ""
        timeframe = (row or {}).get("timeframe") or ""
        master = _master_row(d, symbol, timeframe, (row or {}).get("source") or "MT5") if symbol else None
        man = manifest.get(f"{symbol}_{timeframe}") if symbol else None
        entry = _entry_from(dataset_id, row, master, man, path, ok, reason)
        if not entry["symbol"]:
            return
        out.append(entry)

    for r in rows:
        consider(r["id"], r)
    # the app's own master views (MT5/XAUUSD/<tf>.parquet) and the manifest list
    for key, man in manifest.items():
        sym, _, tf = key.partition("_")
        if not sym or not tf:
            continue
        master = _master_row(d, sym, tf, man.get("source") or "MT5")
        path = de.find_dataset_artifact_path(key)
        ok, reason = _eligible(key) if path else (False, "physical dataset artifact not found on disk")
        entry = _entry_from(key, None, master, man, path, ok, reason)
        if entry["symbol"] and key not in seen:
            seen.add(key)
            out.append(entry)
    payload = {"datasets": out, "manifest": manifest, "_signature": signature, "cached": False}
    with _ELIG_LOCK:
        _CAT_CACHE["catalogue"] = (now + CATALOGUE_TTL_S, payload)
    return {"datasets": out, "manifest": manifest, "cached": False}


def effective_datasets(cat: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One resolvable dataset per (source, symbol, timeframe) — exactly what a run
    would use. A dataset that fails the eligibility contract is reported in the
    capabilities' `rejected` list instead of being offered twice."""
    best: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for ds in cat["datasets"]:
        if not ds["eligible"] or not ds["path"]:
            continue
        key = ((ds["source"] or "UNKNOWN").upper(), ds["symbol"], ds["timeframe"])
        cur = best.get(key)
        if cur is None or (ds["dataset_version"] or 0) > (cur["dataset_version"] or 0):
            best[key] = ds
    out = list(best.values())
    out.sort(key=lambda d: (0 if (d["source"] or "").upper() == MT5_SCOPE else 1, d["symbol"], d["timeframe"]))
    return out


def _datasets_by_symbol(cat: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for ds in cat["datasets"]:
        grouped.setdefault(ds["symbol"], []).append(ds)
    for sym, items in grouped.items():
        items.sort(key=lambda x: (x["source"] != MT5_SCOPE, x["timeframe"] or ""))
    return grouped


def capabilities(db: Any = None, refresh: bool = False) -> Dict[str, Any]:
    """What can be backtested right now: real MT5 datasets, cost defaults, limits."""
    from ..mt5.factory import bridge_status
    d = db or get_db()
    cat = catalogue(d, refresh=refresh)
    effective = effective_datasets(cat)
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for ds in effective:
        grouped.setdefault(ds["symbol"], []).append(ds)
    bt = get_config().backtest
    try:
        bridge = bridge_status()
    except Exception as e:                                  # pragma: no cover - defensive
        bridge = {"active_bridge": "unavailable", "error": str(e)}
    supported = []
    for sym, items in sorted(grouped.items()):
        for ds in items:
            supported.append({
                "symbol": sym,
                "timeframe": ds["timeframe"],
                "source": ds["source"],
                "dataset_id": ds["dataset_id"],
                "bars": ds["bars"],
                "start": ds["start"],
                "end": ds["end"],
                "broker": ds["broker"],
                "server": ds["server"],
                "fingerprint": ds["fingerprint"],
                "eligible": ds["eligible"],
                "eligibility_reason": ds["eligibility_reason"],
                "min_bars": MIN_BARS,
                "max_run_bars": ds["bars"],
            })
    return {
        "label": LABEL,
        "stage": STAGE,
        "cached": bool(cat.get("cached")),
        "scope": {
            "default": MT5_SCOPE,
            "options": [MT5_SCOPE, SIM_SCOPE],
            "note": ("A run is executed over stored historical bars of the dataset it names. "
                     "MT5 means the stored dataset itself is MT5 data; SIMULATOR means the stored "
                     "dataset is lab-simulated data and the run is labelled as such — a simulator "
                     "run is never presented as an MT5 result."),
        },
        "datasets": supported,
        "datasets_by_symbol": {s: [x["timeframe"] for x in items] for s, items in grouped.items()},
        "rejected_datasets": [
            {"symbol": x["symbol"], "timeframe": x["timeframe"], "source": x["source"],
             "dataset_id": x["dataset_id"], "reason": x["eligibility_reason"]}
            for x in cat["datasets"]
            if not (x["eligible"] and x["path"])
        ][:20],
        "bridge": {
            "active_bridge": bridge.get("active_bridge"),
            "source": bridge.get("source"),
            "is_simulated": bridge.get("is_simulated"),
            "mt5_package_installed": bridge.get("mt5_package_installed"),
            "terminal_build": bridge.get("terminal_build"),
            "terminal_company": bridge.get("terminal_company"),
            "note": ("Backtest execution uses the stored historical bars and the local engine; it never "
                     "needs, and never opens, a broker/terminal order session. A real MT5 terminal is "
                     "required only to *fetch* new bars, not to backtest stored ones."),
        },
        "defaults": {
            "initial_balance": bt.initial_balance,
            "risk_per_trade": bt.risk_per_trade,
            "commission_per_lot": bt.commission_per_lot,
            "swap_per_lot_per_day": bt.swap_per_lot_per_day,
            "contract_size": bt.contract_size,
            "point_value": bt.point_value,
            "slippage_model": bt.slippage_model,
            "slippage_mean_points": bt.slippage_mean_points,
            "execution_delay_ms": bt.execution_delay_ms,
            "max_concurrent_positions": bt.max_concurrent_positions,
        },
        "cost_multipliers": {
            "keys": list(COST_KEYS),
            "min": 0.1,
            "max": 10.0,
            "note": "Stress multipliers on the engine's own spread / slippage / commission model.",
        },
        "limits": {
            "min_bars": MIN_BARS,
            "max_queue": MAX_QUEUE,
            "max_active": 1,
            "max_trades_limit": MAX_TRADES_LIMIT,
            "max_equity_points": MAX_EQUITY_POINTS,
            "one_at_a_time": True,
        },
        "modes": {
            "historical_backtest": True,
            "places_orders": False,
            "order_execution_path": "NOT USED — this endpoint family never places, modifies or "
                                    "closes any order; see /api/mt5-demo/* and /api/live-testing/* "
                                    "for the separately activated execution layers.",
        },
    }


# --------------------------------------------------------------------------- #
# request validation
# --------------------------------------------------------------------------- #
def _resolve_dataset(db: Any, symbol: str, timeframe: str, scope: str,
                     cat: Optional[Dict[str, Any]] = None) -> Tuple[Optional[Dict[str, Any]], str]:
    """First eligible dataset for symbol/timeframe in the requested scope."""
    cat = cat or catalogue(db)
    wanted = MT5_SCOPE if scope == MT5_SCOPE else SIM_SCOPE
    for ds in effective_datasets(cat):
        if ds["symbol"] != symbol or ds["timeframe"] != timeframe:
            continue
        if (ds["source"] or "").upper() != wanted:
            continue
        return ds, ""
    why = [f"{d['dataset_id']} ({d['source']}): {d['eligibility_reason']}"
           for d in cat["datasets"] if d["symbol"] == symbol and d["timeframe"] == timeframe]
    reason = (f"no eligible {wanted} dataset for {symbol} {timeframe}"
              + (f" — checked: {'; '.join(why)}" if why else " — no stored dataset for that symbol/timeframe"))
    return None, reason


def _window_for(db: Any, dataset: Dict[str, Any], start_ts: float, end_ts: float) -> Tuple[Optional[Tuple[int, int]], Optional[int], Optional[int], str]:
    """Row slice [lo, hi) of the dataset covering the requested period."""
    from ..data.engine import get_data_engine
    try:
        df = get_data_engine().get_frame(dataset["dataset_id"])
    except Exception as e:
        return None, None, None, f"dataset could not be loaded: {e}"
    ts = df["ts"].to_numpy()
    lo = int((ts < start_ts).sum())
    hi = int((ts <= end_ts).sum())
    bars = max(0, hi - lo)
    if bars <= 0:
        return None, 0, len(ts), (
            f"the requested period contains no bars; dataset {dataset['dataset_id']} covers "
            f"{_iso(dataset['first_ts'])} .. {_iso(dataset['last_ts'])}")
    if bars < MIN_BARS:
        return None, bars, len(ts), (
            f"period too short: {bars} bars in the requested range, the engine needs at least "
            f"{MIN_BARS} (widen the period)")
    return (lo, hi), bars, len(ts), ""


def validate_request(payload: Dict[str, Any], db: Any = None) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, str]]]:
    """Validate a run request. Returns (config, errors); config is None on error."""
    d = db or get_db()
    payload = payload or {}
    errors: List[Dict[str, str]] = []
    sid = _as_int(payload.get("strategy_id"))
    if sid is None:
        return None, [{"field": "strategy_id", "error": "strategy_id is required (integer node id)"}]

    strat = d.one("SELECT * FROM strategies WHERE id=?", (sid,))
    if not strat:
        return None, [{"field": "strategy_id", "error": f"strategy #{sid} does not exist"}]

    data_source = (strat.get("data_source") or "USER_RESEARCH").upper()
    diagnostic_legacy = bool(payload.get("diagnostic_legacy"))
    if data_source == "LEGACY_TEST" and not diagnostic_legacy:
        return None, [{
            "field": "strategy_id",
            "error": (f"strategy #{sid} is a LEGACY_TEST record and is not part of the research "
                      "population; it cannot be backtested. A legacy node is only reachable by an "
                      "explicit diagnostic request (diagnostic_legacy=true), and such a run is "
                      "marked diagnostic and excluded from research statistics."),
        }]

    genome = _jload(strat.get("genome"), {})
    if not isinstance(genome, dict) or not (genome.get("entry_long") or genome.get("entry_short")):
        return None, [{"field": "strategy_id",
                       "error": f"strategy #{sid} has no executable entry rule in its stored genome"}]

    scope = str(payload.get("data_scope") or MT5_SCOPE).upper()
    if scope not in (MT5_SCOPE, SIM_SCOPE):
        errors.append({"field": "data_scope", "error": "data_scope must be MT5 or SIMULATOR"})

    symbol = str(payload.get("symbol") or strat.get("symbol") or genome.get("symbol") or "").upper()
    timeframe = str(payload.get("timeframe") or strat.get("timeframe") or genome.get("timeframe") or "").upper()
    if not symbol:
        errors.append({"field": "symbol", "error": "symbol is required"})
    if not timeframe:
        errors.append({"field": "timeframe", "error": "timeframe is required"})
    if errors:
        return None, errors

    cat = catalogue(d)
    dataset, why = _resolve_dataset(d, symbol, timeframe, scope, cat)
    if dataset is None:
        return None, [{"field": "symbol", "error": why,
                       "available": sorted({f"{x['symbol']} {x['timeframe']} ({x['source']})"
                                            for x in cat["datasets"] if x["eligible"]}) or
                                    ["none — no dataset currently passes the eligibility contract"]}]

    start_ts = _parse_ts(payload.get("start_date") or payload.get("start"))
    end_ts = _parse_ts(payload.get("end_date") or payload.get("end"), end_of_day=True)
    if start_ts is None:
        errors.append({"field": "start_date", "error": "start_date is required (YYYY-MM-DD)"})
    if end_ts is None:
        errors.append({"field": "end_date", "error": "end_date is required (YYYY-MM-DD)"})
    if errors:
        return None, errors
    if start_ts >= end_ts:
        return None, [{"field": "end_date", "error": "end_date must be after start_date"}]
    ds_first, ds_last = _num(dataset["first_ts"]), _num(dataset["last_ts"])
    requested = {"start_ts": start_ts, "end_ts": end_ts}
    adjusted = False
    # A calendar date is coarser than the stored bars: clip to the stored range
    # and report the adjustment explicitly instead of silently testing another
    # period (or refusing a reasonable request).
    if ds_first is not None and start_ts < ds_first:
        start_ts, adjusted = ds_first, True
    if ds_last is not None and end_ts > ds_last:
        end_ts, adjusted = ds_last, True
    if start_ts >= end_ts:
        return None, [{"field": "start_date",
                       "error": (f"the requested period does not overlap the stored data range "
                                 f"({_iso(ds_first)} .. {_iso(ds_last)}) for {symbol} {timeframe}")}]

    window, bars, dataset_bars, why = _window_for(d, dataset, start_ts, end_ts)
    if window is None:
        return None, [{"field": "start_date", "error": why}]

    # V5 §9 — a run may be scheduled instead of fired immediately. The queue
    # accepts it now and the single executor starts it at (or after) that time.
    scheduled_at = _parse_ts(payload.get("schedule_at") or payload.get("scheduled_at"))
    if scheduled_at is not None:
        horizon = time.time() + MAX_SCHEDULE_AHEAD_S
        if scheduled_at < time.time() - 60:
            errors.append({"field": "schedule_at",
                           "error": "the scheduled time is in the past; schedule the run for now or later"})
        elif scheduled_at > horizon:
            errors.append({"field": "schedule_at",
                           "error": (f"the scheduled time is more than "
                                     f"{int(MAX_SCHEDULE_AHEAD_S / 3600)} h ahead; the queue does not "
                                     "hold jobs that far out")})

    balance = _as_float(payload.get("initial_balance", get_config().backtest.initial_balance))
    if balance is None or balance <= 0:
        errors.append({"field": "initial_balance", "error": "initial_balance must be a positive number"})
    risk = _as_float(payload.get("risk_per_trade", get_config().backtest.risk_per_trade))
    if risk is None or not (0 < risk <= 0.5):
        errors.append({"field": "risk_per_trade", "error": "risk_per_trade must be between 0 and 0.5 (fraction of equity)"})
    mults: Dict[str, float] = {}
    for k in COST_KEYS:
        v = _as_float(payload.get(k, 1.0))
        if v is None or not (0.1 <= v <= 10.0):
            errors.append({"field": k, "error": f"{k} must be between 0.1 and 10.0"})
        else:
            mults[k] = v
    max_trades = _as_int(payload.get("max_trades", 3000))
    if max_trades is None or not (1 <= max_trades <= 20000):
        errors.append({"field": "max_trades", "error": "max_trades must be between 1 and 20000"})
    if errors:
        return None, errors

    # ---- V5.1a §21: the node's own schedule travels with the run ---------------
    # The deep backtest applies exactly the schedule the operator saved for this
    # node (same evaluator as live), so "Mon + London + H1" means the same thing
    # in the backtest as it does on the order path. A timeframe the node is not
    # allowed to trade is rejected here rather than silently tested.
    from ..live_testing.schedule import describe as _describe_schedule
    from ..live_testing.schedule import normalize_config as _normalize_schedule
    try:
        _lt_cfg = d.get_live_test_config(sid) or {}
    except Exception:
        _lt_cfg = {}
    _schedule = _normalize_schedule(_lt_cfg)
    _sched_timeframes = _schedule.get("timeframes") or []
    if _sched_timeframes and str(timeframe).upper() not in {str(t).upper() for t in _sched_timeframes}:
        return None, [{"field": "timeframe",
                       "error": (f"node {sid} is scheduled for {'/'.join(_sched_timeframes)} only; "
                                 f"running {timeframe} would ignore its saved schedule. "
                                 f"Change the schedule or pick an allowed timeframe.")}]
    _conditions = _schedule.get("conditions") or {}
    _sched_configured = any([_schedule.get("days"), _schedule.get("sessions"),
                             _schedule.get("regimes"), _schedule.get("timeframes"),
                             _conditions, _schedule.get("windows")])

    cfg = {
        "strategy_id": sid,
        "strategy_status": strat.get("status"),
        "strategy_data_source": data_source,
        "research_node_num": strat.get("research_node_num"),
        "run_id": strat.get("run_id"),
        "generation": strat.get("generation"),
        "symbol": symbol,
        "timeframe": timeframe,
        "data_scope": scope,
        "dataset_id": dataset["dataset_id"],
        "dataset_path": dataset["path"],
        "dataset_source": dataset["source"],
        "dataset_broker": dataset["broker"],
        "dataset_server": dataset["server"],
        "dataset_profile": dataset["profile"],
        "dataset_fingerprint": dataset["fingerprint"],
        "dataset_version": dataset["dataset_version"],
        "dataset_bars": dataset_bars,
        "dataset_first_ts": dataset["first_ts"],
        "dataset_last_ts": dataset["last_ts"],
        "start_ts": start_ts,
        "end_ts": end_ts,
        "start_date": str(payload.get("start_date") or payload.get("start")),
        "end_date": str(payload.get("end_date") or payload.get("end")),
        "scheduled_at": scheduled_at,
        # V5.1a §21/§31 — the node schedule the run was executed under, recorded
        # with the run so the result can always be traced back to it.
        "schedule": _schedule if _sched_configured else None,
        "schedule_configured": bool(_sched_configured),
        "schedule_description": _describe_schedule(_lt_cfg) if _sched_configured else
                                "no schedule configured (node trades its own genome definition)",
        "conditions": _conditions,
        "period_adjusted": bool(adjusted),
        "requested_start_ts": requested["start_ts"],
        "requested_end_ts": requested["end_ts"],
        "window": [window[0], window[1]],
        "bars": bars,
        "initial_balance": balance,
        "risk_per_trade": risk,
        "max_trades": max_trades,
        "cost_multipliers": mults,
        "genome_hash": _genome_hash(genome),
        "diagnostic_legacy": bool(diagnostic_legacy),
    }
    cfg["request_key"] = _request_key(cfg)
    return cfg, []


def _genome_hash(genome: Any) -> str:
    from ..genome.schema import genome_hash
    try:
        return genome_hash(genome)
    except Exception:                                       # pragma: no cover - defensive
        canon = json.dumps(genome, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:32]


def _request_key(cfg: Dict[str, Any]) -> str:
    payload = {
        "strategy_id": cfg["strategy_id"],
        "dataset_id": cfg["dataset_id"],
        "genome_hash": cfg["genome_hash"],
        "start_ts": cfg["start_ts"],
        "end_ts": cfg["end_ts"],
        "initial_balance": cfg["initial_balance"],
        "risk_per_trade": cfg["risk_per_trade"],
        "max_trades": cfg["max_trades"],
        "cost": cfg["cost_multipliers"],
    }
    canon = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:32]


# --------------------------------------------------------------------------- #
# run storage
# --------------------------------------------------------------------------- #
def _new_run_id() -> str:
    return "HRUN-%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:6].upper())


def _reconcile_stale(db: Any) -> None:
    """Runs left RUNNING by a stopped backend must not stay RUNNING forever."""
    cutoff = time.time() - STALE_RUNNING_SECONDS
    try:
        db.x("""UPDATE mt5_historical_runs
                   SET status='FAILED', finished_at=?,
                       error=COALESCE(error,'') || 'backend stopped while this run was executing (orphaned RUNNING record)'
                 WHERE status='RUNNING' AND COALESCE(started_at, created_at) < ?""",
             (time.time(), cutoff))
    except Exception:                                       # pragma: no cover - defensive
        log.debug("stale-run reconciliation skipped", exc_info=True)


def _insert_run(db: Any, cfg: Dict[str, Any]) -> None:
    identity = {
        "node_id": cfg["strategy_id"],
        "research_node_num": cfg["research_node_num"],
        "strategy_run_id": cfg["run_id"],
        "generation": cfg["generation"],
        "strategy_status": cfg["strategy_status"],
        "strategy_data_source": cfg["strategy_data_source"],
    }
    db.x(f"""INSERT INTO {RUN_TABLE} (
                run_id, strategy_id, label, status, data_scope, data_source, dataset_id, dataset_path,
                dataset_fingerprint, symbol, timeframe, start_ts, end_ts, start_date, end_date, bars,
                initial_balance, risk_per_trade, config, request_key, genome_hash, strategy_identity,
                diagnostic_legacy, research_eligible, created_at)
             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
         (_new_run_id(), cfg["strategy_id"], LABEL, "QUEUED", cfg["data_scope"], cfg["dataset_source"],
          cfg["dataset_id"], cfg["dataset_path"], cfg["dataset_fingerprint"], cfg["symbol"],
          cfg["timeframe"], cfg["start_ts"], cfg["end_ts"], cfg["start_date"], cfg["end_date"],
          cfg["bars"], cfg["initial_balance"], cfg["risk_per_trade"], jd(cfg), cfg["request_key"],
          cfg["genome_hash"], jd(identity), 1 if cfg["diagnostic_legacy"] else 0,
          0 if cfg["diagnostic_legacy"] else 1, time.time()))


def _row(db: Any, run_id: str) -> Optional[Dict[str, Any]]:
    return db.one(f"SELECT * FROM {RUN_TABLE} WHERE run_id=?", (run_id,))


def _update(db: Any, run_id: str, **cols: Any) -> None:
    if not cols:
        return
    keys = list(cols.keys())
    sets = ", ".join(f"{k}=?" for k in keys)
    db.x(f"UPDATE {RUN_TABLE} SET {sets} WHERE run_id=?", tuple(cols[k] for k in keys) + (run_id,))


#: terminal run states may never be overwritten (V4.7 run-state safety)
TERMINAL_STATUSES = ("COMPLETED", "FAILED", "CANCELLED")


def _update_from(db: Any, run_id: str, from_statuses: Tuple[str, ...], **cols: Any) -> int:
    """Update a run only while it is in one of ``from_statuses``.

    Returns the number of rows changed. This is what makes a run's terminal state
    final: a late worker update (or a cancel racing with completion) can no
    longer flip a COMPLETED run to FAILED, or resurrect a CANCELLED one, and a
    failed run can never be presented as COMPLETED.
    """
    if not cols:
        return 0
    keys = list(cols.keys())
    sets = ", ".join(f"{k}=?" for k in keys)
    marks = ", ".join("?" for _ in from_statuses)
    args = tuple(cols[k] for k in keys) + (run_id,) + tuple(from_statuses)
    try:
        return db.x(f"UPDATE {RUN_TABLE} SET {sets} WHERE run_id=? AND status IN ({marks})", args)
    except Exception:                                       # pragma: no cover - defensive
        log.debug("guarded run update failed", exc_info=True)
        return 0


# --------------------------------------------------------------------------- #
# artifacts (same parquet convention as the research exporter, run-scoped)
# --------------------------------------------------------------------------- #
def run_dir(run_id: str) -> str:
    return str(P.RESEARCH_DIR / "mt5_historical" / "runs" / run_id)


def _write_artifacts(run_id: str, cfg: Dict[str, Any], metrics: Dict[str, Any],
                     trades: List[Dict[str, Any]], equity: List[List[float]],
                     provenance: Dict[str, Any]) -> Dict[str, Any]:
    base = run_dir(run_id)
    os.makedirs(base, exist_ok=True)
    artifacts: Dict[str, Any] = {"dir": base}
    try:
        if trades:
            tmp = os.path.join(base, "trades.parquet.tmp")
            pd.DataFrame(trades).to_parquet(tmp, index=False)
            os.replace(tmp, os.path.join(base, "trades.parquet"))
            artifacts["trades"] = os.path.join(base, "trades.parquet")
        if equity:
            tmp = os.path.join(base, "equity.parquet.tmp")
            pd.DataFrame(equity, columns=["ts", "equity"]).to_parquet(tmp, index=False)
            os.replace(tmp, os.path.join(base, "equity.parquet"))
            artifacts["equity"] = os.path.join(base, "equity.parquet")
        for name, blob in (("metrics.json", metrics), ("config.json", cfg), ("provenance.json", provenance)):
            with open(os.path.join(base, name), "w", encoding="utf-8") as fh:
                fh.write(json.dumps(blob, indent=2, default=str, allow_nan=False))
            artifacts[name.split(".")[0]] = os.path.join(base, name)
    except Exception as e:
        log.warning("artifact export failed for %s: %s", run_id, e)
        artifacts["error"] = f"artifact export failed: {e}"
    return artifacts


def _trades_frame(run_id: str) -> Optional[pd.DataFrame]:
    cached = _TRADE_CACHE.get(run_id)
    if cached is not None:
        return cached[1]
    path = os.path.join(run_dir(run_id), "trades.parquet")
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_parquet(path)
    except Exception:                                       # pragma: no cover - defensive
        return None
    _TRADE_CACHE[run_id] = (time.time(), df)
    _TRADE_CACHE_ORDER.append(run_id)
    while len(_TRADE_CACHE_ORDER) > TRADE_CACHE_SIZE:
        _TRADE_CACHE.pop(_TRADE_CACHE_ORDER.pop(0), None)
    return df


# --------------------------------------------------------------------------- #
# metrics: engine values only, everything else explicit
# --------------------------------------------------------------------------- #
def _trade_derived(df: Optional[pd.DataFrame]) -> Dict[str, Any]:
    """Values the engine does not emit, computed from the persisted trade list."""
    if df is None or df.empty:
        return {}
    pnl = pd.to_numeric(df["pnl"], errors="coerce").dropna()
    if pnl.empty:
        return {}
    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]
    cw = cl = best_w = best_l = 0
    for v in pnl.tolist():
        if v > 0:
            cw += 1
            cl = 0
        else:
            cl += 1
            cw = 0
        best_w = max(best_w, cw)
        best_l = max(best_l, cl)
    out = {
        "largest_win": _round(wins.max() if not wins.empty else None, 2),
        "largest_loss": _round(losses.min() if not losses.empty else None, 2),
        "avg_win": _round(wins.mean() if not wins.empty else None, 3),
        "avg_loss": _round(losses.mean() if not losses.empty else None, 3),
        "consecutive_wins": int(best_w),
        "consecutive_losses": int(best_l),
        "gross_profit_derived": _round(wins.sum(), 2),
        "gross_loss_derived": _round(abs(losses.sum()), 2),
        "source": "derived from the persisted trades.parquet of this run",
    }
    return out


def _unavailable(metrics: Dict[str, Any], derived: Dict[str, Any]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    checks = (
        ("risk_amount", "research records store no account balance/currency per trade; the engine "
                        "returns money P&L, not the risk amount of a live account"),
        ("position_size_from_risk", "lot sizes come from the engine's own sizing; a risk-normalised "
                                    "lot size is not part of the stored research parameters"),
        ("recovery_factor", "needs the absolute money drawdown, which the engine reports only as a "
                            "percentage of equity"),
        ("drawdown_abs", "the engine reports max_drawdown_pct; absolute money drawdown is not stored"),
        ("equity_curve_all_points", "the API returns a bounded equity series; the complete curve is "
                                    "in the run's equity.parquet artifact"),
    )
    for metric, reason in checks:
        if metric not in metrics:
            out.append({"metric": metric, "reason": reason, "layer": LABEL})
    if "largest_win" not in metrics:
        out.append({"metric": "largest_win",
                    "reason": ("not emitted by the engine"
                               + ("" if derived else "; this run produced no trades"))})
    return out


# --------------------------------------------------------------------------- #
# execution
# --------------------------------------------------------------------------- #
def _provenance(cfg: Dict[str, Any], bridge: Dict[str, Any]) -> Dict[str, Any]:
    path = cfg.get("dataset_path")
    file_info: Dict[str, Any] = {"path": path}
    if path and os.path.exists(path):
        try:
            st = os.stat(path)
            file_info.update({"size_bytes": st.st_size, "mtime": st.st_mtime,
                              "sha256": _sha256_file(path)})
        except Exception:                                   # pragma: no cover - defensive
            pass
    return {
        "label": LABEL,
        "strategy_identity": {
            "node_id": cfg["strategy_id"], "research_node_num": cfg.get("research_node_num"),
            "generation": cfg.get("generation"), "strategy_status_at_run": cfg.get("strategy_status"),
            "strategy_data_source": cfg.get("strategy_data_source"), "run_id": cfg.get("run_id"),
            "genome_hash": cfg.get("genome_hash"),
            "diagnostic_legacy": bool(cfg.get("diagnostic_legacy")),
        },
        "market_data": {
            "symbol": cfg["symbol"], "timeframe": cfg["timeframe"],
            "dataset_id": cfg["dataset_id"], "dataset_source": cfg["dataset_source"],
            "broker": cfg["dataset_broker"], "server": cfg["dataset_server"],
            "profile": cfg["dataset_profile"], "dataset_version": cfg["dataset_version"],
            "dataset_fingerprint": cfg["dataset_fingerprint"],
            "dataset_bars_total": cfg["dataset_bars"],
            "dataset_range": [_iso(cfg["dataset_first_ts"]), _iso(cfg["dataset_last_ts"])],
            "file": file_info,
        },
        "period": {"start": _iso(cfg["start_ts"]), "end": _iso(cfg["end_ts"]),
                   "window_rows": cfg["window"], "bars": cfg["bars"],
                   "timezone": "UTC (bar open time)"},
        "execution": {
            "engine": "app.backtest.engine (same engine as the research pipeline)",
            "stage": STAGE,
            "initial_balance": cfg["initial_balance"],
            "risk_per_trade": cfg["risk_per_trade"],
            "cost_multipliers": cfg["cost_multipliers"],
            "max_trades": cfg["max_trades"],
            "positions": "single position at a time; engine-determined lot sizing",
        },
        "engine_versions": engine_manifest(),
        "bridge": bridge,
        "orders": {"placed": False,
                   "note": ("A historical backtest never places an order. No MT5 DEMO order, no live-test "
                            "order and no live trade is created by this run.")},
        "reproduce": {
            "request_key": cfg["request_key"],
            "fingerprint_params": {"strategy_id": cfg["strategy_id"], "dataset_id": cfg["dataset_id"],
                                   "start": _iso(cfg["start_ts"]), "end": _iso(cfg["end_ts"]),
                                   "initial_balance": cfg["initial_balance"],
                                   "risk_per_trade": cfg["risk_per_trade"],
                                   "cost_multipliers": cfg["cost_multipliers"]},
        },
    }


def _execute(run_id: str, db: Any) -> None:
    """Worker body: RUNNING -> COMPLETED / FAILED. Never raises."""
    from ..backtest.engine import BacktestRequest, run_backtest
    from ..backtest.fingerprint import compute_experiment_fingerprint
    from ..mt5.factory import bridge_status

    row = _row(db, run_id)
    if not row or row["status"] not in ACTIVE_STATUSES:
        return
    cfg = _jload(row.get("config"), {}) or {}
    try:
        bridge = bridge_status()
    except Exception as e:                                  # pragma: no cover - defensive
        bridge = {"active_bridge": "unavailable", "error": str(e)}
    # V5 §9 — honour a scheduled start. The run stays QUEUED (visible, cancellable)
    # until its time arrives; the single-worker executor keeps START executed runs
    # one at a time either way.
    sched = cfg.get("scheduled_at")
    if sched:
        while True:
            if run_id in _CANCELS:
                _CANCELS.discard(run_id)
                _update_from(db, run_id, ("QUEUED",), status="CANCELLED", finished_at=time.time(),
                             error="cancelled by operator while scheduled")
                return
            now = time.time()
            if now >= float(sched):
                break
            if _row(db, run_id) is None:
                return
            time.sleep(min(5.0, max(0.5, float(sched) - now)))

    started = time.time()
    if not _update_from(db, run_id, ("QUEUED",), status="RUNNING", started_at=started):
        log.info("run %s is no longer QUEUED; worker does not start it", run_id)
        return

    if run_id in _CANCELS:
        _CANCELS.discard(run_id)
        _update_from(db, run_id, ("RUNNING",), status="CANCELLED", finished_at=time.time(),
                     error="cancelled by operator before execution started", runtime_ms=0.0)
        return

    try:
        strat = db.one("SELECT * FROM strategies WHERE id=?", (cfg.get("strategy_id"),))
        if not strat:
            raise RuntimeError(f"strategy #{cfg.get('strategy_id')} disappeared before execution")
        genome = _jload(strat.get("genome"), {})
        if not isinstance(genome, dict) or not (genome.get("entry_long") or genome.get("entry_short")):
            raise RuntimeError("stored genome has no executable entry rule")

        window = cfg.get("window") or [0, 0]
        req_kwargs: Dict[str, Any] = {
            "genome": genome,
            "dataset_id": cfg["dataset_id"],
            "stage": STAGE,
            "window": (int(window[0]), int(window[1])),
            "max_trades": int(cfg.get("max_trades") or 3000),
            # §21 — the node's saved schedule is enforced on the historical bars
            "schedule": cfg.get("schedule"),
            "conditions": cfg.get("conditions"),
        }
        for k, v in (cfg.get("cost_multipliers") or {}).items():
            req_kwargs[k] = float(v)
        res = run_backtest(BacktestRequest(**req_kwargs))
        runtime_ms = (time.time() - started) * 1000.0

        if not res.ok:
            _update_from(db, run_id, ("RUNNING",), status="FAILED", finished_at=time.time(),
                         error=f"backtest engine refused the run: {res.error}",
                         runtime_ms=round(runtime_ms, 1))
            return

        metrics = dict(res.metrics or {})
        metrics.pop("trades_sample", None)
        trades = res.trades or []
        equity = res.equity_curve or []
        provenance = _provenance(cfg, bridge)
        try:
            fp = compute_experiment_fingerprint(
                cfg.get("genome_hash") or "", cfg["symbol"], cfg["timeframe"], STAGE,
                dataset_version=cfg.get("dataset_version") or 1,
                dataset_fingerprint=cfg.get("dataset_fingerprint") or "",
                params={"start": _iso(cfg["start_ts"]), "end": _iso(cfg["end_ts"]),
                        "initial_balance": cfg["initial_balance"],
                        "risk_per_trade": cfg["risk_per_trade"],
                        "cost_multipliers": cfg.get("cost_multipliers")})
            provenance["fingerprint"] = fp
        except Exception as e:                              # pragma: no cover - defensive
            provenance["fingerprint_error"] = str(e)

        artifacts = _write_artifacts(run_id, cfg, metrics, trades, equity, provenance)
        tdf = _trades_frame(run_id)
        derived = _trade_derived(tdf)
        stored_metrics = {"metrics": metrics, "derived": derived,
                          "unavailable": _unavailable(metrics, derived)}
        status = "COMPLETED"
        note = None
        if run_id in _CANCELS:
            _CANCELS.discard(run_id)
            status = "CANCELLED"
            note = ("cancelled by operator after execution started; the run finished and its results are "
                    "kept for audit but are marked CANCELLED")
        _update_from(db, run_id, ("RUNNING",),
                status=status,
                data_source=cfg.get("dataset_source"),
                finished_at=time.time(),
                provenance=jd(provenance),
                engine_versions=jd(provenance.get("engine_versions") or {}),
                metrics=jd(stored_metrics),
                trade_count=len(trades),
                equity_points=len(equity),
                artifacts=jd(artifacts),
                runtime_ms=round(runtime_ms, 1),
                notes=note,
                error=None)
    except Exception as e:
        log.warning("historical backtest run %s failed: %s", run_id, e, exc_info=True)
        _update_from(db, run_id, ("RUNNING", "QUEUED"), status="FAILED", finished_at=time.time(),
                     error=f"{type(e).__name__}: {e}",
                     runtime_ms=round((time.time() - started) * 1000.0, 1))


# --------------------------------------------------------------------------- #
# public write API
# --------------------------------------------------------------------------- #
def start_run(payload: Dict[str, Any], db: Any = None) -> Dict[str, Any]:
    """Validate -> duplicate check -> queue -> execute in the background."""
    d = db or get_db()
    _reconcile_stale(d)
    cfg, errors = validate_request(payload, d)
    if cfg is None:
        return {"ok": False, "started": False, "errors": errors}

    with _SUBMIT_LOCK:
        same = d.one(f"""SELECT run_id, status, created_at FROM {RUN_TABLE}
                          WHERE request_key=? AND status IN ('QUEUED','RUNNING')
                          ORDER BY created_at DESC LIMIT 1""", (cfg["request_key"],))
        if same:
            return {"ok": False, "started": False, "duplicate": True, "existing_run_id": same["run_id"],
                    "errors": [{"field": "request", "error":
                                ("an identical run is already " + same["status"].lower() +
                                 f" ({same['run_id']}); wait for it to finish or change the configuration. "
                                 "Separate periods or settings are queued as separate runs.")}],
                    "run": get_run(same["run_id"], d)}
        active = d.one(f"SELECT run_id, status FROM {RUN_TABLE} WHERE status='RUNNING' LIMIT 1")
        queued = d.one(f"SELECT COUNT(*) c FROM {RUN_TABLE} WHERE status='QUEUED'") or {"c": 0}
        if int(queued.get("c") or 0) >= MAX_QUEUE:
            return {"ok": False, "started": False,
                    "errors": [{"field": "queue", "error":
                                f"run queue is full ({queued['c']} waiting; the executor runs one at a time)"}]}
        _insert_run(d, cfg)
        fresh = d.one(f"SELECT run_id FROM {RUN_TABLE} WHERE request_key=? ORDER BY created_at DESC LIMIT 1",
                      (cfg["request_key"],))
        run_id = fresh["run_id"]
        _EXECUTOR.submit(_execute, run_id, d)

    return {"ok": True, "started": True, "run_id": run_id, "status": "QUEUED",
            "executor": {"running_run_id": (active or {}).get("run_id"), "queue_depth": int(queued.get("c") or 0),
                         "one_at_a_time": True},
            "orders_placed": False, "run": get_run(run_id, d)}


def cancel_run(run_id: str, db: Any = None) -> Dict[str, Any]:
    d = db or get_db()
    row = _row(d, run_id)
    if not row:
        return {"ok": False, "error": f"run {run_id} not found"}
    if row["status"] not in ACTIVE_STATUSES:
        return {"ok": False, "error": f"run {run_id} is {row['status']} and cannot be cancelled",
                "run": get_run(run_id, d)}
    _CANCELS.add(run_id)
    if row["status"] == "QUEUED":
        _update_from(d, run_id, ("QUEUED",), status="CANCELLED", finished_at=time.time(),
                     error="cancelled by operator before execution started")
        _CANCELS.discard(run_id)
        return {"ok": True, "cancelled": True, "run": get_run(run_id, d)}
    return {"ok": True, "cancelled": False,
            "note": ("the run is already executing; a historical backtest is short and completes on its "
                     "own — the cancellation flag is recorded and the run will be marked CANCELLED"),
            "run": get_run(run_id, d)}


# --------------------------------------------------------------------------- #
# public read API
# --------------------------------------------------------------------------- #
def _row_to_run(row: Dict[str, Any], db: Any, *, with_metrics: bool = True) -> Dict[str, Any]:
    stored = _jload(row.get("metrics"), {}) or {}
    out: Dict[str, Any] = {
        "run_id": row["run_id"],
        "label": row.get("label") or LABEL,
        "status": row["status"],
        "strategy_id": row.get("strategy_id"),
        "strategy_identity": _jload(row.get("strategy_identity"), {}),
        "symbol": row.get("symbol"),
        "timeframe": row.get("timeframe"),
        "period": {"start": row.get("start_date"), "end": row.get("end_date"),
                   "start_ts": _num(row.get("start_ts")), "end_ts": _num(row.get("end_ts")),
                   "start_iso": _iso(row.get("start_ts")), "end_iso": _iso(row.get("end_ts")),
                   "bars": row.get("bars"), "timezone": "UTC"},
        "data": {"scope": row.get("data_scope"), "source": row.get("data_source"),
                 "dataset_id": row.get("dataset_id"), "dataset_fingerprint": row.get("dataset_fingerprint")},
        "config": _jload(row.get("config"), {}),
        "created_at": _num(row.get("created_at")),
        "created_iso": _iso(row.get("created_at")),
        "started_iso": _iso(row.get("started_at")),
        "finished_iso": _iso(row.get("finished_at")),
        "runtime_ms": _num(row.get("runtime_ms")),
        "trade_count": _as_int(row.get("trade_count")),
        "equity_points": _as_int(row.get("equity_points")),
        "error": row.get("error"),
        "notes": row.get("notes"),
        "diagnostic_legacy": bool(row.get("diagnostic_legacy")),
        "research_eligible": bool(row.get("research_eligible")),
        "orders_placed": False,
        "is_mt5_data": (row.get("data_source") or "").upper() == MT5_SCOPE,
        "provenance": _jload(row.get("provenance"), {}),
        "artifacts": _jload(row.get("artifacts"), {}),
        "placeholder": None,
    }
    metrics_all = stored.get("metrics") or {}
    cfg_all = out["config"] or {}
    sched = cfg_all.get("schedule") or None
    sched_meta = metrics_all.get("schedule") or {}
    out["schedule"] = {
        "configured": bool(sched),
        "source": "app.live_testing.schedule (the node's saved schedule)",
        "description": cfg_all.get("schedule_description"),
        "applied_to_bars": bool(sched_meta.get("applied")),
        "bars_blocked": sched_meta.get("bars_blocked"),
        "bars_allowed": sched_meta.get("bars_allowed"),
        "conditions": cfg_all.get("conditions") or {},
    }
    # §32 — an honest verdict: zero trades under a strategy/schedule is a
    # COMPLETED run, not an error, and it says so explicitly.
    out["verdict"] = None
    if row["status"] == "COMPLETED":
        n_trades = metrics_all.get("trades")
        if n_trades == 0:
            out["verdict"] = ("Completed — no trades generated under this node's "
                              "strategy/schedule for the selected period.")
        elif isinstance(n_trades, int) and n_trades > 0:
            out["verdict"] = f"Completed — {n_trades} trade(s) evaluated by the engine."
    if with_metrics:
        out["results"] = {"metrics": metrics_all,
                          "derived": stored.get("derived") or {},
                          "unavailable": stored.get("unavailable") or []}
    else:
        out["results"] = {"metrics": {"trades": metrics_all.get("trades"),
                                      "net_profit": metrics_all.get("net_profit"),
                                      "total_return_pct": metrics_all.get("total_return_pct"),
                                      "profit_factor": metrics_all.get("profit_factor"),
                                      "win_rate": metrics_all.get("win_rate"),
                                      "max_drawdown_pct": metrics_all.get("max_drawdown_pct")},
                          "unavailable": [], "note": "summary view — see /runs/{run_id} for full metrics"}
    return out


def get_run(run_id: str, db: Any = None) -> Optional[Dict[str, Any]]:
    d = db or get_db()
    row = _row(d, run_id)
    return _row_to_run(row, d) if row else None


def list_runs(db: Any = None, strategy_id: Optional[int] = None, status: Optional[str] = None,
              limit: int = DEFAULT_TRADES_LIMIT, offset: int = 0,
              include_diagnostic: bool = True) -> Dict[str, Any]:
    d = db or get_db()
    _reconcile_stale(d)
    limit = max(1, min(_as_int(limit) or DEFAULT_TRADES_LIMIT, 200))
    offset = max(0, _as_int(offset) or 0)
    where, args = [], []
    if strategy_id is not None:
        where.append("strategy_id=?")
        args.append(_as_int(strategy_id))
    if status:
        where.append("status=?")
        args.append(str(status).upper())
    if not include_diagnostic:
        where.append("COALESCE(research_eligible,1)=1")
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = (d.one(f"SELECT COUNT(*) c FROM {RUN_TABLE}{clause}", tuple(args)) or {}).get("c", 0)
    rows = d.q(f"SELECT * FROM {RUN_TABLE}{clause} ORDER BY created_at DESC, run_id DESC LIMIT ? OFFSET ?",
               tuple(args) + (limit, offset))
    return {"total": int(total or 0), "limit": limit, "offset": offset,
            "count": len(rows), "label": LABEL, "orders_placed": False,
            "runs": [_row_to_run(r, d, with_metrics=(strategy_id is not None or len(rows) <= 25)) for r in rows]}


def run_trades(run_id: str, db: Any = None, limit: int = DEFAULT_TRADES_LIMIT,
               offset: int = 0) -> Dict[str, Any]:
    d = db or get_db()
    row = _row(d, run_id)
    if not row:
        return {"ok": False, "error": f"run {run_id} not found"}
    limit = max(1, min(_as_int(limit) or DEFAULT_TRADES_LIMIT, MAX_TRADES_LIMIT))
    offset = max(0, _as_int(offset) or 0)
    df = _trades_frame(run_id)
    if df is None:
        return {"ok": True, "run_id": run_id, "status": row["status"], "total": 0, "limit": limit,
                "offset": offset, "trades": [],
                "unavailable": [{"metric": "trades",
                                 "reason": (f"this run has no persisted trade list (status {row['status']}"
                                            + (f", error: {row['error']}" if row.get("error") else "") + ")")}]}
    total = int(len(df))
    page = df.iloc[offset:offset + limit]
    trades = []
    for rec in page.to_dict("records"):
        trades.append({k: (_round(v, 6) if isinstance(v, float) else v) for k, v in rec.items()})
    return {"ok": True, "run_id": run_id, "status": row["status"], "total": total,
            "limit": limit, "offset": offset, "count": len(trades), "trades": trades}


def run_equity(run_id: str, db: Any = None, max_points: int = DEFAULT_EQUITY_POINTS) -> Dict[str, Any]:
    d = db or get_db()
    row = _row(d, run_id)
    if not row:
        return {"ok": False, "error": f"run {run_id} not found"}
    max_points = max(2, min(_as_int(max_points) or DEFAULT_EQUITY_POINTS, MAX_EQUITY_POINTS))
    path = os.path.join(run_dir(run_id), "equity.parquet")
    if not os.path.exists(path):
        return {"ok": True, "run_id": run_id, "status": row["status"], "points": [], "points_total": 0,
                "downsampled": False,
                "unavailable": [{"metric": "equity_curve",
                                 "reason": (f"no equity artifact for this run (status {row['status']}"
                                            + (f", error: {row['error']}" if row.get("error") else "") + ")")}]}
    try:
        df = pd.read_parquet(path)
    except Exception as e:
        return {"ok": False, "error": f"equity artifact unreadable: {e}"}
    total = int(len(df))
    downsampled = total > max_points
    if downsampled:
        idx = sorted({int(round(i * (total - 1) / (max_points - 1))) for i in range(max_points)})
        df = df.iloc[idx]
    points = [[_round(t, 3), _round(e, 2)] for t, e in zip(df["ts"].tolist(), df["equity"].tolist())]
    summary = {
        "start_equity": points[0][1] if points else None,
        "end_equity": points[-1][1] if points else None,
        "min_equity": min((p[1] for p in points), default=None),
        "max_equity": max((p[1] for p in points), default=None),
        "source": "equity.parquet of this run (engine equity curve)",
    }
    return {"ok": True, "run_id": run_id, "status": row["status"], "points_total": total,
            "downsampled": downsampled, "max_points": max_points, "points": points,
            "summary": summary, "initial_balance": _num(row.get("initial_balance"))}
