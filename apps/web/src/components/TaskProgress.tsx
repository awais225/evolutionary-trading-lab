import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import { TASK_COLORS, TERMINAL, useTasks, type DashboardTask } from '../state/tasks'

const AUTO_DISMISS = new Set(['COMPLETED', 'CANCELLED'])
const TASK_TABS: Record<string, string> = {
  DEEPTESTING: 'DeepTesting (/deeptesting)',
  MONTE_CARLO: 'Monte Carlo (/monte-carlo)',
  DATA_FETCHING: 'Data (/data)',
  NODE_GENERATION: 'Manual Nodes (/manual-nodes)',
  VALIDATION: 'Manual Nodes (/manual-nodes)',
}

function failureInfo(task: DashboardTask) {
  const nodeFailures = ((task.payload?.nodes ?? []) as any[])
    .filter((n) => ['FAILED', 'BLOCKED'].includes(String(n?.status ?? '').toUpperCase()))
  const first = nodeFailures[0]
  const raw = String(first?.error ?? task.error ?? task.summary ?? 'No error message was recorded.')
  const fileMatch = raw.match(/File ["']([^"']+)["'], line (\d+), in ([^\n]+)/)
    ?? raw.match(/ at ([^\s:]+):(\d+) ([^:\n]+):/)
  const stageMatch = raw.match(/(?:stage|Stage)\s*[:=]\s*([A-Z0-9_ -]+)/)
  const tracebackAt = raw.indexOf('Traceback (most recent call last):')
  return {
    nodeFailures,
    stage: first?.stage ?? task.payload?.stage ?? stageMatch?.[1]?.trim() ?? 'Not captured',
    errorCode: first?.error_code ?? task.payload?.error_code ?? 'Not captured',
    message: first?.error ?? task.error ?? task.summary ?? 'No error message was recorded.',
    file: first?.exception_file ?? fileMatch?.[1] ?? 'Not captured',
    line: first?.exception_line ?? fileMatch?.[2] ?? 'Not captured',
    functionName: first?.exception_function ?? fileMatch?.[3] ?? 'Not captured',
    traceback: first?.traceback ?? (tracebackAt >= 0 ? raw.slice(tracebackAt) : ''),
  }
}

/** Global, per-task progress notifications. Actual percentages and statuses
 * come from the shared backend task records. Successful/cancelled notices are
 * acknowledged individually after five seconds; failures remain until the
 * user acknowledges them, so one task can never dismiss a sibling. */
export function TaskProgressBars() {
  const { tasks, refresh } = useTasks()
  const [dismissed, setDismissed] = useState<Record<string, boolean>>({})
  const [selectedFailure, setSelectedFailure] = useState<DashboardTask | null>(null)
  const timers = useRef(new Map<string, number>())

  useEffect(() => {
    tasks.filter((t) => AUTO_DISMISS.has(t.status)).forEach((t) => {
      const key = `${t.id}:${t.status}`
      if (dismissed[key] || timers.current.has(key)) return
      const elapsedMs = t.finished_at ? Date.now() - t.finished_at * 1000 : 0
      const remainingMs = Math.max(0, 5000 - Math.max(0, elapsedMs))
      const timer = window.setTimeout(() => {
        timers.current.delete(key)
        api.tasks.acknowledgeOne(t.id).then((result) => {
          if (result.acknowledged) setDismissed((prev) => ({ ...prev, [key]: true }))
          refresh()
        }).catch(() => {
          // Keep the notification visible when the server cannot persist its
          // acknowledgement. Retry on the next backend task-list refresh.
        })
      }, remainingMs)
      timers.current.set(key, timer)
    })
  }, [tasks, dismissed, refresh])

  useEffect(() => () => {
    timers.current.forEach(window.clearTimeout)
    timers.current.clear()
  }, [])

  const visibleTasks = tasks.filter((t) => !dismissed[`${t.id}:${t.status}`])
  const acknowledgeFailure = (task: DashboardTask) => {
    const key = `${task.id}:${task.status}`
    setSelectedFailure(null)
    api.tasks.acknowledgeOne(task.id).then((result) => {
      if (result.acknowledged) setDismissed((prev) => ({ ...prev, [key]: true }))
      refresh()
    }).catch(() => {
      // The panel closes on explicit user action; if persistence fails, the
      // server continues returning the failed task and its bar remains.
    })
  }

  if (!visibleTasks.length && !selectedFailure) return null
  return (
    <div className="task-progress-area" role="region" aria-label="Task progress">
      {visibleTasks.map((t) => {
        const terminal = TERMINAL.includes(t.status)
        const needsDetails = t.status === 'FAILED' || t.status === 'BLOCKED' || t.status === 'CANCELLED'
        const color = TASK_COLORS[t.category] ?? '#8b949e'
        const bar = <>
          <span className="task-bar-cat" style={{ color }}>{t.category.replace('_', ' ')}</span>
          <span className="task-bar-label">
            {t.completed_units} / {t.total_units} {t.category === 'DEEPTESTING' ? 'nodes tested' : 'done'}
            {t.label ? ` · ${t.label}` : ''}
            {t.status === 'RUNNING' && t.current_unit ? ` · ${t.current_unit}` : ''}
            {terminal ? ` · ${t.status}` : ''}
          </span>
          <span className="task-bar-track" role="progressbar" aria-valuenow={Math.round(t.percentage)}
                aria-valuemin={0} aria-valuemax={100} aria-label={`${t.category} progress`}>
            <span className="task-bar-fill"
                  style={{ width: `${Math.min(100, Math.max(0, t.percentage))}%`, background: color }} />
          </span>
          <span className="task-bar-pct">{Math.round(t.percentage)}%</span>
        </>
        return needsDetails
          ? <button key={t.id} type="button" className={`task-bar task-${t.status.toLowerCase()} task-bar-clickable`}
                    data-category={t.category} aria-label={`Show error details for ${t.label || t.category}`}
                    onClick={() => setSelectedFailure(t)}>{bar}</button>
          : <div key={t.id} className={`task-bar task-${t.status.toLowerCase()}`} data-category={t.category}>{bar}</div>
      })}
      {selectedFailure && (() => {
        const info = failureInfo(selectedFailure)
        return <section className="task-error-panel" role="dialog" aria-label="Failed task details">
          <div className="task-error-head">
            <div>
              <strong>{selectedFailure.label || selectedFailure.category}</strong>
              <div className="task-error-sub">{selectedFailure.status} · {TASK_TABS[selectedFailure.category] ?? selectedFailure.category}</div>
            </div>
            <button type="button" className="task-error-close" aria-label="Close error details and acknowledge task"
                    onClick={() => acknowledgeFailure(selectedFailure)}>×</button>
          </div>
          <dl className="task-error-grid">
            <dt>Task ID</dt><dd>{selectedFailure.id}</dd>
            <dt>Stage</dt><dd>{info.stage}</dd>
            <dt>Error code</dt><dd>{info.errorCode}</dd>
            <dt>Error</dt><dd className="task-error-message">{info.message}</dd>
            <dt>Module / file</dt><dd>{info.file}</dd>
            <dt>Function</dt><dd>{info.functionName}</dd>
            <dt>Source line</dt><dd>{info.line}</dd>
            <dt>Failed at</dt><dd>{selectedFailure.finished_at ? new Date(selectedFailure.finished_at * 1000).toLocaleString() : 'Not recorded'}</dd>
          </dl>
          {info.traceback && <details open className="task-error-trace">
            <summary>Traceback</summary><pre>{info.traceback}</pre>
          </details>}
          {info.nodeFailures.length > 1 && <p className="task-error-sub">{info.nodeFailures.length} failed/blocked node records are preserved in task history.</p>}
        </section>
      })()}
    </div>
  )
}
