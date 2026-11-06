import { useEffect, useState } from 'react'
import { useInstrument, EXAMPLE_UNSUPPORTED } from '../state/instrument'
import { useFeed } from '../state/feed'
import { api } from '../api/client'
import type { HealthOut } from '../api/types'

function Pill({ label, status }: { label: string; status?: string }) {
  const cls = status === 'healthy' || status === 'ok' ? 'ok' : status === 'unavailable' || status === 'not_detectable' || status === 'unconfigured' ? 'warn' : status === 'error' ? 'err' : 'warn'
  return <span className="status-pill hide-sm"><span className={`dot ${cls}`} />{label}</span>
}

export function TopBar({ onMenu }: { onMenu: () => void }) {
  const { instrument, setInstrument, instruments, instrumentsSource, instrumentsLoading } = useInstrument()
  const { open, toggle } = useFeed()
  const [health, setHealth] = useState<HealthOut | null>(null)
  const [down, setDown] = useState(false)

  useEffect(() => {
    let alive = true
    const poll = async () => {
      try {
        const h = await api.health()
        if (alive) { setHealth(h); setDown(false) }
      } catch {
        if (alive) setDown(true)
      }
    }
    poll()
    const t = window.setInterval(poll, 20000)
    return () => { alive = false; window.clearInterval(t) }
  }, [])

  const known = new Set(instruments.map((i) => i.symbol))
  const enabled = instruments.filter((i) => i.enabled)
  const disabled = instruments.filter((i) => !i.enabled)
  // Illustrative symbols that the registry does NOT enable — shown greyed out so the
  // selector never pretends they are tradable. Never selectable.
  const unsupported = EXAMPLE_UNSUPPORTED.filter((s) => !known.has(s))
  const selectionEnabled = enabled.some((i) => i.symbol === instrument)

  return (
    <header className="topbar">
      <button className="btn btn-ghost btn-sm" onClick={onMenu} aria-label="Toggle navigation">☰</button>
      <div className="topbar-spacer" />
      <div className="status-cluster" aria-label="System status and instrument">
        <label className="instrument-select-wrap" title={
          instrumentsSource === 'backend'
            ? 'Instrument — list from the backend registry (GET /api/v1/instruments). Persists in this browser.'
            : 'Instrument — backend registry unreachable; showing the configured seeded set. Persists in this browser.'
        }>
          <span className="instrument-select-label">INSTRUMENT</span>
          <select className="instrument-select" aria-label="Instrument"
                  value={instrument}
                  onChange={(e) => setInstrument(e.target.value)}>
            <optgroup label="Available (backend registry)">
              {enabled.map((i) => (
                <option key={i.symbol} value={i.symbol}>{i.symbol} — {i.display_name}</option>
              ))}
            </optgroup>
            {disabled.length + unsupported.length > 0 && (
              <optgroup label="Not available / not in registry">
                {disabled.map((i) => (
                  <option key={i.symbol} value={i.symbol} disabled>{i.symbol} — unavailable</option>
                ))}
                {unsupported.map((s) => (
                  <option key={s} value={s} disabled>{s} — not in registry</option>
                ))}
              </optgroup>
            )}
          </select>
        </label>
        {!selectionEnabled && !instrumentsLoading && (
          <span className="instrument-warn" role="status">selected instrument unavailable — operations will be rejected by the backend</span>
        )}
        {down ? <Pill label="BACKEND OFF" status="error" /> : <Pill label="BACKEND" status={health?.checks.registry_db?.status} />}
        <Pill label="OLLAMA" status={health?.checks.ollama?.status} />
        <Pill label="MT5" status={health?.checks.mt5?.status} />
      </div>
      <button className="feed-toggle" aria-pressed={open} onClick={toggle} aria-label="Toggle live feed">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round"><path d="M4 10v4M9 7v10M14 4v16M19 10v4" /></svg>
        LIVE FEED
      </button>
    </header>
  )
}
