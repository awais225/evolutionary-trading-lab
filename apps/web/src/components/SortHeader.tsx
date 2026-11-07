import type { SortState } from '../lib/tableSort'

/** Clickable sortable column header (ISSUE-055): first click applies the
 *  column's default direction, second reverses. The indicator is always
 *  visible on the active column; sortable-but-inactive columns show a
 *  faint affordance. Renders a <th> so it drops into existing tables. */
export function SortHeader({ label, columnKey, state, onToggle, title }: {
  label: string
  columnKey: string
  state: SortState | null
  onToggle: (key: string) => void
  title?: string
}) {
  const active = state?.key === columnKey
  return (
    <th aria-sort={active ? (state.dir === 'asc' ? 'ascending' : 'descending') : undefined}>
      <button type="button" className="sort-btn" title={title ?? `Sort by ${label}`}
              onClick={() => onToggle(columnKey)}>
        <span>{label}</span>
        <span className={active ? 'sort-ind' : 'sort-ind idle'} aria-hidden="true">
          {active ? (state.dir === 'asc' ? '▲' : '▼') : '⇅'}
        </span>
      </button>
    </th>
  )
}
