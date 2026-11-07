import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import App from '../App'
import {
  applySort, nextSortState, naturalCompare, loadSortPref, saveSortPref,
  type SortColumn,
} from '../lib/tableSort'

interface Row { name: string; metric: number | null; dd: number | null; ts: number | null }

const COLS: SortColumn<Row>[] = [
  { key: 'name', label: 'Name', type: 'natural', get: (r) => r.name },
  { key: 'metric', label: 'Metric', type: 'number', get: (r) => r.metric },
  { key: 'dd', label: 'Drawdown', type: 'absNumber', get: (r) => r.dd },
  { key: 'ts', label: 'When', type: 'date', get: (r) => r.ts },
]
const col = (k: string) => COLS.find((c) => c.key === k)!

describe('universal table sorting — comparators (ISSUE-055)', () => {
  it('numeric: first click defaults to descending, second reverses', () => {
    const rows: Row[] = [
      { name: 'a', metric: 5, dd: null, ts: null },
      { name: 'b', metric: -3, dd: null, ts: null },
      { name: 'c', metric: 12.5, dd: null, ts: null },
    ]
    const s1 = nextSortState(null, col('metric'))
    expect(s1).toEqual({ key: 'metric', dir: 'desc' })
    expect(applySort(rows, s1, COLS).map((r) => r.metric)).toEqual([12.5, 5, -3])
    const s2 = nextSortState(s1, col('metric'))
    expect(s2.dir).toBe('asc')
    expect(applySort(rows, s2, COLS).map((r) => r.metric)).toEqual([-3, 5, 12.5])
  })

  it('drawdown sorts by absolute magnitude (-500 before -100)', () => {
    const rows: Row[] = [
      { name: 'a', metric: null, dd: -100, ts: null },
      { name: 'b', metric: null, dd: -500, ts: null },
      { name: 'c', metric: null, dd: 50, ts: null },
    ]
    const s1 = nextSortState(null, col('dd'))
    expect(applySort(rows, s1, COLS).map((r) => r.dd)).toEqual([-500, -100, 50])
  })

  it('natural identifiers: C2 before C10', () => {
    expect(naturalCompare('C2', 'C10')).toBeLessThan(0)
    const rows = ['C10', 'C2', 'C1', 'C20'].map((name) => ({ name, metric: null, dd: null, ts: null }))
    const s1 = nextSortState(null, col('name'))
    expect(s1.dir).toBe('asc')
    expect(applySort(rows, s1, COLS).map((r) => r.name)).toEqual(['C1', 'C2', 'C10', 'C20'])
  })

  it('dates: newest first by default', () => {
    const rows: Row[] = [100, 300, 200].map((ts) => ({ name: 'x', metric: null, dd: null, ts }))
    const s1 = nextSortState(null, col('ts'))
    expect(applySort(rows, s1, COLS).map((r) => r.ts)).toEqual([300, 200, 100])
  })

  it('null / missing values are never zero and always sort last', () => {
    const rows: Row[] = [
      { name: 'a', metric: 3, dd: null, ts: null },
      { name: 'b', metric: null, dd: null, ts: null },
      { name: 'c', metric: 1, dd: null, ts: null },
    ]
    expect(applySort(rows, { key: 'metric', dir: 'desc' }, COLS).map((r) => r.metric)).toEqual([3, 1, null])
    expect(applySort(rows, { key: 'metric', dir: 'asc' }, COLS).map((r) => r.metric)).toEqual([1, 3, null])
  })

  it('stable: equal values keep their previous relative order', () => {
    const rows: Row[] = ['first', 'second', 'third'].map((name) => ({ name, metric: 7, dd: null, ts: null }))
    expect(applySort(rows, { key: 'metric', dir: 'desc' }, COLS).map((r) => r.name))
      .toEqual(['first', 'second', 'third'])
  })

  it('persistence roundtrip per table, isolated between tables', () => {
    localStorage.clear()
    saveSortPref('manual-nodes', { key: 'name', dir: 'asc' })
    saveSortPref('log', { key: 'ts', dir: 'asc' })
    expect(loadSortPref('manual-nodes', ['name', 'id'])).toEqual({ key: 'name', dir: 'asc' })
    expect(loadSortPref('log', ['ts'])).toEqual({ key: 'ts', dir: 'asc' })
    expect(loadSortPref('manual-nodes', ['id'])).toBeNull() // unknown key rejected
    localStorage.clear()
  })
})

const base = {
  instrument_id: 'XAUUSD', origin: 'CUSTOM', description: null, status: 'DRAFT',
  approval_state: 'PENDING', version: 1, spec_hash: 'abc123def456',
  created_by: 'test', created_at: 0, updated_at: 0, starred: false,
  timeframe: 'M15', parent_node_id: null,
  spec_json: JSON.stringify({ schema_version: '1.0.0', identity: { name: 'n', instrument: 'XAUUSD', timeframe: 'M15', direction: 'LONG', origin: 'CUSTOM' } }),
}
const NODES = [
  { ...base, id: 10, name: 'C10' },
  { ...base, id: 2, name: 'C2' },
  { ...base, id: 1, name: 'C1' },
]

function visibleNames(): string[] {
  return screen.getAllByRole('button', { name: /^C\d+$/ }).map((b) => b.textContent ?? '')
}

describe('Manual Nodes sorting UI + persistence', () => {
  beforeEach(() => {
    localStorage.clear()
    ;(globalThis as { __hero1NodeItems?: unknown }).__hero1NodeItems = NODES.map((n) => ({ ...n }))
    window.confirm = () => true
  })
  afterEach(() => {
    delete (globalThis as { __hero1NodeItems?: unknown }).__hero1NodeItems
    localStorage.clear()
  })

  it('sorts by Name naturally, toggles, and persists across remount', async () => {
    const view = render(<MemoryRouter initialEntries={['/manual-nodes']}><App /></MemoryRouter>)
    await screen.findByText('C10')
    fireEvent.click(screen.getByRole('button', { name: /^name/i }))
    expect(visibleNames()).toEqual(['C1', 'C2', 'C10'])
    fireEvent.click(screen.getByRole('button', { name: /^name/i }))
    expect(visibleNames()).toEqual(['C10', 'C2', 'C1'])
    expect(JSON.parse(localStorage.getItem('hero1.sort.manual-nodes') ?? '{}')).toEqual({ key: 'name', dir: 'desc' })
    view.unmount()
    render(<MemoryRouter initialEntries={['/manual-nodes']}><App /></MemoryRouter>)
    await screen.findByText('C10')
    expect(visibleNames()).toEqual(['C10', 'C2', 'C1']) // restored after remount
  })

  it('selection + bulk delete still target the right node after sorting', async () => {
    render(<MemoryRouter initialEntries={['/manual-nodes']}><App /></MemoryRouter>)
    await screen.findByText('C10')
    fireEvent.click(screen.getByRole('button', { name: /^name/i })) // C1, C2, C10
    const boxes = screen.getAllByLabelText(/^select node \d+$/i)
    fireEvent.click(boxes[0]) // first visible row is now C1 (#1)
    fireEvent.click(await screen.findByRole('button', { name: /delete selected \(1\)/i }))
    fireEvent.click(await screen.findByRole('button', { name: /delete 1 nodes|delete node/i }))
    expect(await screen.findByText(/Deleted node\(s\): #1/)).toBeInTheDocument()
  })
})
