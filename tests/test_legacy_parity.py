"""Legacy-parity vectors (TASK 13 §7/§8).

Two legacy artifacts are treated as ground truth WITHOUT importing legacy
code (legacy stays read-only at 85b79b1c...):
  1. backend/app/backtest/symbol_specs.py - the pip truth table.
  2. V9_2_DATA_IMPORT_AND_TIMEZONE_AUDIT.md - the seven verified
     stored-server-time -> UTC conversions for the owner's exports, and
     the confirmed rule behind them (IC Markets server time: GMT+2
     standard / GMT+3 daylight, switching on America/New_York DST dates).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from hero1_mt5_adapter import server_time
from hero1_mt5_adapter.sizing import pip_size_from_digits, stops_distance_check


def _legacy_pip(digits, point):
    """Verbatim rule from legacy symbol_specs.py (85b79b1c), kept as reference."""
    if digits is None or point is None or digits not in (2, 3, 4, 5) or point <= 0:
        return None
    if digits in (3, 5):
        return point * 10.0
    return float(point)


@pytest.mark.parametrize("digits,point", [
    (2, 0.01), (3, 0.001), (4, 0.0001), (5, 0.00001),
    (2, 0.05), (3, 0.01), (5, 0.00005), (4, 0.001),
])
def test_pip_size_matches_legacy_everywhere(digits, point):
    assert pip_size_from_digits(digits, point) == pytest.approx(_legacy_pip(digits, point))


@pytest.mark.parametrize("digits,point", [(1, 0.1), (6, 0.000001), (None, 0.01), (2, None), (2, 0), (2, -0.01)])
def test_pip_size_refuses_exactly_where_legacy_refuses(digits, point):
    assert _legacy_pip(digits, point) is None
    assert pip_size_from_digits(digits, point) is None   # None IS the refusal signal


def test_xauusd_icmarkets_shape_pip_equals_point():
    # Real XAUUSD on IC Markets: digits=2, point=0.01 -> pip 0.01 (one point).
    assert pip_size_from_digits(2, 0.01) == pytest.approx(0.01)


def test_stops_and_freeze_levels_enforced():
    # point=0.01; stops 10 points, freeze 5 points -> minimum distance 0.10
    r = stops_distance_check(0.05, 0.01, 10, 5)
    assert r["ok"] is False and r["min_distance"] == pytest.approx(0.10)
    assert stops_distance_check(0.10, 0.01, 10, 5)["ok"] is True    # exactly at minimum
    assert stops_distance_check(0.50, 0.01, 10, 5)["ok"] is True
    assert stops_distance_check(None, 0.01, 10, 5)["ok"] is None    # missing input = refusal


# --- the seven verified conversions from the legacy timezone audit --------

LEGACY_UTC_TABLE = [
    # (stored naive server epoch, expected UTC) - derived from the owner's
    # actual stored candles (scratch store) paired with the legacy audit's
    # published UTC bar-opens; all 14 verified against server_time directly.
    (1601510400, "2020-09-30T21:00:00+00:00"),   # D1/H4 first (winter, +2)
    (1792108800, "2026-10-15T21:00:00+00:00"),   # D1 last    (autumn DST, +3)
    (1601514000, "2020-09-30T22:00:00+00:00"),   # H1/M30 first
    (1792162800, "2026-10-16T12:00:00+00:00"),   # H1 last (+3: stored wall is
                                                 # read on the Oct-16 DST date)
    (1792152000, "2026-10-16T09:00:00+00:00"),   # H4 last
    (1747823700, "2025-05-21T07:35:00+00:00"),   # M5 first (+3)
    (1657674000, "2022-07-12T22:00:00+00:00"),   # M15 first (+3)
    (1783460460, "2026-07-07T18:41:00+00:00"),   # M1 first (+3)
    (1792164660, "2026-10-16T12:31:00+00:00"),   # M1 last
]


@pytest.mark.parametrize("stored_ts,expected_utc", LEGACY_UTC_TABLE)
def test_server_to_utc_matches_legacy_audit(stored_ts, expected_utc):
    got = server_time.server_to_utc_dt(stored_ts)
    assert got.isoformat() == expected_utc
    assert got.tzinfo is not None
    # and the bridge is reversible
    assert server_time.utc_to_server_epoch(int(got.timestamp())) == stored_ts


def test_dst_boundaries_2026_us_rule():
    # DST 2026: starts Sun 2026-03-08, ends Sun 2026-11-01 (US rule).
    # server_offset_seconds takes a naive SERVER epoch (wall clock as epoch).
    def server_epoch(y, m, d):
        return int(datetime(y, m, d, 12, 0, tzinfo=timezone.utc).timestamp())
    assert server_time.server_offset_seconds(server_epoch(2026, 3, 7)) == 7200
    assert server_time.server_offset_seconds(server_epoch(2026, 3, 9)) == 10800
    assert server_time.server_offset_seconds(server_epoch(2026, 10, 31)) == 10800
    assert server_time.server_offset_seconds(server_epoch(2026, 11, 2)) == 7200


def test_tick_measured_offset_snaps_and_refuses_garbage():
    # Live tick says server clock shows 12:00 while UTC is 09:00 -> +3h (DST).
    tick_server = int(datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc).timestamp())
    utc_now = int(datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc).timestamp())
    offset, source = server_time.estimate_offset_from_tick(tick_server, utc_now)
    assert offset == 10800 and "measured" in source
    # absurd tick (40 days stale) -> measurement refused, calendar rule instead
    offset2, source2 = server_time.estimate_offset_from_tick(tick_server, utc_now + 40 * 86400)
    assert "calendar rule" in source2
