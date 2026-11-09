import { useEffect, useState } from 'react'
import { api } from '../api/client'

const gb=(n?:number)=>typeof n==='number'?(n/1e9).toFixed(1):'N/A'

/** Persistent compact readout for the machine hosting the backend (LMS preview host in sandbox). */
export function SystemMonitor(){
  const [m,setM]=useState<any>(null)
  useEffect(()=>{let live=true;const poll=()=>api.systemMetrics().then(v=>{if(live)setM(v)}).catch(()=>{if(live)setM(null)});poll();const id=window.setInterval(poll,2000);return()=>{live=false;window.clearInterval(id)}},[])
  const c=m?.cpu_ram,g=m?.gpu
  return <div className="system-monitor" role="region" aria-label="System resource monitoring" title={`Metrics describe the ${m?.scope??'API host'}, not the user's local computer`}>
    <span className="system-monitor-scope">API HOST</span>
    <span>CPU <b>{typeof c?.utilization_pct==='number'?`${Math.round(c.utilization_pct)}%`:'N/A'}</b>{c?.logical_threads?` · ${c.logical_threads} threads`:''}</span>
    <span>RAM <b>{typeof c?.ram_utilization_pct==='number'?`${Math.round(c.ram_utilization_pct)}%`:''}</b> {c?.ram_used_bytes!=null?`${gb(c.ram_used_bytes)} / ${gb(c.ram_total_bytes)} GB`:'unavailable'}</span>
    <span aria-label="GPU monitoring">GPU <b>{g?.available?`${g.utilization_pct}%`:'unavailable'}</b>{g?.available?` · VRAM ${gb(g.vram_used_bytes)}/${gb(g.vram_total_bytes)} GB${g.temperature_c!=null?` · ${g.temperature_c}°C`:''}`:' · no sandbox GPU telemetry'}</span>
  </div>
}
