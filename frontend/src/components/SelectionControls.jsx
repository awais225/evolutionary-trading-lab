import React,{useEffect,useRef,useState} from "react";
import {api} from '../api.js';
import {useStoredState} from '../lib/storedState.js';
export function SelectionControls({rows,selected,setSelected,eligible=()=>true}){
 const ids=[...new Set(rows.filter(eligible).map(r=>r.node_id))],count=ids.filter(id=>selected[id]).length;
 const allSelected=Object.values(selected).filter(Boolean).length;
 return <div className="row-bar" aria-label="Full filtered population selection"><button disabled={!ids.length} onClick={()=>setSelected(old=>({...old,...Object.fromEntries(ids.map(id=>[id,true]))}))}>SELECT ALL</button><button disabled={!allSelected} onClick={()=>setSelected({})}>DESELECT ALL</button><span>{count} selected / {ids.length} eligible in current full filtered set (all pages). {allSelected-count} retained outside this scope; not included in this action.</span></div>;
}
export function DeepBulk({nodes,selected,duration,settings,nodeConfig,onUpdated}){
 const [ident,setIdent]=useStoredState('deep.v93.batch',null),[job,setJob]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[retry,setRetry]=useState(false),[savedBatches,setSavedBatches]=useState([]);
 const epoch=useRef(0),lastStatus=useRef(null);
 const ids=[...new Set(nodes.filter(n=>selected[n.node_id]).map(n=>n.node_id))];
 async function load(){if(!ident)return;const seq=++epoch.current;try{const r=await api.deepBatchGet(ident);if(seq!==epoch.current)return;setJob(r.batch);if(lastStatus.current!==r.batch.status){lastStatus.current=r.batch.status;if(!['QUEUED','RUNNING'].includes(r.batch.status))onUpdated?.();}}catch(e){if(seq===epoch.current)setError(e.message);}}
 useEffect(()=>{load();const timer=setInterval(load,1800);return()=>{clearInterval(timer);epoch.current++;};},[ident]);
 async function start(){setBusy(true);setError('');try{const r=await api.deepBatchCreate({node_ids:ids,duration_key:duration,settings,node_settings_by_id:Object.fromEntries(ids.map(id=>[id,nodeConfig(id)])),retry_incomplete:retry});setIdent(r.batch.id);setJob(r.batch);}catch(e){setError(e.message);}finally{setBusy(false);}}
 const running=job&&['QUEUED','RUNNING'].includes(job.status);
 return <section className="panel" style={{padding:10,margin:'8px 0'}} aria-label="Historical batch"><button className="btn primary" disabled={busy||running||!ids.length||ids.length>5000} onClick={start}>DEEPTEST selected ({ids.length})</button> <label><input type="checkbox" checked={retry} onChange={e=>setRetry(e.target.checked)}/>Explicitly retry failed/interrupted matching tests</label>
 <button onClick={async()=>{try{const r=await api.deepBatches();setSavedBatches(r.batches||[]);}catch(e){setError(e.message);}}}>Find saved batches</button>{savedBatches.map(b=><button key={b.id} onClick={()=>setIdent(b.id)}>Inspect {b.id} · {b.status}</button>)}
 <small style={{display:'block'}}>Historical simulation only; one node observed at a time. Valid identical scenarios are reused. Unconfirmed partial history is skipped, not silently accepted: use the node’s DEEPTEST coverage confirmation, then submit the selection again. No live workers or orders.</small>
 {error&&<p role="alert">{error}</p>}{job&&<><p role="status">{job.id} · {job.status} · {job.finished}/{job.total} finished</p><progress aria-label="Deep batch progress" value={job.finished} max={job.total||1}/>{running&&<button onClick={async()=>{await api.deepBatchAction(ident,'cancel');await load();}}>Cancel remaining queue</button>}{['INTERRUPTED','CANCELLED'].includes(job.status)&&<button onClick={async()=>{await api.deepBatchAction(ident,'resume');await load();}}>Explicitly resume incomplete queue</button>}<details><summary>Per-node results (including skips, failures and incomplete runs)</summary>{job.results.map((r,i)=><p key={r.node_id??i}>Node #{r.node_id??'batch'} · {r.status}{r.reused?' · reused existing scenario':''} · {r.run_id||''} {r.reason||''}</p>)}</details></>}
 </section>;
}
export function LiveSelectionReview({rows,selected}){
 const sequence=useRef(0);
 const [review,setReview]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const ids=[...new Set(rows.filter(r=>selected[r.node_id]).map(r=>r.node_id))];
 useEffect(()=>{sequence.current++;setReview(null);return()=>{sequence.current++;};},[JSON.stringify(ids)]);
 return <section style={{margin:'8px 0'}}><button disabled={busy||!ids.length} onClick={async()=>{setBusy(true);setError('');try{const seq=++sequence.current;const result=await api.liveSelectionValidate(ids);if(seq===sequence.current)setReview(result);}catch(e){setError(e.message);}finally{setBusy(false);}}}>LIVE TEST SELECTED ({ids.length}) — REVIEW ONLY</button><small style={{display:'block'}}>Selection never starts trading. Review each node, then use its existing explicit START; all broker, schedule, risk and trade-limit checks still apply.</small>{error&&<p role="alert">{error}</p>}{review&&<details open><summary>{review.selected} reviewed · {review.started} started · {review.orders_submitted} orders submitted</summary>{review.nodes.map(r=><div key={r.node_id}>Node #{r.node_id}: {r.eligible?'Eligible for explicit START':r.reason} · risk {r.config?.risk_pct??'Default'}% · max active {r.config?.max_active_trades??'Default'} · {r.instruction}<details><summary>Backend-confirmed configuration and assigned schedule</summary><pre style={{whiteSpace:"pre-wrap"}}>{JSON.stringify({config:r.config,assigned_schedule:r.assigned_schedule},null,2)}</pre></details></div>)}</details>}</section>;
}
