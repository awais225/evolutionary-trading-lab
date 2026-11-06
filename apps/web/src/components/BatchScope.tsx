import { useEffect, useState } from 'react'
import { useInstrument } from '../state/instrument'
import { useStarredScope } from '../state/starScope'
import { useTimeframeScope } from '../state/timeframeScope'
import { api } from '../api/client'
import { StarredScopeControl } from './StarredScopeControl'
import { TimeframeMultiSelect } from './TimeframeMultiSelect'
import { SUPPORTED_TIMEFRAMES } from '../lib/timeframes'

/**
 * Live batch-scope panel for the testing tabs (DeepTesting / Fine Tuning / Live
 * Testing). Phase 4/5/8 execution does not exist yet — the SCOPE is real and
 * server-enforced: counts come from this instrument's registry filtered by the
 * starred scope AND the shared timeframe selection (the same filter Manual Nodes
 * uses). The batch runners will consume exactly this scope when they ship.
 */
export function BatchScope() {
  const { instrument } = useInstrument()
  const { scope } = useStarredScope()
  const tfScope = useTimeframeScope()
  const [counts, setCounts] = useState<{ total: number; starred: number; filtered: number } | null>(null)
  const [error, setError] = useState<string | null>(null)

  const tfKey = tfScope.timeframes.join(',')
  useEffect(() => {
    let cancelled = false
    setCounts(null); setError(null)
    api.nodes.list(instrument, {
      starred: scope === 'starred' ? true : undefined,
      timeframe: tfScope.timeframes.length ? tfScope.timeframes : undefined,
    })
      .then((res) => { if (!cancelled) setCounts({ total: res.total, starred: res.starred_total, filtered: res.filtered_total }) })
      .catch(() => { if (!cancelled) setError('registry unavailable — counts cannot be shown') })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [instrument, scope, tfKey])

  const tfSummary = tfScope.timeframes.length === 0 ? 'all timeframes' : tfScope.timeframes.join(' · ')
  const scopeBits = `${scope === 'starred' ? 'starred' : 'all'} nodes · ${tfSummary}`

  return (
    <div className="card" style={{ marginTop: 16, maxWidth: 560 }}>
      <div className="card-title">Batch Node Scope · {instrument}</div>
      <div style={{ display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap' }}>
        <StarredScopeControl mini total={counts?.total ?? null} starredTotal={counts?.starred ?? null} />
        <span className="faint mono" style={{ fontSize: 10.5 }}>
          {counts ? `${counts.filtered} of ${counts.total} in scope` : '…'}
        </span>
      </div>
      <div style={{ marginTop: 10 }}>
        <TimeframeMultiSelect id="batch-tf-scope"
                              value={tfScope.timeframes}
                              onChange={tfScope.setTimeframes}
                              label="TIMEFRAMES IN SCOPE"
                              hint="Shared with Manual Nodes — the server enforces both filters (starred + timeframe); Select All will mean exactly this visible set." />
      </div>
      {error && <div className="field-error" role="alert" style={{ marginTop: 6 }}>{error}</div>}
      <p className="scope-hint">
        {scope === 'starred'
          ? <>Only <strong style={{ color: 'var(--accent-strong)' }}>starred</strong> nodes ({scopeBits}) will be selectable when batch runs arrive.</>
          : <>All <strong>{counts?.filtered ?? '…'}</strong> nodes ({scopeBits}) will be selectable when batch runs arrive.</>}
        <br />Execution itself is a later phase; this scope is enforced by the server-side node filter, not just the UI.
      </p>
      <p className="faint" style={{ fontSize: 10, marginTop: 6 }}>Supported timeframes (registry): {SUPPORTED_TIMEFRAMES.join(' · ')}. W1/MN1 are not supported by the spec compiler and are deliberately not selectable.</p>
    </div>
  )
}
