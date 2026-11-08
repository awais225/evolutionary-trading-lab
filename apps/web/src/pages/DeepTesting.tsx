import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api/client'
import { useInstrument } from '../state/instrument'
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
const SESSION_ROWS = ['TOTAL DAY', 'ASIA', 'ASIA-LONDON', 'LONDON', 'LONDON-NY', 'NY'] as const
const DURATIONS = [
  { label: '1 Week', days: 7 }, { label: '2 Weeks', days: 14 },
  { label: '1 Month', days: 30 }, { label: '3 Months', days: 91 },
  { label: '6 Months', days: 182 }, { label: '1 Year', days: 365 },
  { label: '2 Years', days: 730 }, { label: '3 Years', days: 1095 },
]

/** Single-label session cells (UTC, entry time). Windows are contiguous
 * and non-overlapping so weekday/session cells never double-count:
 * ASIA 00-08 & 22-24, ASIA-LONDON 08-09, LONDON 09-13, LONDON-NY 13-17,
 * NY 17-22. ASIA-LONDON / LONDON-NY are the explicitly defined overlaps. */
export function sessionCell(ts: number): string {
  const h = new Date(ts * 1000).getUTCHours()
  if (h >= 22 || h < 8) return 'ASIA'
  if (h < 9) return 'ASIA-LONDON'
  if (h < 13) return 'LONDON'
  if (h < 17) return 'LONDON-NY'
  return 'NY'
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
  const [globalOff, setGlobalOff] = useState<Offsets>(saved?.globalOff ?? EMPTY_OFF)
  useEffect(() => {
    localStorage.setItem('hero1.dt.constraints', JSON.stringify(
      { capital, currency, riskMode, riskValue, riskBasis, maxActive, globalOff }))
  }, [capital, currency, riskMode, riskValue, riskBasis, maxActive, globalOff])

  /* ============ 2. RANGE BUTTONS ============ */
  const [duration, setDuration] = useState(365)

  /* ============ 3. MAIN NODE TABLE ============ */
  const [nodes, setNodes] = useState<any[]>([])
  const [runs, setRuns] = useState<any[]>([])
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [nodeOffsets, setNodeOffsets] = useState<Record<number, Offsets>>(() => {
    try { return JSON.parse(localStorage.getItem('hero1.dt.nodeOffsets2') ?? '{}') } catch { return {} }
  })
  useEffect(() => { localStorage.setItem('hero1.dt.nodeOffsets2', JSON.stringify(nodeOffsets)) }, [nodeOffsets])
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
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  /* ============ 4. INSPECTION ============ */
  const [inspectId, setInspectId] = useState<number | null>(null)
  const [activeRun, setActiveRun] = useState<any | null>(null)
  const [trades, setTrades] = useState<any[]>([])
  const [daySel, setDaySel] = useState<Record<string, boolean>>(
    Object.fromEntries(WEEKDAYS.map((d) => [d, true])))
  const [cellSel, setCellSel] = useState<Record<string, boolean>>(
    Object.fromEntries(WEEKDAYS.flatMap((d) => SESSION_ROWS.slice(1).map((s) => [`${d}|${s}`, true]))))
  const [scenarios, setScenarios] = useState<any[]>([])
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

  const testingStatus = (r: any): string => {
    if (!r) return 'UNTESTED'
    if (r.status === 'RUNNING') return 'RUNNING'
    if (r.status === 'FAILED') return String(r.error ?? '').includes('DATA_BLOCKED') ? 'BLOCKED' : 'FAILED'
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
      winning_trades: m?.winning_trades, losing_trades: m?.losing_trades,
      last_tested: run?.finished_at,
      scenarioCount: runs.filter((r) => r.node_id === n.id && r.scenario_id).length,
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
    { key: 'max_consecutive_losses', label: 'Consec. losses', type: 'number', get: (r) => r.max_consecutive_losses },
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
    if (pair(nodeOffsets[nodeId] ?? EMPTY_OFF, kind).set) return 'Individual node'
    if (pair(globalOff, kind).set) return 'Global DeepTesting'
    return 'Strategy'
  }

  const buildConfig = (extra: Record<string, unknown> = {}) => {
    const end = Math.floor(Date.now() / 1000)
    return {
      mode: 'initial',
      range: { start: end - duration * 86400, end },
      initial_capital: capital, account_currency: currency,
      risk_mode: riskMode, risk_value: riskValue, risk_basis: riskBasis,
      max_active_trades: maxActive,
      global_sl_plus_pips: globalOff.slPlus === '' ? null : Number(globalOff.slPlus),
      global_sl_minus_pips: globalOff.slMinus === '' ? null : Number(globalOff.slMinus),
      global_tp_plus_pips: globalOff.tpPlus === '' ? null : Number(globalOff.tpPlus),
      global_tp_minus_pips: globalOff.tpMinus === '' ? null : Number(globalOff.tpMinus),
      node_offsets: Object.fromEntries(Object.entries(nodeOffsets).map(([id, o]) => [id, {
        sl_plus_pips: o.slPlus === '' ? null : Number(o.slPlus),
        sl_minus_pips: o.slMinus === '' ? null : Number(o.slMinus),
        tp_plus_pips: o.tpPlus === '' ? null : Number(o.tpPlus),
        tp_minus_pips: o.tpMinus === '' ? null : Number(o.tpMinus),
      }])),
      ...extra,
    }
  }

  const runNodes = async (ids: number[], extra: Record<string, unknown> = {}) => {
    if (!ids.length) { setNotice('Select at least one node first.'); return }
    setBusy(true); setNotice(null)
    try {
      const res = await api.deeptesting.createRuns(instrument, ids, buildConfig(extra))
      const failed = res.runs.filter((r) => r.status === 'FAILED')
      setNotice(failed.length
        ? failed.map((f) => `node ${f.node_id}: ${f.error}`).join(' · ')
        : `Completed ${res.runs.length} run(s) using the last ${duration} days (partial where history is shorter).`)
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
  const cellKey = (t: any) => {
    const wd = WEEKDAYS[new Date(t.entry_ts * 1000).getUTCDay() - 1]
    return wd ? `${wd}|${sessionCell(t.entry_ts)}` : null
  }
  const isFilteredActive = useMemo(() =>
    Object.values(daySel).some((v) => !v) || Object.values(cellSel).some((v) => !v),
    [daySel, cellSel])
  const filteredTrades = useMemo(() => {
    if (!isFilteredActive) return trades
    return trades.filter((t) => {
      const wd = WEEKDAYS[new Date(t.entry_ts * 1000).getUTCDay() - 1]
      if (!wd || !daySel[wd]) return false
      if (!cellSel[`${wd}|${sessionCell(t.entry_ts)}`]) return false
      return true
    })
  }, [trades, daySel, cellSel, isFilteredActive])

  const cellStats = (list: any[]) => {
    const wins = list.filter((t) => t.net_pnl > 1e-9)
    const losses = list.filter((t) => t.net_pnl < -1e-9)
    const gp = wins.reduce((a, t) => a + t.net_pnl, 0)
    const gl = losses.reduce((a, t) => a + t.net_pnl, 0)
    return { trades: list.length, wins: wins.length, losses: losses.length,
             be: list.length - wins.length - losses.length,
             winRate: list.length ? wins.length / list.length : null,
             gp, gl, net: gp + gl, pf: gl < 0 ? gp / Math.abs(gl) : null }
  }

  const filteredStats = useMemo(() => {
    const g = [...filteredTrades].sort((a, b) => a.exit_ts - b.exit_ts)
    const base = cellStats(g)
    const wins = g.filter((t) => t.net_pnl > 1e-9), losses = g.filter((t) => t.net_pnl < -1e-9)
    let cum = 0, peak = 0, maxDd = 0, curW = 0, curL = 0, maxW = 0, maxL = 0
    const streaks: number[] = []
    for (const t of g) {
      cum += t.net_pnl; peak = Math.max(peak, cum); maxDd = Math.max(maxDd, peak - cum)
      if (t.net_pnl > 1e-9) { curW++; curL = 0 } else if (t.net_pnl < -1e-9) { curL++; curW = 0 }
      maxW = Math.max(maxW, curW); maxL = Math.max(maxL, curL); streaks.push(maxL)
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
             maxDd, maxDdPct: (capital + peak) > 0 ? (maxDd / (capital + peak)) * 100 : null,
             avgDd: ddMarks.length ? ddMarks.reduce((a, b) => a + b, 0) / ddMarks.length : 0,
             currentDd: peak - cum, maxW, maxL, curW, curL,
             balance: capital + base.net }
  }, [filteredTrades, capital])

  const m = activeRun?.metrics
  const stats = isFilteredActive ? {
    total_trades: filteredStats.trades, winning_trades: filteredStats.wins,
    losing_trades: filteredStats.losses, breakeven_trades: filteredStats.be,
    win_rate: filteredStats.winRate, gross_profit: filteredStats.gp, gross_loss: filteredStats.gl,
    net_profit: filteredStats.net, profit_factor: filteredStats.pf,
    avg_win: filteredStats.avgWin, avg_loss: filteredStats.avgLoss,
    expectancy: filteredStats.expectancy, largest_win: filteredStats.largestWin,
    largest_loss: filteredStats.largestLoss, avg_holding_seconds: filteredStats.avgHolding,
    initial_capital: capital, final_balance: filteredStats.balance,
    final_equity: filteredStats.balance, return_pct: (filteredStats.net / capital) * 100,
    max_drawdown_amount: filteredStats.maxDd, max_drawdown_pct: filteredStats.maxDdPct,
    avg_drawdown_amount: filteredStats.avgDd, current_drawdown_amount: filteredStats.currentDd,
    max_consecutive_wins: filteredStats.maxW, max_consecutive_losses: filteredStats.maxL,
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
    current_streak: m.current_streak ? `${m.current_streak.kind} × ${m.current_streak.count}` : '—',
    max_simultaneous: m.max_simultaneous_positions, max_planned_open_risk: m.max_planned_open_risk,
  } : null

  const equityPoints = useMemo(() => {
    if (isFilteredActive) {
      const sorted = [...filteredTrades].sort((a, b) => a.exit_ts - b.exit_ts)
      let cum = capital
      const pts = [{ x: sorted[0]?.entry_ts ?? 0, y: capital, title: `start · equity ${fmt(capital, 0)}` }]
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
  }, [activeRun, filteredTrades, isFilteredActive, capital])

  const dayCheckboxRefs = useRef<Record<string, HTMLInputElement | null>>({})
  useEffect(() => {
    for (const d of WEEKDAYS) {
      const el = dayCheckboxRefs.current[d]
      if (!el) continue
      const kids = SESSION_ROWS.slice(1).map((s) => cellSel[`${d}|${s}`])
      el.indeterminate = kids.some(Boolean) && !kids.every(Boolean)
    }
  }, [cellSel])

  const inspectNode = nodes.find((n) => n.id === inspectId)
  const nodeScenarios = scenarios.filter((sc) => (sc.config?.node_ids ?? []).includes(inspectId))

  const saveScenario = async () => {
    if (!scName || inspectId === null) return
    const config = buildConfig({
      node_ids: [inspectId],
      description: scDesc,
      day_selections: daySel, session_selections: cellSel,
      source_run_id: activeRun?.id ?? null,
      node_version: inspectNode?.version ?? null,
      fingerprint: activeRun?.fingerprint ?? null,
      requested_range: activeRun?.metrics?.requested_range ?? null,
      actual_range: activeRun?.metrics?.actual_range ?? null,
      coverage_status: activeRun?.metrics?.coverage_status ?? null,
      data_quality: activeRun?.data_quality ?? null,
      assumptions: activeRun?.assumptions ?? null,
      filtered_metrics: { trades: filteredStats.trades, net: filteredStats.net,
                          win_rate: filteredStats.winRate, profit_factor: filteredStats.pf },
    })
    await api.deeptesting.createScenario(instrument, { name: scName, folder: `node-${inspectId}`, config })
    setModal(false); setScName(''); setScDesc('')
    api.deeptesting.listScenarios(instrument).then((r) => setScenarios(r.scenarios ?? []))
    setNotice('Scenario saved with a persistent ID — retrievable by future Live Testing via the scenario handoff reference.')
  }

  const openScenario = async (sc: any) => {
    if (sc.config?.day_selections) setDaySel(sc.config.day_selections)
    if (sc.config?.session_selections) setCellSel(sc.config.session_selections)
    if (sc.config?.source_run_id) await openNodeRun(inspectId!, sc.config.source_run_id)
    setNotice(`Scenario "${sc.name}" loaded — checkbox states restored exactly as saved (scenario #${sc.id}, deeptesting-scenario/v1).`)
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
            <legend>Global TP / SL offsets <span className="faint">(pips · individual node values override · strategy logic never replaced)</span></legend>
            {offInput('SL + (Away from Entry)', globalOff.slPlus, (v) => setGlobalOff({ ...globalOff, slPlus: v }), 'Global SL plus offset')}
            {offInput('SL − (Closer to Entry)', globalOff.slMinus, (v) => setGlobalOff({ ...globalOff, slMinus: v }), 'Global SL minus offset')}
            {offInput('TP + (Away from Entry)', globalOff.tpPlus, (v) => setGlobalOff({ ...globalOff, tpPlus: v }), 'Global TP plus offset')}
            {offInput('TP − (Closer to Entry)', globalOff.tpMinus, (v) => setGlobalOff({ ...globalOff, tpMinus: v }), 'Global TP minus offset')}
          </fieldset>
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
          <button type="button" className="btn btn-ghost btn-xs" disabled={busy}
                  onClick={() => runNodes(filteredRows.map((r) => r.node.id))}>Run all filtered</button>
        </div>

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
                return (
                  <tr key={n.id} className={inspectId === n.id ? 'row-active' : ''}>
                    <td><button type="button" className="btn btn-ghost btn-xs" aria-label={`${n.starred ? 'Unstar' : 'Star'} node ${n.id}`}
                                onClick={() => toggleStar(n)}>{n.starred ? '★' : '☆'}</button></td>
                    <td><input type="checkbox" aria-label={`Select node ${n.id}`} checked={selected.has(n.id)}
                               onChange={(e) => { const nx = new Set(selected); if (e.target.checked) nx.add(n.id); else nx.delete(n.id); setSelected(nx) }} /></td>
                    <td>{n.id}</td><td>{n.name}</td><td>{n.origin}</td><td>{instrument}</td><td>{n.timeframe ?? '—'}</td>
                    <td>{r.direction}</td>
                    <td><span className={`chip chip-${r.status.replace(' ', '-').toLowerCase()}`}>{r.status}</span></td>
                    <td><input className="input input-xs num dt-off-in" type="number" step="any" aria-label={`SL plus node ${n.id}`} value={off.slPlus} onChange={(e) => setOff({ slPlus: e.target.value })} /></td>
                    <td><input className="input input-xs num dt-off-in" type="number" step="any" aria-label={`SL minus node ${n.id}`} value={off.slMinus} onChange={(e) => setOff({ slMinus: e.target.value })} /></td>
                    <td><input className="input input-xs num dt-off-in" type="number" step="any" aria-label={`TP plus node ${n.id}`} value={off.tpPlus} onChange={(e) => setOff({ tpPlus: e.target.value })} /></td>
                    <td><input className="input input-xs num dt-off-in" type="number" step="any" aria-label={`TP minus node ${n.id}`} value={off.tpMinus} onChange={(e) => setOff({ tpMinus: e.target.value })} /></td>
                    <td className="faint micro">SL: {offsetSource(n.id, 'sl')}<br />TP: {offsetSource(n.id, 'tp')}</td>
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
              {pageRows.length === 0 && <tr><td colSpan={26} className="faint">No nodes match the active filters.</td></tr>}
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

          {activeRun && stats && (
            <>
              <p className="faint">
                Run #{activeRun.id} · <strong>{testingStatus(activeRun)}</strong>
                · requested {tsFmt(activeRun.metrics?.requested_range?.start)} → {tsFmt(activeRun.metrics?.requested_range?.end)}
                · actual {tsFmt(activeRun.metrics?.actual_range?.start)} → {tsFmt(activeRun.metrics?.actual_range?.end)}
                · candles tested {fmt(activeRun.metrics?.candles_tested, 0)}
                · readiness {activeRun.data_quality?.readiness}
                {(activeRun.data_quality?.warnings ?? []).length > 0 && <> · warnings: {(activeRun.data_quality?.warnings ?? []).join('; ')}</>}
                · effective offsets SL {activeRun.assumptions?.effective_offsets?.sl?.offset_pips ?? 0} pips ({activeRun.assumptions?.effective_offsets?.sl?.source ?? 'strategy'}) / TP {activeRun.assumptions?.effective_offsets?.tp?.offset_pips ?? 0} pips ({activeRun.assumptions?.effective_offsets?.tp?.source ?? 'strategy'})
                {(activeRun.assumptions?.effective_offsets?.warnings ?? []).map((w: string) => <span key={w} className="field-error"> {w}</span>)}
                {isFilteredActive
                  ? <span className="chip chip-partial-tested"> FILTERED VIEW — statistics below are recalculated from the {filteredTrades.length} matching trades; the original run is untouched</span>
                  : <span className="chip"> ORIGINAL full DeepTesting result</span>}
              </p>

              {/* complete statistics — ONE compact table, stats first */}
              <table className="node-table" aria-label="Complete statistics">
                <tbody>
                  <tr><th colSpan={4}>Trading statistics {isFilteredActive ? '(filtered)' : ''}</th></tr>
                  <tr><td>Total trades</td><td>{stats.total_trades}</td><td>Winning / Losing / Breakeven</td><td>{stats.winning_trades} / {stats.losing_trades} / {stats.breakeven_trades}</td></tr>
                  <tr><td>Win rate / Loss rate</td><td>{stats.win_rate != null ? `${fmt(stats.win_rate * 100, 1)}%` : 'N/A'} / {stats.win_rate != null ? `${fmt((1 - stats.win_rate) * 100, 1)}%` : 'N/A'}</td><td>Gross profit / loss</td><td>{fmt(stats.gross_profit)} / {fmt(stats.gross_loss)}</td></tr>
                  <tr><td>Net profit/loss</td><td>{fmt(stats.net_profit)}</td><td>Profit factor</td><td>{fmt(stats.profit_factor, 3)}</td></tr>
                  <tr><td>Avg win / loss</td><td>{fmt(stats.avg_win)} / {fmt(stats.avg_loss)}</td><td>Expectancy</td><td>{fmt(stats.expectancy)}</td></tr>
                  <tr><td>Largest win / loss</td><td>{fmt(stats.largest_win)} / {fmt(stats.largest_loss)}</td><td>Avg holding</td><td>{fmt((stats.avg_holding_seconds ?? 0) / 3600, 1)}h</td></tr>
                  <tr><th colSpan={4}>Account statistics</th></tr>
                  <tr><td>Initial capital</td><td>{fmt(stats.initial_capital, 0)} {currency}</td><td>Balance / Equity</td><td>{fmt(stats.final_balance)} / {fmt(stats.final_equity)}</td></tr>
                  <tr><td>Total return</td><td>{fmt(stats.net_profit)} ({fmt(stats.return_pct)}%)</td><td>Unrealized P/L</td><td>{isFilteredActive ? 'N/A (filter view)' : fmt(m?.unrealized_at_end)}</td></tr>
                  <tr><th colSpan={4}>Risk statistics</th></tr>
                  <tr><td>Max drawdown</td><td>{fmt(stats.max_drawdown_amount)} ({fmt(stats.max_drawdown_pct)}%)</td><td>Average drawdown</td><td>{fmt(stats.avg_drawdown_amount)}</td></tr>
                  <tr><td>Current drawdown</td><td>{fmt(stats.current_drawdown_amount)} ({fmt(m?.current_drawdown_pct)}%)</td><td>Recovery factor</td><td>{isFilteredActive ? 'N/A (filter view)' : fmt(m?.recovery_factor, 3)}</td></tr>
                  <tr><td>DD duration</td><td>{isFilteredActive ? 'N/A (filter view)' : `${fmt((m?.max_drawdown_duration_seconds ?? 0) / 86400, 1)}d`}</td><td>Max simultaneous / planned open risk</td><td>{stats.max_simultaneous} / {fmt(stats.max_planned_open_risk)}</td></tr>
                  <tr><th colSpan={4}>Streak statistics</th></tr>
                  <tr><td>Max consec. W / L</td><td>{stats.max_consecutive_wins} / {stats.max_consecutive_losses}</td><td>Current streak</td><td>{stats.current_streak}</td></tr>
                  <tr><td>Avg W / L streak</td><td>{isFilteredActive ? 'N/A (filter view)' : `${fmt(m?.streaks?.avg_winning_streak)} / ${fmt(m?.streaks?.avg_losing_streak)}`}</td><td>Long / Short net</td><td>{isFilteredActive ? 'N/A (filter view)' : `${fmt(m?.long_net)} / ${fmt(m?.short_net)}`}</td></tr>
                </tbody>
              </table>

              {/* weekday/session filter table */}
              <div className="dt-table-scroll">
                <table className="node-table dt-session-grid" aria-label="Weekday and session filters">
                  <thead><tr><th>Session</th>{WEEKDAYS.map((d) => (
                    <th key={d}>
                      <label className="check small"><input type="checkbox"
                        ref={(el) => { dayCheckboxRefs.current[d] = el }}
                        checked={daySel[d]} aria-label={`Include ${d}`}
                        onChange={(e) => {
                          const v = e.target.checked
                          setDaySel({ ...daySel, [d]: v })
                          setCellSel({ ...cellSel, ...Object.fromEntries(SESSION_ROWS.slice(1).map((s) => [`${d}|${s}`, v])) })
                        }} /> {d}</label>
                    </th>))}
                  </tr></thead>
                  <tbody>
                    {SESSION_ROWS.map((session) => (
                      <tr key={session}>
                        <th>{session}</th>
                        {WEEKDAYS.map((d) => {
                          const list = session === 'TOTAL DAY'
                            ? trades.filter((t) => WEEKDAYS[new Date(t.entry_ts * 1000).getUTCDay() - 1] === d)
                            : trades.filter((t) => cellKey(t) === `${d}|${session}`)
                          const st = cellStats(list)
                          const key = `${d}|${session}`
                          return (
                            <td key={key} className={session !== 'TOTAL DAY' && !cellSel[key] ? 'dt-cell-off' : ''}>
                              {session !== 'TOTAL DAY' && (
                                <label className="check small"><input type="checkbox" checked={cellSel[key]}
                                  aria-label={`Include ${d} ${session}`}
                                  onChange={(e) => setCellSel({ ...cellSel, [key]: e.target.checked })} /> </label>
                              )}
                              <span className="dt-cell-stats">
                                {st.trades} trades · {st.wins}W/{st.losses}L/{st.be}BE<br />
                                WR {st.winRate != null ? `${fmt(st.winRate * 100, 0)}%` : 'N/A'} · PF {fmt(st.pf, 2)}<br />
                                GP {fmt(st.gp, 0)} · GL {fmt(st.gl, 0)} · <strong>Net {fmt(st.net, 0)}</strong>
                              </span>
                            </td>
                          )
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="faint micro">Sessions are single-label UTC entry-time cells (ASIA 00–08 &amp; 22–24 · ASIA-LONDON 08–09 · LONDON 09–13 · LONDON-NY 13–17 · NY 17–22) so cells never double-count; totals always reconcile with unique trades. Unchecking a weekday excludes all its sessions; partial selection shows the weekday checkbox as indeterminate. Statistics and the equity curve recalculate live from the matching trades.</p>
              <div className="dt-toolbar">
                <button type="button" className="btn btn-ghost btn-xs" onClick={() => {
                  setDaySel(Object.fromEntries(WEEKDAYS.map((d) => [d, true])))
                  setCellSel(Object.fromEntries(WEEKDAYS.flatMap((d) => SESSION_ROWS.slice(1).map((s) => [`${d}|${s}`, true]))))
                }}>Select all days/sessions</button>
                <button type="button" className="btn btn-ghost btn-xs" onClick={() => {
                  setDaySel(Object.fromEntries(WEEKDAYS.map((d) => [d, false])))
                  setCellSel(Object.fromEntries(WEEKDAYS.flatMap((d) => SESSION_ROWS.slice(1).map((s) => [`${d}|${s}`, false]))))
                }}>Deselect all</button>
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
                {nodeScenarios.length > 0 && <span className="faint micro">Saved scenarios keep a persistent ID (deeptesting-scenario/v1) for the future Live Testing handoff.</span>}
              </div>

              {/* ONE equity curve */}
              <EquityCurve points={equityPoints} capital={capital}
                label={isFilteredActive ? 'Equity curve (filtered trades, cumulative)' : 'Equity curve (engine, incl. unrealized)'} />
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
              <p><strong>Capital / Risk / Max active:</strong> {fmt(capital, 0)} {currency} · {riskMode} {riskValue} ({riskBasis}) · {maxActive}</p>
              <p><strong>Range:</strong> last {duration} days · requested {tsFmt(activeRun?.metrics?.requested_range?.start)} → {tsFmt(activeRun?.metrics?.requested_range?.end)} · actual {tsFmt(activeRun?.metrics?.actual_range?.start)} → {tsFmt(activeRun?.metrics?.actual_range?.end)} ({activeRun?.metrics?.coverage_status})</p>
              <p><strong>Offsets:</strong> global SL +{globalOff.slPlus || 0}/−{globalOff.slMinus || 0} · TP +{globalOff.tpPlus || 0}/−{globalOff.tpMinus || 0} pips · node #{inspectId} SL +{nodeOffsets[inspectId ?? -1]?.slPlus || 0}/−{nodeOffsets[inspectId ?? -1]?.slMinus || 0} · TP +{nodeOffsets[inspectId ?? -1]?.tpPlus || 0}/−{nodeOffsets[inspectId ?? -1]?.tpMinus || 0} pips</p>
              <p><strong>Days/Sessions:</strong> {WEEKDAYS.map((d) => `${d} ${daySel[d] ? '✓' : '✗'} (${SESSION_ROWS.slice(1).map((s) => `${s} ${cellSel[`${d}|${s}`] ? '✓' : '✗'}`).join(', ')})`).join(' · ')}</p>
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
