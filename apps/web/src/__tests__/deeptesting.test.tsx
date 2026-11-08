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

describe('DeepTesting refined layout', () => {
  beforeEach(() => {
    ;(globalThis as { __hero1NodeItems?: unknown[] }).__hero1NodeItems = [NODE]
    localStorage.clear()
  })

  it('defaults: capital 200,000, risk 1%, MAX ACTIVE 1; four global pips offsets', async () => {
    renderDT()
    expect(await screen.findByText(/Hard Constraints/i)).toBeTruthy()
    expect((screen.getByLabelText(/Initial capital/i) as HTMLInputElement).value).toBe('200000')
    expect((screen.getByLabelText(/Risk per trade/i) as HTMLInputElement).value).toBe('1')
    expect((screen.getByLabelText(/Max active trades/i) as HTMLInputElement).value).toBe('1')
    for (const label of [/Global SL plus/i, /Global SL minus/i, /Global TP plus/i, /Global TP minus/i]) {
      expect(screen.getByLabelText(label)).toBeTruthy()
    }
    expect(screen.getAllByText('pips').length).toBeGreaterThan(3)
  })

  it('shows all eight range buttons, highlights selection, no Test Configuration or Batch Scope', async () => {
    renderDT()
    for (const label of ['1 Week', '2 Weeks', '1 Month', '3 Months', '6 Months', '1 Year', '2 Years', '3 Years']) {
      expect(await screen.findByRole('button', { name: label })).toBeTruthy()
    }
    const twoYears = screen.getByRole('button', { name: '2 Years' })
    fireEvent.click(twoYears)
    expect(twoYears.getAttribute('aria-pressed')).toBe('true')
    expect(screen.queryByText(/Test Configuration/i)).toBeNull()
    expect(screen.queryByText(/Batch Node Scope/i)).toBeNull()
  })

  it('node table exposes four individual offset inputs and offset sources', async () => {
    renderDT()
    expect(await screen.findByLabelText(/SL plus node 1/i)).toBeTruthy()
    for (const label of [/SL minus node 1/i, /TP plus node 1/i, /TP minus node 1/i]) {
      expect(screen.getByLabelText(label)).toBeTruthy()
    }
    expect(screen.getByText(/TESTED/)).toBeTruthy() // latest mock run is DONE + TESTED
    fireEvent.change(screen.getByLabelText(/TP plus node 1/i), { target: { value: '50' } })
    expect(screen.getAllByText(/Individual node/).length).toBeGreaterThan(0)
  })

  it('inspection: stats first, weekday/session grid, live filtered view, ONE equity curve', async () => {
    renderDT()
    fireEvent.click(await screen.findByRole('button', { name: /^Inspect$/i }))
    expect(await screen.findByText(/Inspection — Acceptance Node/i)).toBeTruthy()
    expect(screen.getByLabelText(/Complete statistics/i)).toBeTruthy()
    expect(screen.getByLabelText(/Weekday and session filters/i)).toBeTruthy()
    expect(screen.getByLabelText('Include Monday')).toBeTruthy()
    expect(screen.getByLabelText(/Include Monday ASIA-LONDON/i)).toBeTruthy()
    // unchecking Monday's ASIA session switches to a clearly-marked filtered view
    fireEvent.click(screen.getByLabelText(/Include Monday ASIA-LONDON/i))
    expect(await screen.findByText(/FILTERED VIEW/)).toBeTruthy()
    expect(screen.getByRole('button', { name: /SAVE FILTERS AS SCENARIO/i })).toBeTruthy()
    expect(screen.getByLabelText(/Equity curve/)).toBeTruthy()
  })

  it('scenario modal summarizes selections and saves', async () => {
    renderDT()
    fireEvent.click(await screen.findByRole('button', { name: /^Inspect$/i }))
    await screen.findByText(/Inspection — Acceptance Node/i)
    fireEvent.click(screen.getByRole('button', { name: /SAVE FILTERS AS SCENARIO/i }))
    expect(await screen.findByRole('dialog')).toBeTruthy()
    fireEvent.change(screen.getByLabelText(/Scenario name/i), { target: { value: 'London weekdays' } })
    expect(screen.getByText(/Days\/Sessions:/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Save Scenario/i }))
    expect(await screen.findByText(/persistent ID/)).toBeTruthy()
  })
})
