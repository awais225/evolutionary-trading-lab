import { useCallback, useEffect, useMemo, useState } from 'react'
import { useInstrument } from '../state/instrument'
import { api, ApiError } from '../api/client'
import { bullishEngulfingSpec } from '../lib/exampleSpec'
import type { Node, NodeOrigin, ValidationIssue, ValidationReport, CompileResult, NodeVersionList, BrokerMeta } from '../api/types'

const ORIGINS: NodeOrigin[] = ['CUSTOM', 'WWW', 'INTERACTIVE', 'FINE_TUNED']
const TIMEFRAMES = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'] as const

const STATUS_COLOR: Record<string, string> = {
  VALID: 'var(--ok)', WARNING: 'var(--warn)', NEEDS_METADATA: 'var(--info)', INVALID: 'var(--err)',
}
const SEV_COLOR: Record<string, string> = {
  INVALID: 'var(--err)', WARNING: 'var(--warn)', NEEDS_METADATA: 'var(--info)',
}

function IssueRow({ issue }: { issue: ValidationIssue }) {
  return (
    <li style={{ border: '1px solid var(--border)', borderRadius: 8, padding: '8px 10px', listStyle: 'none' }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={{ color: SEV_COLOR[issue.severity] ?? 'var(--muted)', fontWeight: 800, fontSize: 9, letterSpacing: 1.2 }}>{issue.severity}</span>
        <code className="mono" style={{ fontSize: 11 }}>{issue.code}</code>
        <code className="mono faint" style={{ fontSize: 10.5 }}>{issue.path}</code>
      </div>
      <div style={{ fontSize: 12, marginTop: 4 }}>{issue.message}</div>
      {issue.remediation && <div className="faint" style={{ fontSize: 11, marginTop: 3 }}>Fix: {issue.remediation}</div>}
    </li>
  )
}

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

  // ---- Phase 2: strategy spec workspace state
  const [specText, setSpecText] = useState('')
  const [timeframe, setTimeframe] = useState<string>('M15')
  const [nodeName, setNodeName] = useState('')
  const [pipSize, setPipSize] = useState('')
  const [tickSize, setTickSize] = useState('')
  const [pointSize, setPointSize] = useState('')
  const [busy, setBusy] = useState<null | 'validate' | 'compile' | 'save' | 'version'>(null)
  const [specParseError, setSpecParseError] = useState<string | null>(null)
  const [validation, setValidation] = useState<ValidationReport | null>(null)
  const [compiled, setCompiled] = useState<CompileResult | null>(null)
  const [actionError, setActionError] = useState<{ message: string; code?: string; issues?: ValidationIssue[] } | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [lineage, setLineage] = useState<NodeVersionList | null>(null)

  const selected = useMemo(() => nodes.find((n) => n.id === selectedId) ?? null, [nodes, selectedId])

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
  useEffect(() => { setValidation(null); setCompiled(null); setActionError(null); setNotice(null); setLineage(null) }, [instrument])

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

  // ------------------------------------------------- spec helpers
  const buildBrokerMeta = (): BrokerMeta | undefined => {
    const meta: Record<string, number> = {}
    const pip = Number(pipSize); if (pipSize.trim() && Number.isFinite(pip) && pip > 0) meta.pip_size = pip
    const tick = Number(tickSize); if (tickSize.trim() && Number.isFinite(tick) && tick > 0) meta.tick_size = tick
    const point = Number(pointSize); if (pointSize.trim() && Number.isFinite(point) && point > 0) meta.point_size = point
    return Object.keys(meta).length ? { [instrument]: meta } : undefined
  }

  const parseEditor = (): Record<string, unknown> | null => {
    try {
      const parsed = JSON.parse(specText)
      if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
        setSpecParseError('Spec must be a JSON object.')
        return null
      }
      setSpecParseError(null)
      return parsed as Record<string, unknown>
    } catch (err) {
      setSpecParseError(err instanceof Error ? err.message : 'invalid JSON')
      return null
    }
  }

  const loadExample = () => {
    const spec = bullishEngulfingSpec(instrument, timeframe)
    setSpecText(JSON.stringify(spec, null, 2))
    setNodeName((spec.identity as { name: string }).name)
    setValidation(null); setCompiled(null); setActionError(null); setNotice(null); setSpecParseError(null)
  }

  const formatEditor = () => {
    const parsed = parseEditor()
    if (parsed) setSpecText(JSON.stringify(parsed, null, 2))
  }

  const asIssues = (err: ApiError): ValidationIssue[] | undefined =>
    Array.isArray(err.issues) ? (err.issues as ValidationIssue[]) : undefined

  const doValidate = async () => {
    const spec = parseEditor(); if (!spec) return
    setBusy('validate'); setActionError(null); setNotice(null)
    try {
      setValidation(await api.nodes.validate(instrument, spec, buildBrokerMeta()))
      setCompiled(null)
    } catch (err) {
      if (err instanceof ApiError) setActionError({ message: err.message, code: err.code, issues: asIssues(err) })
      else setActionError({ message: 'validation failed' })
    } finally { setBusy(null) }
  }

  const doCompile = async () => {
    const spec = parseEditor(); if (!spec) return
    setBusy('compile'); setActionError(null); setNotice(null)
    try {
      const result = await api.nodes.compile(instrument, spec, buildBrokerMeta())
      setCompiled(result)
    } catch (err) {
      setCompiled(null)
      if (err instanceof ApiError) setActionError({ message: err.message, code: err.code, issues: asIssues(err) })
      else setActionError({ message: 'compilation failed' })
    } finally { setBusy(null) }
  }

  const saveNewNode = async () => {
    const spec = parseEditor(); if (!spec) return
    setBusy('save'); setActionError(null); setNotice(null)
    try {
      const created = await api.nodes.create(instrument, {
        name: nodeName.trim() || 'Untitled strategy',
        origin: 'CUSTOM',
        description: 'Saved from the spec workspace (server-validated + compiled).',
        spec_json: JSON.stringify(spec),
        broker_meta: buildBrokerMeta(),
      })
      setNotice(`Saved as node #${created.id} · v${created.version} · fingerprint ${created.spec_hash?.slice(0, 12)}…`)
      setSelectedId(created.id)
      await load()
    } catch (err) {
      if (err instanceof ApiError) setActionError({ message: err.message, code: err.code, issues: asIssues(err) })
      else setActionError({ message: 'save failed' })
    } finally { setBusy(null) }
  }

  const saveNewVersion = async () => {
    if (!selected) return
    const spec = parseEditor(); if (!spec) return
    setBusy('version'); setActionError(null); setNotice(null)
    try {
      const created = await api.nodes.versions(instrument, selected.id, {
        spec, broker_meta: buildBrokerMeta(),
        name: nodeName.trim() || undefined,
      })
      setNotice(`Created v${created.version} of the lineage as node #${created.id} (parent #${selected.id}) · fingerprint ${created.spec_hash?.slice(0, 12)}…`)
      setSelectedId(created.id)
      setLineage(null)
      await load()
    } catch (err) {
      if (err instanceof ApiError) setActionError({ message: err.message, code: err.code, issues: asIssues(err) })
      else setActionError({ message: 'version creation failed' })
    } finally { setBusy(null) }
  }

  const showLineage = async () => {
    if (!selected) return
    try {
      setLineage(await api.nodes.lineage(instrument, selected.id))
    } catch (err) {
      setActionError({ message: err instanceof ApiError ? err.message : 'lineage failed' })
    }
  }

  const loadIntoEditor = () => {
    if (!selected?.spec_json) return
    setSpecText(selected.spec_json)
    setNodeName(selected.name)
    setValidation(null); setCompiled(null); setActionError(null); setNotice(null); setSpecParseError(null)
  }

  return (
    <div>
      <div className="page-head">
        <div className="page-eyebrow">Builder · {instrument}</div>
        <h1 className="page-title">Manual Nodes</h1>
        <p className="page-sub">Foundational node registry for {instrument}. Phase 2 adds the strategy specification workspace: typed specs are validated and deterministically compiled server-side — a strategy is structured data, never executable code.</p>
      </div>

      <div className="banner" role="note">
        <span aria-hidden="true">ⓘ</span>
        <span><strong>Phase 2 scope.</strong> Specs saved here are validated then compiled to a declarative IR and SHA-256 fingerprinted by the server — client-supplied hashes are never authoritative. Nothing is backtested, optimized or executed from this screen. Broker metadata (pip/tick sizes) is supplied per instrument and never assumed. Ordinary metadata edits stay possible; strategy semantics are immutable — changing a spec creates a new version linked to its parent.</span>
      </div>

      <div className="panel-grid">
        {/* ---------------------------------------------- spec workspace */}
        <div className="card" style={{ gridColumn: '1 / -1' }}>
          <div className="card-title">
            Strategy spec workspace · {instrument}
            <span className="faint" style={{ fontWeight: 400, fontSize: 11, marginLeft: 10 }}>validation &amp; compilation happen server-side</span>
          </div>

          <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap', marginBottom: 10 }}>
            <div className="field">
              <label htmlFor="spec-tf">Timeframe</label>
              <select id="spec-tf" className="input" value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
                {TIMEFRAMES.map((tf) => <option key={tf} value={tf}>{tf}</option>)}
              </select>
            </div>
            <button className="btn btn-ghost btn-sm" type="button" onClick={loadExample}>Load bullish engulfing example</button>
            <button className="btn btn-ghost btn-sm" type="button" onClick={formatEditor} disabled={!specText.trim()}>Format JSON</button>
          </div>

          <div className="field">
            <label htmlFor="spec-json">Specification JSON</label>
            <textarea id="spec-json" className="input mono" spellCheck={false}
                      style={{ minHeight: 220, fontSize: 11.5, lineHeight: 1.5, whiteSpace: 'pre' }}
                      value={specText} onChange={(e) => setSpecText(e.target.value)}
                      placeholder='{"schema_version": "1.0.0", "identity": { … }, "entry": …, "exits": …, "risk": …, "execution": …}' />
          </div>
          {specParseError && <div className="field-error" role="alert" style={{ marginTop: 6 }}>JSON: {specParseError}</div>}

          <div className="field" style={{ marginTop: 10 }}>
            <label htmlFor="broker-pip">Broker metadata <span className="faint">(per {instrument} — resolved externally, NEVER assumed; leave blank to see NEEDS_METADATA)</span></label>
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
              <input id="broker-pip" className="input mono" style={{ maxWidth: 150 }} inputMode="decimal" placeholder="pip_size" aria-label="pip size" value={pipSize} onChange={(e) => setPipSize(e.target.value)} />
              <input id="broker-tick" className="input mono" style={{ maxWidth: 150 }} inputMode="decimal" placeholder="tick_size" aria-label="tick size" value={tickSize} onChange={(e) => setTickSize(e.target.value)} />
              <input id="broker-point" className="input mono" style={{ maxWidth: 150 }} inputMode="decimal" placeholder="point_size" aria-label="point size" value={pointSize} onChange={(e) => setPointSize(e.target.value)} />
            </div>
          </div>

          <div className="field" style={{ marginTop: 10 }}>
            <label htmlFor="spec-node-name">Node name</label>
            <input id="spec-node-name" className="input" maxLength={160} value={nodeName} onChange={(e) => setNodeName(e.target.value)} placeholder="e.g. Bullish Engulfing · XAUUSD M15" />
          </div>

          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 12 }}>
            <button className="btn btn-primary" type="button" onClick={doValidate} disabled={busy !== null || !specText.trim()}>{busy === 'validate' ? 'Validating…' : 'Validate'}</button>
            <button className="btn" type="button" onClick={doCompile} disabled={busy !== null || !specText.trim()}>{busy === 'compile' ? 'Compiling…' : 'Compile'}</button>
            <button className="btn" type="button" onClick={saveNewNode} disabled={busy !== null || !specText.trim()}>{busy === 'save' ? 'Saving…' : 'Save as new spec node'}</button>
            <button className="btn btn-ghost" type="button" onClick={saveNewVersion} disabled={busy !== null || !specText.trim() || !selected?.spec_json}
                    title={selected?.spec_json ? `New immutable version in the lineage of node #${selected.id}` : 'Select a spec-backed node in the registry first'}>
              {busy === 'version' ? 'Creating version…' : selected?.spec_json ? `Save as new version of #${selected.id}` : 'Save as new version (select a spec node)'}
            </button>
          </div>

          {notice && <div className="banner" style={{ marginTop: 12, marginBottom: 0, borderColor: 'rgba(61,220,151,.3)', background: 'rgba(61,220,151,.06)', color: '#b6f5d8' }} role="status"><span aria-hidden="true">✓</span><span className="mono" style={{ fontSize: 11 }}>{notice}</span></div>}

          {actionError && (
            <div style={{ marginTop: 12 }} role="alert">
              <div className="field-error">{actionError.code ? `[${actionError.code}] ` : ''}{actionError.message}</div>
              {actionError.issues && actionError.issues.length > 0 && (
                <ul style={{ display: 'grid', gap: 6, margin: '8px 0 0', padding: 0 }}>
                  {actionError.issues.map((issue, i) => <IssueRow key={i} issue={issue} />)}
                </ul>
              )}
            </div>
          )}

          {validation && (
            <div style={{ marginTop: 12 }}>
              <div style={{ display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap' }}>
                <span style={{ color: STATUS_COLOR[validation.status], fontWeight: 800, letterSpacing: 1.4, fontSize: 13 }}>{validation.status}</span>
                <span className="faint" style={{ fontSize: 11 }}>warmup {validation.required_warmup_candles ?? '—'} candles · schema {validation.schema_version ?? '—'}</span>
                <span className="faint" style={{ fontSize: 11 }}>invalid {validation.summary?.invalid ?? 0} · warnings {validation.summary?.warning ?? 0} · needs-metadata {validation.summary?.needs_metadata ?? 0}</span>
              </div>
              {validation.issues.length > 0 && (
                <ul style={{ display: 'grid', gap: 6, margin: '8px 0 0', padding: 0 }}>
                  {validation.issues.map((issue, i) => <IssueRow key={i} issue={issue} />)}
                </ul>
              )}
              {validation.status === 'VALID' && <div className="faint" style={{ fontSize: 11, marginTop: 6 }}>Spec is valid. Compile to inspect the IR and fingerprint, then save.</div>}
              {validation.status === 'NEEDS_METADATA' && <div className="faint" style={{ fontSize: 11, marginTop: 6 }}>A required broker parameter is missing — the server will not guess pip/tick sizes. Supply the metadata above or switch the spec to absolute/ATR distances.</div>}
            </div>
          )}

          {compiled && (
            <div style={{ marginTop: 12, borderTop: '1px solid var(--border)', paddingTop: 12 }}>
              <div className="card-title" style={{ marginBottom: 8 }}>Compiled IR <span className="faint" style={{ fontWeight: 400, fontSize: 10.5 }}>(declarative only — not executable)</span></div>
              <div style={{ display: 'grid', gap: 4, fontSize: 11 }} className="mono">
                <div><span className="faint">fingerprint</span><br /><span style={{ wordBreak: 'break-all' }}>{compiled.fingerprint}</span></div>
                <div className="faint">schema {compiled.schema_version} · compiler {compiled.compiler_version} · ir {compiled.ir_version} · warmup {compiled.required_warmup_candles}</div>
                {compiled.warnings.length > 0 && <div style={{ color: 'var(--warn)' }}>warnings: {compiled.warnings.join('; ')}</div>}
              </div>
              <pre className="input mono" style={{ maxHeight: 260, overflow: 'auto', fontSize: 10.5, marginTop: 8 }}>{JSON.stringify(compiled.ir, null, 2)}</pre>
            </div>
          )}
        </div>

        {/* ---------------------------------------------- create draft */}
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

        {/* ---------------------------------------------- registry */}
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
            <div className="state"><div className="state-title">No nodes yet for {instrument}</div>Create your first draft or save a validated spec — both stay inside this instrument’s database.</div>
          ) : (
            <div style={{ overflowX: 'auto' }}>
              <table className="node-table">
                <thead><tr><th>ID</th><th>Name</th><th>Origin</th><th>v</th><th>Spec</th><th>Status</th><th>Approval</th><th></th></tr></thead>
                <tbody>
                  {nodes.map((n) => (
                    <tr key={n.id} onClick={() => { setSelectedId(n.id); setLineage(null) }}
                        style={{ cursor: 'pointer', background: n.id === selectedId ? 'rgba(92,200,255,.06)' : undefined }}>
                      <td className="mono">#{n.id}</td>
                      <td>{n.name}{n.description && <div className="row-desc">{n.description}</div>}</td>
                      <td><span className={`badge ${n.origin}`}>{n.origin}</span></td>
                      <td className="mono">v{n.version}{n.parent_node_id ? <span className="faint">↳#{n.parent_node_id}</span> : null}</td>
                      <td className="mono faint" style={{ fontSize: 10.5 }}>{n.spec_hash ? `${n.spec_hash.slice(0, 12)}…` : '—'}</td>
                      <td><span className="badge DRAFT">{n.status}</span></td>
                      <td><span className={`badge ${n.approval_state === 'APPROVED' ? 'APPROVED' : 'PENDING'}`}>{n.approval_state}</span></td>
                      <td><button className="btn btn-ghost btn-sm" type="button" onClick={(e) => { e.stopPropagation(); setSelectedId(n.id); setLineage(null) }}>{n.id === selectedId ? 'Selected' : 'Inspect'}</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {selected && (
            <div style={{ borderTop: '1px solid var(--border)', marginTop: 10, paddingTop: 10 }}>
              <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', fontSize: 11.5 }}>
                <strong>#{selected.id} · v{selected.version}</strong>
                <span className="faint">created by {selected.created_by}</span>
                {selected.parent_node_id && <span className="faint">derived from #{selected.parent_node_id}</span>}
                <button className="btn btn-ghost btn-sm" type="button" onClick={showLineage}>Lineage</button>
                {selected.spec_json && <button className="btn btn-ghost btn-sm" type="button" onClick={loadIntoEditor}>Load into editor</button>}
              </div>
              {selected.spec_hash && <div className="mono faint" style={{ fontSize: 10, marginTop: 4, wordBreak: 'break-all' }}>sha256 {selected.spec_hash}</div>}
              {lineage && (
                <div style={{ marginTop: 8 }}>
                  <div className="faint" style={{ fontSize: 10.5, letterSpacing: 1, textTransform: 'uppercase' }}>Lineage (oldest → newest){lineage.lineage_root_id ? ` · root #${lineage.lineage_root_id}` : ''}</div>
                  <ol style={{ margin: '6px 0 0', paddingLeft: 18, fontSize: 11.5 }}>
                    {lineage.items.map((n) => (
                      <li key={n.id} className="mono">#{n.id} · v{n.version}{n.parent_node_id ? ` ← #${n.parent_node_id}` : ' (root)'} · {n.spec_hash ? `${n.spec_hash.slice(0, 12)}…` : 'no spec'}</li>
                    ))}
                  </ol>
                </div>
              )}
              {selected.spec_json && (
                <pre className="input mono" style={{ maxHeight: 200, overflow: 'auto', fontSize: 10, marginTop: 8 }}>{selected.spec_json}</pre>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
