"""Broker server-time <-> UTC conversion (the legacy V9.2 confirmed convention).

Facts (docs: legacy V9_2_DATA_IMPORT_AND_TIMEZONE_AUDIT.md, owner-confirmed
for the IC Markets server used by the supplied history):
  * MT5 candle timestamps and tick times are BROKER SERVER wall time.
  * IC Markets server time is GMT+2 in US standard time and GMT+3 in US
    daylight time; the switch follows the AMERICA/NEW_YORK transition dates
    (second Sunday of March / first Sunday of November), NOT Europe/Athens.
  * The stored candle `ts` convention (importer + market-data store) is the
    naive server epoch: server wall time tagged as if UTC, for ordering
    only. Existing stored data is NEVER retroactively shifted.

This module is the ONLY place that converts between that server epoch and
real UTC. Conversions are date-granular around the two transition Sundays
(the broker flips over the weekend close; the exact intraday switch instant
is not knowable from the evidence, and the fetch merge rules - identical
overlap skips, conflicting overlap refuses - absorb any boundary candle).

`mt5.copy_rates_range()` wants timezone-aware UTC datetimes; the values it
returns carry server-epoch `time`. Use server_to_utc_dt() for requests and
convert returned times with server_to_utc_epoch() before storing them
verbatim into anything. The canonical store is TRUE UTC epoch seconds;
storing server epochs verbatim was the ISSUE-059 root cause (15/15 seam
conflicts were byte-identical candles 3h earlier in stored history).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional, Tuple

STD_OFFSET = 2 * 3600   # GMT+2 (US standard time)
DST_OFFSET = 3 * 3600   # GMT+3 (US daylight time)
RULE_NAME = "IC Markets server time: GMT+2 / GMT+3 on America/New_York DST dates (owner-confirmed, legacy V9.2)"


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The n-th `weekday` (Mon=0..Sun=6) of a month."""
    first = date(year, month, 1)
    delta = (weekday - first.weekday()) % 7
    return first + timedelta(days=delta + 7 * (n - 1))


def us_dst_dates(year: int) -> Tuple[date, date]:
    """(dst_start, dst_end): second Sunday of March, first Sunday of November."""
    return _nth_weekday(year, 3, 6, 2), _nth_weekday(year, 11, 6, 1)


def is_us_dst_on(d: date) -> bool:
    start, end = us_dst_dates(d.year)
    return start <= d < end


def server_offset_seconds(server_epoch: int) -> int:
    """UTC offset of the server wall clock at a naive server epoch."""
    try:
        wall = datetime.fromtimestamp(int(server_epoch), tz=timezone.utc).date()
    except (OverflowError, OSError, ValueError):
        return STD_OFFSET  # absurd inputs: defined result; validation rejects them
    return DST_OFFSET if is_us_dst_on(wall) else STD_OFFSET


def server_to_utc_epoch(server_epoch: int) -> int:
    return int(server_epoch) - server_offset_seconds(server_epoch)


def server_to_utc_dt(server_epoch: int) -> datetime:
    """Timezone-aware UTC datetime for a naive server epoch (MT5 requests)."""
    return datetime.fromtimestamp(server_to_utc_epoch(server_epoch), tz=timezone.utc)


def utc_to_server_epoch(utc_epoch: int) -> int:
    """Inverse of server_to_utc_epoch (iterates to a consistent offset)."""
    utc_epoch = int(utc_epoch)
    for offset in (DST_OFFSET, STD_OFFSET):
        candidate = utc_epoch + offset
        if server_offset_seconds(candidate) == offset:
            return candidate
    # transition-weekend edge: fall back to the offset of the UTC date itself
    d = datetime.fromtimestamp(utc_epoch, tz=timezone.utc).date()
    return utc_epoch + (DST_OFFSET if is_us_dst_on(d) else STD_OFFSET)


def estimate_offset_from_tick(tick_time_epoch: Optional[int], utc_now_epoch: int) -> Tuple[int, str]:
    """Measure the CURRENT server offset from a live tick (terminal fact).

    tick.time is server wall time as an epoch; utc_now is real UTC. The raw
    difference is snapped to the nearest whole hour (server zones are whole
    hours; a stale tick on an illiquid symbol can lag, so the snap also keeps
    small lag from shifting the incomplete-candle cutoff by minutes - and the
    merge layer re-checks every candle anyway).
    Returns (offset_seconds, source-description).
    """
    if tick_time_epoch:
        raw = int(tick_time_epoch) - int(utc_now_epoch)
        snapped = int(round(raw / 3600.0)) * 3600
        if -12 * 3600 <= snapped <= 14 * 3600:
            return snapped, f"measured from live tick (raw {raw}s, snapped to whole hour)"
    return server_offset_seconds(utc_to_server_epoch(utc_now_epoch)), "calendar rule (no usable live tick)"


def server_now_epoch(tick_time_epoch: Optional[int], utc_now_epoch: int) -> int:
    """Best current server epoch: a fresh tick IS server-now; else convert."""
    if tick_time_epoch:
        return int(tick_time_epoch)
    return utc_to_server_epoch(utc_now_epoch)


__all__ = [
    "STD_OFFSET", "DST_OFFSET", "RULE_NAME", "us_dst_dates", "is_us_dst_on",
    "server_offset_seconds", "server_to_utc_epoch", "server_to_utc_dt",
    "utc_to_server_epoch", "estimate_offset_from_tick", "server_now_epoch",
]
