"""V5 §11 — the live-testing schedule, and its enforcement.

A schedule shown in the UI but ignored by the execution engine is not a
schedule. This module is the single authority the live engine consults before it
opens a position; the UI reads the same evaluation back so what the operator sees
is exactly what the engine will do.

Enforced (each with an explicit reason when it blocks):

  * active days            — which weekdays the node may trade (default Mon–Fri: an
                             unconfigured schedule is the product default, not 24/7)
  * sessions               — Asia / London / New York / Custom, in UTC
  * time window            — start_time..end_time inside the configured timezone
  * spread limit           — maximum tolerated spread (points)
  * cooldown               — minimum minutes between this node's entries
  * maximum trades / day   — per node, counted from its recorded live trades
  * maximum positions      — concurrent open positions (engine-wide limit)

Every session window below is stated in UTC. The engine works in UTC and the
mapping to local time happens only for display.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional

#: session windows in UTC hours [start, end) — the standard cash-session times
SESSION_HOURS_UTC: Dict[str, List[int]] = {
    "asia": [0, 8],
    "london": [7, 16],
    "newyork": [12, 21],
    "london_ny_overlap": [12, 16],
}

#: Monday..Sunday as weekday numbers (0 = Monday, matching the lab's dow column)
DEFAULT_DAYS = [0, 1, 2, 3, 4]        # Mon–Fri

#: the stored config uses short day names; the feature engine uses numbers.
#: Both forms are accepted everywhere so a config written by any version works.
_DAY_NAMES = {"MON": 0, "MONDAY": 0, "TUE": 1, "TUES": 1, "TUESDAY": 1,
              "WED": 2, "WEDNESDAY": 2, "THU": 3, "THUR": 3, "THURS": 3, "THURSDAY": 3,
              "FRI": 4, "FRIDAY": 4, "SAT": 5, "SATURDAY": 5, "SUN": 6, "SUNDAY": 6}


def day_number(value: Any) -> Optional[int]:
    """'Mon' | 'monday' | 0 -> 0. Unknown values return None (never guessed)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= 6 else None
    text = str(value or "").strip().upper()
    if text in _DAY_NAMES:
        return _DAY_NAMES[text]
    try:
        n = int(text)
        return n if 0 <= n <= 6 else None
    except ValueError:
        return None


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_clock(text: Optional[str]) -> Optional[dt.time]:
    if not text:
        return None
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return dt.datetime.strptime(str(text).strip(), fmt).time()
        except ValueError:
            continue
    return None


def _tz(name: Optional[str]) -> dt.tzinfo:
    """Resolve the schedule timezone. Unknown names fall back to UTC *and say so*."""
    key = (name or "UTC").strip().upper()
    if key in ("UTC", "GMT", ""):
        return dt.timezone.utc
    # a small, explicit map (no external dependency)
    offsets = {"PAKISTAN": 5, "PKT": 5, "ASIA/KARACHI": 5, "EST": -5, "EDT": -4,
               "CET": 1, "CEST": 2, "BST": 1, "ET": -5, "NEW YORK": -5,
               "LONDON": 0, "TOKYO": 9, "ASIA/TOKYO": 9, "DUBAI": 4}
    if key in offsets:
        return dt.timezone(dt.timedelta(hours=offsets[key]))
    return dt.timezone.utc


def normalize_config(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Read a stored live-test config into the schedule's own vocabulary."""
    cfg = config or {}
    days = cfg.get("days")
    if isinstance(days, str):
        try:
            import json
            days = json.loads(days)
        except Exception:
            days = None
    if days is not None:
        parsed = [day_number(d) for d in days]
        days = [d for d in parsed if d is not None] or None
    sessions = cfg.get("sessions")
    if isinstance(sessions, str):
        try:
            import json
            sessions = json.loads(sessions)
        except Exception:
            sessions = [sessions]
    if sessions is not None:
        sessions = [str(s).strip().lower() for s in sessions if str(s).strip()]
    return {
        "days": days,
        "sessions": sessions,
        "start_time": cfg.get("start_time"),
        "end_time": cfg.get("end_time"),
        "timezone": cfg.get("timezone") or "UTC",
        "spread_limit_points": _f(cfg.get("spread_limit_points") or cfg.get("max_spread_points")),
        "cooldown_minutes": _f(cfg.get("cooldown_minutes") or cfg.get("cooldown")),
        "max_trades_per_day": _f(cfg.get("max_trades_per_day") or cfg.get("max_daily_trades")),
        "slippage_limit_points": _f(cfg.get("slippage_limit_points") or cfg.get("max_slippage_points")),
        "max_positions": _f(cfg.get("max_positions")),
        "is_active": bool(cfg.get("is_active")),
    }


def evaluate(schedule: Dict[str, Any], *, now: Optional[float] = None,
             spread_points: Optional[float] = None,
             trades_today: int = 0, open_positions: int = 0,
             last_entry_ts: Optional[float] = None) -> Dict[str, Any]:
    """Decide whether the schedule allows a new entry right now.

    Returns ``allowed`` plus the reason, and a full trace of every rule so the UI
    can show *why* a node is idle instead of leaving the operator guessing.
    """
    s = normalize_config(schedule)
    ts = now if now is not None else dt.datetime.now(dt.timezone.utc).timestamp()
    local = dt.datetime.fromtimestamp(ts, tz=_tz(s["timezone"]))
    utc = dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc)

    rules: List[Dict[str, Any]] = []
    block_reason: Optional[str] = None

    def rule(name: str, ok: bool, detail: str) -> None:
        nonlocal block_reason
        rules.append({"rule": name, "ok": ok, "detail": detail})
        if not ok and block_reason is None:
            block_reason = f"{name}: {detail}"

    # 1. days (evaluated in the schedule's timezone: a "Friday" belongs to the
    #    operator's day, not to UTC's)
    days = s["days"] if s["days"] is not None else DEFAULT_DAYS
    weekday = local.weekday()
    rule("days", weekday in days,
         f"{local.strftime('%A')} is {'an active' if weekday in days else 'not an active'} "
         f"trading day (active: {', '.join(_day_name(d) for d in days)})")

    # 2. sessions
    if s["sessions"]:
        in_session = any(
            any(SESSION_HOURS_UTC.get(name, [0, 24])[0] <= utc.hour < SESSION_HOURS_UTC.get(name, [0, 24])[1]
                for name in ([sess] if sess in SESSION_HOURS_UTC else []))
            or any(sess not in SESSION_HOURS_UTC for sess in s["sessions"])   # custom = always in scope
            for sess in s["sessions"]
        )
        windows = ", ".join(
            f"{name} {SESSION_HOURS_UTC[name][0]:02d}:00–{SESSION_HOURS_UTC[name][1]:02d}:00 UTC"
            if name in SESSION_HOURS_UTC else f"{name} (custom)"
            for name in s["sessions"])
        rule("sessions", in_session, f"{utc.strftime('%H:%M')} UTC against {windows}")
    else:
        rule("sessions", True, "no session restriction")

    # 3. time window
    start, end = _parse_clock(s["start_time"]), _parse_clock(s["end_time"])
    if start and end:
        now_t = local.time()
        if start <= end:
            ok = start <= now_t < end
        else:                                    # window crosses midnight
            ok = now_t >= start or now_t < end
        rule("window", ok, f"{now_t.strftime('%H:%M')} {s['timezone']} against "
                           f"{start.strftime('%H:%M')}–{end.strftime('%H:%M')}")
    else:
        rule("window", True, "no start/end window configured")

    # 4. spread limit
    limit = s["spread_limit_points"]
    if limit is not None and spread_points is not None:
        rule("spread", float(spread_points) <= limit,
             f"spread {spread_points:g} points against the {limit:g}-point limit")
    elif limit is not None:
        rule("spread", True, f"spread limit {limit:g} points set; current spread unavailable")
    else:
        rule("spread", True, "no spread limit configured")

    # 5. cooldown
    cd = s["cooldown_minutes"]
    if cd is not None and last_entry_ts:
        elapsed = (ts - float(last_entry_ts)) / 60.0
        rule("cooldown", elapsed >= cd,
             f"{elapsed:.1f} min since the last entry against a {cd:g}-min cooldown")
    else:
        rule("cooldown", True, "no cooldown configured" if cd is None else "no previous entry")

    # 6. trades per day
    max_day = s["max_trades_per_day"]
    if max_day is not None:
        rule("max_trades_per_day", float(trades_today) < max_day,
             f"{trades_today:g} of {max_day:g} trades taken today")
    else:
        rule("max_trades_per_day", True, "no daily trade cap configured")

    # 7. positions
    max_pos = s["max_positions"]
    if max_pos is not None:
        rule("max_positions", float(open_positions) < max_pos,
             f"{open_positions:g} open against a {max_pos:g}-position cap")
    else:
        rule("max_positions", True, "no per-node position cap configured")

    return {
        "allowed": block_reason is None,
        "reason": block_reason,
        "rules": rules,
        "local_time": local.isoformat(timespec="seconds"),
        "utc_time": utc.isoformat(timespec="seconds"),
        "timezone": s["timezone"],
        "weekday": _day_name(local.weekday()),
        "schedule": s,
    }


def _day_name(d: int) -> str:
    names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    try:
        return names[int(d)]
    except (IndexError, ValueError):
        return str(d)


def describe(schedule: Optional[Dict[str, Any]]) -> str:
    """One human line describing a schedule (used by the node table)."""
    s = normalize_config(schedule)
    if not any([s["days"], s["sessions"], s["start_time"], s["end_time"], s["cooldown_minutes"]]):
        # an unconfigured schedule is not "unrestricted": the product default is
        # Mon–Fri, all sessions (the same default the config writer stores), and
        # the description must say so rather than imply 24/7 trading.
        return "Mon–Fri (default), all sessions, no other restriction"
    parts = []
    if s["days"] is not None:
        parts.append("/".join(_day_name(d)[:3] for d in s["days"]))
    if s["sessions"]:
        parts.append("+".join(x.title() for x in s["sessions"]))
    if s["start_time"] and s["end_time"]:
        parts.append(f"{s['start_time']}–{s['end_time']} {s['timezone']}")
    if s["cooldown_minutes"]:
        parts.append(f"cooldown {s['cooldown_minutes']:g}m")
    if s["spread_limit_points"]:
        parts.append(f"spread<={s['spread_limit_points']:g}pt")
    if s["max_trades_per_day"]:
        parts.append(f"max {s['max_trades_per_day']:g}/day")
    return ", ".join(parts)
