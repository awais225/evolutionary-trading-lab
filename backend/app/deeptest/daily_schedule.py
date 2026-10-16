"""Opt-in28-cell schedule. Old records retain their original rule version.
Sessions preserve live SESSION_HOURS_UTC; overlap is a distinct exclusive bucket.
Timezone selects weekday; UTC session windows never silently shift with DST.
"""
import datetime as dt
from zoneinfo import ZoneInfo
DAYS=('mon','tue','wed','thu','fri','sat','sun')
MAJORS=('asia','london','newyork','overlaps')
BOUNDARIES='legacy-live-utc-v1'

def normalize(raw):
    if not isinstance(raw,dict) or raw.get('version',1)!=1:raise ValueError('invalid daily schedule version')
    if raw.get('boundaries',BOUNDARIES)!=BOUNDARIES:raise ValueError('unknown session boundary convention')
    zone=str(raw.get('timezone','UTC'));ZoneInfo(zone)
    cells=raw.get('days')
    if not isinstance(cells,dict) or set(cells)!=set(DAYS):raise ValueError('daily schedule requires all seven days')
    out={}
    for d in DAYS:
        if not isinstance(cells[d],dict) or set(cells[d])!=set(MAJORS) or any(type(v) is not bool for v in cells[d].values()):raise ValueError('daily schedule requires28 boolean cells')
        out[d]={s:cells[d][s] for s in MAJORS}
    def state(values):return True if all(values) else False if not any(values) else None
    return {'version':1,'boundaries':BOUNDARIES,'timezone':zone,'days':out,
            'global_sessions':{s:state([out[d][s] for d in DAYS]) for s in MAJORS},
            'day_masters':{d:state(list(out[d].values())) for d in DAYS}}

def category(ts):
    from ..live_testing.schedule import SESSION_HOURS_UTC
    t=dt.datetime.fromtimestamp(float(ts),dt.timezone.utc);hour=t.hour+t.minute/60+t.second/3600
    present=[s for s in MAJORS[:3] if SESSION_HOURS_UTC[s][0]<=hour<SESSION_HOURS_UTC[s][1]]
    return 'overlaps' if len(present)>1 else present[0] if present else None

def allows(ts,raw):
    day=DAYS[dt.datetime.fromtimestamp(float(ts),ZoneInfo(raw['timezone'])).weekday()];session=category(ts)
    return bool(session and raw['days'][day][session])

def defaults():return normalize({'days':{d:{s:d in DAYS[:5] for s in MAJORS} for d in DAYS}})
