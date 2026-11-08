import { useEffect, useMemo, useRef, useState } from 'react'
import { useInstrument } from '../state/instrument'
import { useFeed } from '../state/feed'
import { api } from '../api/client'
import { TASK_COLORS, useTasks } from '../state/tasks'
import { FeedSocket, type FeedStatus } from '../ws/feedSocket'
import type { HeroEvent } from '../api/types'

const CATEGORIES = ['SYSTEM', 'RESEARCH', 'NODE', 'BACKTEST', 'DEEPTEST', 'FINE_TUNING', 'MT5', 'LIVE_TRADING', 'ERROR', 'WARNING']
const MAX_EVENTS = 200

function timeOf(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], { hour12: false })
}

type CardFilter = { category: string; test: (e: HeroEvent) => boolean } | null

const CARD_FILTERS: Record<string, (e: HeroEvent) => boolean> = {
  DEEPTESTING: (e) => e.category === 'DEEPTEST',
  DATA_FETCHING: (e) => e.subsystem === 'market-data',
  NODE_GENERATION: (e) => e.category === 'NODE' && e.message.startsWith('node generated'),
  VALIDATION: (e) => e.category === 'NODE' && e.message.startsWith('spec validated'),
}

export function GlobalLiveFeed() {
  const { instrument } = useInstrument()
  const { open, setOpen } = useFeed()
  const { latest } = useTasks()
  const [events, setEvents] = useState<HeroEvent[]>([])
  const [filter, setFilter] = useState<string | null>(null)
  const [card, setCard] = useState<CardFilter>(null)
  const [status, setStatus] = useState<FeedStatus>('connecting')
  const [autoScroll, setAutoScroll] = useState(true)
  const bodyRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const socket = new FeedSocket(instrument, {
      onReplay: (evs) => setEvents((prev) => [...prev, ...evs].slice(-MAX_EVENTS)),
      onEvent: (ev) => setEvents((prev) => [...prev, ev].slice(-MAX_EVENTS)),
      onStatus: setStatus,
    })
    socket.connect()
    return () => socket.close()
  }, [instrument, open])

  useEffect(() => {
    if (autoScroll && bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight
  }, [events, autoScroll])

  const visible = useMemo(() => {
    let out = events
    if (card) out = out.filter(card.test)
    if (filter) out = out.filter((e) => e.category === filter)
    return out
  }, [events, filter, card])

  const cardStats = (cat: string): string[] => {
    const t = latest[cat]
    if (!t) return ['No tasks yet']
    const lines = [`Status: ${t.status}`, `${t.completed_units} / ${t.total_units} done`, `${Math.round(t.percentage)}%`]
    if (cat === 'DEEPTESTING' && t.payload?.nodes) {
      const nodes = t.payload.nodes as any[]
      const cur = nodes.find((n) => n.status === 'RUNNING')
      lines.push(`Completed ${nodes.filter((n) => n.status === 'COMPLETED').length} · failed/blocked ${nodes.filter((n) => ['FAILED', 'BLOCKED'].includes(n.status)).length} · remaining ${nodes.filter((n) => n.status === 'QUEUED').length}`)
      if (cur) lines.push(`Current: Node ${cur.node_id} — ${cur.name} · ${cur.trades} trades${cur.progress_pct != null ? ` · ${Math.round(cur.progress_pct)}%` : ''}`)
    }
    if (cat === 'DATA_FETCHING' && t.payload) {
      lines.push(`${(t.payload.timeframes ?? []).length} timeframe(s) · ${t.payload.candles_fetched ?? 0} candles`)
      if (t.current_unit) lines.push(`Stage: ${t.current_unit}`)
    }
    if (t.error) lines.push(t.error)
    return lines
  }
  if (!open) return null

  return (
    <aside className="livefeed" aria-label="Global live feed">
      <div className="livefeed-head">
        <span className="live-dot" aria-hidden="true" />
        <span className="livefeed-title">LIVE FEED</span>
        <span className="livefeed-count">{status === 'open' ? `STREAMING · ${visible.length}` : status === 'connecting' ? 'CONNECTING…' : 'RECONNECTING…'}</span>
        <button className="btn btn-ghost btn-sm" onClick={() => setOpen(false)} aria-label="Collapse live feed">✕</button>
      </div>
      <div className="feed-cards" role="group" aria-label="Subsystem activity">
        {Object.keys(TASK_COLORS).map((cat) => {
          const t = latest[cat]
          const active = card?.category === cat
          return (
            <button key={cat} type="button" aria-pressed={active}
                    className={`feed-card${active ? ' selected' : ''}`}
                    style={{ borderColor: TASK_COLORS[cat] }}
                    onClick={() => setCard(active ? null : { category: cat, test: CARD_FILTERS[cat] })}>
              <span className="feed-card-title" style={{ color: TASK_COLORS[cat] }}>{cat.replace('_', ' ')}</span>
              {cardStats(cat).map((line, i) => <span key={i} className="feed-card-line">{line}</span>)}
              <span className="feed-card-status">{t ? t.status : 'IDLE'}</span>
            </button>
          )
        })}
      </div>
      <div className="livefeed-filters" role="group" aria-label="Filter events">
        <button className={`chip${filter === null ? ' active' : ''}`} onClick={() => setFilter(null)}>All</button>
        {CATEGORIES.map((c) => (
          <button key={c} className={`chip${filter === c ? ' active' : ''}`} onClick={() => setFilter(filter === c ? null : c)}>{c}</button>
        ))}
      </div>
      <div className="livefeed-body" ref={bodyRef} onScroll={(e) => {
        const el = e.currentTarget
        setAutoScroll(el.scrollHeight - el.scrollTop - el.clientHeight < 40)
      }}>
        {visible.length === 0 ? (
          <div className="event-empty">
            No events yet.<br />
            Node creation, settings changes and background tasks will stream here in real time — no polling.
          </div>
        ) : visible.map((ev) => (
          <div className="event-row" key={ev.id} data-testid="event-row">
            <span className={`event-rail ${ev.severity}`} aria-hidden="true" />
            <span>
              <span className="event-meta">
                <span className="event-cat">{ev.category}</span>
                <span>{timeOf(ev.ts)}</span>
                {ev.instrument && <span>{ev.instrument}</span>}
                <span>{ev.subsystem}</span>
              </span>
              <div className="event-msg">{ev.message}</div>
            </span>
          </div>
        ))}
      </div>
      <div className="livefeed-foot">
        <button className="btn btn-sm" onClick={() => setEvents([])}>Clear view</button>
        <button className="btn btn-sm" onClick={async () => {
          try { await api.postEvent({ category: 'SYSTEM', severity: 'INFO', subsystem: 'livefeed', message: 'manual ping from LIVE FEED', instrument }) } catch { /* backend offline */ }
        }}>Send test event</button>
      </div>
      <div className="livefeed-note">Event-sourced over WebSocket with since_id replay. The feed never polls the database; history comes from the durable event log.</div>
    </aside>
  )
}
