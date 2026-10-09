/* V4.8 shared UI kit.
 *
 * Every dashboard page uses these primitives so the whole application has one
 * consistent vocabulary for cards, badges, states (loading / empty / error /
 * unavailable / simulator), confirmations and progress. The styling reuses the
 * existing theme tokens from styles.css - no new visual theme is introduced.
 */
import React, { useEffect, useRef, useState } from "react";
import { NA_TEXT, txt, arr } from "../lib/safe.js";

/* ------------------------------------------------------------------ layout */
export function Card({ title, right, children, className = "", style, tone }) {
  const border = tone === "danger" ? "#7f1d1d" : tone === "warn" ? "#7a5a16" : undefined;
  return (
    <div className={"panel " + className} style={{ ...(border ? { borderColor: border } : {}), ...style }}>
      {(title || right) && (
        <div className="kit-head">
          <h3 style={{ margin: 0 }}>{title}</h3>
          {right}
        </div>
      )}
      {children}
    </div>
  );
}

export function SectionTitle({ children, right, hint }) {
  return (
    <div className="kit-section">
      <div>
        <div className="kit-section-title">{children}</div>
        {hint && <div className="muted" style={{ fontSize: 11.5, marginTop: 2 }}>{hint}</div>}
      </div>
      {right}
    </div>
  );
}

/* ------------------------------------------------------------------- atoms */
const TONES = {
  ok: { bg: "#0e3a2a", fg: "#52ffa8" },
  good: { bg: "#0e3a2a", fg: "#52ffa8" },
  bad: { bg: "#401a1a", fg: "#ff8f8f" },
  danger: { bg: "#401a1a", fg: "#ff8f8f" },
  warn: { bg: "#3a2c10", fg: "#ffd982" },
  info: { bg: "#13314a", fg: "#7ec8ff" },
  accent: { bg: "#1b2b48", fg: "#9dc0ff" },
  mute: { bg: "#26282e", fg: "#9aa1b1" },
  sim: { bg: "#402f14", fg: "#ffcf6b" },
  real: { bg: "#123c22", fg: "#63ffa0" },
  violet: { bg: "#2c2440", fg: "#c4b5fd" },
};

export function Badge({ tone = "mute", children, title }) {
  const t = TONES[tone] || TONES.mute;
  return (
    <span className="kit-badge" title={title} style={{ background: t.bg, color: t.fg }}>
      {children}
    </span>
  );
}

export function Kpi({ label, value, sub, tone, title }) {
  const color = tone === "pos" ? "var(--green)" : tone === "neg" ? "var(--red)"
    : tone === "warn" ? "var(--amber)" : tone === "mute" ? "var(--muted)" : undefined;
  return (
    <div className="panel metric-card" title={title}>
      <div className="label">{label}</div>
      <div className="value" style={{ color }}>{value === undefined || value === null ? NA_TEXT : value}</div>
      {sub !== undefined && sub !== null && <div className="delta">{sub}</div>}
    </div>
  );
}

export function Progress({ pct, label, tone = "var(--accent)", height = 7 }) {
  const p = Math.max(0, Math.min(100, Number(pct) || 0));
  return (
    <div>
      {label && (
        <div className="flex justify-between" style={{ display: "flex", justifyContent: "space-between", fontSize: 11.5, color: "var(--muted)", marginBottom: 3 }}>
          <span>{label}</span>
          <span className="mono">{p.toFixed(0)}%</span>
        </div>
      )}
      <div className="kit-progress" style={{ height }}>
        <div style={{ width: `${p}%`, background: tone }} />
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ states */
/**
 * One place that renders the five states every API-driven block can be in:
 * loading, error, empty, unavailable (simulator/MT5/…) and success.
 */
export function StateBlock({ loading, error, empty, unavailable, emptyHint, errorHint, onRetry, children, compact }) {
  if (loading) {
    return (
      <div className={"kit-state" + (compact ? " compact" : "")}>
        <span className="kit-spinner" /> <span className="muted">loading…</span>
      </div>
    );
  }
  if (error) {
    return (
      <div className={"kit-state error" + (compact ? " compact" : "")}>
        <div><b>Could not load this section.</b></div>
        <div className="mono" style={{ marginTop: 4 }}>{txt(error, "unknown error")}</div>
        {errorHint && <div className="muted" style={{ marginTop: 4 }}>{errorHint}</div>}
        {onRetry && <button className="btn" style={{ marginTop: 8 }} onClick={onRetry}>Retry</button>}
      </div>
    );
  }
  if (unavailable) {
    return (
      <div className={"kit-state warn" + (compact ? " compact" : "")}>
        <div><b>Unavailable.</b></div>
        <div className="muted" style={{ marginTop: 4 }}>{txt(unavailable, "not available in this environment")}</div>
      </div>
    );
  }
  if (empty) {
    return (
      <div className={"kit-state" + (compact ? " compact" : "")}>
        <span className="muted">{emptyHint || "No data yet."}</span>
      </div>
    );
  }
  return <>{children}</>;
}

export function EmptyState({ children, compact }) {
  return <div className={"kit-state" + (compact ? " compact" : "")}><span className="muted">{children}</span></div>;
}

/* ------------------------------------------------------------------ inputs */
export function Field({ label, hint, children, width }) {
  return (
    <label className="fld" style={width ? { width } : undefined}>
      {label}
      {children}
      {hint && <span className="kit-hint">{hint}</span>}
    </label>
  );
}

export function ConfirmModal({ open, title, tone = "info", children, confirmLabel = "Confirm",
                              confirmTone = "primary", requireText, requireHint, onConfirm,
                              onCancel, busy, result, dangerText }) {
  const [typed, setTyped] = useState("");
  useEffect(() => { if (!open) setTyped(""); }, [open]);
  if (!open) return null;
  const ok = !requireText || typed.trim() === requireText;
  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="kit-head">
          <div className="modal-title" style={{ color: tone === "danger" ? "#fca5a5" : undefined }}>{title}</div>
          <button className="btn" onClick={onCancel} disabled={busy}>✕</button>
        </div>
        {dangerText && <div className="warn-banner" style={{ borderColor: "#991b1b", background: "#2a1013", color: "#fecaca" }}>{dangerText}</div>}
        <div style={{ fontSize: 13, marginBottom: 10 }}>{children}</div>
        {requireText !== undefined && (
          <Field label={requireHint || `Type ${requireText} to confirm`}>
            <input value={typed} onChange={(e) => setTyped(e.target.value)} className="mono"
                   placeholder={requireText} disabled={busy} />
          </Field>
        )}
        {result && (
          <div className={"kit-state " + (result.ok ? "" : "error")} style={{ marginTop: 8 }}>
            <div className="mono">{JSON.stringify(result).slice(0, 900)}</div>
          </div>
        )}
        <div className="btn-row" style={{ marginTop: 12, marginBottom: 0 }}>
          <button className={"btn " + confirmTone} disabled={!ok || busy} onClick={onConfirm}>
            {busy ? "working…" : confirmLabel}
          </button>
          <button className="btn" onClick={onCancel} disabled={busy}>Cancel</button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------- domain indicators */
export function SimulatorBanner({ source, detail, strict }) {
  const s = String(source || "").toUpperCase();
  const real = s.includes("MT5") && !s.includes("SIM") && s !== "";
  const sim = s.includes("SIM");
  if (real && !strict) {
    return (
      <div className="kit-banner real">
        <b>REAL MT5 DATA</b>
        <span className="muted" style={{ marginLeft: 8 }}>{txt(detail, "terminal bridge")}</span>
      </div>
    );
  }
  return (
    <div className={"kit-banner " + (sim ? "sim" : "unavail")}>
      <b>{sim ? "SIMULATOR DATA — NOT REAL MT5" : "UNAVAILABLE"}</b>
      <div className="muted" style={{ marginTop: 3, fontSize: 12 }}>
        {txt(detail, sim
          ? "The active bridge is the lab simulator. Nothing on this page is a real MetaTrader 5 result."
          : "No market-data source is available in this environment.")}
      </div>
    </div>
  );
}

/** Explicit source chip: REAL broker data / SIMULATED / UNAVAILABLE. */
export function SourceChip({ source, size }) {
  const s = String(source || "").toUpperCase();
  const tone = s.includes("SIM") ? "sim" : s.includes("MT5") || s.includes("REAL") ? "real" : "warn";
  const label = s.includes("SIM") ? "SIMULATED DATA" : s.includes("MT5") || s.includes("REAL") ? "REAL BROKER DATA" : "UNAVAILABLE";
  return <Badge tone={tone} title={`source: ${source || "unknown"}`}><span style={size ? { fontSize: size } : undefined}>{label}</span></Badge>;
}

export function StageTrack({ stages, current, onPick, disabledAfterCurrent = true }) {
  // malformed payloads must never crash a track: keep only real entries
  const list = (Array.isArray(stages) ? stages : [])
    .map((s, i) => (s && typeof s === "object"
      ? s
      : (typeof s === "string" ? { key: s, label: s } : null)))
    .filter(Boolean);
  const idx = list.findIndex((s) => s.key === current);
  return (
    <div className="kit-track">
      {list.map((s, i) => {
        const done = idx >= 0 && i < idx;
        const active = i === idx;
        const locked = disabledAfterCurrent && idx >= 0 && i > idx;
        return (
          <button
            key={s.key}
            className={"kit-track-step" + (active ? " active" : done ? " done" : locked ? " locked" : "")}
            disabled={locked || !onPick}
            title={locked ? "Complete the current stage first" : s.hint}
            onClick={() => onPick && onPick(s, i)}
          >
            <span className="dot">{done ? "✓" : active ? "●" : i + 1}</span>
            <span className="lbl">{txt(s.label, s.key)}</span>
            <span className="st">{active ? "CURRENT" : done ? "DONE" : locked ? "LOCKED" : "AVAILABLE"}</span>
          </button>
        );
      })}
    </div>
  );
}

export function Star({ on, onClick, disabled, title }) {
  return (
    <button className={"kit-star" + (on ? " on" : "")} onClick={onClick} disabled={disabled}
            title={title || (on ? "Shortlisted" : "Add to shortlist")}>
      {on ? "⭐ Shortlisted" : "☆ Add to Shortlist"}
    </button>
  );
}

/** Renders a raw value safely: N/A for missing, never undefined/NaN/[object Object] */
export function Val({ v, children }) {
  if (children !== undefined) return <>{children}</>;
  if (v === undefined || v === null || v === "") return <span className="muted">{NA_TEXT}</span>;
  if (typeof v === "object") return <span className="muted">{NA_TEXT}</span>;
  return <>{String(v)}</>;
}

/* ---------------------------------------------------------------- helpers */
export function useInterval(fn, ms, enabled = true) {
  const ref = useRef(fn);
  useEffect(() => { ref.current = fn; }, [fn]);
  useEffect(() => {
    if (!ms || !enabled) return undefined;
    const t = setInterval(() => ref.current && ref.current(), ms);
    return () => clearInterval(t);
  }, [ms, enabled]);
}

/** Parse a node id from user input: 10590, Node_10590, #10590, 10590 (any case). */
export function parseNodeId(input) {
  if (input === null || input === undefined) return null;
  if (typeof input === "number" && Number.isFinite(input)) return Math.trunc(input);
  const s = String(input).trim();
  if (!s) return null;
  const m = s.match(/(\d+)/);
  if (!m) return null;
  const n = Number(m[1]);
  return Number.isFinite(n) ? n : null;
}

export function fmtId(id) {
  const n = parseNodeId(id);
  return n === null ? NA_TEXT : `Node_${n}`;
}

/* V6.4 — ONE node-identity contract for the entire product.
 *
 * The CANONICAL identity of a node is its database primary key (`id` /
 * `node_id`) — the key every API URL, worker, live-testing config and navigation
 * target already uses. It is displayed as `Node #<id>`.
 *
 * `research_node_num` is the study-local DISPLAY number assigned when the node
 * was created inside its experiment (run_id). It is a secondary label, always
 * rendered as `research #<num>` so it can never be mistaken for the canonical
 * identity — the two namespaces overlap (the study has both `id=6658` and a
 * different node whose research number is 6658), which is exactly how "node
 * #6658" ended up pointing at different nodes on different pages.
 *
 * `generation` and `node_label` are descriptive fields, never identity.
 * Everything that labels a node uses these helpers, so the number the operator
 * reads, the row that opens, and the id the backend acts on are the same node. */
export function nodeIdentity(row) {
  if (row === null || row === undefined) return null;
  if (typeof row === "number") return row;
  const canonical = parseNodeId(row.id ?? row.node_id ?? row.strategy_id);
  return canonical === null ? parseNodeId(row.research_node_num ?? row.node_number) : canonical;
}

export function nodeLabel(row) {
  const n = nodeIdentity(row);
  return n === null ? NA_TEXT : `Node #${n}`;
}

export function researchLabel(row) {
  if (row === null || row === undefined || typeof row === "number") return null;
  const local = parseNodeId(row.research_node_num ?? row.node_number);
  return local === null ? null : `research #${local}`;
}

export function nodeLabelFull(row) {
  const primary = nodeLabel(row);
  const secondary = researchLabel(row);
  return secondary ? `${primary} · ${secondary}` : primary;
}
