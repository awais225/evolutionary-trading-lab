import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, beforeEach } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import App from '../App'

const renderNodes = () => render(<MemoryRouter initialEntries={['/manual-nodes']}><App /></MemoryRouter>)

const STORED_METADATA = {
  instrument: 'XAUUSD', available: true, snapshot_count: 1, metadata_root: '<test-root>/MARKET_METADATA',
  snapshots: [{ instrument: 'XAUUSD', broker_server: 'ICMarketsSC-Demo', retrieved_at: '2026-11-07T00:00:00Z', fingerprint: 'abc', sha256: 'def', source: 'mt5-terminal', validation_ok: true }],
  snapshot: {
    broker_server: 'ICMarketsSC-Demo', source: 'mt5-terminal', retrieved_at: '2026-11-07T00:00:00Z',
    fingerprint: 'abc', digits: 2,
    derived: { point_size: 0.01, tick_size: 0.01, pip_size: 0.01, contract_size: 100, digits_rule: 'pip == point on 2-digit XAU feeds' },
    validation: { ok: true, missing: [], required_for_validation: ['point_size', 'tick_size', 'pip_size'] },
    volume_min: 0.01, volume_max: 50, volume_step: 0.01, stops_level: 0, freeze_level: 0,
    account_login: 1234567, currency_base: 'XAU', currency_profit: 'USD', currency_margin: 'USD',
  },
}

describe('Manual Nodes — spec workspace (Phase 2)', () => {
  beforeEach(() => {
    delete (globalThis as { __hero1Metadata?: unknown }).__hero1Metadata
  })

  it('loads the bullish engulfing example into the editor', async () => {
    renderNodes()
    fireEvent.click((await screen.findAllByRole('button', { name: /load bullish engulfing example/i }))[0])
    fireEvent.click(screen.getByRole('tab', { name: /json editor/i }))
    const editor = screen.getByLabelText(/specification json/i) as HTMLTextAreaElement
    expect(editor.value).toContain('Bullish Engulfing')
    expect(editor.value).toContain('"next_bar_open"')
    expect(editor.value).toContain('"pips"')
    expect(editor.value).toContain('"direction": "LONG"')
  })

  it('shows NEEDS_METADATA without stored metadata', async () => {
    renderNodes()
    fireEvent.click((await screen.findAllByRole('button', { name: /load bullish engulfing example/i }))[0])
    expect(await screen.findByText(/NOT STORED/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /^validate$/i }))
    expect((await screen.findAllByText('NEEDS_METADATA')).length).toBeGreaterThan(0)
  })

  it('with stored broker metadata: validate VALID, compile, save', async () => {
    ;(globalThis as { __hero1Metadata?: unknown }).__hero1Metadata = STORED_METADATA
    renderNodes()
    fireEvent.click((await screen.findAllByRole('button', { name: /load bullish engulfing example/i }))[0])
    expect(await screen.findByText(/ICMarketsSC-Demo/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /^validate$/i }))
    await waitFor(() => expect(screen.getAllByText('VALID').length).toBeGreaterThan(0))

    fireEvent.click(screen.getByRole('button', { name: /^compile$/i }))
    expect(await screen.findByText(/eb132930ec461848abb54173d50cdeda6f812581e39f7cefac1f4155935a689c/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /save as new spec node/i }))
    expect(await screen.findByRole('status')).toHaveTextContent(/Saved as node #\d+ .+ \(M15\) · fingerprint/i)
  })

  it('compile of a pips-based spec without stored metadata surfaces the 422 as structured issues', async () => {
    renderNodes()
    fireEvent.click((await screen.findAllByRole('button', { name: /load bullish engulfing example/i }))[0])
    fireEvent.click(screen.getByRole('button', { name: /^compile$/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/SPEC_NEEDS_METADATA/i)
  })

  it('Refresh from MT5 shows the exact failure when MT5 is unavailable (never fakes it)', async () => {
    renderNodes()
    expect(await screen.findByText(/NOT STORED/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /refresh from mt5/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/MetaTrader5 package not importable/i)
  })

  it('historical data moved to the DATA tab (no duplicate controls here)', async () => {
    renderNodes()
    expect(await screen.findByText(/Registry · XAUUSD/)).toBeInTheDocument()
    expect(screen.queryByText(/Historical data/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /fetch data/i })).not.toBeInTheDocument()
  })

  it('Direction defaults to BOTH in a blank strategy and is editable in Conditions', async () => {
    renderNodes()
    fireEvent.click((await screen.findAllByRole('button', { name: /new blank strategy/i }))[0])
    fireEvent.click(screen.getByRole('tab', { name: /json editor/i }))
    const editor = screen.getByLabelText(/specification json/i) as HTMLTextAreaElement
    expect(editor.value).toContain('"direction": "BOTH"')
  })
})
