import React,{act} from 'react';
import {createRoot} from 'react-dom/client';
import assert from 'node:assert/strict';
import {LiveNodeTable} from '../src/components/LiveNodeIndex.jsx';
import DeepScenarioPanel from '../src/components/DeepScenarioPanel.jsx';
import {LabContext} from '../src/App.jsx';
import SpeedLogPanel from '../src/components/SpeedLogPanel.jsx';
import {payloadFor as nodePayload} from './v9_1_nodes_ui.jsx';
const wait=ms=>new Promise(r=>setTimeout(r,ms));
let requests=[],prompts=[],deleted=0,refuse=false,cleanupErrors=0;
const saved={run_id:'dt-selected',node_id:1,status:'COMPLETED',metrics:{trades:0},analytics:{summary:{trades:0}}};
global.fetch=async(url,opts={})=>{
 const body=opts.body?JSON.parse(opts.body):{};requests.push({url:String(url),method:opts.method||'GET',body});
 let value;
 if(String(url).includes('/manage/delete')){if(refuse)return {ok:false,status:409,text:async()=>JSON.stringify({detail:'Worker still stopping'}),json:async()=>({detail:'Worker still stopping'})};deleted++;value={ok:true,artifact_cleanup_errors:cleanupErrors};}
 else if(String(url).includes('/run/dt-selected/trades'))value={trades:[],total:0};
 else if(String(url).includes('/run/dt-selected/filtered-stats'))value={original:{summary:{trades:0}},filtered:{summary:{trades:0}},trade_counts:{original:0,kept:0}};
 else if(String(url).includes('/run/dt-selected'))value={ok:true,scenario:saved};
 else if(String(url).includes('/deeptest/scenarios'))value={scenarios:deleted?[]:[saved]};
 else value=nodePayload(String(url),opts.method||'GET',opts.body);
 return {ok:true,status:200,headers:{get:()=> 'application/json'},json:async()=>value};
};
window.prompt=text=>{prompts.push(text);return prompts.answer??null;};
const wrap=x=><LabContext.Provider value={{status:{lab:{},counts:{}},events:[],connected:false,refreshStatus:()=>{},openStrategy:()=>{}}}>{x}</LabContext.Provider>;
async function mount(view){const div=document.createElement('div');document.body.append(div);const root=createRoot(div);await act(async()=>{root.render(wrap(view));await wait(50);});await act(async()=>await wait(50));return {div,close:async()=>{await act(async()=>root.unmount());div.remove();}};}
const click=async node=>{assert.ok(node);await act(async()=>{node.dispatchEvent(new window.MouseEvent('click',{bubbles:true}));await wait(50);});};
const button=(div,text)=>[...div.querySelectorAll('button')].find(b=>b.textContent===text);
localStorage.clear();nodePayload('/api/research/shortlist/718','PUT',JSON.stringify({starred:true}));
let mounted=await mount(<LiveNodeTable/>);
const category=mounted.div.querySelector('select[title^="Which nodes"]');assert.ok(category);
await act(async()=>{category.value='starred';category.dispatchEvent(new window.Event('change',{bubbles:true}));await wait(60);});
assert.ok(requests.some(r=>r.url.includes('filter=all')&&r.url.includes('starred_only=true')));
assert.ok(mounted.div.textContent.includes('Node #718'));assert.ok(!mounted.div.textContent.includes('Node #1506'));
assert.ok(!requests.some(r=>r.method!=='GET'));console.log('PASS STARRED category reuses authoritative state, populated view, no trading action');
await act(async()=>{window.dispatchEvent(new window.CustomEvent('candidate-star-changed',{detail:{strategy_id:718,starred:false}}));await wait(20);});
assert.ok(!mounted.div.textContent.includes('Node #718'));console.log('PASS STARRED empty state updates immediately on authoritative unstar event');await mounted.close();
requests=[];mounted=await mount(<DeepScenarioPanel nodeId={1}/>);
prompts.answer='yes';await click(button(mounted.div,'Delete selected scenario…'));assert.equal(deleted,0);console.log('PASS wrong typed confirmation never sends deletion');
prompts.answer='DELETE dt-selected';await click(button(mounted.div,'Delete selected scenario…'));assert.equal(deleted,1);assert.ok(requests.some(r=>r.url.endsWith('/manage/delete')&&r.body.run_id==='dt-selected'));assert.ok(prompts.some(t=>t.includes('Nodes, genomes, ancestry, stars')));console.log('PASS selected deletion has explicit scope/confirmation and calls real endpoint');await mounted.close();
mounted=await mount(<DeepScenarioPanel nodeId={1}/>);prompts.answer='DELETE ALL DEEPTEST DATA';await click(button(mounted.div,'DELETE ALL DEEPTEST DATA…'));assert.equal(deleted,2);assert.ok(requests.some(r=>r.url.endsWith('/manage/delete-all')));console.log('PASS global deletion has separate exact typed confirmation');await mounted.close();
deleted=0;refuse=true;mounted=await mount(<DeepScenarioPanel nodeId={1}/>);prompts.answer='DELETE dt-selected';await click(button(mounted.div,'Delete selected scenario…'));assert.equal(deleted,0);assert.ok(mounted.div.querySelector('[role="alert"]'));console.log('PASS active writer refusal is visible, no optimistic deletion success');await mounted.close();

refuse=false;cleanupErrors=1;deleted=0;mounted=await mount(<DeepScenarioPanel nodeId={1}/>);prompts.answer='DELETE dt-selected';await click(button(mounted.div,'Delete selected scenario…'));assert.equal(deleted,1);assert.ok(mounted.div.querySelector('[role="alert"]'));assert.ok(!mounted.div.querySelector('option[value="dt-selected"]'));console.log('PASS incomplete TEMP cleanup reports warning but still refreshes successfully deleted records');await mounted.close();

let copied;
const snapshot={snapshot_id:'fixture-complete',recording:{active:true,profile:'light'},retention:{retained_events:2,dropped_events:0,latest_seq:2},next_seq:2,events:[{seq:1,timestamp:1,elapsed_seconds:1,severity:'INFO',subsystem:'deeptest',type:'phase',run_id:'dt-one',pid:1,fields:{}},{seq:2,timestamp:2,elapsed_seconds:2,severity:'ERROR',subsystem:'deeptest',type:'phase',run_id:'dt-two',pid:1,fields:{}}]};
global.fetch=async()=>({ok:true,json:async()=>snapshot});
Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{copied=text;}}});
mounted=await mount(<SpeedLogPanel/>);await click([...mounted.div.querySelectorAll('button')].find(b=>b.textContent.startsWith('SPEED LOG')));
await act(async()=>{const s=mounted.div.querySelector('[aria-label="Speed severity"]');s.value='ERROR';s.dispatchEvent(new window.Event('change',{bubbles:true}));const input=mounted.div.querySelector('[aria-label="Speed scenario"]');Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set.call(input,'dt-not-matched');input.dispatchEvent(new window.Event('input',{bubbles:true}));await wait(20);});
assert.ok(mounted.div.textContent.includes('No retained events match'));await click(button(mounted.div,'COPY ALL'));assert.equal(JSON.parse(copied).events.length,2);console.log('PASS SPEED LOG severity/scenario filters affect display only, complete COPY ALL unchanged');await mounted.close();
