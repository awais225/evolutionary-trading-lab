import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, beforeEach } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import App from '../App'

const renderDT = () =>
  render(<MemoryRouter initialEntries={['/deeptesting']}><App /></MemoryRouter>)

const NODE = {
  id: 1, instrument_id: 'XAUUSD', origin: 'CUSTOM', name: 'Acceptance Node', description: null,
  status: 'DRAFT', approval_state: 'PENDING', version: 3, parent_node_id: null,
  spec_hash: 'abc', spec_json: null, created_by: 'test', created_at: 0, updated_at: 0,
  starred: false, timeframe: 'H1',
}

describe('DeepTesting workspace (Phase 3)', () => {
  beforeEach(() => {
    ;(globalThis as { __hero1NodeItems?: unknown[] }).__hero1NodeItems = [NODE]
  })
  it('puts hard constraints first with MAX ACTIVE TRADES defaulting to 1', async () => {
    renderDT()
    expect(await screen.findByText(/Hard Constraints/i)).toBeTruthy()
    const maxActive = screen.getByLabelText(/Max active trades/i) as HTMLInputElement
    expect(maxActive.value).toBe('1')
    const capital = screen.getByLabelText(/Initial capital/i) as HTMLInputElement
    expect(capital.value).toBe('10000')
    // constraint section precedes node selection in the DOM
    const constraints = document.getElementById('dt-constraints')!
    const nodes = document.getElementById('dt-nodes')!
    expect(constraints.compareDocumentPosition(nodes) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('selects nodes by identity and preserves selection across filters', async () => {
    renderDT()
    const box = await screen.findByLabelText(/Select node 1/i)
    fireEvent.click(box)
    expect((box as HTMLInputElement).checked).toBe(true)
    fireEvent.change(screen.getByLabelText(/Search nodes/i), { target: { value: 'zzz-no-match' } })
    expect(screen.getByText(/No nodes match/i)).toBeTruthy()
    fireEvent.change(screen.getByLabelText(/Search nodes/i), { target: { value: '' } })
    expect((await screen.findByLabelText(/Select node 1/i) as HTMLInputElement).checked).toBe(true)
  })

  it('opens a persisted run with reconciling metrics and trade ledger', async () => {
    renderDT()
    fireEvent.click(await screen.findByRole('button', { name: /^Open$/i }))
    expect(await screen.findByText(/Run #1/i)).toBeTruthy()
    expect(screen.getAllByText(/TAKE_PROFIT/).length).toBeGreaterThan(0)
    fireEvent.click(screen.getByRole('button', { name: /Inspect/i }))
    expect(await screen.findByText(/Planned risk/)).toBeTruthy()
    expect(screen.getByText(/Indicators at entry/i)).toBeTruthy()
  })
})
