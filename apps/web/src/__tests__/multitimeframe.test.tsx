import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { describe, it, expect, beforeEach } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import App from '../App'

const renderAt = (route: string) =>
  render(<MemoryRouter initialEntries={[route]}><App /></MemoryRouter>)

const seedNodes = (nodes: Array<Record<string, unknown>>) => {
  ;(globalThis as { __hero1NodeItems?: Array<Record<string, unknown>> }).__hero1NodeItems = nodes
}
const node = (id: number, name: string, timeframe: string | null) => ({
  id, instrument_id: 'XAUUSD', origin: 'CUSTOM', name, description: null,
  status: 'DRAFT', approval_state: 'PENDING', version: 1, parent_node_id: null,
  spec_hash: null, spec_json: null, created_by: 'test', created_at: 0, updated_at: 0,
  starred: false, timeframe,
})
const generateCalls = () =>
  (globalThis.fetch as ReturnType<typeof import('vitest').vi.fn>).mock.calls
    .filter(([url, init]) => String(url).includes('/nodes/generate') && (init as RequestInit)?.method === 'POST')

describe('Multi-timeframe strategy generation (frontend)', () => {
  beforeEach(() => {
    delete (globalThis as { __hero1NodeItems?: unknown[] }).__hero1NodeItems
  })

  it('save fans out one independent node per selected timeframe (default = registry M15)', async () => {
    renderAt('/manual-nodes')
    // load the example strategy and select M1 + M5 on top of the M15 default
    fireEvent.click((await screen.findAllByRole('button', { name: /load bullish engulfing example/i }))[0])
    const tfTrigger = screen.getByRole('button', { name: /timeframes/i })
    expect(tfTrigger).toHaveTextContent(/M15/)
    fireEvent.click(tfTrigger)   // open the panel
    const tfListbox = within(screen.getByRole('listbox'))
    fireEvent.click(tfListbox.getByRole('option', { name: 'M1' }))
    fireEvent.click(tfListbox.getByRole('option', { name: 'M5' }))
    fireEvent.click(tfListbox.getByRole('option', { name: 'M30' }))
    expect(screen.getByRole('button', { name: /generate 4 nodes/i })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /generate 4 nodes/i }))
    await waitFor(() => expect(generateCalls().length).toBe(1))
    const body = JSON.parse(String((generateCalls()[0][1] as RequestInit).body))
    expect(body.timeframes).toEqual(['M1', 'M5', 'M15', 'M30'])
    expect(body.name).toBeUndefined()                    // multi-timeframe → server allocates sequential names
    // one role=status notice lists every created node
    const notices = await screen.findAllByRole('status')
    expect(notices.some((n) => /Saved 4 independent nodes/i.test(n.textContent ?? ''))).toBe(true)
    // exactly ONE spec goes over the wire; the reference TF rides it for authoring and
    // the server re-stamps identity.timeframe per clone (each node fingerprinted separately)
    expect(body.spec.identity.timeframe).toBe('M1')
  })

  it('no timeframe selected blocks save with an inline error and never calls the API', async () => {
    renderAt('/manual-nodes')
    fireEvent.click((await screen.findAllByRole('button', { name: /load bullish engulfing example/i }))[0])
    fireEvent.click(screen.getByRole('button', { name: /timeframes/i }))   // open panel
    fireEvent.click(screen.getByRole('button', { name: 'Clear All' }))
    fireEvent.click(screen.getByRole('button', { name: /timeframes/i }))   // close panel
    fireEvent.click(screen.getByRole('button', { name: /save as new spec node/i }))
    const alerts = await screen.findAllByRole('alert')
    expect(alerts.some((a) => /select at least one timeframe/i.test(a.textContent ?? ''))).toBe(true)
    expect(generateCalls().length).toBe(0)
  })

  it('registry shows the TF column and timeframe chips filter server-side', async () => {
    seedNodes([node(1, 'Alpha', 'M1'), node(2, 'Beta', 'M15'), node(3, 'Gamma', null)])
    renderAt('/manual-nodes')
    expect(await screen.findByText('Alpha')).toBeInTheDocument()
    expect(screen.getByText('Beta')).toBeInTheDocument()
    expect(screen.getAllByText('M1').length).toBeGreaterThan(0)
    expect(screen.getAllByText('M15').length).toBeGreaterThan(0)
    // filter to M15 — list refetches with timeframe=M15 and selection applies to visible only
    fireEvent.click(screen.getByRole('button', { name: /^M15/ }))
    await waitFor(() => {
      const tfCalls = (globalThis.fetch as ReturnType<typeof import('vitest').vi.fn>).mock.calls
        .filter(([url]) => String(url).includes('timeframe=M15'))
      expect(tfCalls.length).toBeGreaterThan(0)
    })
    expect(screen.getByText(/Select All applies only to these visible nodes/i)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('checkbox', { name: /select all visible nodes/i }))
    const sel1 = screen.getAllByRole('status').map((e) => e.textContent ?? '')
    expect(sel1.some((t) => /1 selected/.test(t))).toBe(true)
    // clear the filter — unselected siblings (M1 Alpha, Gamma) stay unchecked
    const tfChips = within(screen.getByRole('group', { name: /filter nodes by timeframe/i }))
    fireEvent.click(tfChips.getByRole('button', { name: /^All$/ }))
    await screen.findByText('Alpha')
    const statuses = screen.getAllByRole('status').map((e) => e.textContent ?? '')
    expect(statuses.some((t) => /1 selected/.test(t))).toBe(true)
  })

  it('group-by-timeframe renders group separator rows and keeps unfiltered nodes visible', async () => {
    seedNodes([node(1, 'Alpha', 'M1'), node(2, 'Beta', 'M15'), node(3, 'Gamma', null)])
    renderAt('/manual-nodes')
    await screen.findByText('Alpha')
    fireEvent.click(screen.getByRole('button', { name: /group by timeframe/i }))
    expect(screen.getByText(/M1 · 1 node/i)).toBeInTheDocument()
    expect(screen.getByText(/M15 · 1 node/i)).toBeInTheDocument()
    expect(screen.getByText(/NO TIMEFRAME \(drafts\) · 1/i)).toBeInTheDocument()
  })

  it('inline rename: Enter saves via PATCH, Escape cancels — hash untouched', async () => {
    seedNodes([node(7, 'C7', 'M15')])
    renderAt('/manual-nodes')
    const nameButton = await screen.findByRole('button', { name: 'C7' })
    // Escape cancels
    fireEvent.click(nameButton)
    const input = screen.getByRole('textbox', { name: /rename node 7/i })
    fireEvent.change(input, { target: { value: 'Discarded Draft' } })
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(screen.getByRole('button', { name: 'C7' })).toBeInTheDocument()
    expect(screen.queryByDisplayValue('Discarded Draft')).not.toBeInTheDocument()
    // Enter saves (metadata-only PATCH)
    fireEvent.click(screen.getByRole('button', { name: 'C7' }))
    const input2 = screen.getByRole('textbox', { name: /rename node 7/i })
    fireEvent.change(input2, { target: { value: 'Renamed Strategy' } })
    fireEvent.keyDown(input2, { key: 'Enter' })
    await waitFor(() => {
      const patches = (globalThis.fetch as ReturnType<typeof import('vitest').vi.fn>).mock.calls
        .filter(([url, init]) => /\/nodes\/\d+$/.test(String(url)) && (init as RequestInit)?.method === 'PATCH')
      expect(patches.length).toBe(1)
      expect(String((patches[0][1] as RequestInit).body)).toContain('Renamed Strategy')
    })
    expect(await screen.findByRole('status')).toHaveTextContent(/Renamed node #7/i)
  })

  it('empty inline rename is rejected client-side and never reaches the API', async () => {
    seedNodes([node(8, 'C8', null)])
    renderAt('/manual-nodes')
    fireEvent.click(await screen.findByRole('button', { name: 'C8' }))
    const input = screen.getByRole('textbox', { name: /rename node 8/i })
    fireEvent.change(input, { target: { value: '   ' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(screen.getByRole('alert')).toHaveTextContent(/cannot be empty/i)
    const patches = (globalThis.fetch as ReturnType<typeof import('vitest').vi.fn>).mock.calls
      .filter(([, init]) => (init as RequestInit)?.method === 'PATCH')
    expect(patches.length).toBe(0)
  })

  it('duplicate inline rename is rejected client-side with the conflicting node named', async () => {
    seedNodes([node(9, 'C9', 'M15'), node(10, 'Taken', 'M15')])
    renderAt('/manual-nodes')
    fireEvent.click(await screen.findByRole('button', { name: 'C9' }))
    const input = screen.getByRole('textbox', { name: /rename node 9/i })
    fireEvent.change(input, { target: { value: 'taken' } })   // case-insensitive clash
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(screen.getByRole('alert')).toHaveTextContent(/node #10/i)
  })

  it('validation error appears when the last selected timeframe is unselected', async () => {
    renderAt('/manual-nodes')
    fireEvent.click((await screen.findAllByRole('button', { name: /load bullish engulfing example/i }))[0])
    fireEvent.click(screen.getByRole('button', { name: /timeframes/i }))   // open panel
    // M15 is the only selected TF (registry default) — toggle it off inside the panel
    fireEvent.click(within(screen.getByRole('listbox')).getByRole('option', { name: 'M15' }))
    expect(screen.getByRole('button', { name: /timeframes/i })).toHaveTextContent(/Select timeframes/i)
    fireEvent.click(screen.getByRole('button', { name: /save as new spec node/i }))
    const alerts = await screen.findAllByRole('alert')
    expect(alerts.some((a) => /select at least one timeframe/i.test(a.textContent ?? ''))).toBe(true)
  })

  it('DeepTesting renders the shared timeframe scope for batch selection', async () => {
    renderAt('/deeptesting')
    expect(await screen.findByText(/Batch Node Scope/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /timeframes in scope/i })).toBeInTheDocument()
    expect(screen.getByText(/W1\/MN1 are not supported/i)).toBeInTheDocument()
  })

  it('keyboard: timeframe listbox opens, moves and toggles with arrows+space', async () => {
    renderAt('/manual-nodes')
    const trigger = await screen.findByRole('button', { name: /timeframes/i })
    trigger.focus()
    fireEvent.keyDown(trigger, { key: 'ArrowDown' })       // opens, active = first SELECTED (M15)
    expect(screen.getByRole('listbox')).toBeInTheDocument()
    fireEvent.keyDown(trigger, { key: 'Home' })            // jump to M1
    fireEvent.keyDown(trigger, { key: ' ' })               // toggle M1 on
    expect(trigger).toHaveTextContent(/M1 · M15/)
    fireEvent.keyDown(trigger, { key: 'Escape' })
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })
})
