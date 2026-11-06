import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'

/**
 * Cross-tab timeframe scope ("All" | explicit set) for node-selection surfaces
 * (Manual Nodes chips / BatchScope for DeepTesting·Fine Tuning·Live Testing).
 * UI preference only — persisted to localStorage; the SERVER-side nodes list
 * enforces the filter (?timeframe=), the context is a convenience.
 * Stored as a comma-joined list; empty list = all timeframes.
 */

const STORAGE_KEY = 'hero1.timeframeScope'

interface TfScopeCtx {
  timeframes: string[]            // empty = all
  setTimeframes: (tfs: string[]) => void
}

const Ctx = createContext<TfScopeCtx>({ timeframes: [], setTimeframes: () => undefined })

function initial(): string[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const tfs = raw.split(',').map((t) => t.trim().toUpperCase()).filter(Boolean)
    return tfs.length ? tfs : []
  } catch { return [] }
}

export function TimeframeScopeProvider({ children }: { children: ReactNode }) {
  const [timeframes, setState] = useState<string[]>(initial)
  useEffect(() => {
    try { window.localStorage.setItem(STORAGE_KEY, timeframes.join(',')) } catch { /* private mode */ }
  }, [timeframes])
  const setTimeframes = (tfs: string[]) => setState([...new Set(tfs.map((t) => t.toUpperCase()))])
  return <Ctx.Provider value={{ timeframes, setTimeframes }}>{children}</Ctx.Provider>
}

export function useTimeframeScope() {
  return useContext(Ctx)
}
