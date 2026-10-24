import React,{act} from 'react';
import {createRoot} from 'react-dom/client';
import {DeepBulk} from '../src/components/SelectionControls.jsx';
let job,calls,delay=0,active=0,maxActive=0;
const wait=ms=>new Promise(r=>setTimeout(r,ms));
const ok=(v,m)=>{if(!v)throw Error(m);};
const makeJob=(status='RUNNING')=>({id:'fixture-v10',payload:{auto_partial:true},status,phase:'PREPARING_FEATURES',selected:2,validated:2,completed:1,success:1,failure:0,skipped:0,percent:50,current_node:2,elapsed_seconds:42,last_update:1792800000,log_path:'/fixture/DATA/logs/DEEPTEST.log',finished:1,total:2,preflight:{selected:2,full:0,partial:2,unavailable:0},results:[{node_id:1,status:'PARTIAL'},{node_id:2,status:'RUNNING'}]});
export function payloadFor(url,method='GET',raw){
 calls.push({url,method,body:raw?JSON.parse(raw):null});
 if(method==='POST'&&url.endsWith('/batches'))return {ok:true,batch:job};
 if(url.includes('/deeptest/batches/')){
  if(delay){active++;maxActive=Math.max(maxActive,active);return wait(delay).then(()=>{active--;return {ok:true,batch:job};});}
  return {ok:true,batch:job};
 }
 return {ok:true};
}
async function mount(){const container=document.createElement('div');document.body.append(container);const root=createRoot(container);await act(async()=>{root.render(<DeepBulk nodes={[{node_id:1},{node_id:2}]} selected={{1:true,2:true}} duration="3M" settings={{}} nodeConfig={()=>({})}/>);await wait(20);});return {container,root,close:async()=>{await act(async()=>root.unmount());container.remove();}};}
const start=m=>[...m.container.querySelectorAll('button')].find(b=>b.textContent.includes('DEEPTEST selected'));
const click=async b=>act(async()=>{b.dispatchEvent(new MouseEvent('click',{bubbles:true}));await wait(40);});
export async function runSmoke(){let total=0;const failures=[];
 async function test(label,fn){total++;localStorage.clear();sessionStorage.clear();calls=[];job=makeJob();delay=active=maxActive=0;try{await fn();console.log('OK '+label);}catch(e){failures.push({label,error:e.message});}}
 await test('explicit payload automatically accepts supported partial coverage without popup',async()=>{const m=await mount();try{await click(start(m));const posts=calls.filter(c=>c.method==='POST');ok(posts.length===1&&posts[0].body.auto_partial===true,'missing auto policy');ok(posts[0].body.node_ids.join(',')==='1,2','selection changed');ok(!m.container.querySelector('[role="dialog"]'),'blocking dialog');}finally{await m.close();}});
 await test('actual phase counters timing current node and durable log are rendered',async()=>{const m=await mount();try{await click(start(m));const text=m.container.textContent;for(const part of ['PREPARING_FEATURES','validated 2','completed 1','success 1','50%','current node 2','elapsed 42s','DEEPTEST.log'])ok(text.includes(part),'missing '+part);}finally{await m.close();}});
 await test('same-tick duplicate clicks submit only once',async()=>{const m=await mount();try{const b=start(m);await act(async()=>{b.dispatchEvent(new MouseEvent('click',{bubbles:true}));b.dispatchEvent(new MouseEvent('click',{bubbles:true}));await wait(40);});ok(calls.filter(c=>c.method==='POST').length===1,'duplicate POST');}finally{await m.close();}});
 await test('refresh recovers saved job without resubmission',async()=>{sessionStorage.setItem('v92.deep.v93.batch',JSON.stringify(job.id));const m=await mount();try{await act(async()=>await wait(60));ok(m.container.textContent.includes(job.id),'saved job not recovered');ok(!calls.some(c=>c.method==='POST'),'blind restart');}finally{await m.close();}});
 await test('slow polling never overlaps',async()=>{delay=2200;sessionStorage.setItem('v92.deep.v93.batch',JSON.stringify(job.id));const m=await mount();try{await act(async()=>await wait(2400));ok(maxActive===1,'overlapping polls');ok(calls.filter(c=>c.method==='GET').length===1,'interval spawned another slow request');}finally{await m.close();}});
 await test('older waiting jobs retain explicit legacy consent',async()=>{job=makeJob('AWAITING_COVERAGE_DECISION');job.payload={};sessionStorage.setItem('v92.deep.v93.batch',JSON.stringify(job.id));const m=await mount();try{await act(async()=>await wait(50));ok(m.container.querySelector('[role="dialog"]'),'legacy consent removed');ok(!calls.some(c=>c.method==='POST'),'old job automatically resumed');}finally{await m.close();}});
 return {total,failures};
}
