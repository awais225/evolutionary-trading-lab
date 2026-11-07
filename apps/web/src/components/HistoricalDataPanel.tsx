import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { ApiError, api } from '../api/client'
import type { MarketDataAvailability, MarketDataFetchReport, MarketDataOperation } from '../api/types'
import { SUPPORTED_TIMEFRAMES } from '../lib/timeframes'
import { useInstrument } from '../state/instrument'
import { logUserAction, newOperationId } from '../lib/opLog'

const FETCH_TIMEFRAMES = ['ALL', ...SUPPORTED_TIMEFRAMES] as const

function FetchTimeframeSelect({ value, onChange, disabled }: {
  value: string
  onChange: (next: string) => void
  disabled?: boolean
}) {
  const [open, setOpen] = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const listId = useId()
  const labelId = useId()

  useEffect(() => {
    if (!open) return
    const onMouseDown = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as globalThis.Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { setOpen(false); triggerRef.current?.focus() }
    }
    document.addEventListener('mousedown', onMouseDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onMouseDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open ])

  return (
    <div ref={wrapRef} className={`tf-multi fetch-timeframe-select${open ? ' open' : ''}`}>
      <span className="tf-multi-label" id={labelId}>Fetch scope</span>
      <button ref={triggerRef} type="button" className="tf-trigger" disabled={disabled}
              aria-haspopup="listbox" aria-expanded={open} aria-controls={open ? listId : undefined}
              aria-labelledby={`${labelId} ${listId}-trigger`} id={`${listId}-trigger`}
              onClick={() => setOpen((v) => !v)}>
        <span className="tf-trigger-value">{value === 'ALL' ? 'ALL (M1–D1)' : value}</span>
        <svg className="instrument-chevron" viewBox="0 0 12 12" aria-hidden="true" fill="none"
             stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
          <path d="M2.5 4.5L6 8l3.5-3.5" />
        </svg>
      </button>
      {open && (
        <div className="instrument-panel tf-panel" role="listbox" id={listId} aria-labelledby={labelId}>
          {FETCH_TIMEFRAMES.map((tf) => (
            <div key={tf} role="option" aria-selected={value === tf}
                 className={`instrument-option tf-option${value === tf ? ' selected' : ''}`}
                 onClick={() => { onChange(tf); setOpen(false); triggerRef.current?.focus() }}>
              <span className="instrument-option-symbol">{tf === 'ALL' ? 'ALL' : tf}</span>
              <span className="faint" style={{ marginLeft: 'auto', fontSize: 10 }}>
                {tf === 'ALL' ? 'M1–D1' : 'single timeframe'}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/**
 * Historical market-data section of the DATA tab: canonical-store
 * availability (queried live from the running backend's own database) plus
 * incremental MT5 FETCH DATA with operation-id tracing.
 */
export function HistoricalDataPanel() {
  const { instrument } = useInstrument()
  const [marketData, setMarketData] = useState<MarketDataAvailability | null>(null)
  const [marketDataLoadedFor, setMarketDataLoadedFor] = useState<string | null>(null)
  const [marketDataBusy, setMarketDataBusy] = useState(false)
  const [marketDataError, setMarketDataError] = useState<string | null>(null)
  const [fetchTimeframe, setFetchTimeframe] = useState('ALL')
  const [fetchReport, setFetchReport] = useState<MarketDataFetchReport | null>(null)
  const [operation, setOperation] = useState<MarketDataOperation | null>(null)
  const pollRef = useRef<number | null>(null)

  const stopPolling = useCallback(() => {
    if (pollRef.current != null) {
      window.clearInterval(pollRef.current)
      pollRef.current = null
    }
  }, [])

  // Resume whatever operation the backend already has for this instrument:
  // reopening DATA never starts a duplicate and never loses a running fetch.
  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const latest = await api.marketData.latestOperation(instrument)
        if (cancelled || !latest.operation_id) return
        setOperation(latest)
        if (latest.report) setFetchReport(latest.report)
        if (latest.state === 'QUEUED' || latest.state === 'RUNNING') {
          pollRef.current = window.setInterval(() => { void pollOnce(latest.operation_id as string) }, 700)
        }
      } catch { /* no operation yet — idle state is fine */ }
    })()
    return () => { cancelled = true; stopPolling() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [instrument])

  const pollOnce = async (operationId: string) => {
    try {
      const rec = await api.marketData.operation(instrument, operationId)
      setOperation(rec)
      if (rec.state !== 'QUEUED' && rec.state !== 'RUNNING') {
        stopPolling()
        if (rec.report) {
          setFetchReport(rec.report)
        } else if (rec.error) {
          setMarketDataError(`[${rec.error.code ?? 'FETCH_FAILED'}] ${rec.error.message ?? 'fetch failed'}`)
        }
        logUserAction(`[${operationId}] dashboard refreshed after fetch (${rec.state})`, {
          operationId, instrument, subsystem: 'market-data-web',
          severity: rec.state === 'FAILED' ? 'ERROR' : 'INFO',
        })
        await loadMarketData()
      }
    } catch (err) {
      stopPolling()
      setMarketDataError(err instanceof ApiError ? err.message : 'fetch status lookup failed')
    }
  }

  const loadMarketData = useCallback(async () => {
    try {
      setMarketData(await api.marketData.get(instrument))
      setMarketDataError(null)
      setMarketDataLoadedFor(instrument)
    } catch (err) {
      setMarketData(null)
      setMarketDataLoadedFor(instrument)
      setMarketDataError(err instanceof ApiError ? err.message : 'historical-data lookup failed')
    }
  }, [instrument])

  useEffect(() => { loadMarketData() }, [loadMarketData])

  const fetchBusy = marketDataBusy || operation?.state === 'QUEUED' || operation?.state === 'RUNNING'

  const fetchMarketData = async () => {
    if (fetchBusy) return // one operation at a time; the backend enforces this too
    const operationId = newOperationId('FETCH')
    logUserAction(`[${operationId}] FETCH_CLICKED scope=${fetchTimeframe}`, {
      operationId, instrument, timeframe: fetchTimeframe, subsystem: 'market-data-web',
    })
    setMarketDataBusy(true); setMarketDataError(null); setFetchReport(null)
    try {
      // Background operation: this request returns immediately; progress is
      // polled, so the tab (and the whole app) stays responsive even if the
      // terminal takes minutes to answer.
      const started = await api.marketData.startFetch(instrument, fetchTimeframe, operationId)
      setOperation(started)
      logUserAction(`[${started.operation_id}] REQUEST_SUBMITTED scope=${fetchTimeframe}${started.existing ? ' (already running — resumed)' : ''}`, {
        operationId: started.operation_id ?? operationId, instrument,
        timeframe: fetchTimeframe, subsystem: 'market-data-web',
      })
      if (started.operation_id) {
        stopPolling()
        pollRef.current = window.setInterval(() => { void pollOnce(started.operation_id as string) }, 700)
      }
    } catch (err) {
      const message = err instanceof ApiError ? err.message : 'MT5 fetch failed'
      setMarketDataError(message)
      logUserAction(`[${operationId}] fetch failed to start: ${message}`, {
        operationId, instrument, timeframe: fetchTimeframe, subsystem: 'market-data-web',
        severity: 'ERROR',
      })
    } finally { setMarketDataBusy(false) }
  }

  return (
    <div className="broker-meta-panel market-data-panel" aria-labelledby="historical-data-label">
      {marketDataLoadedFor !== instrument && !marketData && (
        <p className="faint" style={{ margin: 0 }}>loading stored historical data…</p>
      )}
      {marketData && (
        <>
          <div className="meta-row">
            <span className={`meta-chip ${marketData.available ? 'ok' : 'warn'}`}>{marketData.available ? 'STORED' : 'NOT STORED'}</span>
            <span className="mono" style={{ fontSize: 11 }}>{marketData.store}</span>
            <button className="btn btn-ghost btn-sm" type="button" style={{ marginLeft: 'auto' }}
                    onClick={() => {
                      logUserAction('user refreshed market-data availability', { instrument, subsystem: 'market-data-web' })
                      void loadMarketData()
                    }}>↻ Refresh</button>
          </div>
          {!marketData.available && marketData.reason && (
            <p className="faint" style={{ margin: 0, fontSize: 11 }}>{marketData.reason}</p>
          )}
          {marketData.summary && (
            <div className="meta-row mono" style={{ fontSize: 11 }}>
              <span>{marketData.summary.available_timeframes}/7 timeframes available</span>
              <span>{marketData.summary.total_candles.toLocaleString()} total candles</span>
              <span>{marketData.summary.first_ts ?? '—'} → {marketData.summary.last_ts ?? '—'} UTC</span>
              <span className="faint">last import {marketData.summary.last_successful_import ?? '—'}</span>
              <span className="faint">last MT5 update {marketData.summary.last_mt5_update ?? '—'}</span>
              <span className="faint">last update {marketData.summary.last_successful_update ?? '—'}</span>
              {marketData.summary.mt5_fetch_required_timeframes?.length ? (
                <span className="meta-chip warn">MT5 FETCH required: {marketData.summary.mt5_fetch_required_timeframes.join(', ')}</span>
              ) : (
                <span className="meta-chip ok">MT5 fetch not required</span>
              )}
            </div>
          )}
          <div className="market-data-grid" role="table" aria-label={`Stored ${instrument} candle coverage`}>
            <div className="market-data-row market-data-head" role="row">
              <strong>TF</strong><span>Status</span><span>First candle (UTC)</span><span>Latest candle (UTC)</span><span>Candles</span><span>Source / updates</span>
            </div>
            {SUPPORTED_TIMEFRAMES.map((tf) => {
              const stats = marketData.timeframes?.[tf]
              const status = stats?.status ?? (stats?.row_count ? 'AVAILABLE' : 'EMPTY')
              return (
                <div className="market-data-row" role="row" key={tf}>
                  <strong className="mono">{tf}</strong>
                  <span className={`meta-chip ${status === 'AVAILABLE' ? 'ok' : status === 'ERROR' ? 'err' : 'warn'}`}>{status}</span>
                  {stats?.row_count ? (
                    <>
                      <span className="mono">{stats.first_ts ?? '—'}</span>
                      <span className="mono">{stats.last_ts ?? '—'}</span>
                      <span className="mono">{stats.row_count.toLocaleString()}</span>
                      <span className="faint" style={{ fontSize: 10 }}>
                        {stats.source ?? 'stored dataset'} · UTC epoch seconds
                        {stats.last_successful_import ? ` · imported ${stats.last_successful_import}` : ''}
                        {stats.last_mt5_update ? ` · MT5 ${stats.last_mt5_update}` : ''}
                        {stats.mt5_fetch_required ? ' · MT5 fetch required' : ' · MT5 up to date'}
                        {stats.warnings?.length ? ` · ${stats.warnings.join('; ')}` : ''}
                      </span>
                    </>
                  ) : (
                    <span className="faint">no stored candles — baseline CSV import required (DATA/MARKET_DATA/IMPORTS)</span>
                  )}
                </div>
              )
            })}
          </div>
        </>
      )}
      <div className="market-data-actions">
        <FetchTimeframeSelect value={fetchTimeframe} disabled={fetchBusy}
                              onChange={(next) => {
                                setFetchTimeframe(next)
                                logUserAction(`fetch scope changed to ${next}`, {
                                  instrument, timeframe: next, subsystem: 'market-data-web',
                                })
                              }} />
        <button className="btn btn-primary btn-sm market-fetch-button" type="button"
                onClick={fetchMarketData} disabled={fetchBusy}>
          {fetchBusy ? 'FETCHING…' : 'FETCH DATA'}
        </button>
        {operation?.operation_id && (
          <span className="mono faint" style={{ fontSize: 10.5 }} role="status">
            op {operation.operation_id} · {operation.state}
            {operation.stage ? ` · stage ${operation.stage}` : ''}
            {(operation.state === 'QUEUED' || operation.state === 'RUNNING')
              ? ' — safe to navigate away; reopening DATA resumes this operation'
              : ''}
          </span>
        )}
        <span className="faint" style={{ fontSize: 10.5 }}>
          Appends only completed, validated MT5 candles. Conflicting history is isolated and recorded — never overwritten.
        </span>
      </div>
      {fetchReport && (
        <div className="fetch-report" role="status">
          <div className="meta-row">
            <span className={`meta-chip ${fetchReport.status === 'COMPLETE' ? 'ok' : fetchReport.status === 'PARTIAL' ? 'warn' : 'err'}`}>{fetchReport.status ?? (fetchReport.ok ? 'COMPLETE' : 'FAILED')}</span>
            <span className="mono" style={{ fontSize: 11 }}>{fetchReport.broker_server}</span>
            {fetchReport.operation_id && (
              <span className="mono faint" style={{ fontSize: 10.5 }}>op {fetchReport.operation_id} — trace it in the LOG tab</span>
            )}
            <span className="faint" style={{ fontSize: 10.5 }}>
              UTC now {fetchReport.time?.utc_now ?? fetchReport.server_time?.utc_now ?? '—'} · no broker offset applied
            </span>
          </div>
          {fetchReport.results.map((r) => (
            <div className="fetch-result-row" key={r.timeframe}>
              <strong className="mono">{r.timeframe}</strong>
              <span className={`meta-chip ${r.status === 'FAILED' ? 'err' : r.status === 'UPDATED' ? 'ok' : 'warn'}`}>{r.status}</span>
              <span className="mono">
                {r.requested_from ?? '—'} → {r.requested_to ?? '—'} UTC · received {r.received} · valid {r.valid_records ?? '—'} · appended {r.appended} · duplicates {r.duplicates_skipped} · representation diffs {r.representation_diffs ?? 0} · incomplete {r.incomplete_dropped} · invalid {r.invalid_rejected} · invalid timestamps {r.invalid_timestamps_rejected ?? 0} · conflicts {r.conflicts} · latest {r.latest_stored_ts ?? '—'}
                {r.invalid_examples?.length ? ` · rejected: ${r.invalid_examples.join('; ')}` : ''}
              </span>
              {r.error && <span className="field-error">{r.error}</span>}
              {r.conflict_details?.map((c) => (
                <span className="mono faint" key={c.ts} style={{ fontSize: 10 }}>
                  conflict {c.ts_iso ?? c.ts}: stored preserved — {Object.entries(c.differing_fields ?? {})
                    .map(([k, v]) => `${k} stored=${String(v.stored)} vs MT5=${String(v.incoming)}`).join('; ')}
                </span>
              ))}
            </div>
          ))}
        </div>
      )}
      {marketDataError && <div className="field-error" role="alert">{marketDataError}</div>}
    </div>
  )
}
