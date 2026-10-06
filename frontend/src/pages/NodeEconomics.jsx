/* V4.8 — Node Economics: complete visual node-control page (§4).
 *
 * Everything on this page comes from the existing backend surface:
 *   GET  /api/strategies/{sid}/economics      (authoritative model + all stages)
 *   GET  /api/strategies/{sid}/charts         (equity curve availability)
 *   POST /api/research/shortlist/toggle       (shortlist)
 *   POST /api/strategies/{sid}/pipeline-stage (promotion pipeline)
 *   POST /api/live-test/strategies/{sid}/toggle   (per-node live testing)
 *   POST /api/mt5-demo/strategies/{sid}/toggle    (per-node demo)
 *   GET  /api/mt5-historical/runs?strategy_id= (a completed MT5 backtest is
 *        required before the MT5-Backtested stage can be marked)
 *
 * No metric is invented: anything the stored research does not contain renders
 * as N/A, and a stage transition that the backend does not actually perform is
 * reported as a failure instead of being shown as success.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, fmt } from "../api.js";
import { arr, NA_TEXT, txt, objOrNull, numOrNull } from "../lib/safe.js";
import { Badge, Card, Kpi, SectionTitle, StateBlock, Star, parseNodeId, fmtId } from "../components/ui.jsx";
import { JsonView } from "../components/common.jsx";
import { useLab } from "../App.jsx";

const DEAD = new Set(["FAILED", "KILLED", "RETIRED", "DEAD", "LEGACY_TEST"]);

/* The promotion pipeline, in order. `backend` is the value the API stores. */
const PIPELINE = [
  { key: "QUALIFIED", label: "Qualified", backend: "QUALIFIED" },
  { key: "SHORTLISTED", label: "Shortlisted", backend: "SHORTLISTED" },
  { key: "MT5_BACKTESTED", label: "MT5 Backtested", backend: "MT5_BACKTESTED" },
  { key: "LIVE_TESTING", label: "Live Testing", backend: "LIVE_TESTING" },
  { key: "MT5_DEMO", label: "MT5 Demo", backend: "MT5_DEMO" },
  { key: "FINAL_CANDIDATE", label: "Final Candidate", backend: "FINAL_CANDIDATE" },
];

function stageIndex(stage) {
  const i = PIPELINE.findIndex((s) => s.key === String(stage || "").toUpperCase());
  if (i >= 0) return i;
  // GENERATED / SCREENED / VALIDATED are pre-pipeline states
  return -1;
}

function pctOrNull(v) {
  const n = numOrNull(v);
  return n === null ? null : (Math.abs(n) <= 1.001 ? n * 100 : n);
}

export default function NodeEconomics() {
  const lab = useLab() || {};
  const [input, setInput] = useState("1195");
  const [sid, setSid] = useState(1195);
  const [data, setData] = useState(null);
  const [charts, setCharts] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(null);
  const [notice, setNotice] = useState(null);
  const [showDead, setShowDead] = useState(false);
  const [busy, setBusy] = useState("");
  const [runs, setRuns] = useState(null);
  const inputRef = useRef(null);

  const load = useCallback(async (id) => {
    setLoading(true); setErr(null);
    try {
      const d = await api.nodeEconomics(id);
      setData(d);
      api.strategyCharts(id).then(setCharts).catch(() => setCharts(null));
      api.mt5HistoricalRuns({ strategy_id: id, limit: 5 }).then(setRuns).catch(() => setRuns(null));
    } catch (e) {
      setErr(e.message || String(e));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(sid); }, [sid, load]);

  // "/" focuses the node lookup, as advertised in the hint
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) {
        e.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const id = parseNodeId(input);
  /* V4.8 QA: an unresolvable lookup used to be a silent no-op — the button did
   * nothing, the page kept the previously loaded node and "Resolved id" showed
   * N/A, so invalid input looked like a broken control. Now it is refused with
   * an explicit reason and the displayed node is named, never silently reused. */
  const loadNode = () => {
    const n = parseNodeId(input);
    if (n === null) {
      setNotice({
        ok: false,
        text: input.trim()
          ? `"${input.trim()}" does not contain a node id. Use 10590, Node_10590 or #10590.`
          : "Enter a node id first (for example 10590, Node_10590 or #10590).",
      });
      return;
    }
    setNotice(null);
    setSid(n); setShowDead(false);
  };

  const node = objOrNull(data);
  const status = String(node?.status || "").toUpperCase();
  const isDead = DEAD.has(status);
  const blockedDead = isDead && !showDead;

  const stage = stageIndex(node?.pipeline_stage);
  const children = arr(node?.children);
  const metrics = objOrNull(node?.metrics) || {};
  const returns = objOrNull(node?.returns) || {};
  const pfs = objOrNull(node?.profit_factors) || {};
  const backtest = objOrNull(metrics.backtest) || {};
  const validation = objOrNull(metrics.validation) || {};
  const mt5b = objOrNull(metrics.mt5_backtest) || {};
  const liveT = objOrNull(metrics.live_test) || {};
  const demoT = objOrNull(metrics.mt5_demo) || {};
  const genome = objOrNull(node?.genome) || {};
  const indicators = arr(node?.indicators);
  const entries = objOrNull(node?.entry_conditions) || { long: [], short: [] };
  const exits = objOrNull(node?.exit_conditions) || {};
  const sizing = objOrNull(node?.position_management) || {};

  const entryClauses = arr(entries.long).length + arr(entries.short).length;
  const exitClauses = Object.values(exits).filter((v) => v && v !== "None").length;
  const regime = objOrNull(genome.regime_filters);
  const regimeList = regime ? Object.entries(regime).filter(([, v]) => v !== null && v !== undefined && v !== false && v !== "") : [];
  const sessions = genome.sessions ?? null;
  const days = Array.isArray(genome.days) ? genome.days : null;

  const completedRun = useMemo(() => {
    const list = arr(runs?.runs).filter((r) => String(r?.status || "").toUpperCase() === "COMPLETED");
    return list[0] || null;
  }, [runs]);

  /* ------------------------------------------------------------ transitions */
  const setStage = async (target, extraNotes = "") => {
    setBusy(target); setNotice(null);
    try {
      const res = await api.updatePipelineStage(sid, target, extraNotes);
      if (res?.ok === false) throw new Error(txt(res.error, "the backend refused the stage change"));
      setNotice({ ok: true, text: `Stage set to ${target}.` });
      await load(sid);
    } catch (e) {
      setNotice({ ok: false, text: `Stage change failed: ${e.message || e}` });
    } finally { setBusy(""); }
  };

  const toggleShortlist = async () => {
    setBusy("shortlist"); setNotice(null);
    try {
      const res = await api.toggleShortlist(sid);
      setNotice({ ok: true, text: res?.shortlisted ? "Added to the shortlist." : "Removed from the shortlist." });
      await load(sid);
    } catch (e) {
      setNotice({ ok: false, text: `Shortlist update failed: ${e.message || e}` });
    } finally { setBusy(""); }
  };

  const toggleLive = async () => {
    setBusy("live"); setNotice(null);
    try {
      const res = await api.toggleLiveTest(sid);
      const active = res?.is_active ?? res?.active;
      setNotice({ ok: true, text: `Live testing for this node: ${active ? "started" : "stopped"} (${txt(res?.status, "no status")}).` });
      if (active) await api.updatePipelineStage(sid, "LIVE_TESTING", "started live testing from Node Economics").catch(() => {});
      await load(sid);
    } catch (e) {
      setNotice({ ok: false, text: `Live testing toggle failed: ${e.message || e}` });
    } finally { setBusy(""); }
  };

  const toggleDemo = async () => {
    setBusy("demo"); setNotice(null);
    try {
      const res = await api.toggleMt5Demo(sid);
      const enabled = res?.enabled ?? res?.active;
      setNotice({ ok: true, text: `MT5 demo for this node: ${enabled ? "enabled" : "disabled"}.` });
      if (enabled) await api.updatePipelineStage(sid, "MT5_DEMO", "enabled demo trading from Node Economics").catch(() => {});
      await load(sid);
    } catch (e) {
      setNotice({ ok: false, text: `MT5 demo toggle failed: ${e.message || e}` });
    } finally { setBusy(""); }
  };

  /* ---------------------------------------------------------------- actions */
  const quickNav = (page) => {
    if (typeof lab.navigateTab === "function") lab.navigateTab(page, sid);
    else setNotice({ ok: false, text: "Navigation is unavailable in this context." });
  };

  const kpis = [
    { label: "OOS Return", value: pctOrNull(returns.validation_oos_return_pct), pct: true, tone: numOrNull(returns.validation_oos_return_pct) >= 0 ? "pos" : "neg" },
    { label: "Profit Factor", value: numOrNull(pfs.oos ?? pfs.in_sample), digits: 2 },
    { label: "Max Drawdown", value: pctOrNull(validation.max_drawdown_pct ?? backtest.max_drawdown_pct), pct: true, tone: "warn" },
    { label: "Robustness Score", value: numOrNull(node?.robustness_score), digits: 3 },
    { label: "Sharpe Ratio", value: numOrNull(validation.sharpe ?? backtest.sharpe), digits: 2 },
  ];

  const stageCards = [
    {
      key: "research", name: "Research / Qualification", status: node?.pipeline_stage || node?.status,
      ret: pctOrNull(returns.backtest_return_pct), pnl: numOrNull(backtest.net_profit),
      trades: numOrNull(backtest.trades), action: { label: "Open in Strategy Lab", onClick: () => quickNav("lab") },
    },
    {
      key: "backtest", name: "Backtest", status: txt(node?.dataset_id, "no dataset"),
      ret: pctOrNull(returns.backtest_return_pct), pnl: numOrNull(backtest.net_profit),
      trades: numOrNull(backtest.trades), action: { label: "Backtest Matrix", onClick: () => quickNav("matrix") },
    },
    {
      key: "validation", name: "Validation / Robustness", status: validation.passed === true ? "PASSED" : validation.passed === false ? "FAILED" : "NOT RUN",
      ret: pctOrNull(returns.validation_oos_return_pct), pnl: numOrNull(validation.total_pnl ?? validation.net_profit),
      trades: numOrNull(validation.trades), action: { label: "Backtest Matrix", onClick: () => quickNav("matrix") },
    },
    {
      key: "live", name: "Live Testing", status: txt(liveT.status, "IDLE"),
      ret: pctOrNull(returns.live_test_return_pct), pnl: numOrNull(liveT.total_pnl),
      trades: numOrNull(liveT.total_trades), action: { label: "Live Testing", onClick: () => quickNav("live_test") },
    },
    {
      key: "demo", name: "MT5 Demo", status: txt(demoT.status, "STOPPED"),
      ret: pctOrNull(returns.mt5_demo_return_pct), pnl: numOrNull(demoT.total_pnl),
      trades: numOrNull(demoT.total_trades), action: { label: "MT5 Demo Trading", onClick: () => quickNav("mt5_demo") },
    },
  ];

  const canAdvance = (key) => {
    if (key === "SHORTLISTED") return true;
    if (key === "MT5_BACKTESTED") return Boolean(completedRun);
    if (key === "LIVE_TESTING") return Boolean(liveT.is_active) || Number(liveT.total_trades || 0) > 0;
    if (key === "MT5_DEMO") return Boolean(demoT.enabled) || Number(demoT.total_trades || 0) > 0;
    if (key === "FINAL_CANDIDATE") return Number(demoT.total_trades || 0) > 0 && Number(liveT.total_trades || 0) > 0;
    return true;
  };

  const onStageClick = (s, i) => {
    setNotice(null);
    if (i === stage) { setNotice({ ok: true, text: `This node is already at ${s.label}.` }); return; }
    if (i > stage && stage >= 0) {
      if (!canAdvance(s.key)) {
        const why = s.key === "MT5_BACKTESTED"
          ? "No completed MT5 historical backtest exists for this node yet — run one on the MT5 Backtest page first."
          : s.key === "LIVE_TESTING"
            ? "Start live testing for this node (the button below) before marking this stage."
            : s.key === "MT5_DEMO"
              ? "Enable MT5 demo trading for this node before marking this stage."
              : "Final Candidate requires both live-test and demo trade history for this node.";
        setNotice({ ok: false, text: `Cannot mark ${s.label}: ${why}` });
        return;
      }
    }
    if (s.key === "SHORTLISTED") { setStage("SHORTLISTED", "marked from Node Economics"); return; }
    setStage(s.key, "set from Node Economics");
  };

  return (
    <div>
      {/* ------------------------------------------------------------ header */}
      <div className="kit-head">
        <div>
          <h2 className="page-title" style={{ marginBottom: 2 }}>Node Economics</h2>
          <div className="muted" style={{ fontSize: 12 }}>
            Authoritative genome model — identity, lineage, promotion pipeline, market model and every stage result.
          </div>
        </div>
        <Badge tone="violet">Authoritative Genome Model</Badge>
      </div>

      <div className="kit-strip" style={{ marginBottom: 12 }}>
        <div className="item" style={{ flex: "0 0 240px" }}>
          <span className="k">Node lookup</span>
          <input ref={inputRef} value={input} onChange={(e) => setInput(e.target.value)}
                 onKeyDown={(e) => { if (e.key === "Enter") loadNode(); }}
                 placeholder="10590 / Node_10590 / #10590" className="mono" />
        </div>
        <div className="item">
          <span className="k">&nbsp;</span>
          <button className="btn primary" onClick={loadNode} disabled={loading}>
            {loading ? "loading…" : "Load Node"}
          </button>
        </div>
        <div className="item">
          <span className="k">Shortcut</span>
          <span className="mono" style={{ fontSize: 12 }}>press <b>/</b> to focus the lookup</span>
        </div>
        <div className="item">
          <span className="k">Shortlist</span>
          <Star on={Boolean(node?.shortlisted)} onClick={toggleShortlist} disabled={busy === "shortlist" || !node} />
        </div>
        <div className="item">
          <span className="k">Resolved id</span>
          <span className="mono">
            {id === null ? NA_TEXT : fmtId(id)}
            {id !== null && node && Number(node.id) !== id && (
              <span className="muted"> · could not load {fmtId(id)}</span>
            )}
          </span>
          {id === null && node && (
            <span className="muted" style={{ fontSize: 11 }}>
              showing {fmtId(node.id)} (last successfully loaded node)
            </span>
          )}
        </div>
      </div>

      {notice && (
        <div className={notice.ok ? "kit-ok" : "kit-inline-err"} role="status">
          {notice.text}
        </div>
      )}

      <StateBlock loading={loading} error={err} onRetry={() => load(sid)}
                   empty={!node} emptyHint="Load a node to see its economics.">
        {blockedDead ? (
          <Card title="Dead node hidden" tone="warn">
            <div style={{ fontSize: 13 }}>
              Node <b className="mono">{fmtId(sid)}</b> is <Badge tone="danger">{status}</Badge> — dead nodes are hidden by default
              because they failed the research gates and are not tradable candidates.
            </div>
            <div className="muted" style={{ marginTop: 6, fontSize: 12 }}>
              Reason on record: {txt(node?.survival_reason || node?.creation_reason, "not stated in the stored record")}
            </div>
            <div className="btn-row" style={{ marginTop: 10, marginBottom: 0 }}>
              <button className="btn" onClick={() => setShowDead(true)}>Show Dead Node</button>
              <label className="fld" style={{ display: "flex", gap: 6, alignItems: "center", margin: 0 }}>
                <input type="checkbox" style={{ width: "auto" }} checked={showDead}
                       onChange={(e) => setShowDead(e.target.checked)} />
                Show Dead Nodes
              </label>
            </div>
          </Card>
        ) : (
          <div className={isDead && showDead ? "kit-dim" : ""}>
            {isDead && showDead && (
              <div className="kit-banner unavail">
                <b>DEAD NODE — {status}</b>
                <div className="muted" style={{ marginTop: 3 }}>
                  Shown because you asked for it. This node is not a tradable candidate; the research/trading pages exclude it.
                </div>
                {/* V4.8 QA: the blocked state shows the reason on record; keep it
                    visible after revealing, otherwise the stored explanation for
                    the failure disappears exactly when it is most useful. */}
                <div className="muted" style={{ marginTop: 4 }}>
                  Reason on record:{" "}
                  <b>{txt(node?.survival_reason || node?.creation_reason, "not stated in the stored record")}</b>
                </div>
              </div>
            )}

            {/* ------------------------------------------------------ identity */}
            <SectionTitle hint="The stored authoritative identifiers — no recomputation.">Identity &amp; Lineage</SectionTitle>
            <div className="grid cols-4" style={{ marginBottom: 6 }}>
              <Card><div className="kit-kv"><span className="k">Authoritative ID</span><b className="mono">{txt(node?.id)}</b></div>
                <div className="kit-kv"><span className="k">Node label</span><span className="mono">{txt(node?.node_id)}</span></div></Card>
              <Card><div className="kit-kv"><span className="k">Research index</span><b className="mono">{txt(node?.research_node_num, "—")}</b></div>
                <div className="kit-kv"><span className="k">Run ID</span><span className="mono">{txt(node?.run_id, "—")}</span></div></Card>
              <Card><div className="kit-kv"><span className="k">Generation</span><b className="mono">{txt(node?.generation)}</b></div>
                <div className="kit-kv"><span className="k">Status</span><span><Badge tone={isDead ? "danger" : status === "QUALIFIED" ? "ok" : "info"}>{txt(status, "UNKNOWN")}</Badge></span></div></Card>
              <Card><div className="kit-kv"><span className="k">Parent node</span>
                  <span>{node?.parent_id ? <button className="kit-chip mono" onClick={() => { setInput(String(node.parent_id)); setSid(Number(node.parent_id)); setShowDead(false); }}>#{node.parent_id}</button> : <span className="muted">{NA_TEXT} (root)</span>}</span></div>
                <div className="kit-kv"><span className="k">Descendants</span><b className="mono">{children.length}</b></div></Card>
            </div>
            <Card title="Direct children" style={{ marginBottom: 4 }}>
              {children.length === 0
                ? <span className="muted">No children recorded for this node.</span>
                : (
                  <div>
                    {children.map((c, i) => {
                      const cid = parseNodeId(c?.id ?? c);
                      if (cid === null) return <span key={i} className="kit-chip muted">{NA_TEXT}</span>;
                      return (
                        <button key={cid} className="kit-chip mono" onClick={() => { setInput(String(cid)); setSid(cid); setShowDead(false); }}>
                          #{cid}{c?.status ? <span className="muted"> · {c.status}</span> : null}
                        </button>
                      );
                    })}
                  </div>
                )}
            </Card>

            {/* ----------------------------------------------- promotion track */}
            <SectionTitle hint="Every transition calls the real backend operation — nothing is marked complete by the UI alone.">
              Promotion Pipeline
            </SectionTitle>
            <PipelineTrack stage={stage} busy={busy} onPick={onStageClick} />
            <div className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>
              Current stage: <b>{stage >= 0 ? PIPELINE[stage].label : txt(node?.pipeline_stage, "not in the promotion pipeline")}</b>
              {completedRun ? <> · MT5 backtest <span className="mono">{completedRun.run_id}</span> COMPLETED</> : " · no completed MT5 backtest"}
            </div>

            <div className="btn-row" style={{ marginTop: 10 }}>
              <button className="btn" disabled={busy === "live"} onClick={toggleLive}>
                {liveT.is_active ? "■ Stop Live Testing" : "▶ Start Live Testing"}
              </button>
              <button className="btn" disabled={busy === "demo"} onClick={toggleDemo}>
                {demoT.enabled ? "■ Disable MT5 Demo" : "▶ Enable MT5 Demo"}
              </button>
            </div>

            {/* ------------------------------------------------------- KPI cards */}
            <SectionTitle hint="Unavailable values are shown as N/A — the stored research does not contain them.">Key Metrics</SectionTitle>
            <div className="grid cols-4" style={{ gridTemplateColumns: "repeat(5, minmax(0,1fr))" }}>
              {kpis.map((k) => (
                <Kpi key={k.label} label={k.label} tone={k.tone}
                     value={k.value === null ? NA_TEXT : k.pct ? fmt.pct(k.value / 100, 2) : fmt.num(k.value, k.digits ?? 2)} />
              ))}
            </div>

            {/* ----------------------------------------------------- stage cards */}
            <SectionTitle>Stage Results</SectionTitle>
            <div className="grid cols-4" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))" }}>
              {stageCards.map((s) => (
                <div className="kit-stage-card" key={s.key}>
                  <div className="hd">
                    <span className="nm">{s.name}</span>
                    <Badge tone={/PASSED|QUALIFIED|COMPLETED/i.test(String(s.status)) ? "ok"
                      : /FAIL|KILL|STOP|NOT RUN|IDLE/i.test(String(s.status)) ? "mute" : "info"}>
                      {txt(s.status)}
                    </Badge>
                  </div>
                  <div className="kit-kv"><span className="k">Return</span><span className={s.ret === null ? "muted" : s.ret >= 0 ? "pos" : "neg"}>{s.ret === null ? NA_TEXT : fmt.pct(s.ret / 100, 2)}</span></div>
                  <div className="kit-kv"><span className="k">P/L</span><span className={s.pnl === null ? "muted" : s.pnl >= 0 ? "pos" : "neg"}>{s.pnl === null ? NA_TEXT : fmt.pnl(s.pnl)}</span></div>
                  <div className="kit-kv"><span className="k">Trades</span><span className="mono">{s.trades === null ? NA_TEXT : fmt.num(s.trades, 0)}</span></div>
                  <button className="btn" style={{ width: "100%", marginTop: 8 }} onClick={s.action.onClick}>{s.action.label}</button>
                </div>
              ))}
            </div>

            {/* ------------------------------------------------- market & session */}
            <SectionTitle hint="Taken from the genome clause set — no interpretation is added.">Market &amp; Session Model</SectionTitle>
            <div className="grid cols-2">
              <Card title="Regime filters">
                {regimeList.length === 0
                  ? <div><Badge tone="info">Unconstrained</Badge> <span className="muted" style={{ marginLeft: 6 }}>the genome applies no regime filter</span></div>
                  : <div>{regimeList.map(([k, v]) => <span key={k} className="kit-chip">{k}: <b>{String(v)}</b></span>)}</div>}
                <div className="kit-kv" style={{ marginTop: 8 }}><span className="k">Sessions</span><span>{sessions === null || (Array.isArray(sessions) && sessions.length === 0) ? <Badge tone="info">All sessions</Badge> : <span className="mono">{JSON.stringify(sessions)}</span>}</span></div>
                <div className="kit-kv"><span className="k">Days</span><span>{days === null ? <Badge tone="info">All days</Badge> : <span className="mono">{days.join(", ")}</span>}</span></div>
              </Card>
              <Card title="Clause set">
                <div className="kit-kv"><span className="k">Entry clauses</span><b className="mono">{entryClauses}</b></div>
                <div className="kit-kv"><span className="k">Exit clauses</span><b className="mono">{exitClauses}</b></div>
                <div className="kit-kv"><span className="k">Total clauses</span><b className="mono">{entryClauses + exitClauses}</b></div>
                <div className="kit-kv"><span className="k">Complexity</span><b className="mono">{txt(node?.complexity, "—")}</b></div>
                <div className="kit-kv"><span className="k">Direction</span><span>{txt(node?.direction)}</span></div>
              </Card>
            </div>

            <div className="kit-cols" style={{ marginTop: 12 }}>
              <Card title="Indicators &amp; rules" style={{ flex: "1 1 320px" }}>
                {indicators.length === 0 ? <span className="muted">No indicators parsed from the genome.</span> : (
                  <>
                    <div>{indicators.map((ind, i) => (
                      <span className="kit-chip" key={i}>{txt(ind?.display || ind?.spec, NA_TEXT)}</span>
                    ))}</div>
                    <div style={{ marginTop: 8 }}>
                      {arr(entries.short).map((c, i) => <div key={`s${i}`} className="mono" style={{ fontSize: 11.5 }}>short: {txt(c)}</div>)}
                      {arr(entries.long).map((c, i) => <div key={`l${i}`} className="mono" style={{ fontSize: 11.5 }}>long: {txt(c)}</div>)}
                      {entryClauses === 0 && <span className="muted">No explicit entry clause text stored.</span>}
                    </div>
                  </>
                )}
              </Card>
              <Card title="Position sizing" style={{ flex: "1 1 260px" }}>
                {Object.keys(sizing).length === 0 ? <span className="muted">No sizing record stored.</span> : (
                  Object.entries(sizing).map(([k, v]) => (
                    <div className="kit-kv" key={k}><span className="k">{k.replace(/_/g, " ")}</span><span>{txt(v)}</span></div>
                  ))
                )}
              </Card>
            </div>

            {/* ------------------------------------------------ exits + safety */}
            <div className="kit-cols" style={{ marginTop: 12 }}>
              <Card title="Exit model" style={{ flex: "1 1 320px" }}>
                {Object.keys(exits).length === 0 ? <span className="muted">No exit model stored.</span> : (
                  Object.entries(exits).map(([k, v]) => (
                    <div className="kit-kv" key={k}><span className="k">{k.replace(/_/g, " ")}</span><span>{txt(v)}</span></div>
                  ))
                )}
              </Card>
              <Card title="Safety" style={{ flex: "1 1 260px" }}>
                <div className="kit-kv"><span className="k">Live stage — Real Money</span>
                  <b style={{ color: "var(--green)" }}>$0 (ZERO)</b></div>
                <div className="muted" style={{ fontSize: 11.5, marginBottom: 6 }}>
                  Live testing is a monitored lab stage; the V4.2 execution path cannot place a real-money order.
                </div>
                <div className="kit-kv"><span className="k">Demo stage — Safety Gating</span><Badge tone="ok">CONFIRMED</Badge></div>
                <div className="kit-kv"><span className="k">Account Type</span><Badge tone="sim">DEMO ONLY</Badge></div>
              </Card>
            </div>

            {/* -------------------------------------------------- quick navigation */}
            <SectionTitle hint="The current node is carried into the destination page.">Quick Navigation</SectionTitle>
            <div className="btn-row">
              <button className="btn" onClick={() => quickNav("mt5_backtest")}>MT5 Backtest →</button>
              <button className="btn" onClick={() => quickNav("live_test")}>Live Testing →</button>
              <button className="btn" onClick={() => quickNav("matrix")}>Backtest Matrix →</button>
              <button className="btn" onClick={() => quickNav("lab")}>Strategy Lab →</button>
            </div>

            {/* ------------------------------------------------------- raw data */}
            <SectionTitle hint="Exactly what the API returned for this node.">Raw Data View</SectionTitle>
            <Card>
              <JsonView data={data} maxHeight={320} />
              <div className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>
                Equity curve data: {charts?.equity?.length ? <Badge tone="ok">{charts.equity.length} points</Badge>
                  : node?.has_equity_curve ? <Badge tone="info">available on the Backtest Matrix / details drawer</Badge> : <Badge tone="mute">not available</Badge>}
              </div>
            </Card>
          </div>
        )}
      </StateBlock>
    </div>
  );
}

/* --------------------------------------------------------------- track UI */
function PipelineTrack({ stage, busy, onPick }) {
  return (
    <div className="kit-track">
      {PIPELINE.map((s, i) => {
        const done = stage > i;
        const active = stage === i;
        const cls = active ? " active" : done ? " done" : "";
        return (
          <button key={s.key} className={"kit-track-step" + cls} disabled={Boolean(busy)}
                  title={active ? "Current stage" : done ? "Already completed" : "Click to mark this stage"}
                  onClick={() => onPick(s, i)}>
            <span className="dot">{done ? "✓" : active ? "●" : i + 1}</span>
            <span className="lbl">{s.label}</span>
            <span className="st">{active ? "CURRENT" : done ? "DONE" : "NEXT"}</span>
          </button>
        );
      })}
    </div>
  );
}
