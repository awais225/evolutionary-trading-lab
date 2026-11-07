import { api } from '../api/client'

/** Unique, sortable operation id, e.g. FETCH-20261107-084512-9F3AC1. */
export function newOperationId(prefix = 'FETCH'): string {
  const now = new Date()
  const p = (n: number, w = 2) => String(n).padStart(w, '0')
  const stamp = `${now.getUTCFullYear()}${p(now.getUTCMonth() + 1)}${p(now.getUTCDate())}` +
    `-${p(now.getUTCHours())}${p(now.getUTCMinutes())}${p(now.getUTCSeconds())}`
  const rand = Math.floor(Math.random() * 0xffffff).toString(16).toUpperCase().padStart(6, '0')
  return `${prefix}-${stamp}-${rand}`
}

/**
 * Best-effort persistent user-action log (registry event store, DATA/LOGS
 * architecture). Never throws and never blocks the UI: a logging failure
 * must not break the action being logged.
 */
export function logUserAction(message: string, opts: {
  operationId?: string
  instrument?: string
  timeframe?: string | null
  severity?: 'INFO' | 'WARNING' | 'ERROR' | 'DEBUG'
  subsystem?: string
} = {}): void {
  try {
    void api.postEvent({
      category: 'SYSTEM',
      severity: opts.severity ?? 'INFO',
      instrument: opts.instrument,
      subsystem: opts.subsystem ?? 'web',
      message,
      payload: {
        operation_id: opts.operationId ?? null,
        timeframe: opts.timeframe ?? null,
        source: 'frontend',
      },
    }).catch(() => undefined)
  } catch {
    /* logging must never break the UI */
  }
}
