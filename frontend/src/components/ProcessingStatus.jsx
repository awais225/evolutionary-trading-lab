import React from "react";

export function generationState(lab) {
  if (!lab) return "UNAVAILABLE";
  const state = String(lab.execution_state || "").toUpperCase();
  if (["FAILED", "CANCELLED"].includes(state)) return state;
  if (lab.paused || lab.safe_paused || state === "PAUSED") return "PAUSED";
  if (lab.is_target_reached === true) return "TARGET REACHED";
  if (state === "STOPPING") return "STOPPING";
  if (lab.running === true) return "RUNNING";
  return state || (lab.running === false ? "STOPPED" : "UNAVAILABLE");
}
const value = x => typeof x === "number" && Number.isFinite(x) ? x.toLocaleString(undefined, { maximumFractionDigits: 2 }) : "N/A";
export default function ProcessingStatus({lab, resources}) {
  const cpu = resources?.cpu;
  const gpu = resources?.gpu;
  const items = [
    ["Generation state", generationState(lab)],
    ["Persisted nodes / target", `${value(lab?.current_nodes)} / ${value(lab?.target_nodes)}`],
    ["Remaining nodes", value(lab?.remaining_nodes)],
    ["Persisted nodes/min · trailing 5 min", value(lab?.nodes_per_minute)],
    ["Reported active / effective workers", `${value(cpu?.active_workers)} / ${value(cpu?.effective_workers)}`],
    ["Configured evolution worker width", value(cpu?.configured_workers ?? cpu?.workers_max)],
    ["Reported running / queued jobs", `${value(resources?.jobs?.running)} / ${value(resources?.jobs?.queued)}`],
    ["Owned long-running tasks", value(Array.isArray(lab?.owned_long_tasks ?? cpu?.owned_long_tasks) ? (lab?.owned_long_tasks ?? cpu?.owned_long_tasks).length : (lab?.owned_long_tasks ?? cpu?.owned_long_tasks))],
    ["DeepTest configured / effective process limit", `${value(resources?.deep_test?.configured_processes)} / ${value(resources?.deep_test?.effective_process_limit)}`],
    ["DeepTest admitted / admission capacity", `${value(resources?.deep_test?.admitted_jobs)} / ${value(resources?.deep_test?.admission_capacity)}`],
    ["DeepTest observed active CPU processes", value(resources?.deep_test?.observed_active_processes)],
    ["Current operation", lab?.current_operation || "N/A"],
  ];
  return <section className="panel" aria-label="Generation and processing status" style={{marginBottom:14}}>
    <h3>Generation & processing</h3>
    <div className="grid cols-4">{items.map(([label,v]) => <div key={label} style={{padding:8}}><div className="muted" style={{fontSize:11}}>{label}</div><strong>{v}</strong></div>)}</div>
    <div className="muted" style={{fontSize:12,marginTop:8}}>Target reached refers to generation only; evaluation may continue. Throughput is persisted research over five minutes, not an instantaneous speed or a benchmark. Worker limits are capacity, not CPU utilization. DeepTest admission includes queued/preparing/computing/persisting jobs; observed busy process counts are unavailable.</div>
    <div style={{fontSize:12,marginTop:6}}>Historical compute: {gpu?.historical_compute_backend || "N/A"} · application GPU compute {gpu?.application_gpu_compute_verified === true ? "verified" : "not verified"}. Device utilization (sampled at most every 30 seconds) does not demonstrate application CUDA execution.</div>
    {(lab?.last_error || lab?.safe_paused_reason) && <div role="alert" className="kit-inline-err">{lab.safe_paused_reason || lab.last_error}</div>}
  </section>;
}
