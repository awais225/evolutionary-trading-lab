import { useCallback, useEffect, useState } from 'react'
import { useInstrument } from '../state/instrument'
import { api, ApiError } from '../api/client'
import type { Node, NodeOrigin } from '../api/types'

const ORIGINS: NodeOrigin[] = ['CUSTOM', 'WWW', 'INTERACTIVE', 'FINE_TUNED']

export function ManualNodes() {
  const { instrument } = useInstrument()
  const [nodes, setNodes] = useState<Node[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [origin, setOrigin] = useState<NodeOrigin>('CUSTOM')
  const [description, setDescription] = useState('')
  const [creating, setCreating] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await api.nodes.list(instrument)
      setNodes(res.items)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'failed to load nodes')
    } finally {
      setLoading(false)
    }
  }, [instrument])

  useEffect(() => { load() }, [load])

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    const trimmed = name.trim()
    if (!trimmed) { setFormError('Name is required.'); return }
    setCreating(true)
    setFormError(null)
    try {
      await api.nodes.create(instrument, { name: trimmed, origin, description: description.trim() || undefined })
      setName('')
      setDescription('')
      await load()
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : 'creation failed')
    } finally {
      setCreating(false)
    }
  }

  return (
    <div>
      <div className="page-head">
        <div className="page-eyebrow">Builder · {instrument}</div>
        <h1 className="page-title">Manual Nodes</h1>
        <p className="page-sub">Foundational node registry for {instrument}. Visual strategy building, natural-language generation and the validated code editor arrive in Phase 2 — this phase wires durable storage, origin taxonomy and the global feed.</p>
      </div>

      <div className="banner" role="note">
        <span aria-hidden="true">ⓘ</span>
        <span><strong>Phase 1 scope.</strong> Nodes created here are persisted drafts (origin-tagged, instrument-isolated). No strategy compiler exists yet — <span className="mono">spec_json</span> is validated only as JSON. Live approval is a separate record type and cannot be granted from this screen.</span>
      </div>

      <div className="panel-grid">
        <form className="card" onSubmit={submit}>
          <div className="card-title">Create node draft</div>
          <div className="field">
            <label htmlFor="node-name">Name</label>
            <input id="node-name" className="input" value={name} maxLength={160}
                   onChange={(e) => setName(e.target.value)} placeholder="e.g. M15 EMA cross · London session" />
          </div>
          <div className="field" style={{ marginTop: 12 }}>
            <label htmlFor="node-origin">Origin</label>
            <select id="node-origin" className="input" value={origin} onChange={(e) => setOrigin(e.target.value as NodeOrigin)}>
              {ORIGINS.map((o) => <option key={o} value={o}>{o}</option>)}
            </select>
          </div>
          <div className="field" style={{ marginTop: 12 }}>
            <label htmlFor="node-desc">Description <span className="faint">(optional)</span></label>
            <textarea id="node-desc" className="input" value={description} maxLength={4000}
                      onChange={(e) => setDescription(e.target.value)} placeholder="What is this strategy idea?" />
          </div>
          {formError && <div className="field-error" role="alert" style={{ marginTop: 10 }}>{formError}</div>}
          <button className="btn btn-primary" type="submit" disabled={creating} style={{ marginTop: 14 }}>
            {creating ? 'Creating…' : 'Create node'}
          </button>
        </form>

        <div className="card">
          <div className="card-title">
            Registry · {instrument}
            <button className="btn btn-ghost btn-sm" onClick={load} style={{ marginLeft: 'auto' }} aria-label="Refresh">↻ Refresh</button>
          </div>
          {loading ? (
            <div className="state"><div className="spinner" /><div className="state-title">Loading nodes…</div></div>
          ) : error ? (
            <div className="state error"><div className="state-title">Backend unavailable</div>{error}<div style={{ marginTop: 12 }}><button className="btn btn-sm" onClick={load}>Retry</button></div></div>
          ) : nodes.length === 0 ? (
            <div className="state"><div className="state-title">No nodes yet for {instrument}</div>Create your first draft — it stays inside this instrument’s database.</div>
          ) : (
            <div style={{ overflowX: 'auto' }}>
              <table className="node-table">
                <thead><tr><th>ID</th><th>Name</th><th>Origin</th><th>Status</th><th>Approval</th></tr></thead>
                <tbody>
                  {nodes.map((n) => (
                    <tr key={n.id}>
                      <td className="mono">#{n.id}</td>
                      <td>{n.name}{n.description && <div className="row-desc">{n.description}</div>}</td>
                      <td><span className={`badge ${n.origin}`}>{n.origin}</span></td>
                      <td><span className="badge DRAFT">{n.status}</span></td>
                      <td><span className={`badge ${n.approval_state === 'APPROVED' ? 'APPROVED' : 'PENDING'}`}>{n.approval_state}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
