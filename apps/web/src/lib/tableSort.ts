import { useCallback, useEffect, useState } from 'react'

/**
 * Universal table sorting (ISSUE-055).
 *
 * One system for every Hero1 table:
 *  - each column declares a value type; the FIRST click on a column applies
 *    that type's default direction (numeric metrics: descending; names/IDs:
 *    natural ascending; dates: newest first; text: A-Z), a second click
 *    reverses, further clicks alternate;
 *  - 'absNumber' compares ABSOLUTE magnitude (drawdown: -500 before -100 in
 *    default descending order - sign is display, magnitude is size);
 *  - 'natural' orders identifiers like C2 < C10 (never lexicographic);
 *  - null / undefined / '' / 'N/A' are MISSING - never converted to zero -
 *    and always sort last, in both directions;
 *  - sorting is STABLE (equal values keep their previous relative order)
 *    and never mutates the input array;
 *  - the preference persists per table in localStorage
 *    (`hero1.sort.<tableId>`) - the same mechanism as the instrument,
 *    starred-scope and timeframe-scope preferences - so it survives tab
 *    switches and application restarts.
 */

export type SortDir = 'asc' | 'desc'
export type SortValueType = 'number' | 'absNumber' | 'natural' | 'date' | 'text'

export interface SortColumn<T> {
  key: string
  label: string
  type: SortValueType
  get: (row: T) => unknown
  defaultDir?: SortDir
}

export interface SortState { key: string; dir: SortDir }

export const DEFAULT_DIR_FOR: Record<SortValueType, SortDir> = {
  number: 'desc',     // numeric metrics: maximum first
  absNumber: 'desc',  // drawdown: largest magnitude first
  natural: 'asc',     // names / IDs: C2 before C10
  date: 'desc',       // newest first
  text: 'asc',        // A to Z
}

export function defaultDir<T>(col: SortColumn<T>): SortDir {
  return col.defaultDir ?? DEFAULT_DIR_FOR[col.type]
}

/** First click on a column -> its default direction; clicking the active
 *  column again reverses; alternating from there. */
export function nextSortState<T>(current: SortState | null, col: SortColumn<T>): SortState {
  if (!current || current.key !== col.key) return { key: col.key, dir: defaultDir(col) }
  return { key: col.key, dir: current.dir === 'asc' ? 'desc' : 'asc' }
}

function isMissing(v: unknown): boolean {
  return v === null || v === undefined || v === '' || v === 'N/A'
}

function toComparable(v: unknown, type: SortValueType): number | string | null {
  if (isMissing(v)) return null
  switch (type) {
    case 'number':
    case 'absNumber': {
      const n = typeof v === 'number' ? v : Number(String(v).replace(/[% ,]/g, ''))
      if (!Number.isFinite(n)) return null
      return type === 'absNumber' ? Math.abs(n) : n
    }
    case 'date': {
      if (typeof v === 'number') return Number.isFinite(v) ? v : null
      const t = Date.parse(String(v))
      return Number.isFinite(t) ? t : null
    }
    case 'natural':
      return String(v)
    case 'text':
      return String(v).toLowerCase()
  }
}

/** Natural ordering: digit runs compare numerically (C2 < C10, #9 < #12). */
export function naturalCompare(a: string, b: string): number {
  const pa = a.split(/(\d+)/)
  const pb = b.split(/(\d+)/)
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const x = pa[i] ?? ''
    const y = pb[i] ?? ''
    if (x === y) continue
    const nx = /^\d+$/.test(x) ? Number(x) : null
    const ny = /^\d+$/.test(y) ? Number(y) : null
    if (nx !== null && ny !== null) return nx - ny
    return x.toLowerCase() < y.toLowerCase() ? -1 : 1
  }
  return 0
}

/** Core comparison. Missing values are equal to each other and ALWAYS lose
 *  (sort last) - direction is applied only to real values. */
export function compareForSort(a: unknown, b: unknown, type: SortValueType, dir: SortDir): number {
  const ca = toComparable(a, type)
  const cb = toComparable(b, type)
  if (ca === null && cb === null) return 0
  if (ca === null) return 1
  if (cb === null) return -1
  let cmp: number
  if (type === 'natural') cmp = naturalCompare(String(ca), String(cb))
  else if (typeof ca === 'number' && typeof cb === 'number') cmp = ca - cb
  else cmp = String(ca) < String(cb) ? -1 : String(ca) > String(cb) ? 1 : 0
  return dir === 'asc' ? cmp : -cmp
}

/** Stable, non-mutating sort of a full dataset by the active column. */
export function applySort<T>(rows: readonly T[], state: SortState | null,
                             columns: readonly SortColumn<T>[]): T[] {
  if (!state) return [...rows]
  const col = columns.find((c) => c.key === state.key)
  if (!col) return [...rows]
  return rows
    .map((row, index) => ({ row, index }))
    .sort((a, b) => compareForSort(col.get(a.row), col.get(b.row), col.type, state.dir) || a.index - b.index)
    .map((d) => d.row)
}

const STORAGE_PREFIX = 'hero1.sort.'

export function loadSortPref(tableId: string, validKeys: readonly string[]): SortState | null {
  try {
    const raw = localStorage.getItem(STORAGE_PREFIX + tableId)
    if (!raw) return null
    const parsed = JSON.parse(raw) as SortState
    if (parsed && validKeys.includes(parsed.key) && (parsed.dir === 'asc' || parsed.dir === 'desc')) {
      return { key: parsed.key, dir: parsed.dir }
    }
  } catch { /* storage unavailable (tests, privacy mode) */ }
  return null
}

export function saveSortPref(tableId: string, state: SortState | null): void {
  try {
    if (state) localStorage.setItem(STORAGE_PREFIX + tableId, JSON.stringify(state))
    else localStorage.removeItem(STORAGE_PREFIX + tableId)
  } catch { /* non-fatal */ }
}

/** Hook: sort state + persistence + a stable sorted copy of the rows. */
export function useTableSort<T>(tableId: string, columns: readonly SortColumn<T>[],
                                initial: SortState | null = null) {
  const [state, setState] = useState<SortState | null>(
    () => loadSortPref(tableId, columns.map((c) => c.key)) ?? initial)
  useEffect(() => { saveSortPref(tableId, state) }, [tableId, state])
  const toggle = useCallback((key: string) => {
    const col = columns.find((c) => c.key === key)
    if (col) setState((cur) => nextSortState(cur, col))
  }, [columns])
  const sorted = useCallback(
    (rows: readonly T[]) => applySort(rows, state, columns), [state, columns])
  return { state, toggle, sorted }
}
