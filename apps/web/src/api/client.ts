import type { BrokerMeta, CompileResult, EventListOut, HealthOut, Instrument, InstrumentMetadataView, MarketDataAvailability, MarketDataFetchReport, Mt5ConnectionResult, Mt5TerminalsOut, Node, NodeGenerateOut, NodeListOut, NodeOrigin, NodeVersionList, SettingsOut, ValidationReport } from './types'

class ApiError extends Error {
  constructor(public status: number, message: string,
              public code?: string, public issues?: unknown) {
    super(message)
  }
}

// FastAPI error bodies may carry `detail` as a plain string OR as a structured
// object ({code, message, status?, issues?}) — surface both faithfully.
function parseDetail(data: unknown): { message: string; code?: string; issues?: unknown } {
  const d = (data as { detail?: unknown } | undefined)?.detail
  if (typeof d === 'string') return { message: d }
  if (d && typeof d === 'object') {
    const obj = d as { message?: string; code?: string; issues?: unknown }
    if (typeof obj.message === 'string') {
      return { message: obj.message, code: obj.code, issues: obj.issues }
    }
  }
  const errMsg = (data as { error?: { message?: string } } | undefined)?.error?.message
  return { message: errMsg ?? 'request failed' }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, {
      method,
      headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  } catch (err) {
    throw new ApiError(0, `backend unreachable (${err instanceof Error ? err.message : String(err)})`)
  }
  const text = await res.text()
  let data: unknown = undefined
  if (text) {
    try { data = JSON.parse(text) } catch { /* non-JSON body */ }
  }
  if (!res.ok) {
    const { message, code, issues } = parseDetail(data)
    throw new ApiError(res.status, message, code, issues)
  }
  return data as T
}

export const api = {
  health: () => request<HealthOut>('GET', '/health'),
  instruments: () => request<{ instruments: Instrument[] }>('GET', '/api/v1/instruments').then((r) => r.instruments),
  events: (params: { instrument?: string; category?: string; severity?: string; subsystem?: string; search?: string; limit?: number } = {}) => {
    const q = new URLSearchParams()
    if (params.instrument) q.set('instrument', params.instrument)
    if (params.category) q.set('category', params.category)
    if (params.severity) q.set('severity', params.severity)
    if (params.subsystem) q.set('subsystem', params.subsystem)
    if (params.search) q.set('search', params.search)
    if (params.limit) q.set('limit', String(params.limit))
    return request<EventListOut>('GET', `/api/v1/events?${q}`)
  },
  postEvent: (body: { category: string; severity?: string; instrument?: string; subsystem?: string; message: string; payload?: Record<string, unknown> }) =>
    request<HeroEventCreated>('POST', '/api/v1/events', body),
  nodes: {
    list: (instrument: string, params: { origin?: string; starred?: boolean; limit?: number; timeframe?: string[] } = {}) => {
      const q = new URLSearchParams()
      if (params.origin) q.set('origin', params.origin)
      if (params.starred === true) q.set('starred', 'true')
      else if (params.starred === false) q.set('starred', 'false')
      if (params.timeframe && params.timeframe.length > 0) q.set('timeframe', params.timeframe.join(','))
      if (params.limit) q.set('limit', String(params.limit))
      return request<NodeListOut>('GET', `/api/v1/instruments/${instrument}/nodes?${q}`)
    },
    get: (instrument: string, id: number) =>
      request<Node>('GET', `/api/v1/instruments/${instrument}/nodes/${id}`),
    create: (instrument: string, body: {
      name: string; origin: NodeOrigin; description?: string;
      spec_json?: string; broker_meta?: BrokerMeta; parent_node_id?: number;
    }) => request<Node>('POST', `/api/v1/instruments/${instrument}/nodes`, body),
    // Multi-timeframe generation — one independent node PER timeframe (all-or-nothing).
    // `name` is honoured ONLY for a single timeframe; multiple timeframes allocate
    // sequential first-available C-names server-side.
    generate: (instrument: string, body: {
      spec: Record<string, unknown>; timeframes: string[];
      name?: string; description?: string; broker_meta?: BrokerMeta; created_by?: string;
    }) => request<NodeGenerateOut>('POST', `/api/v1/instruments/${instrument}/nodes/generate`, body),
    validate: (instrument: string, spec: Record<string, unknown>, broker_meta?: BrokerMeta) =>
      request<ValidationReport>('POST', `/api/v1/instruments/${instrument}/nodes/validate`, { spec, broker_meta }),
    compile: (instrument: string, spec: Record<string, unknown>, broker_meta?: BrokerMeta) =>
      request<CompileResult>('POST', `/api/v1/instruments/${instrument}/nodes/compile`, { spec, broker_meta }),
    versions: (instrument: string, id: number, body: {
      spec: Record<string, unknown>; broker_meta?: BrokerMeta;
      name?: string; description?: string; created_by?: string;
    }) => request<Node>('POST', `/api/v1/instruments/${instrument}/nodes/${id}/versions`, body),
    lineage: (instrument: string, id: number) =>
      request<NodeVersionList>('GET', `/api/v1/instruments/${instrument}/nodes/${id}/lineage`),
    // Metadata-only rename (name/description/status/approval). Never touches spec_json,
    // so it cannot change the semantic hash or fork a version.
    rename: (instrument: string, id: number, name: string) =>
      request<Node>('PATCH', `/api/v1/instruments/${instrument}/nodes/${id}`, { name }),
    // Favorite toggle — research metadata only (server contract: never touches
    // spec_json/spec_hash/version/lineage/validation).
    setStarred: (instrument: string, id: number, starred: boolean) =>
      request<Node>('PATCH', `/api/v1/instruments/${instrument}/nodes/${id}`, { starred }),
  },
  settings: {
    get: () => request<SettingsOut>('GET', '/api/v1/settings'),
    put: (settings: Record<string, unknown>) => request<SettingsOut>('PUT', '/api/v1/settings', { settings }),
  },
  mt5: {
    status: () => request<Mt5ConnectionResult>('GET', '/api/v1/mt5/status'),
    terminals: () => request<Mt5TerminalsOut>('GET', '/api/v1/mt5/terminals'),
    connect: (terminal_path?: string) =>
      request<Mt5ConnectionResult>('POST', '/api/v1/mt5/connect', terminal_path ? { terminal_path } : {}),
    disconnect: () => request<{ ok: boolean; detail: string }>('POST', '/api/v1/mt5/disconnect'),
  },
  marketData: {
    get: (instrument: string) => request<MarketDataAvailability>('GET', `/api/v1/instruments/${instrument}/market-data`),
    fetch: (instrument: string, timeframe = 'ALL', operationId?: string) =>
      request<MarketDataFetchReport>('POST', `/api/v1/instruments/${instrument}/market-data/fetch`, { timeframe, operation_id: operationId }),
  },
  metadata: {
    get: (instrument: string) => request<InstrumentMetadataView>('GET', `/api/v1/instruments/${instrument}/metadata`),
    refresh: (instrument: string) => request<InstrumentMetadataView>('POST', `/api/v1/instruments/${instrument}/metadata/refresh`),
    import: (instrument: string, body: {
      broker_server: string; symbol_info: Record<string, unknown>; tick?: Record<string, unknown>;
      terminal_identity?: Record<string, unknown>; account_login?: number; source?: string;
    }) => request<InstrumentMetadataView>('POST', `/api/v1/instruments/${instrument}/metadata/import`, body),
  },
}

export interface HeroEventCreated { id: number }

export { ApiError }
