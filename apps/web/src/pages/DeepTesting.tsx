import { useEffect, useMemo, useState } from 'react'
import { api } from '../api/client'
import { BatchScope } from '../components/BatchScope'
import { useInstrument } from '../state/instrument'
import { applySort, loadSortPref, nextSortState, saveSortPref, type SortColumn, type SortState } from '../lib/tableSort'

/**
 * DeepTesting workspace (Phase 3). Order is a hard requirement:
 * 1) capital / risk / MAX ACTIVE TRADES, 2) nodes, 3) readiness,
 * 4) test configuration, 5) scenarios, then execution and results.
 * Every number shown comes from an actual persisted engine run.
 */

const SESSIONS = ['asian', 'london', 'newyork', 'london_newyork_overlap', 'off_hours']
const MODES = [
  { v: 'initial', label: 'Initial Backtest' },
  { v: 'deeptest', label: 'DeepTest' },
  { v: 'oos', label: 'Out-of-Sample' },
  { v: 'walkforward', label: 'Walk-Forward' },
  { v: 'robustness', label: 'Robustness (scenario batch)' },
]

const fmt = (v: unknown, digits = 2): string =>
  v === null || v === undefined || (typeof v === 'number' && Number.isNaN(v))
    ? 'N/A' : typeof v === 'number' ? v.toLocaleString(undefined, { maximumFractionDigits: digits }) : String(v)
const tsFmt = (ts?: number) => (ts ? new Date(ts * 1000).toISOString().replace('T', ' ').slice(0, 16) : '—')

function LineChart({ points, label, color = '#4da3ff' }: { points: { x: number; y: number }[]; label: string; color?: string }) {
  if (points.length < 2) return <p className="faint">Not enough data for the {label} chart (N/A).</p>
  const xs = points.map((p) => p.x), ys = points.map((p) => p.y)
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys)
  const W = 640, H = 180, PAD = 8
  const sx = (x: number) => PAD + ((x - minX) / Math.max(1, maxX - minX)) * (W - 2 * PAD)
  const sy = (y: number) => H - PAD - ((y - minY) / Math.max(1e-9, maxY - minY)) * (H - 2 * PAD)
  const d = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join(' ')
  return (
    <figure className="dt-chart">
      <figcaption className="faint">{label} · min {fmt(minY)} · max {fmt(maxY)}</figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={label}>
        <path d={d} fill="none" stroke={color} strokeWidth="1.5" />
      </svg>
    </figure>
  )
}

export function DeepTesting() {
  const { instrument } = useInstrument()
  // ---- 1. HARD CONSTRAINTS (mandatory, always first) ----
  const [capital, setCapital] = useState(10000)
  const [currency, setCurrency] = useState('USD')
  const [riskMode, setRiskMode] = useState<'percent' | 'fixed'>('percent')
  const [riskValue, setRiskValue] = useState(1)
  const [riskBasis, setRiskBasis] = useState<'current_equity' | 'initial_capital'>('current_equity')
  const [maxActive, setMaxActive] = useState(1)

  // ---- 2. nodes ----
  const [nodes, setNodes] = useState<any[]>([])
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [search, setSearch] = useState('')
  const [tfFilter, setTfFilter] = useState('')
  const [originFilter, setOriginFilter] = useState('')

  // ---- 4. test config ----
  const [mode, setMode] = useState('initial')
  const [range, setRange] = useState({ start: '', end: '' })
  const [spread, setSpread] = useState(0)
  const [slippage, setSlippage] = useState(0)
  const [commission, setCommission] = useState(0)
  const [ambiguity, setAmbiguity] = useState('sl_first')
  const [sessions, setSessions] = useState<string[]>([])
  const [tpOffset, setTpOffset] = useState(0)
  const [slOffset, setSlOffset] = useState(0)
  const [tickValue, setTickValue] = useState('')

  // ---- execution / results ----
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [runs, setRuns] = useState<any[]>([])
  const [activeRun, setActiveRun] = useState<any | null>(null)
  const [trades, setTrades] = useState<any[]>([])
  const [expandedTrade, setExpandedTrade] = useState<number | null>(null)
  const [readiness, setReadiness] = useState<Record<number, any>>({})
  const [weights, setWeights] = useState({ net_profit: 1, profit_factor: 1, max_drawdown_pct: 1, expectancy: 1 })
  const [ranking, setRanking] = useState<any[]>([])
  const [compareIds, setCompareIds] = useState<Set<number>>(new Set())
  const [suggestions, setSuggestions] = useState<any[]>([])
  const [scenarios, setScenarios] = useState<any[]>([])
  const [scenarioName, setScenarioName] = useState('')
  const [genDims, setGenDims] = useState({ tp: '-10,0,10', sl: '0,5' })
  const [genPreview, setGenPreview] = useState<number | null>(null)
  const [outcomeFilter, setOutcomeFilter] = useState('')
  const [sort, setSort] = useState<SortState | null>(() => loadSortPref('deeptesting-trades', ['entry_ts', 'net_pnl', 'volume', 'holding_seconds']))
  useEffect(() => { saveSortPref('deeptesting-trades', sort) }, [sort])

  useEffect(() => {
    api.nodes.list(instrument, { limit: 500 }).then((r) => setNodes(r.items ?? [])).catch(() => setNodes([]))
    refreshRuns()
    api.deeptesting.listScenarios(instrument).then((r) => setScenarios(r.scenarios ?? [])).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [instrument])

  const refreshRuns = () => api.deeptesting.listRuns(instrument).then((r) => setRuns(r.runs ?? [])).catch(() => {})

  const filteredNodes = useMemo(() => nodes.filter((n) => {
    if (search && !`${n.name} ${n.id}`.toLowerCase().includes(search.toLowerCase())) return false
    if (tfFilter && n.timeframe !== tfFilter) return false
    if (originFilter && n.origin !== originFilter) return false
    return true
  }), [nodes, search, tfFilter, originFilter])

  const moneyRisk = riskMode === 'percent' ? (capital * riskValue) / 100 : riskValue

  const buildConfig = (extra: Record<string, unknown> = {}) => ({
    mode,
    range: {
      start: range.start ? Math.floor(new Date(range.start).getTime() / 1000) : undefined,
      end: range.end ? Math.floor(new Date(range.end).getTime() / 1000) : undefined,
    },
    initial_capital: capital, account_currency: currency,
    risk_mode: riskMode, risk_value: riskValue, risk_basis: riskBasis,
    max_active_trades: maxActive,
    spread_price: spread, slippage_price: slippage, commission_per_lot_round_turn: commission,
    ambiguity_policy: ambiguity,
    sessions: sessions.length ? sessions : undefined,
    tp_offset_pct: tpOffset, sl_offset_pct: slOffset,
    ...(tickValue ? { tick_value_account: Number(tickValue) } : {}),
    ...extra,
  })

  const checkReadiness = async () => {
    const out: Record<number, any> = {}
    for (const n of nodes.filter((x) => selected.has(x.id))) {
      if (!n.timeframe) { out[n.id] = { readiness: 'BLOCKED', blocked_reasons: ['node has no timeframe/spec'] }; continue }
      try {
        out[n.id] = await api.marketData.quality(instrument, n.timeframe,
          buildConfig().range.start, buildConfig().range.end)
      } catch (e) { out[n.id] = { readiness: 'BLOCKED', blocked_reasons: [String(e)] } }
    }
    setReadiness(out)
  }

  const startRun = async (extra: Record<string, unknown> = {}) => {
    if (selected.size === 0) { setError('Select at least one node first.'); return }
    setBusy(true); setError(null)
    try {
      const res = await api.deeptesting.createRuns(instrument, [...selected], buildConfig(extra))
      const failed = res.runs.filter((r) => r.status === 'FAILED')
      if (failed.length) setError(failed.map((f) => `node ${f.node_id}: ${f.error}`).join(' · '))
      await refreshRuns()
      const first = res.runs.find((r) => r.status === 'DONE') ?? res.runs[0]
      if (first) await openRun(first.id)
    } catch (e) { setError(String(e)) } finally { setBusy(false) }
  }

  const openRun = async (id: number) => {
    const run = await api.deeptesting.getRun(instrument, id)
    setActiveRun(run)
    const t = await api.deeptesting.trades(instrument, id)
    setTrades(t.trades ?? [])
    const s = await api.deeptesting.suggestions(instrument, id).catch(() => ({ suggestions: [] }))
    setSuggestions(s.suggestions ?? [])
  }

  const doRank = async () => {
    const done = runs.filter((r) => r.status === 'DONE')
    if (!done.length) return
    const res = await api.deeptesting.rankings(instrument, { run_ids: done.map((r) => r.id), weights })
    setRanking(res.ranking ?? [])
  }

  const tradeColumns: SortColumn<any>[] = [
    { key: 'entry_ts', label: 'Entry', type: 'date', get: (t) => t.entry_ts },
    { key: 'net_pnl', label: 'Net P/L', type: 'number', get: (t) => t.net_pnl },
    { key: 'volume', label: 'Volume', type: 'number', get: (t) => t.volume },
    { key: 'holding_seconds', label: 'Holding', type: 'number', get: (t) => t.holding_seconds },
  ]
  const visibleTrades = useMemo(() => {
    let rows = trades
    if (outcomeFilter) rows = rows.filter((t) => t.result === outcomeFilter)
    return applySort(rows, sort, tradeColumns)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trades, outcomeFilter, sort])

  const equityPts = useMemo(() => (activeRun?.equity_curve ?? []).map((e: any) => ({ x: e.ts, y: e.equity })), [activeRun])
  const ddPts = useMemo(() => {
    let peak = -Infinity
    return (activeRun?.equity_curve ?? []).map((e: any) => {
      peak = Math.max(peak, e.equity)
      return { x: e.ts, y: peak - e.equity }
    })
  }, [activeRun])
  const m = activeRun?.metrics

  return (
    <div className="deeptesting-page">
      <h2>DeepTesting</h2>
      {error && <div className="json-invalid-banner" role="alert">{error}</div>}

      {/* ================= 1. HARD CONSTRAINTS ================= */}
      <section className="builder-section" aria-labelledby="dt-constraints">
        <h4 id="dt-constraints"><span className="section-num">1</span> Hard Constraints — Capital, Risk &amp; Maximum Active Trades <span className="faint">(mandatory execution rules)</span></h4>
        <div className="exits-grid">
          <fieldset className="exit-block">
            <legend>Initial capital</legend>
            <div className="segmented mini" role="group" aria-label="Capital presets">
              {[1000, 5000, 10000, 25000].map((v) => (
                <button key={v} type="button" className={capital === v ? 'active' : ''} onClick={() => setCapital(v)}>${v.toLocaleString()}</button>
              ))}
            </div>
            <label className="mini-field">Custom amount
              <input className="input input-xs num" type="number" min={1} value={capital} aria-label="Initial capital"
                     onChange={(e) => setCapital(Math.max(0, Number(e.target.value)))} /></label>
            <label className="mini-field">Account currency
              <input className="input input-xs" value={currency} aria-label="Account currency"
                     onChange={(e) => setCurrency(e.target.value.toUpperCase().slice(0, 3))} /></label>
          </fieldset>

          <fieldset className="exit-block">
            <legend>Risk per trade</legend>
            <div className="segmented mini" role="group" aria-label="Risk mode">
              <button type="button" className={riskMode === 'percent' ? 'active' : ''} onClick={() => setRiskMode('percent')}>% of account</button>
              <button type="button" className={riskMode === 'fixed' ? 'active' : ''} onClick={() => setRiskMode('fixed')}>Fixed amount</button>
            </div>
            <label className="mini-field">{riskMode === 'percent' ? 'Risk % per trade' : `Fixed risk (${currency})`}
              <input className="input input-xs num" type="number" min={0} step="0.1" value={riskValue} aria-label="Risk per trade"
                     onChange={(e) => setRiskValue(Math.max(0, Number(e.target.value)))} /></label>
            {riskMode === 'percent' && (
              <label className="mini-field">Percentage basis
                <select className="input input-xs" value={riskBasis} aria-label="Risk basis"
                        onChange={(e) => setRiskBasis(e.target.value as never)}>
                  <option value="current_equity">Current equity</option>
                  <option value="initial_capital">Initial capital</option>
                </select></label>
            )}
            <p className="faint">Money at risk per trade: <strong>{currency} {fmt(moneyRisk)}</strong> (recomputed from live equity at each entry when basis = current equity). Volume rounds down to the instrument step; a budget that cannot buy the minimum volume refuses the trade — risk is never silently exceeded.</p>
          </fieldset>

          <fieldset className="exit-block">
            <legend>MAX ACTIVE TRADES</legend>
            <label className="mini-field">Simultaneous open positions per node/scenario
              <input className="input input-xs num" type="number" min={1} max={50} value={maxActive} aria-label="Max active trades"
                     onChange={(e) => setMaxActive(Math.max(1, Math.floor(Number(e.target.value) || 1)))} /></label>
            <p className="faint">Default 1: a node takes its next eligible trade only after the previous one fully closes (SL, TP, trailing, exit rule or time exit). Signals arriving while full are skipped, never queued. Applies independently per node and scenario.</p>
          </fieldset>
        </div>
      </section>

      {/* ================= 2. NODE SELECTION ================= */}
      <section className="builder-section" aria-labelledby="dt-nodes">
        <h4 id="dt-nodes"><span className="section-num">2</span> Node Selection <span className="faint">({selected.size} selected · each node tested on its OWN instrument/timeframe)</span></h4>
        <BatchScope />
        <p className="faint">The shared scope above is informational (starred/timeframe catalogue scope). DeepTesting always executes each node on its OWN instrument and timeframe — the scope never overrides a node's identity.</p>
        <div className="dt-toolbar">
          <input className="input input-sm" placeholder="Search name or ID…" value={search} aria-label="Search nodes"
                 onChange={(e) => setSearch(e.target.value)} />
          <select className="input input-xs" value={tfFilter} onChange={(e) => setTfFilter(e.target.value)} aria-label="Timeframe filter">
            <option value="">All timeframes</option>
            {['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'].map((tf) => <option key={tf}>{tf}</option>)}
          </select>
          <select className="input input-xs" value={originFilter} onChange={(e) => setOriginFilter(e.target.value)} aria-label="Origin filter">
            <option value="">All origins</option>
            {['CUSTOM', 'WWW', 'INTERACTIVE', 'FINE_TUNED'].map((o) => <option key={o}>{o}</option>)}
          </select>
          <button type="button" className="btn btn-ghost btn-xs" onClick={() => setSelected(new Set(nodes.map((n) => n.id)))}>Select all</button>
          <button type="button" className="btn btn-ghost btn-xs" onClick={() => setSelected(new Set(filteredNodes.map((n) => n.id)))}>Select filtered</button>
          <button type="button" className="btn btn-ghost btn-xs" onClick={() => setSelected(new Set())}>Deselect all</button>
        </div>
        <table className="node-table">
          <thead><tr><th></th><th>ID</th><th>Name</th><th>Instrument</th><th>TF</th><th>Version</th><th>Status</th><th>Readiness</th></tr></thead>
          <tbody>
            {filteredNodes.map((n) => (
              <tr key={n.id}>
                <td><input type="checkbox" aria-label={`Select node ${n.id}`} checked={selected.has(n.id)}
                           onChange={(e) => { const next = new Set(selected); if (e.target.checked) next.add(n.id); else next.delete(n.id); setSelected(next) }} /></td>
                <td>{n.id}</td><td>{n.name}</td><td>{instrument}</td><td>{n.timeframe ?? '—'}</td>
                <td>v{n.version}</td><td>{n.status}</td>
                <td>{readiness[n.id] ? <span className={`chip chip-${String(readiness[n.id].readiness).toLowerCase()}`}>{readiness[n.id].readiness}</span> : '—'}</td>
              </tr>
            ))}
            {filteredNodes.length === 0 && <tr><td colSpan={8} className="faint">No nodes match. Nodes without a compiled spec are listed but their runs fail explicitly with the reason.</td></tr>}
          </tbody>
        </table>
      </section>

      {/* ================= 3/4. READINESS + TEST CONFIG ================= */}
      <section className="builder-section" aria-labelledby="dt-config">
        <h4 id="dt-config"><span className="section-num">3</span> Test Configuration &amp; Data Readiness</h4>
        <div className="exits-grid">
          <fieldset className="exit-block">
            <legend>Mode &amp; range (UTC)</legend>
            <label className="mini-field">Testing mode
              <select className="input input-xs" value={mode} onChange={(e) => setMode(e.target.value)} aria-label="Testing mode">
                {MODES.map((mo) => <option key={mo.v} value={mo.v}>{mo.label}</option>)}
              </select></label>
            <label className="mini-field">Start
              <input className="input input-xs" type="datetime-local" value={range.start} aria-label="Start date"
                     onChange={(e) => setRange({ ...range, start: e.target.value })} /></label>
            <label className="mini-field">End
              <input className="input input-xs" type="datetime-local" value={range.end} aria-label="End date"
                     onChange={(e) => setRange({ ...range, end: e.target.value })} /></label>
            <div>
              {[30, 90, 365].map((d) => (
                <button key={d} type="button" className="btn btn-ghost btn-xs"
                        onClick={() => { const end = new Date(); const start = new Date(end.getTime() - d * 86400000); setRange({ start: start.toISOString().slice(0, 16), end: end.toISOString().slice(0, 16) }) }}>{d}d preset</button>
              ))}
            </div>
            <button type="button" className="btn btn-ghost btn-xs" onClick={checkReadiness}>Check data readiness for selected nodes</button>
            {Object.values(readiness).some((r: any) => r?.readiness === 'BLOCKED') &&
              <p className="field-error">At least one selected node is BLOCKED for this range — BLOCKED data is never tested; its run will record FAILED with the blocking reasons.</p>}
          </fieldset>

          <fieldset className="exit-block">
            <legend>Execution assumptions <span className="faint">(explicit — stored with every run)</span></legend>
            <label className="mini-field">Spread (price units, ask = bid + spread)
              <input className="input input-xs num" type="number" min={0} step="any" value={spread} onChange={(e) => setSpread(Number(e.target.value))} /></label>
            <label className="mini-field">Slippage (price units)
              <input className="input input-xs num" type="number" min={0} step="any" value={slippage} onChange={(e) => setSlippage(Number(e.target.value))} /></label>
            <label className="mini-field">Commission per lot (round turn)
              <input className="input input-xs num" type="number" min={0} step="any" value={commission} onChange={(e) => setCommission(Number(e.target.value))} /></label>
            <label className="mini-field">Tick value override (account ccy / tick / lot)
              <input className="input input-xs num" type="number" min={0} step="any" value={tickValue} placeholder="from broker metadata"
                     onChange={(e) => setTickValue(e.target.value)} /></label>
            <label className="mini-field">Same-candle SL/TP ambiguity
              <select className="input input-xs" value={ambiguity} onChange={(e) => setAmbiguity(e.target.value)}>
                <option value="sl_first">Conservative: SL first (flagged)</option>
                <option value="tp_first">TP first (flagged)</option>
              </select></label>
            <p className="faint micro">Historical bid/ask is not stored; spread/slippage are documented assumptions, never claimed as historical fact. Same-candle ambiguity is flagged on every affected trade.</p>
          </fieldset>

          <fieldset className="exit-block">
            <legend>Entry filters &amp; TP/SL offsets (scenario)</legend>
            <div className="mini-field">Sessions (entry-time attribution, UTC)
              <div>{SESSIONS.map((s) => (
                <label key={s} className="check small"><input type="checkbox" checked={sessions.includes(s)}
                  onChange={(e) => setSessions(e.target.checked ? [...sessions, s] : sessions.filter((x) => x !== s))} /> {s}</label>
              ))}</div></div>
            <label className="mini-field">TP offset %
              <input className="input input-xs num" type="number" step="any" value={tpOffset} aria-label="TP offset percent"
                     onChange={(e) => setTpOffset(Number(e.target.value))} /></label>
            <label className="mini-field">SL offset %
              <input className="input input-xs num" type="number" step="any" value={slOffset} aria-label="SL offset percent"
                     onChange={(e) => setSlOffset(Number(e.target.value))} /></label>
            <p className="faint micro">Offsets re-execute the strategy with adjusted distances — results are never just rescaled. The original node is never modified.</p>
          </fieldset>
        </div>
        <div className="dt-runbar">
          <strong>Configuration:</strong> {currency} {fmt(capital, 0)} capital · risk {riskMode === 'percent' ? `${riskValue}% (${riskBasis.replace('_', ' ')}) ≈ ${currency} ${fmt(moneyRisk)}` : `${currency} ${fmt(riskValue)} fixed`} · MAX ACTIVE {maxActive} · {mode} · {selected.size} node(s)
          <button type="button" className="btn btn-primary" disabled={busy} onClick={() => startRun()}>
            {busy ? 'Running…' : '▶ Start DeepTesting'}
          </button>
        </div>
      </section>

      {/* ================= 5. SCENARIOS ================= */}
      <section className="builder-section" aria-labelledby="dt-scenarios">
        <h4 id="dt-scenarios"><span className="section-num">4</span> Scenarios <span className="faint">(immutable snapshots — edits create new versions, history is never overwritten)</span></h4>
        <div className="dt-toolbar">
          <input className="input input-sm" placeholder="Scenario name…" value={scenarioName} aria-label="Scenario name"
                 onChange={(e) => setScenarioName(e.target.value)} />
          <button type="button" className="btn btn-ghost btn-xs" onClick={async () => {
            if (!scenarioName) { setError('Name the scenario first.'); return }
            await api.deeptesting.createScenario(instrument, { name: scenarioName, config: buildConfig() })
            setScenarioName('')
            api.deeptesting.listScenarios(instrument).then((r) => setScenarios(r.scenarios ?? []))
          }}>Save current configuration as scenario</button>
        </div>
        <table className="node-table">
          <thead><tr><th>ID</th><th>Name</th><th>Folder</th><th>Version</th><th>Actions</th></tr></thead>
          <tbody>
            {scenarios.map((sc) => (
              <tr key={sc.id}><td>{sc.id}</td><td>{sc.name}</td><td>{sc.folder ?? '—'}</td><td>v{sc.version}{sc.parent_scenario_id ? ` (from #${sc.parent_scenario_id})` : ''}</td>
                <td>
                  <button type="button" className="btn btn-ghost btn-xs" onClick={() => startRun({ ...sc.config, scenario_id: sc.id })}>Run on selected nodes</button>
                  <button type="button" className="btn btn-ghost btn-xs" onClick={async () => {
                    const edited = { ...sc.config, max_active_trades: maxActive, initial_capital: capital }
                    await api.deeptesting.editScenario(instrument, sc.id, { name: sc.name, config: edited })
                    api.deeptesting.listScenarios(instrument).then((r) => setScenarios(r.scenarios ?? []))
                  }}>Edit with current constraints (new version)</button>
                  <button type="button" className="btn btn-ghost btn-xs" onClick={async () => {
                    await api.deeptesting.deleteScenario(instrument, sc.id)
                    api.deeptesting.listScenarios(instrument).then((r) => setScenarios(r.scenarios ?? []))
                  }}>Delete</button>
                </td></tr>
            ))}
            {scenarios.length === 0 && <tr><td colSpan={5} className="faint">No saved scenarios.</td></tr>}
          </tbody>
        </table>
        <div className="dt-toolbar">
          <label className="mini-field">TP offsets % (comma list)
            <input className="input input-xs" value={genDims.tp} onChange={(e) => setGenDims({ ...genDims, tp: e.target.value })} /></label>
          <label className="mini-field">SL offsets % (comma list)
            <input className="input input-xs" value={genDims.sl} onChange={(e) => setGenDims({ ...genDims, sl: e.target.value })} /></label>
          <button type="button" className="btn btn-ghost btn-xs" onClick={async () => {
            try {
              const res = await api.deeptesting.generateScenarios(instrument, {
                base_config: buildConfig(),
                dimensions: { tp_offset_pct: genDims.tp.split(',').map(Number), sl_offset_pct: genDims.sl.split(',').map(Number) },
                max_count: 200, save: false })
              setGenPreview(res.count)
            } catch (e) { setError(String(e)) }
          }}>Preview scenario count</button>
          <button type="button" className="btn btn-ghost btn-xs" onClick={async () => {
            try {
              await api.deeptesting.generateScenarios(instrument, {
                base_config: buildConfig(),
                dimensions: { tp_offset_pct: genDims.tp.split(',').map(Number), sl_offset_pct: genDims.sl.split(',').map(Number) },
                max_count: 200, save: true, folder: 'generated' })
              api.deeptesting.listScenarios(instrument).then((r) => setScenarios(r.scenarios ?? []))
            } catch (e) { setError(String(e)) }
          }}>Generate &amp; save scenarios</button>
          {genPreview !== null && <span className="faint">Will generate {genPreview} scenario(s) — combinatorial cap 200 enforced.</span>}
        </div>
      </section>

      {/* ================= RESULTS ================= */}
      <section className="builder-section" aria-labelledby="dt-results">
        <h4 id="dt-results"><span className="section-num">5</span> Results Overview &amp; History</h4>
        <table className="node-table">
          <thead><tr><th></th><th>Run</th><th>Node</th><th>Mode/Window</th><th>Status</th><th>Trades</th><th>Net P/L</th><th>Win rate</th><th>Max DD %</th><th>Finished</th><th></th></tr></thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.id} className={activeRun?.id === r.id ? 'row-active' : ''}>
                <td><input type="checkbox" aria-label={`Compare run ${r.id}`} checked={compareIds.has(r.id)}
                           onChange={(e) => { const nx = new Set(compareIds); if (e.target.checked) nx.add(r.id); else nx.delete(r.id); setCompareIds(nx) }} /></td>
                <td>#{r.id}</td><td>#{r.node_id} v{r.node_version}</td><td>{r.mode}{r.window_label ? ` · ${r.window_label}` : ''}</td>
                <td>{r.status}{r.error ? ` — ${r.error}` : ''}</td>
                <td>{fmt(r.metrics?.total_trades, 0)}</td><td>{fmt(r.metrics?.net_profit)}</td>
                <td>{r.metrics?.win_rate != null ? `${fmt(r.metrics.win_rate * 100, 1)}%` : 'N/A'}</td>
                <td>{fmt(r.metrics?.max_drawdown_pct, 2)}</td><td>{tsFmt(r.finished_at)}</td>
                <td>
                  <button type="button" className="btn btn-ghost btn-xs" onClick={() => openRun(r.id)}>Open</button>
                  <button type="button" className="btn btn-ghost btn-xs" onClick={async () => { await api.deeptesting.rerun(instrument, r.id); refreshRuns() }}>Rerun</button>
                </td>
              </tr>
            ))}
            {runs.length === 0 && <tr><td colSpan={11} className="faint">No DeepTesting runs yet. Configure the hard constraints, select nodes and start a test.</td></tr>}
          </tbody>
        </table>

        <div className="dt-toolbar">
          <span className="faint">Ranking weights:</span>
          {Object.keys(weights).map((k) => (
            <label key={k} className="mini-field">{k.replace(/_/g, ' ')}
              <input className="input input-xs num" type="number" min={0} step="0.5" value={(weights as any)[k]}
                     onChange={(e) => setWeights({ ...weights, [k]: Number(e.target.value) })} /></label>
          ))}
          <button type="button" className="btn btn-ghost btn-xs" onClick={doRank}>Rank completed runs</button>
        </div>
        {ranking.length > 0 && (
          <table className="node-table">
            <thead><tr><th>Rank</th><th>Run</th><th>Node</th><th>Score</th><th>Eligible</th></tr></thead>
            <tbody>{ranking.map((r) => (
              <tr key={r.run_id}><td>{r.rank ?? '—'}</td><td>#{r.run_id}</td><td>#{r.node_id}</td>
                <td>{fmt(r.rank_score, 3)}</td><td>{r.eligible ? 'yes' : r.ineligible_reason}</td></tr>
            ))}</tbody>
          </table>
        )}

        {compareIds.size > 1 && (
          <table className="node-table" aria-label="Run comparison">
            <thead><tr><th>Metric</th>{runs.filter((r) => compareIds.has(r.id)).map((r) => <th key={r.id}>Run #{r.id} (node #{r.node_id})</th>)}</tr></thead>
            <tbody>
              {['total_trades', 'net_profit', 'win_rate', 'profit_factor', 'expectancy', 'max_drawdown_amount', 'max_drawdown_pct', 'recovery_factor', 'sharpe_ratio', 'max_consecutive_losses'].map((k) => (
                <tr key={k}><td>{k.replace(/_/g, ' ')}</td>
                  {runs.filter((r) => compareIds.has(r.id)).map((r) => <td key={r.id}>{fmt(r.metrics?.[k], 3)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {/* ================= RUN DETAIL ================= */}
      {activeRun && (
        <section className="builder-section" aria-labelledby="dt-detail">
          <h4 id="dt-detail"><span className="section-num">6</span> Run #{activeRun.id} — Node #{activeRun.node_id} v{activeRun.node_version} · {activeRun.instrument} {activeRun.timeframe}
            <span className="faint"> · fingerprint {String(activeRun.fingerprint ?? '').slice(0, 12)}… · compiler {activeRun.compiler_version} · engine {activeRun.engine_version}</span></h4>
          <p className="faint">Config: capital {fmt(activeRun.config?.initial_capital, 0)} {activeRun.config?.account_currency} · risk {activeRun.config?.risk_mode} {activeRun.config?.risk_value} ({activeRun.config?.risk_basis}) · MAX ACTIVE {activeRun.config?.max_active_trades} · readiness {activeRun.data_quality?.readiness} · spread {activeRun.assumptions?.spread_price} ({activeRun.assumptions?.spread_source}) · ambiguity {activeRun.assumptions?.ambiguity_policy}</p>
          {m && (
            <div className="metrics-grid">
              {[
                ['Trades', fmt(m.total_trades, 0)], ['Win rate', m.win_rate != null ? `${fmt(m.win_rate * 100, 1)}%` : 'N/A'],
                ['Net P/L', fmt(m.net_profit)], ['Gross profit', fmt(m.gross_profit)], ['Gross loss', fmt(m.gross_loss)],
                ['Profit factor', fmt(m.profit_factor, 3)], ['Expectancy', fmt(m.expectancy)],
                ['Max DD', `${fmt(m.max_drawdown_amount)} (${fmt(m.max_drawdown_pct)}%)`],
                ['Recovery factor', fmt(m.recovery_factor, 3)], ['Sharpe (daily, √252)', fmt(m.sharpe_ratio, 3)],
                ['Sortino', fmt(m.sortino_ratio, 3)], ['Max consec. losses', fmt(m.max_consecutive_losses, 0)],
                ['Final balance', fmt(m.final_balance)], ['Final equity', fmt(m.final_equity)],
                ['Long / Short net', `${fmt(m.long_net)} / ${fmt(m.short_net)}`],
                ['Max simultaneous', fmt(m.max_simultaneous_positions, 0)],
              ].map(([k, v]) => <div key={k} className="metric-cell"><span className="faint">{k}</span><strong>{v}</strong></div>)}
            </div>
          )}
          <LineChart points={equityPts} label="Equity curve (bar-close marks, includes unrealized)" />
          <LineChart points={ddPts} label="Drawdown (account currency, equity-based)" color="#ff7a7a" />
          {m?.time_buckets?.by_session?.length > 0 && (
            <table className="node-table" aria-label="Session performance">
              <thead><tr><th>Session (entry, UTC)</th><th>Trades</th><th>Win rate</th><th>Net P/L</th><th>Expectancy</th></tr></thead>
              <tbody>{m.time_buckets.by_session.map((b: any) => (
                <tr key={b.bucket}><td>{b.bucket}</td><td>{b.trades}</td><td>{fmt(b.win_rate * 100, 1)}%</td><td>{fmt(b.net_pnl)}</td><td>{fmt(b.expectancy)}</td></tr>
              ))}</tbody>
            </table>
          )}

          <h5>Trade ledger</h5>
          <div className="dt-toolbar">
            <select className="input input-xs" value={outcomeFilter} onChange={(e) => setOutcomeFilter(e.target.value)} aria-label="Outcome filter">
              <option value="">All outcomes</option><option value="WIN">Winning</option><option value="LOSS">Losing</option><option value="BREAKEVEN">Breakeven</option>
            </select>
            <span className="faint">{visibleTrades.length} trade(s) · filters apply to the full ledger</span>
          </div>
          <table className="node-table">
            <thead><tr>
              {tradeColumns.map((c) => (
                <th key={c.key}><button type="button" className="btn btn-ghost btn-xs" onClick={() => setSort((cur) => nextSortState(cur, c))}>
                  {c.label}{sort?.key === c.key ? (sort.dir === 'asc' ? ' ↑' : ' ↓') : ''}</button></th>
              ))}
              <th>Side</th><th>Entry</th><th>Exit</th><th>Reason</th><th>Result</th><th></th></tr></thead>
            <tbody>
              {visibleTrades.map((t, i) => (
                <>
                  <tr key={`t${i}`}>
                    <td>{tsFmt(t.entry_ts)}</td><td>{fmt(t.net_pnl)}</td><td>{fmt(t.volume, 2)}</td><td>{fmt(t.holding_seconds / 3600, 1)}h</td>
                    <td>{t.side}</td><td>{fmt(t.entry_price, 3)}</td><td>{fmt(t.exit_price, 3)}</td>
                    <td>{t.exit_reason}{t.ambiguous_same_candle ? ' ⚠ ambiguous' : ''}{t.gap_overrun ? ' ⚠ gap' : ''}</td>
                    <td>{t.result}</td>
                    <td><button type="button" className="btn btn-ghost btn-xs" onClick={() => setExpandedTrade(expandedTrade === i ? null : i)}>Inspect</button></td>
                  </tr>
                  {expandedTrade === i && (
                    <tr key={`d${i}`}><td colSpan={10} className="faint">
                      Planned risk {fmt(t.planned_risk)} · initial SL {fmt(t.initial_stop, 3)} · TP {fmt(t.take_profit, 3)} · final SL {fmt(t.final_stop, 3)} ·
                      gross {fmt(t.gross_pnl)} · commission {fmt(t.commission)} · swap {fmt(t.swap)} · spread cost {fmt(t.spread_cost)} · session {t.session} ·
                      MFE {fmt(t.mfe_price, 3)} / MAE {fmt(t.mae_price, 3)}<br />
                      Stop history: {t.stop_history?.length ? t.stop_history.map((h: any) => `${tsFmt(h.ts)}: ${fmt(h.from, 3)} → ${fmt(h.to, 3)} (${h.rule})`).join(' · ') : 'no modifications'}<br />
                      Indicators at entry: <code>{JSON.stringify(t.entry_indicators)}</code>
                    </td></tr>
                  )}
                </>
              ))}
              {visibleTrades.length === 0 && <tr><td colSpan={10} className="faint">No trades in this run (signals may have been skipped — see skipped signals: {activeRun.skipped_signals?.length ?? 0}).</td></tr>}
            </tbody>
          </table>

          {suggestions.length > 0 && (
            <div>
              <h5>Fine-tune suggestions (evidence-based, never auto-applied)</h5>
              {suggestions.map((s, i) => (
                <div key={i} className="exit-body">
                  <strong>{s.kind}</strong> — {s.rationale}
                  {s.patch?.spec && <button type="button" className="btn btn-ghost btn-xs"
                    onClick={async () => { const r = await api.deeptesting.fineTuneApply(instrument, activeRun.id, s); setError(`Created FINE_TUNED child node #${r.child_node_id} (v${r.version}) from parent #${r.parent_node_id} — retest it from Node Selection.`) }}>
                    Create child node</button>}
                </div>
              ))}
            </div>
          )}
        </section>
      )}
    </div>
  )
}
