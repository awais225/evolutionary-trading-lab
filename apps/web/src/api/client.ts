import type { BrokerMeta, CompileResult, EventListOut, HealthOut, Instrument, InstrumentMetadataView, MarketDataAvailability, MarketDataFetchReport, MarketDataOperation, Mt5ConnectionResult, Mt5TerminalsOut, Node, NodeDeleteResult, NodeDependencies, FetchConflict, NodeGenerateOut, NodeListOut, NodeOrigin, NodeVersionList, SettingsOut, ValidationReport } from './types'

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

export async function downloadFile(path: string, filename: string): Promise<void> {
  const res = await fetch(path)
  if (!res.ok) throw new ApiError(res.status, `download failed (${res.status})`)
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = filename; a.click()
  setTimeout(() => URL.revokeObjectURL(url), 5000)
}

export const api = {
  health: () => request<HealthOut>('GET', '/health'),
  tasks: {
    list: () => request<{ tasks: any[]; latest: Record<string, any> }>('GET', '/api/v1/tasks'),
    acknowledge: (category: string) => request<{ acknowledged: number }>('POST', '/api/v1/tasks/acknowledge', { category }),
    acknowledgeOne: (taskId: string) => request<{ acknowledged: boolean; task_id: string }>('POST', `/api/v1/tasks/${encodeURIComponent(taskId)}/acknowledge`),
  },
  instruments: () => request<{ instruments: Instrument[] }>('GET', '/api/v1/instruments').then((r) => r.instruments),
  events: (params: { instrument?: string; category?: string; severity?: string; subsystem?: string; search?: string; limit?: number; offset?: number; order?: 'asc' | 'desc'; sort?: string } = {}) => {
    const q = new URLSearchParams()
    if (params.instrument) q.set('instrument', params.instrument)
    if (params.category) q.set('category', params.category)
    if (params.severity) q.set('severity', params.severity)
    if (params.subsystem) q.set('subsystem', params.subsystem)
    if (params.search) q.set('search', params.search)
    if (params.limit) q.set('limit', String(params.limit))
    if (params.offset) q.set('offset', String(params.offset))
    if (params.sort) q.set('sort', params.sort)
    if (params.order) q.set('order', params.order)
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
    dependencies: (instrument: string, id: number) =>
      request<NodeDependencies>('GET', `/api/v1/instruments/${instrument}/nodes/${id}/dependencies`),
    remove: (instrument: string, id: number, includeDescendants = false) =>
      request<NodeDeleteResult>('DELETE', `/api/v1/instruments/${instrument}/nodes/${id}?include_descendants=${includeDescendants}`),
    removeMany: (instrument: string, ids: number[], includeDescendants = false) =>
      request<NodeDeleteResult>('POST', `/api/v1/instruments/${instrument}/nodes/delete`, { node_ids: ids, include_descendants: includeDescendants }),
    // Duplicate & Edit — a NEW independent node (parent = source). The server
    // recompiles + refingerprints; an unchanged spec returns 409 SPEC_DUPLICATE.
    duplicate: (instrument: string, id: number, body: {
      spec?: Record<string, unknown>; broker_meta?: BrokerMeta;
      name?: string; description?: string;
    }) => request<Node>('POST', `/api/v1/instruments/${instrument}/nodes/${id}/duplicate`, body),
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
  deeptesting: {
    createRuns: (instrument: string, nodeIds: number[], config: Record<string, unknown>, asyncMode = false) =>
      request<{ runs: any[]; task_id?: string; async?: boolean }>('POST', `/api/v1/instruments/${instrument}/deeptesting/runs${asyncMode ? '?async_mode=true' : ''}`, { node_ids: nodeIds, config }),
    auditExportUrl: (instrument: string, id: number, scope = 'full', filters?: Record<string, unknown>, cells?: string[]) => {
      const q = new URLSearchParams({ scope })
      if (filters) q.set('filters', JSON.stringify(filters))
      if (cells) q.set('cells', JSON.stringify(cells))
      return `/api/v1/instruments/${instrument}/deeptesting/runs/${id}/audit-export?${q}`
    },
    listRuns: (instrument: string, params: { node_id?: number; status?: string } = {}) => {
      const q = new URLSearchParams()
      if (params.node_id) q.set('node_id', String(params.node_id))
      if (params.status) q.set('status', params.status)
      return request<{ runs: any[] }>('GET', `/api/v1/instruments/${instrument}/deeptesting/runs?${q}`)
    },
    getRun: (instrument: string, id: number) =>
      request<any>('GET', `/api/v1/instruments/${instrument}/deeptesting/runs/${id}`),
    trades: (instrument: string, id: number, filters?: Record<string, unknown>) =>
      request<{ trades: any[]; count: number }>('GET', `/api/v1/instruments/${instrument}/deeptesting/runs/${id}/trades${filters ? `?filters=${encodeURIComponent(JSON.stringify(filters))}` : ''}`),
    tradesPage: (instrument: string, id: number, q: { page: number; pageSize: number | 'all'; sort: string; dir: string; cells: string[]; filters?: Record<string, unknown> }) => {
      const params = new URLSearchParams({ page: String(q.page), page_size: String(q.pageSize), sort: q.sort, dir: q.dir, cells: JSON.stringify(q.cells) })
      if (q.filters && Object.keys(q.filters).length) params.set('filters', JSON.stringify(q.filters))
      return request<any>('GET', `/api/v1/instruments/${instrument}/deeptesting/runs/${id}/trades?${params}`)
    },
    rerun: (instrument: string, id: number) =>
      request<any>('POST', `/api/v1/instruments/${instrument}/deeptesting/runs/${id}/rerun`),
    rankings: (instrument: string, body: Record<string, unknown>) =>
      request<{ ranking: any[] }>('POST', `/api/v1/instruments/${instrument}/deeptesting/rankings`, body),
    getScenario: (instrument: string, id: number) =>
      request<any>('GET', `/api/v1/instruments/${instrument}/deeptesting/scenarios/${id}`),
    listScenarios: (instrument: string) =>
      request<{ scenarios: any[] }>('GET', `/api/v1/instruments/${instrument}/deeptesting/scenarios`),
    createScenario: (instrument: string, body: Record<string, unknown>) =>
      request<any>('POST', `/api/v1/instruments/${instrument}/deeptesting/scenarios`, body),
    editScenario: (instrument: string, id: number, body: Record<string, unknown>) =>
      request<any>('PUT', `/api/v1/instruments/${instrument}/deeptesting/scenarios/${id}`, body),
    deleteScenario: (instrument: string, id: number) =>
      request<any>('DELETE', `/api/v1/instruments/${instrument}/deeptesting/scenarios/${id}`),
    generateScenarios: (instrument: string, body: Record<string, unknown>) =>
      request<any>('POST', `/api/v1/instruments/${instrument}/deeptesting/scenarios/generate`, body),
    suggestions: (instrument: string, runId: number) =>
      request<{ suggestions: any[] }>('GET', `/api/v1/instruments/${instrument}/deeptesting/runs/${runId}/fine-tune-suggestions`),
    fineTuneApply: (instrument: string, runId: number, suggestion: Record<string, unknown>) =>
      request<any>('POST', `/api/v1/instruments/${instrument}/deeptesting/runs/${runId}/fine-tune-apply`, { suggestion }),
  },
  monteCarlo: {
    nodes: (instrument: string) => request<{ nodes: any[] }>('GET', `/api/v1/instruments/${instrument}/monte-carlo/nodes`),
    start: (instrument: string, body: { node_ids: number[]; methods: string[]; config: Record<string, unknown>; idempotency_key?: string }) =>
      request<{ runs: any[]; async?: boolean }>('POST', `/api/v1/instruments/${instrument}/monte-carlo/runs`, body),
    cancel: (instrument: string, taskId: string) => request<any>('POST', `/api/v1/instruments/${instrument}/monte-carlo/tasks/${encodeURIComponent(taskId)}/cancel`),
    history: (instrument: string, nodeId?: number) => request<{ runs: any[] }>('GET', `/api/v1/instruments/${instrument}/monte-carlo/runs${nodeId ? `?node_id=${nodeId}` : ''}`),
    get: (instrument: string, runId: string) => request<any>('GET', `/api/v1/instruments/${instrument}/monte-carlo/runs/${encodeURIComponent(runId)}`),
    exportUrl: (instrument: string, runId: string, format: 'txt' | 'zip' = 'zip') =>
      `/api/v1/instruments/${instrument}/monte-carlo/runs/${encodeURIComponent(runId)}/export?format=${format}`,
  },
  marketData: {
    get: (instrument: string) => request<MarketDataAvailability>('GET', `/api/v1/instruments/${instrument}/market-data`),
    fetch: (instrument: string, timeframe = 'ALL', operationId?: string) =>
      request<MarketDataFetchReport>('POST', `/api/v1/instruments/${instrument}/market-data/fetch`, { timeframe, operation_id: operationId }),
    startFetch: (instrument: string, timeframe = 'ALL', operationId?: string) =>
      request<MarketDataOperation>('POST', `/api/v1/instruments/${instrument}/market-data/fetch/start`, { timeframe, operation_id: operationId }),
    operation: (instrument: string, operationId: string) =>
      request<MarketDataOperation>('GET', `/api/v1/instruments/${instrument}/market-data/fetch/operations/${operationId}`),
    latestOperation: (instrument: string) =>
      request<MarketDataOperation>('GET', `/api/v1/instruments/${instrument}/market-data/fetch/operations`),
    conflicts: (instrument: string, params: { operation_id?: string; resolution?: string } = {}) => {
      const q = new URLSearchParams()
      if (params.operation_id) q.set('operation_id', params.operation_id)
      if (params.resolution) q.set('resolution', params.resolution)
      const qs = q.toString()
      return request<{ instrument: string; conflicts: FetchConflict[]; total: number }>(
        'GET', `/api/v1/instruments/${instrument}/market-data/conflicts${qs ? `?${qs}` : ''}`)
    },
    quality: (instrument: string, timeframe: string, fromTs?: number, toTs?: number) => {
      const q = new URLSearchParams({ timeframe })
      if (fromTs != null) q.set('from_ts', String(fromTs))
      if (toTs != null) q.set('to_ts', String(toTs))
      return request<Record<string, unknown>>('GET', `/api/v1/instruments/${instrument}/market-data/quality?${q}`)
    },
    resolveConflict: (instrument: string, conflictId: number, action: 'KEPT_STORED' | 'ACCEPTED_INCOMING', note?: string) =>
      request<{ conflict: FetchConflict; before: Record<string, unknown>; after: Record<string, unknown> }>(
        'POST', `/api/v1/instruments/${instrument}/market-data/conflicts/${conflictId}/resolve`, { action, note }),
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
