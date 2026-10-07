"""V5 status taxonomy — strategy outcomes vs data/infrastructure outcomes.

Spec V5 §3: a strategy must never be classified as failed because historical
data was unavailable, a dataset was missing or corrupt, a feature artifact could
not be loaded, or a worker crashed. Those are infrastructure conditions with
their own statuses.

This module is the single authority for that translation:

    * :data:`V5_STATUSES`      — the canonical vocabulary
    * :func:`classify_failure` — reason text  -> failure class
    * :func:`v5_status`        — stored row   -> one of the V5 statuses
    * :func:`is_alive`         — does this status count as an alive node

Nothing here rewrites historical records: the classification is derived from the
stored ``status`` / ``creation_reason`` / ``failure_reason`` / ``survival_reason``
columns, so the authoritative DATA keeps its own history untouched while the
dashboard reports what actually happened.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

# --------------------------------------------------------------------------- #
# canonical vocabulary (spec V5 §3)
# --------------------------------------------------------------------------- #
NOT_TESTED = "NOT_TESTED"
PENDING = "PENDING"
TESTING = "TESTING"
VALID = "VALID"
STRATEGY_FAILED = "STRATEGY_FAILED"
DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
DATA_CORRUPT = "DATA_CORRUPT"
BACKTEST_ERROR = "BACKTEST_ERROR"
LIVE_ELIGIBLE = "LIVE_ELIGIBLE"
LIVE_TESTING = "LIVE_TESTING"
LIVE_COMPLETED = "LIVE_COMPLETED"
MT5_DEMO = "MT5_DEMO"

V5_STATUSES = (
    NOT_TESTED, PENDING, TESTING, VALID, STRATEGY_FAILED,
    DATA_UNAVAILABLE, DATA_CORRUPT, BACKTEST_ERROR,
    LIVE_ELIGIBLE, LIVE_TESTING, LIVE_COMPLETED, MT5_DEMO,
)

#: statuses that mean "this node is usable / not a dead end"
ALIVE_STATUSES = (VALID, LIVE_ELIGIBLE, LIVE_TESTING, LIVE_COMPLETED, MT5_DEMO, TESTING, NOT_TESTED, PENDING)

#: infrastructure conditions: the strategy itself was never judged
INFRASTRUCTURE_STATUSES = (DATA_UNAVAILABLE, DATA_CORRUPT, BACKTEST_ERROR)

#: stored (pre-V5) statuses that already mean "alive". Historical rows keep the
#: vocabulary they were written with — the counter has to read both, otherwise
#: 33 genuinely alive nodes in the authoritative DATA would read as 0.
LEGACY_ALIVE_STATUSES = ("SURVIVED", "QUALIFIED", "SHORTLISTED", "SCREENED",
                         "PAPER", "MT5_BACKTESTED", "LIVE_ELIGIBLE", "LIVE_TESTING")

#: human labels for the UI
STATUS_LABELS = {
    NOT_TESTED: "Not tested",
    PENDING: "Pending",
    TESTING: "Testing",
    VALID: "Valid",
    STRATEGY_FAILED: "Strategy failed",
    DATA_UNAVAILABLE: "Data unavailable",
    DATA_CORRUPT: "Data corrupt",
    BACKTEST_ERROR: "Backtest error",
    LIVE_ELIGIBLE: "Live eligible",
    LIVE_TESTING: "Live testing",
    LIVE_COMPLETED: "Live completed",
    MT5_DEMO: "MT5 demo",
}

#: one-line explanations used as tooltips / table text
STATUS_HINTS = {
    DATA_UNAVAILABLE: "Historical data for this symbol/timeframe/date range was not available — "
                      "the strategy was never judged on performance.",
    DATA_CORRUPT: "A dataset or feature artifact could not be read — the strategy was never judged on performance.",
    BACKTEST_ERROR: "The backtest itself failed (worker/engine error) — the strategy was never judged on performance.",
    STRATEGY_FAILED: "The strategy was tested against real data and did not meet the research gates.",
    VALID: "Tested against real data and still alive.",
}

# --------------------------------------------------------------------------- #
# reason classification
# --------------------------------------------------------------------------- #
#: ordered (pattern, class) pairs. The first match wins, so the more specific
#: data/corruption patterns come before the generic ones.
_REASON_RULES: Tuple[Tuple[str, str], ...] = (
    # ---- data corruption / unreadable artifacts -------------------------- #
    ("magic bytes", DATA_CORRUPT),
    ("parquet", DATA_CORRUPT),
    ("corrupt", DATA_CORRUPT),
    ("unreadable", DATA_CORRUPT),
    ("failed to load dataset", DATA_CORRUPT),
    ("failed to load feature", DATA_CORRUPT),
    ("feature artifact has 0 rows", DATA_CORRUPT),
    ("dataset parquet on disk has 0 rows", DATA_CORRUPT),
    ("schema invalid", DATA_CORRUPT),
    ("feature artifact missing", DATA_UNAVAILABLE),
    ("physical dataset artifact not found", DATA_UNAVAILABLE),
    # ---- data unavailability --------------------------------------------- #
    ("dataset unavailable", DATA_UNAVAILABLE),
    ("no eligible dataset", DATA_UNAVAILABLE),
    ("train_window_failed", DATA_UNAVAILABLE),
    ("insufficient bars", DATA_UNAVAILABLE),
    ("insufficient history", DATA_UNAVAILABLE),
    ("no bars", DATA_UNAVAILABLE),
    ("date range", DATA_UNAVAILABLE),
    ("history unavailable", DATA_UNAVAILABLE),
    ("no historical data", DATA_UNAVAILABLE),
    ("data unavailable", DATA_UNAVAILABLE),
    # ---- backtest / worker errors ----------------------------------------- #
    ("evaluation worker error", BACKTEST_ERROR),
    ("evaluation timeout", BACKTEST_ERROR),
    ("failed recovery", BACKTEST_ERROR),
    ("worker", BACKTEST_ERROR),
    ("engine error", BACKTEST_ERROR),
    ("backtest error", BACKTEST_ERROR),
    ("exception", BACKTEST_ERROR),
    ("traceback", BACKTEST_ERROR),
)

#: reasons that mean the *strategy* was judged and rejected (kept explicit so a
#: wording change in the gates can never silently reclassify a real rejection)
_STRATEGY_MARKERS = (
    "rejected:",
    "insufficient trades",
    "drawdown",
    "profit factor",
    "sharpe",
    "complexity",
    "oos collapse",
    "oos profit factor",
    "degradation",
    "instability",
    "failed screening",
    "sub-threshold",
    "sub threshold",
    "failed validation",
    "no positive edge",
)


def classify_failure(*reasons: Optional[str]) -> Optional[str]:
    """Return the failure class for the given reason text, or None if it is not
    an infrastructure condition (i.e. the strategy itself was judged)."""
    text = " ".join(str(r) for r in reasons if r).strip().lower()
    if not text:
        return None
    for pattern, cls in _REASON_RULES:
        if pattern in text:
            # a *strategy* rejection always carries its own gate wording; the
            # gates never mention datasets, so the two can be separated safely.
            if "rejected:" in text and cls == DATA_UNAVAILABLE:
                return STRATEGY_FAILED
            return cls
    if any(m in text for m in _STRATEGY_MARKERS):
        return STRATEGY_FAILED
    return None


# --------------------------------------------------------------------------- #
# stored row -> V5 status
# --------------------------------------------------------------------------- #
#: pipeline stage (authoritative progression) -> V5 status, when it is further
#: along than the raw strategy status
_STAGE_STATUS = {
    "LIVE_TESTING": LIVE_TESTING,
    "LIVE_TESTED": LIVE_COMPLETED,
    "MT5_DEMO": MT5_DEMO,
    "FINAL_CANDIDATE": LIVE_COMPLETED,
}


def v5_status(row: Dict[str, Any]) -> str:
    """Translate one stored strategy row into the V5 vocabulary."""
    if not row:
        return NOT_TESTED
    raw = str(row.get("status") or "").strip().upper()
    stage = str(row.get("pipeline_stage") or "").strip().upper()
    reason = row.get("failure_reason") or row.get("creation_reason") or row.get("survival_reason")

    # V5-native rows already speak the canonical vocabulary — pass them through
    # verbatim. (Falling through to the "unknown" branch would have reported a
    # DATA_UNAVAILABLE node as NOT_TESTED, i.e. as alive.)
    if raw in V5_STATUSES:
        return raw

    # terminal infrastructure outcomes keep their own class
    if raw == "FAILED":
        cls = classify_failure(reason)
        return cls or STRATEGY_FAILED
    if raw in ("KILLED", "RETIRED"):
        return STRATEGY_FAILED
    if raw in ("SURVIVED", "QUALIFIED", "SHORTLISTED", "MT5_BACKTESTED", "PAPER", "LIVE_ELIGIBLE"):
        # a node that has progressed further is reported at its furthest stage
        if stage in _STAGE_STATUS:
            return _STAGE_STATUS[stage]
        if raw == "SHORTLISTED" or row.get("shortlisted"):
            return LIVE_ELIGIBLE
        return VALID
    if raw in ("BORN", "PENDING", ""):
        return NOT_TESTED
    if raw in ("BACKTESTING", "VALIDATING", "TESTING"):
        return TESTING
    if raw in ("SCREENED",):
        return TESTING
    # unknown stored value: never silently call it failed
    return NOT_TESTED


def is_alive(row_or_status: Any) -> bool:
    """True when a node is not a dead end (used by the 'alive nodes' counters)."""
    if isinstance(row_or_status, str):
        status = row_or_status.strip().upper()
        return status in ALIVE_STATUSES or status in LEGACY_ALIVE_STATUSES
    if isinstance(row_or_status, dict):
        raw = str(row_or_status.get("status") or "").strip().upper()
        if raw in LEGACY_ALIVE_STATUSES:
            return True
    return v5_status(row_or_status) in ALIVE_STATUSES


def is_infrastructure(row_or_status: Any) -> bool:
    status = row_or_status if isinstance(row_or_status, str) else v5_status(row_or_status)
    return status in INFRASTRUCTURE_STATUSES


def status_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    """Everything the dashboard needs to render a status cell honestly."""
    status = v5_status(row)
    reason = row.get("failure_reason") or row.get("creation_reason") or row.get("survival_reason")
    return {
        "v5_status": status,
        "v5_status_label": STATUS_LABELS.get(status, status),
        "v5_status_hint": STATUS_HINTS.get(status, ""),
        "is_alive": status in ALIVE_STATUSES,
        "is_infrastructure_failure": status in INFRASTRUCTURE_STATUSES,
        "failure_class": classify_failure(reason) if str(row.get("status") or "").upper() == "FAILED" else None,
        "failure_reason": reason,
    }
