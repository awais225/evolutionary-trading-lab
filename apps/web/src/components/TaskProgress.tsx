import { TASK_COLORS, TERMINAL, useTasks } from '../state/tasks'

/** Global dashboard task progress area (Section 1): one bar per active or
 * completed-but-unacknowledged task, visible identically on every tab.
 * Percentages come straight from the shared backend task records. */
export function TaskProgressBars() {
  const { tasks } = useTasks()
  if (!tasks.length) return null
  return (
    <div className="task-progress-area" role="region" aria-label="Task progress">
      {tasks.map((t) => {
        const terminal = TERMINAL.includes(t.status)
        const color = TASK_COLORS[t.category] ?? '#8b949e'
        return (
          <div key={t.id} className={`task-bar task-${t.status.toLowerCase()}`}
               data-category={t.category}>
            <span className="task-bar-cat" style={{ color }}>{t.category.replace('_', ' ')}</span>
            <span className="task-bar-label">
              {t.completed_units} / {t.total_units} {t.category === 'DEEPTESTING' ? 'nodes tested' : 'done'}
              {t.current_unit && t.status === 'RUNNING' ? ` · ${t.current_unit}` : ''}
              {terminal ? ` · ${t.status}` : ''}
              {t.error ? ` · ${t.error}` : ''}
            </span>
            <span className="task-bar-track" role="progressbar" aria-valuenow={Math.round(t.percentage)}
                  aria-valuemin={0} aria-valuemax={100} aria-label={`${t.category} progress`}>
              <span className="task-bar-fill"
                    style={{ width: `${Math.min(100, Math.max(0, t.percentage))}%`, background: color }} />
            </span>
            <span className="task-bar-pct">{Math.round(t.percentage)}%</span>
          </div>
        )
      })}
    </div>
  )
}
