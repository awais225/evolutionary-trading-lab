import { createContext, useContext, useState, type ReactNode } from 'react'

const FeedContext = createContext<{ open: boolean; setOpen: (v: boolean) => void; toggle: () => void }>({
  open: true,
  setOpen: () => undefined,
  toggle: () => undefined,
})

export function FeedProvider({ children }: { children: ReactNode }) {
  const [open, setOpenState] = useState<boolean>(() => {
    const saved = localStorage.getItem('hero1.feedOpen')
    return saved === null ? true : saved === '1'
  })
  const setOpen = (v: boolean) => {
    setOpenState(v)
    localStorage.setItem('hero1.feedOpen', v ? '1' : '0')
  }
  const toggle = () => setOpen(!open)
  return <FeedContext.Provider value={{ open, setOpen, toggle }}>{children}</FeedContext.Provider>
}

export function useFeed() {
  return useContext(FeedContext)
}
