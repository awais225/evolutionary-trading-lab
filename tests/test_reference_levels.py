"""Reference levels + M1 momentum + NODE 1 (Parts 2, 3, 17).

Synthetic candles with known prices; every expectation hand-computed."""
from datetime import datetime, timezone

from hero1_exec_semantics.reference_levels import (
    DEFAULT_SESSIONS, SessionDef, compute_momentum_series, compute_reference_series)
from hero1_strategy_spec import compile_spec, validate_spec
from hero1_strategy_spec.examples import prev_day_high_low_momentum_reversal

META = {"XAUUSD": {"pip_size": 0.1, "point_size": 0.01, "tick_size": 0.01,
                   "contract_size": 100}}


def C(ts, o, h, l, c):
    return {"ts": ts, "open": o, "high": h, "low": l, "close": c, "volume": 1}


def ts(y, mo, d, h=0, mi=0):
    return int(datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp())


def test_prev_day_high_low_no_lookahead():
    src = [  # day 1 (2026-01-05): high 110 / low 90; day 2: developing
        C(ts(2026, 1, 5, 10), 100, 110, 95, 105),
        C(ts(2026, 1, 5, 11), 105, 108, 90, 100),
        C(ts(2026, 1, 6, 10), 100, 500, 1, 100),  # day-2 extremes must NOT leak back
    ]
    node = [C(ts(2026, 1, 6, 10), 100, 500, 1, 100)]
    out = compute_reference_series(["PREV_DAY_HIGH", "PREV_DAY_LOW"], node, src, 3600)
    assert out["PREV_DAY_HIGH"] == [110.0]
    assert out["PREV_DAY_LOW"] == [90.0]
    # on day 1 itself there is no previous day
    out2 = compute_reference_series(["PREV_DAY_HIGH"], src[:1], src, 3600)
    assert out2["PREV_DAY_HIGH"] == [None]


def test_session_levels_boundaries_and_developing():
    # London session 08:00-17:00 Europe/London (winter = UTC)
    src = [
        C(ts(2026, 1, 5, 7), 100, 999, 1, 100),    # before session: excluded
        C(ts(2026, 1, 5, 8), 100, 120, 95, 110),   # in session
        C(ts(2026, 1, 5, 16), 110, 130, 100, 120),  # in session
        C(ts(2026, 1, 5, 17), 120, 999, 1, 120),   # boundary end: excluded
    ]
    node = [C(ts(2026, 1, 5, 9), 100, 1, 1, 100),   # developing as of 10:00 close
            C(ts(2026, 1, 6, 9), 100, 1, 1, 100)]   # next day: last completed session
    out = compute_reference_series(["LONDON_HIGH", "LONDON_LOW"], node, src, 3600)
    assert out["LONDON_HIGH"] == [120.0, 130.0]
    assert out["LONDON_LOW"] == [95.0, 95.0]


def test_midnight_crossing_session():
    sdef = SessionDef("X", "UTC", "22:00", "02:00")
    assert sdef.instance_key(ts(2026, 1, 5, 23)) == "2026-01-05"
    assert sdef.instance_key(ts(2026, 1, 6, 1)) == "2026-01-05"  # after midnight -> prior instance
    assert sdef.instance_key(ts(2026, 1, 6, 12)) is None


def test_session_dst_transition():
    # Europe/London springs forward 2026-03-29 01:00 UTC. 08:00 local on
    # 2026-03-30 is 07:00 UTC - the level window must follow local time.
    london = DEFAULT_SESSIONS["LONDON"]
    assert london.instance_key(ts(2026, 3, 30, 7)) == "2026-03-30"   # 08:00 BST
    assert london.instance_key(ts(2026, 3, 30, 6)) is None            # 07:00 BST
    assert london.instance_key(ts(2026, 1, 5, 8)) == "2026-01-05"    # 08:00 GMT


def test_momentum_series_signs_and_no_substitution():
    m1 = [C(ts(2026, 1, 5, 10, 0), 100, 100, 100, 100),
          C(ts(2026, 1, 5, 10, 1), 100, 101, 100, 101),
          C(ts(2026, 1, 5, 10, 2), 101, 101, 100, 101),
          C(ts(2026, 1, 5, 10, 3), 101, 101, 99, 99)]
    node = [C(ts(2026, 1, 5, 10, 2), 0, 0, 0, 0),   # cutoff 10:03 -> closes 101 vs 101 = 0... last two < 10:03: (10:01,101),(10:02,101)
            C(ts(2026, 1, 5, 10, 3), 0, 0, 0, 0)]   # cutoff 10:04 -> 99 vs 101 = down
    out = compute_momentum_series(node, m1, 60)
    assert out == [0.0, -1.0]
    assert compute_momentum_series(node, None, 60) == [None, None]
    assert compute_momentum_series(node, [], 60) == [None, None]


def test_node1_spec_validates_compiles_and_ratio_is_a_parameter():
    spec = prev_day_high_low_momentum_reversal("XAUUSD", "H1")
    rep = validate_spec(spec, broker_meta=META)
    assert rep.status.value == "VALID", rep.issues
    ir = compile_spec(spec, broker_meta=META).ir
    assert ir["data_requirements"] == {"reference_levels": ["PREV_DAY_HIGH", "PREV_DAY_LOW"],
                                       "m1_momentum": True}
    assert ir["execution"]["entry_side_rule"] == "m1_momentum_reversal"
    assert ir["exits"]["stop_loss"]["distance"] == {"unit": "pips", "value": 300.0}
    assert ir["exits"]["take_profit"] == {"ratio": 2.0}
    # reward_risk_ratio is data: 1.5 compiles with a different fingerprint, no engine change
    spec15 = prev_day_high_low_momentum_reversal("XAUUSD", "H1")
    spec15["exits"]["take_profit"] = {"ratio": 1.5}
    spec15["identity"]["metadata"] = {"reward_risk_ratio": 1.5, "official_seed": True}
    c15 = compile_spec(spec15, broker_meta=META)
    assert c15.ir["exits"]["take_profit"] == {"ratio": 1.5}
    assert c15.fingerprint != compile_spec(spec, broker_meta=META).fingerprint
