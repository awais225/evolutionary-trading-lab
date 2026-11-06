export type InstrumentSymbol = 'XAUUSD' | 'EURUSD' | 'BTCUSD'
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
  created_by: string
  created_at: number
  updated_at: number
}

export interface NodeListOut {
  instrument: string
  total: number
  items: Node[]
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
