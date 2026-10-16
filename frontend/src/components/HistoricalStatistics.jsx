import React from 'react';
import {useStoredState} from '../lib/storedState.js';
export function useHistoricalSource(){return useStoredState('v93.historicalSource','effective');}
export function HistoricalSource({value,onChange}){return <label>Historical statistics source <select aria-label="Historical statistics source" value={value} onChange={e=>onChange(e.target.value)}><option value="effective">Valid Deep result, otherwise labelled generation</option><option value="generation">Generation record only</option><option value="deeptest">Deep results only (no fallback)</option></select></label>;}
export function historicalRank(a,b,descending=true){
 const val=r=>{const v=r.historical_statistics?.metrics?.win_rate??r.metrics?.win_rate;return typeof v==='number'&&Number.isFinite(v)?v:null;};
 const x=val(a),y=val(b);if(x===null||y===null)return x===y?Number(a.node_id)-Number(b.node_id):x===null?1:-1;
 const ap=a.historical_statistics?.status==='PARTIAL',bp=b.historical_statistics?.status==='PARTIAL';if(ap!==bp)return ap?1:-1;
 return (descending?y-x:x-y)||Number(a.node_id)-Number(b.node_id);
}
const date=v=>{if(v==null)return 'Range unavailable';const d=new Date(typeof v==='number'?v*1000:v);return Number.isFinite(d.getTime())?d.toISOString():'Range unavailable';};
const number=(v,pct=false)=>typeof v==='number'&&Number.isFinite(v)?`${(pct?v*100:v).toLocaleString(undefined,{maximumFractionDigits:pct?2:3})}${pct?' %':''}`:'N/A';
export function HistoricalCells({row,sourceOnly=false}){
 const h=row.historical_statistics,p=h?.provenance||{},m=h?.metrics||row.metrics||{};
 const label=h?`${h.source} · ${h.status}`:(row.metrics_source_label||'Source unavailable');
 const title=JSON.stringify({...p,node_id:row.node_id,source:h?.source,status:h?.status},null,2);
 if(sourceOnly)return <td title={title}><small>{label}<br/>{p.scenario_id||p.research_run_id||'No run provenance'}<br/>{date(p.start_ts)} → {date(p.end_ts)}</small></td>;
 return <><td className="mono" title={title}>{number(m.return_pct,true)}</td><td className="mono" title={title}>{number(m.profit_factor)}</td><td className="mono" title={title}>{number(m.max_drawdown_pct,true)}</td><td className="mono" title={title}>{number(m.win_rate,true)}</td><td className="mono" title={title}>{number(m.trades)}</td><HistoricalCells row={row} sourceOnly/></>;
}
