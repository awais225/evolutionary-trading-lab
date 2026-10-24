// Opt-in REAL HTTP/component integration. Never generates nodes or candles.
// The caller MUST configure an isolated SQLite-backup API and supply 1-2 IDs.
import React,{act,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {api} from '../src/api.js';
import {DeepBulk,SelectionControls} from '../src/components/SelectionControls.jsx';

export async function runRealData(ids,trace,observationSeconds=300){
 if(!ids.length||ids.length>2||new Set(ids).size!==ids.length||ids.some(id=>!Number.isInteger(id)||id<=0))throw Error('Exactly one or two explicit existing qualified IDs required');
 const inventory=await api.nodes({filter:'qualified',limit:0});
 const nodes=(inventory.nodes||[]).filter(n=>ids.includes(n.node_id));
 if(nodes.length!==ids.length)throw Error('Requested IDs are not all in the real qualified inventory');
 const {settings}=await api.deeptestSettings();
 function Harness(){const [selected,setSelected]=useState({});return <><SelectionControls rows={nodes} selected={selected} setSelected={setSelected}/><DeepBulk nodes={nodes} selected={selected} duration="3M" settings={settings} nodeConfig={()=>({})}/></>;}
 localStorage.clear();const element=document.createElement('div');document.body.append(element);const root=createRoot(element);
 const button=text=>[...element.querySelectorAll('button')].find(b=>b.textContent.includes(text));
 const click=async b=>{if(!b||b.disabled)throw Error('Missing/disabled action');await act(async()=>{b.dispatchEvent(new MouseEvent('click',{bubbles:true}));await new Promise(r=>setTimeout(r,50));});};
 try{
  await act(async()=>root.render(<Harness/>));
  await click(button('SELECT ALL'));
  await click(button('DEEPTEST selected'));
  const end=Date.now()+observationSeconds*1000;
  while(Date.now()<end){
   if(element.querySelector('[role="dialog"]'))throw Error('Explicit action incorrectly blocked by coverage popup');
   const last=trace.latest;
   if(last&&!['QUEUED','RUNNING','PREFLIGHTING','AWAITING_COVERAGE_DECISION'].includes(last.status)){
    trace.visibleStatus=element.querySelector('[role="status"]')?.textContent;
    trace.visibleText=element.textContent;
    if(!trace.created?.auto_partial||trace.created.node_ids.join(',')!==ids.join(','))throw Error('Actual frontend POST selection/policy mismatch');
    if(trace.creates!==1)throw Error('Duplicate submission');
    if(!last.results?.length||last.results.some(r=>!['COMPLETED','PARTIAL','REUSED'].includes(r.status)))throw Error('Genuine engine/persistence failure: '+JSON.stringify(last.results));
    return {ok:true,ids,qualifiedInventoryCount:inventory.nodes.length,batch:last,visibleStatus:trace.visibleStatus,creates:trace.creates};
   }
   await act(async()=>await new Promise(r=>setTimeout(r,200)));
  }
  throw Error('Observation ended without terminal result; job NOT labelled successful or automatically cancelled');
 }finally{await act(async()=>root.unmount());element.remove();}
}
