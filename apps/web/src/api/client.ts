import type { BrokerMeta, CompileResult, EventListOut, HealthOut, Instrument, Node, NodeListOut, NodeOrigin, NodeVersionList, SettingsOut, ValidationReport } from './types'

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
  events: (params: { instrument?: string; category?: string; severity?: string; limit?: number } = {}) => {
    const q = new URLSearchParams()
    if (params.instrument) q.set('instrument', params.instrument)
    if (params.category) q.set('category', params.category)
    if (params.severity) q.set('severity', params.severity)
    if (params.limit) q.set('limit', String(params.limit))
    return request<EventListOut>('GET', `/api/v1/events?${q}`)
  },
  postEvent: (body: { category: string; severity?: string; instrument?: string; subsystem?: string; message: string }) =>
    request<HeroEventCreated>('POST', '/api/v1/events', body),
  nodes: {
    list: (instrument: string, params: { origin?: string } = {}) => {
      const q = new URLSearchParams()
      if (params.origin) q.set('origin', params.origin)
      return request<NodeListOut>('GET', `/api/v1/instruments/${instrument}/nodes?${q}`)
    },
    get: (instrument: string, id: number) =>
      request<Node>('GET', `/api/v1/instruments/${instrument}/nodes/${id}`),
    create: (instrument: string, body: {
      name: string; origin: NodeOrigin; description?: string;
      spec_json?: string; broker_meta?: BrokerMeta; parent_node_id?: number;
    }) => request<Node>('POST', `/api/v1/instruments/${instrument}/nodes`, body),
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
  },
  settings: {
    get: () => request<SettingsOut>('GET', '/api/v1/settings'),
    put: (settings: Record<string, unknown>) => request<SettingsOut>('PUT', '/api/v1/settings', { settings }),
  },
}

export interface HeroEventCreated { id: number }

export { ApiError }
