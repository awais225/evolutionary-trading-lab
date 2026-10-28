import React,{act} from 'react';
import {createRoot} from 'react-dom/client';
import DeepScenarioPanel from '../src/components/DeepScenarioPanel.jsx';
import Overview from '../src/pages/Overview.jsx';
import {LabContext} from '../src/App.jsx';
import ProcessingStatus,{generationState} from '../src/components/ProcessingStatus.jsx';
let calls=[], broken=false, delayed=false;
const wait=ms=>new Promise(r=>setTimeout(r,ms));
const ok=(v,m)=>{if(!v)throw Error(m);};
const scenario=id=>({run_id:id,status:'COMPLETED',symbol:'EURUSD',timeframe:'M1',analytics:{summary:{trades:id==='other'?1:3015}},settings:{},node_settings:{},bars_used:3030});
export async function payloadFor(url){
 calls.push(url);
 if(url.includes('/node-data'))return {ok:true,node_data:{revision:'fixture-r1',counts:{},tops:{}}};
 if(url.includes('/filtered-stats'))return {original:{summary:{trades:3015}},trade_counts:{kept:3015,original:3015,excluded:0}};
 if(url.includes('/trades?')){
  const u=new URL(url,'http://localhost'),offset=Number(u.searchParams.get('offset')||0),limit=Number(u.searchParams.get('limit'));
  if(delayed)await wait(30);
  const total=url.includes('/other/')?1:3015;
  return {total,trades:broken&&offset>=500?[]:Array.from({length:Math.min(limit,total-offset)},(_,i)=>({trade_no:offset+i+1,net_pnl:offset+i+1,side:'long',entry_ts:1780000000+offset+i,session:'london'}))};
 }
 if(url.includes('/run/'))return {ok:true,scenario:scenario(url.split('/run/')[1])};
 return {ok:true,scenarios:[]};
}
async function mount(view){const container=document.createElement('div');document.body.append(container);const root=createRoot(container);await act(async()=>{root.render(view);await wait(10);});return {container,root,close:async()=>{await act(async()=>root.unmount());container.remove();}};}
async function settle(ms=100){await act(async()=>await wait(ms));}
async function click(b){ok(b,'missing button');await act(async()=>{b.dispatchEvent(new MouseEvent('click',{bubbles:true}));await wait(10);});}
const inspector=()=> <DeepScenarioPanel nodeId={123} scenarios={[scenario('large')]} />;
export async function runSmoke(){let total=0;const failures=[];
 async function test(label,fn){total++;calls=[];broken=delayed=false;try{await fn();console.log('OK '+label);}catch(e){failures.push({label,error:e.message});}}
 await test('authoritative generation states, never infer running from below-target count',async()=>{
  for(const [lab,want] of [[null,'UNAVAILABLE'],[{running:false,execution_state:'STOPPED',generation_status:'ACTIVE'},'STOPPED'],[{running:true},'RUNNING'],[{paused:true,running:true},'PAUSED'],[{is_target_reached:true},'TARGET REACHED'],[{execution_state:'FAILED'},'FAILED'],[{execution_state:'CANCELLED'},'CANCELLED']])ok(generationState(lab)===want,want);
 });
 await test('missing resources are N/A; device activity does not imply app GPU compute',async()=>{const m=await mount(<ProcessingStatus lab={{running:false}} resources={{gpu:{enabled:true,available:true,utilization_pct:90}}}/>);try{const t=m.container.textContent;ok(t.includes('N/A'),'fabricated metric');ok(t.includes('not verified'),'device utilization called app GPU compute');ok(!t.includes('Accelerated'),'fake acceleration');}finally{await m.close();}});
 await test('complete 3015-trade population loaded with bounded pages, CSV unchanged',async()=>{const m=await mount(inspector());try{await settle(150);ok(m.container.textContent.includes('Complete saved history loaded: 3,015'),'truncated population');const reads=calls.filter(u=>u.includes('/trades?'));ok(reads.length===7,'not all history pages read');ok(reads.every(u=>new URL(u,'http://localhost').searchParams.get('limit')==='500'),'oversized chunk');ok(m.container.querySelector('a[href$="/export.csv"]'),'complete CSV missing');}finally{await m.close();}});
 await test('jump directly beyond trade 3000 without refetch or display cap',async()=>{const m=await mount(inspector());try{await settle(150);const before=calls.filter(u=>u.includes('/trades?')).length;const input=m.container.querySelector('[aria-label="Trade page"]');ok(input,'no page jump');await act(async()=>{const set=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;set.call(input,'301');input.dispatchEvent(new window.Event('input',{bubbles:true}));input.dispatchEvent(new window.Event('change',{bubbles:true}));await wait(10);});ok(m.container.querySelector('tbody').textContent.includes('3001'),'cannot inspect trade 3001');ok(calls.filter(u=>u.includes('/trades?')).length===before,'pagination refetched full history');}finally{await m.close();}});
 await test('incomplete page is a visible error, never a substitute truncated history',async()=>{broken=true;const m=await mount(inspector());try{await settle(80);ok(m.container.textContent.includes('Incomplete trade response'),'error swallowed');ok(!m.container.textContent.includes('Complete saved history loaded'),'partial history masquerades as complete');}finally{await m.close();}});
 await test('unmount stops progressive pagination after in-flight request',async()=>{delayed=true;const m=await mount(inspector());await m.close();const before=calls.filter(u=>u.includes('/trades?')).length;await settle(100);ok(calls.filter(u=>u.includes('/trades?')).length<=before,'pagination continued after unmount');});
 await test('global descending sort includes trades beyond 3000 without refetch',async()=>{const m=await mount(inspector());try{await settle(150);const n=calls.filter(u=>u.includes('/trades?')).length;const b=[...m.container.querySelectorAll('button')].find(b=>b.textContent.startsWith('P&L'));await click(b);await click(b);ok(m.container.querySelector('tbody').textContent.includes('3015'),'sort used only display page');ok(calls.filter(u=>u.includes('/trades?')).length===n,'sort refetched history');}finally{await m.close();}});
 await test('Overview events do not restart inventory polling; status resources are reused',async()=>{
  const status={lab:{running:false},resources:{cpu:{},memory:{},gpu:{}},node_data:{revision:'fixture-r1'}};
  const view=events=><LabContext.Provider value={{status,events,connected:true,refreshStatus:()=>{},openStrategy:()=>{}}}><Overview/></LabContext.Provider>;
  const m=await mount(view([]));try{await settle(40);const n=calls.filter(u=>u==='/api/data/inventory').length;ok(n===1,'initial inventory not loaded');for(let i=1;i<=3;i++){await act(async()=>{m.root.render(view(Array.from({length:i},()=>({type:'tick'}))));await wait(10);});}ok(calls.filter(u=>u==='/api/data/inventory').length===n,'event restarted expensive requests');ok(!calls.some(u=>u==='/api/resources'),'duplicate resources request despite status snapshot');}finally{await m.close();}});
 return {total,failures};
}
