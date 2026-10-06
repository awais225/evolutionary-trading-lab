import React, { useState } from "react";
import { fmt } from "../api.js";

export function Metric({ label, value, sub, color }) {
  return (
    <div className="panel metric-card">
      <div className="label">{label}</div>
      <div className="value" style={color ? { color } : undefined}>{value}</div>
      {sub && <div className="delta">{sub}</div>}
    </div>
  );
}

export function Pill({ status }) {
  return <span className={"pill " + (status || "")}>{status || "–"}</span>;
}

export function Card({ children, style, className }) {
  return <div className={"panel " + (className || "")} style={style}>{children}</div>;
}

export function SignedNum({ v, pct, digits = 2 }) {
  if (v == null || Number.isNaN(v)) return <span className="muted">–</span>;
  const cls = v > 0 ? "pos" : v < 0 ? "neg" : "muted";
  return <span className={cls}>{pct ? fmt.pct(v, digits) : fmt.signed(v, digits)}</span>;
}

/* V4.8 QA: a raw payload is a diagnostic, not the page. Rendering every byte of
 * a /strategies/{id}/economics response dumped ~146 kB of JSON into the DOM and
 * pushed the actual analysis off-screen, so the raw view is now collapsed by
 * default, states its own size, and stays one click away (with copy). */
export function JsonView({ data, maxHeight, defaultOpen = false, label = "raw JSON" }) {
  const [open, setOpen] = useState(defaultOpen);
  const [copied, setCopied] = useState("");
  let text = "";
  try {
    text = JSON.stringify(data, null, 2) ?? "null";
  } catch (e) {
    text = `/* payload could not be serialised: ${e && e.message ? e.message : e} */`;
  }
  const kb = (text.length / 1024).toFixed(1);
  const lines = text.split("\n").length;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied("copied");
    } catch {
      setCopied("copy blocked by the browser");
    }
    setTimeout(() => setCopied(""), 2000);
  };
  if (!open) {
    return (
      <div className="kit-kv" style={{ alignItems: "center" }}>
        <span className="k">
          {label} — {lines.toLocaleString()} lines, {kb} kB (hidden so it does not flood the page)
        </span>
        <span style={{ display: "flex", gap: 6 }}>
          <button className="btn btn-xs" onClick={() => setOpen(true)}>show raw JSON</button>
          <button className="btn btn-xs" onClick={copy}>copy</button>
          {copied && <span className="muted" style={{ fontSize: 11 }}>{copied}</span>}
        </span>
      </div>
    );
  }
  return (
    <div>
      <div className="kit-kv" style={{ marginBottom: 4 }}>
        <span className="k">{label} — {lines.toLocaleString()} lines, {kb} kB</span>
        <span style={{ display: "flex", gap: 6 }}>
          <button className="btn btn-xs" onClick={copy}>copy</button>
          <button className="btn btn-xs" onClick={() => setOpen(false)}>hide</button>
          {copied && <span className="muted" style={{ fontSize: 11 }}>{copied}</span>}
        </span>
      </div>
      <pre className="json" style={maxHeight ? { maxHeight } : undefined}>
        {text}
      </pre>
    </div>
  );
}

export function EventFeed({ events, limit = 60, filter }) {
  const shown = (filter ? events.filter(filter) : events).slice(0, limit);
  return (
    <div className="event-feed">
      {shown.map((e, i) => (
        <div className="ev" key={i}>
          <span className="t">{fmt.ts(e.ts)}</span>
          <span className="ty">{e.type}</span>
          <span className="muted" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {summarize(e)}
          </span>
        </div>
      ))}
      {!shown.length && <div className="ev muted">waiting for events…</div>}
    </div>
  );
}

function summarize(e) {
  const p = e.payload || {};
  switch (e.type) {
    case "strategy_survived": return `#${p.id} fitness=${p.fitness} trades=${p.trades} pf=${p.pf}`;
    case "strategy_failed": return `#${p.id} ${(p.reasons || []).join("; ")}`;
    case "strategy_killed": return `#${p.id} [${p.stage}] ${(p.reasons || []).join("; ")}`;
    case "detail_result": return `#${p.id} PF ${p.pf} ret ${fmt.pct(p.return_pct)} DD ${fmt.pct(p.dd)} (${p.trades} trades) — ${p.desc || ""}`;
    case "strategy_qualified": return `#${p.id} QUALIFIED robustness=${p.robustness} — ${p.desc || ""}`;
    case "validation_started": return `#${p.id} entering validation battery`;
    case "births": return `mutation=${p.mutation} crossover=${p.crossover} exploration=${p.exploration} duplicates_blocked=${p.duplicate}`;
    case "population_seeded": return `generation 0 seeded with ${p.n} random genomes`;
    case "generation_complete": return `gen ${p.generation}: qualified=${p.qualified} survived=${p.survived} killed=${p.killed} dup=${p.duplicates_blocked} best_fit=${p.best_fitness}`;
    case "hypothesis_created": return `#${p.strategy_id} [${p.action}] ${p.hypothesis}`;
    case "hypothesis_applied": return `hypothesis #${p.hypothesis_id} → child strategy #${p.child_strategy_id}`;
    case "specialized_child": return `#${p.parent} → #${p.child} [${p.action}] ${p.reason}`;
    case "matrices_ready": return `diagnostic matrices computed for #${p.id}`;
    case "paper_signal": return `strategy #${p.strategy_id} ${p.side} signal (feed=${p.source})`;
    case "paper_order": return `#${p.strategy_id} ${p.side} ${p.ok ? "filled" : "FAILED"} @${p.price} delay=${Math.round(p.delay_ms)}ms slip=${fmt.num(p.slippage_points)}pt`;
    case "paper_exit": return `#${p.strategy_id} closed (${p.reason}) PnL ${fmt.signed(p.pnl)}`;
    case "paper_rejected": return `#${p.strategy_id} REJECTED by risk layer: ${p.reason}`;
    case "paper_divergence": return `#${p.strategy_id} paper WR ${p.paper_win_rate} vs backtest ${p.backtest_win_rate} — flagged`;
    case "lab_state": return `${p.action} (mode=${p.mode})`;
    case "datasets_ready": return "historical datasets ingested & cached";
    case "features_cached": return `${p.dataset_id}: ${p.features} feature arrays cached`;
    case "kill_switch": return p.on ? "KILL SWITCH ENGAGED" : "kill switch released";
    case "recalibrated": return `execution model recalibrated: ${JSON.stringify(p)}`;
    default: return JSON.stringify(p).slice(0, 140);
  }
}

export function Spinner() {
  return <div className="muted" style={{ padding: 20 }}>loading…</div>;
}

export function ErrorNote({ err }) {
  if (!err) return null;
  return <div className="warn-banner" style={{ background: "#3a1414", borderColor: "#7f1d1d", color: "#fca5a5" }}>{String(err)}</div>;
}

export function MatrixTable({ title, data, valueKeys }) {
  if (!data || !Object.keys(data).length) return null;
  const keys = valueKeys || ["trades", "pnl", "pf", "win_rate"];
  return (
    <div className="panel" style={{ marginBottom: 12 }}>
      <h3>{title}</h3>
      <table className="tbl matrix-tbl">
        <thead><tr><th></th>{keys.map((k) => <th key={k}>{k}</th>)}</tr></thead>
        <tbody>
          {Object.entries(data).map(([row, st]) => (
            <tr key={row} style={{ cursor: "default" }}>
              <td><b>{row}</b></td>
              {keys.map((k) => {
                const v = st?.[k];
                let cls = "";
                if (k === "pnl") cls = v > 0 ? "good" : v < 0 ? "bad" : "";
                if (k === "pf") cls = v > 1.2 ? "good" : v != null && v < 1 ? "bad" : "";
                return <td key={k} className={cls}>{v == null ? "–" : typeof v === "number" ? fmt.num(v, k === "pf" || k === "win_rate" ? 2 : 1) : String(v)}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
