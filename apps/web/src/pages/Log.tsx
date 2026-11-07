import { useCallback, useEffect, useMemo, useState } from 'react'
import { ApiError, api } from '../api/client'
import type { HeroEvent } from '../api/types'
import { useInstrument } from '../state/instrument'

const SEVERITIES = ['ALL', 'INFO', 'WARNING', 'ERROR', 'DEBUG'] as const

function fmtTs(ts: number): string {
  const ms = ts < 1e12 ? ts * 1000 : ts
  return new Date(ms).toISOString().replace('T', ' ').slice(0, 19) + 'Z'
}

function payloadText(payload: unknown): string {
  if (payload == null) return ''
  try { return typeof payload === 'string' ? payload : JSON.stringify(payload, null, 2) } catch { return String(payload) }
}

function operationIdOf(event: HeroEvent): string | null {
  const p = event.payload as Record<string, unknown> | null
  const fromPayload = p && typeof p.operation_id === 'string' ? p.operation_id : null
  if (fromPayload) return fromPayload
  const m = event.message.match(/FETCH-\d{8}-\d{6}-[0-9A-Fa-f]{6}/)
  return m ? m[0] : null
}

/**
 * LOG tab: the persistent registry event store (DATA-backed, survives
 * restarts). Filters are display-side projections over real backend events —
 * clearing them never deletes history.
 */
export function LogView() {
  const { instrument } = useInstrument()
  const [events, setEvents] = useState<HeroEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [severity, setSeverity] = useState<string>('ALL')
  const [component, setComponent] = useState('')
  const [operationId, setOperationId] = useState('')
  const [search, setSearch] = useState('')
  const [allInstruments, setAllInstruments] = useState(false)
  const [autoRefresh, setAutoRefresh] = useState(false)
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [copiedId, setCopiedId] = useState<number | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.events({
        instrument: allInstruments ? undefined : instrument,
        severity: severity === 'ALL' ? undefined : severity,
        subsystem: component.trim() || undefined,
        search: [operationId.trim(), search.trim()].filter(Boolean).join(' ') || undefined,
        limit: 500,
      })
      setEvents([...res.events].reverse()) // newest first for display
      setError(null)
    } catch (err) {
      setError(err instanceof ApiError ? `[${err.code ?? err.status}] ${err.message}` : 'failed to load logs')
    } finally {
      setLoading(false)
    }
  }, [instrument, allInstruments, severity, component, operationId, search])

  useEffect(() => { void load() }, [load])

  useEffect(() => {
    if (!autoRefresh) return
    const t = setInterval(() => { void load() }, 5000)
    return () => clearInterval(t)
  }, [autoRefresh, load])

  const filtered = useMemo(() => events.filter((e) => {
    if (operationId.trim()) {
      const oid = operationIdOf(e)
      if (oid !== operationId.trim() && !e.message.includes(operationId.trim())) return false
    }
    return true
  }), [events, operationId])

  const clearFilters = () => {
    setSeverity('ALL'); setComponent(''); setOperationId(''); setSearch('')
    // display-only: historical log records are never deleted here
  }

  const copyDetails = async (event: HeroEvent) => {
    const text = [
      `#${event.id} ${fmtTs(event.ts)} [${event.severity}] ${event.subsystem}`,
      `instrument: ${event.instrument ?? '—'}  operation: ${operationIdOf(event) ?? '—'}`,
      event.message,
      payloadText(event.payload),
    ].join('\n')
    try {
      await navigator.clipboard.writeText(text)
      setCopiedId(event.id)
      setTimeout(() => setCopiedId(null), 1500)
    } catch { /* clipboard unavailable in some contexts; details stay visible */ }
  }

  return (
    <div className="card" aria-labelledby="log-title">
      <div className="card-title" id="log-title">
        LOG
        <span className="faint" style={{ fontWeight: 400, fontSize: 10.5, marginLeft: 8 }}>
          persistent backend event store — filter by operation id to reconstruct a FETCH DATA run
        </span>
        <button className="btn btn-ghost btn-sm" type="button" style={{ marginLeft: 'auto' }}
                onClick={() => void load()}>↻ Refresh</button>
        <label className="faint" style={{ fontSize: 11, display: 'flex', gap: 6, alignItems: 'center' }}>
          <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} />
          auto-refresh
        </label>
      </div>

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
        <select className="input" style={{ maxWidth: 150 }} aria-label="Severity filter"
                value={severity} onChange={(e) => setSeverity(e.target.value)}>
          {SEVERITIES.map((sev) => <option key={sev} value={sev}>{sev === 'ALL' ? 'Severity: all' : sev}</option>)}
        </select>
        <input className="input" style={{ maxWidth: 180 }} placeholder="Component (subsystem)…"
               aria-label="Component filter" value={component}
               onChange={(e) => setComponent(e.target.value)} />
        <input className="input mono" style={{ maxWidth: 260 }} placeholder="Operation ID (FETCH-…)…"
               aria-label="Operation ID filter" value={operationId}
               onChange={(e) => setOperationId(e.target.value)} />
        <input className="input" style={{ maxWidth: 220 }} placeholder="Search message / details…"
               aria-label="Search logs" value={search}
               onChange={(e) => setSearch(e.target.value)} />
        <label className="faint" style={{ fontSize: 11, display: 'flex', gap: 6, alignItems: 'center' }}>
          <input type="checkbox" checked={allInstruments} onChange={(e) => setAllInstruments(e.target.checked)} />
          all instruments
        </label>
        <button className="btn btn-ghost btn-sm" type="button" onClick={clearFilters}>Clear filters</button>
      </div>

      {error && <div className="field-error" role="alert" style={{ marginBottom: 10 }}>{error}</div>}
      {loading && events.length === 0 ? (
        <div className="state"><div className="spinner" /><div className="state-title">Loading logs…</div></div>
      ) : filtered.length === 0 ? (
        <div className="state"><div className="state-title">No log events match</div>
          {events.length === 0 && !error
            ? 'The persistent event store is empty for this scope.'
            : 'Adjust or clear the filters — clearing filters never deletes log history.'}</div>
      ) : (
        <div className="log-list" role="log" aria-label="Application events">
          {filtered.map((e) => {
            const oid = operationIdOf(e)
            const expanded = expandedId === e.id
            return (
              <div key={e.id} className={`log-row sev-${e.severity.toLowerCase()}`}>
                <button type="button" className="log-main"
                        aria-expanded={expanded}
                        onClick={() => setExpandedId(expanded ? null : e.id)}>
                  <span className="mono faint">{fmtTs(e.ts)}</span>
                  <span className={`meta-chip ${e.severity === 'ERROR' ? 'err' : e.severity === 'WARNING' ? 'warn' : 'ok'}`}>{e.severity}</span>
                  <span className="mono faint">{e.subsystem}</span>
                  {e.instrument && <span className="mono">{e.instrument}</span>}
                  {oid && <span className="mono faint">{oid}</span>}
                  <span className="log-message">{e.message}</span>
                </button>
                {expanded && (
                  <div className="log-details">
                    <pre className="mono">{payloadText(e.payload) || '(no details)'}</pre>
                    <button className="btn btn-ghost btn-sm" type="button" onClick={() => void copyDetails(e)}>
                      {copiedId === e.id ? 'Copied ✓' : 'Copy details'}
                    </button>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
      <p className="faint" style={{ fontSize: 10.5, marginTop: 10 }}>
        Showing {filtered.length} of {events.length} loaded events (newest first). Logs persist in the
        project DATA event store across restarts; this tab never deletes them.
      </p>
    </div>
  )
}
