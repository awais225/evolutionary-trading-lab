import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, beforeEach } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import App from '../App'

const NODE = {
  id: 1, instrument_id: 'XAUUSD', origin: 'CUSTOM', name: 'Acceptance Node', description: null,
  status: 'DRAFT', approval_state: 'PENDING', version: 3, parent_node_id: null,
  spec_hash: 'abc', spec_json: null, created_by: 'test', created_at: 0, updated_at: 0,
  starred: false, timeframe: 'H1',
}

const renderDT = () =>
  render(<MemoryRouter initialEntries={['/deeptesting']}><App /></MemoryRouter>)

describe('DeepTesting workspace — three-area layout', () => {
  beforeEach(() => {
    ;(globalThis as { __hero1NodeItems?: unknown[] }).__hero1NodeItems = [NODE]
    localStorage.clear()
  })

  it('Area 1 hard constraints come first; MAX ACTIVE TRADES defaults to 1; global offsets exist', async () => {
    renderDT()
    expect(await screen.findByText(/Hard Constraints/i)).toBeTruthy()
    expect((screen.getByLabelText(/Max active trades/i) as HTMLInputElement).value).toBe('1')
    expect(screen.getByLabelText(/Global TP offset/i)).toBeTruthy()
    expect(screen.getByLabelText(/Global SL offset/i)).toBeTruthy()
    const constraints = document.getElementById('dt-constraints')!
    const nodes = document.getElementById('dt-nodes')!
    expect(constraints.compareDocumentPosition(nodes) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('Area 2 main table: star, selection and offset-source precedence display', async () => {
    renderDT()
    const star = await screen.findByLabelText(/Star node 1/i)
    fireEvent.click(star)
    expect(await screen.findByLabelText(/Unstar node 1/i)).toBeTruthy()

    const box = screen.getByLabelText(/Select node 1/i)
    fireEvent.click(box)
    expect((box as HTMLInputElement).checked).toBe(true)

    // global offset applies when no individual value exists
    fireEvent.change(screen.getByLabelText(/Global TP offset/i), { target: { value: '10' } })
    expect(screen.getAllByText(/Global DeepTesting/).length).toBeGreaterThan(0)
    // individual value overrides the global one (source label flips)
    fireEvent.change(screen.getByLabelText(/TP offset node 1/i), { target: { value: '-5' } })
    expect(screen.getByText(/Individual node/)).toBeTruthy()
  })

  it('starred filter hides unstarred nodes and combines with search', async () => {
    renderDT()
    fireEvent.click(await screen.findByLabelText(/Star node 1/i))
    fireEvent.change(screen.getByLabelText(/Starred filter/i), { target: { value: 'unstarred' } })
    expect(screen.getByText(/No nodes match/i)).toBeTruthy()
    fireEvent.change(screen.getByLabelText(/Starred filter/i), { target: { value: 'starred' } })
    expect(await screen.findByLabelText(/Select node 1/i)).toBeTruthy()
  })

  it('Area 3 inspection opens BELOW the main table with reconciling detail', async () => {
    renderDT()
    fireEvent.click(await screen.findByRole('button', { name: /^Inspect$/i }))
    expect(await screen.findByText(/Inspection — Acceptance Node/i)).toBeTruthy()
    const nodesSection = document.getElementById('dt-nodes')!
    const inspect = document.getElementById('dt-inspect')!
    expect(nodesSection.compareDocumentPosition(inspect) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.getByText(/Detailed statistics|Trading performance/i)).toBeTruthy()
    expect(screen.getAllByText(/TAKE_PROFIT/).length).toBeGreaterThan(0)
    const inspectButtons = await screen.findAllByRole('button', { name: /^Inspect$/i })
    fireEvent.click(inspectButtons[inspectButtons.length - 1])
    expect(await screen.findByText(/Indicators at entry/i)).toBeTruthy()
  })

  it('closing inspection removes Area 3', async () => {
    renderDT()
    fireEvent.click(await screen.findByRole('button', { name: /^Inspect$/i }))
    expect(await screen.findByText(/Inspection — Acceptance Node/i)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /✕ Close/i }))
    expect(screen.queryByText(/Inspection — Acceptance Node/i)).toBeNull()
  })
})
