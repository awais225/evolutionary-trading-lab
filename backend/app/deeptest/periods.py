"""V6.6 §6 — the nine historical period presets, resolved to explicit timestamps.

The period selector is a REQUESTED HISTORY WINDOW only: it never changes the
node's configured candle timeframe. Convention (documented and shown to the
user):

* ``1D`` .. ``3Y`` count CALENDAR time backwards from the period end.
* ``N MONTHS`` = N calendar months, ``N YEARS`` = N calendar years (so 1 MONTH
  ending 2026-03-15 starts 2026-02-15, and 1 YEAR ending a Feb 29 starts the
  previous Feb 28 — the same convention Python's dateutil-free arithmetic uses
  when clamping to the month's length).
* days are 24-hour periods.
* The default period end is "now" (UTC); callers may pass an explicit end.

Every resolved period carries BOTH bounds as UTC ISO strings so the UI can show
the exact requested dates and every scenario can persist them.
"""
from __future__ import annotations

import calendar
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

#: the nine buttons, in display order
PERIODS: Tuple[str, ...] = ("1D", "7D", "14D", "1M", "3M", "6M", "1Y", "2Y", "3Y")

_PERIOD_LABELS = {
    "1D": "1 DAY", "7D": "7 DAYS", "14D": "14 DAYS", "1M": "1 MONTH",
    "3M": "3 MONTHS", "6M": "6 MONTHS", "1Y": "1 YEAR", "2Y": "2 YEARS",
    "3Y": "3 YEARS",
}

_DAYS = {"1D": 1, "7D": 7, "14D": 14}
_MONTHS = {"1M": 1, "3M": 3, "6M": 6}
_YEARS = {"1Y": 1, "2Y": 2, "3Y": 3}

#: documented convention, shipped with the API so the UI can display it
PERIOD_CONVENTION = (
    "Calendar-based windows measured backwards from the period end (UTC): "
    "1 DAY..14 DAYS are 24-hour periods; 1/3/6 MONTHS and 1/2/3 YEARS are calendar "
    "months/years (day clamped to the month's length). The window selects HISTORY "
    "RANGE ONLY — the node's configured candle timeframe is never changed."
)


def _sub_months(end: datetime, months: int) -> datetime:
    """Calendar-month subtraction with day-of-month clamping (leap-safe)."""
    y, m = end.year, end.month
    total = (y * 12 + (m - 1)) - months
    y2, m2 = divmod(total, 12)
    m2 += 1
    day = min(end.day, calendar.monthrange(y2, m2)[1])
    return end.replace(year=y2, month=m2, day=day)


def label_of(duration_key: str) -> str:
    return _PERIOD_LABELS.get(str(duration_key or "").upper(), str(duration_key))


def resolve_period(duration_key: str,
                   end_ts: Optional[float] = None) -> Dict[str, Any]:
    """Resolve one of the nine presets to explicit UTC bounds.

    Returns ``{duration_key, label, start_ts, end_ts, start_iso, end_iso}``.
    Raises ``ValueError`` for an unknown preset (never guesses a window).
    """
    key = str(duration_key or "").strip().upper()
    if key not in PERIODS:
        raise ValueError(
            f"unknown period {duration_key!r} — expected one of {', '.join(PERIODS)}")
    end = datetime.fromtimestamp(float(end_ts) if end_ts else time.time(),
                                 tz=timezone.utc)
    if key in _DAYS:
        start = end - timedelta(days=_DAYS[key])
    elif key in _MONTHS:
        start = _sub_months(end, _MONTHS[key])
    else:
        start = _sub_months(end, _YEARS[key] * 12)
    start_ts = start.timestamp()
    end_ts = end.timestamp()
    return {
        "duration_key": key,
        "label": label_of(key),
        "start_ts": start_ts,
        "end_ts": end_ts,
        "start_iso": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end_iso": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "convention": PERIOD_CONVENTION,
    }


def explicit_period(start_ts: float, end_ts: float) -> Dict[str, Any]:
    """A custom window (CSV / explicit request) normalised the same way."""
    s = datetime.fromtimestamp(float(start_ts), tz=timezone.utc)
    e = datetime.fromtimestamp(float(end_ts), tz=timezone.utc)
    return {
        "duration_key": "CUSTOM",
        "label": "CUSTOM",
        "start_ts": s.timestamp(),
        "end_ts": e.timestamp(),
        "start_iso": s.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end_iso": e.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "convention": PERIOD_CONVENTION,
    }
