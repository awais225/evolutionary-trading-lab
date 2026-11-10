import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api/client'
import { useInstrument } from '../state/instrument'
import { TERMINAL, useTasks } from '../state/tasks'
import { downloadFile } from '../api/client'
import { classifyDeepTestRun, DeepTestingErrorPanel } from '../components/DeepTestingErrorPanel'
import { applySort, loadSortPref, nextSortState, saveSortPref, type SortColumn, type SortState } from '../lib/tableSort'

/**
 * DeepTesting workspace — the compact, table-driven layout:
 *  1. Hard Constraints (capital / risk / MAX ACTIVE / global SL·TP offsets)
 *  2. Date-range buttons (1W … 3Y, partial testing supported)
 *  3. Main Node Table (filters, stars, selection, 4 individual offsets,
 *     TESTED / PARTIAL TESTED statistics, sorting, horizontal scroll)
 *  4. Selected Node Inspection: complete statistics table, then the
 *     weekday/session filter table, SAVE FILTERS AS SCENARIO, and ONE
 *     equity curve. Nothing else.
 */

const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'] as const
/** Grid columns: Mon-Fri plus an explicit UNASSIGNED bucket. Trades whose
 * entry falls on a weekend (e.g. Sunday-evening market open) are NEVER
 * silently dropped from the breakdown - they land in UNASSIGNED so the
 * weekday/session totals always reconcile with the main statistics. */
const COL_DAYS: string[] = [...WEEKDAYS, 'UNASSIGNED']
export const weekdayOf = (t: any): string =>
  WEEKDAYS[new Date(t.entry_ts * 1000).getUTCDay() - 1] ?? 'UNASSIGNED'
const SESSION_ROWS = ['TOTAL DAY', 'ASIA', 'ASIA-LONDON', 'LONDON', 'LONDON-NY', 'NY', 'UNASSIGNED'] as const
const DURATIONS = [
  { label: '1 Week', days: 7 }, { label: '2 Weeks', days: 14 },
  { label: '1 Month', days: 30 }, { label: '3 Months', days: 91 },
  { label: '6 Months', days: 182 }, { label: '1 Year', days: 365 },
  { label: '2 Years', days: 730 }, { label: '3 Years', days: 1095 },
  { label: 'TILL DATA', days: -1 },
]

/** Single-label session cells (UTC, entry time). Windows are contiguous
 * and non-overlapping so weekday/session cells never double-count:
 * ASIA 00-08 & 22-24, ASIA-LONDON 08-09, LONDON 09-13, LONDON-NY 13-17,
 * NY 17-22. ASIA-LONDON / LONDON-NY are the explicitly defined overlaps. */
export function sessionCell(ts: number): string {
  const date = new Date(ts * 1000)
  const day = date.getUTCDay()
  if (day === 0 || day === 6) return 'UNASSIGNED'
  const h = date.getUTCHours()
  if (h >= 22 || h < 8) return 'ASIA'
  if (h < 9) return 'ASIA-LONDON'
  if (h < 13) return 'LONDON'
  if (h < 17) return 'LONDON-NY'
  return 'NY'
}

const fmtDist = (d: any): string => {
  if (!d) return 'none declared'
  if (d.ratio !== null && d.ratio !== undefined) return `SL distance x ${d.ratio} (ratio)`
  const dist = d.distance ?? d
  if (dist && typeof dist === 'object' && dist.value !== undefined) return `${dist.value} ${dist.unit}`
  return 'declared'
}
const fmt = (v: unknown, d = 2): string =>
  v === null || v === undefined || (typeof v === 'number' && Number.isNaN(v))
    ? 'N/A' : typeof v === 'number' ? v.toLocaleString(undefined, { maximumFractionDigits: d }) : String(v)
const tsFmt = (ts?: number) => (ts ? new Date(ts * 1000).toISOString().replace('T', ' ').slice(0, 16) : '—')

function EquityCurve({ points, capital, label }: { points: { x: number; y: number; title: string }[]; capital: number; label: string }) {
  if (points.length < 2) return <p className="faint">{label}: not enough equity data (N/A).</p>
  const W = 900, H = 260, PAD = 10
  const xs = points.map((p) => p.x), ys = [...points.map((p) => p.y), capital]
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys)
  const sx = (x: number) => PAD + ((x - minX) / Math.max(1, maxX - minX)) * (W - 2 * PAD)
  const sy = (y: number) => H - PAD - ((y - minY) / Math.max(1e-9, maxY - minY)) * (H - 2 * PAD)
  const capY = sy(capital)
  // split into green/red segments at capital crossings
  const segments: { d: string; color: string }[] = []
  let cur: { d: string; color: string } | null = null
  const push = (x: number, y: number, color: string, move: boolean) => {
    if (!cur || cur.color !== color) {
      cur = { d: `${move ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`, color }
      segments.push(cur)
    } else cur.d += ` L${x.toFixed(1)},${y.toFixed(1)}`
  }
  points.forEach((p, i) => {
    const color = p.y >= capital ? '#3fb950' : '#f85149'
    if (i > 0) {
      const prev = points[i - 1]
      if ((prev.y - capital) * (p.y - capital) < 0) {
        const t = (capital - prev.y) / (p.y - prev.y)
        const cx = sx(prev.x + (p.x - prev.x) * t)
        push(cx, capY, prev.y >= capital ? '#3fb950' : '#f85149', false)
        cur = null
        push(cx, capY, color, true)
      }
    }
    push(sx(p.x), sy(p.y), color, i === 0)
  })
  const hoverEvery = Math.max(1, Math.floor(points.length / 60))
  return (
    <figure className="dt-chart dt-equity">
      <figcaption className="faint">{label} · capital reference {fmt(capital, 0)} · green above / red below</figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={label}>
        <line x1={PAD} x2={W - PAD} y1={capY} y2={capY} stroke="#e8b93e" strokeWidth="1.4" strokeDasharray="3 5" />
        {segments.map((s, i) => <path key={i} d={s.d} fill="none" stroke={s.color} strokeWidth="1.6" />)}
        {points.filter((_, i) => i % hoverEvery === 0).map((p, i) => (
          <circle key={i} cx={sx(p.x)} cy={sy(p.y)} r="5" fill="transparent">
            <title>{p.title}</title>
          </circle>
        ))}
      </svg>
    </figure>
  )
}

function SortTh({ col, sort, setSort }: { col: SortColumn<any>; sort: SortState | null; setSort: (fn: (s: SortState | null) => SortState) => void }) {
  return (
    <th><button type="button" className="btn btn-ghost btn-xs" onClick={() => setSort((cur) => nextSortState(cur, col))}>
      {col.label}{sort?.key === col.key ? (sort.dir === 'asc' ? ' ↑' : ' ↓') : ''}</button></th>
  )
}

interface Offsets { slPlus: string; slMinus: string; tpPlus: string; tpMinus: string }
const EMPTY_OFF: Offsets = { slPlus: '', slMinus: '', tpPlus: '', tpMinus: '' }

export function DeepTesting() {
  const { instrument } = useInstrument()

  /* ============ 1. HARD CONSTRAINTS (defaults 200k / 1% / 1) ============ */
  const saved = (() => { try { return JSON.parse(localStorage.getItem('hero1.dt.constraints') ?? 'null') } catch { return null } })()
  const [capital, setCapital] = useState<number>(saved?.capital ?? 200000)
  const [currency, setCurrency] = useState<string>(saved?.currency ?? 'USD')
  const [riskMode, setRiskMode] = useState<'percent' | 'fixed'>(saved?.riskMode ?? 'percent')
  const [riskValue, setRiskValue] = useState<number>(saved?.riskValue ?? 1)
  const [riskBasis, setRiskBasis] = useState<'current_equity' | 'initial_capital'>(saved?.riskBasis ?? 'current_equity')
  const [maxActive, setMaxActive] = useState<number>(saved?.maxActive ?? 1)
  const [spreadMode, setSpreadMode] = useState<'historical_points' | 'no_spread'>(saved?.spreadMode ?? 'historical_points')
  const [missingSpreadPolicy, setMissingSpreadPolicy] = useState<'block' | 'configured_fallback'>(saved?.missingSpreadPolicy ?? 'block')
  const [spreadFallbackPrice, setSpreadFallbackPrice] = useState<number>(saved?.spreadFallbackPrice ?? 0)
  const [globalOff, setGlobalOff] = useState<Offsets>(saved?.globalOff ?? EMPTY_OFF)
  // APPLIED configuration: the ONLY values ever sent to the backend.
  // Inputs above are pending until the user clicks APPLY (Part 4).
  const [applied, setApplied] = useState(() => ({
    capital: saved?.capital ?? 200000, currency: saved?.currency ?? 'USD',
    riskMode: (saved?.riskMode ?? 'percent') as 'percent' | 'fixed',
    riskValue: saved?.riskValue ?? 1,
    riskBasis: (saved?.riskBasis ?? 'current_equity') as 'current_equity' | 'initial_capital',
    maxActive: saved?.maxActive ?? 1, globalOff: saved?.globalOff ?? EMPTY_OFF,
    spreadMode: (saved?.spreadMode ?? 'historical_points') as 'historical_points' | 'no_spread',
    missingSpreadPolicy: (saved?.missingSpreadPolicy ?? 'block') as 'block' | 'configured_fallback',
    spreadFallbackPrice: saved?.spreadFallbackPrice ?? 0,
  }))
  const pendingDirty = JSON.stringify({ capital, currency, riskMode, riskValue, riskBasis, maxActive, globalOff, spreadMode, missingSpreadPolicy, spreadFallbackPrice })
    !== JSON.stringify(applied)
  const applyConstraints = () => {
    if (!(capital > 0)) { setNotice('Initial capital must be a positive amount.'); return }
    if (!(riskValue > 0)) { setNotice('Risk per trade must be positive.'); return }
    if (!(maxActive >= 1)) { setNotice('Maximum active trades must be at least 1.'); return }
    const offVals = [globalOff.slPlus, globalOff.slMinus, globalOff.tpPlus, globalOff.tpMinus]
    if (offVals.some((v) => v !== '' && !Number.isFinite(Number(v)))) {
      setNotice('TP/SL offsets must be numeric values in pips.'); return
    }
    if (!(spreadFallbackPrice >= 0)) { setNotice('Fallback spread must be a non-negative price amount.'); return }
    const next = { capital, currency, riskMode, riskValue, riskBasis, maxActive, globalOff,
      spreadMode, missingSpreadPolicy, spreadFallbackPrice }
    setApplied(next)
    localStorage.setItem('hero1.dt.constraints', JSON.stringify(next))
    setNotice('Hard Constraints APPLIED — they take effect on the next DeepTesting execution. Existing results are not modified; rerun a node to produce a new result with these values.')
  }

  /* ============ 2. RANGE BUTTONS ============ */
  // Range selection persists per user; NEW configurations default to
  // TILL DATA (all available eligible data). Existing saved selections
  // are never overwritten.
  const [duration, setDuration] = useState<number>(() => {
    const v = Number(localStorage.getItem('hero1.dt.rangeDays'))
    return localStorage.getItem('hero1.dt.rangeDays') !== null && Number.isFinite(v) ? v : -1
  })
  useEffect(() => { localStorage.setItem('hero1.dt.rangeDays', String(duration)) }, [duration])

  /* ============ 3. MAIN NODE TABLE ============ */
  const [nodes, setNodes] = useState<any[]>([])
  const [runs, setRuns] = useState<any[]>([])
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [nodeOffsets, setNodeOffsets] = useState<Record<number, Offsets>>(() => {
    try { return JSON.parse(localStorage.getItem('hero1.dt.nodeOffsets2') ?? '{}') } catch { return {} }
  })
  useEffect(() => { localStorage.setItem('hero1.dt.nodeOffsets2', JSON.stringify(nodeOffsets)) }, [nodeOffsets])
  // Per-node APPLIED offsets (pending row inputs apply only on confirmation)
  const [appliedNodeOffsets, setAppliedNodeOffsets] = useState<Record<number, Offsets>>(() => {
    try {
      return JSON.parse(localStorage.getItem('hero1.dt.nodeOffsetsApplied') ?? 'null')
        ?? JSON.parse(localStorage.getItem('hero1.dt.nodeOffsets2') ?? '{}')
    } catch { return {} }
  })
  const applyNodeOffsets = (id: number) => {
    const next = { ...appliedNodeOffsets, [id]: nodeOffsets[id] ?? EMPTY_OFF }
    setAppliedNodeOffsets(next)
    localStorage.setItem('hero1.dt.nodeOffsetsApplied', JSON.stringify(next))
    setNotice(`Node #${id} offsets APPLIED — effective on its next DeepTesting run; other nodes are unaffected.`)
  }
  const nodePending = (id: number) =>
    JSON.stringify(nodeOffsets[id] ?? EMPTY_OFF) !== JSON.stringify(appliedNodeOffsets[id] ?? EMPTY_OFF)
  const [fSearch, setFSearch] = useState('')
  const [fStarred, setFStarred] = useState('all')
  const [fOrigin, setFOrigin] = useState('')
  const [fTf, setFTf] = useState('')
  const [fDir, setFDir] = useState('')
  const [fTesting, setFTesting] = useState('all')
  const [page, setPage] = useState(0)
  const PAGE_SIZE = 25
const scenarioFilterSummary = (f: any) => {
  const filters = f?.filters ?? f ?? {}
  const days = filters.day_selections ?? {}
  const sessions = filters.session_selections ?? {}
  const excludedDays = Object.entries(days).filter(([, v]) => v === false).map(([k]) => k)
  const excludedSessions = Object.entries(sessions).filter(([, v]) => v === false).map(([k]) => k.replace('|', ' / '))
  const trade = filters.trade_filters ?? {}
  const extra = Object.entries(trade).filter(([, v]) => v !== '' && v != null).map(([k, v]) => `${k}=${v}`)
  return [...(excludedDays.length ? [`Exclude ${excludedDays.join(', ')}`] : []),
    ...(excludedSessions.length ? [`Exclude ${excludedSessions.join(', ')}`] : []), ...extra].join(' · ') || 'No exclusions'
}
  const [nodeSort, setNodeSort] = useState<SortState | null>(() => loadSortPref('deeptesting-nodes', ['id', 'name']))
  useEffect(() => { saveSortPref('deeptesting-nodes', nodeSort) }, [nodeSort])
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  /* ============ 4. INSPECTION ============ */
  const [inspectId, setInspectId] = useState<number | null>(null)
  const [activeRun, setActiveRun] = useState<any | null>(null)
  const [trades, setTrades] = useState<any[]>([])
  const [daySel, setDaySel] = useState<Record<string, boolean>>(
    Object.fromEntries(COL_DAYS.map((d) => [d, true])))
  const [cellSel, setCellSel] = useState<Record<string, boolean>>(
    Object.fromEntries(COL_DAYS.flatMap((d) => SESSION_ROWS.slice(1).map((s) => [`${d}|${s}`, true]))))
  const [scenarios, setScenarios] = useState<any[]>([])
  const [expandedScenarioParents, setExpandedScenarioParents] = useState<Set<number>>(new Set())
  const [modal, setModal] = useState(false)
  const [scName, setScName] = useState('')
  const [scDesc, setScDesc] = useState('')

  useEffect(() => {
    api.nodes.list(instrument, { limit: 500 }).then((r) => setNodes(r.items ?? [])).catch(() => setNodes([]))
    refreshRuns()
    api.deeptesting.listScenarios(instrument).then((r) => setScenarios(r.scenarios ?? [])).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [instrument])

  const refreshRuns = () => api.deeptesting.listRuns(instrument).then((r) => setRuns(r.runs ?? [])).catch(() => {})
  const latestByNode = useMemo(() => {
    const map = new Map<number, any>()
    for (const r of runs) { const cur = map.get(r.node_id); if (!cur || r.id > cur.id) map.set(r.node_id, r) }
    return map
  }, [runs])

  const runDiffersFromApplied = (r: any): boolean => {
    const c = r?.config
    if (!c) return false
    const num = (v: any) => (v === null || v === undefined || v === '' ? 0 : Number(v))
    const off = appliedNodeOffsets[r.node_id] ?? EMPTY_OFF
    const no = (c.node_offsets ?? {})[String(r.node_id)] ?? {}
    return num(c.initial_capital) !== applied.capital
      || num(c.risk_value) !== applied.riskValue
      || num(c.max_active_trades) !== applied.maxActive
      || num(c.global_sl_plus_pips) !== num(applied.globalOff.slPlus)
      || num(c.global_sl_minus_pips) !== num(applied.globalOff.slMinus)
      || num(c.global_tp_plus_pips) !== num(applied.globalOff.tpPlus)
      || num(c.global_tp_minus_pips) !== num(applied.globalOff.tpMinus)
      || num(no.sl_plus_pips) !== num(off.slPlus) || num(no.sl_minus_pips) !== num(off.slMinus)
      || num(no.tp_plus_pips) !== num(off.tpPlus) || num(no.tp_minus_pips) !== num(off.tpMinus)
  }

  const testingStatus = (r: any): string => {
    if (!r) return 'UNTESTED'
    if (r.status === 'FAILED' || r.status === 'BLOCKED') return classifyDeepTestRun(r.status, r.error)
    if (r.status === 'RUNNING') return 'RUNNING'
    if (r.completion_status === 'COMPLETED_WITH_WARNINGS') return 'COMPLETED WITH WARNINGS'
    return r.coverage_status === 'PARTIAL_TESTED' ? 'PARTIAL TESTED' : 'TESTED'
  }

  const rows = useMemo(() => nodes.map((n) => {
    const run = latestByNode.get(n.id)
    const m = run?.status === 'DONE' ? run.metrics : null
    let direction = '—'
    try { direction = JSON.parse(n.spec_json ?? '{}')?.identity?.direction ?? '—' } catch { /* keep — */ }
    return {
      node: n, run, direction, status: testingStatus(run),
      total_trades: m?.total_trades, win_rate: m?.win_rate, net_profit: m?.net_profit,
      profit_factor: m?.profit_factor, expectancy: m?.expectancy,
      max_drawdown_amount: m?.max_drawdown_amount, max_drawdown_pct: m?.max_drawdown_pct,
      recovery_factor: m?.recovery_factor, max_consecutive_losses: m?.max_consecutive_losses,
      max_consecutive_loss_amount: m?.max_consecutive_loss_amount,
      max_consecutive_loss_pct: m?.max_consecutive_loss_pct,
      winning_trades: m?.winning_trades, losing_trades: m?.losing_trades,
      last_tested: run?.finished_at,
      scenarioCount: scenarios.filter((sc) => (sc.node_id ?? sc.config?.node_id ?? sc.config?.node_ids?.[0]) === n.id).length,
    }
  }), [nodes, latestByNode, runs, scenarios])

  const filteredRows = useMemo(() => rows.filter((r) => {
    const n = r.node
    if (fSearch && !`${n.name} ${n.id}`.toLowerCase().includes(fSearch.toLowerCase())) return false
    if (fStarred === 'starred' && !n.starred) return false
    if (fStarred === 'unstarred' && n.starred) return false
    if (fOrigin && n.origin !== fOrigin) return false
    if (fTf && n.timeframe !== fTf) return false
    if (fDir && r.direction !== fDir) return false
    if (fTesting === 'tested' && !['TESTED', 'PARTIAL TESTED'].includes(r.status)) return false
    if (fTesting === 'untested' && r.status !== 'UNTESTED') return false
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
    { key: 'max_consecutive_losses', label: 'Max consec. losses', type: 'number', get: (r) => r.max_consecutive_losses },
    { key: 'max_consecutive_loss_amount', label: 'Max consec. loss', type: 'number', get: (r) => r.max_consecutive_loss_amount },
    { key: 'max_consecutive_loss_pct', label: 'Max consec. loss %', type: 'number', get: (r) => r.max_consecutive_loss_pct },
    { key: 'last_tested', label: 'Last tested', type: 'date', get: (r) => r.last_tested },
  ]
  const sortedRows = useMemo(() => applySort(filteredRows, nodeSort, nodeColumns),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [filteredRows, nodeSort])
  const pageRows = sortedRows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)

  const pair = (o: Offsets, kind: 'sl' | 'tp') => {
    const plus = kind === 'sl' ? o.slPlus : o.tpPlus
    const minus = kind === 'sl' ? o.slMinus : o.tpMinus
    return { plus, minus, set: plus !== '' || minus !== '' }
  }
  const offsetSource = (nodeId: number, kind: 'sl' | 'tp'): string => {
    if (pair(appliedNodeOffsets[nodeId] ?? EMPTY_OFF, kind).set) return 'Individual node'
    if (pair(applied.globalOff, kind).set) return 'Global DeepTesting'
    return 'Strategy'
  }

  const buildConfig = (extra: Record<string, unknown> = {}) => {
    const end = Math.floor(Date.now() / 1000)
    return {
      mode: 'initial',
      range: duration < 0 ? {} : { start: end - duration * 86400, end },
      initial_capital: applied.capital, account_currency: applied.currency,
      risk_mode: applied.riskMode, risk_value: applied.riskValue, risk_basis: applied.riskBasis,
      max_active_trades: applied.maxActive,
      spread_mode: applied.spreadMode,
      missing_spread_policy: applied.missingSpreadPolicy,
      spread_price: applied.spreadFallbackPrice,
      slippage_price: 0,
      global_sl_plus_pips: applied.globalOff.slPlus === '' ? null : Number(applied.globalOff.slPlus),
      global_sl_minus_pips: applied.globalOff.slMinus === '' ? null : Number(applied.globalOff.slMinus),
      global_tp_plus_pips: applied.globalOff.tpPlus === '' ? null : Number(applied.globalOff.tpPlus),
      global_tp_minus_pips: applied.globalOff.tpMinus === '' ? null : Number(applied.globalOff.tpMinus),
      node_offsets: Object.fromEntries(Object.entries(appliedNodeOffsets).map(([id, o]) => [id, {
        sl_plus_pips: o.slPlus === '' ? null : Number(o.slPlus),
        sl_minus_pips: o.slMinus === '' ? null : Number(o.slMinus),
        tp_plus_pips: o.tpPlus === '' ? null : Number(o.tpPlus),
        tp_minus_pips: o.tpMinus === '' ? null : Number(o.tpMinus),
      }])),
      ...extra,
    }
  }

  const { tasks: taskList, latest: latestTasks, refresh: refreshTasks } = useTasks()
  const dtTask = useMemo(() => {
    const mine = taskList.filter((t) => t.category === 'DEEPTESTING' && t.instrument === instrument)
    if (mine.length) return mine[0]
    const l = latestTasks.DEEPTESTING
    return l && l.instrument === instrument ? l : null
  }, [taskList, latestTasks, instrument])
  const [progressOpen, setProgressOpen] = useState(false)
  const prevDtStatus = useRef<string | null>(null)
  useEffect(() => {
    const status = dtTask?.status ?? null
    if (status && prevDtStatus.current && !TERMINAL.includes(prevDtStatus.current)
        && TERMINAL.includes(status)) {
      // task just finished: auto-collapse details, refresh results (Sec 3.4)
      setProgressOpen(false)
      void refreshRuns()
      if (inspectId) void openNodeRun(inspectId)
    }
    prevDtStatus.current = status
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dtTask?.status])

  const runNodes = async (ids: number[], extra: Record<string, unknown> = {}) => {
    if (!ids.length) { setNotice('Select at least one node first.'); return }
    setBusy(true); setNotice(null)
    try {
      const res = await api.deeptesting.createRuns(instrument, ids, buildConfig(extra), true)
      if (res.task_id) {
        setNotice(`DeepTesting started for ${ids.length} node(s) — progress is shown below and in the global task bar.`)
        refreshTasks()
        return
      }
      const failed = res.runs.filter((r) => r.status === 'FAILED')
      setNotice(failed.length
        ? failed.map((f) => `node ${f.node_id}: ${f.error}`).join(' · ')
        : `Completed ${res.runs.length} run(s).`)
      await refreshRuns()
      if (inspectId && ids.includes(inspectId)) await openNodeRun(inspectId)
    } catch (e) { setNotice(String(e)) } finally { setBusy(false) }
  }

  const openNodeRun = async (nodeId: number, runId?: number) => {
    setInspectId(nodeId)
    const target = runId ?? latestByNode.get(nodeId)?.id
    if (!target) { setActiveRun(null); setTrades([]); return }
    const run = await api.deeptesting.getRun(instrument, target)
    setActiveRun(run)
    const t = await api.deeptesting.trades(instrument, target)
    setTrades(t.trades ?? [])
  }

  const toggleStar = async (n: any) => {
    const next = !n.starred
    setNodes(nodes.map((x) => (x.id === n.id ? { ...x, starred: next } : x)))
    try { await api.nodes.setStarred(instrument, n.id, next) } catch { /* optimistic state stays visible */ }
  }

  /* ---- weekday/session engine over REAL trades ---- */
  const cellKey = (t: any) => `${weekdayOf(t)}|${sessionCell(t.entry_ts)}`
  const isFilteredActive = useMemo(() =>
    Object.values(daySel).some((v) => !v) || Object.values(cellSel).some((v) => !v),
    [daySel, cellSel])
  // Trade-table filters (Section 8): AND logic, same session classification
  // as the weekday/session grid (weekdayOf/sessionCell = backend inspect_cell).
  const [tf, setTf] = useState<{ side: string; outcome: string; session: string;
    weekday: string; exitReason: string; from: string; to: string;
    minNet: string; maxNet: string; minHoldH: string; maxHoldH: string }>(
    { side: '', outcome: '', session: '', weekday: '', exitReason: '', from: '', to: '',
      minNet: '', maxNet: '', minHoldH: '', maxHoldH: '' })
  const tfActive = Object.values(tf).some((v) => v !== '')
  const anyFilter = isFilteredActive || tfActive
  const tradeMatches = (t: any): boolean => {
    if (tf.side && t.side !== tf.side) return false
    if (tf.outcome) {
      const r = t.net_pnl > 1e-9 ? 'WIN' : t.net_pnl < -1e-9 ? 'LOSS' : 'BREAKEVEN'
      if (r !== tf.outcome) return false
    }
    if (tf.session && sessionCell(t.entry_ts) !== tf.session) return false
    if (tf.weekday && weekdayOf(t) !== tf.weekday) return false
    if (tf.exitReason && t.exit_reason !== tf.exitReason) return false
    if (tf.from && t.entry_ts < Date.parse(tf.from) / 1000) return false
    if (tf.to && t.entry_ts > Date.parse(tf.to) / 1000 + 86399) return false
    if (tf.minNet !== '' && !(t.net_pnl >= Number(tf.minNet))) return false
    if (tf.maxNet !== '' && !(t.net_pnl <= Number(tf.maxNet))) return false
    if (tf.minHoldH !== '' && !((t.holding_seconds ?? 0) >= Number(tf.minHoldH) * 3600)) return false
    if (tf.maxHoldH !== '' && !((t.holding_seconds ?? 0) <= Number(tf.maxHoldH) * 3600)) return false
    return true
  }
  const serverFilters = useMemo(() => {
    const f: Record<string, unknown> = {}
    if (tf.side) f.side = tf.side
    if (tf.outcome) f.outcome = tf.outcome
    if (tf.session) f.session = tf.session
    if (tf.weekday) f.weekday = tf.weekday
    if (tf.exitReason) f.exit_reason = tf.exitReason
    if (tf.from) f.entry_from = Date.parse(tf.from) / 1000
    if (tf.to) f.entry_to = Date.parse(tf.to) / 1000 + 86399
    if (tf.minNet !== '') f.min_net = Number(tf.minNet)
    if (tf.maxNet !== '') f.max_net = Number(tf.maxNet)
    if (tf.minHoldH !== '') f.min_hold = Number(tf.minHoldH) * 3600
    if (tf.maxHoldH !== '') f.max_hold = Number(tf.maxHoldH) * 3600
    return f
  }, [tf])

  const filteredTrades = useMemo(() => {
    return trades.filter((t) => {
      if (isFilteredActive) {
        const wd = weekdayOf(t)
        if (!daySel[wd]) return false
        if (!cellSel[`${wd}|${sessionCell(t.entry_ts)}`]) return false
      }
      return tradeMatches(t)
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trades, daySel, cellSel, isFilteredActive, tf])

  const runCapital = activeRun?.metrics?.initial_capital ?? applied.capital
  const cellStats = (list: any[]) => {
    const wins = list.filter((t) => t.net_pnl > 1e-9)
    const losses = list.filter((t) => t.net_pnl < -1e-9)
    const gp = wins.reduce((a, t) => a + t.net_pnl, 0)
    const gl = losses.reduce((a, t) => a + t.net_pnl, 0)
    // Independent per-cell drawdown: chronological (exit-time) equity path
    // of THIS subset only, starting at the configured initial capital;
    // percentage vs the running peak equity at the worst point.
    let eq = runCapital, peak = runCapital, ddAmt = 0, ddPct = 0
    for (const t of [...list].sort((a, b) => a.exit_ts - b.exit_ts)) {
      eq += t.net_pnl; peak = Math.max(peak, eq)
      const dd = peak - eq
      if (dd > ddAmt) { ddAmt = dd; ddPct = peak > 0 ? (dd / peak) * 100 : 0 }
    }
    return { trades: list.length, wins: wins.length, losses: losses.length,
             be: list.length - wins.length - losses.length,
             winRate: list.length ? wins.length / list.length : null,
             gp, gl, net: gp + gl, pf: gl < 0 ? gp / Math.abs(gl) : null,
             ddAmt: list.length ? ddAmt : null, ddPct: list.length ? ddPct : null }
  }

  // Independent BUY/SELL statistics for the filtered view (same definitions
  // as the backend directional_stats used for the original result).
  const dirStats = (list: any[], side: string) => {
    const sub = [...list.filter((t) => t.side === side)].sort((a, b) => a.exit_ts - b.exit_ts)
    const base = cellStats(sub)
    let curW = 0, curL = 0, maxW = 0, maxL = 0
    for (const t of sub) {
      if (t.net_pnl > 1e-9) { curW++; curL = 0 } else if (t.net_pnl < -1e-9) { curL++; curW = 0 }
      else { curW = 0; curL = 0 }
      maxW = Math.max(maxW, curW); maxL = Math.max(maxL, curL)
    }
    const wins = sub.filter((t) => t.net_pnl > 1e-9), losses = sub.filter((t) => t.net_pnl < -1e-9)
    return { ...base, maxW, maxL, curW, curL,
             avgWin: wins.length ? wins.reduce((a, t) => a + t.net_pnl, 0) / wins.length : null,
             avgLoss: losses.length ? losses.reduce((a, t) => a + t.net_pnl, 0) / losses.length : null,
             expectancy: sub.length ? base.net / sub.length : null,
             largestWin: wins.length ? Math.max(...wins.map((t) => t.net_pnl)) : null,
             largestLoss: losses.length ? Math.min(...losses.map((t) => t.net_pnl)) : null,
             returnPct: runCapital > 0 ? (base.net / runCapital) * 100 : null,
             avgHold: sub.length ? sub.reduce((a, t) => a + (t.holding_seconds ?? 0), 0) / sub.length : null,
             // reuse the subset equity path in cellStats for max DD; current DD:
             currentDd: (() => { let e = runCapital, pk = runCapital; for (const t of sub) { e += t.net_pnl; pk = Math.max(pk, e) } return pk - e })(),
             currentDdPct: (() => { let e = runCapital, pk = runCapital; for (const t of sub) { e += t.net_pnl; pk = Math.max(pk, e) } return pk > 0 ? ((pk - e) / pk) * 100 : 0 })(),
             avgDd: null }
  }

  // Aggregate statistics across the CURRENTLY SELECTED weekdays/sessions
  // (same fields as every weekday/session cell; each trade counted once).
  const aggStats = (session: string) => {
    const selectedTrades = trades.filter((t) => {
      const wd = weekdayOf(t)
      if (!daySel[wd] || !cellSel[`${wd}|${sessionCell(t.entry_ts)}`]) return false
      if (session === 'TOTAL DAY') return true
      if (session === 'UNASSIGNED') return wd === 'UNASSIGNED' && sessionCell(t.entry_ts) === 'UNASSIGNED'
      // Named session aggregates cover weekdays only; weekend trades have an
      // explicit UNASSIGNED session and never leak into ASIA or another window.
      if (!(WEEKDAYS as readonly string[]).includes(wd)) return false
      return sessionCell(t.entry_ts) === session
    })
    const stats = cellStats(selectedTrades)
    // On the full, unfiltered TOTAL DAY aggregate, use the run's actual
    // bar-close equity curve definition so this matches the main report.
    if (session === 'TOTAL DAY' && selectedTrades.length === trades.length && activeRun?.metrics) {
      return { ...stats, ddAmt: activeRun.metrics.max_drawdown_amount,
        ddPct: activeRun.metrics.max_drawdown_pct }
    }
    return stats
  }

  // DESELECT -VE NET BOXES: deselect ONLY boxes whose own underlying
  // UNROUNDED net P/L is strictly < 0. Positive and exactly-zero boxes are
  // never touched; weekday parents then re-derive from their children.
  const deselectNegative = () => {
    const nc = { ...cellSel }
    for (const d of COL_DAYS) {
      for (const s2 of SESSION_ROWS.slice(1)) {
        const list = trades.filter((t) => cellKey(t) === `${d}|${s2}`)
        if (list.length && cellStats(list).net < 0) nc[`${d}|${s2}`] = false
      }
    }
    const nd = { ...daySel }
    for (const d of COL_DAYS) nd[d] = SESSION_ROWS.slice(1).some((s2) => nc[`${d}|${s2}`])
    setCellSel(nc); setDaySel(nd)
  }
  // Single-cell toggle; the weekday parent re-derives from its children.
  const setCell = (key: string, d: string, v: boolean) => {
    const nc = { ...cellSel, [key]: v }
    setCellSel(nc)
    setDaySel({ ...daySel, [d]: SESSION_ROWS.slice(1).some((s2) => nc[`${d}|${s2}`]) })
  }
  const toggleDay = (d: string, v: boolean) => {
    setDaySel({ ...daySel, [d]: v })
    setCellSel({ ...cellSel, ...Object.fromEntries(SESSION_ROWS.slice(1).map((s2) => [`${d}|${s2}`, v])) })
  }
  const toggleSessionAll = (session: string, v: boolean) => {
    const nc = { ...cellSel, ...Object.fromEntries(COL_DAYS.map((d) => [`${d}|${session}`, v])) }
    setCellSel(nc)
    const nd = { ...daySel }
    for (const d of COL_DAYS) nd[d] = SESSION_ROWS.slice(1).some((s2) => nc[`${d}|${s2}`])
    setDaySel(nd)
  }
  const sessionState = (session: string) => {
    const vals = COL_DAYS.map((d) => !!cellSel[`${d}|${session}`])
    return { all: vals.every(Boolean), some: vals.some(Boolean) }
  }

  const filteredStats = useMemo(() => {
    const g = [...filteredTrades].sort((a, b) => a.exit_ts - b.exit_ts)
    const base = cellStats(g)
    const wins = g.filter((t) => t.net_pnl > 1e-9), losses = g.filter((t) => t.net_pnl < -1e-9)
    let cum = 0, peak = 0, maxDd = 0, curW = 0, curL = 0, maxW = 0, maxL = 0
    let curLossAmt = 0, maxLossAmt = 0
    for (const t of g) {
      cum += t.net_pnl; peak = Math.max(peak, cum); maxDd = Math.max(maxDd, peak - cum)
      if (t.net_pnl > 1e-9) { curW++; curL = 0; curLossAmt = 0 }
      else if (t.net_pnl < -1e-9) { curL++; curW = 0; curLossAmt += -t.net_pnl }
      else { curW = 0; curL = 0; curLossAmt = 0 }
      maxW = Math.max(maxW, curW)
      if (curL > maxL || (curL === maxL && curLossAmt > maxLossAmt)) maxLossAmt = curLossAmt
      maxL = Math.max(maxL, curL)
    }
    const ddMarks: number[] = []
    cum = 0; peak = 0
    for (const t of g) { cum += t.net_pnl; peak = Math.max(peak, cum); if (peak > cum) ddMarks.push(peak - cum) }
    return { ...base,
             avgWin: wins.length ? wins.reduce((a, t) => a + t.net_pnl, 0) / wins.length : null,
             avgLoss: losses.length ? losses.reduce((a, t) => a + t.net_pnl, 0) / losses.length : null,
             expectancy: g.length ? base.net / g.length : null,
             largestWin: wins.length ? Math.max(...wins.map((t) => t.net_pnl)) : null,
             largestLoss: losses.length ? Math.min(...losses.map((t) => t.net_pnl)) : null,
             avgHolding: g.length ? g.reduce((a, t) => a + t.holding_seconds, 0) / g.length : null,
             maxDd, maxDdPct: (runCapital + peak) > 0 ? (maxDd / (runCapital + peak)) * 100 : null,
             avgDd: ddMarks.length ? ddMarks.reduce((a, b) => a + b, 0) / ddMarks.length : 0,
             currentDd: peak - cum, maxW, maxL, curW, curL, maxLossAmt,
             balance: runCapital + base.net }
  }, [filteredTrades, runCapital])

  const m = activeRun?.metrics
  const stats = anyFilter ? {
    total_trades: filteredStats.trades, winning_trades: filteredStats.wins,
    losing_trades: filteredStats.losses, breakeven_trades: filteredStats.be,
    win_rate: filteredStats.winRate, gross_profit: filteredStats.gp, gross_loss: filteredStats.gl,
    net_profit: filteredStats.net, profit_factor: filteredStats.pf,
    avg_win: filteredStats.avgWin, avg_loss: filteredStats.avgLoss,
    expectancy: filteredStats.expectancy, largest_win: filteredStats.largestWin,
    largest_loss: filteredStats.largestLoss, avg_holding_seconds: filteredStats.avgHolding,
    initial_capital: runCapital, final_balance: filteredStats.balance,
    final_equity: filteredStats.balance, return_pct: runCapital > 0 ? (filteredStats.net / runCapital) * 100 : null,
    max_drawdown_amount: filteredStats.maxDd, max_drawdown_pct: filteredStats.maxDdPct,
    avg_drawdown_amount: filteredStats.avgDd, current_drawdown_amount: filteredStats.currentDd,
    max_consecutive_wins: filteredStats.maxW, max_consecutive_losses: filteredStats.maxL,
    max_consecutive_loss_amount: filteredStats.maxLossAmt,
    max_consecutive_loss_pct: (m?.initial_capital ?? runCapital) > 0
      ? (filteredStats.maxLossAmt / (m?.initial_capital ?? runCapital)) * 100 : null,
    current_streak: filteredStats.curW ? `W × ${filteredStats.curW}` : filteredStats.curL ? `L × ${filteredStats.curL}` : '—',
    max_simultaneous: 'N/A (filter view)', max_planned_open_risk: 'N/A (filter view)',
  } : m ? {
    total_trades: m.total_trades, winning_trades: m.winning_trades, losing_trades: m.losing_trades,
    breakeven_trades: m.breakeven_trades, win_rate: m.win_rate, gross_profit: m.gross_profit,
    gross_loss: m.gross_loss, net_profit: m.net_profit, profit_factor: m.profit_factor,
    avg_win: m.avg_win, avg_loss: m.avg_loss, expectancy: m.expectancy,
    largest_win: m.largest_win, largest_loss: m.largest_loss,
    avg_holding_seconds: m.avg_holding_seconds, initial_capital: m.initial_capital,
    final_balance: m.final_balance, final_equity: m.final_equity,
    return_pct: m.return_on_initial_capital_pct,
    max_drawdown_amount: m.max_drawdown_amount, max_drawdown_pct: m.max_drawdown_pct,
    avg_drawdown_amount: m.avg_drawdown_amount, current_drawdown_amount: m.current_drawdown_amount,
    max_consecutive_wins: m.max_consecutive_wins, max_consecutive_losses: m.max_consecutive_losses,
    max_consecutive_loss_amount: m.max_consecutive_loss_amount,
    max_consecutive_loss_pct: m.max_consecutive_loss_pct,
    current_streak: m.current_streak ? `${m.current_streak.kind} × ${m.current_streak.count}` : '—',
    max_simultaneous: m.max_simultaneous_positions, max_planned_open_risk: m.max_planned_open_risk,
  } : null

  const dirLong = anyFilter ? dirStats(filteredTrades, 'LONG')
    : (m?.directional?.LONG ?? null)
  const dirShort = anyFilter ? dirStats(filteredTrades, 'SHORT')
    : (m?.directional?.SHORT ?? null)
  const dirShape = (d: any) => d == null ? null : ({
    total: d.total_trades ?? d.trades, wins: d.winning_trades ?? d.wins,
    losses: d.losing_trades ?? d.losses, be: d.breakeven_trades ?? d.be,
    winRate: d.win_rate ?? d.winRate, gp: d.gross_profit ?? d.gp, gl: d.gross_loss ?? d.gl,
    net: d.net_profit ?? d.net, pf: d.profit_factor ?? d.pf,
    avgWin: d.avg_win ?? d.avgWin, avgLoss: d.avg_loss ?? d.avgLoss,
    expectancy: d.expectancy, largestWin: d.largest_win ?? d.largestWin,
    largestLoss: d.largest_loss ?? d.largestLoss,
    returnPct: d.return_on_initial_capital_pct ?? d.returnPct,
    ddMax: d.drawdown?.max_amount ?? d.ddAmt, ddMaxPct: d.drawdown?.max_pct ?? d.ddPct,
    ddCur: d.drawdown?.current_amount ?? d.currentDd, ddCurPct: d.drawdown?.current_pct ?? d.currentDdPct,
    ddAvg: d.drawdown?.avg_amount ?? d.avgDd,
    avgHold: d.avg_holding_seconds ?? d.avgHold,
    maxW: d.max_consecutive_wins ?? d.maxW, maxL: d.max_consecutive_losses ?? d.maxL,
    curStreak: d.current_streak ? (typeof d.current_streak === 'string' ? d.current_streak
      : `${d.current_streak.kind} x ${d.current_streak.count}`) : (d.curW ? `W x ${d.curW}` : d.curL ? `L x ${d.curL}` : '—'),
  })
  const equityPoints = useMemo(() => {
    if (anyFilter) {
      const sorted = [...filteredTrades].sort((a, b) => a.exit_ts - b.exit_ts)
      let cum = runCapital
      const pts = [{ x: sorted[0]?.entry_ts ?? 0, y: runCapital, title: `start · equity ${fmt(runCapital, 0)}` }]
      for (const t of sorted) {
        cum += t.net_pnl
        pts.push({ x: t.exit_ts, y: cum, title: `${tsFmt(t.exit_ts)} · equity ${fmt(cum)} · trade ${fmt(t.net_pnl)}` })
      }
      return pts
    }
    return (activeRun?.equity_curve ?? []).map((e: any) => ({
      x: e.ts, y: e.equity,
      title: `${tsFmt(e.ts)} · equity ${fmt(e.equity)} · balance ${fmt(e.balance)} · open ${e.open_positions}`,
    }))
  }, [activeRun, filteredTrades, anyFilter, runCapital])

  const dayCheckboxRefs = useRef<Record<string, HTMLInputElement | null>>({})
  const sessionCheckboxRefs = useRef<Record<string, HTMLInputElement | null>>({})
  useEffect(() => {
    for (const s2 of SESSION_ROWS.slice(1)) {
      const el = sessionCheckboxRefs.current[s2]
      if (el) { const st = sessionState(s2); el.indeterminate = st.some && !st.all }
    }
    for (const d of COL_DAYS) {
      const el = dayCheckboxRefs.current[d]
      if (!el) continue
      const kids = SESSION_ROWS.slice(1).map((s) => cellSel[`${d}|${s}`])
      el.indeterminate = kids.some(Boolean) && !kids.every(Boolean)
    }
  }, [cellSel])

  const inspectNode = nodes.find((n) => n.id === inspectId)
  const nodeScenarios = scenarios.filter((sc) => (sc.node_id ?? sc.config?.node_id ?? sc.config?.node_ids?.[0]) === inspectId)

  const saveScenario = async () => {
    if (!scName || inspectId === null) return
    const config = buildConfig({
      node_ids: [inspectId],
      description: scDesc,
      day_selections: daySel, session_selections: cellSel,
      trade_filters: tf, filters: { day_selections: daySel, session_selections: cellSel, trade_filters: tf },
      filtered_trade_ids: filteredTrades.map((t: any) => t.id),
      source_run_id: activeRun?.id ?? null,
      node_version: inspectNode?.version ?? null,
      fingerprint: activeRun?.fingerprint ?? null,
      requested_range: activeRun?.metrics?.requested_range ?? null,
      actual_range: activeRun?.metrics?.actual_range ?? null,
      coverage_status: activeRun?.metrics?.coverage_status ?? null,
      data_quality: activeRun?.data_quality ?? null,
      assumptions: activeRun?.assumptions ?? null,
      filtered_metrics: { ...filteredStats, net_profit: filteredStats.net,
                          total_trades: filteredStats.trades, win_rate: filteredStats.winRate,
                          profit_factor: filteredStats.pf },
      filtered_trades: filteredTrades,
    })
    const savedScenario = await api.deeptesting.createScenario(instrument, { name: scName, folder: `node-${inspectId}`, config })
    setModal(false); setScName(''); setScDesc('')
    const refreshed = await api.deeptesting.listScenarios(instrument)
    setScenarios(refreshed.scenarios ?? [])
    setExpandedScenarioParents(prev => new Set(prev).add(inspectId))
    setNotice(`Scenario ${scName} saved as persistent child #${savedScenario.id} with ${filteredTrades.length} source-run trades and its filter-specific statistics.`)
  }

  const openScenario = async (sc: any) => {
    if (sc.config?.day_selections) setDaySel({
      ...Object.fromEntries(COL_DAYS.map((d) => [d, true])), ...sc.config.day_selections })
    if (sc.config?.session_selections) setCellSel({
      ...Object.fromEntries(COL_DAYS.flatMap((d) => SESSION_ROWS.slice(1).map((s2) => [`${d}|${s2}`, true]))),
      ...sc.config.session_selections })
    if (sc.config?.source_run_id) await openNodeRun(inspectId!, sc.config.source_run_id)
    setNotice(`Scenario "${sc.name}" loaded — checkbox states restored exactly as saved (scenario #${sc.id}, deeptesting-scenario/v1).`)
  }

  const [showTrades, setShowTrades] = useState(false)
  const [tradePage, setTradePage] = useState(0)
  const [tradePageSize, setTradePageSize] = useState<number | 'all'>(25)
  const [tradeSort, setTradeSort] = useState<{ key: string; dir: string }>({ key: 'entry_ts', dir: 'asc' })
  const [tradeData, setTradeData] = useState<any | null>(null)
  const selectedCells = useMemo(() => COL_DAYS.flatMap((d) =>
    SESSION_ROWS.slice(1).filter((s2) => daySel[d] && cellSel[`${d}|${s2}`]).map((s2) => `${d}|${s2}`)),
    [daySel, cellSel])
  useEffect(() => { setTradePage(0) }, [cellSel, daySel, activeRun?.id, tradeSort, tradePageSize, tf])
  useEffect(() => {
    if (!showTrades || !activeRun) return
    api.deeptesting.tradesPage(instrument, activeRun.id,
      { page: tradePage, pageSize: tradePageSize, sort: tradeSort.key, dir: tradeSort.dir, cells: selectedCells, filters: serverFilters })
      .then(setTradeData).catch(() => setTradeData(null))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showTrades, activeRun?.id, tradePage, tradePageSize, tradeSort, selectedCells, serverFilters])

  // EXPORT TABLE (Section 6): TXT report of the CURRENT filtered subset.
  const exportTableTxt = () => {
    if (!activeRun || !stats) return
    const nodeName = nodes.find((n) => n.id === inspectId)?.name ?? ''
    const cfg = activeRun.config ?? {}
    const L = dirShape(dirLong); const S = dirShape(dirShort)
    const dirLine = (label: string, d: any) => d
      ? `${label}: ${d.total} trades · ${d.wins}W/${d.losses}L/${d.be}BE · GP ${fmt(d.gp)} · GL ${fmt(d.gl)} · Net ${fmt(d.net)} · PF ${fmt(d.pf, 3)} · DD ${fmt(d.ddMax)} (${fmt(d.ddMaxPct)}%)`
      : `${label}: N/A`
    const rows = [...filteredTrades].sort((a, b) => a.entry_ts - b.entry_ts)
    const head = ['ID', 'Entry', 'Exit', 'Day', 'Session', 'Dir', 'EntryPx', 'ExitPx', 'SL', 'TP', 'Size', 'Gross', 'Net', 'Outcome', 'HeldH', 'ExitReason']
    const body = rows.map((t) => [String(t.id ?? ''), tsFmt(t.entry_ts), tsFmt(t.exit_ts), weekdayOf(t),
      sessionCell(t.entry_ts), t.side, fmt(t.entry_price, 2), fmt(t.exit_price, 2), fmt(t.initial_stop, 2),
      fmt(t.take_profit, 2), fmt(t.volume, 2), fmt(t.gross_pnl), fmt(t.net_pnl), t.result ?? '',
      fmt((t.holding_seconds ?? 0) / 3600, 1), t.exit_reason ?? ''])
    const widths = head.map((h, i) => Math.max(h.length, ...body.map((r) => r[i].length)))
    const line = (r: string[]) => r.map((v, i) => v.padEnd(widths[i])).join('  ')
    const activeFilters = [
      ...COL_DAYS.filter((d) => !(daySel[d] && SESSION_ROWS.slice(1).every((x) => cellSel[`${d}|${x}`]))).map((d) => `weekday/session grid excludes parts of ${d}`),
      ...Object.entries(serverFilters).map(([k, v]) => `${k}=${v}`),
    ]
    const txt = [
      'HERO1 DEEPTESTING - TRADE TABLE EXPORT', '='.repeat(72),
      `Node: #${inspectId} ${nodeName}`, `Run: #${activeRun.id}`,
      `Instrument/Timeframe: ${instrument} ${activeRun.timeframe}`,
      `Requested range: ${cfg.requested_range ? `${tsFmt(cfg.requested_range.start)} .. ${tsFmt(cfg.requested_range.end)}` : 'N/A'}`,
      `Actual range: ${cfg.actual_range ? `${tsFmt(cfg.actual_range.start)} .. ${tsFmt(cfg.actual_range.end)}` : 'N/A'}`,
      `Active filters: ${activeFilters.length ? activeFilters.join('; ') : 'none (full run)'}`,
      `Trade count (filtered): ${rows.length} of ${trades.length}`,
      '', 'SUMMARY (filtered subset)', '-'.repeat(72),
      `Net P/L: ${fmt(stats.net_profit)} · Gross profit: ${fmt(stats.gross_profit)} · Gross loss: ${fmt(stats.gross_loss)}`,
      `Profit factor: ${fmt(stats.profit_factor, 3)} · Win rate: ${stats.win_rate != null ? `${fmt(stats.win_rate * 100, 1)}%` : 'N/A'}`,
      `Max drawdown: ${fmt(stats.max_drawdown_amount)} (${fmt(stats.max_drawdown_pct)}%) · Current drawdown: ${fmt(stats.current_drawdown_amount)}`,
      dirLine('LONG ', L), dirLine('SHORT', S),
      '', 'TRADES', '-'.repeat(72), line(head), ...body.map(line), '',
    ].join('\n')
    const url = URL.createObjectURL(new Blob([txt], { type: 'text/plain' }))
    const a = document.createElement('a')
    a.href = url; a.download = `hero1_trades_run${activeRun.id}.txt`; a.click()
    setTimeout(() => URL.revokeObjectURL(url), 5000)
  }

  const moneyRisk = riskMode === 'percent' ? (capital * riskValue) / 100 : riskValue
  const offInput = (label: string, value: string, onChange: (v: string) => void, aria: string) => (
    <label className="mini-field">{label}
      <span className="dt-off"><input className="input input-xs num" style={{ width: 72 }} type="number" step="any"
        value={value} aria-label={aria} onChange={(e) => onChange(e.target.value)} /> pips</span></label>
  )

  return (
    <div className="deeptesting-page">
      <h2>DeepTesting</h2>
      {notice && <div className="json-invalid-banner" role="status">{notice}</div>}

      {/* ================= 1. HARD CONSTRAINTS ================= */}
      <section className="builder-section" aria-labelledby="dt-constraints">
        <h4 id="dt-constraints"><span className="section-num">1</span> Hard Constraints</h4>
        <div className="exits-grid">
          <fieldset className="exit-block">
            <legend>Initial capital</legend>
            <div className="segmented mini" role="group" aria-label="Capital presets">
              {[1000, 5000, 10000, 25000, 200000].map((v) => (
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
            <p className="faint">Money at risk: <strong>{currency} {fmt(moneyRisk)}</strong>. Defaults 200,000 / 1% / 1 apply to new configurations; your saved values are kept.</p>
          </fieldset>

          <fieldset className="exit-block">
            <legend>MAX ACTIVE TRADES</legend>
            <label className="mini-field">Simultaneous positions per node/scenario
              <input className="input input-xs num" type="number" min={1} max={50} value={maxActive} aria-label="Max active trades"
                     onChange={(e) => setMaxActive(Math.max(1, Math.floor(Number(e.target.value) || 1)))} /></label>
            <p className="faint">Default 1 — enforced by the backend engine per node/scenario.</p>
          </fieldset>

          <fieldset className="exit-block">
            <legend>Historical spread and execution assumptions</legend>
            <label className="mini-field">Spread mode
              <select className="input input-xs" aria-label="Spread mode" value={spreadMode}
                onChange={(e) => setSpreadMode(e.target.value as 'historical_points' | 'no_spread')}>
                <option value="historical_points">Historical Spread</option>
                <option value="no_spread">NO SPREAD</option>
              </select></label>
            <label className="mini-field">Missing/invalid historical spread policy
              <select className="input input-xs" aria-label="Missing spread policy" value={missingSpreadPolicy} disabled={spreadMode === 'no_spread'}
                onChange={(e) => setMissingSpreadPolicy(e.target.value as 'block' | 'configured_fallback')}>
                <option value="block">Block run</option><option value="configured_fallback">Configured fallback (warning)</option>
              </select></label>
            {missingSpreadPolicy === 'configured_fallback' && <label className="mini-field">Fallback spread price (explicit assumption)
              <input className="input input-xs num" type="number" min="0" step="0.01" aria-label="Fallback spread price"
                value={spreadFallbackPrice} onChange={(e) => setSpreadFallbackPrice(Math.max(0, Number(e.target.value)))} /></label>}
            <p className="faint micro">{spreadMode === 'no_spread' ? 'NO SPREAD applies zero spread and does not inspect historical spread; this is not realistic broker execution.' : 'Historical candle spread is used per execution when available.'} Zero slippage is explicitly an assumption. Commission remains NOT CONFIGURED unless supplied. Swap and commission settings remain independent.</p>
          </fieldset>

          <fieldset className="exit-block">
            <legend>Global TP / SL offsets <span className="faint">(pips · individual node values override · strategy logic never replaced)</span></legend>
            {offInput('SL + (Away from Entry)', globalOff.slPlus, (v) => setGlobalOff({ ...globalOff, slPlus: v }), 'Global SL plus offset')}
            {offInput('SL − (Closer to Entry)', globalOff.slMinus, (v) => setGlobalOff({ ...globalOff, slMinus: v }), 'Global SL minus offset')}
            {offInput('TP + (Away from Entry)', globalOff.tpPlus, (v) => setGlobalOff({ ...globalOff, tpPlus: v }), 'Global TP plus offset')}
            {offInput('TP − (Closer to Entry)', globalOff.tpMinus, (v) => setGlobalOff({ ...globalOff, tpMinus: v }), 'Global TP minus offset')}
          </fieldset>
        </div>
        <div className="dt-toolbar">
          <button type="button" className="btn btn-primary" onClick={applyConstraints}>APPLY</button>
          {pendingDirty
            ? <span className="chip chip-partial-tested">Pending changes — not applied yet</span>
            : <span className="chip">Applied</span>}
          <span className="faint micro">
            Applied: {fmt(applied.capital, 0)} {applied.currency} · risk {applied.riskMode === 'percent' ? `${applied.riskValue}%` : fmt(applied.riskValue)} ({applied.riskBasis}) · max active {applied.maxActive}
            · global offsets SL +{applied.globalOff.slPlus || 0}/−{applied.globalOff.slMinus || 0} · TP +{applied.globalOff.tpPlus || 0}/−{applied.globalOff.tpMinus || 0} pips.
            Edits stay pending until APPLY; applied values drive the next run and are recorded in its result.
          </span>
        </div>
      </section>

      {/* ================= 2. RANGE BUTTONS ================= */}
      <div className="dt-range-row" role="group" aria-label="Testing range">
        <span className="faint">Testing range (ends at the latest available candle per node):</span>
        {DURATIONS.map((d) => (
          <button key={d.label} type="button" aria-pressed={duration === d.days}
                  className={`btn btn-xs ${duration === d.days ? 'btn-primary' : 'btn-ghost'}`}
                  onClick={() => setDuration(d.days)}>{d.label}</button>
        ))}
      </div>

      {/* ================= 3. MAIN NODE TABLE ================= */}
      <section className="builder-section" aria-labelledby="dt-nodes">
        <h4 id="dt-nodes"><span className="section-num">2</span> Nodes <span className="faint">({filteredRows.length} filtered · {selected.size} selected · own instrument/timeframe per node)</span></h4>
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
          <button type="button" className="btn btn-danger" disabled={!dtTask || !['QUEUED','RUNNING','PAUSED'].includes(dtTask.status)}
                  onClick={async () => { if (!dtTask) return; try { await api.tasks.cancel(dtTask.id); setNotice('STOP requested — the active DeepTesting task will stop cooperatively at the next candle checkpoint.'); refreshTasks() } catch (e) { setNotice(`Unable to stop DeepTesting: ${String(e)}`) } }}>STOP</button>
          <button type="button" className="btn btn-ghost btn-xs" disabled={busy}
                  onClick={() => runNodes(filteredRows.map((r) => r.node.id))}>Run all filtered</button>
        </div>

        {dtTask && (
          <div className="dt-progress" aria-label="DeepTesting progress">
            <div className="dt-progress-head">
              <strong>DEEPTESTING</strong>
              <span>{dtTask.completed_units} / {dtTask.total_units} nodes</span>
              <span className="task-bar-track" role="progressbar" aria-valuenow={Math.round(dtTask.percentage)}
                    aria-valuemin={0} aria-valuemax={100} aria-label="DeepTesting overall progress">
                <span className="task-bar-fill" style={{ width: `${dtTask.percentage}%`, background: '#e8b93e' }} />
              </span>
              <span>{Math.round(dtTask.percentage)}%</span>
              <span className="faint">{dtTask.status}{dtTask.error ? ` · ${dtTask.error}` : ''}</span>
              <button type="button" className="btn btn-ghost btn-xs" aria-expanded={progressOpen}
                      aria-label="Toggle node progress details"
                      onClick={() => setProgressOpen(!progressOpen)}>{progressOpen ? '▾' : '▸'}</button>
            </div>
            {progressOpen && (
              <table className="node-table dt-progress-table" aria-label="Node by node progress">
                <thead><tr><th>Node</th><th>Status</th><th>Trades</th><th>Progress</th><th>Detail</th></tr></thead>
                <tbody>
                  {(dtTask.payload?.nodes ?? []).map((n: any) => (
                    <tr key={n.node_id}>
                      <td>Node {n.node_id} — {n.name}</td>
                      <td>{n.status}</td>
                      <td>{n.trades ?? 0} trades</td>
                      <td>{n.status === 'COMPLETED' ? '100%' : n.status === 'RUNNING'
                        ? (n.progress_pct != null ? `${Math.round(n.progress_pct)}% (${n.processed ?? 0}/${n.total_candles ?? '…'} candles)` : '…')
                        : n.status === 'QUEUED' ? '—' : '—'}</td>
                      <td>{n.error ? <DeepTestingErrorPanel nodeId={n.node_id} runId={n.run_id} error={n.technical_details ?? n.error} explicitStatus={n.status} /> : (n.run_id ? `run #${n.run_id}` : '')}</td>
                    </tr>))}
                </tbody>
              </table>
            )}
          </div>
        )}

        <div className="dt-table-scroll">
          <table className="node-table dt-node-table">
            <thead><tr>
              <th>★</th><th>✓</th>
              {nodeColumns.slice(0, 2).map((c) => <SortTh key={c.key} col={c} sort={nodeSort} setSort={setNodeSort} />)}
              <th>Type</th><th>Instrument</th><th>TF</th><th>Dir</th><th>Status</th>
              <th>SL + pips</th><th>SL − pips</th><th>TP + pips</th><th>TP − pips</th><th>Offset source</th>
              {nodeColumns.slice(2).map((c) => <SortTh key={c.key} col={c} sort={nodeSort} setSort={setNodeSort} />)}
              <th>Win/Loss</th><th>Scenarios</th><th></th>
            </tr></thead>
            <tbody>
              {pageRows.map((r) => {
                const n = r.node
                const off = nodeOffsets[n.id] ?? EMPTY_OFF
                const setOff = (patch: Partial<Offsets>) => setNodeOffsets({ ...nodeOffsets, [n.id]: { ...off, ...patch } })
                const childScenarios = scenarios.filter((sc) => (sc.node_id ?? sc.config?.node_id ?? sc.config?.node_ids?.[0]) === n.id)
                return (
                  <Fragment key={n.id}>
                  <tr className={inspectId === n.id ? 'row-active' : ''}>
                    <td><button type="button" className="btn btn-ghost btn-xs" aria-label={`${n.starred ? 'Unstar' : 'Star'} node ${n.id}`}
                                onClick={() => toggleStar(n)}>{n.starred ? '★' : '☆'}</button></td>
                    <td><input type="checkbox" aria-label={`Select node ${n.id}`} checked={selected.has(n.id)}
                               onChange={(e) => { const nx = new Set(selected); if (e.target.checked) nx.add(n.id); else nx.delete(n.id); setSelected(nx) }} /></td>
                    <td>{n.id}</td><td>{childScenarios.length > 0 && <button type="button" className="btn btn-ghost btn-xs" aria-label={`${expandedScenarioParents.has(n.id) ? 'Collapse' : 'Expand'} scenarios for node ${n.id}`} onClick={() => setExpandedScenarioParents(prev => { const next = new Set(prev); next.has(n.id) ? next.delete(n.id) : next.add(n.id); return next })}>{expandedScenarioParents.has(n.id) ? '▾' : '▸'} </button>}{n.name}</td><td>{n.origin}</td><td>{instrument}</td><td>{n.timeframe ?? '—'}</td>
                    <td>{r.direction}</td>
                    <td><span className={`chip chip-${r.status.replace(' ', '-').toLowerCase()}`}>{r.status}</span></td>
                    <td><input className="input input-xs num dt-off-in" type="number" step="any" aria-label={`SL plus node ${n.id}`} value={off.slPlus} onChange={(e) => setOff({ slPlus: e.target.value })} /></td>
                    <td><input className="input input-xs num dt-off-in" type="number" step="any" aria-label={`SL minus node ${n.id}`} value={off.slMinus} onChange={(e) => setOff({ slMinus: e.target.value })} /></td>
                    <td><input className="input input-xs num dt-off-in" type="number" step="any" aria-label={`TP plus node ${n.id}`} value={off.tpPlus} onChange={(e) => setOff({ tpPlus: e.target.value })} /></td>
                    <td><input className="input input-xs num dt-off-in" type="number" step="any" aria-label={`TP minus node ${n.id}`} value={off.tpMinus} onChange={(e) => setOff({ tpMinus: e.target.value })} /></td>
                    <td className="faint micro">SL: {offsetSource(n.id, 'sl')}<br />TP: {offsetSource(n.id, 'tp')}<br />
                      <button type="button" className="btn btn-ghost btn-xs" aria-label={`Apply offsets node ${n.id}`}
                              onClick={() => applyNodeOffsets(n.id)}>Apply</button>
                      {nodePending(n.id) && <span className="chip chip-partial-tested">pending</span>}</td>
                    <td>{fmt(r.total_trades, 0)}</td><td>{r.win_rate != null ? `${fmt(r.win_rate * 100, 1)}%` : 'N/A'}</td>
                    <td>{fmt(r.net_profit)}</td><td>{fmt(r.profit_factor, 3)}</td><td>{fmt(r.expectancy)}</td>
                    <td>{fmt(r.max_drawdown_amount)}</td><td>{fmt(r.max_drawdown_pct)}</td>
                    <td>{fmt(r.recovery_factor, 3)}</td><td>{fmt(r.max_consecutive_losses, 0)}</td>
                    <td>{fmt(r.max_consecutive_loss_amount)}</td>
                    <td>{r.max_consecutive_loss_pct != null ? `${fmt(r.max_consecutive_loss_pct)}%` : 'N/A'}</td>
                    <td>{tsFmt(r.last_tested)}</td>
                    <td>{fmt(r.winning_trades, 0)}/{fmt(r.losing_trades, 0)}</td>
                    <td>{childScenarios.length > 0 ? <button type="button" className="btn btn-ghost btn-xs" onClick={() => setExpandedScenarioParents(prev => { const next = new Set(prev); next.has(n.id) ? next.delete(n.id) : next.add(n.id); return next })}>{r.scenarioCount} saved · {expandedScenarioParents.has(n.id) ? 'hide' : 'show'}</button> : 0}</td>
                    <td><button type="button" className="btn btn-ghost btn-xs" onClick={() => openNodeRun(n.id)}>Inspect</button></td>
                  </tr>
                  {expandedScenarioParents.has(n.id) && childScenarios.map((sc: any) => { const st = sc.stats ?? {}; const summary = scenarioFilterSummary(sc.filters ?? sc.config?.filters ?? sc.config); const winRate = st.winRate ?? st.win_rate; return <tr key={`scenario-${sc.id}`} className="dt-scenario-child"><td></td><td></td><td>{n.id}↳{sc.id}</td><td title={summary}><span className="faint">↳ </span><b>{sc.name}</b><br/><span className="faint micro" title={summary}>{summary}</span></td><td>SCENARIO</td><td>{instrument}</td><td>{n.timeframe ?? '—'}</td><td>{st.direction ?? '—'}</td><td>Saved</td><td colSpan={5} className="faint micro">Source run #{sc.source_run_id ?? sc.config?.source_run_id} · v{sc.node_version ?? sc.config?.node_version}<br/><button type="button" className="btn btn-ghost btn-xs" onClick={() => openScenario(sc)}>Open filters</button></td><td>{fmt(st.trades ?? st.total_trades, 0)}</td><td>{winRate == null ? 'N/A' : `${fmt(winRate > 1 ? winRate : winRate * 100, 1)}%`}</td><td>{fmt(st.net_profit ?? st.net)}</td><td>{fmt(st.profit_factor ?? st.pf, 3)}</td><td>{fmt(st.expectancy)}</td><td>{fmt(st.max_drawdown_amount ?? st.maxDd)}</td><td>{fmt(st.max_drawdown_pct)}</td><td>{fmt(st.recovery_factor, 3)}</td><td>{fmt(st.max_consecutive_losses, 0)}</td><td>{fmt(st.max_consecutive_loss_amount)}</td><td>{st.max_consecutive_loss_pct == null ? 'N/A' : `${fmt(st.max_consecutive_loss_pct)}%`}</td><td>—</td><td>{fmt(st.wins, 0)}/{fmt(st.losses, 0)}</td><td></td><td><button type="button" className="btn btn-ghost btn-xs" onClick={() => openScenario(sc)}>Inspect</button></td></tr> })}
                  </Fragment>
                )
              })}
              {pageRows.length === 0 && <tr><td colSpan={28} className="faint">No nodes match the active filters.</td></tr>}
            </tbody>
          </table>
        </div>
        <div className="dt-toolbar">
          <button type="button" className="btn btn-ghost btn-xs" disabled={page === 0} onClick={() => setPage(page - 1)}>← Prev</button>
          <span className="faint">Page {page + 1} of {Math.max(1, Math.ceil(sortedRows.length / PAGE_SIZE))}</span>
          <button type="button" className="btn btn-ghost btn-xs" disabled={(page + 1) * PAGE_SIZE >= sortedRows.length} onClick={() => setPage(page + 1)}>Next →</button>
        </div>
      </section>

      {/* ================= 4. INSPECTION ================= */}
      {inspectId !== null && (
        <section className="builder-section" aria-labelledby="dt-inspect">
          <h4 id="dt-inspect"><span className="section-num">3</span> Inspection — {inspectNode?.name ?? 'node'} <span className="faint">#{inspectId} · {instrument} {inspectNode?.timeframe ?? ''} v{inspectNode?.version}</span>
            <button type="button" className="btn btn-ghost btn-xs" onClick={() => setInspectId(null)}>✕ Close</button></h4>

          {!activeRun && <p className="faint">No DeepTesting run for this node yet. Select a range above and run it — statistics, day/session filters and the equity curve appear here from the persisted result.</p>}
          {activeRun && (activeRun.status === 'FAILED' || activeRun.status === 'BLOCKED') && activeRun.error &&
            <DeepTestingErrorPanel nodeId={activeRun.node_id} runId={activeRun.id} error={activeRun.error} explicitStatus={activeRun.status} />}

          {activeRun && stats && (
            <>
              <p className="faint">
                Run #{activeRun.id} · <strong>{testingStatus(activeRun)}</strong>
                · requested {tsFmt(activeRun.metrics?.requested_range?.start)} → {tsFmt(activeRun.metrics?.requested_range?.end)}
                · actual {tsFmt(activeRun.metrics?.actual_range?.start)} → {tsFmt(activeRun.metrics?.actual_range?.end)}
                · candles tested {fmt(activeRun.metrics?.candles_tested, 0)}
                · spread mode {String(activeRun.config?.spread_mode ?? activeRun.assumptions?.spread_mode ?? 'unknown')}
                · readiness {activeRun.data_quality?.readiness}
                {(activeRun.data_quality?.warnings ?? []).length > 0 && <> · warnings: {(activeRun.data_quality?.warnings ?? []).join('; ')}</>}
                · effective offsets SL {activeRun.assumptions?.effective_offsets?.sl?.offset_pips ?? 0} pips ({activeRun.assumptions?.effective_offsets?.sl?.source ?? 'strategy'}) / TP {activeRun.assumptions?.effective_offsets?.tp?.offset_pips ?? 0} pips ({activeRun.assumptions?.effective_offsets?.tp?.source ?? 'strategy'})
                {(activeRun.assumptions?.effective_offsets?.warnings ?? []).map((w: string) => <span key={w} className="field-error"> {w}</span>)}
                {anyFilter
                  ? <span className="chip chip-partial-tested"> FILTERED VIEW — statistics below are recalculated from the {filteredTrades.length} matching trades; the original run is untouched</span>
                  : runDiffersFromApplied(activeRun)
                    ? <span className="chip chip-partial-tested"> ORIGINAL result — generated with different applied constraints; rerun to use the current settings</span>
                    : <span className="chip"> ORIGINAL full DeepTesting result</span>}
              </p>

              {(() => {
                const eff = activeRun.assumptions?.effective_offsets ?? {}
                const strat = eff.strategy ?? {}
                const rc = activeRun.config ?? {}
                const rno = (rc.node_offsets ?? {})[String(inspectId)] ?? {}
                return (
                  <table className="node-table" aria-label="Effective offsets">
                    <tbody>
                      <tr><th colSpan={4}>Effective offsets — values used by this result (pending edits are NOT shown here)</th></tr>
                      <tr><td>Original strategy SL / TP</td><td>{fmtDist(strat.sl)} / {fmtDist(strat.tp)}</td>
                          <td>Source</td><td>strategy</td></tr>
                      <tr><td>Global offsets (as run)</td><td colSpan={3}>SL +{rc.global_sl_plus_pips ?? 0} / −{rc.global_sl_minus_pips ?? 0} · TP +{rc.global_tp_plus_pips ?? 0} / −{rc.global_tp_minus_pips ?? 0} pips</td></tr>
                      <tr><td>Node offsets (as run)</td><td colSpan={3}>SL +{rno.sl_plus_pips ?? 0} / −{rno.sl_minus_pips ?? 0} · TP +{rno.tp_plus_pips ?? 0} / −{rno.tp_minus_pips ?? 0} pips</td></tr>
                      <tr><td>Effective SL offset</td><td>{eff.sl?.offset_pips ?? 0} pips ({eff.sl?.source ?? 'strategy'})</td>
                          <td>Effective TP offset</td><td>{eff.tp?.offset_pips ?? 0} pips ({eff.tp?.source ?? 'strategy'})</td></tr>
                      {(eff.warnings ?? []).map((w: string) => (
                        <tr key={w}><td colSpan={4} className="field-error">{w}</td></tr>))}
                    </tbody>
                  </table>
                )
              })()}

              {/* complete statistics — ONE compact table, stats first */}
              <table className="node-table dt-stats" aria-label="Complete statistics">
                <tbody>
                  <tr><th colSpan={4}>Trading statistics {anyFilter ? '(filtered)' : ''}</th></tr>
                  <tr><td>Total trades</td><td>{stats.total_trades}</td><td>Winning / Losing / Breakeven</td><td>{stats.winning_trades} / {stats.losing_trades} / {stats.breakeven_trades}</td></tr>
                  <tr><td>Win rate / Loss rate</td><td>{stats.win_rate != null ? `${fmt(stats.win_rate * 100, 1)}%` : 'N/A'} / {stats.win_rate != null ? `${fmt((1 - stats.win_rate) * 100, 1)}%` : 'N/A'}</td><td>Gross profit / loss</td><td><span className="stat-gp">{fmt(stats.gross_profit)}</span> / <span className="stat-gl">{fmt(stats.gross_loss)}</span></td></tr>
                  <tr><td>Net profit/loss</td><td><span className={typeof stats.net_profit === 'number' ? (stats.net_profit > 0 ? 'stat-pos' : stats.net_profit < 0 ? 'stat-neg' : '') : ''}>{fmt(stats.net_profit)}</span></td><td>Profit factor</td><td>{fmt(stats.profit_factor, 3)}</td></tr>
                  <tr><td>Avg win / loss</td><td>{fmt(stats.avg_win)} / {fmt(stats.avg_loss)}</td><td>Expectancy</td><td>{fmt(stats.expectancy)}</td></tr>
                  <tr><td>Largest win / loss</td><td>{fmt(stats.largest_win)} / {fmt(stats.largest_loss)}</td><td>Avg holding</td><td>{fmt((stats.avg_holding_seconds ?? 0) / 3600, 1)}h</td></tr>
                  <tr><th colSpan={4}>Account statistics</th></tr>
                  <tr><td>Initial capital</td><td>{fmt(stats.initial_capital, 0)} {currency}</td><td>Balance / Equity</td><td>{fmt(stats.final_balance)} / {fmt(stats.final_equity)}</td></tr>
                  <tr><td>Total return</td><td>{fmt(stats.net_profit)} ({fmt(stats.return_pct)}%)</td><td>Unrealized P/L</td><td>{anyFilter ? 'N/A (filter view)' : fmt(m?.unrealized_at_end)}</td></tr>
                  <tr><th colSpan={4}>Risk statistics</th></tr>
                  <tr><td>Max drawdown</td><td><span className="stat-dd">{fmt(stats.max_drawdown_amount)} ({fmt(stats.max_drawdown_pct)}%)</span></td><td>Average drawdown</td><td><span className="stat-dd">{fmt(stats.avg_drawdown_amount)}</span></td></tr>
                  <tr><td>Current drawdown</td><td><span className="stat-dd">{fmt(stats.current_drawdown_amount)} ({fmt(m?.current_drawdown_pct)}%)</span></td><td>Recovery factor</td><td>{anyFilter ? 'N/A (filter view)' : fmt(m?.recovery_factor, 3)}</td></tr>
                  <tr><td>DD duration</td><td>{anyFilter ? 'N/A (filter view)' : `${fmt((m?.max_drawdown_duration_seconds ?? 0) / 86400, 1)}d`}</td><td>Max simultaneous / planned open risk</td><td>{stats.max_simultaneous} / {fmt(stats.max_planned_open_risk)}</td></tr>
                  <tr><th colSpan={4}>Streak statistics</th></tr>
                  <tr><td>Max consec. W / L</td><td>{stats.max_consecutive_wins} / {stats.max_consecutive_losses}</td><td>Current streak</td><td>{stats.current_streak}</td></tr>
                  <tr><td>Max consecutive losses</td><td>{stats.max_consecutive_losses}</td><td>Max consecutive loss amount</td><td>{fmt(stats.max_consecutive_loss_amount)}</td></tr>
                  <tr><td>Max consecutive loss %</td><td>{stats.max_consecutive_loss_pct != null ? `${fmt(stats.max_consecutive_loss_pct)}%` : 'N/A'}</td><td>Of starting capital</td><td>{fmt(stats.initial_capital, 0)} {currency}</td></tr>
                  <tr><td>Avg W / L streak</td><td>{anyFilter ? 'N/A (filter view)' : `${fmt(m?.streaks?.avg_winning_streak)} / ${fmt(m?.streaks?.avg_losing_streak)}`}</td><td>Long / Short net</td><td>{anyFilter ? 'N/A (filter view)' : `${fmt(m?.long_net)} / ${fmt(m?.short_net)}`}</td></tr>
                </tbody>
              </table>

              {/* compact BUY / SELL summary cards, directly above weekday/session table */}
              {(() => {
                const L = dirShape(dirLong) ?? dirShape({ total_trades: 0, winning_trades: 0, losing_trades: 0, breakeven_trades: 0, gross_profit: 0, gross_loss: 0, net_profit: 0, drawdown: {} })
                const S = dirShape(dirShort) ?? dirShape({ total_trades: 0, winning_trades: 0, losing_trades: 0, breakeven_trades: 0, gross_profit: 0, gross_loss: 0, net_profit: 0, drawdown: {} })
                if (!L || !S) return null
                const cards = [['BUY', L, 'stat-sec-buy'], ['SELL', S, 'stat-sec-sell']] as const
                const details: [string, any, any][] = [
                  ['Avg win', L.avgWin, S.avgWin], ['Avg loss', L.avgLoss, S.avgLoss],
                  ['Expectancy', L.expectancy, S.expectancy],
                  ['Max drawdown', `${fmt(L.ddMax)} (${fmt(L.ddMaxPct)}%)`, `${fmt(S.ddMax)} (${fmt(S.ddMaxPct)}%)`],
                  ['Average drawdown', L.ddAvg, S.ddAvg],
                  ['Return on capital', L.returnPct != null ? `${fmt(L.returnPct)}%` : 'N/A', S.returnPct != null ? `${fmt(S.returnPct)}%` : 'N/A'],
                  ['Avg holding (seconds)', L.avgHold, S.avgHold], ['Max consec. W / L', `${L.maxW} / ${L.maxL}`, `${S.maxW} / ${S.maxL}`],
                ]
                return <>
                  <div className="dt-dir-cards" aria-label="BUY and SELL statistics">
                    {cards.map(([label, d, cls]) => <section className={`dt-dir-card ${cls}`} key={label}>
                      <h4>{label}</h4>
                      <dl>{[
                        ['Trade count', d.total], ['Wins', d.wins], ['Losses', d.losses], ['Breakeven', d.be],
                        ['Win rate', d.winRate != null ? `${fmt(d.winRate * 100, 1)}%` : 'N/A'],
                        ['Gross profit', fmt(d.gp)], ['Gross loss', fmt(d.gl)], ['Net P/L', fmt(d.net)], ['Profit factor', fmt(d.pf, 3)],
                      ].map(([k,v]) => <div key={String(k)}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
                    </section>)}
                  </div>
                  <details className="dt-dir-extra"><summary>Additional directional statistics</summary>
                    <table className="node-table"><tbody>{details.map(([label,a,b]) => <tr key={label}><td>{label}</td><td>{typeof a === 'number' ? fmt(a) : a}</td><td>{typeof b === 'number' ? fmt(b) : b}</td></tr>)}</tbody></table>
                  </details>
                  <div className="faint micro">Both directions are shown regardless of direction filter{anyFilter ? ' (filtered trade set)' : ''}; LONG + SHORT reconcile with the ledger.</div>
                </>
              })()}
              {/* weekday/session filter table — whole boxes clickable */}
              <div className="dt-table-scroll">
                <table className="node-table dt-session-grid" aria-label="Weekday and session filters">
                  <thead><tr><th>Session</th>{COL_DAYS.map((d) => (
                    <th key={d} className="dt-clickable" onClick={() => toggleDay(d, !daySel[d])}>
                      <label className="check small" onClick={(e) => e.stopPropagation()}><input type="checkbox"
                        ref={(el) => { dayCheckboxRefs.current[d] = el }}
                        checked={!!daySel[d]} aria-label={`Include ${d}`}
                        onClick={(e) => e.stopPropagation()}
                        onChange={(e) => toggleDay(d, e.target.checked)} /> {d === 'UNASSIGNED' ? 'UNASSIGNED (weekend)' : d}</label>
                    </th>))}
                    <th>TOTAL — selected days</th>
                  </tr></thead>
                  <tbody>
                    {SESSION_ROWS.map((session) => (
                      <tr key={session}>
                        {session === 'TOTAL DAY'
                          ? <th>TOTAL DAY</th>
                          : <th className="dt-clickable" onClick={() => toggleSessionAll(session, !sessionState(session).all)}>
                              <label className="check small" onClick={(e) => e.stopPropagation()}><input type="checkbox"
                                ref={(el) => { sessionCheckboxRefs.current[session] = el }}
                                checked={sessionState(session).all} aria-label={`Include ${session} all days`}
                                onClick={(e) => e.stopPropagation()}
                                onChange={(e) => toggleSessionAll(session, e.target.checked)} /> {session === 'UNASSIGNED' ? 'UNASSIGNED (weekend)' : session}</label>
                            </th>}
                        {COL_DAYS.map((d) => {
                          const list = session === 'TOTAL DAY'
                            ? trades.filter((t) => weekdayOf(t) === d)
                            : trades.filter((t) => cellKey(t) === `${d}|${session}`)
                          const st = cellStats(list)
                          const key = `${d}|${session}`
                          const netCls = st.net > 0 ? 'dt-net-pos' : st.net < 0 ? 'dt-net-neg' : 'dt-net-zero'
                          return (
                            <td key={key}
                                className={`dt-clickable ${session !== 'TOTAL DAY' && !cellSel[key] ? 'dt-cell-off' : ''}`}
                                onClick={() => { if (session === 'TOTAL DAY') toggleDay(d, !daySel[d]); else setCell(key, d, !cellSel[key]) }}>
                              <span className="dt-cell-box">
                                {session !== 'TOTAL DAY' && (
                                  <input type="checkbox" checked={!!cellSel[key]}
                                    aria-label={`Include ${d} ${session}`}
                                    onClick={(e) => e.stopPropagation()}
                                    onChange={(e) => setCell(key, d, e.target.checked)} />
                                )}
                                <span className="dt-cell-stats">
                                  <span>{st.trades} trades · {st.wins}W/{st.losses}L/{st.be}BE</span>
                                  <span>WR {st.winRate != null ? `${fmt(st.winRate * 100, 0)}%` : 'N/A'} · PF {fmt(st.pf, 2)}</span>
                                  <span>GP {fmt(st.gp, 0)} · GL {fmt(st.gl, 0)}</span>
                                  <span className="dt-dd-line">DD {st.ddAmt != null ? `${fmt(st.ddAmt, 0)} (${fmt(st.ddPct, 1)}%)` : 'N/A'}</span>
                                  <span className={netCls}><strong>Net {fmt(st.net, 0)}</strong></span>
                                </span>
                              </span>
                            </td>
                          )
                        })}
                        <td className="dt-agg">
                          {(() => { const agg = aggStats(session); const netCls = agg.net > 0 ? 'dt-net-pos' : agg.net < 0 ? 'dt-net-neg' : 'dt-net-zero'; return (
                          <span className="dt-cell-box">
                            <span className="dt-cell-stats">
                              <span>{agg.trades} trades · {agg.wins}W/{agg.losses}L/{agg.be}BE</span>
                              <span>WR {agg.winRate != null ? `${fmt(agg.winRate * 100, 0)}%` : 'N/A'} · PF {fmt(agg.pf, 2)}</span>
                              <span>GP {fmt(agg.gp, 0)} · GL {fmt(agg.gl, 0)}</span>
                              <span className="dt-dd-line">DD {agg.ddAmt != null ? `${fmt(agg.ddAmt, 0)} (${fmt(agg.ddPct, 1)}%)` : 'N/A'}</span>
                              <span className={netCls}><strong>Net {fmt(agg.net, 0)}</strong></span>
                            </span>
                          </span>) })()}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="faint micro">Click any box to toggle it: a weekday box toggles the whole day, a session heading toggles that session across all days, a cell toggles only that weekday/session. Weekday session cells are single-label UTC entry-time windows (ASIA 22–08 · ASIA-LONDON 08–09 · LONDON 09–13 · LONDON-NY 13–17 · NY 17–22); weekend entries are classified only as UNASSIGNED. Cells never double-count and totals reconcile with the trade ledger. The unfiltered TOTAL DAY drawdown uses the run's bar-close equity curve (same as the main report); filtered/session-cell drawdown uses the selected trades' realized close-to-close P/L path. The original result is never modified.</p>
              <div className="dt-toolbar">
                <button type="button" className="btn btn-ghost btn-xs" onClick={() => {
                  setDaySel(Object.fromEntries(COL_DAYS.map((d) => [d, true])))
                  setCellSel(Object.fromEntries(COL_DAYS.flatMap((d) => SESSION_ROWS.slice(1).map((s) => [`${d}|${s}`, true]))))
                }}>SELECT ALL DAYS/SESSIONS</button>
                <button type="button" className="btn btn-ghost btn-xs" onClick={() => {
                  setDaySel(Object.fromEntries(COL_DAYS.map((d) => [d, false])))
                  setCellSel(Object.fromEntries(COL_DAYS.flatMap((d) => SESSION_ROWS.slice(1).map((s) => [`${d}|${s}`, false]))))
                }}>DESELECT ALL</button>
                <button type="button" className="btn btn-ghost btn-xs" onClick={deselectNegative}>DESELECT -VE NET BOXES</button>
              </div>

              {/* save scenario */}
              <div className="dt-toolbar">
                <button type="button" className="btn btn-primary" onClick={() => setModal(true)}>SAVE FILTERS AS SCENARIO</button>
                {nodeScenarios.map((sc) => (
                  <span key={sc.id} className="chip">
                    <button type="button" className="btn btn-ghost btn-xs" onClick={() => openScenario(sc)}>{sc.name} (#{sc.id})</button>
                    <button type="button" className="btn btn-ghost btn-xs" aria-label={`Delete scenario ${sc.id}`}
                            onClick={async () => { await api.deeptesting.deleteScenario(instrument, sc.id); api.deeptesting.listScenarios(instrument).then((r) => setScenarios(r.scenarios ?? [])) }}>✕</button>
                  </span>
                ))}
                {nodeScenarios.length > 0 && <span className="faint micro">Scenario configuration, source run and filtered result are persisted independently of the parent node.</span>}
              </div>

              {/* ONE equity curve */}
              <EquityCurve points={equityPoints} capital={runCapital}
                label={anyFilter ? 'Equity curve (filtered trades, cumulative)' : 'Equity curve (engine, incl. unrealized)'} />

              {/* trade filters (Section 8) - drive stats, equity and table */}
              <div className="dt-trade-filters" aria-label="Trade filters">
                <label className="mini-field">Direction
                  <select className="input input-xs" value={tf.side} aria-label="Trade direction"
                          onChange={(e) => setTf({ ...tf, side: e.target.value })}>
                    <option value="">All</option><option value="LONG">LONG only</option><option value="SHORT">SHORT only</option>
                  </select></label>
                <label className="mini-field">Outcome
                  <select className="input input-xs" value={tf.outcome} aria-label="Outcome filter"
                          onChange={(e) => setTf({ ...tf, outcome: e.target.value })}>
                    <option value="">All</option><option value="WIN">Winners</option><option value="LOSS">Losers</option><option value="BREAKEVEN">Breakeven</option>
                  </select></label>
                <label className="mini-field">Session
                  <select className="input input-xs" value={tf.session} aria-label="Session filter"
                          onChange={(e) => setTf({ ...tf, session: e.target.value })}>
                    <option value="">All</option>
                    {['ASIA', 'ASIA-LONDON', 'LONDON', 'LONDON-NY', 'NY', 'UNASSIGNED'].map((x) => <option key={x} value={x}>{x}</option>)}
                  </select></label>
                <label className="mini-field">Weekday
                  <select className="input input-xs" value={tf.weekday} aria-label="Weekday filter"
                          onChange={(e) => setTf({ ...tf, weekday: e.target.value })}>
                    <option value="">All</option>
                    {[...WEEKDAYS, 'UNASSIGNED'].map((x) => <option key={x} value={x}>{x}</option>)}
                  </select></label>
                <label className="mini-field">From
                  <input type="date" className="input input-xs" value={tf.from} aria-label="Entry date from"
                         onChange={(e) => setTf({ ...tf, from: e.target.value })} /></label>
                <label className="mini-field">To
                  <input type="date" className="input input-xs" value={tf.to} aria-label="Entry date to"
                         onChange={(e) => setTf({ ...tf, to: e.target.value })} /></label>
                <label className="mini-field">Exit reason
                  <select className="input input-xs" value={tf.exitReason} aria-label="Exit reason filter"
                          onChange={(e) => setTf({ ...tf, exitReason: e.target.value })}>
                    <option value="">All</option>
                    {[...new Set(trades.map((t) => t.exit_reason).filter(Boolean))].map((x: any) => <option key={x} value={x}>{x}</option>)}
                  </select></label>
                <label className="mini-field">Min net
                  <input type="number" className="input input-xs" value={tf.minNet} aria-label="Minimum net P/L"
                         onChange={(e) => setTf({ ...tf, minNet: e.target.value })} /></label>
                <label className="mini-field">Max net
                  <input type="number" className="input input-xs" value={tf.maxNet} aria-label="Maximum net P/L"
                         onChange={(e) => setTf({ ...tf, maxNet: e.target.value })} /></label>
                <label className="mini-field">Min hold (h)
                  <input type="number" className="input input-xs" value={tf.minHoldH} aria-label="Minimum holding hours"
                         onChange={(e) => setTf({ ...tf, minHoldH: e.target.value })} /></label>
                <label className="mini-field">Max hold (h)
                  <input type="number" className="input input-xs" value={tf.maxHoldH} aria-label="Maximum holding hours"
                         onChange={(e) => setTf({ ...tf, maxHoldH: e.target.value })} /></label>
                <span className="faint micro">{instrument} · {activeRun.timeframe} · {filteredTrades.length} matching trade(s)</span>
                {tfActive && <button type="button" className="btn btn-ghost btn-xs"
                                      onClick={() => setTf({ side: '', outcome: '', session: '', weekday: '', exitReason: '', from: '', to: '', minNet: '', maxNet: '', minHoldH: '', maxHoldH: '' })}>Clear filters</button>}
              </div>

              {/* paginated trade list (server-side sort + pagination) */}
              <div className="dt-toolbar">
                <span style={{ marginLeft: 'auto' }} className="dt-export-btns">
                  <button type="button" className="btn btn-ghost btn-xs" onClick={() => exportTableTxt()}>EXPORT TABLE</button>
                  <button type="button" className="btn btn-ghost btn-xs"
                          onClick={() => void downloadFile(api.deeptesting.auditExportUrl(instrument, activeRun.id, 'full'), `hero1_audit_run${activeRun.id}_full.zip`).catch((e) => setNotice(String(e)))}>EXPORT TRADING DATA</button>
                  <button type="button" className="btn btn-ghost btn-xs"
                          onClick={() => void downloadFile(api.deeptesting.auditExportUrl(instrument, activeRun.id, 'filtered', serverFilters, selectedCells), `hero1_audit_run${activeRun.id}_filtered.zip`).catch((e) => setNotice(String(e)))}>EXPORT TRADING DATA (FILTERED)</button>
                </span>
                <button type="button" className="btn btn-primary" aria-expanded={showTrades}
                        onClick={() => setShowTrades(!showTrades)}>{showTrades ? 'HIDE TRADES' : 'SHOW TRADES'}</button>
                {showTrades && tradeData && <span className="faint micro">{tradeData.count} filtered trades · page {tradePage + 1} of {tradeData.pages} · sorted by {tradeSort.key} ({tradeSort.dir}) on the server across the full filtered set</span>}
              </div>
              {showTrades && (
                <div className="dt-table-scroll">
                  <table className="node-table" aria-label="Filtered trades">
                    <thead><tr>
                      {[['id', 'ID'], ['entry_ts', 'Entry'], ['exit_ts', 'Exit'], ['weekday', 'Weekday'],
                        ['session', 'Session'], ['side', 'Dir'], ['entry_price', 'Entry px'],
                        ['exit_price', 'Exit px'], ['initial_stop', 'SL'], ['take_profit', 'TP'],
                        ['volume', 'Size'], ['gross_pnl', 'Gross profit'], ['gross_pnl', 'Gross loss'], ['net_pnl', 'Net P/L'],
                        ['result', 'Outcome'], ['holding_seconds', 'Held'], ['exit_reason', 'Exit reason']].map(([key, label]) => (
                        <th key={`${key}-${label}`}>
                          <button type="button" className="btn btn-ghost btn-xs" aria-label={`Sort by ${label}`}
                                  onClick={() => setTradeSort((cur) => cur.key === key
                                    ? { key, dir: cur.dir === 'asc' ? 'desc' : 'asc' }
                                    : { key, dir: ['net_pnl', 'gross_pnl', 'volume', 'entry_price', 'exit_price'].includes(key) ? 'desc' : key === 'entry_ts' || key === 'exit_ts' ? 'desc' : 'asc' })}>
                            {label}{tradeSort.key === key ? (tradeSort.dir === 'asc' ? ' ↑' : ' ↓') : ''}
                          </button>
                        </th>))}
                      <th>Instrument</th><th>TF</th>
                    </tr></thead>
                    <tbody>
                      {(tradeData?.trades ?? []).map((t: any) => (
                        <tr key={t.id}>
                          <td>{t.id}</td><td>{tsFmt(t.entry_ts)}</td><td>{tsFmt(t.exit_ts)}</td>
                          <td>{t.weekday}</td><td>{t.session}</td><td><span className={t.side === 'LONG' ? 'dir-long' : 'dir-short'}>{t.side}</span></td>
                          <td>{fmt(t.entry_price, 2)}</td><td>{fmt(t.exit_price, 2)}</td>
                          <td>{fmt(t.initial_stop, 2)}</td><td>{fmt(t.take_profit, 2)}</td>
                          <td>{fmt(t.volume, 2)}</td>
                          <td>{t.gross_pnl > 0 ? fmt(t.gross_pnl) : fmt(0)}</td>
                          <td>{t.gross_pnl < 0 ? fmt(t.gross_pnl) : fmt(0)}</td>
                          <td><span className={t.net_pnl > 0 ? 'stat-pos' : t.net_pnl < 0 ? 'stat-neg' : ''}>{fmt(t.net_pnl)}</span></td>
                          <td>{t.result}</td><td>{fmt((t.holding_seconds ?? 0) / 3600, 1)}h</td>
                          <td>{t.exit_reason}</td><td>{instrument}</td><td>{activeRun.timeframe}</td>
                        </tr>))}
                      {(tradeData?.trades ?? []).length === 0 && (
                        <tr><td colSpan={19} className="faint">No trades match the active filters.</td></tr>)}
                    </tbody>
                  </table>
                </div>
              )}
              {showTrades && tradeData && (
                <div className="dt-toolbar">
                  <button type="button" className="btn btn-ghost btn-xs" disabled={tradePage === 0}
                          onClick={() => setTradePage(tradePage - 1)}>← Prev</button>
                  <span className="faint">Page {tradePage + 1} of {tradeData.pages} · {tradeData.count} trades</span>
                  <button type="button" className="btn btn-ghost btn-xs" disabled={tradePage + 1 >= tradeData.pages}
                          onClick={() => setTradePage(tradePage + 1)}>Next →</button>
                  <label className="mini-field">Page size
                    <select className="input input-xs" value={String(tradePageSize)} aria-label="Trade page size"
                            onChange={(e) => setTradePageSize(e.target.value === 'all' ? 'all' : Number(e.target.value))}>
                      {[10, 15, 25].map((n) => <option key={n} value={n}>{n}</option>)}
                      <option value="all">ALL</option>
                    </select></label>
                </div>
              )}
            </>
          )}
        </section>
      )}

      {/* scenario modal */}
      {modal && (
        <div className="dt-modal-backdrop" role="dialog" aria-modal="true" aria-label="Save filters as scenario">
          <div className="dt-modal">
            <h4>Save Filters as Scenario</h4>
            <label className="mini-field">Scenario Name
              <input className="input input-sm" value={scName} aria-label="Scenario name" onChange={(e) => setScName(e.target.value)} /></label>
            <label className="mini-field">Scenario Description
              <textarea className="input input-sm" rows={2} value={scDesc} aria-label="Scenario description" onChange={(e) => setScDesc(e.target.value)} /></label>
            <div className="faint dt-modal-summary">
              <p><strong>Node:</strong> #{inspectId} {inspectNode?.name} v{inspectNode?.version} · fingerprint {String(activeRun?.fingerprint ?? '').slice(0, 12)}…</p>
              <p><strong>Capital / Risk / Max active (applied):</strong> {fmt(applied.capital, 0)} {applied.currency} · {applied.riskMode} {applied.riskValue} ({applied.riskBasis}) · {applied.maxActive}</p>
              <p><strong>Range:</strong> last {duration} days · requested {tsFmt(activeRun?.metrics?.requested_range?.start)} → {tsFmt(activeRun?.metrics?.requested_range?.end)} · actual {tsFmt(activeRun?.metrics?.actual_range?.start)} → {tsFmt(activeRun?.metrics?.actual_range?.end)} ({activeRun?.metrics?.coverage_status})</p>
              <p><strong>Offsets (applied):</strong> global SL +{applied.globalOff.slPlus || 0}/−{applied.globalOff.slMinus || 0} · TP +{applied.globalOff.tpPlus || 0}/−{applied.globalOff.tpMinus || 0} pips · node #{inspectId} SL +{appliedNodeOffsets[inspectId ?? -1]?.slPlus || 0}/−{appliedNodeOffsets[inspectId ?? -1]?.slMinus || 0} · TP +{appliedNodeOffsets[inspectId ?? -1]?.tpPlus || 0}/−{appliedNodeOffsets[inspectId ?? -1]?.tpMinus || 0} pips · effective SL {activeRun?.assumptions?.effective_offsets?.sl?.offset_pips ?? 0} pips ({activeRun?.assumptions?.effective_offsets?.sl?.source ?? 'strategy'}) · TP {activeRun?.assumptions?.effective_offsets?.tp?.offset_pips ?? 0} pips ({activeRun?.assumptions?.effective_offsets?.tp?.source ?? 'strategy'})</p>
              <p><strong>Original result:</strong> run #{activeRun?.id} (immutable — this scenario references it; it is never edited)</p>
              <p><strong>Days/Sessions:</strong> {COL_DAYS.map((d) => `${d} ${daySel[d] ? '✓' : '✗'} (${SESSION_ROWS.slice(1).map((s) => `${s} ${cellSel[`${d}|${s}`] ? '✓' : '✗'}`).join(', ')})`).join(' · ')}</p>
              <p><strong>Filtered result:</strong> {filteredStats.trades} trades · net {fmt(filteredStats.net)} · win rate {filteredStats.winRate != null ? `${fmt(filteredStats.winRate * 100, 1)}%` : 'N/A'}</p>
            </div>
            <div className="dt-toolbar">
              <button type="button" className="btn btn-primary" onClick={saveScenario}>Save Scenario</button>
              <button type="button" className="btn btn-ghost" onClick={() => setModal(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
