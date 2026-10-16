import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { api } from "../src/api.js";
import NodeTable, { compareNodes, CandidateStar } from "../src/components/NodeTable.jsx";
import DeepBacktest from "../src/pages/DeepBacktest.jsx";
import Overview from "../src/pages/Overview.jsx";
import { LiveNodeTable } from "../src/components/LiveNodeIndex.jsx";
import { LabContext } from "../src/App.jsx";
const calls = [];
const stars = new Set();
const record = (id, over={}) => ({ node_id:id, strategy_id:id, run_id:"SAVED-RUN", research_node_num:id===718?318:718,
  node_label:`Node #${id}`, qualification:"QUALIFIED", bucket:"qualified", generation:2,
  symbol:"XAUUSD", timeframe:"M15", fitness:2, starred:stars.has(id),
  generation_metrics:{ return_pct:id===718?1.481:.0917, profit_factor:2, trades:57 },
  metrics:{ return_pct:.0917, profit_factor:2, trades:57 },
  risk:{pct:1}, schedule:{}, worker:{state:"STOPPED"}, lifecycle:"STOPPED", ...over });
export function payloadFor(url, method="GET", body) {
  calls.push({url,method,body});
  if (url.includes("/api/research/shortlist/") && method==="PUT") {
    const id=Number(url.split("/").pop()); const desired=JSON.parse(body).starred;
    if(desired) stars.add(id); else stars.delete(id);
    return {ok:true,strategy_id:id,starred:desired};
  }
  if(url.includes("/api/node-data")) return {ok:true,node_data:{counts:{qualified:2,total:3},tops:{},revision:"fixture"}};
  if(url.includes("/api/nodes/populations")) return {ok:true,counts:{qualified:2,total:3},state:{},definitions:{}};
  if(url.includes("/api/nodes?")) {
    const u=new URL(url,"http://test"),filter=u.searchParams.get("filter");
    let rows=[record(1505),record(1506,{research_node_num:null})];
    if(filter==="all") rows.push(record(718,{qualification:"SURVIVED"}));
    if(u.searchParams.get("starred_only")==="true") rows=[...stars].map((id)=>record(id));
    return {ok:true,nodes:rows,total:rows.length,filter_totals:{qualified:2,all:3},counts:{qualified:2},experiment:{}};
  }
  if(url.includes("/api/deeptest/nodes")) return {nodes:[]};
  if(url.includes("/api/deeptest/coverage/pairs")) return {coverage:[]};
  if(url.includes("/api/deeptest/settings")) return {settings:{total_capital:200000,risk_pct:1,max_active_trades_per_node:0}};
  if(url.includes("/api/resources")) return {};
  if(url.includes("/api/research/shortlist/saved")) return {shortlist:[...stars]};
  return {};
}
const wrap=(x)=><LabContext.Provider value={{status:{lab:{},counts:{}},events:[],connected:false,refreshStatus:()=>{},openStrategy:()=>{}}}>{x}</LabContext.Provider>;
const flush=()=>act(async()=>{await new Promise(r=>setTimeout(r,20));});
async function mount(component) {
  const container=document.createElement("div");document.body.appendChild(container);const root=createRoot(container);
  await act(async()=>root.render(wrap(component)));await flush();
  return {container,unmount:async()=>{await act(async()=>root.unmount());container.remove();}};
}
const click=async(el)=>{if(!el)throw Error("missing control");await act(async()=>el.dispatchEvent(new MouseEvent("click",{bubbles:true})));await flush();};
const assert=(v,m)=>{if(!v)throw Error(m);};
export async function runSmoke() {
  const results=[];async function run(label,f){try{await f();results.push({label,ok:true});}catch(e){results.push({label,ok:false,error:e.message});}}
  await run("raw numeric mixed values, negatives, missing-last both directions",async()=>{
    const values=["100",2,"10",0,-2,"-100",null,"",NaN];const rows=values.map((n,i)=>({node_id:i+1,n}));
    assert(rows.slice().sort((a,b)=>compareNodes(a,b,r=>r.n)).map(r=>r.node_id).join()==="6,5,4,2,3,1,7,8,9","ascending numeric");
    assert(rows.slice().sort((a,b)=>compareNodes(a,b,r=>r.n,-1)).map(r=>r.node_id).join()==="1,3,2,4,5,6,7,8,9","descending numeric");
  });
  await run("shared table pages full sorted set and caps DOM without dropping nodes",async()=>{
    const rows=Array.from({length:1000},(_,i)=>({node_id:i+1,n:String(1000-i)}));rows.sort((a,b)=>compareNodes(a,b,r=>r.n));
    const m=await mount(<NodeTable rows={rows}>{visible=><tbody>{visible.map(r=><tr key={r.node_id}><td>{r.n}</td></tr>)}</tbody>}</NodeTable>);
    assert(m.container.querySelectorAll('tbody tr').length===100,"bounded page");
    assert(m.container.querySelector('tbody tr').textContent==="1","full set sorted before page");
    await click([...m.container.querySelectorAll('button')].find(b=>b.textContent==="Next"));
    assert(m.container.querySelector('tbody tr').textContent==="101","second sorted page");await m.unmount();
  });
  await run("Deep durable star, unchanged controls, one PUT and no population refetch",async()=>{
    calls.length=0;const m=await mount(<DeepBacktest/>);
    assert(m.container.querySelector('[data-canonical-node-table]'),"shared table missing");
    const text=m.container.textContent;
    assert(m.container.querySelector('[data-generation-metric="return_pct"]').textContent.includes("9.17"),"Deep generation result differs");
    for(const control of ['1D','7D','14D','1M','3M','6M','1Y','2Y','3Y','INSPECT','DEEPTEST','SCENARIO'])assert(text.includes(control),`lost ${control}`);
    assert(calls.some(c=>c.url.includes('/api/deeptest/coverage/pairs')&&JSON.parse(c.body).duration_key==='3M'),'initial rows never got persisted coverage');
    await click([...m.container.querySelectorAll('button')].find(b=>b.textContent.trim()==='3Y'));
    assert(calls.some(c=>c.url.includes('/api/deeptest/coverage/pairs')&&JSON.parse(c.body).duration_key==='3Y'),'duration change did not recount range bars');
    const before=calls.filter(c=>c.url.includes('/api/nodes?')).length;
    await click(m.container.querySelector('[aria-label="Star Node #1505"]'));
    assert(stars.has(1505),"not persisted");assert(m.container.querySelector('[aria-label="Unstar Node #1505"]'),"row not updated");
    assert(calls.filter(c=>c.method==='PUT').length===1,"duplicate star writes");
    assert(calls.filter(c=>c.url.includes('/api/nodes?')).length===before,"star recomputed population");
    assert(!calls.some(c=>/\/start$|manual_order|\/deeptest\/run$/.test(c.url)),"star triggered work");await m.unmount();
    const again=await mount(<DeepBacktest/>);assert(again.container.querySelector('[aria-label="Unstar Node #1505"]'),"refresh lost star");await again.unmount();
  });
  await run("Live Starred shows exact durable candidates and keeps controls",async()=>{
    const m=await mount(<LiveNodeTable/>);
    const label=[...m.container.querySelectorAll('label')].find(l=>l.textContent.toLowerCase().includes('starred'));
    await click(label?.querySelector('input'));
    assert(calls.some(c=>c.url.includes('starred_only=true')),"no starred registry request");
    assert(m.container.querySelector('[aria-label="Unstar Node #1505"]'),"candidate missing");
    assert(m.container.querySelector('[data-generation-metric="return_pct"]').textContent.includes("9.17"),"Live generation result differs");
    assert(!m.container.textContent.includes('Node #1506'),"unstarred leaked");
    for(const control of ['START','STOP ALL','Risk','Max trades','Schedule'])assert(m.container.textContent.includes(control),`lost ${control}`);
    await click(m.container.querySelector('[aria-label="Unstar Node #1505"]'));
    assert(!stars.has(1505),"unstar not durable");assert(!m.container.textContent.includes('Node #1505'),"unstar row not removed promptly");await m.unmount();
  });
  await run("Overview qualified saved view uses same registry and all-history scope",async()=>{
    calls.length=0;const m=await mount(<Overview/>);
    assert(m.container.querySelector('[data-canonical-node-table]'),"Overview not shared");
    assert(m.container.querySelector('[data-generation-metric="return_pct"]').textContent.includes("9.17"),"Overview generation result differs");
    assert(calls.some(c=>c.url.includes('/api/nodes?')&&c.url.includes('filter=qualified')),"Overview default not qualified");
    assert(!calls.some(c=>c.url.includes('/api/population?')),"independent inventory request");
    const select=m.container.querySelector('[aria-label="Top strategies scope"]');
    await act(async()=>{select.value='all';select.dispatchEvent(new Event('change',{bubbles:true}));});await flush();
    assert(m.container.textContent.includes('Node #718'),"all history cannot show top saved canonical 718");
    assert(m.container.textContent.includes('research #318'),"canonical identity confused with research718");await m.unmount();
  });
  await run("render fixture benchmark old full-map versus bounded shared table",async()=>{
    const rows=Array.from({length:1000},(_,i)=>({node_id:i+1}));
    const body=visible=><tbody>{visible.map(r=><tr key={r.node_id}>{Array.from({length:8},(_,i)=><td key={i}>{r.node_id}</td>)}</tr>)}</tbody>;
    let t=performance.now();const old=await mount(<table>{body(rows)}</table>);const before={ms:performance.now()-t,rows:old.container.querySelectorAll('tbody tr').length,cells:old.container.querySelectorAll('td').length};await old.unmount();
    t=performance.now();const next=await mount(<NodeTable rows={rows}>{body}</NodeTable>);const after={ms:performance.now()-t,rows:next.container.querySelectorAll('tbody tr').length,cells:next.container.querySelectorAll('td').length};await next.unmount();
    console.log('V9.1_RENDER_FIXTURE '+JSON.stringify({before,after,environment:'jsdom; synthetic 1000 rows x8cells; includes20ms flush; not Edge/browser paint'}));
    assert(before.rows===1000&&after.rows===100,"DOM benchmark invalid");
  });
  return {total:results.length,results,failures:results.filter(r=>!r.ok)};
}
