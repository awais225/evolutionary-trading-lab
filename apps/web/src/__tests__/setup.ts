import '@testing-library/jest-dom/vitest'
import { vi, beforeEach } from 'vitest'

// Deterministic fetch mock: routes by URL suffix. Unknown -> 404 JSON error.
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

const SETTINGS = {
  settings: { log_level: 'INFO', research_max_sources: 40, research_max_minutes: 30, research_max_tokens: 200000, research_require_evidence: true },
  environment: { data_root: '<test-data-root>', ollama_host: 'http://127.0.0.1:11434', ollama_model: 'qwen2.5:0.5b' },
}

const SPEC_FINGERPRINT = 'eb132930ec461848abb54173d50cdeda6f812581e39f7cefac1f4155935a689c'

const baseNode = {
  id: 1, instrument_id: 'XAUUSD', origin: 'CUSTOM', name: 'x', description: null,
  status: 'DRAFT', approval_state: 'PENDING', version: 1, parent_node_id: null,
  spec_hash: null as string | null, spec_json: null as string | null,
  created_by: 'test', created_at: 0, updated_at: 0,
}

function route(url: string, method: string, bodyText?: string): Response {
  const body = bodyText ? JSON.parse(bodyText) : undefined
  if (url.endsWith('/health')) return json({ status: 'ok', version: '1.0.0', checks: { registry_db: { status: 'healthy' }, ollama: { status: 'healthy' }, mt5: { status: 'unavailable' } } })

  if (url.includes('/api/v1/instruments/') && url.includes('/nodes')) {
    // Phase 2 spec pipeline endpoints
    if (url.includes('/validate')) {
      const spec = body?.spec ?? {}
      const needsMeta = JSON.stringify(spec).includes('"pips"') && !body?.broker_meta
      return json({
        instrument: 'XAUUSD',
        status: needsMeta ? 'NEEDS_METADATA' : 'VALID',
        issues: needsMeta ? [{ code: 'RISK_METADATA_REQUIRED', path: 'exits.stop_loss.distance', message: 'pip_size is required', severity: 'NEEDS_METADATA', remediation: 'supply broker_meta' }] : [],
        required_warmup_candles: 1, schema_version: '1.0.0',
        summary: needsMeta ? { invalid: 0, warning: 0, needs_metadata: 1 } : { invalid: 0, warning: 0, needs_metadata: 0 },
      })
    }
    if (url.includes('/compile')) {
      if (JSON.stringify(body?.spec ?? {}).includes('"pips"') && !body?.broker_meta) {
        return json({ detail: { code: 'SPEC_NEEDS_METADATA', message: 'specification is needs_metadata and cannot be saved/compiled', status: 'NEEDS_METADATA', issues: [{ code: 'RISK_METADATA_REQUIRED', path: 'exits.stop_loss.distance', message: 'pip_size is required', severity: 'NEEDS_METADATA', remediation: 'supply broker_meta' }] } }, 422)
      }
      return json({
        fingerprint: SPEC_FINGERPRINT, canonical_json: '{"canonical":true}',
        schema_version: '1.0.0', compiler_version: '1.0.0', ir_version: '1.0.0',
        ir: { ir_version: '1.0.0', strategy: { instrument: 'XAUUSD', origin: 'CUSTOM' } },
        required_warmup_candles: 1, warnings: [],
      })
    }
    if (url.includes('/lineage')) {
      return json({ instrument: 'XAUUSD', lineage_root_id: 1, items: [baseNode] })
    }
    if (/\/nodes\/\d+\/versions$/.test(url)) {
      return json({ ...baseNode, id: 2, version: 2, parent_node_id: 1, spec_hash: SPEC_FINGERPRINT, spec_json: '{"schema_version":"1.0.0"}' }, 201)
    }
    if (method === 'POST') {
      const hasSpec = typeof body?.spec_json === 'string'
      return json({ ...baseNode, spec_hash: hasSpec ? SPEC_FINGERPRINT : null, spec_json: hasSpec ? body.spec_json : null }, 201)
    }
    return json({ instrument: 'XAUUSD', total: 0, items: [] })
  }
  if (url.includes('/api/v1/instruments')) return json({ instruments: [{ symbol: 'XAUUSD', display_name: 'Gold Spot vs US Dollar', asset_class: 'METAL', enabled: true, default_timeframe: 'M15', digits: 2, contract_size: 100 }] })
  if (url.includes('/api/v1/settings')) return json(SETTINGS)
  if (url.includes('/api/v1/events')) return json(method === 'POST' ? { id: 42 } : { limit: 50, count: 0, events: [] })
  return json({ detail: 'not found' }, 404)
}

beforeEach(() => {
  localStorage.clear()
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
    route(String(input), init?.method ?? 'GET', typeof init?.body === 'string' ? init.body : undefined)))
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
