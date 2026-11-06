import type { EventListOut, HealthOut, Instrument, Node, NodeListOut, NodeOrigin, SettingsOut } from './types'

class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
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
    const detail = (data as { detail?: unknown; error?: { message?: string } } | undefined)?.detail
      ?? (data as { error?: { message?: string } } | undefined)?.error?.message
    throw new ApiError(res.status, typeof detail === 'string' ? detail : `HTTP ${res.status}`)
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
    create: (instrument: string, body: { name: string; origin: NodeOrigin; description?: string }) =>
      request<Node>('POST', `/api/v1/instruments/${instrument}/nodes`, body),
  },
  settings: {
    get: () => request<SettingsOut>('GET', '/api/v1/settings'),
    put: (settings: Record<string, unknown>) => request<SettingsOut>('PUT', '/api/v1/settings', { settings }),
  },
}

export interface HeroEventCreated { id: number }

export { ApiError }
