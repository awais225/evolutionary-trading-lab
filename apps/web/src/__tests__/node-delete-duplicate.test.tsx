import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import App from '../App'

const renderAt = (path: string) => render(<MemoryRouter initialEntries={[path]}><App /></MemoryRouter>)

const specJson = (direction: string, timeframe: string) => JSON.stringify({
  schema_version: '1.0.0',
  identity: { name: 'n', instrument: 'XAUUSD', timeframe, direction, origin: 'CUSTOM' },
})

const base = {
  instrument_id: 'XAUUSD', origin: 'CUSTOM', description: null, status: 'DRAFT',
  approval_state: 'PENDING', version: 1, spec_hash: 'abc123def456',
  created_by: 'test', created_at: 0, updated_at: 0, starred: false,
}

const NODES = [
  { ...base, id: 1, name: 'C1', parent_node_id: null, timeframe: 'M15', spec_json: specJson('LONG', 'M15') },
  { ...base, id: 2, name: 'C2', parent_node_id: 1, timeframe: 'H1', version: 2, spec_json: specJson('SHORT', 'H1') },
  { ...base, id: 3, name: 'C3', parent_node_id: null, timeframe: null, spec_json: null, spec_hash: null },
]

describe('Manual Nodes — delete & Duplicate & Edit (ISSUE-054)', () => {
  beforeEach(() => {
    ;(globalThis as { __hero1NodeItems?: unknown }).__hero1NodeItems = NODES.map((n) => ({ ...n }))
    window.confirm = () => true
  })
  afterEach(() => {
    delete (globalThis as { __hero1NodeItems?: unknown }).__hero1NodeItems
    delete (globalThis as { __hero1Conflicts?: unknown }).__hero1Conflicts
  })

  it('deletes a single node after confirmation', async () => {
    renderAt('/manual-nodes')
    fireEvent.click(await screen.findByTitle('Delete node #3'))
    expect(await screen.findByRole('dialog')).toHaveTextContent(/will be permanently deleted/)
    fireEvent.click(screen.getByRole('button', { name: /^delete node$/i }))
    expect(await screen.findByText(/Deleted node\(s\): #3/)).toBeInTheDocument()
  })

  it('cancel leaves the node untouched', async () => {
    renderAt('/manual-nodes')
    fireEvent.click(await screen.findByTitle('Delete node #3'))
    fireEvent.click(await screen.findByRole('button', { name: /^cancel$/i }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByText('C3')).toBeInTheDocument()
  })

  it('descendants require explicit subtree opt-in', async () => {
    renderAt('/manual-nodes')
    fireEvent.click(await screen.findByTitle('Delete node #1'))
    expect(await screen.findByText(/descendant\(s\)/)).toBeInTheDocument()
    fireEvent.click(screen.getByLabelText(/also delete the descendant subtree/i))
    fireEvent.click(screen.getByRole('button', { name: /^delete node$/i }))
    expect(await screen.findByText(/Deleted node\(s\): #1, #2|Deleted node\(s\): #2, #1/)).toBeInTheDocument()
  })

  it('bulk delete via selection reports the selected count', async () => {
    renderAt('/manual-nodes')
    fireEvent.click(await screen.findByLabelText('Select node 3'))
    fireEvent.click(await screen.findByRole('button', { name: /delete selected \(1\)/i }))
    expect(await screen.findByRole('dialog')).toHaveTextContent(/Delete 1 selected nodes?|Delete node?/)
    fireEvent.click(screen.getByRole('button', { name: /delete 1 nodes|delete node/i }))
    expect(await screen.findByText(/Deleted node\(s\): #3/)).toBeInTheDocument()
  })

  it('Duplicate & Edit saves an independent variant and keeps the original', async () => {
    renderAt('/manual-nodes')
    const dupButtons = await screen.findAllByRole('button', { name: /duplicate & edit/i })
    fireEvent.click(dupButtons[0])
    expect(await screen.findByText(/new variant based on/i)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /save variant/i }))
    expect(await screen.findByText(/Saved variant #999/)).toBeInTheDocument()
    expect(screen.getByText(/original #\d+ unchanged/)).toBeInTheDocument()
  })

  it('conflicts panel shows stored vs incoming and resolves explicitly', async () => {
    ;(globalThis as { __hero1Conflicts?: unknown }).__hero1Conflicts = [{
      id: 7, operation_id: 'FETCH-TEST', instrument: 'XAUUSD', timeframe: 'M5',
      ts: 1792153500, ts_iso: '2026-10-16 12:25:00', stored_dataset_id: 'ds1',
      stored_source: 'CSV import: XAUUSD_M5.csv', incoming_source: 'MT5 copy_rates_range (ICMarketsSC-Demo)',
      stored: { open: 4177.94, high: 4178.34, low: 4175.46, close: 4176.15, tick_volume: 2026, volume: 0, spread: 5 },
      incoming: { open: 4177.90, high: 4178.30, low: 4175.40, close: 4176.10, tick_volume: 2031, volume: 0, spread: 5 },
      differing_fields: { close: { stored: 4176.15, incoming: 4176.10 } },
      classification: 'CONFLICT_STORED_PRESERVED', resolution: 'UNRESOLVED',
      resolution_note: null, resolved_at: null, created_at: '2026-11-07T13:54:58Z',
    }]
    renderAt('/data')
    expect(await screen.findByText(/Fetch conflicts — unresolved \(1\)/)).toBeInTheDocument()
    expect(screen.getByText(/CSV import: XAUUSD_M5\.csv/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /keep stored/i }))
    await waitFor(() => expect(screen.getByText(/resolved: stored candle kept/)).toBeInTheDocument())
  })
})
