import type { ReactNode } from 'react'

export function Placeholder({ icon, title, blurb, chips, phase }: {
  icon: ReactNode
  title: string
  blurb: string
  chips: string[]
  phase: string
}) {
  return (
    <div className="placeholder" role="status">
      <svg className="placeholder-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        {icon}
      </svg>
      <h3>{title}</h3>
      <p>{blurb}</p>
      <div className="placeholder-list">{chips.map((c) => <span key={c}>{c}</span>)}</div>
      <div className="phase-chip">{phase}</div>
    </div>
  )
}
