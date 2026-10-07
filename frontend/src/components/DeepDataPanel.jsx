// V5.2 §11–§14 — DEEP TESTING DATA
//
// The block above the node table that makes Deep Testing honest and runnable:
//
//   §11  Data Requirements  — computed over ALL deep-eligible nodes (symbol,
//        timeframe, earliest/latest date, fields, warm-up, required bars).
//   §12  Suggested Data     — the UNION of those requirements, shown before
//        anything is downloaded.
//   §13  GET MT5 DATA       — one button, staged progress (DISCOVERING
//        REQUIREMENTS → … → NODES READY) with requested vs received vs stored
//        bars per symbol/timeframe, current operation and errors, mirrored into
//        the global live progress strip by the backend job.
//   §14  Readiness          — DATA READY + X/Y NODES READY; each node either
//        READY or NOT READY with its reason. The node table only enables a deep
//        run for a READY node.
//
// Nothing in this component downloads anything by itself and nothing fabricates
// a number: every value comes from the backend endpoints, and a value the
// backend cannot compute is rendered as "unknown".
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { Card, Progress } from "./ui";
import { arr, txt } from "../lib/safe.js";

const TONE = {
  READY: "var(--green)",
  "NOT READY": "var(--red)",
  RUNNING: "var(--accent)",
  IDLE: "var(--muted)",
  COMPLETED: "var(--green)",
  FAILED: "var(--red)",
  CANCELLED: "var(--muted)",
};

function fmtInt(v) {
  return v == null ? "unknown" : Number(v).toLocaleString();
}

export function StageRail({ stages, current, pct }) {
  const idx = current ? arr(stages).indexOf(current) : -1;
  return (
    <div className="row" style={{ gap: 6, flexWrap: "wrap", alignItems: "center" }}>
      {arr(stages).map((s, i) => {
        const done = idx >= 0 && i < idx;
        const active = i === idx;
        return (
          <span key={s} className="mono" style={{
            fontSize: 9.5, padding: "2px 6px", borderRadius: 999,
            border: `1px solid ${active ? "var(--accent)" : "var(--line)"}`,
            color: active ? "var(--accent)" : done ? "var(--green)" : "var(--muted)",
            background: "transparent", whiteSpace: "nowrap",
          }} title={active ? `current stage${pct != null ? ` · ${pct}%` : ""}` : s}>
            {done ? "✓ " : active ? "● " : ""}{s}
          </span>
        );
      })}
    </div>
  );
}

export default function DeepDataPanel({ onReadiness }) {
  const [state, setState] = useState(null);
  const [req, setReq] = useState(null);
  const [sug, setSug] = useState(null);
  const [job, setJob] = useState(null);
  const [ready, setReady] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(true);
  const poll = useRef(null);

  const load = useCallback(async () => {
    try {
      const st = await api.deepTestingState();
      setState(st);
      setReady(st.readiness || null);
      setJob(st.get_data || null);
      if (onReadiness) onReadiness(st.readiness || null);
    } catch (e) {
      setError(e.message || String(e));
    }
  }, [onReadiness]);

  const loadDetail = useCallback(async () => {
    try {
      const [r, s] = await Promise.all([api.deepTestingRequirements(), api.deepTestingSuggested()]);
      setReq(r); setSug(s);
    } catch (e) { setError(e.message || String(e)); }
  }, []);

  useEffect(() => { load(); loadDetail(); }, [load, loadDetail]);

  const jobStatus = job?.status;
  useEffect(() => {
    if (poll.current) { clearInterval(poll.current); poll.current = null; }
    const running = jobStatus === "RUNNING";
    poll.current = setInterval(async () => {
      try {
        const st = await api.deepTestingGetDataStatus();
        setJob(st);
        if (st.status !== "RUNNING") {
          const r = await api.deepTestingReadiness();
          setReady(r);
          if (onReadiness) onReadiness(r);
          await load();
        }
      } catch { /* polling is best-effort; the panel stays readable */ }
    }, running ? 1200 : 6000);
    return () => { if (poll.current) clearInterval(poll.current); };
  }, [jobStatus, load, onReadiness]);

  const start = async () => {
    setBusy(true); setError("");
    try {
      const res = await api.deepTestingGetDataStart({});
      setJob(res.status || null);
      await load();
    } catch (e) { setError(e.message || String(e)); } finally { setBusy(false); }
  };

  const cancel = async () => {
    try { setJob((await api.deepTestingGetDataCancel()).status || null); } catch (e) { setError(e.message || String(e)); }
    finally { await load(); }
  };

  const agg = req?.aggregate || {};
  const readinessRows = useMemo(() => arr(ready?.nodes), [ready]);
  const notReady = readinessRows.filter((n) => !n.ready);

  return (
    <Card title="DEEP TESTING — DATA REQUIREMENTS & READINESS"
          right={<span className="muted" style={{ fontSize: 10 }}>
            {ready ? ready.nodes_status : "checking…"} · {ready ? ready.data_status : ""}
          </span>}>
      <div className="row" style={{ gap: 10, flexWrap: "wrap", alignItems: "center", marginBottom: 6 }}>
        <button className="btn ghost" style={{ fontSize: 11 }} onClick={() => setOpen((v) => !v)}>
          {open ? "Hide details" : "Show details"}
        </button>
        <button className="btn ghost" style={{ fontSize: 11 }} onClick={() => { load(); loadDetail(); }}>Refresh</button>
        <button className="btn primary" style={{ fontSize: 11 }} disabled={busy || job?.status === "RUNNING"}
                onClick={start}
                title={job?.status === "RUNNING" ? "a GET MT5 DATA job is running" : "fetch the suggested package through the existing MT5 historical path"}>
          {job?.status === "RUNNING" ? "GET MT5 DATA — running…" : "GET MT5 DATA"}
        </button>
        {job?.status === "RUNNING" && (
          <button className="btn ghost" style={{ fontSize: 11 }} onClick={cancel}>Cancel</button>
        )}
        <span className="muted" style={{ fontSize: 10 }}>
          uses the existing MT5 historical infrastructure only — no second downloader, no fabricated bars
        </span>
      </div>

      {error && <div className="error-note" style={{ marginBottom: 6 }}>{error}</div>}

      {/* §14 — the gate the node table reads */}
      <div className="kit-strip" style={{ border: "none", padding: 0, flexWrap: "wrap" }}>
        <div className="item"><span className="k">Data</span>
          <span className="v mono" style={{ color: TONE[ready?.data_status] || "var(--muted)" }}>
            {ready ? ready.data_status : "not checked"}
          </span></div>
        <div className="item"><span className="k">Nodes ready</span>
          <span className="v mono">{ready ? ready.nodes_status : "—"}</span></div>
        <div className="item"><span className="k">Deep-eligible</span>
          <span className="v mono">{txt(state?.populations?.state?.DEEP_TESTING_ELIGIBLE, "—")}</span></div>
        <div className="item"><span className="k">Package items</span>
          <span className="v mono">{arr(agg.items).length}</span></div>
        <div className="item"><span className="k">Warm-up</span>
          <span className="v mono">{txt(agg.warmup_bars, "—")} bars</span></div>
        <div className="item"><span className="k">Union window</span>
          <span className="v mono">{txt(agg.date_range?.start, "—")} → {txt(agg.date_range?.end, "—")}</span></div>
      </div>

      {open && (
        <>
          <div style={{ marginTop: 8 }}>
            <div className="muted" style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: 1 }}>
              §11 data requirements (computed over all deep-eligible nodes)
            </div>
            <table className="tbl" style={{ marginTop: 4 }}>
              <thead>
                <tr><th>Symbol</th><th>Timeframe</th><th>Earliest</th><th>Latest</th>
                    <th>Fields</th><th>Warm-up</th><th>Required bars</th><th>Nodes needing it</th></tr>
              </thead>
              <tbody>
                {arr(agg.items).map((it) => (
                  <tr key={`${it.symbol}|${it.timeframe}`}>
                    <td className="mono">{it.symbol}</td>
                    <td className="mono">{it.timeframe}</td>
                    <td className="mono" style={{ fontSize: 10 }}>{txt(it.earliest, "—")}</td>
                    <td className="mono" style={{ fontSize: 10 }}>{txt(it.latest, "—")}</td>
                    <td className="mono" style={{ fontSize: 10 }}>{arr(it.fields).join(", ")}</td>
                    <td className="mono">{it.warmup_bars}</td>
                    <td className="mono">{fmtInt(it.required_bars)}
                      {it.required_bars_reason ? <span className="muted"> ({it.required_bars_reason})</span> : null}</td>
                    <td className="mono">{it.node_count}</td>
                  </tr>
                ))}
                {!arr(agg.items).length && (
                  <tr><td colSpan={8} className="muted">No deep-eligible node asks for data (nothing to fetch).</td></tr>
                )}
              </tbody>
            </table>
            <div className="muted" style={{ fontSize: 10, marginTop: 3 }}>
              per-node requirements: {txt(req?.nodes_with_requirements, "—")} of {txt(req?.nodes_total, "—")} eligible nodes
              {arr(req?.nodes_ineligible).length ? ` · ${arr(req.nodes_ineligible).length} node(s) have no symbol/timeframe` : ""}
            </div>
          </div>

          <div style={{ marginTop: 10 }}>
            <div className="muted" style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: 1 }}>
              §12 suggested data (union of every eligible node's requirement)
            </div>
            <div className="row" style={{ gap: 8, flexWrap: "wrap", marginTop: 4 }}>
              {arr(sug?.symbols).map((s) => (
                <span key={s.symbol} className="mono" style={{ fontSize: 11, padding: "3px 8px",
                        border: "1px solid var(--line)", borderRadius: 6 }}>
                  {s.symbol} · {arr(s.timeframes).join(" / ")} · {s.earliest} → {s.latest} · {fmtInt(s.required_bars)} bars · {s.node_count} node(s)
                </span>
              ))}
              {!arr(sug?.symbols).length && <span className="muted" style={{ fontSize: 11 }}>no union yet</span>}
            </div>
            <div className="muted" style={{ fontSize: 10, marginTop: 3 }}>{sug?.note}</div>
          </div>

          <div style={{ marginTop: 10 }}>
            <div className="muted" style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: 1 }}>
              §13 get MT5 data
            </div>
            <div className="row" style={{ gap: 8, alignItems: "center", marginTop: 4, flexWrap: "wrap" }}>
              <span className="mono" style={{ fontSize: 10, color: TONE[job?.status] || "var(--muted)" }}>
                ● {txt(job?.status, "IDLE")}
              </span>
              {job?.elapsed_s != null && <span className="muted" style={{ fontSize: 10 }}>{job.elapsed_s}s</span>}
              {job?.current_operation && <span className="mono" style={{ fontSize: 10 }}>{job.current_operation}</span>}
              <span className="muted" style={{ fontSize: 10 }}>
                requested {fmtInt(job?.totals?.requested_bars)} · received {fmtInt(job?.totals?.received_bars)} ·
                {" "}stored {fmtInt(job?.totals?.stored_bars)} bars · {txt(job?.totals?.items_done, 0)}/{txt(job?.totals?.items_total, 0)} items
              </span>
            </div>
            {job?.status && job.status !== "IDLE" && (
              <div style={{ marginTop: 4 }}>
                <Progress pct={job?.pct ?? null} label={job?.stage || job?.status} />
                <div style={{ marginTop: 4 }}>
                  <StageRail stages={job?.stages || state?.stages} current={job?.stage} pct={job?.pct} />
                </div>
              </div>
            )}
            {arr(job?.items).length > 0 && (
              <table className="tbl" style={{ marginTop: 6 }}>
                <thead>
                  <tr><th>Symbol</th><th>TF</th><th>Requested range</th><th>Requested bars</th>
                      <th>Received</th><th>Stored</th><th>Stored range</th><th>Stage</th><th>Status</th></tr>
                </thead>
                <tbody>
                  {arr(job.items).map((it) => (
                    <tr key={`${it.symbol}|${it.timeframe}`}>
                      <td className="mono">{it.symbol}</td>
                      <td className="mono">{it.timeframe}</td>
                      <td className="mono" style={{ fontSize: 10 }}>{txt(it.requested_start, "—")} → {txt(it.requested_end, "—")}</td>
                      <td className="mono">{fmtInt(it.requested_bars)}</td>
                      <td className="mono">{fmtInt(it.received_bars)}</td>
                      <td className="mono">{fmtInt(it.stored_bars)}</td>
                      <td className="mono" style={{ fontSize: 10 }}>{txt(it.stored_start, "—")} → {txt(it.stored_end, "—")}</td>
                      <td className="mono" style={{ fontSize: 10 }}>{txt(it.stage, "—")}</td>
                      <td className="mono" style={{ fontSize: 10, color: it.error ? "var(--red)" : "var(--muted)" }}>
                        {it.error ? `error: ${it.error}` : txt(it.status, "—")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {arr(job?.errors).length > 0 && (
              <ul style={{ margin: "4px 0 0 16px", fontSize: 11 }}>
                {arr(job.errors).slice(0, 8).map((e, i) => <li key={i} className="mono">{e}</li>)}
              </ul>
            )}
            <div className="muted" style={{ fontSize: 10, marginTop: 3 }}>{job?.note}</div>
          </div>

          <div style={{ marginTop: 10 }}>
            <div className="muted" style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: 1 }}>
              §14 readiness — every node READY or NOT READY with its reason
            </div>
            <div className="muted" style={{ fontSize: 10, marginTop: 2 }}>{ready?.note}</div>
            {notReady.length > 0 && (
              <table className="tbl" style={{ marginTop: 4 }}>
                <thead><tr><th>Node</th><th>Symbol</th><th>TF</th><th>Stored bars</th><th>Required</th><th>Why not ready</th></tr></thead>
                <tbody>
                  {notReady.slice(0, 25).map((n) => (
                    <tr key={n.node_id}>
                      <td className="mono">Node_{n.node_id}</td>
                      <td className="mono">{txt(n.symbol, "—")}</td>
                      <td className="mono">{txt(n.timeframe, "—")}</td>
                      <td className="mono">{fmtInt(n.stored_bars)}</td>
                      <td className="mono">{fmtInt(n.required_bars)}</td>
                      <td className="muted" style={{ fontSize: 10 }}>{txt(n.reason, "—")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {notReady.length === 0 && ready?.nodes_total > 0 && (
              <div className="muted" style={{ fontSize: 11 }}>every eligible node has the data its own genome needs</div>
            )}
            {notReady.length > 25 && (
              <div className="muted" style={{ fontSize: 10 }}>showing 25 of {notReady.length} nodes that are not ready</div>
            )}
          </div>
        </>
      )}
    </Card>
  );
}
