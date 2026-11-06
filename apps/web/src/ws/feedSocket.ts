import type { HeroEvent } from '../api/types'

export type FeedStatus = 'connecting' | 'open' | 'reconnecting'

export interface FeedHandlers {
  onEvent: (ev: HeroEvent) => void
  onReplay: (evs: HeroEvent[]) => void
  onStatus: (s: FeedStatus) => void
}

/** Reconnecting WebSocket client for the global LIVE FEED.
 *  Server sends {type:'replay'} first, then {type:'event'} pushes.
 *  Reconnect uses since_id so missed events are replayed, never polled. */
export class FeedSocket {
  private ws: WebSocket | null = null
  private retry = 0
  private closed = false
  private sinceId = 0
  private timer: ReturnType<typeof setTimeout> | null = null

  constructor(private instrument: string, private handlers: FeedHandlers) {}

  connect(): void {
    this.closed = false
    this.open()
  }

  close(): void {
    this.closed = true
    if (this.timer) clearTimeout(this.timer)
    this.ws?.close()
    this.ws = null
  }

  private buildUrl(): string {
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const q = new URLSearchParams({ since_id: String(this.sinceId) })
    if (this.instrument) q.set('instrument', this.instrument)
    return `${proto}://${window.location.host}/ws/v1/events?${q.toString()}`
  }

  private open(): void {
    this.handlers.onStatus(this.retry === 0 ? 'connecting' : 'reconnecting')
    let ws: WebSocket
    try {
      ws = new WebSocket(this.buildUrl())
    } catch {
      this.scheduleReconnect()
      return
    }
    this.ws = ws
    ws.onopen = () => { this.retry = 0; this.handlers.onStatus('open') }
    ws.onmessage = (msg) => {
      try {
        const data = JSON.parse(String(msg.data))
        if (data.type === 'replay') {
          const evs = (data.events ?? []) as HeroEvent[]
          this.advance(evs)
          this.handlers.onReplay(evs)
        } else if (data.type === 'event') {
          const ev = data.event as HeroEvent
          this.advance([ev])
          this.handlers.onEvent(ev)
        }
      } catch { /* malformed frame — ignore */ }
    }
    ws.onclose = () => { if (!this.closed) this.scheduleReconnect() }
    ws.onerror = () => { ws.close() }
  }

  private advance(evs: HeroEvent[]): void {
    for (const e of evs) if (e.id && e.id > this.sinceId) this.sinceId = e.id
  }

  private scheduleReconnect(): void {
    if (this.closed) return
    const delay = Math.min(10_000, 500 * 2 ** this.retry) + Math.floor(Math.random() * 250)
    this.retry += 1
    this.handlers.onStatus('reconnecting')
    this.timer = setTimeout(() => this.open(), delay)
  }
}
