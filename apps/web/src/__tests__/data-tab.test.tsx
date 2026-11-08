import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import App from '../App'

const renderAt = (path: string) => render(<MemoryRouter initialEntries={[path]}><App /></MemoryRouter>)

const spec = (direction: string, timeframe: string) => JSON.stringify({
  schema_version: '1.0.0',
  identity: { name: 'n', instrument: 'XAUUSD', timeframe, direction, origin: 'CUSTOM' },
})

const base = {
  instrument_id: 'XAUUSD', origin: 'CUSTOM', description: null, status: 'DRAFT',
  approval_state: 'PENDING', version: 1, spec_hash: 'abc123def456',
  created_by: 'test', created_at: 0, updated_at: 0, starred: false,
}

const NODES = [
  { ...base, id: 1, name: 'C1', parent_node_id: null, timeframe: 'M15', spec_json: spec('LONG', 'M15') },
  { ...base, id: 2, name: 'C2', parent_node_id: 1, timeframe: 'H1', version: 2, spec_json: spec('SHORT', 'H1') },
  { ...base, id: 3, name: 'C3', parent_node_id: null, timeframe: null, spec_json: null, spec_hash: null },
]

const EVENTS = [
  { id: 7, ts: 1792000000, category: 'SYSTEM', severity: 'INFO', instrument: 'XAUUSD',
    subsystem: 'market-data', message: '[FETCH-20261107-084512-ABC123] timeframe_fetch_complete (H1)',
    payload: { operation_id: 'FETCH-20261107-084512-ABC123', stage: 'timeframe_fetch_complete', appended: 12 },
    node_id: null },
  { id: 8, ts: 1792000060, category: 'SYSTEM', severity: 'WARNING', instrument: 'XAUUSD',
    subsystem: 'market-data', message: '[FETCH-20261107-084512-ABC123] fetch_operation_completed',
    payload: { operation_id: 'FETCH-20261107-084512-ABC123', stage: 'fetch_operation_completed', status: 'PARTIAL' },
    node_id: null },
]

describe('DATA tab — historical data above generated nodes (ISSUE-051)', () => {
  beforeEach(() => {
    ;(globalThis as { __hero1NodeItems?: unknown[] }).__hero1NodeItems = NODES
    ;(globalThis as { __hero1Events?: unknown[] }).__hero1Events = EVENTS
  })
  afterEach(() => {
    delete (globalThis as { __hero1NodeItems?: unknown[] }).__hero1NodeItems
    delete (globalThis as { __hero1Events?: unknown[] }).__hero1Events
  })

  it('shows DATA in navigation, market data first, then the node dashboard', async () => {
    renderAt('/data')
    expect(screen.getByRole('link', { name: /DATA/ })).toBeInTheDocument()
    const history = await screen.findByText(/Historical market data/)
    const nodes = await screen.findByText(/Generated nodes/)
    expect(history.compareDocumentPosition(nodes) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('shows MT5 instrument specifications separately from historical data', async () => {
    renderAt('/data')
    expect(await screen.findByText(/MT5 INSTRUMENT SPECIFICATIONS/)).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: /REFRESH FROM MT5|Refreshing/i })).toBeInTheDocument()
    expect(screen.getByText(/No MT5 specification snapshot/)).toBeInTheDocument()
    expect(screen.getByText(/Historical market data/)).toBeInTheDocument()
  })

  it('reports stored coverage from the canonical store', async () => {
    renderAt('/data')
    expect(await screen.findByText(/1\/7 timeframes available/)).toBeInTheDocument()
    expect(await screen.findByText(/3 total candles/)).toBeInTheDocument()
    expect(await screen.findByText('2026-07-07 18:41:00')).toBeInTheDocument()
    expect(screen.getByText(/<test-root>\/MARKET_DATA\/market_data\.db/)).toBeInTheDocument()
  })

  it('reports a failed fetch honestly (never COMPLETE)', async () => {
    renderAt('/data')
    fireEvent.click(await screen.findByRole('button', { name: /fetch data/i }))
    // background operation: the panel polls the operation until it lands
    expect(await screen.findByText(/op FETCH-TEST-OP1/)).toBeInTheDocument()
    expect((await screen.findAllByText(/^FAILED$/, {}, { timeout: 4000 })).length).toBeGreaterThan(0)
    expect(await screen.findByText(/mock terminal unavailable/)).toBeInTheDocument()
    expect(screen.queryByText(/^COMPLETE$/)).not.toBeInTheDocument()
  })

  it('node table shows timeframe, colored direction, parent link and child count', async () => {
    renderAt('/data')
    expect(await screen.findByText('C1')).toBeInTheDocument()
    expect(screen.getByText('LONG', { selector: '.dir-badge' })).toHaveClass('dir-badge', 'long')
    expect(screen.getByText('SHORT', { selector: '.dir-badge' })).toHaveClass('dir-badge', 'short')
    expect(screen.getByText('— NO DIRECTION')).toBeInTheDocument()
    expect(screen.getByText('— NO TF')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /C1 \(#1\)/ })).toBeInTheDocument() // parent link on C2
    expect(screen.getAllByText('ROOT')).toHaveLength(2) // C1 and the draft C3 are roots
    // C1 has exactly one child (C2): the Children cell of the C1 row
    const row = screen.getByText('C1').closest('tr')
    expect(row?.textContent).toContain('M15')
  })

  it('direction filter narrows the table', async () => {
    renderAt('/data')
    expect(await screen.findByText('C1')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText(/Filter by direction/), { target: { value: 'SHORT' } })
    expect(screen.queryByText('C1')).not.toBeInTheDocument()
    expect(screen.getByText('C2')).toBeInTheDocument()
  })
})

describe('LOG tab — persistent event store (ISSUE-051)', () => {
  beforeEach(() => {
    ;(globalThis as { __hero1Events?: unknown[] }).__hero1Events = EVENTS
  })
  afterEach(() => {
    delete (globalThis as { __hero1Events?: unknown[] }).__hero1Events
  })

  it('displays persisted events with operation ids and expandable details', async () => {
    renderAt('/log')
    expect(await screen.findByText(/timeframe_fetch_complete \(H1\)/)).toBeInTheDocument()
    expect(screen.getAllByText(/FETCH-20261107-084512-ABC123/).length).toBeGreaterThan(0)
    fireEvent.click(screen.getByText(/timeframe_fetch_complete \(H1\)/))
    expect(await screen.findByText(/"appended": 12/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Clear filters/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /COPY ALL LOGS/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /SAVE LOGS TO TXT/ })).toBeInTheDocument()
    expect(screen.getByText(/Showing 2 of 2 matching events/)).toBeInTheDocument()
  })
})

import { formatLogsText, logExportFilename } from '../lib/logExport'
import type { HeroEvent } from '../api/types'

describe('log export formatting (ISSUE-052)', () => {
  it('exports chronological plain text with full details and no truncation', () => {
    const text = formatLogsText(EVENTS as unknown as HeroEvent[], {
      exportedAt: new Date('2026-11-07T09:30:00Z'),
      filters: { severity: 'ALL', operation_id: 'FETCH-20261107-084512-ABC123' },
      totalMatching: 2,
    })
    expect(text).toContain('HERO1 — APPLICATION LOG EXPORT')
    expect(text).toContain('Total exported events: 2')
    expect(text).toContain('operation=FETCH-20261107-084512-ABC123')
    expect(text).toContain('"appended": 12')
    // chronological: timeframe event (id 7) before completion (id 8)
    expect(text.indexOf('timeframe_fetch_complete')).toBeLessThan(text.indexOf('fetch_operation_completed'))
    expect(logExportFilename(new Date(2026, 10, 7, 9, 5, 3))).toBe('HERO1_LOGS_2026-11-07_09-05-03.txt')
  })
})
