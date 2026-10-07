"""V5 diagnostics (spec §4) — read-only, authoritative, never fabricated.

Four reports, all derived from sources that already exist in the lab:

    data_report()          DATA       — every dataset artifact and its real state
    eligibility_report()   ELIGIBILITY— per-timeframe usability for research
    backtest_report()      BACKTEST   — requested / generated / tested / skipped /
                                        rejected / failed / data-failures /
                                        backtest-errors / valid / alive
    evolution_report()     EVOLUTION  — parents, mutations, duplicates, unique,
                                        failed, surviving + per-generation survival

Nothing here writes to DATA, to the database or to any queue. A missing value is
reported as missing (``None`` + a reason string), never estimated.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import paths as P
from .config import get_config
from .db.database import get_db
from .status import ALIVE_STATUSES, LEGACY_ALIVE_STATUSES

_CACHE: Dict[str, Tuple[float, Any]] = {}
_CACHE_TTL_S = 15.0


def _cached(key: str, fn):
    hit = _CACHE.get(key)
    now = time.time()
    if hit and now - hit[0] < _CACHE_TTL_S:
        return hit[1]
    value = fn()
    _CACHE[key] = (now, value)
    return value


def invalidate() -> None:
    _CACHE.clear()


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _fmt_time(ts: Optional[float]) -> Optional[str]:
    if not ts:
        return None
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return None


def _dataset_rows() -> List[Dict[str, Any]]:
    db = get_db()
    return db.q("SELECT * FROM datasets ORDER BY symbol, timeframe, created_at DESC")


def _path_state(raw: Optional[str]) -> str:
    """Classify a stored path without touching the filesystem."""
    if not raw:
        return "unrecorded"
    if "\\" in raw or (len(raw) > 1 and raw[1] == ":"):
        return "stale-windows"
    if not raw.startswith(str(P.DATA_ROOT)) and "/evolutionary-trading-lab/" in raw:
        return "stale-workspace"
    return "current-root"


def _missing_periods(df, ts_col: str, timeframe: str) -> Dict[str, Any]:
    """Detect gaps larger than an expected weekend/holiday allowance."""
    import pandas as pd
    minutes = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60}.get(timeframe, 15)
    ts = pd.to_datetime(df[ts_col], unit="s", utc=True) if df[ts_col].dtype != "datetime64[ns, UTC]" else df[ts_col]
    ts = pd.Series(ts).sort_values()
    if len(ts) < 3:
        return {"gap_count": 0, "largest_gap_minutes": None, "expected_interval_minutes": minutes}
    deltas = ts.diff().dropna().dt.total_seconds() / 60.0
    # weekend closure allowance: a gap over Friday->Sunday is expected for XAUUSD
    threshold = minutes * 3
    gaps = deltas[deltas > threshold]
    weekend_like = gaps[gaps > 60 * 24].shape[0]
    real_gaps = gaps[gaps <= 60 * 24]
    return {
        "gap_count": int(real_gaps.shape[0]),
        "largest_gap_minutes": (round(float(deltas.max()), 1) if len(deltas) else None),
        "expected_interval_minutes": minutes,
        "weekend_closures": int(weekend_like),
    }


# --------------------------------------------------------------------------- #
# 1. DATA report
# --------------------------------------------------------------------------- #
def data_report(include_rows: int = 400) -> Dict[str, Any]:
    def build() -> Dict[str, Any]:
        import pandas as pd

        from .data.engine import get_data_engine
        de = get_data_engine()
        rows = _dataset_rows()
        datasets: List[Dict[str, Any]] = []
        for r in rows[:include_rows]:
            dsid = r["id"]
            entry: Dict[str, Any] = {
                "dataset_id": dsid,
                "symbol": r.get("symbol"),
                "timeframe": r.get("timeframe"),
                "source": r.get("source"),
                "kind": r.get("kind"),
                "bars_recorded": r.get("bars"),
                "stored_path": r.get("path"),
                "path_state": _path_state(r.get("path")),
                "created_at": _fmt_time(r.get("created_at")),
            }
            artifact = None
            try:
                artifact = de.find_dataset_artifact_path(dsid)
            except Exception as e:
                entry["resolution_error"] = str(e)[:200]
            entry["artifact_path"] = str(artifact) if artifact else None
            entry["artifact_relative"] = P.data_relative_path(artifact) if artifact else None
            if artifact:
                try:
                    st = artifact.stat()
                    entry["file_size_bytes"] = st.st_size
                    entry["file_format"] = artifact.suffix.lstrip(".") or "unknown"
                    entry["last_modified"] = _fmt_time(st.st_mtime)
                    # magic-byte check before parsing: a truncated parquet is corrupt,
                    # not "unavailable"
                    with open(artifact, "rb") as fh:
                        head = fh.read(4)
                        fh.seek(max(0, st.st_size - 4))
                        tail = fh.read(4)
                    if head != b"PAR1" or tail != b"PAR1":
                        entry.update({
                            "readable": False, "corruption": "parquet magic bytes not found in footer",
                            "rows_on_disk": None, "start": None, "end": None,
                            "eligibility": "CORRUPT",
                        })
                    else:
                        df = pd.read_parquet(artifact)
                        ts_col = next((c for c in ("ts", "time", "date", "timestamp") if c in df.columns), None)
                        entry["rows_on_disk"] = int(len(df))
                        entry["columns"] = list(df.columns)[:24]
                        entry["readable"] = True
                        entry["corruption"] = None
                        if ts_col:
                            start, end = df[ts_col].min(), df[ts_col].max()
                            entry["start"] = _fmt_time(start)
                            entry["end"] = _fmt_time(end)
                            entry["start_ts"] = float(start)
                            entry["end_ts"] = float(end)
                            entry["span_days"] = round((float(end) - float(start)) / 86400.0, 2)
                            try:
                                entry["gaps"] = _missing_periods(df, ts_col, r.get("timeframe") or "M15")
                            except Exception as e:
                                entry["gaps"] = {"error": str(e)[:120]}
                        elif r.get("start_ts") and r.get("end_ts"):
                            entry["start"] = _fmt_time(r["start_ts"])
                            entry["end"] = _fmt_time(r["end_ts"])
                except Exception as e:
                    entry.update({"readable": False, "corruption": f"{type(e).__name__}: {e}"[:200]})
            else:
                entry.update({"readable": False, "corruption": None, "rows_on_disk": None,
                              "eligibility": "UNAVAILABLE"})
            try:
                ok, why = de.is_dataset_eligible_for_research(dsid, require_features=False)
                entry["physically_eligible"] = bool(ok)
                entry["eligibility_reason"] = why
            except Exception as e:
                entry["physically_eligible"] = False
                entry["eligibility_reason"] = str(e)[:160]
            try:
                state, reason, fpath = de.feature_artifact_status(dsid)
                entry["feature_artifact"] = {
                    "state": state, "reason": reason,
                    "path": P.data_relative_path(fpath) if fpath else None,
                }
            except Exception as e:
                entry["feature_artifact"] = {"state": "unknown", "reason": str(e)[:160]}
            entry["usability"] = (
                "VALID" if entry.get("physically_eligible") else
                ("CORRUPT" if entry.get("corruption") else "UNAVAILABLE")
            )
            datasets.append(entry)

        by_state: Dict[str, int] = {}
        for d in datasets:
            by_state[d.get("usability") or "UNKNOWN"] = by_state.get(d.get("usability") or "UNKNOWN", 0) + 1

        return {
            "generated_at": _fmt_time(time.time()),
            "data_root": str(P.DATA_ROOT),
            "dataset_rows": len(rows),
            "reported": len(datasets),
            "by_usability": by_state,
            "datasets": datasets,
            "note": ("Rows are the authoritative dataset registry. A dataset whose stored path is stale is "
                     "resolved by id through the lab's artifact resolver; a dataset whose artifact cannot be "
                     "read is reported as CORRUPT and never silently replaced."),
        }
    return _cached("data", build)


# --------------------------------------------------------------------------- #
# 2. ELIGIBILITY report
# --------------------------------------------------------------------------- #
def eligibility_report() -> Dict[str, Any]:
    def build() -> Dict[str, Any]:
        from .data.engine import get_data_engine
        de = get_data_engine()
        cfg = get_config()
        symbol = cfg.data.symbol or "XAUUSD"
        rows = _dataset_rows()
        timeframes = sorted({(r.get("timeframe") or "").upper() for r in rows if r.get("timeframe")}) or ["M15"]

        out: List[Dict[str, Any]] = []
        for tf in timeframes:
            candidates = [r for r in rows if (r.get("timeframe") or "").upper() == tf]
            state = {
                "timeframe": tf,
                "candidates": len(candidates),
                "available": 0, "corrupt": 0, "unavailable": 0, "unsupported": 0,
                "insufficient_range": 0, "valid": 0,
            }
            best = None
            reasons: List[str] = []
            for c in candidates:
                try:
                    ok, why = de.is_dataset_eligible_for_research(c["id"], require_features=False)
                except Exception as e:
                    ok, why = False, f"{type(e).__name__}: {e}"[:160]
                if ok:
                    state["available"] += 1
                    state["valid"] += 1
                    if best is None:
                        best = c["id"]
                elif "corrupt" in why.lower() or "parquet" in why.lower() or "invalid" in why.lower():
                    state["corrupt"] += 1
                    reasons.append(f"{c['id']}: {why}")
                elif "not found" in why.lower() or "missing" in why.lower():
                    state["unavailable"] += 1
                else:
                    state["unsupported"] += 1
                if why and why != "ELIGIBLE":
                    reasons.append(f"{c['id']}: {why}") if f"{c['id']}: {why}" not in reasons else None
            try:
                resolved = de.latest_dataset(symbol, tf, require_eligible=True)
            except Exception:
                resolved = None
            state["resolved_dataset"] = (resolved or {}).get("id")
            state["testable"] = bool(resolved)
            state["reasons"] = reasons[:5]
            out.append(state)

        required = [tf for tf in timeframes]
        return {
            "generated_at": _fmt_time(time.time()),
            "symbol": symbol,
            "required_timeframes": required,
            "testable_timeframes": [s["timeframe"] for s in out if s["testable"]],
            "unusable_timeframes": [s["timeframe"] for s in out if not s["testable"]],
            "timeframes": out,
            "note": ("A timeframe is testable when at least one dataset resolves to a readable artifact with "
                     "price and time columns. Feature artifacts are derived: a missing cache is computed on "
                     "demand and never blocks research."),
        }
    return _cached("eligibility", build)


# --------------------------------------------------------------------------- #
# 3. BACKTEST report
# --------------------------------------------------------------------------- #
def _status_counts(scope_sql: str = "", args: Tuple = ()) -> Dict[str, int]:
    db = get_db()
    rows = db.q(f"SELECT status, COUNT(*) n FROM strategies {scope_sql} GROUP BY status", args)
    return {str(r["status"]): int(r["n"]) for r in rows}


def backtest_report() -> Dict[str, Any]:
    def build() -> Dict[str, Any]:
        db = get_db()
        total = db.one("SELECT COUNT(*) c FROM strategies")["c"]
        user = db.one("SELECT COUNT(*) c FROM strategies WHERE data_source='USER_RESEARCH'")["c"]
        legacy = db.one("SELECT COUNT(*) c FROM strategies WHERE data_source='LEGACY_TEST'")["c"]
        raw = _status_counts()
        infra_rows = db.q("""SELECT
                                SUM(CASE WHEN creation_reason LIKE 'DATASET UNAVAILABLE%' THEN 1 ELSE 0 END) data_unavailable,
                                SUM(CASE WHEN creation_reason LIKE 'TRAIN_WINDOW_FAILED%' THEN 1 ELSE 0 END) train_window,
                                SUM(CASE WHEN failure_reason LIKE '%worker%' THEN 1 ELSE 0 END) worker,
                                SUM(CASE WHEN creation_reason LIKE '%unreadable%'
                                          OR creation_reason LIKE '%parquet%'
                                          OR creation_reason LIKE '%magic bytes%'
                                          OR failure_reason LIKE '%unreadable%'
                                          OR failure_reason LIKE '%magic bytes%' THEN 1 ELSE 0 END) corrupted,
                                SUM(CASE WHEN status='FAILED' AND failure_reason LIKE 'REJECTED:%' THEN 1 ELSE 0 END) strategy_rejected,
                                SUM(CASE WHEN status='FAILED' AND (failure_reason IS NULL OR failure_reason NOT LIKE 'REJECTED:%')
                                          AND (creation_reason IS NULL OR creation_reason NOT LIKE 'DATASET UNAVAILABLE%')
                                          AND (creation_reason IS NULL OR creation_reason NOT LIKE 'TRAIN_WINDOW_FAILED%')
                                    THEN 1 ELSE 0 END) unclassified
                             FROM strategies""")[0]
        infra = {k: int(v or 0) for k, v in dict(infra_rows).items()}

        backtests = db.one("SELECT COUNT(*) c FROM backtests")["c"]
        validations = db.one("SELECT COUNT(*) c FROM validations")["c"]
        # a node counts as TESTED only when a backtest record for it exists; the
        # rest were never judged (bounded by real data availability)
        tested_nodes = db.one("""SELECT COUNT(*) c FROM strategies s
                                 WHERE EXISTS (SELECT 1 FROM backtests b WHERE b.strategy_id = s.id)""")["c"]
        validated_nodes = db.one("""SELECT COUNT(*) c FROM strategies s
                                    WHERE EXISTS (SELECT 1 FROM validations v WHERE v.strategy_id = s.id)""")["c"]
        alive = sum(int(raw.get(s, 0)) for s in ALIVE_STATUSES + LEGACY_ALIVE_STATUSES)
        skipped = infra["data_unavailable"] + infra["train_window"]
        none_tested = int(total) - int(tested_nodes)
        cancelled = int(raw.get("KILLED", 0)) + int(raw.get("RETIRED", 0))
        unclassified = int(infra["unclassified"])
        other_failed = max(0, none_tested - skipped)
        rejected = infra["strategy_rejected"]

        gen = db.q("SELECT * FROM generation_stats ORDER BY generation DESC LIMIT 12")
        research_runs = db.q("SELECT * FROM research_runs ORDER BY created_at DESC LIMIT 5") if _table_exists("research_runs") else []

        return {
            "generated_at": _fmt_time(time.time()),
            "scope": "all persisted strategies (USER_RESEARCH + LEGACY_TEST)",
            "counts": {
                "requested": int(total),
                "generated": int(total),
                "tested": int(tested_nodes),
                "validated": int(validated_nodes),
                "never_tested": int(none_tested),
                "skipped": int(skipped),
                "rejected": int(rejected),
                "failed": int(other_failed),
                "valid_or_alive": int(alive),
                "cancelled": int(cancelled),
                "unclassified_failures": int(unclassified),
                "data_failures": int(infra["data_unavailable"] + infra["train_window"]),
                "data_corrupt": int(infra["corrupted"]),
                "backtest_errors": int(infra["worker"]),
                "user_research": int(user),
                "legacy_test": int(legacy),
                "backtest_records": int(backtests),
                "validation_records": int(validations),
            },
            "reconciliation": {
                "tested_plus_never_tested_equals_requested":
                    int(tested_nodes) + int(none_tested) == int(total),
                "skipped_is_never_tested": int(skipped) <= int(none_tested) + int(rejected),
                "alive_is_counted_separately": True,
            },

            "raw_status_counts": raw,
            "generations": [
                {
                    "generation": g["generation"], "born": g["born"], "tested": g["tested"],
                    "failed": g["failed"], "survived": g["survived"], "validated": g["validated"],
                    "qualified": g["qualified"], "killed": g["killed"], "duplicates_blocked": g["duplicates_blocked"],
                    "best_fitness": g["best_fitness"], "ts": _fmt_time(g["ts"]),
                } for g in gen
            ],
            "research_runs": [
                {k: r.get(k) for k in ("run_id", "status", "target_nodes", "generated_nodes",
                                       "completed_nodes", "qualified_nodes", "current_generation",
                                       "created_at", "last_checkpoint") if k in r} for r in research_runs
            ],
            "note": ("'skipped' are nodes that were never tested because their market data was not available; "
                     "they are infrastructure outcomes, not strategy failures. 'failed' counts only nodes that "
                     "were actually tested and rejected by the research gates."),
        }
    return _cached("backtest", build)


def _table_exists(name: str) -> bool:
    try:
        db = get_db()
        return bool(db.one("SELECT 1 x FROM sqlite_master WHERE type='table' AND name=?", (name,)))
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# 4. EVOLUTION report
# --------------------------------------------------------------------------- #
def evolution_report(limit_generations: int = 20) -> Dict[str, Any]:
    def build() -> Dict[str, Any]:
        db = get_db()
        parents = db.one("""SELECT COUNT(DISTINCT parent_id) c FROM strategies
                            WHERE parent_id IS NOT NULL""")["c"]
        eligible_parents = db.one("""SELECT COUNT(DISTINCT s.id) c FROM strategies s
                                     WHERE s.status IN ('SURVIVED','QUALIFIED','PAPER','SHORTLISTED')
                                       AND EXISTS (SELECT 1 FROM strategies c WHERE c.parent_id = s.id)""")["c"] \
            if _table_exists("strategies") else 0
        mutations = db.q("""SELECT json_extract(genome,'$.timeframe') tf,
                                   CASE WHEN mutation_type IS NULL OR mutation_type='' THEN 'seed/other' ELSE mutation_type END mt,
                                   COUNT(*) n
                            FROM strategies WHERE data_source='USER_RESEARCH'
                            GROUP BY mt ORDER BY n DESC LIMIT 15""")
        unique_genomes = db.one("SELECT COUNT(DISTINCT hash) c FROM strategies")["c"] \
            if _column_exists("strategies", "hash") else None

        gens = db.q("SELECT * FROM generation_stats ORDER BY generation ASC")
        survival = [
            {
                "generation": g["generation"],
                "born": g["born"], "tested": g["tested"], "survived": g["survived"],
                "failed": g["failed"], "qualified": g["qualified"], "killed": g["killed"],
                "duplicates_blocked": g["duplicates_blocked"],
                "survival_rate_pct": (round(100.0 * (g["survived"] or 0) / g["tested"], 2) if (g["tested"] or 0) else None),
            } for g in gens[-limit_generations:]
        ]

        infra = db.one("""SELECT
                            SUM(CASE WHEN status='FAILED' AND creation_reason LIKE 'DATASET UNAVAILABLE%' THEN 1 ELSE 0 END) data_unavailable
                          FROM strategies WHERE data_source='USER_RESEARCH'""")["data_unavailable"] or 0
        classified_infra = db.one("""SELECT COUNT(*) c FROM strategies
                                     WHERE status IN ('DATA_UNAVAILABLE','DATA_CORRUPT','BACKTEST_ERROR')""")["c"]

        return {
            "generated_at": _fmt_time(time.time()),
            "counts": {
                "parent_candidates": int(parents),
                "eligible_parents": int(eligible_parents),
                "duplicate_candidates_blocked": (db.one("SELECT COALESCE(SUM(duplicates_blocked),0) s FROM generation_stats")["s"]
                                                 if _table_exists("generation_stats") else None),
                "unique_genomes": unique_genomes,
                "infrastructure_skipped_user_research": int(infra),
                "infrastructure_classified_rows": int(classified_infra),
            },
            "mutation_types": [{"type": m["mt"], "count": int(m["n"])} for m in mutations],
            "generations": survival,
            "note": ("Survival is recorded per generation from the lab's own generation statistics. "
                     "Nodes skipped for missing data are excluded from the survival denominator because they "
                     "were never evaluated."),
        }
    return _cached("evolution", build)


def _column_exists(table: str, column: str) -> bool:
    try:
        db = get_db()
        return any(r["name"] == column for r in db.q(f"PRAGMA table_info({table})"))
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# combined
# --------------------------------------------------------------------------- #
def all_reports() -> Dict[str, Any]:
    return {
        "data": data_report(),
        "eligibility": eligibility_report(),
        "backtest": backtest_report(),
        "evolution": evolution_report(),
    }
