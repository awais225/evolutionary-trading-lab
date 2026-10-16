import React,{useEffect,useState} from "react";
import {api} from '../api.js';
const terminal=new Set(['COMPLETE','PARTIAL','FAILED','CANCELLED']);
const date=ts=>ts==null?'N/A':new Date(ts*1000).toISOString();
export default function HistoryLatest(){
 const [state,setState]=useState(null),[error,setError]=useState(null),[scope,setScope]=useState('ALL'),[busy,setBusy]=useState(false);
 const load=()=>api.historyLatest().then(setState).catch(e=>setError(e.message));
 useEffect(()=>{let active=true;api.historyLatest().then(s=>active&&setState(s)).catch(e=>active&&setError(e.message));return()=>{active=false;};},[]);
 const job=state?.job,running=job&&!terminal.has(job.status);
 useEffect(()=>{if(!running)return;const t=setInterval(load,1500);return()=>clearInterval(t);},[running]);
 async function start(){setBusy(true);setError(null);try{const s=await api.historyLatestStart(scope==='ALL'?undefined:[scope]);if(!s.ok)throw new Error(s.error);setState(v=>({...v,job:s.job}));await load();}catch(e){setError(e.message);}finally{setBusy(false);}}
 return <section className="panel" style={{padding:12,marginBottom:12}} aria-label="Shared persistent history">
  <div style={{display:'flex',gap:10,alignItems:'center',flexWrap:'wrap'}}><button disabled={busy||running} onClick={start}>FETCH LATEST MT5 DATA</button>
   <select aria-label="History update scope" value={scope} onChange={e=>setScope(e.target.value)} disabled={running}><option value="ALL">All seven XAUUSD timeframes</option>{['M1','M5','M15','M30','H1','H4','D1'].map(tf=><option key={tf}>{tf}</option>)}</select>
   {running&&<button onClick={async()=>{await api.historyLatestCancel();await load();}}>Cancel history fetch</button>}
   <span>{job?`${job.status} — ${job.progress_pct??0}% — ${job.operation||job.error||''}`:'Persisted baseline ready; explicit fetch only.'}</span>
  </div>
  <small>Shared Overview / Deep store. Incremental UTC closed bars, two-bar correction overlap. Saves locally; Git synchronization is manual. Never starts generation or live trading.</small>
  {error&&<p role="alert">{error}</p>}{job?.error&&<p role="alert">{job.error}</p>}
  <details><summary>Stored history, actual coverage and latest result</summary><table className="table"><thead><tr><th>TF</th><th>Unique bars</th><th>UTC first → last</th><th>Source timezone / provenance</th></tr></thead><tbody>{(state?.inventory||[]).map(r=><tr key={r.timeframe}><td>{r.timeframe}</td><td>{r.unique_bars}</td><td>{date(r.first_ts)} → {date(r.last_ts)}</td><td>{r.timezone} / {r.source}</td></tr>)}</tbody></table><pre style={{maxHeight:220,overflow:'auto',fontSize:11}}>{JSON.stringify(job?.results||[],null,2)}</pre></details>
 </section>;
}
