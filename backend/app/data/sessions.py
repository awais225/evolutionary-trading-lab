"""
DST-aware trading sessions (spec §43).

Derives session boundaries using the IANA timezone database for every bar date:
  asia      09:00-15:00 Asia/Tokyo
  london    08:00-16:00 Europe/London     (GMT/BST)
  newyork   08:00-16:00 America/New_York  (EST/EDT)
  off       everything else

Overlaps belong to the later-opening session (newyork > london > asia).
All internal timestamps remain UTC; labeling is timezone- and DST-accurate.
"""
from __future__ import annotations

import time as _t
from datetime import datetime, timezone
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

_TZ_ASIA = ZoneInfo("Asia/Tokyo")
_TZ_LONDON = ZoneInfo("Europe/London")
_TZ_NY = ZoneInfo("America/New_York")

SESSION_NAMES = ("asia", "london", "newyork", "off")


def session_of_ts(ts: float) -> str:
    """Label one UTC epoch timestamp using local exchange times."""
    dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)

    # 1. New York (08:00 - 16:00 local)
    ny_hour = dt.astimezone(_TZ_NY).hour
    if 8 <= ny_hour < 16:
        return "newyork"

    # 2. London (08:00 - 16:00 local)
    lon_hour = dt.astimezone(_TZ_LONDON).hour
    if 8 <= lon_hour < 16:
        return "london"

    # 3. Asia / Tokyo (09:00 - 15:00 local)
    asia_hour = dt.astimezone(_TZ_ASIA).hour
    if 9 <= asia_hour < 15:
        return "asia"

    return "off"


def session_labels(ts_array: np.ndarray) -> np.ndarray:
    """Vectorized session labels for an array of UTC epoch timestamps (spec §43)."""
    ts = np.asarray(ts_array, dtype=np.float64)
    if ts.size == 0:
        return np.array([], dtype=object)

    dt_idx = pd.to_datetime(ts, unit="s", utc=True)
    ny_hours = dt_idx.tz_convert(_TZ_NY).hour
    lon_hours = dt_idx.tz_convert(_TZ_LONDON).hour
    asia_hours = dt_idx.tz_convert(_TZ_ASIA).hour

    out = np.full(len(ts), "off", dtype=object)
    # Apply in priority order: later wins
    out[(asia_hours >= 9) & (asia_hours < 15)] = "asia"
    out[(lon_hours >= 8) & (lon_hours < 16)] = "london"
    out[(ny_hours >= 8) & (ny_hours < 16)] = "newyork"
    return out


def session_now() -> str:
    return session_of_ts(_t.time())
