import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'

/**
 * Global node-scope preference ("All Nodes" | "Starred Only").
 * ONE consistent filter across every tab that selects/ranks/validates/tests nodes.
 * This is a UI preference, never research data: persisted to localStorage only.
 * The server-side nodes list enforces the scope (starred=true) — the context is
 * a convenience, not a correctness boundary for batch operations.
 */
export type StarScope = 'all' | 'starred'

const STORAGE_KEY = 'hero1.nodeScope'

interface StarScopeCtx {
  scope: StarScope
  setScope: (s: StarScope) => void
}

const Ctx = createContext<StarScopeCtx>({ scope: 'all', setScope: () => undefined })

function initialScope(): StarScope {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === 'starred' ? 'starred' : 'all'
  } catch {
    return 'all'
  }
}

export function StarScopeProvider({ children }: { children: ReactNode }) {
  const [scope, setScopeState] = useState<StarScope>(initialScope)
  useEffect(() => {
    try { window.localStorage.setItem(STORAGE_KEY, scope) } catch { /* private mode */ }
  }, [scope])
  const setScope = (s: StarScope) => setScopeState(s)
  return <Ctx.Provider value={{ scope, setScope }}>{children}</Ctx.Provider>
}

export function useStarredScope() {
  return useContext(Ctx)
}
