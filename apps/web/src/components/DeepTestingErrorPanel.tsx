import { useState } from 'react'

const BLOCKED_CODES = new Set(['DATA_BLOCKED', 'HISTORICAL_SPREAD_UNAVAILABLE', 'MISSING_TICK_SIZE',
  'MISSING_TICK_VALUE', 'UNSUPPORTED_CHART_PRICE_MODE', 'UNSUPPORTED_SWAP_MODE', 'INVALID_SPREAD_MODE'])

export function classifyDeepTestRun(status: string, error?: string | null): 'FAILED' | 'BLOCKED' | 'COMPLETED' | 'COMPLETED WITH WARNINGS' | 'RUNNING' {
  if (status === 'RUNNING') return 'RUNNING'
  if (status === 'DONE') return 'COMPLETED'
  const code = String(error ?? '').split(':', 1)[0].split(/\s+/, 1)[0]
  if (status === 'BLOCKED' || (status === 'FAILED' && BLOCKED_CODES.has(code))) return 'BLOCKED'
  return 'FAILED'
}

export function DeepTestingErrorPanel({ nodeId, runId, error, explicitStatus }: {
  nodeId?: number | string; runId?: number | string; error: string; explicitStatus?: string
}) {
  const [copied, setCopied] = useState(false)
  const status = classifyDeepTestRun(explicitStatus ?? 'FAILED', error)
  const code = error.match(/^([A-Z][A-Z0-9_]+)(?:\s|:)/)?.[1] ?? 'UNEXPECTED'
  const stage = error.match(/Stage:\s*([^;\n]+)/)?.[1] ?? 'See technical details'
  const firstLine = error.split('\n')[0]
  return <section className={`dt-error-panel dt-error-${status.toLowerCase().replaceAll(' ', '-')}`} role="alert" aria-label="DeepTesting error details">
    <strong>{status}: Node #{nodeId ?? '—'} · Run #{runId ?? '—'}</strong>
    <div>Stage: {stage} · Code: {code}</div>
    <p>{firstLine}</p>
    <details><summary>Technical details</summary><pre>{error}</pre>
      <button type="button" className="btn btn-xs" onClick={async () => {
        try { await navigator.clipboard.writeText(error); setCopied(true) }
        catch { setCopied(false) }
      }}>{copied ? 'Copied' : 'Copy error details'}</button>
    </details>
  </section>
}
