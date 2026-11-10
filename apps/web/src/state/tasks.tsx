import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { api } from '../api/client'

/** The ONE shared task-progress source (Sections 1/2/3/13): global bars,
 * Live Feed infographics and the DeepTesting node list all render from
 * these backend `dashboard_tasks` records - never from local counters. */

export interface DashboardTask {
  id: string
  category: string
  instrument: string | null
  label: string
  status: string
  total_units: number
  completed_units: number
  current_unit: string | null
  current_unit_progress: number | null
  percentage: number
  summary: string | null
  error: string | null
  acknowledged: boolean
  payload: any
  created_at: number
  finished_at: number | null
}

export const TASK_COLORS: Record<string, string> = {
  DEEPTESTING: '#e8b93e',
  DATA_FETCHING: '#58a6ff',
  NODE_GENERATION: '#3fb950',
  VALIDATION: '#bc8cff',
}

export const TERMINAL = ['COMPLETED', 'FAILED', 'CANCELLED', 'BLOCKED']

interface TasksCtx {
  tasks: DashboardTask[]
  latest: Record<string, DashboardTask>
  refresh: () => void
  refreshNow: () => Promise<void>
}

const Ctx = createContext<TasksCtx>({ tasks: [], latest: {}, refresh: () => {}, refreshNow: async () => {} })

export function TasksProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<{ tasks: DashboardTask[]; latest: Record<string, DashboardTask> }>(
    { tasks: [], latest: {} })
  const mounted = useRef(true)
  const refreshFlight = useRef<Promise<void> | null>(null)

  const refreshNow = (): Promise<void> => {
    if (refreshFlight.current) return refreshFlight.current
    const request = api.tasks.list().then((r) => {
      if (mounted.current) setState({ tasks: r.tasks ?? [], latest: r.latest ?? {} })
    }).finally(() => { refreshFlight.current = null })
    refreshFlight.current = request
    return request
  }
  const refresh = () => { void refreshNow().catch(() => { /* preserve last good state */ }) }

  useEffect(() => {
    mounted.current = true
    refresh()
    const iv = setInterval(refresh, 2000)
    return () => { mounted.current = false; clearInterval(iv) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return <Ctx.Provider value={{ tasks: state.tasks, latest: state.latest, refresh, refreshNow }}>{children}</Ctx.Provider>
}

export function useTasks(): TasksCtx {
  return useContext(Ctx)
}

/** Acknowledge a category's completed tasks (opening its tab). Active
 * tasks are never dismissed - the backend enforces that. */
export function acknowledgeCategory(category: string): void {
  api.tasks.acknowledge(category).catch(() => { /* offline: stays visible */ })
}
