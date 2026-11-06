import { useEffect, useId, useRef, useState } from 'react'
import { useInstrument, EXAMPLE_UNSUPPORTED } from '../state/instrument'

interface Option {
  symbol: string
  detail: string
  disabled: boolean
}

/**
 * Global instrument selector — themed custom listbox (replaces the native
 * <select>; no OS dropdown popup is ever rendered).
 * Data & persistence are IDENTICAL to the previous implementation and are owned
 * by state/instrument.tsx (registry fetch, hero1.instrument localStorage,
 * XAUUSD default, disabled unavailable options). This component is visual +
 * keyboard/a11y only.
 *
 * Pattern: button trigger (focus lives here) + role=listbox with
 * aria-activedescendant tracking the active option; Escape/Tab/outside-click close.
 */
export function InstrumentSelect() {
  const { instrument, setInstrument, instruments, instrumentsSource } = useInstrument()
  const [open, setOpen] = useState(false)
  const [activeIdx, setActiveIdx] = useState(-1)
  const wrapRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const labelId = useId()
  const listboxId = useId()

  const known = new Set(instruments.map((i) => i.symbol))
  const enabled = instruments.filter((i) => i.enabled)
  const disabled = instruments.filter((i) => !i.enabled)
  const unsupported = EXAMPLE_UNSUPPORTED.filter((s) => !known.has(s))

  const options: Option[] = [
    ...enabled.map((i): Option => ({ symbol: i.symbol, detail: i.display_name, disabled: false })),
    ...disabled.map((i): Option => ({ symbol: i.symbol, detail: 'unavailable', disabled: true })),
    ...unsupported.map((s): Option => ({ symbol: s, detail: 'not in registry', disabled: true })),
  ]
  const enabledIdxs = options.map((o, i) => (o.disabled ? -1 : i)).filter((i) => i >= 0)
  const group1Start = 0
  const group2Start = enabled.length
  const currentIdx = options.findIndex((o) => !o.disabled && o.symbol === instrument)

  // close on outside click / Escape (document-level so focus can stay on the trigger)
  useEffect(() => {
    if (!open) return
    const onMouseDown = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { setOpen(false); triggerRef.current?.focus() }
    }
    document.addEventListener('mousedown', onMouseDown)
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('mousedown', onMouseDown); document.removeEventListener('keydown', onKey) }
  }, [open])

  const openMenu = (focus?: number) => {
    setOpen(true)
    setActiveIdx(focus ?? (currentIdx >= 0 ? currentIdx : (enabledIdxs[0] ?? -1)))
  }
  const closeMenu = (refocus = true) => {
    setOpen(false)
    if (refocus) triggerRef.current?.focus()
  }
  const select = (idx: number) => {
    const o = options[idx]
    if (!o || o.disabled) return
    setInstrument(o.symbol)
    closeMenu()
  }
  const move = (dir: 1 | -1) => {
    if (enabledIdxs.length === 0) return
    const pos = enabledIdxs.indexOf(activeIdx)
    const nextPos = pos === -1 ? (dir === 1 ? 0 : enabledIdxs.length - 1)
      : (pos + dir + enabledIdxs.length) % enabledIdxs.length
    setActiveIdx(enabledIdxs[nextPos])
  }
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!open) {
      if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openMenu() }
      else if (e.key === 'ArrowUp') { e.preventDefault(); openMenu(enabledIdxs[enabledIdxs.length - 1]) }
      return
    }
    switch (e.key) {
      case 'ArrowDown': e.preventDefault(); move(1); break
      case 'ArrowUp': e.preventDefault(); move(-1); break
      case 'Home': e.preventDefault(); if (enabledIdxs.length) setActiveIdx(enabledIdxs[0]); break
      case 'End': e.preventDefault(); if (enabledIdxs.length) setActiveIdx(enabledIdxs[enabledIdxs.length - 1]); break
      case 'Enter':
      case ' ': e.preventDefault(); if (activeIdx >= 0) select(activeIdx); break
      case 'Escape': e.preventDefault(); closeMenu(); break
      case 'Tab': setOpen(false); break
      default: break
    }
  }

  const renderOption = (o: Option, idx: number) => {
    const selected = idx === currentIdx
    const active = idx === activeIdx
    return (
      <div key={`${o.symbol}-${idx}`}
           id={`${listboxId}-opt-${idx}`}
           role="option"
           aria-selected={selected}
           aria-disabled={o.disabled || undefined}
           className={`instrument-option${selected ? ' selected' : ''}${active ? ' active' : ''}${o.disabled ? ' disabled' : ''}`}
           onMouseEnter={() => { if (!o.disabled) setActiveIdx(idx) }}
           onClick={() => select(idx)}>
        <span className="instrument-option-symbol">{o.symbol}</span>
        <span className="instrument-option-detail">{o.detail}</span>
        {selected && (
          <svg className="instrument-option-check" viewBox="0 0 24 24" aria-hidden="true"
               fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
            <path d="M4.5 12.5l5 5L19.5 6.5" />
          </svg>
        )}
      </div>
    )
  }

  return (
    <div ref={wrapRef} className={`instrument-select-wrap${open ? ' open' : ''}`}
         title={instrumentsSource === 'backend'
           ? 'Instrument — list from the backend registry (GET /api/v1/instruments). Persists in this browser.'
           : 'Instrument — backend registry unreachable; showing the configured seeded set. Persists in this browser.'}
         onKeyDown={onKeyDown}>
      <span className="instrument-select-label" id={labelId}>INSTRUMENT</span>
      <button ref={triggerRef} type="button" className="instrument-trigger"
              aria-haspopup="listbox" aria-expanded={open} aria-controls={open ? listboxId : undefined}
              aria-label={`Instrument — current: ${instrument}`}
              onClick={() => (open ? closeMenu(false) : openMenu())}>
        <span className="instrument-trigger-value">{instrument}</span>
        <svg className="instrument-chevron" viewBox="0 0 12 12" aria-hidden="true"
             fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
          <path d="M2.5 4.5L6 8l3.5-3.5" />
        </svg>
      </button>
      {open && (
        <div className="instrument-panel" role="listbox" id={listboxId}
             aria-labelledby={labelId}
             aria-activedescendant={activeIdx >= 0 ? `${listboxId}-opt-${activeIdx}` : undefined}>
          <div className="instrument-panel-group" role="presentation">Available (backend registry)</div>
          {options.slice(group1Start, group2Start).map((o, i) => renderOption(o, group1Start + i))}
          {options.length > group2Start && (
            <>
              <div className="instrument-panel-group" role="presentation">Not available / not in registry</div>
              {options.slice(group2Start).map((o, i) => renderOption(o, group2Start + i))}
            </>
          )}
        </div>
      )}
    </div>
  )
}
