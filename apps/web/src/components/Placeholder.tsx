import type { ReactNode } from 'react'
import { useInstrument } from '../state/instrument'

export function Placeholder({ icon, title, blurb, chips, phase }: {
  icon: ReactNode
  title: string
  blurb: string
  chips: string[]
  phase: string
}) {
  const { instrument } = useInstrument()
  return (
    <div className="placeholder" role="status">
      <svg className="placeholder-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        {icon}
      </svg>
      <h3>{title}</h3>
      <p>{blurb}</p>
      <div className="placeholder-list">{chips.map((c) => <span key={c}>{c}</span>)}</div>
      <div className="phase-chip">{phase}</div>
      <div className="placeholder-instrument">Strategy context instrument: <span className="mono">{instrument}</span> — switch in the top bar; operations here will always run on it.</div>
    </div>
  )
}
