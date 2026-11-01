import React,{act} from 'react';
import {createRoot} from 'react-dom/client';
import assert from 'node:assert/strict';
import DeepScenarioPanel from '../src/components/DeepScenarioPanel.jsx';
import DeepBacktest from '../src/pages/DeepBacktest.jsx';
const wait=ms=>new Promise(r=>setTimeout(r,ms));
const summary={initial_capital:100,account_currency:'EUR',net_profit:-70,max_drawdown:270,max_drawdown_pct:2.7,trades:3,profit_factor:0.74074,sharpe:null};
const cold={run_id:'fixture',node_id:1,status:'PARTIAL',coverage_status:'PARTIAL',analytics:{summary:{}},metrics:{},metric_provenance:{availability:'UNAVAILABLE',validation_state:'HISTORICAL_UNVERIFIED',field_states:{}}};
const detail={...cold,analytics:{summary},metrics:summary,metric_provenance:{availability:'VERIFIED',validation_state:'AUTHORITATIVE',field_states:{sharpe:{state:'NOT_APPLICABLE',reason:'statistical evidence unavailable in fixture'}},bars_processed:3,trade_count_matches_stored:true}};
let requests=[],mode='waiting',release;
const gate=new Promise(resolve=>{release=resolve;});
global.fetch=async(url,opts={})=>{
  const path=String(url);requests.push(path);
  if(mode==='waiting' && (path.includes('/run/')||path.includes('/filtered')))await gate;
  if(mode==='failure' && path.includes('/run/'))throw new Error('Synthetic validation request failed');
  const missingSummary={initial_capital:100,account_currency:'EUR',net_profit:null,trades:null};
  const selected=mode==='missing'?{...detail,analytics:{summary:missingSummary},metrics:missingSummary,metric_provenance:{...detail.metric_provenance,validation_state:'HISTORICAL_UNAVAILABLE',integrity:'MISSING_TRADE_RECORDS'}}:detail;
  let body=path.includes('/filtered')?{ok:true,original:{summary:selected.metrics},filtered:{summary:selected.metrics},trade_counts:{original:3,kept:3,excluded:0}}:path.includes('/trades')?{trades:[],total:0}:{ok:true,scenario:selected};
  if(mode==='listing'){
    if(path.includes('/deeptest/nodes'))body={nodes:[{node_id:1,scenario_count:1,valid_scenario_count:1,preferred:cold,scenarios:[{...cold,headline:{}}]}]};
    else if(path.includes('/nodes'))body={nodes:[],counts:{}};
    else if(path.includes('/settings'))body={settings:{total_capital:100}};
    else body={ok:true,scenarios:[]};
  }
  return {ok:true,status:200,headers:{get:()=> 'application/json'},json:async()=>body};
};
let div,root;
const mount=async element=>{div=document.createElement('div');document.body.append(div);root=createRoot(div);await act(async()=>{root.render(element);await wait(80);});await act(async()=>await wait(80));};
const unmount=async()=>{await act(async()=>root.unmount());div.remove();};
try {
  await mount(<DeepScenarioPanel nodeId={1} scenarios={[cold,{...cold,run_id:'other-history'},{...cold,run_id:'third-history'}]}/>);
  assert.ok(div.textContent.includes('Historical listing metrics are unverified, not genuine N/A'));
  assert.ok(div.textContent.includes('Inspect, View Trades and Compare use the authoritative saved trades'));
  console.log('PASS unavailable listing summary is explicit, not disguised as current financial evidence');
  assert.ok(div.textContent.includes('Loading / validation pending for the selected scenario'));
  assert.ok(!requests.some(u=>u.includes('/run/other-history')||u.includes('/run/third-history')));
  console.log('PASS validation pending reflects an actual selected request, not a background all-history workload');
  await act(async()=>{mode='success';release();await wait(100);});
  await act(async()=>await wait(80));
  assert.ok(div.textContent.includes('€270'));
  assert.ok(div.textContent.includes('270.000% of initial capital'));
  assert.ok(div.textContent.includes('PARTIAL DATASET'));
  assert.ok(requests.some(u=>u.includes('/run/fixture')));
  console.log('PASS opening detail retains authoritative currency/drawdown and PARTIAL coverage');
  assert.ok(!div.textContent.includes('Loading / validation pending'));
  assert.ok(div.textContent.includes('Authoritative saved-trade calculation available'));
  assert.ok(div.textContent.includes('N/A means this calculation cannot support that metric'));
  console.log('PASS completed authority and genuine mathematical N/A are distinguished from unverified history');
  await unmount();
  mode='failure';await mount(<DeepScenarioPanel nodeId={1} scenarios={[cold]}/>);
  assert.ok(div.textContent.includes('Synthetic validation request failed'));
  assert.ok(!div.textContent.includes('Loading / validation pending'));
  assert.ok(!div.textContent.includes('Authoritative saved-trade calculation available'));
  console.log('PASS failed validation is not left pending or shown as authoritative');
  await unmount();
  mode='missing';await mount(<DeepScenarioPanel nodeId={1} scenarios={[cold]}/>);
  assert.ok(div.textContent.includes('Historical metric unavailable: saved trade evidence is missing or incomplete'));
  assert.ok(!div.textContent.includes('Authoritative saved-trade calculation available'));
  console.log('PASS missing historical evidence is unavailable rather than mathematical N/A');
  await unmount();
  mode='listing';requests=[];localStorage.clear();await mount(<DeepBacktest/>);
  const row=[...div.querySelectorAll('tr')].find(r=>r.textContent.includes('Node #1'));
  assert.ok(row,'saved DeepTest row remains present');
  assert.ok(row.textContent.includes('Unverified'));
  assert.ok(!requests.some(u=>u.includes('/api/deeptest/run/')));
  console.log('PASS dashboard financial cells say Unverified without launching detailed history requests');
} finally {
  if(div?.isConnected)await unmount();
}
