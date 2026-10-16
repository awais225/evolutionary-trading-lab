"""Versioned UTC entry rules shared by saved-trade Inspect and live entry gates.
Independent predicates use set union; each trade is evaluated exactly once.
"""
import datetime as dt,math
from zoneinfo import ZoneInfo
DAYS=('mon','tue','wed','thu','fri','sat','sun')
SESSIONS=('asia','london','newyork','off','asia_london_overlap','london_newyork_overlap')

def normalize(raw=None):
    raw=raw or {}
    if raw.get('schema_version',1)!=1:raise ValueError('unsupported filter rule schema')
    if raw.get('timezone','UTC')!='UTC':raise ValueError('Inspect/scenario entry rules use UTC, not computer local time')
    def values(key,options):
        out=sorted({str(x).lower() for x in raw.get(key,[])})
        if not set(out)<=set(options):raise ValueError('unknown '+key)
        return out
    by={str(d).lower():sorted(set(str(x).lower() for x in ss)) for d,ss in (raw.get('exclude_by_day') or {}).items()}
    if not set(by)<=set(DAYS) or any(not set(ss)<=set(SESSIONS) for ss in by.values()):raise ValueError('invalid weekday session rule')
    out={'schema_version':1,'timezone':'UTC','exclude_days':values('exclude_days',DAYS),'exclude_sessions':values('exclude_sessions',SESSIONS),'include_sessions':values('include_sessions',SESSIONS),'exclude_by_day':by}
    for key in ('range_start','range_end'):
        v=raw.get(key)
        if v is not None:
            v=float(v)
            if not math.isfinite(v):raise ValueError('range must be a finite UTC timestamp')
        out[key]=v
    if out['range_start'] is not None and out['range_end'] is not None and out['range_start']>=out['range_end']:raise ValueError('range start must precede end')
    if raw.get('daily_schedule') is not None:
        from .daily_schedule import normalize as normalize_daily
        out['daily_schedule']=normalize_daily(raw['daily_schedule'])
    return out

def memberships(ts):
    t=dt.datetime.fromtimestamp(float(ts),dt.timezone.utc);out=[]
    for name,zone,a,b in [('asia','Asia/Tokyo',9,15),('london','Europe/London',8,16),('newyork','America/New_York',8,16)]:
        if a<=t.astimezone(ZoneInfo(zone)).hour<b:out.append(name)
    if 'asia' in out and 'london' in out:out.append('asia_london_overlap')
    if 'london' in out and 'newyork' in out:out.append('london_newyork_overlap')
    return out or ['off']

def context(trade):
    try:
        ts=float(trade.get('entry_ts',trade.get('ts')))
        if not math.isfinite(ts):raise ValueError()
        day=DAYS[dt.datetime.fromtimestamp(ts,dt.timezone.utc).weekday()]
        return ts,day,memberships(ts)
    except (TypeError,ValueError,OverflowError,OSError):
        # Legacy saved trades may lack entry_ts. Keep their own stored labels;
        # never derive an identity/time from a different node or current clock.
        d=trade.get('dow');day=DAYS[int(d)] if d is not None and str(d).isdigit() and 0<=int(d)<7 else None
        return None,day,[str(trade.get('session') or 'unknown').lower()]

def reason(trade,rules):
    ts,day,sessions=context(trade);ss=set(sessions)
    if rules.get('daily_schedule'):
        from .daily_schedule import allows as daily_allows
        if ts is None or not daily_allows(ts,rules['daily_schedule']):return 'daily session matrix blocks this entry'
    if day in rules['exclude_days']:return 'day:'+day
    excluded=set(rules['exclude_sessions'])|set(rules['exclude_by_day'].get(day,[]))
    if ss&excluded:return 'session:'+','.join(sorted(ss&excluded))
    if rules['include_sessions'] and not ss&set(rules['include_sessions']):return 'outside selected sessions'
    if rules['range_start'] is not None and (ts is None or ts<rules['range_start']):return 'outside/unknown range start'
    if rules['range_end'] is not None and (ts is None or ts>=rules['range_end']):return 'outside/unknown range end'
    return None

def allows(ts,rules):return reason({'entry_ts':ts},rules) is None
