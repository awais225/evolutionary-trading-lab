import React,{useEffect,useState} from "react";
import {api} from '../api.js';
const DAYS=['mon','tue','wed','thu','fri','sat','sun'],SESSIONS=['asia','london','newyork','off','asia_london_overlap','london_newyork_overlap'];
export function SaveFilterScenario({nodeId,rules,runId}){
 const [open,setOpen]=useState(false),[name,setName]=useState(''),[scope,setScope]=useState('NODE'),[draft,setDraft]=useState(rules),[message,setMessage]=useState(''),[busy,setBusy]=useState(false);
 async function save(){setBusy(true);try{const r=await api.filterScenarioSave({name,scope,node_id:nodeId,rules:draft,provenance:{run_id:runId,analysis:'saved-trade entry filters; not a fresh strategy rerun'}});setMessage(`Saved ${r.scenario.name}. Assign explicitly through SCHEDULE; nothing was enabled.`);setOpen(false);}catch(e){setMessage(e.message);}finally{setBusy(false);}}
 const toggle=(key,v,on)=>setDraft(d=>({...d,[key]:on?[...(d[key]||[]),v]:(d[key]||[]).filter(x=>x!==v)}));
 return <><button onClick={()=>{setDraft(rules);setOpen(true);setMessage('');}}>SAVE CURRENT FILTERS AS SCENARIO</button>{message&&<p role="status">{message}</p>}
 {open&&<div role="dialog" aria-modal="true" aria-label="Save current filters as scenario" style={{position:'fixed',inset:0,zIndex:150,background:'#000b',display:'grid',placeItems:'center'}}><section className="panel" style={{padding:20,width:650,maxWidth:'90vw',maxHeight:'85vh',overflow:'auto'}}>
 <h3>Save current filters as scenario</h3><label>Name <input aria-label="Filter scenario name" value={name} maxLength={120} onChange={e=>setName(e.target.value)}/></label>
 <label> Scope <select value={scope} onChange={e=>setScope(e.target.value)}><option value="NODE">This canonical node only</option><option value="GLOBAL">Globally reusable</option></select></label>
 <h4>Current exclusion checklist · actual UTC entry time</h4><div>{DAYS.map(d=><label key={d} style={{marginRight:8}}><input type="checkbox" checked={draft?.exclude_days?.includes(d)||false} onChange={e=>toggle('exclude_days',d,e.target.checked)}/>{d}</label>)}</div>
 <div>{SESSIONS.map(s=><label key={s} style={{display:'inline-block',marginRight:8}}><input type="checkbox" checked={draft?.exclude_sessions?.includes(s)||false} onChange={e=>toggle('exclude_sessions',s,e.target.checked)}/>{s}</label>)}</div>
 <p>Weekday-specific session rules and range restrictions are included below. Future Live entry gating uses the same predicates; post-trade metrics are not a fresh backtest.</p><pre style={{whiteSpace:'pre-wrap',fontSize:11}}>{JSON.stringify(draft,null,2)}</pre>
 <button disabled={busy||!name.trim()} onClick={save}>{busy?'Saving…':'Save scenario'}</button> <button disabled={busy} onClick={()=>setOpen(false)}>Cancel</button>
 </section></div>}</>;
}
export function ScenarioAssignment({nodeId,target='live'}){
 const [rows,setRows]=useState([]),[choice,setChoice]=useState(''),[assigned,setAssigned]=useState(null),[msg,setMsg]=useState(''),[busy,setBusy]=useState(false);
 useEffect(()=>{let active=true;Promise.all([api.filterScenarios(nodeId),api.filterScenarioAssignment(nodeId,target)]).then(([r,a])=>{if(active){setRows(Array.isArray(r.scenarios)?r.scenarios:[]);setAssigned(a.assignment);setChoice(a.assignment?.id||'');}}).catch(e=>active&&setMsg(e.message));return()=>{active=false;};},[nodeId,target]);
 async function assign(id){setBusy(true);try{const r=await api.filterScenarioAssign(nodeId,{target,scenario_id:id||null});setAssigned(r.assignment);setChoice(r.assignment?.id||'');setMsg(r.note);}catch(e){setMsg(e.message);}finally{setBusy(false);}}
 return <section className="panel" style={{padding:12,border:'1px solid var(--amber)',marginBottom:12}}><h3>FILTER SCENARIO · {target.toUpperCase()} entry gate</h3><div>Assigned: <b>{assigned?.name||'None — original schedule applies'}</b></div>
 <select aria-label="Saved filter scenario" value={choice} onChange={e=>setChoice(e.target.value)}><option value="">No filter scenario</option>{rows.map(s=><option value={s.id} key={s.id}>{s.name} ({s.scope})</option>)}</select> <button disabled={busy||!choice} onClick={()=>assign(choice)}>Assign scenario</button> <button disabled={busy||!assigned} onClick={()=>assign(null)}>Remove / restore original schedule</button>
 <small style={{display:'block'}}>Assignments are saved by these explicit buttons. They never start workers, enable Live or change risk. Your existing schedule remains unchanged and resumes on removal.</small>{assigned&&<details><summary>Active structured entry rules (UTC)</summary><pre>{JSON.stringify(assigned.rules,null,2)}</pre></details>}{msg&&<p role="status">{msg}</p>}</section>;
}
