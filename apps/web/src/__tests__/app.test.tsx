import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import App from '../App'

const renderAt = (route: string) =>
  render(<MemoryRouter initialEntries={[route]}><App /></MemoryRouter>)

describe('Hero1 shell', () => {
  it('keeps real-host resource monitoring visible when navigating between tabs', async () => {
    renderAt('/manual-nodes')
    expect(await screen.findByText('API HOST')).toBeInTheDocument()
    expect(screen.getByText(/CPU/).parentElement).toHaveTextContent('24%')
    expect(screen.getByText(/GPU/).parentElement).toHaveTextContent('unavailable')
    fireEvent.click(screen.getByRole('link', { name: /DeepTesting/i }))
    expect(screen.getByText('API HOST')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('link', { name: /Monte Carlo/i }))
    expect(screen.getByText('API HOST')).toBeInTheDocument()
  })
  it('renders all eight navigation tabs', () => {
    renderAt('/manual-nodes')
    for (const label of ['Manual Nodes', 'WWW Research', 'Interactive WWW', 'DeepTesting', 'Fine Tuning', 'Live Testing', 'LOG', 'SETTINGS']) {
      expect(screen.getByRole('link', { name: new RegExp(label, 'i') })).toBeInTheDocument()
    }
  })

  it('redirects / to manual nodes', () => {
    renderAt('/')
    expect(screen.getByRole('heading', { name: /manual nodes/i })).toBeInTheDocument()
  })

  it('instrument listbox defaults to XAUUSD and syncs across tabs', () => {
    renderAt('/manual-nodes')
    const trigger = screen.getByRole('button', { name: /instrument — current: XAUUSD/i })
    expect(trigger).toBeInTheDocument() // default is ALWAYS XAUUSD with empty storage
    fireEvent.click(trigger)
    fireEvent.click(screen.getByRole('option', { name: /BTCUSD Bitcoin vs US Dollar/i }))
    expect(screen.getByRole('button', { name: /instrument — current: BTCUSD/i })).toBeInTheDocument()
    // switch to EURUSD, navigate to settings — the shared state travels with us
    fireEvent.click(screen.getByRole('button', { name: /instrument — current: BTCUSD/i }))
    fireEvent.click(screen.getByRole('option', { name: /EURUSD Euro vs US Dollar/i }))
    fireEvent.click(screen.getByRole('link', { name: /settings/i }))
    expect(screen.getByRole('button', { name: /instrument — current: EURUSD/i })).toBeInTheDocument()
    // refresh persistence: persisted selection wins on next mount
    expect(localStorage.getItem('hero1.instrument')).toBe('EURUSD')
  })

  it('instrument listbox keyboard navigation (ArrowDown/Enter/Escape/Home/End)', () => {
    renderAt('/manual-nodes')
    const trigger = screen.getByRole('button', { name: /instrument — current/i })
    trigger.focus()
    fireEvent.keyDown(trigger, { key: 'ArrowDown' })          // opens, active = current (XAUUSD)
    expect(screen.getByRole('listbox')).toBeInTheDocument()
    fireEvent.keyDown(trigger, { key: 'ArrowDown' })          // move to EURUSD
    fireEvent.keyDown(trigger, { key: 'ArrowDown' })          // move to BTCUSD
    fireEvent.keyDown(trigger, { key: 'ArrowUp' })            // back to EURUSD
    fireEvent.keyDown(trigger, { key: 'Enter' })              // select
    expect(screen.getByRole('button', { name: /instrument — current: EURUSD/i })).toBeInTheDocument()
    expect(localStorage.getItem('hero1.instrument')).toBe('EURUSD')
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    // Escape closes without changing the selection
    fireEvent.keyDown(trigger, { key: 'ArrowDown' })
    fireEvent.keyDown(trigger, { key: 'Escape' })
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /instrument — current: EURUSD/i })).toBeInTheDocument()
  })

  it('instrument listbox never selects unavailable instruments (click or keyboard)', () => {
    renderAt('/manual-nodes')
    const trigger = screen.getByRole('button', { name: /instrument — current: XAUUSD/i })
    fireEvent.click(trigger)
    const gbp = screen.getByRole('option', { name: /GBPUSD not in registry/i })
    expect(gbp).toHaveAttribute('aria-disabled', 'true')
    fireEvent.click(gbp)
    expect(screen.getByRole('button', { name: /instrument — current: XAUUSD/i })).toBeInTheDocument()
    expect(screen.getByRole('listbox')).toBeInTheDocument()  // stays open; nothing was selected
    fireEvent.keyDown(trigger, { key: 'End' })               // End jumps past disabled entries
    fireEvent.keyDown(trigger, { key: 'Enter' })
    expect(screen.getByRole('button', { name: /instrument — current: BTCUSD/i })).toBeInTheDocument()
  })

  it('restores the persisted instrument on refresh', () => {
    localStorage.setItem('hero1.instrument', 'BTCUSD')
    renderAt('/manual-nodes')
    expect(screen.getByRole('button', { name: /instrument — current: BTCUSD/i })).toBeInTheDocument()
  })

  it('navigates to settings and renders persisted values', async () => {
    renderAt('/manual-nodes')
    fireEvent.click(screen.getByRole('link', { name: /settings/i }))
    expect(await screen.findByRole('heading', { name: 'SETTINGS' })).toBeInTheDocument()
    await waitFor(() => expect(screen.getByLabelText(/max sources/i)).toHaveValue(40))
  })

  it('shows empty state for an instrument with no nodes', async () => {
    renderAt('/manual-nodes')
    expect(await screen.findByText(/no nodes yet for xauusd/i)).toBeInTheDocument()
  })

  it('global live feed renders and collapses via its toggle', () => {
    renderAt('/manual-nodes')
    expect(screen.getByRole('complementary', { name: /global live feed/i })).toBeInTheDocument()
    const toggle = screen.getByRole('button', { name: /toggle live feed/i })
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-pressed', 'false')
    expect(screen.queryByRole('complementary', { name: /global live feed/i })).not.toBeInTheDocument()
  })
})
