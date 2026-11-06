import { createContext, useContext, useState, useEffect, type ReactNode } from 'react'
import type { InstrumentSymbol } from '../api/types'

const InstrumentContext = createContext<{ instrument: InstrumentSymbol; setInstrument: (s: InstrumentSymbol) => void }>({
  instrument: 'XAUUSD',
  setInstrument: () => undefined,
})

const INSTRUMENTS: InstrumentSymbol[] = ['XAUUSD', 'EURUSD', 'BTCUSD']

export function InstrumentProvider({ children }: { children: ReactNode }) {
  const [instrument, setInstrumentState] = useState<InstrumentSymbol>(() => {
    const saved = localStorage.getItem('hero1.instrument') as InstrumentSymbol | null
    return saved && INSTRUMENTS.includes(saved) ? saved : 'XAUUSD'
  })
  const setInstrument = (s: InstrumentSymbol) => {
    setInstrumentState(s)
    localStorage.setItem('hero1.instrument', s)
  }
  useEffect(() => { document.documentElement.dataset.instrument = instrument }, [instrument])
  return <InstrumentContext.Provider value={{ instrument, setInstrument }}>{children}</InstrumentContext.Provider>
}

export function useInstrument() {
  return useContext(InstrumentContext)
}
export const ALL_INSTRUMENTS = INSTRUMENTS
