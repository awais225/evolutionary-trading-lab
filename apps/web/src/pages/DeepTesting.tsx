import { useEffect, useMemo, useState } from 'react'
import { api } from '../api/client'
import { useInstrument } from '../state/instrument'
import { BatchScope } from '../components/BatchScope'
import { applySort, loadSortPref, nextSortState, saveSortPref, type SortColumn, type SortState } from '../lib/tableSort'

/**
 * DeepTesting workspace — THREE areas only:
 *  1. Hard Constraints (capital, risk, MAX ACTIVE TRADES, global offsets)
 *  2. Main Node Table (filters, stars, selection, individual offsets,
 *     persisted results, sorting, Inspect)
 *  3. Selected Node Inspection below the table (statistics, daily/session
 *     tables, filters, trade ledger, charts, scenarios)
 * Everything displayed originates from actual persisted engine runs.
 */

const SESSIONS = ['asian', 'london', 'newyork', 'london_newyork_overlap', 'off_hours']
const MODES = [
  { v: 'initial', label: 'Initial Backtest' }, { v: 'deeptest', label: 'DeepTest' },
  { v: 'oos', label: 'Out-of-Sample' }, { v: 'walkforward', label: 'Walk-Forward' },
  { v: 'robustness', label: 'Robustness' },
]
const fmt = (v: unknown, d = 2): string =>
  v === null || v === undefined || (typeof v === 'number' && Number.isNaN(v))
    ? 'N/A' : typeof v === 'number' ? v.toLocaleString(undefined, { maximumFractionDigits: d }) : String(v)
const tsFmt = (ts?: number) => (ts ? new Date(ts * 1000).toISOString().replace('T', ' ').slice(0, 16) : '—')
const dayOf = (ts: number) => new Date(ts * 1000).toISOString().slice(0, 10)

function LineChart({ points, label, color = '#4da3ff' }: { points: { x: number; y: number }[]; label: string; color?: string }) {
  if (points.length < 2) return <p className="faint">{label}: not enough data (N/A).</p>
  const xs = points.map((p) => p.x), ys = points.map((p) => p.y)
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys)
  const W = 640, H = 150, PAD = 8
  const sx = (x: number) => PAD + ((x - minX) / Math.max(1, maxX - minX)) * (W - 2 * PAD)
  const sy = (y: number) => H - PAD - ((y - minY) / Math.max(1e-9, maxY - minY)) * (H - 2 * PAD)
  const d = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join(' ')
  return (
    <figure className="dt-chart"><figcaption className="faint">{label} · min {fmt(minY)} · max {fmt(maxY)}</figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={label}><path d={d} fill="none" stroke={color} strokeWidth="1.5" /></svg>
    </figure>
  )
}

function BarChart({ data, label }: { data: { label: string; value: number }[]; label: string }) {
  if (!data.length) return <p className="faint">{label}: no data (N/A).</p>
  const max = Math.max(...data.map((d) => Math.abs(d.value)), 1e-9)
  return (
    <figure className="dt-chart"><figcaption className="faint">{label}</figcaption>
      <div className="dt-bars">{data.map((d, i) => (
        <div key={i} className="dt-bar-col" title={`${d.label}: ${fmt(d.value)}`}>
          <div className={`dt-bar ${d.value < 0 ? 'neg' : ''}`} style={{ height: `${Math.max(2, (Math.abs(d.value) / max) * 56)}px` }} />
          <span>{d.label}</span><strong>{fmt(d.value, Math.abs(d.value) < 10 ? 2 : 0)}</strong>
        </div>))}
      </div></figure>
  )
}

function SortTh({ col, sort, setSort }: { col: SortColumn<any>; sort: SortState | null; setSort: (fn: (s: SortState | null) => SortState) => void }) {
  return (
    <th><button type="button" className="btn btn-ghost btn-xs" onClick={() => setSort((cur) => nextSortState(cur, col))}>
      {col.label}{sort?.key === col.key ? (sort.dir === 'asc' ? ' ↑' : ' ↓') : ''}</button></th>
  )
}

export function DeepTesting() {
  const { instrument } = useInstrument()
  /* ================= AREA 1 — HARD CONSTRAINTS ================= */
  const [capital, setCapital] = useState(10000)
  const [currency, setCurrency] = useState('USD')
  const [riskMode, setRiskMode] = useState<'percent' | 'fixed'>('percent')
  const [riskValue, setRiskValue] = useState(1)
  const [riskBasis, setRiskBasis] = useState<'current_equity' | 'initial_capital'>('current_equity')
  const [maxActive, setMaxActive] = useState(1)
  const [globalTp, setGlobalTp] = useState(0)
  const [globalSl, setGlobalSl] = useState(0)

  /* test configuration (lives with Area 1 config flow) */
  const [mode, setMode] = useState('initial')
  const [range, setRange] = useState({ start: '', end: '' })
  const [spread, setSpread] = useState(0)
  const [slippage, setSlippage] = useState(0)
  const [commission, setCommission] = useState(0)
  const [ambiguity, setAmbiguity] = useState('sl_first')
  const [sessions, setSessions] = useState<string[]>([])
  const [tickValue, setTickValue] = useState('')

  /* ================= AREA 2 — MAIN NODE TABLE ================= */
  const [nodes, setNodes] = useState<any[]>([])
  const [runs, setRuns] = useState<any[]>([])
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [nodeOffsets, setNodeOffsets] = useState<Record<number, { tp: string; sl: string }>>(() => {
    try { return JSON.parse(localStorage.getItem('hero1.dt.nodeOffsets') ?? '{}') } catch { return {} }
  })
  useEffect(() => { localStorage.setItem('hero1.dt.nodeOffsets', JSON.stringify(nodeOffsets)) }, [nodeOffsets])
  const [fSearch, setFSearch] = useState('')
  const [fStarred, setFStarred] = useState('all')
  const [fOrigin, setFOrigin] = useState('')
  const [fTf, setFTf] = useState('')
  const [fDir, setFDir] = useState('')
  const [fTesting, setFTesting] = useState('all')
  const [page, setPage] = useState(0)
  const PAGE_SIZE = 25
  const [nodeSort, setNodeSort] = useState<SortState | null>(() => loadSortPref('deeptesting-nodes', ['id', 'name']))
  useEffect(() => { saveSortPref('deeptesting-nodes', nodeSort) }, [nodeSort])

  /* ================= AREA 3 — INSPECTION ================= */
  const [inspectId, setInspectId] = useState<number | null>(null)
  const [activeRun, setActiveRun] = useState<any | null>(null)
  const [trades, setTrades] = useState<any[]>([])
  const [expanded, setExpanded] = useState<number | null>(null)
  const [tf, setTf] = useState({ logic: 'AND', outcome: '', session: '', weekday: '', exitReason: '', side: '', minPnl: '', maxPnl: '', day: '', trailing: '' })
  const [tradeSort, setTradeSort] = useState<SortState | null>(() => loadSortPref('deeptesting-trades', ['entry_ts', 'net_pnl']))
  const [dailySort, setDailySort] = useState<SortState | null>(null)
  useEffect(() => { saveSortPref('deeptesting-trades', tradeSort) }, [tradeSort])
  const [scenarios, setScenarios] = useState<any[]>([])
  const [scenarioName, setScenarioName] = useState('')
  const [genDims, setGenDims] = useState({ tp: '-10,0,10', sl: '0,5', risk: '' })
  const [genPreview, setGenPreview] = useState<number | null>(null)
  const [suggestions, setSuggestions] = useState<any[]>([])
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  useEffect(() => {
    api.nodes.list(instrument, { limit: 500 }).then((r) => setNodes(r.items ?? [])).catch(() => setNodes([]))
    refreshRuns()
    api.deeptesting.listScenarios(instrument).then((r) => setScenarios(r.scenarios ?? [])).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [instrument])

  const refreshRuns = () => api.deeptesting.listRuns(instrument).then((r) => setRuns(r.runs ?? [])).catch(() => {})

  const latestByNode = useMemo(() => {
    const map = new Map<number, any>()
    for (const r of runs) {
      if (r.status !== 'DONE') continue
      const cur = map.get(r.node_id)
      if (!cur || r.id > cur.id) map.set(r.node_id, r)
    }
    return map
  }, [runs])

  const directionOf = (n: any): string => {
    try { return JSON.parse(n.spec_json ?? '{}')?.identity?.direction ?? '—' } catch { return '—' }
  }

  const rows = useMemo(() => nodes.map((n) => {
    const run = latestByNode.get(n.id)
    const m = run?.metrics
    return {
      node: n, run, direction: directionOf(n),
      testingStatus: run ? 'TESTED' : (runs.some((r) => r.node_id === n.id && r.status === 'FAILED') ? 'FAILED' : 'UNTESTED'),
      total_trades: m?.total_trades, win_rate: m?.win_rate, net_profit: m?.net_profit,
      profit_factor: m?.profit_factor, expectancy: m?.expectancy,
      max_drawdown_amount: m?.max_drawdown_amount, max_drawdown_pct: m?.max_drawdown_pct,
      recovery_factor: m?.recovery_factor, max_consecutive_losses: m?.max_consecutive_losses,
      winning_trades: m?.winning_trades, losing_trades: m?.losing_trades,
      last_tested: run?.finished_at, scenarioCount: runs.filter((r) => r.node_id === n.id && r.scenario_id).length,
    }
  }), [nodes, latestByNode, runs])

  const filteredRows = useMemo(() => rows.filter((r) => {
    const n = r.node
    if (fSearch && !`${n.name} ${n.id}`.toLowerCase().includes(fSearch.toLowerCase())) return false
    if (fStarred === 'starred' && !n.starred) return false
    if (fStarred === 'unstarred' && n.starred) return false
    if (fOrigin && n.origin !== fOrigin) return false
    if (fTf && n.timeframe !== fTf) return false
    if (fDir && r.direction !== fDir) return false
    if (fTesting === 'tested' && r.testingStatus !== 'TESTED') return false
    if (fTesting === 'untested' && r.testingStatus === 'TESTED') return false
    return true
  }), [rows, fSearch, fStarred, fOrigin, fTf, fDir, fTesting])

  const nodeColumns: SortColumn<any>[] = [
    { key: 'id', label: 'ID', type: 'natural', get: (r) => r.node.id },
    { key: 'name', label: 'Name', type: 'text', get: (r) => r.node.name },
    { key: 'total_trades', label: 'Trades', type: 'number', get: (r) => r.total_trades },
    { key: 'win_rate', label: 'Win rate', type: 'number', get: (r) => r.win_rate },
    { key: 'net_profit', label: 'Net P/L', type: 'number', get: (r) => r.net_profit },
    { key: 'profit_factor', label: 'PF', type: 'number', get: (r) => r.profit_factor },
    { key: 'expectancy', label: 'Expectancy', type: 'number', get: (r) => r.expectancy },
    { key: 'max_drawdown_amount', label: 'Max DD', type: 'absNumber', get: (r) => r.max_drawdown_amount },
    { key: 'max_drawdown_pct', label: 'Max DD %', type: 'absNumber', get: (r) => r.max_drawdown_pct },
    { key: 'recovery_factor', label: 'Recovery', type: 'number', get: (r) => r.recovery_factor },
    { key: 'max_consecutive_losses', label: 'Consec. losses', type: 'number', get: (r) => r.max_consecutive_losses },
    { key: 'last_tested', label: 'Last tested', type: 'date', get: (r) => r.last_tested },
  ]
  const sortedRows = useMemo(() => applySort(filteredRows, nodeSort, nodeColumns),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [filteredRows, nodeSort])
  const pageRows = sortedRows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)

  const moneyRisk = riskMode === 'percent' ? (capital * riskValue) / 100 : riskValue

  const offsetSource = (nodeId: number, kind: 'tp' | 'sl'): string => {
    const v = nodeOffsets[nodeId]?.[kind]
    if (v !== undefined && v !== '') return 'Individual node'
    if ((kind === 'tp' ? globalTp : globalSl) !== 0) return 'Global DeepTesting'
    return 'Strategy'
  }

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
    ambiguity_policy: ambiguity, sessions: sessions.length ? sessions : undefined,
    global_tp_offset_pct: globalTp, global_sl_offset_pct: globalSl,
    node_offsets: Object.fromEntries(Object.entries(nodeOffsets).map(([id, o]) => [id, {
      tp: o.tp === '' ? null : Number(o.tp), sl: o.sl === '' ? null : Number(o.sl),
    }])),
    ...(tickValue ? { tick_value_account: Number(tickValue) } : {}),
    ...extra,
  })

  const runNodes = async (ids: number[], extra: Record<string, unknown> = {}) => {
    if (!ids.length) { setNotice('Select at least one node first.'); return }
    setBusy(true); setNotice(null)
    try {
      const res = await api.deeptesting.createRuns(instrument, ids, buildConfig(extra))
      const failed = res.runs.filter((r) => r.status === 'FAILED')
      setNotice(failed.length
        ? failed.map((f) => `node ${f.node_id}: ${f.error}`).join(' · ')
        : `Completed ${res.runs.length} run(s). The table now shows the persisted results.`)
      await refreshRuns()
      if (inspectId && ids.includes(inspectId)) await openNodeRun(inspectId)
    } catch (e) { setNotice(String(e)) } finally { setBusy(false) }
  }

  const openNodeRun = async (nodeId: number, runId?: number) => {
    setInspectId(nodeId)
    const target = runId ?? latestByNode.get(nodeId)?.id
    if (!target) { setActiveRun(null); setTrades([]); setSuggestions([]); return }
    const run = await api.deeptesting.getRun(instrument, target)
    setActiveRun(run)
    const t = await api.deeptesting.trades(instrument, target)
    setTrades(t.trades ?? [])
    const s = await api.deeptesting.suggestions(instrument, target).catch(() => ({ suggestions: [] }))
    setSuggestions(s.suggestions ?? [])
  }

  const toggleStar = async (n: any) => {
    const next = !n.starred
    setNodes(nodes.map((x) => (x.id === n.id ? { ...x, starred: next } : x)))
    try { await api.nodes.setStarred(instrument, n.id, next) } catch { /* keep optimistic state visible */ }
  }

  /* ---- inspection derivations ---- */
  const visibleTrades = useMemo(() => {
    const checks = trades.map((t) => {
      const parts: boolean[] = []
      if (tf.outcome) parts.push(t.result === tf.outcome)
      if (tf.session) parts.push(t.session === tf.session)
      if (tf.side) parts.push(t.side === tf.side)
      if (tf.exitReason) parts.push(String(t.exit_reason).startsWith(tf.exitReason))
      if (tf.weekday !== '') parts.push(new Date(t.entry_ts * 1000).getUTCDay() === (Number(tf.weekday) + 1) % 7)
      if (tf.day) parts.push(dayOf(t.entry_ts) === tf.day)
      if (tf.minPnl !== '') parts.push(t.net_pnl >= Number(tf.minPnl))
      if (tf.maxPnl !== '') parts.push(t.net_pnl <= Number(tf.maxPnl))
      if (tf.trailing === 'yes') parts.push((t.stop_history?.length ?? 0) > 0)
      if (tf.trailing === 'no') parts.push(!(t.stop_history?.length ?? 0))
      if (!parts.length) return true
      return tf.logic === 'AND' ? parts.every(Boolean) : parts.some(Boolean)
    })
    return trades.filter((_, i) => checks[i])
  }, [trades, tf])

  const filteredStats = useMemo(() => {
    const g = visibleTrades
    const wins = g.filter((t) => t.net_pnl > 1e-9), losses = g.filter((t) => t.net_pnl < -1e-9)
    const gp = wins.reduce((a, t) => a + t.net_pnl, 0), gl = losses.reduce((a, t) => a + t.net_pnl, 0)
    let run = 0, maxConsLoss = 0
    for (const t of [...g].sort((a, b) => a.exit_ts - b.exit_ts)) { run = t.net_pnl < -1e-9 ? run + 1 : 0; maxConsLoss = Math.max(maxConsLoss, run) }
    return { trades: g.length, wins: wins.length, losses: losses.length, net: gp + gl,
             winRate: g.length ? wins.length / g.length : null,
             pf: gl < 0 ? gp / Math.abs(gl) : null, expectancy: g.length ? (gp + gl) / g.length : null,
             maxConsLoss }
  }, [visibleTrades])

  const tradeColumns: SortColumn<any>[] = [
    { key: 'id', label: 'ID', type: 'natural', get: (t) => t.entry_ts },
    { key: 'entry_ts', label: 'Entry', type: 'date', get: (t) => t.entry_ts },
    { key: 'net_pnl', label: 'Net P/L', type: 'number', get: (t) => t.net_pnl },
    { key: 'volume', label: 'Size', type: 'number', get: (t) => t.volume },
    { key: 'holding_seconds', label: 'Holding', type: 'number', get: (t) => t.holding_seconds },
  ]
  const sortedTrades = useMemo(() => applySort(visibleTrades, tradeSort, tradeColumns),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [visibleTrades, tradeSort])

  const dailyColumns: SortColumn<any>[] = [
    { key: 'date', label: 'Date', type: 'date', get: (d) => d.date },
    { key: 'net_pnl', label: 'Net P/L', type: 'number', get: (d) => d.net_pnl },
    { key: 'trades', label: 'Trades', type: 'number', get: (d) => d.trades },
    { key: 'win_rate', label: 'Win rate', type: 'number', get: (d) => d.win_rate },
    { key: 'max_drawdown_amount', label: 'Max DD', type: 'absNumber', get: (d) => d.max_drawdown_amount },
  ]
  const m = activeRun?.metrics
  const inspectNode = nodes.find((n) => n.id === inspectId)
  const nodeScenarios = scenarios.filter((sc) => (sc.config?.node_ids ?? []).includes(inspectId))
  const rollingWinRate = useMemo(() => {
    const sorted = [...trades].sort((a, b) => a.exit_ts - b.exit_ts)
    return sorted.map((_, i) => {
      const w = sorted.slice(Math.max(0, i - 9), i + 1)
      return { x: i + 1, y: (w.filter((t) => t.net_pnl > 0).length / w.length) * 100 }
    })
  }, [trades])

  const resetFilters = () => setTf({ logic: 'AND', outcome: '', session: '', weekday: '', exitReason: '', side: '', minPnl: '', maxPnl: '', day: '', trailing: '' })

  return (
    <div className="deeptesting-page">
      <h2>DeepTesting</h2>
      {notice && <div className="json-invalid-banner" role="status">{notice}</div>}

      {/* ================= AREA 1: HARD CONSTRAINTS ================= */}
      <section className="builder-section" aria-labelledby="dt-constraints">
        <h4 id="dt-constraints"><span className="section-num">1</span> Hard Constraints — Capital, Risk, Maximum Active Trades &amp; Global Offsets</h4>
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
                <select className="input input-xs" value={riskBasis} aria-label="Risk basis" onChange={(e) => setRiskBasis(e.target.value as never)}>
                  <option value="current_equity">Current equity</option>
                  <option value="initial_capital">Initial capital</option>
                </select></label>
            )}
            <p className="faint">Money at risk: <strong>{currency} {fmt(moneyRisk)}</strong> per trade. Sizes round down; insufficient budget refuses the trade.</p>
          </fieldset>

          <fieldset className="exit-block">
            <legend>MAX ACTIVE TRADES</legend>
            <label className="mini-field">Simultaneous positions per node/scenario
              <input className="input input-xs num" type="number" min={1} max={50} value={maxActive} aria-label="Max active trades"
                     onChange={(e) => setMaxActive(Math.max(1, Math.floor(Number(e.target.value) || 1)))} /></label>
            <p className="faint">Default 1 — next trade only after the previous fully closes. Independent per node/scenario.</p>
          </fieldset>

          <fieldset className="exit-block">
            <legend>Global TP / SL offsets <span className="faint">(apply only when the node row has no individual value; never replace strategy logic or trailing)</span></legend>
            <label className="mini-field">Global TP offset %
              <input className="input input-xs num" type="number" step="any" value={globalTp} aria-label="Global TP offset"
                     onChange={(e) => setGlobalTp(Number(e.target.value))} /></label>
            <label className="mini-field">Global SL offset %
              <input className="input input-xs num" type="number" step="any" value={globalSl} aria-label="Global SL offset"
                     onChange={(e) => setGlobalSl(Number(e.target.value))} /></label>
            <p className="faint micro">Precedence: Strategy TP/SL &amp; management &gt; individual node offset &gt; global offset. Effective values + source are stored on every run.</p>
          </fieldset>

          <fieldset className="exit-block">
            <legend>Test configuration</legend>
            <label className="mini-field">Mode
              <select className="input input-xs" value={mode} onChange={(e) => setMode(e.target.value)} aria-label="Testing mode">
                {MODES.map((mo) => <option key={mo.v} value={mo.v}>{mo.label}</option>)}
              </select></label>
            <label className="mini-field">Start (UTC)
              <input className="input input-xs" type="datetime-local" value={range.start} aria-label="Start date"
                     onChange={(e) => setRange({ ...range, start: e.target.value })} /></label>
            <label className="mini-field">End (UTC)
              <input className="input input-xs" type="datetime-local" value={range.end} aria-label="End date"
                     onChange={(e) => setRange({ ...range, end: e.target.value })} /></label>
            <div>{[30, 90, 365].map((d) => (
              <button key={d} type="button" className="btn btn-ghost btn-xs"
                      onClick={() => { const end = new Date(); const start = new Date(end.getTime() - d * 86400000); setRange({ start: start.toISOString().slice(0, 16), end: end.toISOString().slice(0, 16) }) }}>{d}d</button>
            ))}</div>
            <label className="mini-field">Spread (price)
              <input className="input input-xs num" type="number" min={0} step="any" value={spread} onChange={(e) => setSpread(Number(e.target.value))} /></label>
            <label className="mini-field">Slippage (price)
              <input className="input input-xs num" type="number" min={0} step="any" value={slippage} onChange={(e) => setSlippage(Number(e.target.value))} /></label>
            <label className="mini-field">Commission / lot
              <input className="input input-xs num" type="number" min={0} step="any" value={commission} onChange={(e) => setCommission(Number(e.target.value))} /></label>
            <label className="mini-field">Tick value override
              <input className="input input-xs num" type="number" min={0} step="any" value={tickValue} placeholder="broker metadata"
                     onChange={(e) => setTickValue(e.target.value)} /></label>
            <label className="mini-field">Same-candle SL/TP
              <select className="input input-xs" value={ambiguity} onChange={(e) => setAmbiguity(e.target.value)}>
                <option value="sl_first">SL first (conservative, flagged)</option>
                <option value="tp_first">TP first (flagged)</option>
              </select></label>
            <div className="mini-field">Sessions (entry filter, UTC)
              <div>{SESSIONS.map((s) => (
                <label key={s} className="check small"><input type="checkbox" checked={sessions.includes(s)}
                  onChange={(e) => setSessions(e.target.checked ? [...sessions, s] : sessions.filter((x) => x !== s))} /> {s}</label>
              ))}</div></div>
          </fieldset>
        </div>
      </section>

      {/* ================= AREA 2: MAIN NODE TABLE ================= */}
      <section className="builder-section" aria-labelledby="dt-nodes">
        <h4 id="dt-nodes"><span className="section-num">2</span> Nodes <span className="faint">({filteredRows.length} filtered · {selected.size} selected · each node runs independently on its own instrument/timeframe)</span></h4>
        <BatchScope />
        <div className="dt-toolbar">
          <input className="input input-sm" placeholder="Search name or ID…" value={fSearch} aria-label="Search nodes"
                 onChange={(e) => { setFSearch(e.target.value); setPage(0) }} />
          <select className="input input-xs" value={fStarred} onChange={(e) => setFStarred(e.target.value)} aria-label="Starred filter">
            <option value="all">All nodes</option><option value="starred">Starred</option><option value="unstarred">Unstarred</option>
          </select>
          <select className="input input-xs" value={fOrigin} onChange={(e) => setFOrigin(e.target.value)} aria-label="Origin filter">
            <option value="">All types</option>{['CUSTOM', 'WWW', 'INTERACTIVE', 'FINE_TUNED'].map((o) => <option key={o}>{o}</option>)}
          </select>
          <select className="input input-xs" value={fTf} onChange={(e) => setFTf(e.target.value)} aria-label="Timeframe filter">
            <option value="">All timeframes</option>{['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'].map((t) => <option key={t}>{t}</option>)}
          </select>
          <select className="input input-xs" value={fDir} onChange={(e) => setFDir(e.target.value)} aria-label="Direction filter">
            <option value="">All directions</option>{['LONG', 'SHORT', 'BOTH'].map((d) => <option key={d}>{d}</option>)}
          </select>
          <select className="input input-xs" value={fTesting} onChange={(e) => setFTesting(e.target.value)} aria-label="Testing status filter">
            <option value="all">Tested + untested</option><option value="tested">Tested</option><option value="untested">Untested</option>
          </select>
          <button type="button" className="btn btn-ghost btn-xs" onClick={() => setSelected(new Set(pageRows.map((r) => r.node.id)))}>Select visible</button>
          <button type="button" className="btn btn-ghost btn-xs" onClick={() => setSelected(new Set(filteredRows.map((r) => r.node.id)))}>Select all filtered</button>
          <button type="button" className="btn btn-ghost btn-xs" onClick={() => setSelected(new Set())}>Deselect all</button>
          <button type="button" className="btn btn-primary" disabled={busy} onClick={() => runNodes([...selected])}>
            {busy ? 'Running…' : `▶ Run DeepTesting (${selected.size})`}</button>
          <button type="button" className="btn btn-ghost btn-xs" disabled={busy}
                  onClick={() => runNodes(filteredRows.map((r) => r.node.id))}>Run all filtered</button>
        </div>

        <div className="dt-table-scroll">
          <table className="node-table">
            <thead><tr>
              <th>★</th><th>✓</th>
              {nodeColumns.slice(0, 2).map((c) => <SortTh key={c.key} col={c} sort={nodeSort} setSort={setNodeSort} />)}
              <th>Type</th><th>Instrument</th><th>TF</th><th>Dir</th><th>Testing</th>
              <th>TP offset % (source)</th><th>SL offset % (source)</th>
              {nodeColumns.slice(2).map((c) => <SortTh key={c.key} col={c} sort={nodeSort} setSort={setNodeSort} />)}
              <th>Win/Loss</th><th>Scenarios</th><th></th>
            </tr></thead>
            <tbody>
              {pageRows.map((r) => {
                const n = r.node
                const off = nodeOffsets[n.id] ?? { tp: '', sl: '' }
                return (
                  <tr key={n.id} className={inspectId === n.id ? 'row-active' : ''}>
                    <td><button type="button" className="btn btn-ghost btn-xs" aria-label={`${n.starred ? 'Unstar' : 'Star'} node ${n.id}`}
                                onClick={() => toggleStar(n)}>{n.starred ? '★' : '☆'}</button></td>
                    <td><input type="checkbox" aria-label={`Select node ${n.id}`} checked={selected.has(n.id)}
                               onChange={(e) => { const nx = new Set(selected); if (e.target.checked) nx.add(n.id); else nx.delete(n.id); setSelected(nx) }} /></td>
                    <td>{n.id}</td><td>{n.name}</td><td>{n.origin}</td><td>{instrument}</td><td>{n.timeframe ?? '—'}</td>
                    <td>{r.direction}</td><td>{r.testingStatus}</td>
                    <td>
                      <input className="input input-xs num" style={{ width: 64 }} type="number" step="any" placeholder="global"
                             aria-label={`TP offset node ${n.id}`} value={off.tp}
                             onChange={(e) => setNodeOffsets({ ...nodeOffsets, [n.id]: { ...off, tp: e.target.value } })} />
                      <span className="faint micro"> {offsetSource(n.id, 'tp')}</span>
                    </td>
                    <td>
                      <input className="input input-xs num" style={{ width: 64 }} type="number" step="any" placeholder="global"
                             aria-label={`SL offset node ${n.id}`} value={off.sl}
                             onChange={(e) => setNodeOffsets({ ...nodeOffsets, [n.id]: { ...off, sl: e.target.value } })} />
                      <span className="faint micro"> {offsetSource(n.id, 'sl')}</span>
                    </td>
                    <td>{fmt(r.total_trades, 0)}</td><td>{r.win_rate != null ? `${fmt(r.win_rate * 100, 1)}%` : 'N/A'}</td>
                    <td>{fmt(r.net_profit)}</td><td>{fmt(r.profit_factor, 3)}</td><td>{fmt(r.expectancy)}</td>
                    <td>{fmt(r.max_drawdown_amount)}</td><td>{fmt(r.max_drawdown_pct)}</td>
                    <td>{fmt(r.recovery_factor, 3)}</td><td>{fmt(r.max_consecutive_losses, 0)}</td>
                    <td>{tsFmt(r.last_tested)}</td>
                    <td>{fmt(r.winning_trades, 0)}/{fmt(r.losing_trades, 0)}</td>
                    <td>{r.scenarioCount}</td>
                    <td><button type="button" className="btn btn-ghost btn-xs" onClick={() => openNodeRun(n.id)}>Inspect</button></td>
                  </tr>
                )
              })}
              {pageRows.length === 0 && <tr><td colSpan={22} className="faint">No nodes match the active filters.</td></tr>}
            </tbody>
          </table>
        </div>
        <div className="dt-toolbar">
          <button type="button" className="btn btn-ghost btn-xs" disabled={page === 0} onClick={() => setPage(page - 1)}>← Prev</button>
          <span className="faint">Page {page + 1} of {Math.max(1, Math.ceil(sortedRows.length / PAGE_SIZE))}</span>
          <button type="button" className="btn btn-ghost btn-xs" disabled={(page + 1) * PAGE_SIZE >= sortedRows.length} onClick={() => setPage(page + 1)}>Next →</button>
        </div>
      </section>

      {/* ================= AREA 3: SELECTED NODE INSPECTION ================= */}
      {inspectId !== null && (
        <section className="builder-section" aria-labelledby="dt-inspect">
          <h4 id="dt-inspect"><span className="section-num">3</span> Inspection — {inspectNode?.name ?? 'node'} <span className="faint">#{inspectId} · {instrument} {inspectNode?.timeframe ?? ''} v{inspectNode?.version}</span>
            <button type="button" className="btn btn-ghost btn-xs" onClick={() => setInspectId(null)}>✕ Close</button></h4>

          {!activeRun && <p className="faint">No completed DeepTesting run for this node yet. Run it from the table above — statistics appear here from the persisted result.</p>}

          {activeRun && m && (
            <>
              <p className="faint">Run #{activeRun.id} · {activeRun.mode}{activeRun.window_label ? ` · ${activeRun.window_label}` : ''} · fingerprint {String(activeRun.fingerprint ?? '').slice(0, 12)}… · compiler {activeRun.compiler_version} · engine {activeRun.engine_version} · readiness {activeRun.data_quality?.readiness}
                · effective offsets TP {activeRun.assumptions?.effective_offsets?.tp?.offset_pct ?? 0}% ({activeRun.assumptions?.effective_offsets?.tp?.source ?? 'strategy'}) / SL {activeRun.assumptions?.effective_offsets?.sl?.offset_pct ?? 0}% ({activeRun.assumptions?.effective_offsets?.sl?.source ?? 'strategy'})
                {(activeRun.assumptions?.effective_offsets?.warnings ?? []).map((w: string) => <span key={w} className="field-error"> {w}</span>)}
                <button type="button" className="btn btn-ghost btn-xs" onClick={async () => { await api.deeptesting.rerun(instrument, activeRun.id); await refreshRuns(); openNodeRun(inspectId) }}>Rerun</button>
              </p>

              {/* detailed statistics — ONE compact table */}
              <table className="node-table" aria-label="Detailed statistics">
                <tbody>
                  <tr><th colSpan={4}>Trading performance</th></tr>
                  <tr><td>Total trades</td><td>{m.total_trades}</td><td>Win / Loss / BE</td><td>{m.winning_trades} / {m.losing_trades} / {m.breakeven_trades}</td></tr>
                  <tr><td>Win rate</td><td>{m.win_rate != null ? `${fmt(m.win_rate * 100, 1)}%` : 'N/A'}</td><td>Gross profit / loss</td><td>{fmt(m.gross_profit)} / {fmt(m.gross_loss)}</td></tr>
                  <tr><td>Net profit</td><td>{fmt(m.net_profit)}</td><td>Profit factor</td><td>{fmt(m.profit_factor, 3)}</td></tr>
                  <tr><td>Avg win / loss</td><td>{fmt(m.avg_win)} / {fmt(m.avg_loss)}</td><td>Expectancy</td><td>{fmt(m.expectancy)}</td></tr>
                  <tr><td>Largest win / loss</td><td>{fmt(m.largest_win)} / {fmt(m.largest_loss)}</td><td>Avg / max holding</td><td>{fmt((m.avg_holding_seconds ?? 0) / 3600, 1)}h / {fmt((m.max_holding_seconds ?? 0) / 3600, 1)}h</td></tr>
                  <tr><th colSpan={4}>Account performance</th></tr>
                  <tr><td>Initial capital</td><td>{fmt(m.initial_capital, 0)} {activeRun.config?.account_currency}</td><td>Balance / Equity</td><td>{fmt(m.final_balance)} / {fmt(m.final_equity)}</td></tr>
                  <tr><td>Total return</td><td>{fmt(m.net_profit)} ({fmt(m.return_on_initial_capital_pct)}%)</td><td>Unrealized at end</td><td>{fmt(m.unrealized_at_end)}</td></tr>
                  <tr><td>Avg position size</td><td>{fmt(m.avg_volume, 3)} lots</td><td>Planned open risk (max)</td><td>{fmt(m.max_planned_open_risk)}</td></tr>
                  <tr><th colSpan={4}>Risk performance</th></tr>
                  <tr><td>Max drawdown</td><td>{fmt(m.max_drawdown_amount)} ({fmt(m.max_drawdown_pct)}%)</td><td>Average drawdown</td><td>{fmt(m.avg_drawdown_amount)}</td></tr>
                  <tr><td>Current drawdown</td><td>{fmt(m.current_drawdown_amount)} ({fmt(m.current_drawdown_pct)}%)</td><td>Recovery factor</td><td>{fmt(m.recovery_factor, 3)}</td></tr>
                  <tr><td>DD duration / recovery</td><td>{fmt(m.max_drawdown_duration_seconds / 86400, 1)}d / {fmt(m.max_recovery_seconds / 86400, 1)}d</td><td>Max simultaneous</td><td>{m.max_simultaneous_positions} (limit {activeRun.config?.max_active_trades})</td></tr>
                  <tr><td>Sharpe / Sortino</td><td>{fmt(m.sharpe_ratio, 3)} / {fmt(m.sortino_ratio, 3)}</td><td>Method</td><td className="faint">{m.sharpe_method}</td></tr>
                  <tr><th colSpan={4}>Streaks</th></tr>
                  <tr><td>Max consec. W / L</td><td>{m.max_consecutive_wins} / {m.max_consecutive_losses}</td><td>Current streak</td><td>{m.current_streak ? `${m.current_streak.kind} × ${m.current_streak.count}` : '—'}</td></tr>
                  <tr><td>Avg W / L streak</td><td>{fmt(m.streaks?.avg_winning_streak)} / {fmt(m.streaks?.avg_losing_streak)}</td><td>Long / Short net</td><td>{fmt(m.long_net)} / {fmt(m.short_net)}</td></tr>
                </tbody>
              </table>

              {/* charts */}
              <div className="dt-charts">
                <LineChart points={(activeRun.equity_curve ?? []).map((e: any) => ({ x: e.ts, y: e.equity }))} label="Equity curve" />
                <LineChart points={(activeRun.equity_curve ?? []).map((e: any) => ({ x: e.ts, y: e.balance }))} label="Balance curve" color="#7ad67a" />
                <LineChart points={(activeRun.equity_curve ?? []).reduce((acc: any[], e: any) => {
                  const peak = Math.max(acc.length ? acc[acc.length - 1].peak : -Infinity, e.equity)
                  acc.push({ x: e.ts, y: peak - e.equity, peak }); return acc
                }, [])} label="Drawdown (currency)" color="#ff7a7a" />
                <BarChart data={(m.daily ?? []).map((d: any) => ({ label: d.date.slice(5), value: d.net_pnl }))} label="Daily P/L" />
                <BarChart data={(m.time_buckets?.by_month ?? []).map((b: any) => ({ label: b.bucket, value: b.net_pnl }))} label="Monthly P/L" />
                <BarChart data={(m.time_buckets?.by_session ?? []).map((b: any) => ({ label: b.bucket, value: b.net_pnl }))} label="Session comparison (net P/L)" />
                <LineChart points={rollingWinRate} label="Rolling win rate % (10-trade window)" color="#c792ea" />
              </div>

              {/* daily + session tables */}
              <table className="node-table" aria-label="Daily performance">
                <thead><tr>
                  {dailyColumns.map((c) => <SortTh key={c.key} col={c} sort={dailySort} setSort={setDailySort} />)}
                  <th>Weekday</th><th>Win/Loss/BE</th><th>Gross +/−</th><th>Consec. losses</th><th>Avg hold</th>
                </tr></thead>
                <tbody>
                  {applySort(m.daily ?? [], dailySort, dailyColumns).map((d: any) => (
                    <tr key={d.date} onClick={() => setTf({ ...tf, day: d.date })} title="Click to filter trades to this day">
                      <td>{d.date}</td><td>{fmt(d.net_pnl)}</td><td>{d.trades}</td>
                      <td>{d.win_rate != null ? `${fmt(d.win_rate * 100, 0)}%` : 'N/A'}</td><td>{fmt(d.max_drawdown_amount)}</td>
                      <td>{d.weekday}</td><td>{d.wins}/{d.losses}/{d.breakeven}</td>
                      <td>{fmt(d.gross_profit)} / {fmt(d.gross_loss)}</td><td>{d.max_consecutive_losses}</td>
                      <td>{fmt((d.avg_holding_seconds ?? 0) / 3600, 1)}h</td>
                    </tr>
                  ))}
                  {(m.daily ?? []).length === 0 && <tr><td colSpan={10} className="faint">No daily data.</td></tr>}
                </tbody>
              </table>

              <table className="node-table" aria-label="Session performance">
                <thead><tr><th>Session (entry, UTC)</th><th>Trades</th><th>W/L</th><th>Win rate</th><th>Net P/L</th><th>PF</th><th>Expectancy</th><th>Max DD</th><th>Avg hold</th></tr></thead>
                <tbody>{(m.time_buckets?.by_session ?? []).map((b: any) => (
                  <tr key={b.bucket}><td>{b.bucket}</td><td>{b.trades}</td><td>{b.wins}/{b.losses}</td>
                    <td>{b.win_rate != null ? `${fmt(b.win_rate * 100, 1)}%` : 'N/A'}</td><td>{fmt(b.net_pnl)}</td>
                    <td>{fmt(b.profit_factor, 3)}</td><td>{fmt(b.expectancy)}</td><td>{fmt(b.max_drawdown_amount)}</td>
                    <td>{fmt((b.avg_holding_seconds ?? 0) / 3600, 1)}h</td></tr>
                ))}</tbody>
              </table>

              {/* filters */}
              <div className="dt-toolbar" aria-label="Trade filters">
                <select className="input input-xs" value={tf.logic} onChange={(e) => setTf({ ...tf, logic: e.target.value })} aria-label="Filter logic">
                  <option>AND</option><option>OR</option>
                </select>
                <select className="input input-xs" value={tf.outcome} onChange={(e) => setTf({ ...tf, outcome: e.target.value })} aria-label="Outcome filter">
                  <option value="">All outcomes</option><option value="WIN">Winning</option><option value="LOSS">Losing</option><option value="BREAKEVEN">Breakeven</option>
                </select>
                <select className="input input-xs" value={tf.session} onChange={(e) => setTf({ ...tf, session: e.target.value })} aria-label="Session filter">
                  <option value="">All sessions</option>{SESSIONS.map((s) => <option key={s}>{s}</option>)}
                </select>
                <select className="input input-xs" value={tf.side} onChange={(e) => setTf({ ...tf, side: e.target.value })} aria-label="Direction filter (trades)">
                  <option value="">Long + Short</option><option>LONG</option><option>SHORT</option>
                </select>
                <select className="input input-xs" value={tf.weekday} onChange={(e) => setTf({ ...tf, weekday: e.target.value })} aria-label="Weekday filter">
                  <option value="">All weekdays</option>{['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((d, i) => <option key={d} value={i}>{d}</option>)}
                </select>
                <select className="input input-xs" value={tf.exitReason} onChange={(e) => setTf({ ...tf, exitReason: e.target.value })} aria-label="Exit reason filter">
                  <option value="">All exit reasons</option>{['STOP_LOSS', 'TAKE_PROFIT', 'EXIT_RULE', 'MAX_HOLDING', 'TIME_EXIT', 'END_OF_DATA'].map((r) => <option key={r}>{r}</option>)}
                </select>
                <select className="input input-xs" value={tf.trailing} onChange={(e) => setTf({ ...tf, trailing: e.target.value })} aria-label="Trailing filter">
                  <option value="">Trailing: any</option><option value="yes">Trailing involved</option><option value="no">No trailing</option>
                </select>
                <input className="input input-xs num" style={{ width: 84 }} type="number" placeholder="Min P/L" value={tf.minPnl}
                       onChange={(e) => setTf({ ...tf, minPnl: e.target.value })} aria-label="Minimum P/L" />
                <input className="input input-xs num" style={{ width: 84 }} type="number" placeholder="Max P/L" value={tf.maxPnl}
                       onChange={(e) => setTf({ ...tf, maxPnl: e.target.value })} aria-label="Maximum P/L" />
                {tf.day && <span className="chip">Day: {tf.day} <button type="button" className="btn btn-ghost btn-xs" onClick={() => setTf({ ...tf, day: '' })}>✕</button></span>}
                <button type="button" className="btn btn-ghost btn-xs" onClick={resetFilters}>Reset filters</button>
                <span className="faint">Filtered: {filteredStats.trades} trades · net {fmt(filteredStats.net)} · win rate {filteredStats.winRate != null ? `${fmt(filteredStats.winRate * 100, 1)}%` : 'N/A'} · PF {fmt(filteredStats.pf, 3)} · expectancy {fmt(filteredStats.expectancy)} · max consec. losses {filteredStats.maxConsLoss} (recalculated from matching trades)</span>
              </div>

              {/* trade ledger */}
              <table className="node-table" aria-label="Trade ledger">
                <thead><tr>
                  {tradeColumns.map((c) => <SortTh key={c.key} col={c} sort={tradeSort} setSort={setTradeSort} />)}
                  <th>Side</th><th>Exit</th><th>Reason</th><th>Gross</th><th>Costs (comm/swap/spread)</th><th>Planned risk</th><th>SL → final</th><th>Result</th><th></th>
                </tr></thead>
                <tbody>
                  {sortedTrades.map((t, i) => (
                    <>
                      <tr key={`t${i}`}>
                        <td>{i + 1}</td><td>{tsFmt(t.entry_ts)}</td><td>{fmt(t.net_pnl)}</td><td>{fmt(t.volume, 2)}</td>
                        <td>{fmt(t.holding_seconds / 3600, 1)}h</td><td>{t.side}</td><td>{fmt(t.exit_price, 3)}</td>
                        <td>{t.exit_reason}{t.ambiguous_same_candle ? ' ⚠' : ''}{t.gap_overrun ? ' ⚠gap' : ''}</td>
                        <td>{fmt(t.gross_pnl)}</td><td>{fmt(t.commission)}/{fmt(t.swap)}/{fmt(t.spread_cost)}</td>
                        <td>{fmt(t.planned_risk)}</td><td>{fmt(t.initial_stop, 3)} → {fmt(t.final_stop, 3)}</td><td>{t.result}</td>
                        <td><button type="button" className="btn btn-ghost btn-xs" onClick={() => setExpanded(expanded === i ? null : i)}>Inspect</button></td>
                      </tr>
                      {expanded === i && (
                        <tr key={`d${i}`}><td colSpan={14} className="faint">
                          Entry {fmt(t.entry_price, 3)} @ {tsFmt(t.entry_ts)} · exit {fmt(t.exit_price, 3)} @ {tsFmt(t.exit_ts)} · TP {fmt(t.take_profit, 3)} · session {t.session} · MFE {fmt(t.mfe_price, 3)} / MAE {fmt(t.mae_price, 3)}<br />
                          Stop changes: {t.stop_history?.length ? t.stop_history.map((h: any) => `${tsFmt(h.ts)}: ${fmt(h.from, 3)} → ${fmt(h.to, 3)} (${h.rule})`).join(' · ') : 'none'}<br />
                          Indicators at entry: <code>{JSON.stringify(t.entry_indicators)}</code> · P/L = gross {fmt(t.gross_pnl)} − commission {fmt(t.commission)} + swap {fmt(t.swap)} (spread/slippage embedded in fills)
                        </td></tr>
                      )}
                    </>
                  ))}
                  {sortedTrades.length === 0 && <tr><td colSpan={14} className="faint">No trades match. Skipped signals: {activeRun.skipped_signals?.length ?? 0}.</td></tr>}
                </tbody>
              </table>

              {/* scenarios inside inspection */}
              <h5>Scenarios for {inspectNode?.name} <span className="faint">(node #{inspectId} v{inspectNode?.version} · edits create new versions)</span></h5>
              <div className="dt-toolbar">
                <input className="input input-sm" placeholder="Scenario name…" value={scenarioName} aria-label="Scenario name"
                       onChange={(e) => setScenarioName(e.target.value)} />
                <button type="button" className="btn btn-ghost btn-xs" onClick={async () => {
                  if (!scenarioName) { setNotice('Name the scenario first.'); return }
                  await api.deeptesting.createScenario(instrument, {
                    name: scenarioName,
                    config: buildConfig({ node_ids: [inspectId], trade_filters: tf }) })
                  setScenarioName('')
                  api.deeptesting.listScenarios(instrument).then((r) => setScenarios(r.scenarios ?? []))
                }}>Save current configuration + filters as scenario</button>
              </div>
              <table className="node-table" aria-label="Node scenarios">
                <thead><tr><th>ID</th><th>Name</th><th>Version</th><th>Capital / Risk / Max</th><th>Actions</th></tr></thead>
                <tbody>
                  {nodeScenarios.map((sc) => (
                    <tr key={sc.id}><td>{sc.id}</td><td>{sc.name}</td><td>v{sc.version}</td>
                      <td>{fmt(sc.config?.initial_capital, 0)} / {sc.config?.risk_mode} {sc.config?.risk_value} / {sc.config?.max_active_trades}</td>
                      <td>
                        <button type="button" className="btn btn-ghost btn-xs" onClick={() => runNodes([inspectId], { ...sc.config, scenario_id: sc.id })}>Rerun</button>
                        <button type="button" className="btn btn-ghost btn-xs" onClick={async () => {
                          await api.deeptesting.createScenario(instrument, { name: `${sc.name} (copy)`, folder: sc.folder, config: sc.config })
                          api.deeptesting.listScenarios(instrument).then((r) => setScenarios(r.scenarios ?? []))
                        }}>Duplicate</button>
                        <button type="button" className="btn btn-ghost btn-xs" onClick={async () => {
                          await api.deeptesting.editScenario(instrument, sc.id, { name: sc.name, config: buildConfig({ node_ids: [inspectId], trade_filters: tf }) })
                          api.deeptesting.listScenarios(instrument).then((r) => setScenarios(r.scenarios ?? []))
                        }}>Edit with current config (v+1)</button>
                        <button type="button" className="btn btn-ghost btn-xs" onClick={async () => {
                          await api.deeptesting.deleteScenario(instrument, sc.id)
                          api.deeptesting.listScenarios(instrument).then((r) => setScenarios(r.scenarios ?? []))
                        }}>Delete</button>
                      </td></tr>
                  ))}
                  {nodeScenarios.length === 0 && <tr><td colSpan={5} className="faint">No scenarios saved for this node.</td></tr>}
                </tbody>
              </table>
              <div className="dt-toolbar">
                <label className="mini-field">TP offsets %
                  <input className="input input-xs" value={genDims.tp} onChange={(e) => setGenDims({ ...genDims, tp: e.target.value })} /></label>
                <label className="mini-field">SL offsets %
                  <input className="input input-xs" value={genDims.sl} onChange={(e) => setGenDims({ ...genDims, sl: e.target.value })} /></label>
                <label className="mini-field">Risk values
                  <input className="input input-xs" value={genDims.risk} placeholder="e.g. 0.5,1,2" onChange={(e) => setGenDims({ ...genDims, risk: e.target.value })} /></label>
                <button type="button" className="btn btn-ghost btn-xs" onClick={async () => {
                  const dims: Record<string, number[]> = { tp_offset_pct: genDims.tp.split(',').map(Number), sl_offset_pct: genDims.sl.split(',').map(Number) }
                  if (genDims.risk) dims.risk_value = genDims.risk.split(',').map(Number)
                  try {
                    const res = await api.deeptesting.generateScenarios(instrument, {
                      base_config: buildConfig({ node_ids: [inspectId] }), dimensions: dims, max_count: 200, save: false })
                    setGenPreview(res.count)
                  } catch (e) { setNotice(String(e)) }
                }}>Preview count</button>
                <button type="button" className="btn btn-ghost btn-xs" onClick={async () => {
                  const dims: Record<string, number[]> = { tp_offset_pct: genDims.tp.split(',').map(Number), sl_offset_pct: genDims.sl.split(',').map(Number) }
                  if (genDims.risk) dims.risk_value = genDims.risk.split(',').map(Number)
                  try {
                    await api.deeptesting.generateScenarios(instrument, {
                      base_config: buildConfig({ node_ids: [inspectId] }), dimensions: dims, max_count: 200, save: true, folder: `node-${inspectId}` })
                    api.deeptesting.listScenarios(instrument).then((r) => setScenarios(r.scenarios ?? []))
                  } catch (e) { setNotice(String(e)) }
                }}>Generate &amp; save</button>
                {genPreview !== null && <span className="faint">{genPreview} scenario(s) (cap 200 enforced server-side)</span>}
              </div>

              {suggestions.length > 0 && (
                <div>
                  <h5>Fine-tune suggestions (evidence-based, never auto-applied)</h5>
                  {suggestions.map((s, i) => (
                    <div key={i} className="exit-body"><strong>{s.kind}</strong> — {s.rationale}
                      {s.patch?.spec && <button type="button" className="btn btn-ghost btn-xs"
                        onClick={async () => { const r = await api.deeptesting.fineTuneApply(instrument, activeRun.id, s); setNotice(`Created FINE_TUNED child node #${r.child_node_id} (v${r.version}) from #${r.parent_node_id}.`) }}>
                        Create child node</button>}
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </section>
      )}
    </div>
  )
}
