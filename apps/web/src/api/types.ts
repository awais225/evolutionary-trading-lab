// Backend-driven (seeded registry: XAUUSD/EURUSD/BTCUSD today); widen to string.
export type InstrumentSymbol = string
export type NodeOrigin = 'CUSTOM' | 'WWW' | 'INTERACTIVE' | 'FINE_TUNED'

export interface Instrument {
  symbol: InstrumentSymbol
  display_name: string
  asset_class: string
  enabled: boolean
  default_timeframe: string
  digits: number | null
  contract_size: number | null
}

export interface Node {
  id: number
  instrument_id: string
  origin: NodeOrigin
  name: string
  description: string | null
  status: string
  approval_state: string
  version: number
  parent_node_id: number | null
  spec_hash: string | null
  spec_json: string | null
  created_by: string
  created_at: number
  updated_at: number
  /** Favorite flag — research metadata only. Never affects spec semantics. */
  starred: boolean
  /** Node-identity timeframe (assigned from the spec at creation). null = draft / legacy. */
  timeframe: string | null
}

export interface NodeListOut {
  instrument: string
  /** UNFILTERED instrument total (stable contract). */
  total: number
  /** Starred nodes in this instrument (unfiltered). */
  starred_total: number
  /** Rows matching the ACTIVE filter (== total when no filter is applied). */
  filtered_total: number
  /** Per-timeframe node counts within the ACTIVE starred scope (filter ignored). */
  timeframe_counts: Record<string, number>
  /** Nodes with no timeframe (drafts) within the ACTIVE starred scope. */
  unassigned_timeframe_total: number
  items: Node[]
}

export interface NodeGenerateOut {
  instrument: string
  requested: string[]
  count: number
  created: Node[]
}

export type EventSeverity = 'DEBUG' | 'INFO' | 'SUCCESS' | 'WARNING' | 'ERROR'
export interface HeroEvent {
  id: number
  ts: number
  category: string
  severity: EventSeverity
  instrument: string | null
  subsystem: string
  message: string
  payload: unknown
  node_id: number | null
}

export interface EventListOut {
  limit: number
  count: number
  events: HeroEvent[]
}

export interface SettingsOut {
  settings: Record<string, unknown>
  environment: Record<string, string>
}

export interface HealthOut {
  status: 'ok' | 'degraded'
  version: string
  checks: Record<string, { status: string; detail?: string; host?: string; models?: string[] }>
}

export type ValidationSeverity = 'INVALID' | 'WARNING' | 'NEEDS_METADATA'
export type ValidationStatus = 'VALID' | 'WARNING' | 'NEEDS_METADATA' | 'INVALID'

export interface ValidationIssue {
  code: string
  path: string
  message: string
  severity: ValidationSeverity
  remediation: string | null
}

export interface ValidationReport {
  instrument: string
  status: ValidationStatus
  issues: ValidationIssue[]
  required_warmup_candles: number | null
  schema_version: string | null
  summary: Record<string, number>
}

export interface CompileResult {
  fingerprint: string
  canonical_json: string
  schema_version: string
  compiler_version: string
  ir_version: string
  ir: Record<string, unknown>
  required_warmup_candles: number
  warnings: string[]
}

export interface NodeVersionList {
  instrument: string
  lineage_root_id: number | null
  items: Node[]
}

export type BrokerMeta = Record<string, Record<string, number>>

// ---- MT5 / broker metadata (added 2026-11-07, V1.4.1 milestone) ----
export interface Mt5Terminal {
  name: string
  path: string
  origin: string
  running?: boolean | null
  state?: 'DISCOVERED' | 'RUNNING' | string
}

export interface Mt5ConnectionResult {
  ok: boolean
  state?: 'DISCOVERED' | 'RUNNING' | 'INITIALIZING' | 'CONNECTED' | 'ACCOUNT_LOADED' | 'FAILED' | 'UNAVAILABLE' | 'NOT_CONNECTED' | string
  launched?: boolean
  login_required?: boolean
  launch?: Record<string, unknown>
  checked_at: string
  platform: string
  package_available: boolean
  import_error?: string | null
  error_code?: string | null
  error?: string | null
  warning?: string
  path_note?: string | null
  active_terminal_path?: string | null
  last_error?: unknown[] | null
  session?: Record<string, unknown>
  terminal?: {
    path?: string
    connected?: boolean
    build?: number
    company?: string
    name?: string
    language?: string
    trade_allowed?: boolean
    trade_expert?: boolean
    data_path?: string
    raw?: Record<string, unknown>
  } | null
  account?: Mt5Account | null
}

export interface Mt5Account {
  login?: number
  name?: string
  server?: string
  company?: string
  currency?: string
  balance?: number
  equity?: number
  margin?: number
  margin_free?: number
  margin_level?: number
  leverage?: number
  profit?: number
  trade_allowed?: boolean
  trade_expert?: boolean
  trade_mode?: number
  trade_mode_name?: 'DEMO' | 'CONTEST' | 'REAL' | null
  raw?: Record<string, unknown>
}

export interface Mt5TerminalsOut {
  platform_windows: boolean
  package_available: boolean
  import_error?: string | null
  saved_terminal_path?: string | null
  terminals: Mt5Terminal[]
  note: string
}

export interface MarketDataTimeframeStats {
  row_count: number
  first_ts?: string | null
  last_ts?: string | null
}

export interface MarketDataDataset {
  dataset_id: string
  instrument: string
  timeframe: string
  source_file?: string
  row_count?: number
  first_ts?: string | null
  last_ts?: string | null
  validation_result?: string
  status?: string
  imported_at?: string
}

export interface MarketDataAvailability {
  instrument: string
  available: boolean
  store: string
  reason?: string
  timeframes: Record<string, MarketDataTimeframeStats | null>
  datasets: MarketDataDataset[]
}

export interface MarketDataFetchResult {
  timeframe: string
  status: 'UPDATED' | 'UP_TO_DATE' | 'FAILED' | string
  requested_from?: string | null
  requested_to?: string | null
  received: number
  appended: number
  duplicates_skipped: number
  incomplete_dropped: number
  invalid_rejected: number
  conflicts: number
  latest_stored_ts?: string | null
  error?: string | null
}

export interface MarketDataFetchReport {
  instrument: string
  broker_server: string
  server_time: {
    rule: string
    server_now: string
    offset_seconds: number
    offset_source: string
  }
  ok: boolean
  results: MarketDataFetchResult[]
  fetched_at: string
}

export interface InstrumentMetadataView {
  instrument: string
  available: boolean
  snapshot_count: number
  metadata_root: string
  reason?: string
  snapshots: Array<Record<string, unknown>>
  saved?: Record<string, unknown>
  snapshot?: {
    broker_server?: string
    terminal?: Record<string, unknown>
    account_login?: number | null
    source?: string
    retrieved_at?: string
    fingerprint?: string
    digits?: number
    derived?: {
      point_size?: number
      tick_size?: number
      pip_size?: number
      contract_size?: number
      digits_rule?: string
    }
    validation?: { ok: boolean; missing: string[]; required_for_validation: string[] }
    tick?: Record<string, unknown>
    stops_level?: number
    freeze_level?: number
    volume_min?: number
    volume_max?: number
    volume_step?: number
    currency_base?: string
    currency_profit?: string
    currency_margin?: string
    trade_mode?: number
  }
}
