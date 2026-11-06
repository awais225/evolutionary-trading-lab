import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import App from '../App'

const renderNodes = () =>
  render(<MemoryRouter initialEntries={['/manual-nodes']}><App /></MemoryRouter>)

const startBlank = async () => {
  fireEvent.click(await screen.findAllByRole('button', { name: /new blank strategy/i }).then((b) => b[0]))
}
const jsonTab = () => fireEvent.click(screen.getByRole('tab', { name: /json editor/i }))
const visualTab = () => fireEvent.click(screen.getByRole('tab', { name: /visual builder/i }))
const jsonText = () => (screen.getByLabelText(/specification json/i) as HTMLTextAreaElement).value

const crowdedRegistry = (n: number) =>
  Array.from({ length: n }, (_, i) => ({
    id: i + 1, instrument_id: 'XAUUSD', origin: 'CUSTOM', name: `C${i + 1}`, description: null,
    status: 'DRAFT', approval_state: 'PENDING', version: 1, parent_node_id: null,
    spec_hash: null, spec_json: null, created_by: 'test', created_at: 0, updated_at: 0,
  }))

const postCalls = () =>
  (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.filter(
    ([url, init]) => String(url).includes('/nodes') && (init as RequestInit)?.method === 'POST')

describe('Manual Nodes — visual strategy builder', () => {
  beforeEach(() => {
    delete (globalThis as { __hero1NodeItems?: unknown[] }).__hero1NodeItems
  })

  it('adds SMA / EMA / RSI / VWAP / ATR from the searchable library', async () => {
    renderNodes()
    await startBlank()
    for (const name of ['SMA', 'EMA', 'RSI', 'VWAP', 'ATR']) {
      fireEvent.click(screen.getByRole('button', { name: new RegExp(`^${name} `) }))
    }
    jsonTab()
    const j = jsonText()
    for (const name of ['"SMA"', '"EMA"', '"RSI"', '"VWAP"', '"ATR"']) expect(j).toContain(name)
  })

  it('filters the indicator library by search', async () => {
    renderNodes()
    await startBlank()
    fireEvent.change(screen.getByLabelText(/search indicators/i), { target: { value: 'relative' } })
    expect(screen.getByRole('button', { name: /^RSI / })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^SMA / })).not.toBeInTheDocument()
  })

  it('edits parameters with validation hints and keeps JSON valid', async () => {
    renderNodes()
    await startBlank()
    fireEvent.click(screen.getByRole('button', { name: /^SMA / }))
    const period = screen.getByLabelText(/^Period/)
    fireEvent.change(period, { target: { value: '50' } })
    jsonTab(); expect(jsonText()).toContain('"period": 50')
    visualTab()
    fireEvent.change(screen.getByLabelText(/^Period/), { target: { value: '999' } })
    expect(screen.getAllByText(/out of range \[2, 500\]/i).length).toBeGreaterThan(0)
  })

  it('changes the comparison operator and compares indicator to number', async () => {
    renderNodes()
    await startBlank()
    fireEvent.click(screen.getByRole('button', { name: /^RSI / }))
    fireEvent.change(screen.getAllByLabelText(/comparison operator/i)[1], { target: { value: 'lte' } })
    jsonTab()
    expect(jsonText()).toContain('"op": "lte"')
    expect(jsonText()).toContain('"kind": "indicator"')
    expect(jsonText()).toContain('"kind": "constant"')
  })

  it('compares indicator to indicator (EMA crossover shape)', async () => {
    renderNodes()
    await startBlank()
    fireEvent.click(screen.getByRole('button', { name: /^EMA / }))
    const kindSelects = screen.getAllByLabelText(/right operand type/i)
    fireEvent.change(kindSelects[1], { target: { value: 'indicator' } }) // EMA row (row 0 is the blank close>0)
    jsonTab()
    const spec = JSON.parse(jsonText())
    const first = spec.entry.children[0]   // blank had 1 (close>0)… find the EMA row
    const cmp = [first, spec.entry.children[1]].find((c: { left: { name?: string } }) => c.left?.name === 'EMA')
    expect(cmp.left.kind).toBe('indicator')
    expect(cmp.right.kind).toBe('indicator')
  })

  it('toggles AND/OR and duplicates/removes conditions', async () => {
    renderNodes()
    await startBlank() // one comparison to work with
    fireEvent.click(within(screen.getByRole('group', { name: /group logic/i })).getByRole('button', { name: 'OR' }))
    jsonTab(); expect(jsonText()).toContain('"type": "or"'); visualTab()
    fireEvent.click(screen.getAllByRole('button', { name: '⧉' })[0])
    jsonTab(); expect((jsonText().match(/"type": "compare"/g) ?? []).length).toBe(2); visualTab()
    fireEvent.click(screen.getAllByRole('button', { name: '✕' })[0])
    jsonTab(); expect((jsonText().match(/"type": "compare"/g) ?? []).length).toBe(1)
  })

  it('flags unsupported configurations (MACD fast < slow) without corrupting state', async () => {
    renderNodes()
    await startBlank()
    fireEvent.click(screen.getByRole('button', { name: /^MACD / }))
    const fast = screen.getByLabelText(/^Fast EMA/)
    fireEvent.change(fast, { target: { value: '30' } }) // default slow = 26 → 30 >= 26
    expect(screen.getAllByText(/fast < slow/i).length).toBeGreaterThan(0)
    jsonTab()
    expect(() => JSON.parse(jsonText())).not.toThrow() // JSON stays valid
  })

  it('reports indicator values as unavailable — never fakes numbers', async () => {
    renderNodes()
    await startBlank()
    fireEvent.click(screen.getByRole('button', { name: /^EMA / }))
    expect(screen.getAllByText(/INDICATOR ENGINE NOT AVAILABLE/i).length).toBeGreaterThan(0)
    expect(screen.queryByText(/EMA\(\d+[^)]*\):\s*[0-9]/)).not.toBeInTheDocument()
  })

  it('keeps JSON ↔ Visual in sync, surfaces invalid JSON safely, and reverts', async () => {
    renderNodes()
    await startBlank()
    jsonTab()
    const edited = JSON.stringify({
      schema_version: '1.0.0',
      identity: { name: 'x', description: '', instrument: 'XAUUSD', timeframe: 'M15', direction: 'LONG', origin: 'CUSTOM', tags: [], metadata: {} },
      execution: { entry_timing: 'next_bar_open', candle: 'completed', permit_forming_candle: false, order_type: 'market', position_handling: 'single' },
      entry: { type: 'and', children: [{ type: 'compare', op: 'gte', left: { kind: 'indicator', name: 'VWAP', params: { anchor: 'daily' }, offset: 0 }, right: { kind: 'constant', value: 0 } }] },
      exits: {}, risk: { sizing: { mode: 'fixed_risk', risk_pct: 1 } },
    }, null, 2)
    fireEvent.change(screen.getByLabelText(/specification json/i), { target: { value: edited } })
    visualTab()
    expect(screen.getByText(/VWAP\(daily\)/)).toBeInTheDocument()
    // invalid JSON: banner + last-valid preserved + revert restores
    jsonTab()
    fireEvent.change(screen.getByLabelText(/specification json/i), { target: { value: '{ broken' } })
    expect(screen.getByRole('alert')).toHaveTextContent(/the visual builder keeps the last valid spec/i)
    visualTab()
    expect(screen.getByRole('alert')).toHaveTextContent(/JSON is invalid/i)
    expect(screen.getByText(/VWAP\(daily\)/)).toBeInTheDocument() // last valid spec still shown
    fireEvent.click(screen.getByRole('button', { name: /revert json to last valid/i }))
    jsonTab()
    expect(jsonText()).toContain('"VWAP"')
  })

  it('does not wipe the JSON editor when JSON is invalid', async () => {
    renderNodes()
    await startBlank()
    jsonTab()
    const broken = '{ broken'
    fireEvent.change(screen.getByLabelText(/specification json/i), { target: { value: broken } })
    visualTab()
    fireEvent.click(screen.getByRole('button', { name: /^EMA / })) // blocked while JSON invalid
    jsonTab()
    expect(jsonText()).toBe(broken)
  })

  it('auto-names C<Next> from the registry and refreshes proven correct', async () => {
    ;(globalThis as { __hero1NodeItems?: unknown[] }).__hero1NodeItems = crowdedRegistry(4) // C1..C4
    const { unmount } = renderNodes()
    const field = await screen.findByLabelText(/^node name$/i)
    await waitFor(() => expect((field as HTMLInputElement).value).toBe('C5'))
    unmount()
    // refresh: same registry ⇒ same number — never resets to C1, never reuses
    renderNodes()
    const field2 = await screen.findByLabelText(/^node name$/i)
    await waitFor(() => expect((field2 as HTMLInputElement).value).toBe('C5'))
  })

  it('auto-name starts at C1 with an empty registry; save uses the auto name', async () => {
    renderNodes()
    const field = await screen.findByLabelText(/^node name$/i)
    await waitFor(() => expect((field as HTMLInputElement).value).toBe('C1'))
    await startBlank()
    fireEvent.click(screen.getByRole('button', { name: /save as new spec node/i }))
    await waitFor(() => expect(postCalls().length).toBeGreaterThan(0))
    const body = JSON.parse(String((postCalls()[0][1] as RequestInit).body))
    expect(body.name).toBe('C1')
    expect(body.spec_json).toBeTruthy()
  })

  it('preserves a manual custom name on save (hash path still backend-compiled)', async () => {
    renderNodes()
    const field = await screen.findByLabelText(/^node name$/i)
    await waitFor(() => expect((field as HTMLInputElement).value).toBe('C1'))
    fireEvent.change(field, { target: { value: 'XAU Breakout' } })
    await startBlank()
    fireEvent.click(screen.getByRole('button', { name: /save as new spec node/i }))
    await waitFor(() => expect(postCalls().length).toBeGreaterThan(0))
    const body = JSON.parse(String((postCalls()[0][1] as RequestInit).body))
    expect(body.name).toBe('XAU Breakout')
    expect(body.spec_json).toContain('XAU Breakout') // identity.name follows the node name
  })

  it('instrument switch resets the workspace — no cross-instrument spec mixing', async () => {
    renderNodes()
    await startBlank()
    fireEvent.click(screen.getByRole('button', { name: /^EMA / }))
    fireEvent.change(screen.getByLabelText(/^instrument$/i), { target: { value: 'EURUSD' } })
    // workspace cleared; EURUSD registry is empty → auto name back to C1
    const field = await screen.findByLabelText(/^node name$/i)
    await waitFor(() => expect((field as HTMLInputElement).value).toBe('C1'))
    expect(screen.getByText(/no strategy loaded/i)).toBeInTheDocument()
  })
})
