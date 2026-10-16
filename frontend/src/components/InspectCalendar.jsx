import React from "react";
import {SaveFilterScenario} from './FilterScenarios.jsx';
const SESSIONS=['asia','london','newyork','off','asia_london_overlap','london_newyork_overlap'];
function Metrics({r}){const m=r?.metrics||{};return <span className="mono">{r?.state||'MISSING'} · trades {m.trades??'N/A'} · net {m.net_profit??'N/A'} · return {m.net_return_pct==null?'N/A':(m.net_return_pct*100).toFixed(3)+'%'} · PF {m.profit_factor??'N/A'} · DD {m.max_drawdown_pct==null?'N/A':(m.max_drawdown_pct*100).toFixed(3)+'%'} · win {m.win_rate==null?'N/A':(m.win_rate*100).toFixed(2)+'%'} · Sharpe {m.sharpe??'N/A'}</span>;}
export default function InspectCalendar({calendar,nodeId,runId,byDay={},onByDay}){
 if(!calendar)return null;
 return <section className="panel" style={{padding:12,marginTop:12}}><h3>Weekday → session breakdown and ISO/calendar weeks</h3><p>{calendar.disclosure}</p>
 <b>Unique aggregate across selected range:</b> <Metrics r={calendar.total}/>
 <div style={{margin:'10px 0'}}><SaveFilterScenario nodeId={nodeId} runId={runId} rules={calendar.rules}/></div>
 {calendar.weekdays.map(d=><details key={d.day}><summary><b>{d.day.toUpperCase()}</b> — <Metrics r={d}/></summary><div style={{marginLeft:20}}>{d.sessions.map(s=><div key={s.session}><label><input type="checkbox" aria-label={`Exclude ${d.day} ${s.session}`} checked={(byDay[d.day]||[]).includes(s.session)} onChange={e=>onByDay({...byDay,[d.day]:e.target.checked?[...(byDay[d.day]||[]),s.session]:(byDay[d.day]||[]).filter(x=>x!==s.session)})}/> Exclude {s.session} for {d.day} only</label> — <Metrics r={s}/></div>)}</div></details>)}
 <details><summary>Aggregate sessions across all selected weekdays (overlaps not additive)</summary>{calendar.sessions.map(s=><div key={s.session}>{s.session}: <Metrics r={s}/></div>)}</details>
 <h4>Calendar weeks — no four-week cap</h4>{calendar.weeks.map(w=><div key={w.iso_week}><b>{w.iso_week}</b> {w.calendar_start} → {w.calendar_end} (UTC): <Metrics r={w}/></div>)}
 <small>Missing entry timestamps: {calendar.missing_entry_time}. NO_TRADES means zero observed entries, not verified complete candle history.</small></section>;
}
