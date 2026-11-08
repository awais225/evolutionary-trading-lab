"""Calculated price reference levels + M1 momentum (Part 3).

These are REAL calculations over historical candles - never typed-in
values and never decorative. Everything is causal: the value aligned to
node bar ``i`` uses only source bars that end at or before that bar's
close, so no level can see into the future.

Session definitions are CONFIGURABLE (name, IANA timezone, local start /
end, midnight-crossing, DST via ``zoneinfo``). The defaults below are
sensible market conventions expressed in each session's own timezone so
daylight-saving shifts are honoured instead of hardcoding UTC hours:

* ASIA            Asia/Tokyo      09:00-17:00 (JST has no DST)
* LONDON          Europe/London   08:00-17:00
* NEW_YORK        America/New_York 08:00-17:00
* ASIA_LONDON     Europe/London   08:00-09:00 (configured overlap window)
* LONDON_NEW_YORK Europe/London   12:00-17:00 (configured overlap window)

An ongoing session contributes its DEVELOPING high/low from bars already
available at the evaluation time; a completed session contributes its
final high/low. Previous Day High/Low use the previous completed UTC
trading day present in the data (the most recent prior day with candles).

With OHLC source data a level is the extreme of whole bars - exact
intrabar sequencing is never claimed (see the engine's touch operator).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class SessionDef:
    name: str
    tz: str
    start: str  # "HH:MM" local
    end: str    # "HH:MM" local; end <= start means the session crosses midnight

    def _hm(self, v: str) -> time:
        h, m = v.split(":")
        return time(int(h), int(m))

    @property
    def crosses_midnight(self) -> bool:
        return self._hm(self.end) <= self._hm(self.start)

    def instance_key(self, ts: int) -> Optional[str]:
        """Session-instance date (local date the session STARTED) for a
        candle timestamp, or None when outside the session."""
        tz = ZoneInfo(self.tz)
        local = datetime.fromtimestamp(ts, tz=tz)
        t = local.time()
        start, end = self._hm(self.start), self._hm(self.end)
        if not self.crosses_midnight:
            if start <= t < end:
                return local.date().isoformat()
            return None
        if t >= start:
            return local.date().isoformat()
        if t < end:
            return (local.date() - timedelta(days=1)).isoformat()
        return None


DEFAULT_SESSIONS: Dict[str, SessionDef] = {
    "ASIA": SessionDef("ASIA", "Asia/Tokyo", "09:00", "17:00"),
    "LONDON": SessionDef("LONDON", "Europe/London", "08:00", "17:00"),
    "NEW_YORK": SessionDef("NEW_YORK", "America/New_York", "08:00", "17:00"),
    "ASIA_LONDON": SessionDef("ASIA_LONDON", "Europe/London", "08:00", "09:00"),
    "LONDON_NEW_YORK": SessionDef("LONDON_NEW_YORK", "Europe/London", "12:00", "17:00"),
}

# level name -> (session key or 'PREV_DAY', 'high'|'low')
LEVEL_DEFS: Dict[str, tuple] = {
    "PREV_DAY_HIGH": ("PREV_DAY", "high"),
    "PREV_DAY_LOW": ("PREV_DAY", "low"),
    "ASIA_HIGH": ("ASIA", "high"), "ASIA_LOW": ("ASIA", "low"),
    "ASIA_LONDON_HIGH": ("ASIA_LONDON", "high"), "ASIA_LONDON_LOW": ("ASIA_LONDON", "low"),
    "LONDON_HIGH": ("LONDON", "high"), "LONDON_LOW": ("LONDON", "low"),
    "LONDON_NEW_YORK_HIGH": ("LONDON_NEW_YORK", "high"),
    "LONDON_NEW_YORK_LOW": ("LONDON_NEW_YORK", "low"),
    "NEW_YORK_HIGH": ("NEW_YORK", "high"), "NEW_YORK_LOW": ("NEW_YORK", "low"),
}

TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800,
              "H1": 3600, "H4": 14400, "D1": 86400}


def _session_groups(candles: Sequence[dict], sdef: SessionDef) -> Dict[str, List[dict]]:
    groups: Dict[str, List[dict]] = {}
    for c in candles:
        key = sdef.instance_key(int(c["ts"]))
        if key is not None:
            groups.setdefault(key, []).append(c)
    return groups


def _day_groups(candles: Sequence[dict]) -> Dict[str, List[dict]]:
    groups: Dict[str, List[dict]] = {}
    for c in candles:
        day = datetime.fromtimestamp(int(c["ts"]), tz=timezone.utc).date().isoformat()
        groups.setdefault(day, []).append(c)
    return groups


def _value_as_of(bars: List[dict], cutoff: int, field: str,
                 agg) -> Optional[float]:
    vals = [float(b[field]) for b in bars if int(b["ts"]) < cutoff]
    return agg(vals) if vals else None


def compute_reference_series(levels: Sequence[str], node_candles: Sequence[dict],
                             source_candles: Sequence[dict], tf_seconds: int,
                             sessions: Optional[Dict[str, SessionDef]] = None
                             ) -> Dict[str, List[Optional[float]]]:
    """One causal value per node bar for each requested level.

    ``source_candles`` is the finest available series (M1 when the service
    could load it, otherwise the node's own candles - the caller records
    which basis was used; values are never fabricated)."""
    sessions = sessions or DEFAULT_SESSIONS
    cutoffs = [int(c["ts"]) + tf_seconds for c in node_candles]
    out: Dict[str, List[Optional[float]]] = {}
    day_groups = _day_groups(source_candles) if any(
        LEVEL_DEFS.get(l, (None,))[0] == "PREV_DAY" for l in levels) else {}
    session_groups: Dict[str, Dict[str, List[dict]]] = {}
    for level in levels:
        if level not in LEVEL_DEFS:
            raise ValueError(f"unknown reference level {level!r}")
        kind, field = LEVEL_DEFS[level]
        agg = max if field == "high" else min
        series: List[Optional[float]] = []
        if kind == "PREV_DAY":
            days = sorted(day_groups)
            for cutoff in cutoffs:
                cur_day = datetime.fromtimestamp(cutoff - 1, tz=timezone.utc).date().isoformat()
                prior = [d for d in days if d < cur_day]
                if not prior:
                    series.append(None)
                    continue
                series.append(_value_as_of(day_groups[prior[-1]], cutoff, field, agg))
        else:
            if kind not in session_groups:
                session_groups[kind] = _session_groups(source_candles, sessions[kind])
            groups = session_groups[kind]
            keys = sorted(groups)
            for cutoff in cutoffs:
                # Developing instance first (bars already available), else
                # the most recent completed instance with data.
                value = None
                for key in reversed(keys):
                    value = _value_as_of(groups[key], cutoff, field, agg)
                    if value is not None:
                        break
                series.append(value)
        out[level] = series
    return out


def compute_momentum_series(node_candles: Sequence[dict],
                            m1_candles: Optional[Sequence[dict]], tf_seconds: int
                            ) -> List[Optional[float]]:
    """M1 momentum aligned to node bars: +1 up / -1 down / 0 equal, from
    the last two COMPLETED M1 closes strictly before each node bar's
    close. None when M1 data is unavailable - never substituted with the
    node's own timeframe (that would silently change the definition)."""
    cutoffs = [int(c["ts"]) + tf_seconds for c in node_candles]
    if not m1_candles:
        return [None] * len(node_candles)
    closes = [(int(c["ts"]), float(c["close"])) for c in m1_candles]
    out: List[Optional[float]] = []
    j = 0
    last_two: List[float] = []
    for cutoff in cutoffs:
        while j < len(closes) and closes[j][0] < cutoff:
            last_two = (last_two + [closes[j][1]])[-2:]
            j += 1
        if len(last_two) < 2:
            out.append(None)
        else:
            diff = last_two[1] - last_two[0]
            out.append(1.0 if diff > 0 else (-1.0 if diff < 0 else 0.0))
    return out


__all__ = ["SessionDef", "DEFAULT_SESSIONS", "LEVEL_DEFS", "TF_SECONDS",
           "compute_reference_series", "compute_momentum_series"]
