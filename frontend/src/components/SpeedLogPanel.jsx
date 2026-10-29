import React,{useEffect,useLayoutEffect,useMemo,useRef,useState} from 'react';
import {speedRequest,copyAllSnapshot,downloadSnapshot} from './speedLog.js';

export default function SpeedLogPanel(){
 const [open,setOpen]=useState(false),[data,setData]=useState(null),[events,setEvents]=useState([]),[paused,setPaused]=useState(false);
 const [severity,setSeverity]=useState(''),[scenario,setScenario]=useState(''),[search,setSearch]=useState(''),[subsystem,setSubsystem]=useState(''),[kind,setKind]=useState(''),[from,setFrom]=useState(''),[to,setTo]=useState(''),[page,setPage]=useState(0);
 const [error,setError]=useState(''),[feedback,setFeedback]=useState(''),[busy,setBusy]=useState(false),[displayDropped,setDisplayDropped]=useState(0);
 const rowsRef=useRef([]),clearBefore=useRef(0),cursor=useRef(0),flight=useRef(false),live=useRef(true),pausedRef=useRef(false),renderStart=useRef(performance.now()),renderMs=useRef(null),lastFull=useRef(null);
 renderStart.current=performance.now();pausedRef.current=paused;
 useLayoutEffect(()=>{renderMs.current=Math.max(0,performance.now()-renderStart.current);});
 useEffect(()=>{live.current=true;return()=>{live.current=false;};},[]);
 useEffect(()=>{
  if(!open)return;
  let stopped=false,timer,ticks=0;
  const poll=async()=>{
   if(!flight.current&&!pausedRef.current&&!document.hidden){
    flight.current=true;const begin=performance.now();
    try{
     const next=await speedRequest(`events?after=${cursor.current}&limit=250`);
     if(stopped)return;
     if(!pausedRef.current){
     cursor.current=Math.max(cursor.current,next.next_seq);setData(next);setError('');
     const all=[...rowsRef.current,...next.events.filter(e=>e.seq>clearBefore.current)];const dropped=Math.max(0,all.length-1000);
     rowsRef.current=all.slice(-1000);setEvents(rowsRef.current);if(dropped)setDisplayDropped(n=>n+dropped);
     if(++ticks%10===0)await speedRequest('frontend',{request_ms:performance.now()-begin,render_ms:renderMs.current,rows:next.events.length});
     }
    }catch(e){if(!stopped)setError(e.message||String(e));}
    finally{flight.current=false;}
   }
   if(!stopped)timer=setTimeout(poll,2000);
  };
  poll();return()=>{stopped=true;clearTimeout(timer);};
 },[open]);
 const filtered=useMemo(()=>events.filter(e=>(!severity||e.severity===severity)&&(!scenario||(e.run_id||'').includes(scenario))&&(!subsystem||e.subsystem===subsystem)&&(!kind||e.type===kind)&&(!from||e.timestamp>=Date.parse(from)/1000)&&(!to||e.timestamp<=Date.parse(to)/1000)&&(!search||JSON.stringify(e).toLowerCase().includes(search.toLowerCase()))),[events,search,subsystem,kind,from,to,severity,scenario]);
 useEffect(()=>setPage(p=>Math.min(p,Math.max(0,Math.ceil(filtered.length/50)-1))),[filtered.length]);
 const change=setter=>e=>{setter(e.target.value);setPage(0);};
 const configure=async profile=>{setBusy(true);setError('');try{const next=await speedRequest('recording',{profile,seconds:60});if(live.current)setData(next);}catch(e){if(live.current)setError(e.message);}finally{if(live.current)setBusy(false);}};
 const copy=async()=>{setBusy(true);setFeedback('');try{const result=await copyAllSnapshot();lastFull.current=result.text;if(live.current)setFeedback(result.status);}catch(e){if(live.current)setError('Copy/export failed. No incomplete success claimed. '+e.message);}finally{if(live.current)setBusy(false);}};
 const download=async()=>{setBusy(true);try{const snapshot=await speedRequest('snapshot');const text=JSON.stringify(snapshot,null,2);lastFull.current=text;downloadSnapshot(text);if(live.current)setFeedback('Complete retained snapshot exported, independent of filters.');}catch(e){if(live.current)setError('Full export failed: '+e.message);}finally{if(live.current)setBusy(false);}};
 return <section className="panel" aria-label="SPEED LOG" style={{margin:'8px 10px',padding:10,fontSize:12}}>
  <div style={{display:'flex',justifyContent:'space-between',gap:6}}><button className="btn btn-sm" aria-expanded={open} onClick={()=>setOpen(!open)}>SPEED LOG {open?'▾':'▸'}</button><button className="btn primary" disabled={busy} onClick={copy}>COPY ALL</button></div>
  <p role="status" style={{margin:'6px 0'}}>Recording: {data?.recording?.active?data.recording.profile.toUpperCase():data?'OFF':'status not loaded'}{paused?' · DISPLAY PAUSED — backend measurement continues':''}</p>
  {error&&<p role="alert" className="kit-inline-err">{error}</p>}{feedback&&<p role="status">{feedback}</p>}
  {open&&<>
   <div className="btn-row"><button className="btn btn-sm" disabled={busy} onClick={()=>configure('light')}>Record light</button><button className="btn btn-sm" disabled={busy} onClick={()=>configure('detailed')}>Detailed 60s</button><button className="btn btn-sm" disabled={busy} onClick={()=>configure('off')}>Recording OFF</button><button className="btn btn-sm" onClick={()=>setPaused(!paused)}>{paused?'Resume display':'Pause display'}</button><button className="btn btn-sm" onClick={()=>{clearBefore.current=Math.max(cursor.current,data?.retention?.latest_seq||0);rowsRef.current=[];setEvents([]);setPage(0);setFeedback('Visible entries cleared only. COPY ALL still includes all backend-retained diagnostics.');}}>Clear display</button><button className="btn btn-sm" disabled={busy} onClick={download}>Export ALL JSON</button></div>
   <p className="muted">Light samples hot stages; detailed records all stage calls for 60 seconds, then returns to light. Neither changes worker counts. COPY ALL ignores display search, filters, clearing and pages.</p>
   <label>Severity <select aria-label="Speed severity" value={severity} onChange={change(setSeverity)}><option value="">All</option>{['INFO','WARN','ERROR'].map(s=><option key={s}>{s}</option>)}</select></label>
   <label>Scenario/run <input aria-label="Speed scenario" value={scenario} onChange={change(setScenario)}/></label>
   <label>Search <input aria-label="Speed search" value={search} onChange={change(setSearch)} style={{width:'95%'}}/></label>
   <div style={{display:'flex',gap:5,flexWrap:'wrap'}}><label>Subsystem <select aria-label="Speed subsystem" value={subsystem} onChange={change(setSubsystem)}><option value="">All</option>{[...new Set(events.map(e=>e.subsystem))].sort().map(v=><option key={v}>{v}</option>)}</select></label><label>Event type <select aria-label="Speed event type" value={kind} onChange={change(setKind)}><option value="">All</option>{[...new Set(events.map(e=>e.type))].sort().map(v=><option key={v}>{v}</option>)}</select></label></div>
   <label>From (local) <input aria-label="Speed from" type="datetime-local" value={from} onChange={change(setFrom)}/></label><label>To (local) <input aria-label="Speed to" type="datetime-local" value={to} onChange={change(setTo)}/></label>
   <p className="muted">Backend retained {data?.retention?.retained_events??'N/A'}; dropped {data?.retention?.dropped_events??'N/A'}. Display retains last 1000; dropped here {displayDropped}. Aggregates cover the session, not just retained events.</p>
   <details><summary>Measured bottleneck summary (not a diagnosis)</summary>{(data?.bottlenecks||[]).map((b,i)=><p key={i}><b>{b.classification}</b> {b.stage} {b.measured_self_seconds!=null?`${b.measured_self_seconds.toFixed(4)}s self · ${b.calls} measured calls · PID ${b.pid}`:b.note}</p>)}<p>Do not sum nested inclusive time or overlapping processes. CPU-low is not proof of a bottleneck. Busy GPU device does not prove app GPU compute.</p></details>
   <div style={{maxHeight:290,overflow:'auto'}}>{filtered.slice(page*50,page*50+50).map(e=><div key={e.seq} style={{borderBottom:'1px solid var(--border)',padding:'5px 0'}}><b>{e.severity} · {e.subsystem} · {e.type}</b><div className="muted">{new Date(e.timestamp*1000).toLocaleTimeString()} · +{e.elapsed_seconds.toFixed(3)}s · PID {e.pid} · {e.run_id||'no run context'}</div><pre style={{whiteSpace:'pre-wrap',fontSize:10,margin:0}}>{JSON.stringify(e.fields)}</pre></div>)}{!filtered.length&&<p>No retained events match the display filters.</p>}</div>
   <div className="btn-row"><button className="btn btn-sm" disabled={page===0} onClick={()=>setPage(page-1)}>Previous</button><span>{page+1}/{Math.max(1,Math.ceil(filtered.length/50))} · {filtered.length} matching</span><button className="btn btn-sm" disabled={(page+1)*50>=filtered.length} onClick={()=>setPage(page+1)}>Next</button></div>
  </>}
 </section>;
}
