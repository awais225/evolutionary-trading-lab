import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ApiError, api } from '../api/client'
import type { Node, NodeVersionList } from '../api/types'
import { useTableSort, type SortColumn } from '../lib/tableSort'
import { SortHeader } from './SortHeader'
import { SUPPORTED_TIMEFRAMES } from '../lib/timeframes'
import { useInstrument } from '../state/instrument'
import { logUserAction } from '../lib/opLog'

/** Direction from the node's OWN saved spec (identity.direction). Never inferred. */
export function nodeDirection(node: Node): string | null {
  if (!node.spec_json) return null
  try {
    const spec = JSON.parse(node.spec_json) as Record<string, unknown>
    const identity = (spec.identity ?? {}) as Record<string, unknown>
    const raw = (identity.direction ?? spec.direction) as unknown
    return typeof raw === 'string' && raw ? raw.toUpperCase() : null
  } catch {
    return null
  }
}

export function DirectionBadge({ direction }: { direction: string | null }) {
  if (!direction) {
    return <span className="dir-badge none" title="No direction recorded in the saved spec">— NO DIRECTION</span>
  }
  const cls = direction === 'LONG' ? 'long' : direction === 'SHORT' ? 'short' : 'both'
  return <span className={`dir-badge ${cls}`}>{direction}</span>
}

/**
 * Generated-nodes dashboard (lower section of the DATA tab). Reads the real
 * per-instrument node database through the existing API — names, IDs,
 * timeframe, direction (from each saved spec), status/stage, and the
 * persisted parent/child lineage. Nothing is reconstructed or invented:
 * missing timeframe/direction renders an explicit missing-data state.
 */
export function NodeDashboard() {
  const { instrument } = useInstrument()
  const [nodes, setNodes] = useState<Node[]>([])
  const [total, setTotal] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [tfFilter, setTfFilter] = useState('ALL')
  const [dirFilter, setDirFilter] = useState('ALL')
  const [statusFilter, setStatusFilter] = useState('ALL')

  const [inspectId, setInspectId] = useState<number | null>(null)
  const [lineage, setLineage] = useState<NodeVersionList | null>(null)
  const rowRefs = useRef(new Map<number, HTMLTableRowElement>())

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await api.nodes.list(instrument, { limit: 500 })
      setNodes(res.items)
      setTotal(res.total)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'failed to load nodes')
    } finally {
      setLoading(false)
    }
  }, [instrument])

  useEffect(() => { void load() }, [load])

  const byId = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes])
  const childCounts = useMemo(() => {
    const counts = new Map<number, number>()
    for (const n of nodes) {
      if (n.parent_node_id != null) counts.set(n.parent_node_id, (counts.get(n.parent_node_id) ?? 0) + 1)
    }
    return counts
  }, [nodes])

  const statuses = useMemo(
    () => [...new Set(nodes.map((n) => n.status))].sort(), [nodes])

  const dashColumns: SortColumn<Node>[] = [
    { key: 'id', label: 'ID', type: 'natural', get: (n) => n.id },
    { key: 'name', label: 'Name', type: 'natural', get: (n) => n.name },
    { key: 'timeframe', label: 'TF', type: 'text', get: (n) => n.timeframe },
    { key: 'direction', label: 'Direction', type: 'text', get: (n) => nodeDirection(n) },
    { key: 'status', label: 'Status / stage', type: 'text', get: (n) => n.status },
    { key: 'parent', label: 'Parent', type: 'natural', get: (n) => n.parent_node_id },
    { key: 'children', label: 'Children', type: 'number', get: (n) => childCounts.get(n.id) ?? 0 },
    { key: 'version', label: 'v', type: 'number', get: (n) => n.version },
  ]
  const dashSort = useTableSort<Node>('data-nodes', dashColumns, { key: 'id', dir: 'desc' })

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase()
    const out = nodes.filter((n) => {
      if (tfFilter !== 'ALL' && (n.timeframe ?? '').toUpperCase() !== tfFilter) return false
      if (dirFilter !== 'ALL' && nodeDirection(n) !== dirFilter) return false
      if (statusFilter !== 'ALL' && n.status !== statusFilter) return false
      if (q && !`${n.name} #${n.id}`.toLowerCase().includes(q)) return false
      return true
    })
    return dashSort.sorted(out)
  }, [nodes, search, tfFilter, dirFilter, statusFilter, dashSort])

  const inspected = inspectId != null ? byId.get(inspectId) ?? null : null

  const inspect = async (node: Node) => {
    setInspectId(node.id)
    setLineage(null)
    logUserAction(`user opened node details #${node.id}`, { instrument, subsystem: 'nodes-web' })
    try {
      setLineage(await api.nodes.lineage(instrument, node.id))
    } catch {
      setLineage(null) // lineage is supplementary; the row data already shown stands
    }
    requestAnimationFrame(() => {
      rowRefs.current.get(node.id)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    })
  }

  return (
    <div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 10 }}>
        <span className="mono" style={{ fontSize: 11.5 }}>
          {total ?? nodes.length} node{total === 1 ? '' : 's'} · {instrument}
        </span>
        <button className="btn btn-ghost btn-sm" type="button" style={{ marginLeft: 'auto' }}
                onClick={() => void load()}>↻ Refresh</button>
      </div>

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 10 }}>
        <input className="input" style={{ maxWidth: 220 }} placeholder="Search name or #id…"
               aria-label="Search nodes" value={search}
               onChange={(e) => setSearch(e.target.value)} />
        <div className="tf-chips" role="group" aria-label="Filter nodes by timeframe">
          {['ALL', ...SUPPORTED_TIMEFRAMES].map((tf) => (
            <button key={tf} type="button" className={`tf-chip${tfFilter === tf ? ' on' : ''}`}
                    aria-pressed={tfFilter === tf}
                    onClick={() => {
                      setTfFilter(tf)
                      logUserAction(`node timeframe filter: ${tf}`, { instrument, timeframe: tf, subsystem: 'nodes-web' })
                    }}>{tf}</button>
          ))}
        </div>
        <select className="input" style={{ maxWidth: 170 }} aria-label="Filter by direction"
                value={dirFilter}
                onChange={(e) => {
                  setDirFilter(e.target.value)
                  logUserAction(`node direction filter: ${e.target.value}`, { instrument, subsystem: 'nodes-web' })
                }}>
          <option value="ALL">Direction: all</option>
          <option value="LONG">LONG</option>
          <option value="SHORT">SHORT</option>
          <option value="BOTH">BOTH</option>
        </select>
        <select className="input" style={{ maxWidth: 180 }} aria-label="Filter by status"
                value={statusFilter}
                onChange={(e) => {
                  setStatusFilter(e.target.value)
                  logUserAction(`node status filter: ${e.target.value}`, { instrument, subsystem: 'nodes-web' })
                }}>
          <option value="ALL">Status: all</option>
          {statuses.map((st) => <option key={st} value={st}>{st}</option>)}
        </select>
      </div>

      {loading ? (
        <div className="state"><div className="spinner" /><div className="state-title">Loading nodes…</div></div>
      ) : error ? (
        <div className="state error"><div className="state-title">Backend unavailable</div>{error}
          <div style={{ marginTop: 12 }}><button className="btn btn-sm" onClick={() => void load()}>Retry</button></div></div>
      ) : visible.length === 0 ? (
        <div className="state"><div className="state-title">No nodes match</div>
          {nodes.length === 0
            ? `No nodes saved for ${instrument} yet — create them in Manual Nodes.`
            : 'Adjust the filters above to see the saved nodes.'}</div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table className="node-table" aria-label={`Generated nodes for ${instrument}`}>
            <thead><tr>
              <SortHeader label="ID" columnKey="id" state={dashSort.state} onToggle={dashSort.toggle} />
              <SortHeader label="Name" columnKey="name" state={dashSort.state} onToggle={dashSort.toggle} />
              <th>Instrument</th>
              <SortHeader label="TF" columnKey="timeframe" state={dashSort.state} onToggle={dashSort.toggle} />
              <SortHeader label="Direction" columnKey="direction" state={dashSort.state} onToggle={dashSort.toggle} />
              <SortHeader label="Status / stage" columnKey="status" state={dashSort.state} onToggle={dashSort.toggle} />
              <SortHeader label="Parent" columnKey="parent" state={dashSort.state} onToggle={dashSort.toggle} />
              <SortHeader label="Children" columnKey="children" state={dashSort.state} onToggle={dashSort.toggle} />
              <SortHeader label="v" columnKey="version" state={dashSort.state} onToggle={dashSort.toggle} />
              <th></th>
            </tr></thead>
            <tbody>
              {visible.map((n) => {
                const parent = n.parent_node_id != null ? byId.get(n.parent_node_id) : undefined
                return (
                  <tr key={n.id}
                      ref={(el) => { if (el) rowRefs.current.set(n.id, el); else rowRefs.current.delete(n.id) }}
                      onClick={() => void inspect(n)}
                      style={{ cursor: 'pointer', background: n.id === inspectId ? 'rgba(92,200,255,.06)' : undefined }}>
                    <td className="mono">#{n.id}</td>
                    <td>
                      <span className={`badge ${n.origin}`} style={{ marginRight: 6 }}>{n.origin}</span>
                      {n.name}
                      {n.description && <div className="row-desc">{n.description}</div>}
                    </td>
                    <td className="mono">{instrument}</td>
                    <td>{n.timeframe
                      ? <span className="tf-badge">{n.timeframe}</span>
                      : <span className="tf-badge none" title="No timeframe recorded on this node">— NO TF</span>}</td>
                    <td><DirectionBadge direction={nodeDirection(n)} /></td>
                    <td>
                      <span className="badge DRAFT">{n.status}</span>{' '}
                      <span className={`badge ${n.approval_state === 'APPROVED' ? 'APPROVED' : 'PENDING'}`}>{n.approval_state}</span>
                    </td>
                    <td>{n.parent_node_id != null ? (
                      <button type="button" className="btn btn-ghost btn-sm mono"
                              title="Inspect the parent node"
                              onClick={(e) => { e.stopPropagation(); if (parent) void inspect(parent) }}>
                        {parent ? `${parent.name} (#${parent.id})` : `#${n.parent_node_id}`}
                      </button>
                    ) : <span className="meta-chip ok">ROOT</span>}</td>
                    <td className="mono">{childCounts.get(n.id) ?? 0}</td>
                    <td className="mono">v{n.version}</td>
                    <td><button className="btn btn-ghost btn-sm" type="button"
                                onClick={(e) => { e.stopPropagation(); void inspect(n) }}>
                      {n.id === inspectId ? 'Selected' : 'Inspect'}</button></td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {inspected && (
        <div style={{ borderTop: '1px solid var(--border)', marginTop: 10, paddingTop: 10 }}>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', fontSize: 11.5 }}>
            <strong>#{inspected.id} · v{inspected.version}</strong>
            {inspected.timeframe
              ? <span className="tf-badge">{inspected.timeframe}</span>
              : <span className="tf-badge none">no TF recorded</span>}
            <DirectionBadge direction={nodeDirection(inspected)} />
            <span className="faint">created by {inspected.created_by}</span>
            {inspected.parent_node_id
              ? <span className="faint">derived from #{inspected.parent_node_id}</span>
              : <span className="faint">root / original node</span>}
            <span className="faint">{childCounts.get(inspected.id) ?? 0} child node(s)</span>
          </div>
          {inspected.spec_hash && (
            <div className="mono faint" style={{ fontSize: 10, marginTop: 4, wordBreak: 'break-all' }}>
              sha256 {inspected.spec_hash}</div>
          )}
          {lineage && (
            <div style={{ marginTop: 8 }}>
              <div className="faint" style={{ fontSize: 10.5, letterSpacing: 1, textTransform: 'uppercase' }}>
                Lineage (oldest → newest){lineage.lineage_root_id ? ` · root #${lineage.lineage_root_id}` : ''}</div>
              <ol style={{ margin: '6px 0 0', paddingLeft: 18, fontSize: 11.5 }}>
                {lineage.items.map((n) => (
                  <li key={n.id} className="mono">
                    #{n.id} · v{n.version}{n.parent_node_id ? ` ← #${n.parent_node_id}` : ' (root)'} ·{' '}
                    {n.spec_hash ? `${n.spec_hash.slice(0, 12)}…` : 'no spec'}</li>
                ))}
              </ol>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
