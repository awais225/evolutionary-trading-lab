"""Metric-preserving weekday→session and unbounded ISO/calendar-week analytics.
Each aggregation uses the existing summarize() on its own unique trades. Session
memberships may overlap; totals are recalculated from the union, never added.
"""
import datetime as dt
from .filter_rules import normalize,reason,context,DAYS,SESSIONS
from .analytics import summarize,eligible_trades

def aggregate(trades,initial_capital,rules=None,start=None,end=None):
    r=normalize(rules);valid=eligible_trades(trades)
    contexts={id(t):context(t) for t in valid}
    def ctx(t):return contexts[id(t)]
    kept=[t for t in valid if reason(t,r) is None]
    def stats(rows):return {'state':'OBSERVED' if rows else 'NO_TRADES','metrics':summarize(rows,initial_capital=initial_capital)['summary']}
    dayrows=[]
    for d in DAYS:
        rows=[t for t in kept if ctx(t)[1]==d]
        dayrows.append({'day':d,**stats(rows),'sessions':[{'session':s,**stats([t for t in rows if s in ctx(t)[2]])} for s in SESSIONS]})
    known=[ctx(t)[0] for t in valid if ctx(t)[0] is not None];weeks=[]
    a=start if start is not None else min(known) if known else None
    b=end if end is not None else max(known)+1 if known else None
    if a is not None and b is not None and b>a:
        cursor=dt.datetime.fromtimestamp(a,dt.timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)
        cursor-=dt.timedelta(days=cursor.weekday())
        while cursor.timestamp()<b:
            finish=cursor+dt.timedelta(days=7);lo=max(a,cursor.timestamp());hi=min(b,finish.timestamp())
            rows=[t for t in kept if ctx(t)[0] is not None and lo<=ctx(t)[0]<hi]
            iso=cursor.isocalendar()
            weeks.append({'iso_week':f'{iso.year}-W{iso.week:02d}','start_utc':lo,'end_utc_exclusive':hi,'calendar_start':cursor.date().isoformat(),'calendar_end':(finish-dt.timedelta(days=1)).date().isoformat(),**stats(rows)})
            cursor=finish
    return {'schema_version':1,'timezone':'UTC','rules':r,'total':stats(kept),'weekdays':dayrows,
      'sessions':[{'session':s,**stats([t for t in kept if s in ctx(t)[2]])} for s in SESSIONS],
      'weeks':weeks,'original_trades':len(valid),'kept_trades':len(kept),'excluded_trades':len(valid)-len(kept),
      'missing_entry_time':sum(ctx(t)[0] is None for t in valid),
      'disclosure':'Post-trade analysis, not a rerun. UTC entry day/session; realized equity ordered by exit. No-trade is not missing history. Overlap-session totals must not be summed; aggregate uses the unique union.'}
