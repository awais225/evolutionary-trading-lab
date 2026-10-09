/* V4.8 — shared node detail drawer.
 *
 * Used by Strategy Lab (and reachable from other pages) to show everything the
 * backend knows about one node without recomputing anything in React:
 *   identity / status / lineage  <- /api/strategies/{sid}/economics (authoritative)
 *   research + execution records <- /api/strategies/{sid}          (V4.4 surface)
 *   equity + drawdown + trades   <- /api/strategies/{sid}/charts
 *   raw payload                  <- the endpoints above, verbatim
 *
 * Every section handles loading / empty / error independently, so a failing
 * sub-request never blanks the whole drawer.
 */
import React, { useEffect, useState } from "react";
import { Area, AreaChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, fmt } from "../api.js";
import { arr, NA_TEXT, numOrNull, objOrNull, txt } from "../lib/safe.js";
import { Badge, Kpi, SectionTitle, StateBlock, Star, fmtId } from "./ui.jsx";
import { JsonView } from "./common.jsx";
import NodeResearchDetail from "./NodeResearchDetail.jsx";

const TABS = [
  ["overview", "Identity & metrics"],
  ["research", "Research & execution"],
  ["equity", "Equity curve"],
  ["lineage", "Lineage"],
  ["raw", "Raw data"],
];

export default function NodeDetailDrawer({ id, onClose, onOpenStrategy, initialTab = "overview" }) {
  const [tab, setTab] = useState(initialTab);
  const [econ, setEcon] = useState(null);
  const [detail, setDetail] = useState(null);
  const [charts, setCharts] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(null);
  const [econErr, setEconErr] = useState(null);
  const [chartErr, setChartErr] = useState(null);
  const [detailErr, setDetailErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState(null);

  useEffect(() => {
    if (id === null || id === undefined) return;
    let alive = true;
    setLoading(true); setErr(null); setEconErr(null); setChartErr(null); setDetailErr(null);
    setEcon(null); setDetail(null); setCharts(null);
    Promise.allSettled([
      api.nodeEconomics(id).then((d) => alive && setEcon(d)).catch((e) => alive && setEconErr(e.message || String(e))),
      api.strategy(id).then((d) => alive && setDetail(d)).catch((e) => alive && setDetailErr(e.message || String(e))),
      api.strategyCharts(id).then((d) => alive && setCharts(d)).catch((e) => alive && setChartErr(e.message || String(e))),
    ]).then(() => alive && setLoading(false));
    return () => { alive = false; };
  }, [id]);

  const n = objOrNull(econ) || {};
  const metrics = objOrNull(n.metrics) || {};
  const returns = objOrNull(n.returns) || {};
  const pfs = objOrNull(n.profit_factors) || {};
  const backtest = objOrNull(metrics.backtest) || {};
  const validation = objOrNull(metrics.validation) || {};
  const liveT = objOrNull(metrics.live_test) || {};
  const demoT = objOrNull(metrics.mt5_demo) || {};
  const children = arr(n.children);
  const equityRows = arr(charts?.equity_curve).map((p) => ({ t: arr(p)[0], eq: arr(p)[1] }));
  const ddRows = arr(charts?.drawdown_curve).map((p) => ({ t: arr(p)[0], dd: numOrNull(arr(p)[1]) }));

  const toggleShortlist = async () => {
    setBusy(true); setNote(null);
    try {
      const res = await api.toggleShortlist(id);
      setNote({ ok: true, text: res?.shortlisted ? "Added to the shortlist." : "Removed from the shortlist." });
      const d = await api.nodeEconomics(id);
      setEcon(d);
    } catch (e) {
      setNote({ ok: false, text: `Shortlist update failed: ${e.message || e}` });
    } finally { setBusy(false); }
  };

  const exportProfile = () => {
    try {
      const payload = {
        exported_at: new Date().toISOString(),
        source: "GET /api/strategies/{id}/economics (authoritative node model)",
        node: n,
        research_detail: objOrNull(detail),
      };
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = `node_${id}_research_profile.json`;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
      setNote({ ok: true, text: `Research profile for node ${id} exported.` });
    } catch (e) {
      setNote({ ok: false, text: `Export failed: ${e.message || e}` });
    }
  };

  if (id === null || id === undefined) return null;

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-label={`Node ${id} details`}>
        <div className="kit-head">
          <div>
            <div style={{ fontSize: 15, fontWeight: 650 }}>
              {fmtId(id)}{" "}
              {n.status ? <Badge tone={/QUALIFIED|PAPER/.test(String(n.status)) ? "ok" : /FAIL|KILL|RETIRED/.test(String(n.status)) ? "danger" : "info"}>{n.status}</Badge> : null}
            </div>
            <div className="muted" style={{ fontSize: 11.5 }}>
              {txt(n.symbol, "—")} {txt(n.timeframe, "")} · generation {txt(n.generation, "—")} · run {txt(n.run_id, "—")}
            </div>
          </div>
          <div className="kit-cols">
            <Star on={Boolean(n.shortlisted)} onClick={toggleShortlist} disabled={busy} />
            <button className="btn" onClick={exportProfile}>Export research profile</button>
            <button className="btn" onClick={onClose}>✕ close</button>
          </div>
        </div>

        {note && <div className={note.ok ? "kit-ok" : "kit-inline-err"}>{note.text}</div>}
        {econErr && <div className="kit-inline-err">Authoritative model unavailable: {econErr}</div>}

        <div className="tabs" style={{ marginTop: 10 }}>
          {TABS.map(([k, label]) => (
            <button key={k} className={tab === k ? "active" : ""} onClick={() => setTab(k)}>{label}</button>
          ))}
        </div>

        <StateBlock loading={loading} error={!econ && !detail ? err : null}>
          {tab === "overview" && (
            <div>
              <div className="grid cols-4" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(150px,1fr))" }}>
                {/* V6.4 — every *_return_pct field is a FRACTION (0.0671 = 6.71%),
                    same convention as the backtest metrics; fmt.pct multiplies by
                    100. The pre-V6.4 /100 here rendered 6.71% as 0.07%. */}
                <Kpi label="OOS Return" value={returns.validation_oos_return_pct === null || returns.validation_oos_return_pct === undefined ? NA_TEXT : fmt.pct(Number(returns.validation_oos_return_pct), 2)} />
                <Kpi label="Profit Factor (ratio)" value={pfs.oos === null && pfs.in_sample === null ? NA_TEXT : fmt.num(pfs.oos ?? pfs.in_sample, 2)} />
                <Kpi label="Max Drawdown" value={backtest.max_drawdown_pct === undefined && validation.max_drawdown_pct === undefined ? NA_TEXT : fmt.pct(validation.max_drawdown_pct ?? backtest.max_drawdown_pct, 2)} tone="warn" />
                <Kpi label="Robustness" value={n.robustness_score === null || n.robustness_score === undefined ? NA_TEXT : fmt.num(n.robustness_score, 3)} />
                <Kpi label="Sharpe" value={backtest.sharpe === undefined && validation.sharpe === undefined ? NA_TEXT : fmt.num(validation.sharpe ?? backtest.sharpe, 2)} />
              </div>
              <SectionTitle>Identity</SectionTitle>
              <div className="kit-cols">
                <div style={{ flex: "1 1 240px" }}>
                  <div className="kit-kv"><span className="k">Authoritative ID</span><b className="mono">{txt(n.id)}</b></div>
                  <div className="kit-kv"><span className="k">Research index</span><span className="mono">{txt(n.research_node_num, "—")}</span></div>
                  <div className="kit-kv"><span className="k">Run ID</span><span className="mono">{txt(n.run_id, "—")}</span></div>
                  <div className="kit-kv"><span className="k">Dataset</span><span className="mono">{txt(n.dataset_id, "—")}</span></div>
                </div>
                <div style={{ flex: "1 1 240px" }}>
                  <div className="kit-kv"><span className="k">Status</span><span>{txt(n.status)}</span></div>
                  <div className="kit-kv"><span className="k">Pipeline stage</span><span>{txt(n.pipeline_stage, "—")}</span></div>
                  <div className="kit-kv"><span className="k">Symbol / timeframe</span><span>{txt(n.symbol)} {txt(n.timeframe, "")}</span></div>
                  <div className="kit-kv"><span className="k">Direction</span><span>{txt(n.direction)}</span></div>
                </div>
              </div>
              <SectionTitle>Stage snapshot</SectionTitle>
              <div className="kit-cols">
                {[["Backtest", backtest], ["Validation", validation], ["Live test", liveT], ["MT5 demo", demoT]].map(([label, m]) => (
                  <div className="kit-stage-card" key={label} style={{ flex: "1 1 170px" }}>
                    <div className="hd"><span className="nm">{label}</span></div>
                    <div className="kit-kv"><span className="k">Return %</span><span>{m.total_return_pct === undefined ? NA_TEXT : fmt.pct(m.total_return_pct, 2)}</span></div>
                    <div className="kit-kv"><span className="k">P/L</span><span>{m.net_profit === undefined && m.total_pnl === undefined ? NA_TEXT : fmt.pnl(m.net_profit ?? m.total_pnl)}</span></div>
                    <div className="kit-kv"><span className="k">Trades</span><span className="mono">{m.trades === undefined && m.total_trades === undefined ? NA_TEXT : fmt.num(m.trades ?? m.total_trades, 0)}</span></div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {tab === "research" && (
            detailErr
              ? <div className="kit-inline-err">Research detail unavailable: {detailErr}</div>
              : <NodeResearchDetail node={detail} busy={loading} onOpenStrategy={onOpenStrategy} emptyHint="No research record for this node yet." />
          )}

          {tab === "equity" && (
            <div>
              {chartErr && <div className="kit-inline-err">Equity curve unavailable: {chartErr}</div>}
              {!chartErr && equityRows.length === 0 && (
                <div className="kit-state"><span className="muted">No equity curve stored for this node yet.</span></div>
              )}
              {!chartErr && equityRows.length > 1 && (
                <>
                  <SectionTitle hint={`${equityRows.length} points · ${txt(charts?.message, "stored backtest curve")}`}>Equity curve</SectionTitle>
                  <div style={{ height: 220 }}>
                    <ResponsiveContainer>
                      <AreaChart data={equityRows}>
                        <CartesianGrid stroke="#232b3d" />
                        <XAxis dataKey="t" tickFormatter={(v) => new Date(Number(v) * 1000).toLocaleDateString()} stroke="#8b93a7" fontSize={10} />
                        <YAxis stroke="#8b93a7" fontSize={10} domain={["auto", "auto"]} />
                        <Tooltip contentStyle={{ background: "#151a26", border: "1px solid #232b3d" }}
                                 labelFormatter={(v) => new Date(Number(v) * 1000).toLocaleString()} />
                        <Area type="monotone" dataKey="eq" stroke="#4f8ef7" fill="#1b2b48" name="equity" />
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                  <SectionTitle>Drawdown</SectionTitle>
                  <div style={{ height: 160 }}>
                    <ResponsiveContainer>
                      <LineChart data={ddRows}>
                        <CartesianGrid stroke="#232b3d" />
                        <XAxis dataKey="t" tickFormatter={(v) => new Date(Number(v) * 1000).toLocaleDateString()} stroke="#8b93a7" fontSize={10} />
                        <YAxis stroke="#8b93a7" fontSize={10} />
                        <Tooltip contentStyle={{ background: "#151a26", border: "1px solid #232b3d" }}
                                 labelFormatter={(v) => new Date(Number(v) * 1000).toLocaleString()} />
                        <Line type="monotone" dataKey="dd" stroke="#f87171" dot={false} name="drawdown" />
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                  <div className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>
                    {arr(charts?.trades).length} stored trades accompany this curve.
                  </div>
                </>
              )}
            </div>
          )}

          {tab === "lineage" && (
            <div>
              <SectionTitle>Parents and children</SectionTitle>
              <div className="kit-kv"><span className="k">Parent</span>
                <span>{n.parent_id ? <button className="kit-chip mono" onClick={() => onOpenStrategy && onOpenStrategy(Number(n.parent_id))}>Node #{n.parent_id}</button> : <span className="muted">{NA_TEXT} (root)</span>}</span></div>
              {children.length === 0
                ? <div className="muted" style={{ marginTop: 6 }}>No children recorded.</div>
                : <div style={{ marginTop: 6 }}>{children.map((c, i) => {
                    const cid = Number(c?.id ?? c);
                    if (!Number.isFinite(cid)) return <span className="kit-chip muted" key={i}>{NA_TEXT}</span>;
                    return <button key={cid} className="kit-chip mono" onClick={() => onOpenStrategy && onOpenStrategy(cid)}>Node #{cid}{c?.status ? <span className="muted"> · {c.status}</span> : null}</button>;
                  })}</div>}
              <SectionTitle>Why this node exists</SectionTitle>
              <div className="lineage">
                <div className="step"><b>Created</b><div className="why">{txt(n.creation_reason, "no reason recorded")}</div></div>
                <div className="step"><b>Mutation</b><div className="why">{txt(n.mutation_type, "—")}</div></div>
                <div className="step"><b>Survival</b><div className="why">{txt(n.survival_reason, "no survival note recorded")}</div></div>
              </div>
            </div>
          )}

          {tab === "raw" && (
            <div>
              <SectionTitle hint="The exact API payloads backing every tab above.">Raw data view</SectionTitle>
              <div className="muted" style={{ fontSize: 11.5, marginBottom: 4 }}>/api/strategies/{id}/economics</div>
              <JsonView data={econ} maxHeight={260} label="economics payload" />
              <div className="muted" style={{ fontSize: 11.5, margin: "10px 0 4px" }}>/api/strategies/{id}</div>
              {detailErr ? <div className="kit-inline-err">{detailErr}</div> : <JsonView data={detail} maxHeight={260} label="strategy detail payload" />}
              <div className="muted" style={{ fontSize: 11.5, margin: "10px 0 4px" }}>/api/strategies/{id}/charts</div>
              {chartErr ? <div className="kit-inline-err">{chartErr}</div> : <JsonView data={charts} maxHeight={220} label="charts payload" />}
            </div>
          )}
        </StateBlock>
      </aside>
    </>
  );
}
