import { useStarredScope } from '../state/starScope'

interface Props {
  total: number | null
  starredTotal: number | null
  /** smaller rendering for dense toolbars */
  mini?: boolean
}

/** Consistent All Nodes / Starred Only control (shared across tabs) with the
 *  match count called out explicitly — the operator always knows the batch scope. */
export function StarredScopeControl({ total, starredTotal, mini }: Props) {
  const { scope, setScope } = useStarredScope()
  const t = total ?? 0
  const s = starredTotal ?? 0
  return (
    <div>
      <div className={`segmented${mini ? ' mini' : ''}`} role="group" aria-label="Node scope">
        <button type="button" className={scope === 'all' ? 'active' : ''}
                onClick={() => setScope('all')} aria-pressed={scope === 'all'}>
          All Nodes
        </button>
        <button type="button" className={scope === 'starred' ? 'active' : ''}
                onClick={() => setScope('starred')} aria-pressed={scope === 'starred'}>
          Starred Only{starredTotal != null ? ` (${s})` : ''}
        </button>
      </div>
      <div className="scope-line">
        {scope === 'starred'
          ? `Showing ${s} starred nodes out of ${t} total nodes`
          : `Showing all ${t} nodes · ${s} starred`}
      </div>
    </div>
  )
}
