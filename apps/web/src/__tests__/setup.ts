import '@testing-library/jest-dom/vitest'
import { vi, beforeEach } from 'vitest'

// Deterministic fetch mock: routes by URL suffix. Unknown -> 404 JSON error.
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

const SETTINGS = {
  settings: { log_level: 'INFO', research_max_sources: 40, research_max_minutes: 30, research_max_tokens: 200000, research_require_evidence: true },
  environment: { data_root: '<test-data-root>', ollama_host: 'http://127.0.0.1:11434', ollama_model: 'qwen2.5:0.5b' },
}

function route(url: string, method: string): Response {
  if (url.endsWith('/health')) return json({ status: 'ok', version: '1.0.0', checks: { registry_db: { status: 'healthy' }, ollama: { status: 'healthy' }, mt5: { status: 'unavailable' } } })
  if (url.includes('/api/v1/instruments/') && url.includes('/nodes')) {
    if (method === 'POST') return json({ id: 1, instrument_id: 'XAUUSD', origin: 'CUSTOM', name: 'x', description: null, status: 'DRAFT', approval_state: 'PENDING', version: 1, parent_node_id: null, spec_hash: null, created_by: 'test', created_at: 0, updated_at: 0 }, 201)
    return json({ instrument: 'XAUUSD', total: 0, items: [] })
  }
  if (url.includes('/api/v1/instruments')) return json({ instruments: [{ symbol: 'XAUUSD', display_name: 'Gold Spot vs US Dollar', asset_class: 'METAL', enabled: true, default_timeframe: 'M15', digits: 2, contract_size: 100 }] })
  if (url.includes('/api/v1/settings')) return json(SETTINGS)
  if (url.includes('/api/v1/events')) return json(method === 'POST' ? { id: 42 } : { limit: 50, count: 0, events: [] })
  return json({ detail: 'not found' }, 404)
}

beforeEach(() => {
  localStorage.clear()
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => route(String(input), init?.method ?? 'GET')))
  // Deterministic WS that never opens: feed stays in connecting state, no timers storm
  vi.stubGlobal('WebSocket', class {
    static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3
    readyState = 0
    onopen: (() => void) | null = null
    onmessage: ((e: MessageEvent) => void) | null = null
    onclose: (() => void) | null = null
    onerror: (() => void) | null = null
    constructor(public url: string) { /* no-op */ }
    close() { this.readyState = 3; this.onclose?.() }
    send() { /* no-op */ }
  } as unknown as typeof WebSocket)
})
