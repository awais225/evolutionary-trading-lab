import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api } from '../api/client'
import type { InstrumentSymbol } from '../api/types'

export interface SelectorInstrument {
  symbol: string
  display_name: string
  enabled: boolean
}

/**
 * Global instrument selection — one shared source of truth for every tab.
 *
 * PERSISTENCE POLICY (documented, deterministic):
 *   - selection persists in localStorage key `hero1.instrument`;
 *   - default is ALWAYS XAUUSD when no selection has been saved, or when the saved
 *     selection is no longer enabled in the backend registry;
 *   - the list of selectable instruments comes from the backend registry
 *     (GET /api/v1/instruments). If the backend is unreachable we fall back to the
 *     known seeded registry (XAUUSD/EURUSD/BTCUSD) and flag the source as fallback.
 *   - selection is passed explicitly to every frontend request that carries an
 *     instrument (all /api/v1/instruments/{symbol}/... calls).
 */

const InstrumentContext = createContext<{
  instrument: InstrumentSymbol
  setInstrument: (s: InstrumentSymbol) => void
  instruments: SelectorInstrument[]
  instrumentsSource: 'backend' | 'fallback'
  instrumentsLoading: boolean
}>({
  instrument: 'XAUUSD',
  setInstrument: () => undefined,
  instruments: [],
  instrumentsSource: 'fallback',
  instrumentsLoading: true,
})

/** Static fallback = seeded backend registry. Not a fabricated list — it mirrors hero1_storage.KNOWN_INSTRUMENTS. */
const FALLBACK: SelectorInstrument[] = [
  { symbol: 'XAUUSD', display_name: 'Gold vs US Dollar', enabled: true },
  { symbol: 'EURUSD', display_name: 'Euro vs US Dollar', enabled: true },
  { symbol: 'BTCUSD', display_name: 'Bitcoin vs US Dollar', enabled: true },
]

/** Symbols shown greyed-out when the registry does not enable them — never invented, never selectable. */
export const EXAMPLE_UNSUPPORTED = ['GBPUSD', 'USDJPY', 'ETHUSD']

const STORAGE_KEY = 'hero1.instrument'

export function InstrumentProvider({ children }: { children: ReactNode }) {
  const [instruments, setInstruments] = useState<SelectorInstrument[]>(FALLBACK)
  const [instrumentsSource, setInstrumentsSource] = useState<'backend' | 'fallback'>('fallback')
  const [instrumentsLoading, setInstrumentsLoading] = useState(true)
  const [instrument, setInstrumentState] = useState<InstrumentSymbol>(() => {
    const saved = localStorage.getItem(STORAGE_KEY)
    // Accept any saved string here; once the registry answers we reconcile against it.
    return saved && saved.trim() ? saved.toUpperCase() : 'XAUUSD'
  })

  useEffect(() => {
    let cancelled = false
    api.instruments()
      .then((items) => {
        if (cancelled) return
        const list = items.map((i) => ({
          symbol: i.symbol.toUpperCase(),
          display_name: i.display_name,
          enabled: i.enabled,
        }))
        if (list.length === 0) {
          setInstruments(FALLBACK)
          setInstrumentsSource('fallback')
        } else {
          setInstruments(list)
          setInstrumentsSource('backend')
          // Reconcile saved selection against the live registry.
          const saved = (localStorage.getItem(STORAGE_KEY) || '').toUpperCase()
          const enabled = list.find((i) => i.symbol === saved && i.enabled)
          setInstrumentState((current) => {
            const curEnabled = list.find((i) => i.symbol === current && i.enabled)
            if (!curEnabled) {
              const next = enabled?.symbol ?? 'XAUUSD'
              localStorage.setItem(STORAGE_KEY, next)
              return next
            }
            return current
          })
        }
        setInstrumentsLoading(false)
      })
      .catch(() => {
        if (cancelled) return
        setInstruments(FALLBACK)
        setInstrumentsSource('fallback')
        setInstrumentsLoading(false)
      })
    return () => { cancelled = true }
  }, [])

  const setInstrument = (s: InstrumentSymbol) => {
    const next = s.toUpperCase()
    setInstrumentState(next)
    localStorage.setItem(STORAGE_KEY, next)
  }

  useEffect(() => { document.documentElement.dataset.instrument = instrument }, [instrument])

  const value = useMemo(
    () => ({ instrument, setInstrument, instruments, instrumentsSource, instrumentsLoading }),
    [instrument, instruments, instrumentsSource, instrumentsLoading],
  )
  return <InstrumentContext.Provider value={value}>{children}</InstrumentContext.Provider>
}

export function useInstrument() {
  return useContext(InstrumentContext)
}
export const ALL_INSTRUMENTS = FALLBACK.map((i) => i.symbol)
