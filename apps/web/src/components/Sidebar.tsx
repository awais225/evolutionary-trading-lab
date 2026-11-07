import { NavLink } from 'react-router-dom'

const ITEMS = [
  { to: '/manual-nodes', label: 'Manual Nodes', phase: 'P1', icon: 'M4 6h16M4 12h10M4 18h16' },
  { to: '/data', label: 'DATA', phase: 'P4', icon: 'M4 5h16M4 10h16M4 15h16M4 20h10' },
  { to: '/www-research', label: 'WWW Research', phase: 'P7', icon: 'M12 3a9 9 0 019 9M12 7a5 5 0 015 5M3 12h6m2.5-7L9 12l2.5 7' },
  { to: '/interactive-www', label: 'Interactive WWW', phase: 'P7', icon: 'M8 10h.01M12 10h.01M16 10h.01M21 12a8 8 0 01-11.6 7.1L4 21l1.9-5.4A8 8 0 1121 12z' },
  { to: '/deeptesting', label: 'DeepTesting', phase: 'P4', icon: 'M4 5h16M4 10h16M4 15h10M4 20h7' },
  { to: '/fine-tuning', label: 'Fine Tuning', phase: 'P8', icon: 'M4 17l6-6-6-6M12 17h8' },
  { to: '/live-testing', label: 'Live Testing', phase: 'P5', icon: 'M13 2L4.5 13.5H11L10 22l8.5-11.5H12L13 2z' },
  { to: '/log', label: 'LOG', phase: 'P6', icon: 'M7 8h13M7 12h13M7 16h7M3 8h.01M3 12h.01M3 16h.01' },
  { to: '/settings', label: 'SETTINGS', phase: 'P1', icon: 'M12 15a3 3 0 100-6 3 3 0 000 6z M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 11-4 0v-.09a1.65 1.65 0 00-1-1.51 1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 11-2.83-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 110-4h.09a1.65 1.65 0 001.51-1 1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 112.83-2.83l.06.06a1.65 1.65 0 001.82.33h0a1.65 1.65 0 001-1.51V3a2 2 0 114 0v.09a1.65 1.65 0 001 1.51h0a1.65 1.65 0 001.82-.33l.06-.06a2 2 0 112.83 2.83l-.06.06a1.65 1.65 0 00-.33 1.82v0a1.65 1.65 0 001.51 1H21a2 2 0 110 4h-.09a1.65 1.65 0 00-1.51 1z' },
] as const

export function Sidebar({ open }: { open: boolean }) {
  return (
    <aside className={`sidebar${open ? ' open' : ''}`} aria-label="Primary navigation">
      <div className="brand">
        <svg className="brand-mark" viewBox="0 0 32 32" aria-hidden="true">
          <path fill="#e6b93d" d="M16 1l13 7.5v15L16 31 3 23.5v-15L16 1z" />
          <path fill="#0d1117" d="M11.5 9h3.4v14h-3.4zM17.1 9h3.4v14h-3.4z" />
        </svg>
        <div className="brand-text">
          <div className="brand-name">HERO<em>1</em></div>
          <div className="brand-sub">Trading Research Workstation</div>
        </div>
      </div>
      <div className="nav-section">Workspace</div>
      {ITEMS.map((item) => (
        <NavLink key={item.to} to={item.to} className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}>
          <svg viewBox="0 0 24 24" fill="none" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            {item.icon.split('M').filter(Boolean).map((d, i) => <path key={i} d={`M${d}`} />)}
          </svg>
          <span className="nav-label">{item.label}</span>
          <span className="nav-phase">{item.phase}</span>
        </NavLink>
      ))}
      <div className="sidebar-footer">
        <div className="mono">HERO1 v1.0.0 · PHASE 1</div>
        Local-first · Windows-verified path<br />No data leaves this machine
      </div>
    </aside>
  )
}
