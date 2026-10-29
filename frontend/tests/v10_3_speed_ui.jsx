import React,{act} from 'react';
import {createRoot} from 'react-dom/client';
let root;
const matches=(text,want)=>want instanceof RegExp?want.test(text):text===want;
const queryText=want=>[...document.body.querySelectorAll('*')].filter(n=>n.children.length===0&&matches(n.textContent,want));
const waitFor=async fn=>{const end=Date.now()+5000;for(;;){try{return fn();}catch(e){if(Date.now()>end)throw e;await act(async()=>await new Promise(r=>setTimeout(r,20)));}}};
const screen={getByRole:(role,{name})=>{const node=[...document.querySelectorAll(role==='button'?'button':'[role="'+role+'"]')].find(n=>matches(n.textContent,name));if(!node)throw Error('No '+name);return node;},getByLabelText:name=>document.querySelector('[aria-label="'+name+'"]'),getAllByText:queryText,findByText:want=>waitFor(()=>{const node=queryText(want)[0];if(!node)throw Error('Text not found '+want);return node;})};
const fireEvent={click:node=>act(()=>node.dispatchEvent(new window.MouseEvent('click',{bubbles:true}))),change:(node,{target})=>act(()=>{const proto=node.tagName==='SELECT'?window.HTMLSelectElement.prototype:window.HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(proto,'value').set.call(node,target.value);node.dispatchEvent(new window.Event('input',{bubbles:true}));node.dispatchEvent(new window.Event('change',{bubbles:true}));})};
const render=view=>{const node=document.createElement('div');document.body.append(node);root=createRoot(node);act(()=>root.render(view));};
const cleanup=()=>act(()=>root.unmount());
import assert from 'node:assert/strict';
import SpeedLogPanel from '../src/components/SpeedLogPanel.jsx';
import {copyAllSnapshot} from '../src/components/speedLog.js';

const snapshot={snapshot_id:'complete-123',recording:{active:true,profile:'light'},retention:{retained_events:120,dropped_events:8,latest_seq:120},events:Array.from({length:120},(_,i)=>({seq:i+1,timestamp:1783339200+i,elapsed_seconds:i,subsystem:i%2?'generation':'db',type:'progress',severity:'INFO',pid:123,fields:{node_id:i+1}}))};
let copied,downloaded;
let result=await copyAllSnapshot({load:async()=>snapshot,clipboard:async text=>{copied=text;},download:text=>{downloaded=text;}});
assert.deepEqual(JSON.parse(copied),snapshot);assert.match(result.status,/Copied ALL/);console.log('PASS complete COPY ALL includes all 120 events');
result=await copyAllSnapshot({load:async()=>snapshot,clipboard:async()=>{throw Error('denied');},download:text=>{downloaded=text;}});
assert.deepEqual(JSON.parse(downloaded),snapshot);assert.match(result.status,/Clipboard failed/);console.log('PASS clipboard failure downloads complete JSON');
let called=false;await copyAllSnapshot({load:async()=>snapshot,limit:10,clipboard:async()=>{called=true;},download:text=>{downloaded=text;}});
assert.equal(called,false);assert.deepEqual(JSON.parse(downloaded),snapshot);console.log('PASS oversized snapshot has complete download, no truncated clipboard');
await assert.rejects(()=>copyAllSnapshot({load:async()=>{throw Error('snapshot failed');}}),/snapshot failed/);console.log('PASS snapshot errors do not report copy success');
let after=0,fetches=0,active=0,maxActive=0,fullFetches=0;
global.fetch=async(url,options)=>{
 active++;maxActive=Math.max(active,maxActive);fetches++;
 try{
  await new Promise(resolve=>setTimeout(resolve,4));
  if(String(url).endsWith('/snapshot')){fullFetches++;return {ok:true,json:async()=>snapshot};}
  if(String(url).includes('/recording'))return {ok:true,json:async()=>snapshot};
  if(String(url).includes('/frontend'))return {ok:true,json:async()=>({ok:true})};
  const events=after?[]:snapshot.events;after=120;return {ok:true,json:async()=>({...snapshot,events,next_seq:120})};
 }finally{active--;}
};
Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{copied=text;}}});
render(<SpeedLogPanel/>);fireEvent.click(screen.getByRole('button',{name:/SPEED LOG/}));
await screen.findByText(/node_id.*1/);assert.equal(screen.getAllByText(/PID 123/).length,50);console.log('PASS bounded page displays 50 of 120 retained entries');
fireEvent.change(screen.getByLabelText('Speed subsystem'),{target:{value:'db'}});
fireEvent.change(screen.getByLabelText('Speed search'),{target:{value:'not-present'}});
fireEvent.click(screen.getByRole('button',{name:'COPY ALL'}));await waitFor(()=>assert.equal(fullFetches,1));
await screen.findByText(/Copied ALL retained/);assert.equal(JSON.parse(copied).events.length,120);console.log('PASS COPY ALL ignores filters and page');
fireEvent.click(screen.getByRole('button',{name:'Clear display'}));fireEvent.click(screen.getByRole('button',{name:'COPY ALL'}));await waitFor(()=>assert.equal(fullFetches,2));assert.equal(JSON.parse(copied).events.length,120);console.log('PASS clear display does not clear backend COPY ALL');
fireEvent.click(screen.getByRole('button',{name:'Pause display'}));await screen.findByText(/DISPLAY PAUSED/);
const before=fetches;await act(async()=>{await new Promise(resolve=>setTimeout(resolve,2100));});assert.equal(fetches,before);assert.equal(maxActive,1);console.log('PASS display pause does not post recording OFF; polling does not overlap');
cleanup();const afterCleanup=fetches;await new Promise(resolve=>setTimeout(resolve,2100));assert.equal(fetches,afterCleanup);console.log('PASS unmount stops polling');
