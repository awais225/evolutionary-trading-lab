import { useEffect, useId, useRef, useState } from 'react'
import { SUPPORTED_TIMEFRAMES, summarizeTimeframes } from '../lib/timeframes'

/**
 * Themed MULTI-select timeframe picker — graphical-tier dropdown (never a native
 * <select>/OS popup). Built on the same panel language as the instrument listbox.
 *
 * Contract:
 *   - value/onChange are plain string arrays (values restricted to the supported
 *     registry — the component cannot express an unsupported timeframe)
 *   - Select All / Clear All / per-item toggle / selected count / readable summary
 *   - keyboard: ArrowUp/Down (open + move), Space/Enter (toggle), Home/End,
 *     Escape (close), Tab (close); outside-mousedown closes
 *   - `invalid` marks the trigger + message REQUIRED for the "no timeframe" state
 */
export function TimeframeMultiSelect({
  value, onChange, id, label, invalid, hint,
}: {
  value: string[]
  onChange: (next: string[]) => void
  id?: string
  label?: string
  invalid?: boolean
  hint?: string
}) {
  const [open, setOpen] = useState(false)
  const [activeIdx, setActiveIdx] = useState(-1)
  const wrapRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const listId = useId()
  const triggerId = id ?? useId()
  const labelId = useId()

  const tfs = SUPPORTED_TIMEFRAMES as readonly string[]
  const selected = new Set(value.map((v) => v.toUpperCase()))
  const activeList = tfs // active navigation covers every option

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

  const toggle = (tf: string) => {
    const next = new Set(selected)
    if (next.has(tf)) next.delete(tf); else next.add(tf)
    onChange(tfs.filter((t) => next.has(t)))
  }
  const openMenu = (focusIdx?: number) => {
    setOpen(true)
    setActiveIdx(focusIdx ?? Math.max(0, tfs.findIndex((t) => selected.has(t))))
  }
  const closeMenu = (refocus = true) => {
    setOpen(false)
    if (refocus) triggerRef.current?.focus()
  }
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!open) {
      if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openMenu() }
      else if (e.key === 'ArrowUp') { e.preventDefault(); openMenu(tfs.length - 1) }
      return
    }
    switch (e.key) {
      case 'ArrowDown': e.preventDefault(); setActiveIdx((i) => (i + 1 + tfs.length) % tfs.length); break
      case 'ArrowUp': e.preventDefault(); setActiveIdx((i) => (i - 1 + tfs.length) % tfs.length); break
      case 'Home': e.preventDefault(); setActiveIdx(0); break
      case 'End': e.preventDefault(); setActiveIdx(tfs.length - 1); break
      case 'Enter':
      case ' ': e.preventDefault(); if (activeIdx >= 0) toggle(activeList[activeIdx]); break
      case 'Escape': e.preventDefault(); closeMenu(); break
      case 'Tab': setOpen(false); break
      default: break
    }
  }

  const count = selected.size
  return (
    <div ref={wrapRef} className={`tf-multi${open ? ' open' : ''}${invalid ? ' invalid' : ''}`}
         onKeyDown={onKeyDown}>
      {label && <span className="tf-multi-label" id={labelId}>{label}</span>}
      <button ref={triggerRef} id={triggerId} type="button" className="tf-trigger"
              aria-haspopup="listbox" aria-expanded={open} aria-controls={open ? listId : undefined}
              aria-labelledby={label ? `${labelId} ${triggerId}` : undefined}
              aria-label={label ? undefined : `Timeframes — ${count} selected`}
              aria-invalid={invalid || undefined}
              onClick={() => (open ? closeMenu(false) : openMenu())}>
        <span className="tf-trigger-value">{count === 0 ? 'Select timeframes…' : summarizeTimeframes(tfs.filter((t) => selected.has(t)))}</span>
        <span className="tf-trigger-count scope-count" aria-hidden="true">{count}</span>
        <svg className="instrument-chevron" viewBox="0 0 12 12" aria-hidden="true"
             fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
          <path d="M2.5 4.5L6 8l3.5-3.5" />
        </svg>
      </button>
      {open && (
        <div className="instrument-panel tf-panel" role="listbox" id={listId}
             aria-multiselectable="true" aria-labelledby={label ? labelId : triggerId}>
          <div className="tf-panel-actions">
            <button type="button" className="tf-action" onClick={() => onChange([...tfs])}>Select All</button>
            <button type="button" className="tf-action" onClick={() => onChange([])}>Clear All</button>
            <span className="faint" style={{ marginLeft: 'auto', fontSize: 10.5 }}>{count} of {tfs.length} selected</span>
          </div>
          {tfs.map((tf, i) => {
            const checked = selected.has(tf)
            const active = i === activeIdx
            return (
              <div key={tf} id={`${listId}-opt-${i}`} role="option" aria-selected={checked}
                   className={`instrument-option tf-option${checked ? ' selected' : ''}${active ? ' active' : ''}`}
                   onMouseEnter={() => setActiveIdx(i)}
                   onClick={() => toggle(tf)}>
                <span className="tf-check" aria-hidden="true">
                  {checked && (
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6"
                         strokeLinecap="round" strokeLinejoin="round"><path d="M4.5 12.5l5 5L19.5 6.5" /></svg>
                  )}
                </span>
                <span className="instrument-option-symbol">{tf}</span>
              </div>
            )
          })}
        </div>
      )}
      {hint && !invalid && <div className="faint" style={{ fontSize: 10.5, marginTop: 5 }}>{hint}</div>}
      {invalid && <div className="field-error" role="alert" style={{ marginTop: 5 }}>Select at least one timeframe — one node will be created per timeframe.</div>}
    </div>
  )
}
