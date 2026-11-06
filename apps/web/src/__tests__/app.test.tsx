import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import App from '../App'

const renderAt = (route: string) =>
  render(<MemoryRouter initialEntries={[route]}><App /></MemoryRouter>)

describe('Hero1 shell', () => {
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

  it('instrument selector defaults to XAUUSD and syncs across tabs', () => {
    renderAt('/manual-nodes')
    const select = screen.getByLabelText(/^instrument$/i) as HTMLSelectElement
    expect(select.value).toBe('XAUUSD') // default is ALWAYS XAUUSD with empty storage
    fireEvent.change(select, { target: { value: 'BTCUSD' } })
    expect(select.value).toBe('BTCUSD')
    // switch to EURUSD, navigate to settings — the shared state travels with us
    fireEvent.change(select, { target: { value: 'EURUSD' } })
    fireEvent.click(screen.getByRole('link', { name: /settings/i }))
    expect((screen.getByLabelText(/^instrument$/i) as HTMLSelectElement).value).toBe('EURUSD')
    // refresh persistence: persisted selection wins on next mount
    expect(localStorage.getItem('hero1.instrument')).toBe('EURUSD')
  })

  it('restores the persisted instrument on refresh', () => {
    localStorage.setItem('hero1.instrument', 'BTCUSD')
    renderAt('/manual-nodes')
    expect((screen.getByLabelText(/^instrument$/i) as HTMLSelectElement).value).toBe('BTCUSD')
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
