import { useEffect, useState } from 'react'
import { useInstrument } from '../state/instrument'
import { useFeed } from '../state/feed'
import { api } from '../api/client'
import type { HealthOut } from '../api/types'
import { InstrumentSelect } from './InstrumentSelect'

function Pill({ label, status }: { label: string; status?: string }) {
  const cls = status === 'healthy' || status === 'ok' ? 'ok' : status === 'unavailable' || status === 'not_detectable' || status === 'unconfigured' ? 'warn' : status === 'error' ? 'err' : 'warn'
  return <span className="status-pill hide-sm"><span className={`dot ${cls}`} />{label}</span>
}

export function TopBar({ onMenu }: { onMenu: () => void }) {
  const { instrument, instruments, instrumentsLoading } = useInstrument()
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

  // Tab-local: is the globally selected instrument currently enabled in the registry?
  const selectionEnabled = instruments.some((i) => i.symbol === instrument && i.enabled)

  return (
    <header className="topbar">
      <button className="btn btn-ghost btn-sm" onClick={onMenu} aria-label="Toggle navigation">☰</button>
      <div className="topbar-spacer" />
      <div className="status-cluster" aria-label="System status and instrument">
        <InstrumentSelect />
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
