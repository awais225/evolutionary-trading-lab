import React,{useEffect,useRef} from "react";
export const DAYS=['mon','tue','wed','thu','fri','sat','sun'];
export const MAJORS=['asia','london','newyork','overlaps'];
const state=values=>values.every(Boolean)?true:values.some(Boolean)?null:false;
export function scheduleModel(days,timezone='UTC'){
 return {version:1,boundaries:'legacy-live-utc-v1',timezone,days,
  global_sessions:Object.fromEntries(MAJORS.map(s=>[s,state(DAYS.map(d=>days[d][s]))])),
  day_masters:Object.fromEntries(DAYS.map(d=>[d,state(MAJORS.map(s=>days[d][s]))]))};
}
export const defaultSchedule=()=>scheduleModel(Object.fromEntries(DAYS.map(d=>[d,Object.fromEntries(MAJORS.map(s=>[s,!['sat','sun'].includes(d)]))])));
function Check({value,onChange,label,disabled}){
 const ref=useRef();useEffect(()=>{if(ref.current)ref.current.indeterminate=value===null;},[value]);
 return <input ref={ref} type="checkbox" aria-label={label} aria-checked={value===null?'mixed':value} checked={value===true} disabled={disabled} onChange={e=>onChange(e.target.checked)}/>;
}
export default function DailySchedule({value,onChange,disabled=false}){
 const model=value||defaultSchedule();
 function change(day,session,checked){const days=Object.fromEntries(DAYS.map(d=>[d,{...model.days[d]}]));for(const d of day?[day]:DAYS)for(const s of session?[session]:MAJORS)days[d][s]=checked;onChange(scheduleModel(days,model.timezone));}
 const derived=scheduleModel(model.days,model.timezone);
 return <fieldset disabled={disabled} style={{border:'1px solid var(--border)',borderRadius:8,padding:12}}><legend>Daily session schedule · 28 independent cells</legend>
 <label>Weekday timezone <input aria-label="Schedule timezone" value={model.timezone} onChange={e=>onChange(scheduleModel(model.days,e.target.value))} placeholder="UTC or IANA timezone"/></label>
 <div style={{overflowX:'auto'}}><table className="table compact"><thead><tr><th>Day master</th>{MAJORS.map(s=><th key={s}><label><Check label={`Global ${s}`} value={derived.global_sessions[s]} disabled={disabled} onChange={v=>change(null,s,v)}/>{s.toUpperCase()}</label></th>)}</tr></thead>
 <tbody>{DAYS.map(d=><tr key={d}><th><label><Check label={`All sessions ${d}`} value={derived.day_masters[d]} onChange={v=>change(d,null,v)} disabled={disabled}/>{d.toUpperCase()}</label></th>{MAJORS.map(s=><td key={s}><Check label={`${d} ${s}`} value={model.days[d][s]} onChange={v=>change(d,s,v)} disabled={disabled}/></td>)}</tr>)}</tbody></table></div>
 <small>UTC session boundaries: ASIA 00–08, LONDON 07–16, NEWYORK 12–21. OVERLAPS exclusively covers 07–08 and 12–16; other columns exclude those hours. 21–24 is outside this opted-in matrix. The timezone changes weekdays per timestamp (including DST), not these legacy UTC windows. Normalized candle timestamps are UTC; no second broker-time conversion. Other schedule, Inspect, risk and enable gates remain in force.</small>
 </fieldset>;
}
