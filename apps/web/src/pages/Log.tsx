import { useCallback, useEffect, useMemo, useState } from 'react'
import { loadSortPref, nextSortState, saveSortPref, type SortColumn, type SortState } from '../lib/tableSort'
import { ApiError, api } from '../api/client'
import type { HeroEvent } from '../api/types'
import { useInstrument } from '../state/instrument'
import { eventOperationId, fmtEventTs, formatLogsText, logExportFilename } from '../lib/logExport'

const SEVERITIES = ['ALL', 'INFO', 'WARNING', 'ERROR', 'DEBUG'] as const
const PAGE = 200

function payloadText(payload: unknown): string {
  if (payload == null) return ''
  try { return typeof payload === 'string' ? payload : JSON.stringify(payload, null, 2) } catch { return String(payload) }
}

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    // Clipboard API unavailable (permissions/insecure context): textarea fallback.
    try {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      const ok = document.execCommand('copy')
      document.body.removeChild(ta)
      return ok
    } catch {
      return false
    }
  }
}

/**
 * LOG tab: the persistent registry event store (DATA-backed, survives
 * restarts). Server-side pagination with an honest total; COPY ALL LOGS and
 * SAVE LOGS TO TXT export the COMPLETE filtered dataset (fetched page by
 * page, oldest-first, full details and stack traces) — filters are
 * display/export projections and never delete history.
 */
export function LogView() {
  const { instrument } = useInstrument()
  const [events, setEvents] = useState<HeroEvent[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [loadingOlder, setLoadingOlder] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [severity, setSeverity] = useState<string>('ALL')
  // Universal sorting (ISSUE-055): SERVER-SIDE - the backend orders the full
  // filtered dataset before pagination. Persisted per table like other prefs.
  const LOG_SORT_COLUMNS: SortColumn<HeroEvent>[] = [
    { key: 'ts', label: 'Time', type: 'date', get: (e) => e.ts },
    { key: 'severity', label: 'Severity', type: 'text', get: (e) => e.severity },
    { key: 'subsystem', label: 'Component', type: 'text', get: (e) => e.subsystem },
    { key: 'instrument', label: 'Instrument', type: 'text', get: (e) => e.instrument },
    { key: 'message', label: 'Message', type: 'text', get: (e) => e.message },
  ]
  const [sortState, setSortState] = useState<SortState>(
    () => loadSortPref('log', LOG_SORT_COLUMNS.map((c) => c.key)) ?? { key: 'ts', dir: 'desc' })
  useEffect(() => { saveSortPref('log', sortState) }, [sortState])
  const toggleLogSort = (key: string) => {
    const col = LOG_SORT_COLUMNS.find((c) => c.key === key)
    if (col) setSortState((cur) => nextSortState(cur, col))
  }
  const [component, setComponent] = useState('')
  const [operationId, setOperationId] = useState('')
  const [search, setSearch] = useState('')
  const [allInstruments, setAllInstruments] = useState(false)
  const [autoRefresh, setAutoRefresh] = useState(false)
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [copiedId, setCopiedId] = useState<number | null>(null)
  const [exportStatus, setExportStatus] = useState<string | null>(null)
  const [exportBusy, setExportBusy] = useState(false)

  const queryParams = useCallback((offset: number, limit: number) => ({
    instrument: allInstruments ? undefined : instrument,
    severity: severity === 'ALL' ? undefined : severity,
    subsystem: component.trim() || undefined,
    search: [operationId.trim(), search.trim()].filter(Boolean).join(' ') || undefined,
    limit, offset, order: sortState.dir, sort: sortState.key,
  }), [instrument, allInstruments, severity, component, operationId, search, sortState])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.events(queryParams(0, PAGE))
      setEvents(res.events) // server returns newest first
      setTotal(res.total ?? res.count)
      setError(null)
    } catch (err) {
      setError(err instanceof ApiError ? `[${err.code ?? err.status}] ${err.message}` : 'failed to load logs')
    } finally {
      setLoading(false)
    }
  }, [queryParams])

  useEffect(() => { void load() }, [load])

  useEffect(() => {
    if (!autoRefresh) return
    const t = setInterval(() => { void load() }, 5000)
    return () => clearInterval(t)
  }, [autoRefresh, load])

  const loadOlder = async () => {
    setLoadingOlder(true)
    try {
      const res = await api.events(queryParams(events.length, PAGE))
      setEvents((prev) => [...prev, ...res.events])
      setTotal(res.total ?? total)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'failed to load older logs')
    } finally {
      setLoadingOlder(false)
    }
  }

  /** Fetch EVERY event matching the active filters (paged, newest-first). */
  const fetchAllMatching = async (): Promise<HeroEvent[]> => {
    const all: HeroEvent[] = []
    let offset = 0
    for (;;) {
      const res = await api.events(queryParams(offset, 500))
      all.push(...res.events)
      offset += res.events.length
      if (res.events.length === 0 || offset >= (res.total ?? all.length)) break
    }
    return all
  }

  const activeFilters = useMemo(() => ({
    severity, component: component.trim(), operation_id: operationId.trim(),
    search: search.trim(), instrument: allInstruments ? '(all)' : instrument,
  }), [severity, component, operationId, search, allInstruments, instrument])

  const exportText = async (): Promise<{ text: string; count: number }> => {
    const all = await fetchAllMatching()
    return {
      text: formatLogsText(all, {
        exportedAt: new Date(), filters: activeFilters, totalMatching: total,
      }),
      count: all.length,
    }
  }

  const copyAllLogs = async () => {
    setExportBusy(true); setExportStatus(null)
    try {
      const { text, count } = await exportText()
      const ok = await copyText(text)
      setExportStatus(ok
        ? `Copied ${count} log event(s) matching the current filters.`
        : 'Copy failed — clipboard unavailable in this browser context. Use SAVE LOGS TO TXT instead.')
    } catch (err) {
      setExportStatus(`Copy failed: ${err instanceof ApiError ? err.message : 'log export request failed'}`)
    } finally { setExportBusy(false) }
  }

  const saveLogsToTxt = async () => {
    setExportBusy(true); setExportStatus(null)
    try {
      const { text, count } = await exportText()
      const blob = new Blob([text], { type: 'text/plain;charset=utf-8' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = logExportFilename(new Date())
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
      setExportStatus(`Saved ${count} log event(s) to ${a.download}.`)
    } catch (err) {
      setExportStatus(`Save failed: ${err instanceof ApiError ? err.message : 'log export request failed'}`)
    } finally { setExportBusy(false) }
  }

  const clearFilters = () => {
    setSeverity('ALL'); setComponent(''); setOperationId(''); setSearch('')
    // display-only: historical log records are never deleted here
  }

  const copyDetails = async (event: HeroEvent) => {
    const text = [
      `#${event.id} ${fmtEventTs(event.ts)} [${event.severity}] ${event.subsystem}`,
      `instrument: ${event.instrument ?? '—'}  operation: ${eventOperationId(event) ?? '—'}`,
      event.message,
      payloadText(event.payload),
    ].join('\n')
    if (await copyText(text)) {
      setCopiedId(event.id)
      setTimeout(() => setCopiedId(null), 1500)
    }
  }

  return (
    <div className="card" aria-labelledby="log-title">
      <div className="card-title" id="log-title">
        LOG
        <span className="faint" style={{ fontWeight: 400, fontSize: 10.5, marginLeft: 8 }}>
          persistent backend event store — filter by operation id to reconstruct a FETCH DATA run
        </span>
      </div>

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
        <button className="btn btn-primary btn-sm" type="button" onClick={() => void copyAllLogs()} disabled={exportBusy}>
          COPY ALL LOGS
        </button>
        <button className="btn btn-primary btn-sm" type="button" onClick={() => void saveLogsToTxt()} disabled={exportBusy}>
          SAVE LOGS TO TXT
        </button>
        <button className="btn btn-ghost btn-sm" type="button" onClick={() => void load()}>↻ Refresh</button>
        <label className="faint" style={{ fontSize: 11, display: 'flex', gap: 6, alignItems: 'center' }}>
          <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} />
          auto-refresh
        </label>
        {exportStatus && <span className="faint" style={{ fontSize: 11 }} role="status">{exportStatus}</span>}
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
      ) : events.length === 0 ? (
        <div className="state"><div className="state-title">No log events match</div>
          {!error
            ? 'The persistent event store is empty for this scope.'
            : 'Adjust or clear the filters — clearing filters never deletes log history.'}</div>
      ) : (
        <>
        <div className="log-sort-row" role="row" aria-label="Sort events">
          <span className="faint" style={{ fontSize: 10, marginRight: 2 }}>SORT</span>
          {LOG_SORT_COLUMNS.map((col) => {
            const active = sortState.key === col.key
            return (
              <button key={col.key} type="button" className="sort-btn"
                      title={`Sort by ${col.label.toLowerCase()}`}
                      onClick={() => toggleLogSort(col.key)}>
                <span>{col.label}</span>
                <span className={active ? 'sort-ind' : 'sort-ind idle'} aria-hidden="true">
                  {active ? (sortState.dir === 'asc' ? '▲' : '▼') : '⇅'}
                </span>
              </button>
            )
          })}
          <span className="faint" style={{ fontSize: 10 }}>applies to the full filtered log, not just this page</span>
        </div>
        <div className="log-list" role="log" aria-label="Application events">
          {events.map((e) => {
            const oid = eventOperationId(e)
            const expanded = expandedId === e.id
            return (
              <div key={e.id} className={`log-row sev-${e.severity.toLowerCase()}`}>
                <button type="button" className="log-main"
                        aria-expanded={expanded}
                        onClick={() => setExpandedId(expanded ? null : e.id)}>
                  <span className="mono faint">{fmtEventTs(e.ts)}</span>
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
        </>
      )}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 10 }}>
        <p className="faint" style={{ fontSize: 10.5, margin: 0 }}>
          Showing {events.length} of {total} matching events (newest first). Logs persist in the
          project DATA event store across restarts; this tab never deletes them.
        </p>
        {events.length < total && (
          <button className="btn btn-ghost btn-sm" type="button" onClick={() => void loadOlder()} disabled={loadingOlder}>
            {loadingOlder ? 'Loading…' : 'Load older events'}
          </button>
        )}
      </div>
    </div>
  )
}
