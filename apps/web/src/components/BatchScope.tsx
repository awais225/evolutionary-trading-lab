import { useEffect, useState } from 'react'
import { useInstrument } from '../state/instrument'
import { useStarredScope } from '../state/starScope'
import { api } from '../api/client'
import { StarredScopeControl } from './StarredScopeControl'

/**
 * Live batch-scope panel for the testing tabs (DeepTesting / Fine Tuning / Live
 * Testing). Phase 4/5/8 execution does not exist yet, so there is nothing to
 * run — but the SCOPE is real: counts come from this instrument's registry via
 * the server-enforced starred filter, the choice persists across tabs, and the
 * batch runners will consume exactly this scope when they ship.
 */
export function BatchScope() {
  const { instrument } = useInstrument()
  const { scope } = useStarredScope()
  const [counts, setCounts] = useState<{ total: number; starred: number } | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setCounts(null); setError(null)
    api.nodes.list(instrument, { starred: true })
      .then((res) => { if (!cancelled) setCounts({ total: res.total, starred: res.starred_total }) })
      .catch(() => { if (!cancelled) setError('registry unavailable — counts cannot be shown') })
    return () => { cancelled = true }
  }, [instrument])

  return (
    <div className="card" style={{ marginTop: 16, maxWidth: 560 }}>
      <div className="card-title">Batch Node Scope · {instrument}</div>
      <StarredScopeControl mini total={counts?.total ?? null} starredTotal={counts?.starred ?? null} />
      {error && <div className="field-error" role="alert" style={{ marginTop: 6 }}>{error}</div>}
      <p className="scope-hint">
        {scope === 'starred'
          ? <>Only <strong style={{ color: 'var(--accent-strong)' }}>starred</strong> nodes will be selectable when batch runs arrive — Select All will mean exactly the starred set above.</>
          : <>All nodes are selectable when batch runs arrive — Select All will mean all <strong>{counts?.total ?? '…'}</strong> nodes in this instrument.</>}
        <br />Execution itself is a later phase; this scope is enforced by the server-side node filter, not just the UI.
      </p>
    </div>
  )
}
