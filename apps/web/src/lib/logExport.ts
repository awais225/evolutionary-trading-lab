import type { HeroEvent } from '../api/types'

export function fmtEventTs(ts: number): string {
  const ms = ts < 1e12 ? ts * 1000 : ts
  return new Date(ms).toISOString().replace('T', ' ').slice(0, 19) + 'Z'
}

export function eventOperationId(event: HeroEvent): string | null {
  const p = event.payload as Record<string, unknown> | null
  if (p && typeof p.operation_id === 'string') return p.operation_id
  const m = event.message.match(/FETCH-\d{8}-\d{6}-[0-9A-Fa-f]{6}/)
  return m ? m[0] : null
}

export function eventStage(event: HeroEvent): string | null {
  const p = event.payload as Record<string, unknown> | null
  return p && typeof p.stage === 'string' ? p.stage : null
}

function detailsText(payload: unknown): string {
  if (payload == null) return ''
  try {
    return typeof payload === 'string' ? payload : JSON.stringify(payload, null, 2)
  } catch {
    return String(payload)
  }
}

/**
 * Plain-text export of log events in CHRONOLOGICAL order (oldest first),
 * with full details and stack traces. Nothing is truncated.
 */
export function formatLogsText(events: HeroEvent[], meta: {
  exportedAt: Date
  filters: Record<string, string>
  totalMatching: number
}): string {
  const chronological = [...events].sort((a, b) => a.id - b.id)
  const filterLines = Object.entries(meta.filters)
    .filter(([, v]) => v && v !== 'ALL')
    .map(([k, v]) => `  ${k}: ${v}`)
  const lines: string[] = [
    'HERO1 — APPLICATION LOG EXPORT',
    `Exported: ${meta.exportedAt.toISOString()}`,
    `Total exported events: ${chronological.length} (matching total: ${meta.totalMatching})`,
    'Active filters:',
    ...(filterLines.length ? filterLines : ['  (none — all available logs)']),
    '',
    '='.repeat(72),
    '',
  ]
  for (const e of chronological) {
    const oid = eventOperationId(e)
    const stage = eventStage(e)
    lines.push(
      `[${fmtEventTs(e.ts)}] [${e.severity}] component=${e.subsystem}` +
      ` instrument=${e.instrument ?? '—'} operation=${oid ?? '—'} stage=${stage ?? '—'} id=${e.id}`,
    )
    lines.push(`  ${e.message}`)
    const details = detailsText(e.payload)
    if (details) {
      lines.push('  details:')
      for (const line of details.split('\n')) lines.push(`    ${line}`)
    }
    lines.push('')
  }
  return lines.join('\n')
}

/** HERO1_LOGS_YYYY-MM-DD_HH-MM-SS.txt */
export function logExportFilename(now: Date): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `HERO1_LOGS_${now.getFullYear()}-${p(now.getMonth() + 1)}-${p(now.getDate())}` +
    `_${p(now.getHours())}-${p(now.getMinutes())}-${p(now.getSeconds())}.txt`
}
