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

  it('switches instrument via the top-bar selector', () => {
    renderAt('/manual-nodes')
    const eur = screen.getByRole('tab', { name: 'EURUSD' })
    fireEvent.click(eur)
    expect(eur).toHaveAttribute('aria-selected', 'true')
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
