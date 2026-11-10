import { render, screen, fireEvent, act, waitFor } from '@testing-library/react'
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import App from '../App'
import { api } from '../api/client'
import { sessionCell } from '../pages/DeepTesting'

const NODE = {
  id: 1, instrument_id: 'XAUUSD', origin: 'CUSTOM', name: 'Acceptance Node', description: null,
  status: 'DRAFT', approval_state: 'PENDING', version: 3, parent_node_id: null,
  spec_hash: 'abc', spec_json: null, created_by: 'test', created_at: 0, updated_at: 0,
  starred: false, timeframe: 'H1',
}

const renderDT = () =>
  render(<MemoryRouter initialEntries={['/deeptesting']}><App /></MemoryRouter>)

describe('DeepTesting session classification', () => {
  it('classifies weekend timestamps only as UNASSIGNED, never as ASIA or another session', () => {
    expect(sessionCell(Date.UTC(2026, 0, 4, 23) / 1000)).toBe('UNASSIGNED')
    expect(sessionCell(Date.UTC(2026, 0, 3, 14) / 1000)).toBe('UNASSIGNED')
    expect(sessionCell(Date.UTC(2026, 0, 5, 7) / 1000)).toBe('ASIA')
    expect(sessionCell(Date.UTC(2026, 0, 5, 8) / 1000)).toBe('ASIA-LONDON')
  })
})

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

  it('offers Historical Spread and NO SPREAD as saved DeepTesting constraints', async () => {
    renderDT()
    const select = await screen.findByLabelText('Spread mode') as HTMLSelectElement
    expect(Array.from(select.options).map((o) => o.textContent)).toEqual(['Historical Spread', 'NO SPREAD'])
    fireEvent.change(select, { target: { value: 'no_spread' } })
    fireEvent.click(screen.getAllByRole('button', { name: /^APPLY$/i })[0])
    expect(select.value).toBe('no_spread')
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
    // offsets are PENDING until the node's Apply is clicked (Part 4)
    fireEvent.change(screen.getByLabelText(/TP plus node 1/i), { target: { value: '50' } })
    expect(screen.getByText('pending')).toBeTruthy()
    expect(screen.queryAllByText(/Individual node/).length).toBe(0)
    fireEvent.click(screen.getByLabelText(/Apply offsets node 1/i))
    expect(screen.getAllByText(/Individual node/).length).toBeGreaterThan(0)
  })

  it('Hard Constraints edits stay pending until APPLY', async () => {
    renderDT()
    expect(await screen.findByText(/^Applied$/)).toBeTruthy()
    fireEvent.change(screen.getByLabelText(/Global TP plus/i), { target: { value: '25' } })
    expect(screen.getByText(/Pending changes — not applied yet/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /^APPLY$/i }))
    expect(await screen.findByText(/^Applied$/)).toBeTruthy()
    expect(screen.getByText(/global offsets SL \+0\/−0 · TP \+25\/−0 pips/)).toBeTruthy()
  })

  it('inspection: stats first, weekday/session grid, live filtered view, ONE equity curve', async () => {
    renderDT()
    fireEvent.click(await screen.findByRole('button', { name: /^Inspect$/i }))
    expect(await screen.findByText(/Inspection — Acceptance Node/i)).toBeTruthy()
    expect(screen.getByLabelText(/Complete statistics/i)).toBeTruthy()
    expect(screen.getByLabelText(/Weekday and session filters/i)).toBeTruthy()
    expect(screen.getByLabelText('Include Monday')).toBeTruthy()
    expect(screen.getByLabelText(/Include Monday ASIA-LONDON/i)).toBeTruthy()
    expect(screen.getByLabelText('Include UNASSIGNED all days')).toBeTruthy()
    // unchecking Monday's ASIA session switches to a clearly-marked filtered view
    fireEvent.click(screen.getByLabelText(/Include Monday ASIA-LONDON/i))
    expect(await screen.findByText(/FILTERED VIEW/)).toBeTruthy()
    // whole-box click toggles too: clicking the Tuesday ASIA cell removes its trade
    const tueAsiaCell = screen.getByLabelText('Include Tuesday ASIA').closest('td') as HTMLElement
    fireEvent.click(tueAsiaCell)
    expect((screen.getByLabelText('Include Tuesday ASIA') as HTMLInputElement).checked).toBe(false)
    fireEvent.click(tueAsiaCell) // restore so the equity curve has data again
    expect((screen.getByLabelText('Include Tuesday ASIA') as HTMLInputElement).checked).toBe(true)
    expect(screen.getByRole('button', { name: /SAVE FILTERS AS SCENARIO/i })).toBeTruthy()
    expect(screen.getByLabelText(/Equity curve/)).toBeTruthy()
  })

  it('node table has three sortable consecutive-loss columns matching inspection', async () => {
    renderDT()
    for (const h of [/^Max consec\. losses/, /^Max consec\. loss(?!$)/, /^Max consec\. loss %/]) {
      expect((await screen.findAllByRole('button', { name: h })).length).toBeGreaterThan(0)
    }
    expect(screen.getByRole('button', { name: /^Max consec\. loss$/ })).toBeTruthy()
    fireEvent.click(await screen.findByRole('button', { name: /^Inspect$/i }))
    await screen.findByText(/Inspection — Acceptance Node/i)
    expect(screen.getByText('Max consecutive losses')).toBeTruthy()
    expect(screen.getByText('Max consecutive loss amount')).toBeTruthy()
    expect(screen.getByText('Max consecutive loss %')).toBeTruthy()
  })

  it('aggregate TOTAL column combines selected days and updates with checkboxes', async () => {
    const { container } = renderDT()
    fireEvent.click(await screen.findByRole('button', { name: /^Inspect$/i }))
    await screen.findByText(/Inspection — Acceptance Node/i)
    expect(screen.getByText('TOTAL — selected days')).toBeTruthy()
    const aggCells = () => container.querySelectorAll('td.dt-agg')
    // mock run: 1 winning trade, Tuesday 22:13 UTC -> ASIA
    expect(aggCells()[0].textContent).toContain('1 trades') // TOTAL DAYS, counted once
    expect(aggCells()[1].textContent).toContain('1 trades') // ASIA across selected days
    expect(aggCells()[0].textContent).toContain('Net 200')
    expect(aggCells()[0].textContent).toContain('DD 25 (2.5%)') // full TOTAL DAY uses run equity curve
    fireEvent.click(screen.getByLabelText('Include Tuesday ASIA'))
    expect(aggCells()[0].textContent).toContain('0 trades') // updates dynamically
    expect(aggCells()[1].textContent).toContain('0 trades')
  })

  it('whole session heading toggles across days; DESELECT -VE NET BOXES works', async () => {
    renderDT()
    fireEvent.click(await screen.findByRole('button', { name: /^Inspect$/i }))
    await screen.findByText(/Inspection — Acceptance Node/i)
    // mock trade is Tuesday ASIA with net +200: session heading click clears it everywhere
    fireEvent.click(screen.getByLabelText('Include ASIA all days'))
    expect((screen.getByLabelText('Include Tuesday ASIA') as HTMLInputElement).checked).toBe(false)
    fireEvent.click(screen.getByLabelText('Include ASIA all days'))
    expect((screen.getByLabelText('Include Tuesday ASIA') as HTMLInputElement).checked).toBe(true)
    // +200 net is positive: negative-net deselect must leave it selected
    fireEvent.click(screen.getByRole('button', { name: /DESELECT -VE NET BOXES/i }))
    expect((screen.getByLabelText('Include Tuesday ASIA') as HTMLInputElement).checked).toBe(true)
    expect(screen.getAllByRole('button', { name: /DESELECT ALL/i }).length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: /SELECT ALL DAYS\/SESSIONS/i })).toBeTruthy()
  })

  it('scenario modal persists filtered trades and renders an expandable child under its source node', async () => {
    const scenario = { id: 31, name: 'Tuesday sessions', node_id: 1, node_version: 1, source_run_id: 1,
      filters: { day_selections: { Tuesday: true }, session_selections: { 'Tuesday|ASIA': true } },
      stats: { trades: 1, net: 200, winRate: 1, pf: null, maxDd: 25 }, trades: [{ id: 1, net_pnl: 200 }] }
    const list = vi.spyOn(api.deeptesting, 'listScenarios')
      .mockResolvedValueOnce({ scenarios: [] } as any)
      .mockResolvedValue({ scenarios: [scenario] } as any)
    const save = vi.spyOn(api.deeptesting, 'createScenario').mockResolvedValue({ id: 31, version: 1 } as any)
    renderDT()
    fireEvent.click(await screen.findByRole('button', { name: /^Inspect$/i }))
    await screen.findByText(/Inspection — Acceptance Node/i)
    fireEvent.click(screen.getByRole('button', { name: /SAVE FILTERS AS SCENARIO/i }))
    expect(await screen.findByRole('dialog')).toBeTruthy()
    fireEvent.change(screen.getByLabelText(/Scenario name/i), { target: { value: 'London weekdays' } })
    expect(screen.getByText(/Days\/Sessions:/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Save Scenario/i }))
    expect(await screen.findByText(/saved as persistent child/)).toBeTruthy()
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1))
    const payload = save.mock.calls[0][1] as any
    expect(payload.config.node_ids).toEqual([1])
    expect(payload.config.source_run_id).toBe(1)
    expect(payload.config.filtered_trade_ids).toEqual([1])
    expect(payload.config.filtered_metrics.trades).toBe(1)
    expect(list).toHaveBeenCalledTimes(2)
    expect(screen.getByText(/1 saved/)).toBeTruthy()
    const child = await waitFor(() => {
      const row = document.querySelector('tr.dt-scenario-child') as HTMLTableRowElement | null
      expect(row).toBeTruthy()
      return row!
    })
    expect(child.textContent).toContain('Tuesday sessions')
    expect(child.cells[5].textContent).toBe('XAUUSD')
    expect(child.textContent).toContain('200')
    expect(child.textContent).not.toContain('day_selections')
    expect(child.textContent).not.toContain('session_selections')
  })
})

describe('DeepTesting final improvements', () => {
  beforeEach(() => {
    ;(globalThis as { __hero1NodeItems?: unknown[] }).__hero1NodeItems = [NODE]
    localStorage.clear()
  })

  it('shows independent BUY/LONG and SELL/SHORT sections in the main statistics only', async () => {
    renderDT()
    fireEvent.click(await screen.findByRole('button', { name: /^Inspect$/i }))
    await screen.findByText(/Inspection — Acceptance Node/i)
    expect(screen.getByText(/^BUY$/)).toBeTruthy()
    expect(screen.getByText(/^SELL$/)).toBeTruthy()
    const grid = screen.getByLabelText(/Weekday and session filters/i)
    expect(grid.textContent).not.toMatch(/BUY \/ LONG/)
  })

  it('highlights the full inspected node row in the node table', async () => {
    const { container } = renderDT()
    fireEvent.click(await screen.findByRole('button', { name: /^Inspect$/i }))
    await screen.findByText(/Inspection — Acceptance Node/i)
    expect(container.querySelector('tr.row-active')).toBeTruthy()
  })

  it('shows a per-cell drawdown line inside weekday/session cells', async () => {
    renderDT()
    fireEvent.click(await screen.findByRole('button', { name: /^Inspect$/i }))
    await screen.findByText(/Inspection — Acceptance Node/i)
    const cell = screen.getByLabelText('Include Tuesday ASIA').closest('td') as HTMLElement
    expect(cell.textContent).toMatch(/DD 0 \(0%\)/) // single winning trade: no drawdown
  })

  it('SHOW TRADES reveals the trade table only on demand, with server page info', async () => {
    renderDT()
    fireEvent.click(await screen.findByRole('button', { name: /^Inspect$/i }))
    await screen.findByText(/Inspection — Acceptance Node/i)
    expect(screen.queryByLabelText(/Filtered trades/i)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /SHOW TRADES/i }))
    expect(await screen.findByLabelText(/Filtered trades/i)).toBeTruthy()
    expect(screen.getByText(/1 filtered trades/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /HIDE TRADES/i }))
    expect(screen.queryByLabelText(/Filtered trades/i)).toBeNull()
  })

  it('range options include persisted TILL DATA', async () => {
    renderDT()
    const till = await screen.findByRole('button', { name: 'TILL DATA' })
    expect(till).toBeTruthy()
    expect(screen.getByRole('button', { name: '1 Year' })).toBeTruthy()
    fireEvent.click(till)
    expect(localStorage.getItem('hero1.dt.rangeDays')).toBe('-1')
  })

  it('parent weekday checkbox is indeterminate when only some sessions are off', async () => {
    renderDT()
    fireEvent.click(await screen.findByRole('button', { name: /^Inspect$/i }))
    await screen.findByText(/Inspection — Acceptance Node/i)
    fireEvent.click(screen.getByLabelText('Include Tuesday NY'))
    const parent = screen.getByLabelText('Include Tuesday') as HTMLInputElement
    expect(parent.indeterminate).toBe(true) // some-but-not-all selected
  })
})

describe('Task progress, filters, exports, JSON viewer', () => {
  beforeEach(() => {
    ;(globalThis as { __hero1NodeItems?: unknown[] }).__hero1NodeItems = [NODE]
    localStorage.clear()
  })

  it('side-by-side BUY/SELL comparison shows both directions compactly', async () => {
    renderDT()
    fireEvent.click(await screen.findByRole('button', { name: /^Inspect$/i }))
    await screen.findByText(/Inspection — Acceptance Node/i)
    const cards = screen.getByLabelText(/BUY and SELL statistics/i)
    expect(cards.textContent).toMatch(/BUY/)
    expect(cards.textContent).toMatch(/SELL/)
    expect(cards.textContent).toMatch(/Trade count/)
    expect(cards.textContent).toMatch(/Profit factor/)
    fireEvent.click(screen.getByText(/Additional directional statistics/))
    expect(screen.getAllByText(/Avg holding/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Max drawdown/).length).toBeGreaterThan(0)
  })

  it('trade filters drive the filtered view; page size offers ALL', async () => {
    renderDT()
    fireEvent.click(await screen.findByRole('button', { name: /^Inspect$/i }))
    await screen.findByText(/Inspection — Acceptance Node/i)
    fireEvent.click(screen.getByRole('button', { name: /SHOW TRADES/i }))
    expect(await screen.findByLabelText(/Filtered trades/i)).toBeTruthy()
    const size = screen.getByLabelText(/Trade page size/i) as HTMLSelectElement
    expect([...size.options].map((o) => o.text)).toEqual(['10', '15', '25', 'ALL'])
    expect(size.value).toBe('25')
    // direction filter excludes the mock LONG trade -> filtered view marks 0 trades
    fireEvent.change(screen.getByLabelText(/Trade direction/i), { target: { value: 'SHORT' } })
    expect(await screen.findByText(/0 matching trade\(s\)/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Clear filters/i }))
    expect(await screen.findByText(/1 matching trade\(s\)/)).toBeTruthy()
  })

  it('direction text is coloured by side only', async () => {
    renderDT()
    fireEvent.click(await screen.findByRole('button', { name: /^Inspect$/i }))
    await screen.findByText(/Inspection — Acceptance Node/i)
    fireEvent.click(screen.getByRole('button', { name: /SHOW TRADES/i }))
    const table = await screen.findByLabelText(/Filtered trades/i)
    expect(table.querySelector('.dir-long')?.textContent).toBe('LONG')
  })

  it('export controls are present for table and trading data', async () => {
    renderDT()
    fireEvent.click(await screen.findByRole('button', { name: /^Inspect$/i }))
    await screen.findByText(/Inspection — Acceptance Node/i)
    expect(screen.getByRole('button', { name: 'EXPORT TABLE' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'EXPORT TRADING DATA' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'EXPORT TRADING DATA (FILTERED)' })).toBeTruthy()
  })
})

describe('Global task bars and JSON viewer', () => {
  beforeEach(() => {
    ;(globalThis as { __hero1NodeItems?: unknown[] }).__hero1NodeItems = [NODE]
    localStorage.clear()
  })

  it('renders no global task bars when no tasks exist', async () => {
    renderDT()
    await screen.findByText(/Hard Constraints/i)
    expect(document.querySelector('.task-progress-area')).toBeNull()
  })

  it('JSON viewer pretty-prints nested strategy JSON with line numbers and copy', async () => {
    const { JsonViewer } = await import('../components/JsonViewer')
    const { render: r2 } = await import('@testing-library/react')
    const raw = '{"identity":{"name":"N1","tags":["a"]},"risk":{"value":1.5,"enabled":true,"note":null}}'
    const view = r2(<JsonViewer raw={raw} title="Stored strategy JSON" />)
    expect(view.getByText(/Copy JSON/)).toBeTruthy()
    const code = view.container.querySelector('.json-viewer')?.textContent ?? ''
    expect(code).toContain('"identity"')
    expect(code).toContain('"value"')
    // values unchanged: the pretty text parses back to the same object
    const pretty = JSON.stringify(JSON.parse(raw), null, 2)
    expect(pretty).toContain('"note": null')
    expect(JSON.parse(pretty)).toEqual(JSON.parse(raw))
  })
})

describe('Global progress bars from shared task state', () => {
  afterEach(() => { vi.useRealTimers(); (globalThis as { __hero1Tasks?: unknown }).__hero1Tasks = undefined })
  beforeEach(() => {
    ;(globalThis as { __hero1NodeItems?: unknown[] }).__hero1NodeItems = [NODE]
    localStorage.clear()
  })

  const TASK = (over: Record<string, unknown>) => ({
    id: 't1', category: 'DEEPTESTING', instrument: 'XAUUSD', label: 'DeepTesting',
    status: 'RUNNING', total_units: 20, completed_units: 2, current_unit: 'Node 3',
    current_unit_progress: 34, percentage: 10, summary: null, error: null,
    acknowledged: false, payload: { nodes: [] }, created_at: 0, finished_at: null, ...over })

  it('shows one bar per active task with its own percentage and colour category', async () => {
    ;(globalThis as { __hero1Tasks?: unknown }).__hero1Tasks = {
      tasks: [TASK({}), TASK({ id: 't2', category: 'DATA_FETCHING', percentage: 72, completed_units: 13, total_units: 18, current_unit: 'H1' })],
      latest: {} }
    renderDT()
    await screen.findByText(/2 \/ 20 nodes tested/)
    const area = document.querySelector('.task-progress-area') as HTMLElement
    expect(area.textContent).toContain('10%')
    expect(area.textContent).toContain('72%')
    expect(area.textContent).toContain('DEEPTESTING')
    expect(area.textContent).toContain('DATA FETCHING')
    expect(area.querySelectorAll('.task-bar')).toHaveLength(2)
    ;(globalThis as { __hero1Tasks?: unknown }).__hero1Tasks = undefined
  })

  it('a completed unacknowledged task stays visible at 100%', async () => {
    ;(globalThis as { __hero1Tasks?: unknown }).__hero1Tasks = {
      tasks: [TASK({ status: 'COMPLETED', percentage: 100, completed_units: 20, current_unit: null })],
      latest: {} }
    renderDT()
    await screen.findByText(/20 \/ 20 nodes tested/)
    const area = document.querySelector('.task-progress-area') as HTMLElement
    expect(area.textContent).toContain('COMPLETED')
    expect(area.textContent).toContain('100%')
    ;(globalThis as { __hero1Tasks?: unknown }).__hero1Tasks = undefined
  })

  it('auto-dismisses terminal progress after five seconds while running tasks remain', async () => {
    vi.useFakeTimers()
    const completed = TASK({ status: 'COMPLETED', percentage: 100, completed_units: 20, current_unit: null })
    const failed = TASK({ id: 'tfail', status: 'FAILED', percentage: 40, error: 'simulated failure' })
    const cancelled = TASK({ id: 'tcancel', status: 'CANCELLED', percentage: 25 })
    const running = TASK({ id: 'trun', status: 'RUNNING', percentage: 12 })
    ;(globalThis as { __hero1Tasks?: unknown }).__hero1Tasks = { tasks: [completed, failed, cancelled, running], latest: {} }
    const view = renderDT()
    await act(async () => { for (let i = 0; i < 12; i++) await Promise.resolve() })
    expect(view.container.querySelectorAll('.task-bar')).toHaveLength(4)
    await act(async () => { await vi.advanceTimersByTimeAsync(4999) })
    expect(view.container.querySelectorAll('.task-bar')).toHaveLength(4)
    await act(async () => { await vi.advanceTimersByTimeAsync(1) })
    expect(view.container.querySelectorAll('.task-bar')).toHaveLength(2)
    expect(view.container.querySelector('.task-running')).toBeTruthy()
    expect(view.container.querySelector('.task-failed')).toBeTruthy() // failure is not auto-dismissed
    expect((globalThis as { __hero1Tasks?: any }).__hero1Tasks.tasks).toHaveLength(4) // history is retained
    fireEvent.click(screen.getByRole('button', { name: 'Show error details for DeepTesting' }))
    const panel = screen.getByRole('dialog', { name: 'Failed task details' })
    expect(panel.textContent).toContain('tfail')
    expect(panel.textContent).toContain('simulated failure')
    expect(panel.textContent).toContain('DeepTesting (/deeptesting)')
    fireEvent.click(screen.getByRole('button', { name: 'Close error details and acknowledge task' }))
    await act(async () => { for (let i = 0; i < 8; i++) await Promise.resolve() })
    expect(view.container.querySelectorAll('.task-bar')).toHaveLength(1)
    expect(view.container.querySelector('.task-running')).toBeTruthy()
    ;(globalThis as { __hero1Tasks?: unknown }).__hero1Tasks = undefined
    vi.useRealTimers()
  })

  it('auto-dismiss timers are independent for concurrently completed tasks', async () => {
    vi.useFakeTimers()
    const first = TASK({ id: 'done1', label: 'First task', status: 'COMPLETED', percentage: 100 })
    const second = TASK({ id: 'done2', label: 'Second task', status: 'CANCELLED', percentage: 50 })
    ;(globalThis as { __hero1Tasks?: unknown }).__hero1Tasks = { tasks: [first], latest: {} }
    const view = renderDT()
    await act(async () => { for (let i = 0; i < 12; i++) await Promise.resolve() })
    expect(view.container.querySelectorAll('.task-bar')).toHaveLength(1)
    ;(globalThis as { __hero1Tasks?: unknown }).__hero1Tasks = { tasks: [first, second], latest: {} }
    await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
    expect(view.container.querySelectorAll('.task-bar')).toHaveLength(2)
    await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
    expect(view.container.querySelectorAll('.task-bar')).toHaveLength(1)
    expect(view.container.textContent).toContain('Second task')
    ;(globalThis as { __hero1Tasks?: unknown }).__hero1Tasks = undefined
    vi.useRealTimers()
  })

  it('Live Feed infographic cards filter and unfilter the feed', async () => {
    const { GlobalLiveFeed } = await import('../components/GlobalLiveFeed')
    const { FeedProvider, useFeed } = await import('../state/feed')
    const { TasksProvider } = await import('../state/tasks')
    const { InstrumentProvider } = await import('../state/instrument')
    const { render: r2 } = await import('@testing-library/react')
    const { useEffect } = await import('react')
    function Harness() {
      const { setOpen } = useFeed()
      useEffect(() => { setOpen(true) }, [setOpen])
      return <GlobalLiveFeed />
    }
    ;(globalThis as { __hero1Tasks?: unknown }).__hero1Tasks = {
      tasks: [], latest: { DEEPTESTING: TASK({}) } }
    r2(<InstrumentProvider><TasksProvider><FeedProvider><Harness /></FeedProvider></TasksProvider></InstrumentProvider>)
    const card = await screen.findByRole('button', { name: /DEEPTESTING/ })
    expect(screen.getByRole('group', { name: 'Subsystem activity' })).toHaveClass('feed-cards-compact')
    expect(card.textContent).toMatch(/2 \/ 20/)
    fireEvent.click(card)
    expect(card.getAttribute('aria-pressed')).toBe('true')
    fireEvent.click(card)
    expect(card.getAttribute('aria-pressed')).toBe('false')
    ;(globalThis as { __hero1Tasks?: unknown }).__hero1Tasks = undefined
  })
})
