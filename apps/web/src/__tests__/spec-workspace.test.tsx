import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import App from '../App'

const renderNodes = () => render(<MemoryRouter initialEntries={['/manual-nodes']}><App /></MemoryRouter>)

describe('Manual Nodes — spec workspace (Phase 2)', () => {
  it('loads the bullish engulfing example into the editor', async () => {
    renderNodes()
    fireEvent.click(await screen.findByRole('button', { name: /load bullish engulfing example/i }))
    const editor = screen.getByLabelText(/specification json/i) as HTMLTextAreaElement
    expect(editor.value).toContain('Bullish Engulfing')
    expect(editor.value).toContain('"next_bar_open"')
    expect(editor.value).toContain('"pips"')
  })

  it('validate → compile → save flow renders server verdicts (never fakes them)', async () => {
    renderNodes()
    fireEvent.click(await screen.findByRole('button', { name: /load bullish engulfing example/i }))

    fireEvent.click(screen.getByRole('button', { name: /^validate$/i }))
    expect((await screen.findAllByText('NEEDS_METADATA')).length).toBeGreaterThan(0)

    fireEvent.change(screen.getByLabelText(/pip size/i), { target: { value: '0.1' } })
    fireEvent.click(screen.getByRole('button', { name: /^validate$/i }))
    expect((await screen.findAllByText('VALID')).length).toBeGreaterThan(0)

    fireEvent.click(screen.getByRole('button', { name: /^compile$/i }))
    expect(await screen.findByText(/eb132930ec461848abb54173d50cdeda6f812581e39f7cefac1f4155935a689c/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /save as new spec node/i }))
    expect(await screen.findByRole('status')).toHaveTextContent(/Saved as node #1 · v1/i)
  })

  it('compile of a pips-based spec without broker metadata surfaces the 422 as structured issues', async () => {
    renderNodes()
    fireEvent.click(await screen.findByRole('button', { name: /load bullish engulfing example/i }))
    fireEvent.click(screen.getByRole('button', { name: /^compile$/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/SPEC_NEEDS_METADATA/i)
  })
})
