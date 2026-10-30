// Isolated startup/polling regressions using synthetic HTTP fixtures, not native telemetry.
import React,{act} from 'react';
import {createRoot} from 'react-dom/client';
import App,{LabContext} from '../src/App.jsx';
import Overview from '../src/pages/Overview.jsx';
import TopStatusRow from '../src/components/TopStatusRow.jsx';
let calls=[],fail=new Set(),held=new Map(),intervals=new Map(),timeouts=new Map(),serial=0;
const realSetTimeout=globalThis.setTimeout,realClearTimeout=globalThis.clearTimeout;
const wait=ms=>new Promise(r=>realSetTimeout(r,ms));
const ok=(value,message)=>{if(!value)throw Error(message);};
const status=()=>({lab:{running:false,target:2,population_snapshot:{observed_at:1}},resources:{cpu:{percent:23},memory:{},gpu:{}},node_data:{revision:'fixture-startup-r1',counts:{}},status_counts:{QUALIFIED:1},components:{},config_summary:{symbol:'EURUSD',timeframes:['M1']}});
export async function payloadFor(url){
 calls.push(url);
 if(held.has(url))await held.get(url).promise;
 if(fail.has(url))throw new DOMException('signal timed out','TimeoutError');
 if(url==='/api/status')return status();
 if(url==='/api/system/status_row')return {cpu:{percent:23},gpu:{enabled:false,processing_state:'NOT VERIFIED'},mt5:{is_simulated:true,connected:false},db:{total_strategies:1},data:{dataset_count:1},progress:{fill_percentage:0}};
 if(url.includes('/api/node-data'))return {ok:true,node_data:status().node_data};
 if(url.startsWith('/api/nodes?'))return {ok:true,total:1,nodes:[{node_id:42,node_label:'Node #42',symbol:'EURUSD',timeframe:'M1',generation:1,fitness:.7,bucket:'qualified',qualification:'QUALIFIED',generation_metrics:{return_pct:.12,profit_factor:1.5,max_drawdown_pct:.02,win_rate:.5,trades:10,net_profit:120}}]};
 if(url.includes('/lifecycle')||url.includes('/ready'))return {code:'ready',state:'ready',ready:true,steps:[]};
 return {ok:true,scenarios:[],jobs:[],runs:[],datasets:[],shortlist:[]};
}
function installTimers(){
 globalThis.setInterval=(fn,ms)=>{const id=++serial;intervals.set(id,{fn,ms});return id;};
 globalThis.clearInterval=id=>intervals.delete(id);
 globalThis.setTimeout=(fn,ms,...args)=>{if(ms===4000){const id=++serial;timeouts.set(id,{fn,ms});return id;}return realSetTimeout(fn,ms,...args);};
 globalThis.clearTimeout=id=>{if(!timeouts.delete(id))realClearTimeout(id);};
}
async function settle(){await act(async()=>await wait(35));}
async function fire(ms){await act(async()=>{for(const [id,t] of [...timeouts])if(t.ms===ms){timeouts.delete(id);t.fn();}for(const t of [...intervals.values()])if(t.ms===ms)t.fn();await wait(35);});}
function hold(url){let release;const promise=new Promise(r=>release=r);held.set(url,{promise});return ()=>{held.delete(url);release();};}
async function mount(view){const container=document.createElement('div');document.body.append(container);const root=createRoot(container);await act(async()=>{root.render(view);await wait(15);});await settle();return {container,root,close:async()=>{await act(async()=>root.unmount());container.remove();}};}
async function click(button){ok(button,'button missing');await act(async()=>{button.dispatchEvent(new MouseEvent('click',{bubbles:true}));await wait(35);});}
const overview=()=> <LabContext.Provider value={{status:status(),events:[],connected:true,refreshStatus:()=>{},openStrategy:()=>{}}}><Overview/></LabContext.Provider>;
const count=url=>calls.filter(u=>u===url).length;
export async function runSmoke(){
 let total=0;const failures=[];installTimers();
 globalThis.WebSocket=class {close(){}};
 async function test(label,fn){total++;calls=[];fail.clear();try{await fn();console.log('PASS '+label);}catch(e){failures.push({label,error:e.stack||e.message});}finally{ok(!held.size,'unreleased test fixture');}}
 try{
 await test('Overview initial load explicitly requests generation records and retains existing metrics',async()=>{const m=await mount(overview());try{const nodes=calls.filter(u=>u.startsWith('/api/nodes?'));ok(nodes.length===1,'duplicate initial node request');ok(new URL(nodes[0],'http://localhost').searchParams.get('metric_source')==='generation','startup eagerly requests DeepTest history');ok(m.container.textContent.includes('42'),'actual fixture node missing');ok(m.container.querySelector('[data-generation-metric=profit_factor]')?.textContent==='1.5','generation profit factor lost');ok(!m.container.textContent.includes('signal timed out'),'startup failed');ok(!calls.includes('/api/resources'),'duplicate resource poll');}finally{await m.close();}});
 await test('Overview consecutive refreshes reuse unchanged node/inventory snapshots without a request storm',async()=>{const m=await mount(overview());try{for(let i=0;i<3;i++)await fire(4000);ok(count('/api/jobs?limit=50')===4,'refresh scheduling changed');ok(count('/api/data/inventory')===1,'unnecessary inventory reload');ok(calls.filter(u=>u.startsWith('/api/nodes?')).length===1,'unchanged node snapshot refetched');}finally{await m.close();}});
 await test('Overview genuine temporary failure retains metrics, displays stale warning and recovers',async()=>{const m=await mount(overview());try{fail.add('/api/jobs?limit=50');await fire(4000);ok(m.container.textContent.includes('Dashboard refresh failed; last values retained'),'failure hidden');ok(m.container.querySelector('[data-generation-metric=profit_factor]')?.textContent==='1.5','last generation metrics erased');fail.clear();await fire(4000);ok(!m.container.textContent.includes('Dashboard refresh failed'),'recovery warning not cleared');}finally{await m.close();}});
 await test('System Status loads CPU while optional MT5/GPU services are unavailable',async()=>{const m=await mount(<TopStatusRow/>);try{ok(m.container.textContent.includes('23%'),'measured CPU fixture missing');ok(m.container.textContent.includes('SIMULATOR'),'unavailable native MT5 not labelled');ok(m.container.textContent.includes('NOT VERIFIED'),'GPU availability lost');ok(count('/api/system/status_row')===1,'duplicate initial status');}finally{await m.close();}});
 await test('System Status serializes slow polls, retains last values on failure, then recovers',async()=>{const m=await mount(<TopStatusRow/>);let release;try{release=hold('/api/system/status_row');await fire(2500);await fire(2500);await fire(2500);ok(count('/api/system/status_row')===2,'overlapping status requests');fail.add('/api/system/status_row');release();release=null;await settle();ok(m.container.textContent.includes('values may be stale'),'stale warning absent');ok(m.container.textContent.includes('23%'),'last known CPU erased');fail.clear();await fire(2500);ok(!m.container.textContent.includes('values may be stale'),'recovery failed');}finally{if(release)release();await settle();await m.close();}});
 await test('Dashboard startup status failure supports Retry status and clears the error',async()=>{fail.add('/api/status');const m=await mount(<App/>);try{ok(m.container.textContent.includes('Status unavailable; displayed values may be stale'),'initial status failure not visible');const retry=[...m.container.querySelectorAll('button')].find(b=>b.textContent==='Retry status');fail.clear();await click(retry);ok(count('/api/status')===2,'Retry did not issue exactly one request');ok(!m.container.textContent.includes('Status unavailable; displayed values may be stale'),'Retry did not recover');}finally{await m.close();}});
 await test('Dashboard repeated status ticks never overlap pending requests and preserve values after failure',async()=>{const m=await mount(<App/>);let release;try{release=hold('/api/status');await fire(3000);await fire(3000);await fire(3000);ok(count('/api/status')===2,'status request storm');fail.add('/api/status');release();release=null;await settle();ok(m.container.textContent.includes('Status unavailable; displayed values may be stale'),'failure swallowed');ok(m.container.querySelector('[aria-label="Generation and processing status"]')?.textContent.includes('STOPPED'),'last known lab status erased');fail.clear();await click([...m.container.querySelectorAll('button')].find(b=>b.textContent==='Retry status'));ok(count('/api/status')===3,'retry overlaps or is blocked');}finally{if(release)release();await settle();await m.close();}});
 await test('Unmount clears status and Overview polling timers',async()=>{const m=await mount(<App/>);await m.close();const before=calls.length;for(const ms of [2500,3000,4000,15000])await fire(ms);ok(calls.length===before,'polling continued after unmount');ok(!intervals.size&&!timeouts.size,'timer cleanup missing');});
 }finally{globalThis.setTimeout=realSetTimeout;globalThis.clearTimeout=realClearTimeout;}
 return {total,failures};
}
