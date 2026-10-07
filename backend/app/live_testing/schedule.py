"""V5 §11 — the live-testing schedule, and its enforcement.

A schedule shown in the UI but ignored by the execution engine is not a
schedule. This module is the single authority the live engine consults before it
opens a position; the UI reads the same evaluation back so what the operator sees
is exactly what the engine will do — and the deep backtest applies the *same*
:func:`bar_mask` to the historical bars, so a node configured ``Mon + Wed`` can
never be backtested (or traded) across the whole week by accident.

Enforced (each with an explicit reason when it blocks):

  * enabled                — an explicit on/off switch for the node's schedule
  * active days            — which weekdays the node may trade (default Mon–Fri: an
                             unconfigured schedule is the product default, not 24/7)
  * sessions               — Asia / London / New York / Custom, in UTC
  * regimes                — the research engine's own regime vocabulary
  * timeframes             — the timeframes this node may trade/backtest
  * signal conditions      — which of the node's own entry/exit groups are active
  * time windows           — one or more start..end windows in the configured tz
  * spread limit           — maximum tolerated spread (points)
  * cooldown               — minimum minutes between this node's entries
  * maximum trades / day   — per node, counted from its recorded live trades
  * maximum positions      — concurrent open positions (engine-wide limit)

Every session window below is stated in UTC. The engine works in UTC and the
mapping to local time happens only for display.

Empty-list semantics are explicit and documented: for days, sessions, regimes and
timeframes an **empty list means "no restriction from this group"**, and the
canonical *unconfigured* state is the product default (Mon–Fri, all sessions,
all regimes, the node's own timeframe). The UI always states which of the two it
is showing, and :func:`describe` spells it out, so "no restriction" is never an
accident of an empty array.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

#: session windows in UTC hours [start, end) — the standard cash-session times
SESSION_HOURS_UTC: Dict[str, List[int]] = {
    "asia": [0, 8],
    "london": [7, 16],
    "newyork": [12, 21],
    "london_ny_overlap": [12, 16],
}

#: Monday..Sunday as weekday numbers (0 = Monday, matching the lab's dow column)
DEFAULT_DAYS = [0, 1, 2, 3, 4]        # Mon–Fri

#: The session names the engine can enforce. ``custom`` is accepted because the
#: operator may express the whole restriction through explicit time windows.
SUPPORTED_SESSIONS: List[str] = list(SESSION_HOURS_UTC) + ["custom"]

#: Regime vocabulary — re-exported from the backtest engine so the schedule, the
#: research engine and the feature store can never drift apart.
def supported_regimes() -> List[str]:
    from ..backtest.engine import REGIME_NAMES
    return list(REGIME_NAMES)


#: Timeframes the market bridge / data engine can serve.
def supported_timeframes() -> List[str]:
    from ..config import get_config
    tfs = list(get_config().data.timeframes or [])
    for tf in ("M1", "M5", "M15", "M30", "H1", "H4", "D1"):
        if tf not in tfs:
            tfs.append(tf)
    return tfs


#: The node's own signal components that the schedule may switch on or off. These
#: are read from the genome — the schedule never invents strategy logic.
def conditions_for_genome(genome: Optional[Dict[str, Any]]) -> List[str]:
    g = genome or {}
    names: List[str] = []
    if g.get("entry_long"):
        names.append("entry_long")
    if g.get("entry_short"):
        names.append("entry_short")
    ex = g.get("exit") or {}
    if isinstance(ex, dict):
        if ex.get("exit_condition"):
            names.append("exit_condition")
        if ex.get("trailing"):
            names.append("trailing_stop")
    return names


CONDITION_LABELS = {
    "entry_long": "long entry rule",
    "entry_short": "short entry rule",
    "exit_condition": "conditional exit rule",
    "trailing_stop": "trailing stop",
}

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


def _json_list(raw: Any) -> Optional[list]:
    """Accept a JSON string, a list/tuple, or None; anything else -> None."""
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            import json
            raw = json.loads(raw)
        except Exception:
            return [raw] if raw.strip() else None
    if isinstance(raw, (list, tuple, set)):
        return list(raw)
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


def tz_is_known(name: Optional[str]) -> bool:
    key = (name or "UTC").strip().upper()
    return key in ("UTC", "GMT", "", "PAKISTAN", "PKT", "ASIA/KARACHI", "EST", "EDT",
                   "CET", "CEST", "BST", "ET", "NEW YORK", "LONDON", "TOKYO",
                   "ASIA/TOKYO", "DUBAI")


def _norm_days(raw: Any) -> Optional[List[int]]:
    days = _json_list(raw)
    if days is None:
        return None
    parsed = [day_number(d) for d in days]
    out: List[int] = []
    for d in parsed:
        if d is None:
            continue
        if d not in out:
            out.append(d)
    if days and not out:
        # every value was unrecognised: report the group as unusable (None) rather
        # than as an empty selection, so nobody can confuse "Caturday" with "no
        # days" — validate_config rejects the raw values and the API refuses them.
        return None
    # an explicitly empty selection is *not* "no restriction" — the validator
    # rejects it before it can be stored, and callers treat [] as "nothing allowed"
    return sorted(out)


def _norm_str_list(raw: Any, lower: bool = True) -> Optional[List[str]]:
    items = _json_list(raw)
    if items is None:
        return None
    out: List[str] = []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        text = text.lower() if lower else text.upper()
        if text not in out:
            out.append(text)
    return out


def _norm_windows(raw: Any) -> List[Dict[str, Any]]:
    """Normalise the window list: [{start,end,enabled}] with HH:MM strings."""
    items = _json_list(raw)
    out: List[Dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        start = str(item.get("start") or "").strip()
        end = str(item.get("end") or "").strip()
        if not start or not end:
            continue
        out.append({"start": start, "end": end,
                    "enabled": bool(item.get("enabled", True))})
    return out


def normalize_config(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Read a stored live-test config into the schedule's own vocabulary."""
    cfg = config or {}

    days = _norm_days(cfg.get("days"))
    sessions = _norm_str_list(cfg.get("sessions"))
    regimes = _norm_str_list(cfg.get("regimes"))
    timeframes = _norm_str_list(cfg.get("timeframes"), lower=False)

    conditions_raw = cfg.get("conditions")
    if isinstance(conditions_raw, str):
        try:
            import json
            conditions_raw = json.loads(conditions_raw)
        except Exception:
            conditions_raw = None
    conditions = ({str(k): bool(v) for k, v in conditions_raw.items()}
                  if isinstance(conditions_raw, dict) else None)

    windows = _norm_windows(cfg.get("windows"))
    # legacy single window (start_time/end_time) is folded into the window list
    if not windows and cfg.get("start_time") and cfg.get("end_time"):
        windows = [{"start": str(cfg["start_time"]), "end": str(cfg["end_time"]),
                    "enabled": True}]

    # "enabled" is only *explicit* when the input actually says so. A config that
    # has already passed through here carries the `enabled_explicit` marker; its
    # `enabled` key is then a derived value, and re-reading it as an explicit
    # switch would flip "not configured" into "switched off" — which silently
    # zeroes every bar of a historical run. This function must be idempotent
    # because its own output is re-normalised by describe(), bar_mask(), evaluate()
    # and the run builder.
    # Only the schedule's own switch counts as explicit. ``is_active`` is the
    # node's live *arming* state (START/STOP), not a rule: reading it as an
    # explicit switch-off would make a deep backtest of a not-currently-armed node
    # block every bar and report COMPLETED with 0 trades.
    enabled_raw = cfg.get("enabled")
    if cfg.get("enabled_explicit") is False:
        enabled_raw = None
    if enabled_raw is None:
        enabled = bool(cfg.get("is_active"))
        enabled_explicit = False
    else:
        enabled = bool(enabled_raw)
        enabled_explicit = True

    return {
        "enabled": enabled,
        "enabled_explicit": enabled_explicit,
        "days": days,
        "sessions": sessions,
        "regimes": regimes,
        "timeframes": timeframes,
        "conditions": conditions,
        "windows": windows,
        # kept for backward compatibility with older readers
        "start_time": cfg.get("start_time") or ((windows[0]["start"]) if windows else None),
        "end_time": cfg.get("end_time") or ((windows[0]["end"]) if windows else None),
        "timezone": cfg.get("timezone") or "UTC",
        "spread_limit_points": _f(cfg.get("spread_limit_points") or cfg.get("max_spread_points")),
        "cooldown_minutes": _f(cfg.get("cooldown_minutes") or cfg.get("cooldown")),
        "max_trades_per_day": _f(cfg.get("max_trades_per_day") or cfg.get("max_daily_trades")),
        "slippage_limit_points": _f(cfg.get("slippage_limit_points") or cfg.get("max_slippage_points")),
        "max_positions": _f(cfg.get("max_positions")),
        "is_active": bool(cfg.get("is_active")),
    }


def condition_enabled(schedule: Optional[Dict[str, Any]], name: str) -> bool:
    """Is one of the node's own signal components active under this schedule?

    ``None`` (unconfigured) means "leave the node's own definition alone".
    """
    s = normalize_config(schedule)
    conds = s["conditions"]
    if not conds:
        return True
    return bool(conds.get(name, True))


# --------------------------------------------------------------------------- #
# validation (§19) — nothing invalid or ambiguous may be stored
# --------------------------------------------------------------------------- #
def validate_config(config: Dict[str, Any], *, genome: Optional[Dict[str, Any]] = None,
                    node_timeframe: Optional[str] = None) -> List[Dict[str, str]]:
    """Reject impossible or ambiguous schedules *before* they are stored."""
    errors: List[Dict[str, str]] = []
    cfg = config or {}

    days = _json_list(cfg.get("days"))
    if days is not None:
        if not days:
            errors.append({"field": "days",
                           "error": "select at least one day (an empty day list is not "
                                    "'all days' — use Select All)"})
        else:
            bad = [d for d in days if day_number(d) is None]
            if bad:
                errors.append({"field": "days", "error": f"unsupported day value(s) {bad}"})

    sessions = _json_list(cfg.get("sessions"))
    if sessions is not None:
        if not sessions:
            errors.append({"field": "sessions",
                           "error": "select at least one session (an empty list is not "
                                    "'all sessions' — use Select All)"})
        else:
            bad = [s for s in sessions if str(s).strip().lower() not in SUPPORTED_SESSIONS]
            if bad:
                errors.append({"field": "sessions",
                               "error": f"unsupported session(s) {bad}; supported: "
                                        f"{SUPPORTED_SESSIONS}"})

    regimes = _json_list(cfg.get("regimes"))
    if regimes is not None and regimes:
        allowed = set(supported_regimes())
        bad = [r for r in regimes if str(r).strip().lower() not in allowed]
        if bad:
            errors.append({"field": "regimes",
                           "error": f"unsupported regime(s) {bad}; supported: {sorted(allowed)}"})

    tfs = _json_list(cfg.get("timeframes"))
    if tfs is not None:
        if not tfs:
            errors.append({"field": "timeframes",
                           "error": "select at least one timeframe (an empty list is not "
                                    "'all timeframes' — use Select All)"})
        else:
            allowed = set(supported_timeframes())
            bad = [t for t in tfs if str(t).strip().upper() not in allowed]
            if bad:
                errors.append({"field": "timeframes",
                               "error": f"unsupported timeframe(s) {bad}; supported: "
                                        f"{sorted(allowed)}"})
            elif node_timeframe and str(node_timeframe).upper() not in \
                    {str(t).strip().upper() for t in tfs}:
                errors.append({"field": "timeframes",
                               "error": (f"node timeframe {node_timeframe} is not in the "
                                         f"allowed list {tfs}: the node could never trade")})

    conds = cfg.get("conditions")
    if isinstance(conds, str):
        try:
            import json
            conds = json.loads(conds)
        except Exception:
            conds = None
    if conds is not None:
        if not isinstance(conds, dict):
            errors.append({"field": "conditions", "error": "conditions must be an object"})
        else:
            supported = set(conditions_for_genome(genome))
            unknown = [k for k in conds if k not in supported]
            if unknown:
                errors.append({"field": "conditions",
                               "error": f"this node has no such signal component(s) {unknown}; "
                                        f"supported: {sorted(supported) or 'none'}"})
            if supported and conds and not any(bool(v) for v in conds.values()):
                errors.append({"field": "conditions",
                               "error": "every signal component is switched off — the node "
                                        "could never open a position"})

    windows = _json_list(cfg.get("windows"))
    if windows:
        for i, w in enumerate(windows):
            if not isinstance(w, dict):
                errors.append({"field": "windows", "error": f"window #{i + 1} is not an object"})
                continue
            start, end = _parse_clock(w.get("start")), _parse_clock(w.get("end"))
            if start is None or end is None:
                errors.append({"field": "windows",
                               "error": f"window #{i + 1} needs both a start and an end "
                                        f"time in HH:MM"})
            elif start == end:
                errors.append({"field": "windows",
                               "error": f"window #{i + 1} starts and ends at the same time"})

    tzname = cfg.get("timezone")
    if tzname and not tz_is_known(tzname):
        errors.append({"field": "timezone",
                       "error": f"unknown timezone {tzname!r}; supported: UTC, Pakistan/PKT, "
                                f"EST, EDT, CET, CEST, BST, New York, London, Tokyo, Dubai"})

    for field in ("spread_limit_points", "cooldown_minutes", "max_trades_per_day", "max_positions"):
        raw = cfg.get(field)
        if raw in (None, ""):
            continue
        val = _f(raw)
        if val is None or val < 0:
            errors.append({"field": field, "error": f"{field} must be a non-negative number"})

    start = _parse_clock(cfg.get("start_time"))
    end = _parse_clock(cfg.get("end_time"))
    if (cfg.get("start_time") or cfg.get("end_time")) and (start is None or end is None):
        errors.append({"field": "start_time",
                       "error": "start/end time must both be HH:MM (24-hour)"})
    return errors


# --------------------------------------------------------------------------- #
# evaluation (§20) — what the live engine calls before every order
# --------------------------------------------------------------------------- #
def evaluate(schedule: Dict[str, Any], *, now: Optional[float] = None,
             spread_points: Optional[float] = None,
             trades_today: int = 0, open_positions: int = 0,
             last_entry_ts: Optional[float] = None,
             regime: Optional[str] = None, timeframe: Optional[str] = None) -> Dict[str, Any]:
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

    # 0. the explicit switch
    if s["enabled_explicit"]:
        rule("enabled", s["enabled"],
             "schedule is switched ON" if s["enabled"] else "schedule is switched OFF")

    # 1. days (evaluated in the schedule's timezone: a "Friday" belongs to the
    #    operator's day, not to UTC's)
    days = s["days"] if s["days"] is not None else DEFAULT_DAYS
    weekday = local.weekday()
    rule("days", weekday in days,
         f"{local.strftime('%A')} is {'an active' if weekday in days else 'not an active'} "
         f"trading day (active: {', '.join(_day_name(d) for d in days)})")

    # 2. sessions
    if s["sessions"]:
        known = [x for x in s["sessions"] if x in SESSION_HOURS_UTC]
        unknown = [x for x in s["sessions"] if x not in SESSION_HOURS_UTC]
        in_session = any(SESSION_HOURS_UTC[x][0] <= utc.hour < SESSION_HOURS_UTC[x][1]
                         for x in known) or bool(unknown)   # custom = always in scope
        windows = ", ".join(
            f"{name} {SESSION_HOURS_UTC[name][0]:02d}:00–{SESSION_HOURS_UTC[name][1]:02d}:00 UTC"
            if name in SESSION_HOURS_UTC else f"{name} (custom)"
            for name in s["sessions"])
        rule("sessions", in_session, f"{utc.strftime('%H:%M')} UTC against {windows}")
    else:
        rule("sessions", True, "no session restriction")

    # 3. market regime
    if s["regimes"]:
        allowed = s["regimes"]
        if regime is None:
            rule("regimes", True,
                 f"allowed: {'+'.join(allowed)}; the current regime was not supplied "
                 "to this evaluation")
        else:
            rule("regimes", str(regime).lower() in allowed,
                 f"current regime {regime} against {'+'.join(allowed)}")
    else:
        rule("regimes", True, "no regime restriction")

    # 4. timeframes
    if s["timeframes"]:
        allowed = {str(x).upper() for x in s["timeframes"]}
        if timeframe is None:
            rule("timeframes", True, f"allowed: {','.join(sorted(allowed))} (not evaluated here)")
        else:
            rule("timeframes", str(timeframe).upper() in allowed,
                 f"timeframe {timeframe} against {','.join(sorted(allowed))}")
    else:
        rule("timeframes", True, "no timeframe restriction")

    # 5. time windows (any enabled window containing "now" opens the gate)
    active_windows = [w for w in s["windows"] if w.get("enabled", True)]
    if active_windows:
        now_t = local.time()
        hits = []
        for w in active_windows:
            start, end = _parse_clock(w["start"]), _parse_clock(w["end"])
            if start is None or end is None:
                continue
            inside = (start <= now_t < end) if start <= end else (now_t >= start or now_t < end)
            hits.append((inside, w))
        ok = any(hit for hit, _ in hits)
        detail = ", ".join(window_label(w) for _, w in hits) or "invalid windows"
        rule("window", ok, f"{now_t.strftime('%H:%M')} {s['timezone']} against {detail}")
    else:
        rule("window", True, "no start/end window configured")

    # 6. spread limit
    limit = s["spread_limit_points"]
    if limit is not None and spread_points is not None:
        rule("spread", float(spread_points) <= limit,
             f"spread {spread_points:g} points against the {limit:g}-point limit")
    elif limit is not None:
        rule("spread", True, f"spread limit {limit:g} points set; current spread unavailable")
    else:
        rule("spread", True, "no spread limit configured")

    # 7. cooldown
    cd = s["cooldown_minutes"]
    if cd is not None and last_entry_ts:
        elapsed = (ts - float(last_entry_ts)) / 60.0
        rule("cooldown", elapsed >= cd,
             f"{elapsed:.1f} min since the last entry against a {cd:g}-min cooldown")
    else:
        rule("cooldown", True, "no cooldown configured" if cd is None else "no previous entry")

    # 8. trades per day
    max_day = s["max_trades_per_day"]
    if max_day is not None:
        rule("max_trades_per_day", float(trades_today) < max_day,
             f"{trades_today:g} of {max_day:g} trades taken today")
    else:
        rule("max_trades_per_day", True, "no daily trade cap configured")

    # 9. positions
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


# --------------------------------------------------------------------------- #
# historical application (§21) — the deep backtest applies the same schedule
# --------------------------------------------------------------------------- #
def bar_mask(schedule: Optional[Dict[str, Any]], *, ts, dow, session=None,
             regime=None) -> np.ndarray:
    """Boolean per-bar mask: True where the schedule *permits* an entry.

    Vectorised over the dataset the backtest is about to run, using the very same
    rules :func:`evaluate` applies live, so a node configured ``Mon + Wed +
    London`` cannot be silently backtested across the whole week.

    ``ts``      — bar open times (epoch seconds, numpy array)
    ``dow``     — weekday numbers, 0 = Monday (numpy array)
    ``session`` — session labels per bar (optional)
    ``regime``  — dominant regime label per bar (optional)
    """
    s = normalize_config(schedule)
    ts = np.asarray(ts, dtype=np.float64)
    dow = np.asarray(dow).astype(np.int64)
    mask = np.ones(ts.shape, dtype=bool)

    if s["enabled_explicit"] and not s["enabled"]:
        return np.zeros(ts.shape, dtype=bool)

    day_zone = _tz(s["timezone"])
    # days are operator-local: convert the bar time into the schedule timezone
    if len(ts):
        offset = dt.datetime.fromtimestamp(float(ts[0]), tz=dt.timezone.utc).astimezone(day_zone).utcoffset()
        shift = offset.total_seconds() if offset else 0.0
    else:
        shift = 0.0
    local_dow = ((np.floor((ts + shift) / 86400.0).astype(np.int64) + 3) % 7)  # 1970-01-01 = Thursday
    days = s["days"] if s["days"] is not None else DEFAULT_DAYS
    if days:
        mask &= np.isin(local_dow, list(days))
    else:
        mask &= False                                   # explicit empty = nothing allowed

    utc_hour = ((ts // 3600) % 24).astype(np.int64)
    if s["sessions"]:
        sess_mask = np.zeros(ts.shape, dtype=bool)
        for name in s["sessions"]:
            if name in SESSION_HOURS_UTC:
                lo, hi = SESSION_HOURS_UTC[name]
                sess_mask |= (utc_hour >= lo) & (utc_hour < hi)
            else:
                sess_mask |= True                       # custom/unknown = the windows decide
        mask &= sess_mask

    if s["regimes"]:
        if regime is None:
            # no regime labels were supplied: the caller cannot honour the rule, so
            # the run must fail loudly rather than pretend the filter was applied
            raise ValueError("schedule restricts regimes but no regime labels were supplied "
                             "for the historical bars")
        labels = np.asarray([str(x).lower() for x in np.asarray(regime, dtype=object)])
        mask &= np.isin(labels, [str(x).lower() for x in s["regimes"]])

    active_windows = [w for w in s["windows"] if w.get("enabled", True)]
    if active_windows:
        local_sec = (ts + shift) % 86400.0
        win_mask = np.zeros(ts.shape, dtype=bool)
        for w in active_windows:
            start, end = _parse_clock(w["start"]), _parse_clock(w["end"])
            if start is None or end is None:
                continue
            a = start.hour * 3600 + start.minute * 60
            b = end.hour * 3600 + end.minute * 60
            if a <= b:
                win_mask |= (local_sec >= a) & (local_sec < b)
            else:
                win_mask |= (local_sec >= a) | (local_sec < b)
        mask &= win_mask
    return mask


# --------------------------------------------------------------------------- #
# presentation
# --------------------------------------------------------------------------- #
def _day_name(d: int) -> str:
    names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    try:
        return names[int(d)]
    except (IndexError, ValueError):
        return str(d)


def window_label(w: Dict[str, Any]) -> str:
    """``17:00–08:00 (overnight)`` — never let a midnight-crossing window look plain."""
    start, end = _parse_clock(w.get("start")), _parse_clock(w.get("end"))
    text = f"{w.get('start')}–{w.get('end')}"
    if start is not None and end is not None and start > end:
        return text + " (overnight)"
    return text


def describe(schedule: Optional[Dict[str, Any]]) -> str:
    """One human line describing a schedule (used by the node table and dialog)."""
    s = normalize_config(schedule)
    configured = any([s["days"], s["sessions"], s["regimes"], s["timeframes"],
                      s["conditions"], s["windows"], s["cooldown_minutes"],
                      s["spread_limit_points"], s["max_trades_per_day"]])
    if not configured:
        # an unconfigured schedule is not "unrestricted": the product default is
        # Mon–Fri, all sessions (the same default the config writer stores), and
        # the description must say so rather than imply 24/7 trading.
        base = "Mon–Fri (default), all sessions, no other restriction"
        if s["enabled_explicit"] and not s["enabled"]:
            return "switched OFF — " + base
        return base
    parts: List[str] = []
    if s["enabled_explicit"] and not s["enabled"]:
        parts.append("OFF")
    if s["days"] is not None:
        parts.append("/".join(_day_name(d)[:3] for d in s["days"]) if s["days"] else "no days")
    if s["sessions"]:
        parts.append("+".join(x.title() for x in s["sessions"]))
    if s["regimes"]:
        parts.append("+".join(x for x in s["regimes"]))
    if s["timeframes"]:
        parts.append("+".join(x for x in s["timeframes"]))
    if s["conditions"]:
        off = [k for k, v in s["conditions"].items() if not v]
        on = [k for k, v in s["conditions"].items() if v]
        parts.append("conditions " + "+".join(on) if not off else
                     f"conditions {', '.join(on) or 'none'} (off: {', '.join(off)})")
    if s["windows"]:
        parts.append(" ".join(f"{window_label(w)}{'' if w.get('enabled', True) else ' (off)'}"
                              for w in s["windows"]) + f" {s['timezone']}")
    if s["cooldown_minutes"]:
        parts.append(f"cooldown {s['cooldown_minutes']:g}m")
    if s["spread_limit_points"]:
        parts.append(f"spread<={s['spread_limit_points']:g}pt")
    if s["max_trades_per_day"]:
        parts.append(f"max {s['max_trades_per_day']:g}/day")
    if s["max_positions"]:
        parts.append(f"max {s['max_positions']:g} open")
    return ", ".join(parts)


def supported_options(genome: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Everything the operator may choose from for *this* node (§17)."""
    return {
        "days": [{"value": i, "label": name} for i, name in enumerate(
            ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])],
        "sessions": [{"value": name,
                      "label": name.replace("_", " ").title(),
                      "hours_utc": SESSION_HOURS_UTC.get(name)}
                     for name in SUPPORTED_SESSIONS],
        "regimes": [{"value": name, "label": name.replace("_", " ").title()}
                    for name in supported_regimes()],
        "timeframes": [{"value": name, "label": name} for name in supported_timeframes()],
        "conditions": [{"value": name, "label": CONDITION_LABELS.get(name, name)}
                       for name in conditions_for_genome(genome)],
        "node_timeframe": (genome or {}).get("timeframe"),
        "timezones": ["UTC", "Pakistan", "EST", "EDT", "CET", "CEST", "BST",
                      "New York", "London", "Tokyo", "Dubai"],
        "empty_list_means": "no restriction from that group; the validator rejects an "
                           "empty list that was produced by deselection (Select All instead)",
        "window_semantics": ("start < end = a window inside one day; start > end = an overnight "
                             "window that crosses midnight (e.g. 17:00–08:00); start == end is "
                             "rejected as impossible"),
    }
