import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, fmt } from "../api.js";
import { useLab } from "../App.jsx";
import { Card, ErrorNote, Metric, Pill, Spinner } from "../components/common.jsx";
import NodeResearchDetail from "../components/NodeResearchDetail.jsx";
import BacktestMatrixTable from "../components/BacktestMatrixTable.jsx";

/* V4.5 — Strategy Lab (research selection, filtering, comparison).
 *
 * Everything the page shows comes from the server:
 *   GET /api/research/facets      -> filter options + research population summary
 *   GET /api/research/strategies  -> the paged/filtered/sorted table
 *   GET /api/research/matrix      -> the comparison matrix (Backtest Matrix)
 *   GET /api/research/compare     -> side-by-side comparison (<= 8 nodes)
 *   GET /api/stats/node/{id}      -> the V4.4 node detail (shared component)
 *
 * Filtering, sorting and pagination happen in SQL on the backend; the browser
 * never loads the whole 10k-node population and never recalculates a metric.
 * The selection survives filter/page changes and the UI states plainly when a
 * selected strategy is hidden by the current filter.
 */

const RESULT_STATE = [
  { key: "", label: "any result state" },
  { key: "backtested", label: "has backtest" },
  { key: "validated", label: "has validation" },
  { key: "validation_passed", label: "validation passed" },
  { key: "qualified", label: "qualified" },
  { key: "shortlisted", label: "shortlisted" },
];

const STAGES = [
  { key: "", label: "any stage" },
  { key: "detail", label: "detail" },
  { key: "screen", label: "screen" },
  { key: "none", label: "no backtest" },
];

const EMPTY_FILTERS = {
  search: "", generation: "", status: "", symbol: "", timeframe: "", direction: "",
  result_state: "", stage: "", min_return: "", min_profit_factor: "", min_trades: "",
  max_drawdown: "", min_robustness: "",
};

function filtersToParams(f) {
  const p = {};
  if (f.search) p.search = f.search.trim();
  if (f.generation) p.generation = f.generation;
  if (f.status) p.status = f.status;
  if (f.symbol) p.symbol = f.symbol;
  if (f.timeframe) p.timeframe = f.timeframe;
  if (f.direction) p.direction = f.direction;
  if (f.stage) p.stage = f.stage;
  ["min_return", "min_profit_factor", "min_trades", "max_drawdown", "min_robustness"].forEach((k) => {
    if (f[k] !== "" && f[k] !== undefined) p[k] = f[k];
  });
  if (f.result_state === "backtested") p.has_backtest = true;
  if (f.result_state === "validated") p.has_validation = true;
  if (f.result_state === "validation_passed") p.validated_passed = true;
  if (f.result_state === "qualified") p.qualified = true;
  if (f.result_state === "shortlisted") p.shortlist_only = true;
  return p;
}

export default function StrategyLab() {
  const { openStrategy, shortlist, toggleShortlist, navigateTab, setSelectedStrategyId } = useLab() || {};

  const [facets, setFacets] = useState(null);
  const [facetsErr, setFacetsErr] = useState(null);

  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [sort, setSort] = useState("return");
  const [dir, setDir] = useState("desc");
  const [limit, setLimit] = useState(50);
  const [offset, setOffset] = useState(0);

  const [list, setList] = useState(null);
  const [listErr, setListErr] = useState(null);
  const [listBusy, setListBusy] = useState(false);

  const [selectedIds, setSelectedIds] = useState([]);
  const [compare, setCompare] = useState(null);
  const [matrix, setMatrix] = useState(null);
  const [busyAction, setBusyAction] = useState("");
  const [actionErr, setActionErr] = useState(null);

  const [detail, setDetail] = useState(null);
  const [detailBusy, setDetailBusy] = useState(false);
  const [detailErr, setDetailErr] = useState(null);

  // debounce the free-text search so typing does not fire a request per keystroke
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(filters.search), 350);
    return () => clearTimeout(t);
  }, [filters.search]);

  const loadFacets = useCallback(() => {
    api.researchFacets()
      .then((r) => { setFacets(r); setFacetsErr(null); })
      .catch((e) => setFacetsErr(e));
  }, []);
  useEffect(() => { loadFacets(); }, [loadFacets]);

  const params = useMemo(() => {
    const p = filtersToParams({ ...filters, search: debouncedSearch });
    p.limit = limit; p.offset = offset; p.sort = sort; p.dir = dir;
    return p;
  }, [filters, debouncedSearch, limit, offset, sort, dir]);

  const loadList = useCallback(() => {
    setListBusy(true);
    api.researchStrategies(params)
      .then((r) => { setList(r); setListErr(null); })
      .catch((e) => setListErr(e))
      .finally(() => setListBusy(false));
  }, [params]);
  useEffect(() => { loadList(); }, [loadList]);

  // any filter/sort change returns to the first page (deterministic paging)
  const firstLoad = useRef(true);
  useEffect(() => {
    if (firstLoad.current) { firstLoad.current = false; return; }
    setOffset(0);
  }, [filters, debouncedSearch, sort, dir, limit]);

  const nodes = list?.nodes || [];
  const total = list?.total ?? 0;
  const pageCount = list?.pages ?? 0;
  const page = Math.floor(offset / limit) + 1;
  const maxCompare = facets?.limits?.max_compare ?? 8;

  const pageIds = nodes.map((n) => n.id);
  const hiddenSelected = selectedIds.filter((id) => list && !pageIds.includes(id));

  const toggleSelected = (id) => {
    setSelectedIds((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]));
  };

  const openDetail = (id) => {
    setDetailBusy(true); setDetailErr(null);
    api.statsNode(id)
      .then((r) => setDetail(r))
      .catch((e) => { setDetailErr(e); setDetail(null); })
      .finally(() => setDetailBusy(false));
  };

  const runCompare = () => {
    setBusyAction("compare"); setActionErr(null);
    api.researchCompare(selectedIds)
      .then((r) => setCompare(r))
      .catch((e) => setActionErr(e))
      .finally(() => setBusyAction(""));
  };

  const runMatrix = (ids = selectedIds) => {
    setBusyAction("matrix"); setActionErr(null);
    api.researchMatrix({ ids: ids.join(","), limit: 200 })
      .then((r) => setMatrix(r))
      .catch((e) => setActionErr(e))
      .finally(() => setBusyAction(""));
  };

  const clearSelection = () => { setSelectedIds([]); setCompare(null); setMatrix(null); };
  const selectPage = () => setSelectedIds((cur) => Array.from(new Set([...cur, ...pageIds])));
  const openInWorkspace = (id) => {
    if (setSelectedStrategyId) setSelectedStrategyId(id);
    if (navigateTab) navigateTab("economics", id);
    if (openStrategy) openStrategy(id);
  };

  const pop = facets?.population;

  return (
    <div>
      <h2 className="page-title">Strategy Lab</h2>
      <div className="page-sub">
        Research selection and comparison over the USER_RESEARCH population
        {list && ` — ${fmt.num(list.population_total, 0)} research nodes, ${fmt.num(list.legacy_excluded_total, 0)} LEGACY_TEST records excluded`}.
        Filtering, sorting and paging run in SQL on the backend; research results
        (BACKTEST / VALIDATION) and MT5 / live-test execution records are always labelled
        separately and never merged.
      </div>

      {facetsErr && <ErrorNote err={facetsErr} />}
      {listErr && <ErrorNote err={listErr} />}
      {actionErr && <ErrorNote err={actionErr} />}

      {/* ---------------- filters ---------------- */}
      <Card style={{ marginBottom: 14 }}>
        <h3>Filters <span className="muted" style={{ fontSize: 11 }}>server-side</span></h3>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
          <input className="input" style={{ minWidth: 200 }} placeholder="search node id / symbol / status…"
                 value={filters.search} onChange={(e) => setFilters({ ...filters, search: e.target.value })} />
          <select className="input" value={filters.generation}
                  onChange={(e) => setFilters({ ...filters, generation: e.target.value })}>
            <option value="">any generation</option>
            {(facets?.options?.generation || []).map((g) => (
              <option key={g.value} value={g.value}>gen {g.value} ({fmt.num(g.count, 0)})</option>
            ))}
          </select>
          <select className="input" value={filters.status}
                  onChange={(e) => setFilters({ ...filters, status: e.target.value })}>
            <option value="">any status</option>
            {(facets?.options?.status || []).map((s) => (
              <option key={s.value} value={s.value}>{s.value} ({fmt.num(s.count, 0)})</option>
            ))}
          </select>
          <select className="input" value={filters.symbol}
                  onChange={(e) => setFilters({ ...filters, symbol: e.target.value })}>
            <option value="">any symbol</option>
            {(facets?.options?.symbol || []).map((s) => (
              <option key={s.value} value={s.value}>{s.value} ({fmt.num(s.count, 0)})</option>
            ))}
          </select>
          <select className="input" value={filters.timeframe}
                  onChange={(e) => setFilters({ ...filters, timeframe: e.target.value })}>
            <option value="">any timeframe</option>
            {(facets?.options?.timeframe || []).map((s) => (
              <option key={s.value} value={s.value}>{s.value} ({fmt.num(s.count, 0)})</option>
            ))}
          </select>
          <select className="input" value={filters.result_state}
                  onChange={(e) => setFilters({ ...filters, result_state: e.target.value })}>
            {RESULT_STATE.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
          </select>
          <select className="input" value={filters.stage}
                  onChange={(e) => setFilters({ ...filters, stage: e.target.value })}>
            {STAGES.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
          <input className="input" style={{ width: 110 }} placeholder="min return"
                 title="minimum stored backtest return (e.g. 0.05 = 5%)"
                 value={filters.min_return} onChange={(e) => setFilters({ ...filters, min_return: e.target.value })} />
          <input className="input" style={{ width: 110 }} placeholder="min PF"
                 title="minimum stored profit factor"
                 value={filters.min_profit_factor} onChange={(e) => setFilters({ ...filters, min_profit_factor: e.target.value })} />
          <input className="input" style={{ width: 110 }} placeholder="min trades"
                 title="minimum stored trade count"
                 value={filters.min_trades} onChange={(e) => setFilters({ ...filters, min_trades: e.target.value })} />
          <input className="input" style={{ width: 120 }} placeholder="max drawdown"
                 title="maximum stored drawdown (e.g. 0.1 = 10%)"
                 value={filters.max_drawdown} onChange={(e) => setFilters({ ...filters, max_drawdown: e.target.value })} />
          <input className="input" style={{ width: 130 }} placeholder="min robustness"
                 title="minimum validation robustness score"
                 value={filters.min_robustness} onChange={(e) => setFilters({ ...filters, min_robustness: e.target.value })} />
          <button className="btn" onClick={() => setFilters(EMPTY_FILTERS)}>reset</button>
        </div>
        <div className="muted" style={{ fontSize: 11, marginTop: 6 }}>
          Applied on the server: {JSON.stringify(list?.filters || {})}
        </div>
      </Card>

      {/* ---------------- research population summary ---------------- */}
      <div className="grid cols-4" style={{ marginBottom: 14 }}>
        <Metric label="Research population" value={fmt.num(pop?.total ?? list?.population_total, 0)}
                sub={`scope ${list?.scope || "USER_RESEARCH"}`} />
        <Metric label="Alive / Dead" value={`${fmt.num(pop?.alive, 0)} / ${fmt.num(pop?.dead, 0)}`}
                sub="engine status grouping" />
        <Metric label="Qualified" value={fmt.num(pop?.qualified, 0)} sub="status QUALIFIED" />
        <Metric label="Matching filters" value={fmt.num(total, 0)} sub={`${pageCount} page(s)`} />
        <Metric label="With backtest" value={fmt.num((facets?.options?.result_state || []).find((r) => r.value === "backtested")?.count, 0)} sub="stored BACKTEST records" />
        <Metric label="With validation" value={fmt.num((facets?.options?.result_state || []).find((r) => r.value === "validated")?.count, 0)} sub="stored VALIDATION records" />
        <Metric label="Validation passed" value={fmt.num((facets?.options?.result_state || []).find((r) => r.value === "validation_passed")?.count, 0)} sub="passed = true" />
        <Metric label="LEGACY_TEST excluded" value={fmt.num(list?.legacy_excluded_total, 0)} sub="never in research results" />
      </div>

      {/* ---------------- strategy table ---------------- */}
      <Card style={{ marginBottom: 14 }}>
        <h3>
          Research strategies
          <span className="muted" style={{ fontSize: 11, marginLeft: 8 }}>
            page {page} / {pageCount || 1} · {listBusy ? "loading…" : `${nodes.length} rows`}
          </span>
        </h3>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", marginBottom: 8 }}>
          <select className="input" value={sort} onChange={(e) => setSort(e.target.value)}>
            {(facets?.sorts || [{ key: "return", label: "Backtest return" }]).map((s) => (
              <option key={s.key} value={s.key}>{s.label}</option>
            ))}
          </select>
          <button className="btn" onClick={() => setDir(dir === "desc" ? "asc" : "desc")}>
            {dir === "desc" ? "▼ desc" : "▲ asc"}
          </button>
          <select className="input" value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
            {[25, 50, 100, 200].map((n) => <option key={n} value={n}>{n} / page</option>)}
          </select>
          <button className="btn" disabled={offset <= 0} onClick={() => setOffset(Math.max(0, offset - limit))}>‹ prev</button>
          <button className="btn" disabled={offset + limit >= total} onClick={() => setOffset(offset + limit)}>next ›</button>
          <button className="btn" onClick={selectPage}>select page</button>
        </div>

        <div className="scroll-y" style={{ maxHeight: 460 }}>
          <table className="tbl">
            <thead>
              <tr>
                <th style={{ width: 28 }} title="select for comparison">☐</th>
                <th>node</th><th>gen</th><th>status</th><th>sym / tf</th>
                <th style={{ textAlign: "right" }}>stage</th>
                <th style={{ textAlign: "right" }}>trades</th>
                <th style={{ textAlign: "right" }}>wins</th>
                <th style={{ textAlign: "right" }}>win rate</th>
                <th style={{ textAlign: "right" }}>net P&amp;L</th>
                <th style={{ textAlign: "right" }}>return</th>
                <th style={{ textAlign: "right" }}>max DD</th>
                <th style={{ textAlign: "right" }}>PF</th>
                <th style={{ textAlign: "right" }}>expectancy</th>
                <th style={{ textAlign: "right" }}>R:R</th>
                <th style={{ textAlign: "right" }}>robust.</th>
                <th style={{ textAlign: "right" }}>exec</th>
              </tr>
            </thead>
            <tbody>
              {nodes.map((n) => {
                const r = n.research || {};
                const v = n.validation || {};
                const e = n.economics || {};
                const checked = selectedIds.includes(n.id);
                const starred = shortlist?.includes(n.id) || n.shortlisted;
                return (
                  <tr key={n.id} style={checked ? { background: "#152238" } : undefined}>
                    <td>
                      <input type="checkbox" checked={checked} onChange={() => toggleSelected(n.id)} />
                    </td>
                    <td>
                      <button className="btn" style={{ padding: "1px 6px" }}
                              onClick={() => openDetail(n.id)} title="open the node detail (research / economics / execution)">
                        Node_{n.id}
                      </button>
                      {starred ? <span title="shortlisted" style={{ marginLeft: 4 }}>⭐</span> : null}
                    </td>
                    <td className="mono">G{n.generation}</td>
                    <td><Pill status={n.status} /></td>
                    <td className="mono">{n.symbol} {n.timeframe}</td>
                    <td style={{ textAlign: "right" }} className="mono">{r.stage || <span className="muted" title="no stored backtest">—</span>}</td>
                    <td style={{ textAlign: "right" }} className="mono">{r.trades == null ? <span className="muted">N/A</span> : fmt.num(r.trades, 0)}</td>
                    <td style={{ textAlign: "right" }} className="mono">{r.wins == null ? <span className="muted" title="derived as trades × win rate; needs both stored">N/A</span> : fmt.num(r.wins, 0)}</td>
                    <td style={{ textAlign: "right" }} className="mono">{r.win_rate == null ? <span className="muted">N/A</span> : fmt.pct(r.win_rate, 1)}</td>
                    <td style={{ textAlign: "right" }} className="mono">{r.net_profit == null ? <span className="muted">N/A</span> : fmt.pnl(r.net_profit)}</td>
                    <td style={{ textAlign: "right" }} className="mono">{r.return_pct == null ? <span className="muted">N/A</span> : fmt.ret(r.return_pct, 2)}</td>
                    <td style={{ textAlign: "right" }} className="mono">{r.max_drawdown_pct == null ? <span className="muted">N/A</span> : fmt.pct(r.max_drawdown_pct, 2)}</td>
                    <td style={{ textAlign: "right" }} className="mono">{r.profit_factor == null ? <span className="muted">N/A</span> : fmt.ratio(r.profit_factor, 3)}</td>
                    <td style={{ textAlign: "right" }} className="mono">{r.expectancy == null ? <span className="muted">N/A</span> : fmt.num(r.expectancy, 4)}</td>
                    <td style={{ textAlign: "right" }} className="mono">{e.reward_risk_ratio == null ? <span className="muted" title="genome has no SL/TP multiple">N/A</span> : fmt.ratio(e.reward_risk_ratio, 2)}</td>
                    <td style={{ textAlign: "right" }} className="mono">{v.robustness_score == null ? <span className="muted">N/A</span> : fmt.ratio(v.robustness_score, 3)}</td>
                    <td style={{ textAlign: "right" }} className="mono" title={`paper ${n.execution?.paper_trades || 0} · live test ${n.execution?.live_test_trades || 0} · mt5 demo ${n.execution?.mt5_demo_trades || 0}`}>
                      {fmt.num(n.execution?.records_total || 0, 0)}
                    </td>
                  </tr>
                );
              })}
              {!nodes.length && (
                <tr><td colSpan={17}>{listBusy ? <Spinner /> : <span className="muted">No strategy matches the current filters.</span>}</td></tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="muted" style={{ fontSize: 11, marginTop: 6 }}>
          BACKTEST / VALIDATION columns are research results. The exec column counts execution records
          (PAPER / LIVE TEST / MT5 DEMO) and never feeds a research number. R:R comes from the V4.4 node
          economics (genome SL/TP ATR multiples). Values the stored data does not contain show N/A.
        </div>
      </Card>

      {/* ---------------- selection + comparison ---------------- */}
      <Card style={{ marginBottom: 14 }}>
        <h3>
          Selected strategies
          <span className="pill" style={{ marginLeft: 8 }}>{selectedIds.length}</span>
          {selectedIds.length > 0 && (
            <button className="btn" style={{ marginLeft: 8 }} onClick={clearSelection}>clear</button>
          )}
          <button className="btn" style={{ marginLeft: 8 }} disabled={!selectedIds.length || busyAction === "matrix"}
                  onClick={() => runMatrix()}>
            {busyAction === "matrix" ? "building matrix…" : "Backtest Matrix"}
          </button>
          <button className="btn primary" style={{ marginLeft: 8 }}
                  disabled={!selectedIds.length || selectedIds.length > maxCompare || busyAction === "compare"}
                  title={selectedIds.length > maxCompare ? `at most ${maxCompare} strategies can be compared at once` : "compare side by side"}
                  onClick={runCompare}>
            {busyAction === "compare" ? "comparing…" : `Compare (max ${maxCompare})`}
          </button>
        </h3>
        {selectedIds.length === 0 && (
          <div className="muted" style={{ fontSize: 12 }}>
            Tick rows in the table to build a comparison set. The selection is kept while you filter or
            change pages, so you can collect strategies from several generations.
          </div>
        )}
        {selectedIds.length > 0 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {selectedIds.map((id) => (
              <button key={id} className="pill" style={{ cursor: "pointer" }}
                      title="remove from selection" onClick={() => toggleSelected(id)}>
                Node_{id} ✕
              </button>
            ))}
          </div>
        )}
        {hiddenSelected.length > 0 && (
          <div className="warn-banner" style={{ marginTop: 8, fontSize: 11.5 }}>
            {hiddenSelected.length} selected strateg{hiddenSelected.length === 1 ? "y is" : "ies are"} hidden by
            the current filter/page ({hiddenSelected.join(", ")}). They stay selected — clearing the filter
            brings them back, or use Backtest Matrix / Compare directly (they use the full selection).
          </div>
        )}
        {selectedIds.length > maxCompare && (
          <div className="warn-banner" style={{ marginTop: 8, fontSize: 11.5 }}>
            {selectedIds.length} selected — the side-by-side comparison is limited to {maxCompare} nodes
            (matrix has no such limit). Deselect some to compare.
          </div>
        )}
      </Card>

      {/* ---------------- comparison / backtest matrix ---------------- */}
      {(compare || matrix) && (
        <Card style={{ marginBottom: 14 }}>
          <h3>Comparison / Backtest Matrix</h3>
          {matrix && (
            <>
              <h4 className="muted" style={{ fontSize: 12 }}>Backtest Matrix ({matrix.count} nodes)</h4>
              <BacktestMatrixTable data={matrix} onSelectNode={openDetail} />
            </>
          )}
          {compare && (
            <>
              <h4 className="muted" style={{ fontSize: 12, marginTop: 12 }}>
                Side-by-side comparison ({compare.count} nodes)
              </h4>
              {(compare.not_found || []).length > 0 && (
                <div className="warn-banner" style={{ marginBottom: 8, fontSize: 11.5 }}>
                  not found: {(compare.not_found || []).join(", ")}
                </div>
              )}
              <div className="scroll-x">
                <table className="tbl" style={{ fontSize: 11.5 }}>
                  <thead>
                    <tr>
                      <th>field</th>
                      {compare.rows.map((r) => (
                        <th key={r.id}>
                          Node_{r.id}
                          {r.identity?.research_eligible === false ? (
                            <span className="pill" style={{ fontSize: 9, marginLeft: 4 }} title={r.identity?.scope_note}>LEGACY</span>
                          ) : null}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {[
                      ["generation", (r) => `G${r.identity?.generation}`],
                      ["status", (r) => <Pill status={r.identity?.status} />],
                      ["symbol / tf", (r) => `${r.identity?.symbol || "—"} ${r.identity?.timeframe || ""}`],
                      ["direction", (r) => r.identity?.direction || "—"],
                      ["fitness", (r) => fmt.num(r.identity?.fitness, 4)],
                      ["entry (long)", (r) => (r.definition?.entry_conditions?.long || []).join(" · ") || "—"],
                      ["entry (short)", (r) => (r.definition?.entry_conditions?.short || []).join(" · ") || "—"],
                      ["SL", (r) => r.definition?.exit_conditions?.stop_loss || "—"],
                      ["TP", (r) => r.definition?.exit_conditions?.take_profit || "—"],
                      ["trailing", (r) => r.definition?.exit_conditions?.trailing_stop || "—"],
                      ["risk per trade", (r) => r.definition?.position_management?.risk_per_trade || "—"],
                      ["BACKTEST stage", (r) => r.research_results?.backtest?.stage || "N/A"],
                      ["trades", (r) => (r.research_results?.trades == null ? "N/A" : fmt.num(r.research_results.trades, 0))],
                      ["win rate", (r) => (r.research_results?.win_rate == null ? "N/A" : fmt.pct(r.research_results.win_rate))],
                      ["net P&L", (r) => (r.research_results?.net_profit == null ? "N/A" : fmt.pnl(r.research_results.net_profit))],
                      ["max drawdown", (r) => (r.research_results?.max_drawdown_pct == null ? "N/A" : fmt.pct(r.research_results.max_drawdown_pct, 2))],
                      ["profit factor", (r) => (r.research_results?.profit_factor == null ? "N/A" : fmt.ratio(r.research_results.profit_factor, 3))],
                      ["expectancy", (r) => (r.research_results?.expectancy == null ? "N/A" : fmt.num(r.research_results.expectancy, 4))],
                      ["validation passed", (r) => (r.research_results?.validation?.passed == null ? "N/A" : (r.research_results.validation.passed ? "YES" : "NO"))],
                      ["robustness", (r) => (r.research_results?.robustness_score == null ? "N/A" : fmt.ratio(r.research_results.robustness_score, 4))],
                      ["qualified", (r) => (r.research_results?.qualification?.qualified ? "YES" : "NO")],
                      ["risk per trade %", (r) => (r.node_economics?.risk_per_trade_pct == null ? "N/A" : fmt.pct(r.node_economics.risk_per_trade_pct / 100, 2))],
                      ["SL / TP (ATR)", (r) => `${r.node_economics?.sl_atr_multiple ?? "N/A"} / ${r.node_economics?.tp_atr_multiple ?? "N/A"}`],
                      ["reward / risk", (r) => (r.node_economics?.reward_risk_ratio == null ? "N/A" : fmt.ratio(r.node_economics.reward_risk_ratio, 3))],
                      ["risk amount", (r) => (r.node_economics?.risk_amount == null ? "N/A" : fmt.pnl(r.node_economics.risk_amount))],
                      ["position size", (r) => (r.node_economics?.position_size_lots == null ? "N/A" : fmt.num(r.node_economics.position_size_lots, 2))],
                    ].map(([label, render]) => (
                      <tr key={label}>
                        <td className="muted">{label}</td>
                        {compare.rows.map((r) => <td key={r.id} className="mono">{render(r)}</td>)}
                      </tr>
                    ))}
                    <tr>
                      <td className="muted">
                        EXECUTION RECORDS
                        <span className="pill" style={{ fontSize: 9, marginLeft: 4 }}>NOT RESEARCH</span>
                      </td>
                      {compare.rows.map((r) => (
                        <td key={r.id} className="mono" style={{ fontSize: 11 }}>
                          PAPER {fmt.num(r.execution?.paper?.trades ?? 0, 0)} ·
                          LIVE TEST {fmt.num(r.execution?.live_test?.total_trades ?? 0, 0)} ·
                          MT5 DEMO {fmt.num(r.execution?.mt5_demo?.total_trades ?? 0, 0)}
                        </td>
                      ))}
                    </tr>
                  </tbody>
                </table>
              </div>
              <div className="muted" style={{ fontSize: 11, marginTop: 6 }}>{compare.layers?.economics}</div>
            </>
          )}
        </Card>
      )}

      {/* ---------------- strategy detail (V4.4 component, reused) ---------------- */}
      <h3 style={{ fontSize: 13, marginBottom: 6 }}>
        Strategy detail
        <span className="muted" style={{ fontSize: 11, marginLeft: 8 }}>
          fetched only for the node you open
        </span>
      </h3>
      {detailErr && <ErrorNote err={detailErr} />}
      <NodeResearchDetail node={detail} busy={detailBusy}
                          emptyHint="Click a node id in the table above to load the V4.4 detail (research results, node economics and the separate execution records)."
                          onOpenStrategy={openInWorkspace} />
      {detail && !detailBusy && (
        <div className="btn-row" style={{ marginTop: 8 }}>
          <button className="btn" onClick={() => openInWorkspace(detail.node?.id)}>open in workspace</button>
          <button className="btn" onClick={() => toggleShortlist && toggleShortlist(detail.node?.id)}>
            {(shortlist || []).includes(detail.node?.id) ? "remove from shortlist" : "add to shortlist"}
          </button>
          <button className="btn" onClick={() => runMatrix([detail.node?.id])}>matrix for this node</button>
        </div>
      )}
    </div>
  );
}
