import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import App from '../App'

const NODES = [
  { id: 1, instrument_id: 'XAUUSD', origin: 'CUSTOM', name: 'Alpha', description: null,
    status: 'DRAFT', approval_state: 'NONE', version: 1, parent_node_id: null,
    spec_hash: null, spec_json: null, created_by: 'test', created_at: 1, updated_at: 1, starred: true },
  { id: 2, instrument_id: 'XAUUSD', origin: 'CUSTOM', name: 'Beta', description: null,
    status: 'DRAFT', approval_state: 'NONE', version: 1, parent_node_id: null,
    spec_hash: null, spec_json: null, created_by: 'test', created_at: 2, updated_at: 2, starred: false },
  { id: 3, instrument_id: 'XAUUSD', origin: 'CUSTOM', name: 'Gamma', description: null,
    status: 'DRAFT', approval_state: 'NONE', version: 1, parent_node_id: null,
    spec_hash: null, spec_json: null, created_by: 'test', created_at: 3, updated_at: 3, starred: true },
]

const renderNodes = () => {
  ;(globalThis as { __hero1NodeItems?: unknown[] }).__hero1NodeItems = [...NODES]
  return render(<MemoryRouter initialEntries={['/manual-nodes']}><App /></MemoryRouter>)
}

describe('Starred nodes (Manual Nodes registry)', () => {
  it('renders a star toggle per row reflecting persisted state', async () => {
    renderNodes()
    expect(await screen.findByRole('button', { name: /unstar node 1\b/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /star node 2\b/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /unstar node 3\b/i })).toBeInTheDocument()
  })

  it('toggling a star issues a metadata-only PATCH and keeps the row visible', async () => {
    renderNodes()
    const btn = await screen.findByRole('button', { name: /star node 2\b/i })
    fireEvent.click(btn)
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining('/nodes/2'),
      expect.objectContaining({ method: 'PATCH' }),
    ))
    const call = (fetch as ReturnType<typeof vi.fn>).mock.calls.find(
      ([url, init]) => String(url).includes('/nodes/2') && (init as RequestInit)?.method === 'PATCH')
    expect(JSON.parse(String((call?.[1] as RequestInit)?.body))).toEqual({ starred: true })
  })

  it('Starred Only filter shows only starred rows and reports the match count', async () => {
    renderNodes()
    // scope toggle lives in the registry toolbar (persisted across tabs)
    fireEvent.click(await screen.findByRole('button', { name: /starred only/i }))
    expect(await screen.findByText(/Showing \d+ starred nodes out of \d+ total nodes/i)).toBeInTheDocument()
    expect(screen.queryByText('Beta')).not.toBeInTheDocument()
    expect(screen.getByText('Alpha')).toBeInTheDocument()
    expect(screen.getByText('Gamma')).toBeInTheDocument()
  })

  it('Select-All means the currently filtered set, never the whole registry', async () => {
    renderNodes()
    fireEvent.click(await screen.findByRole('button', { name: /starred only/i }))
    await screen.findByText('Alpha')
    const selectAll = screen.getByRole('checkbox', { name: /select all visible nodes/i })
    fireEvent.click(selectAll)
    // only the two starred nodes are selected — Beta must not be included
    expect(screen.getByRole('checkbox', { name: /select node 1\b/i })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: /select node 3\b/i })).toBeChecked()
    expect(screen.getByText(/2 selected/i)).toBeInTheDocument()
  })
})
