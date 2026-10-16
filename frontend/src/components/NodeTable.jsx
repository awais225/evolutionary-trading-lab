import {useStoredState} from "../lib/storedState.js";
import React, { useEffect, useState, useRef } from "react";
import { api } from "../api.js";

export function numeric(value) {
  if (value == null || typeof value === "boolean" || (typeof value === "string" && !value.trim())) return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}
export function compareNodes(a, b, value, direction = 1, text = false) {
  const av = text ? value(a) : numeric(value(a));
  const bv = text ? value(b) : numeric(value(b));
  if (av == null || bv == null) return av == null && bv == null ? a.node_id - b.node_id : av == null ? 1 : -1;
  const cmp = text ? String(av).localeCompare(String(bv)) : av - bv;
  return cmp ? Math.sign(cmp) * direction : a.node_id - b.node_id;
}

/** One table implementation. The whole filtered registry is sorted before the
 * bounded render page is selected. Tabs inject their existing columns/actions;
 * paging never changes selection or launches work. No polling here. */
export default function NodeTable({ rows = [], children, className = "tbl mono", style, pageSize = 100, resetKey = "", tableKey = "nodes" }) {
  const [page, setPage] = useStoredState("page."+tableKey, 0);
  const oldReset=useRef(resetKey);
  useEffect(() => {if(oldReset.current!==resetKey){setPage(0);oldReset.current=resetKey;}}, [resetKey]);
  const last = Math.max(0, Math.ceil(rows.length / pageSize) - 1);
  const current = Math.min(page, last);
  const visible = rows.slice(current * pageSize, (current + 1) * pageSize);
  return <>
    <table className={className} style={style} data-canonical-node-table="true">{children(visible)}</table>
    {rows.length > pageSize && <nav aria-label="Node table pages" className="btn-row">
      <button className="btn" disabled={!current} onClick={() => setPage(current - 1)}>Previous</button>
      <span>{current * pageSize + 1}–{Math.min((current + 1) * pageSize, rows.length)} of {rows.length} · sorted across all matching nodes</span>
      <button className="btn" disabled={current >= last} onClick={() => setPage(current + 1)}>Next</button>
    </nav>}
  </>;
}

export function NodeIdentity({ row, onInspect }) {
  return <a className="btn ghost" href="#" onClick={(e) => { e.preventDefault(); e.stopPropagation(); onInspect?.(); }} title={`canonical ${row.node_id}; run ${row.run_id ?? "N/A"}`}>
    {row.node_label || `Node #${row.node_id}`}
    {row.research_node_num != null && <span className="muted"> · research #{row.research_node_num}</span>}
  </a>;
}

/** Durable desired-state bookmark; retry cannot invert a previous success.
 * Only the affected candidate row changes; no population reload/computation. */
export function CandidateStar({ row }) {
  const [starred, setStarred] = useState(!!row.starred);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => setStarred(!!row.starred), [row.starred]);
  useEffect(() => {
    const onStar = (e) => { if (e.detail.strategy_id === row.node_id) setStarred(e.detail.starred); };
    window.addEventListener("candidate-star-changed", onStar);
    return () => window.removeEventListener("candidate-star-changed", onStar);
  }, [row.node_id]);
  const save = async (e) => {
    e.stopPropagation();
    if (busy) return;
    setBusy(true); setError("");
    try {
      const res = await api.setCandidateStar(row.node_id, !starred);
      setStarred(res.starred);
      window.dispatchEvent(new window.CustomEvent("candidate-star-changed", { detail: res }));
    } catch (err) { setError(err.message || String(err)); }
    finally { setBusy(false); }
  };
  return <><button className="btn ghost" disabled={busy} aria-pressed={starred}
    aria-label={`${starred ? "Unstar" : "Star"} Node #${row.node_id}`}
    title="Saved candidate only — never starts live trading" onClick={save}>{busy ? "…" : starred ? "★" : "☆"}</button>
    {error && <span role="alert" className="neg">{error}</span>}</>;
}

/** Same saved generation cells on every retained node table. Scenario/effective
 * and live columns are separate injected layers; never overwrite these cells. */
export function GenerationCells({ row }) {
  const m = row.generation_metrics || {};
  const columns = ["return_pct", "net_profit", "profit_factor", "max_drawdown_pct", "win_rate", "trades"];
  const provenance = row.generation_record;
  return <>{columns.map((key) => {
    const n = numeric(m[key]);
    const percent = ["return_pct", "max_drawdown_pct", "win_rate"].includes(key);
    const display = n == null ? "N/A" : key === "trades" ? String(n) : (n * (percent ? 100 : 1)).toLocaleString(undefined, { maximumFractionDigits: 3 }) + (percent ? "%" : "");
    return <td className="mono" key={key} data-generation-metric={key}
      title={`Saved generation only · backtest ${provenance?.backtest_id ?? "N/A"} · ${provenance?.stage ?? "N/A"}`}>{display}</td>;
  })}</>;
}
