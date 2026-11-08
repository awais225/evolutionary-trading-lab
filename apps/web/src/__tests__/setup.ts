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
  created_by: 'test', created_at: 0, updated_at: 0, starred: false, timeframe: 'M15',
}

function route(url: string, method: string, bodyText?: string): Response {
  const body = bodyText ? JSON.parse(bodyText) : undefined
  if (url.endsWith('/health')) return json({ status: 'ok', version: '1.0.0', checks: { registry_db: { status: 'healthy' }, ollama: { status: 'healthy' }, mt5: { status: 'unavailable' } } })

  if (url.includes('/metadata/refresh')) {
    return json({ detail: { code: 'MT5_UNAVAILABLE', message: 'MetaTrader5 package not importable: not installed.', platform: 'Linux', package_available: false } }, 503)
  }
  if (url.includes('/metadata')) {
    const override = (globalThis as { __hero1Metadata?: unknown }).__hero1Metadata
    if (override) return json(override)
    return json({ instrument: 'XAUUSD', available: false, snapshot_count: 0, metadata_root: '<test-root>/MARKET_METADATA', snapshots: [], reason: 'no stored MT5 metadata for this instrument.' })
  }
  if (url.includes('/api/v1/mt5/')) {
    return json({ ok: false, checked_at: '2026-11-07T00:00:00Z', platform: 'Linux', package_available: false, import_error: 'not installed', session: {}, terminal: null, account: null, active_terminal_path: null })
  }
  if (url.includes('/market-data')) {
    if (url.includes('/conflicts')) {
      if (method === 'POST' && url.includes('/resolve')) {
        return json({ conflict: { id: 1, resolution: 'KEPT_STORED' }, before: {}, after: {} })
      }
      const list = (globalThis as { __hero1Conflicts?: unknown[] }).__hero1Conflicts ?? []
      return json({ instrument: 'XAUUSD', conflicts: list, total: list.length })
    }
    if (url.includes('/fetch/start')) {
      return json({ operation_id: 'FETCH-TEST-OP1', state: 'QUEUED', instrument: 'XAUUSD', timeframes: ['M1'], existing: false, started_at: 1792000000 })
    }
    if (/\/fetch\/operations\/.+/.test(url)) {
      return json({ operation_id: 'FETCH-TEST-OP1', state: 'COMPLETED', instrument: 'XAUUSD',
        report: { instrument: 'XAUUSD', operation_id: 'FETCH-TEST-OP1', broker_server: 'ICMarketsSC-Demo', ok: false, status: 'FAILED',
          time: { rule: 'UTC epoch seconds', utc_now: '2026-11-07 00:00:00', offset_seconds: 0, offset_source: 'not applied (UTC canonical store)' },
          results: [{ timeframe: 'M1', status: 'FAILED', requested_from: null, requested_to: null, received: 0, appended: 0, duplicates_skipped: 0, incomplete_dropped: 0, invalid_rejected: 0, conflicts: 0, latest_stored_ts: null, error: 'mock terminal unavailable' }],
          fetched_at: '2026-11-07T00:00:00Z' },
        error: null })
    }
    if (url.includes('/fetch/operations')) {
      return json({ operation_id: null, state: 'IDLE', instrument: 'XAUUSD', timeframes: [], report: null, error: null })
    }
    if (url.includes('/fetch')) {
      return json({ instrument: 'XAUUSD', broker_server: 'ICMarketsSC-Demo', ok: false, status: 'FAILED',
        time: { rule: 'UTC epoch seconds', utc_now: '2026-11-07 00:00:00', offset_seconds: 0, offset_source: 'not applied (UTC canonical store)' },
        server_time: { rule: 'IC Markets server time', server_now: '2026-07-21 00:00:00', offset_seconds: 10800, offset_source: 'calendar rule' },
        results: [{ timeframe: 'M1', status: 'FAILED', requested_from: null, requested_to: null, received: 0, appended: 0, duplicates_skipped: 0, incomplete_dropped: 0, invalid_rejected: 0, conflicts: 0, latest_stored_ts: null, error: 'mock terminal unavailable' }],
        fetched_at: '2026-11-07T00:00:00Z' })
    }
    return json({ instrument: 'XAUUSD', available: true, store: '<test-root>/MARKET_DATA/market_data.db', timezone: 'UTC (epoch seconds)',
      summary: { available_timeframes: 1, total_candles: 3, first_ts: '2026-07-07 18:41:00', last_ts: '2026-07-07 18:43:00', last_successful_update: '2026-11-07T00:00:00+00:00' },
      timeframes: { M1: { row_count: 3, first_ts: '2026-07-07 18:41:00', last_ts: '2026-07-07 18:43:00', status: 'AVAILABLE', timezone: 'UTC (epoch seconds)', source: 'XAUUSD_M1.csv', warnings: [] },
        M5: { row_count: 0, status: 'EMPTY' }, M15: { row_count: 0, status: 'EMPTY' }, M30: { row_count: 0, status: 'EMPTY' }, H1: { row_count: 0, status: 'EMPTY' }, H4: { row_count: 0, status: 'EMPTY' }, D1: { row_count: 0, status: 'EMPTY' } }, datasets: [] })
  }
  if (url.includes('/deeptesting/')) {
    const run = {
      id: 1, node_id: 1, node_version: 1, scenario_id: null, mode: 'initial', window_label: null,
      status: 'DONE', instrument: 'XAUUSD', timeframe: 'H1', fingerprint: 'abc123def456',
      compiler_version: '1.2.0', engine_version: '3.0.0', error: null,
      started_at: 1700000000, finished_at: 1700003600,
      config: { initial_capital: 10000, account_currency: 'USD', risk_mode: 'percent', risk_value: 1, risk_basis: 'current_equity', max_active_trades: 1 },
      metrics: { total_trades: 1, win_rate: 1, net_profit: 200, profit_factor: null, expectancy: 200, max_drawdown_amount: 0, max_drawdown_pct: 0, final_balance: 10200, final_equity: 10200, long_net: 200, short_net: 0, max_simultaneous_positions: 1, recovery_factor: null, sharpe_ratio: null, sortino_ratio: null, max_consecutive_losses: 0, max_consecutive_loss_amount: 0, max_consecutive_loss_pct: 0, time_buckets: { by_session: [{ bucket: 'london', trades: 1, win_rate: 1, net_pnl: 200, expectancy: 200 }] } },
      data_quality: { readiness: 'READY' }, assumptions: { spread_price: 0, spread_source: 'zero', ambiguity_policy: 'sl_first' },
      equity_curve: [{ ts: 1700000000, balance: 10000, equity: 10000, open_positions: 0 }, { ts: 1700003600, balance: 10200, equity: 10200, open_positions: 0 }],
      skipped_signals: [],
    }
    if (url.includes('/fine-tune-suggestions')) return json({ suggestions: [] })
    if (/\/runs\/\datz/.test(url)) return json({}, 500)
    if (/\/runs\/\d+\/trades/.test(url)) return json({ count: 1, trades: [{ side: 'LONG', volume: 5, entry_ts: 1700000000, entry_price: 94, exit_ts: 1700003600, exit_price: 98, exit_reason: 'TAKE_PROFIT', net_pnl: 200, gross_pnl: 200, commission: 0, swap: 0, spread_cost: 0, planned_risk: 100, initial_stop: 92, take_profit: 98, final_stop: 92, result: 'WIN', holding_seconds: 3600, session: 'london', mfe_price: 98.6, mae_price: 93.9, stop_history: [], entry_indicators: {}, ambiguous_same_candle: false, gap_overrun: false }] })
    if (/\/runs\/\d+$/.test(url.split('?')[0]) && method === 'GET') return json(run)
    if (url.includes('/runs') && method === 'POST') return json({ runs: [run] })
    if (url.includes('/runs')) return json({ runs: [run] })
    if (url.includes('/scenarios')) return json({ scenarios: [] })
    if (url.includes('/rankings')) return json({ ranking: [{ run_id: 1, node_id: 1, rank: 1, rank_score: 0.9, eligible: true, ineligible_reason: null }] })
    return json({})
  }
  if (url.includes('/api/v1/instruments/') && url.includes('/nodes')) {
    // Phase 2 spec pipeline endpoints
    if (url.includes('/validate')) {
      const spec = body?.spec ?? {}
      const metaStored = Boolean((globalThis as { __hero1Metadata?: { available?: boolean } }).__hero1Metadata?.available)
      const needsMeta = JSON.stringify(spec).includes('"pips"') && !body?.broker_meta && !metaStored
      return json({
        instrument: 'XAUUSD',
        status: needsMeta ? 'NEEDS_METADATA' : 'VALID',
        issues: needsMeta ? [{ code: 'RISK_METADATA_REQUIRED', path: 'exits.stop_loss.distance', message: 'pip_size is required', severity: 'NEEDS_METADATA', remediation: 'supply broker_meta' }] : [],
        required_warmup_candles: 1, schema_version: '1.0.0',
        summary: needsMeta ? { invalid: 0, warning: 0, needs_metadata: 1 } : { invalid: 0, warning: 0, needs_metadata: 0 },
      })
    }
    if (url.includes('/compile')) {
      const metaStoredC = Boolean((globalThis as { __hero1Metadata?: { available?: boolean } }).__hero1Metadata?.available)
      if (JSON.stringify(body?.spec ?? {}).includes('"pips"') && !body?.broker_meta && !metaStoredC) {
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
    if (url.includes('/generate')) {
      const tfs: string[] = Array.isArray(body?.timeframes) ? body.timeframes.map((t: string) => String(t).toUpperCase()) : ['M15']
      const all = (globalThis as { __hero1NodeItems?: Array<Record<string, unknown>> }).__hero1NodeItems ?? []
      const used = new Set<number>()
      for (const n of all) {
        const m = /^c(\d+)$/i.exec(String((n as Record<string, unknown>).name ?? '').trim())
        if (m) used.add(parseInt(m[1], 10))
      }
      const names: string[] = []
      if (tfs.length === 1 && body?.name) { names.push(String(body.name)) } else {
        let k = 1
        while (names.length < tfs.length) { if (!used.has(k)) { names.push(`C${k}`); used.add(k) } k += 1 }
      }
      const created = tfs.map((tf, i) => ({
        ...baseNode, id: i + 1, name: names[i], timeframe: tf,
        spec_hash: SPEC_FINGERPRINT,
        spec_json: JSON.stringify({ ...(body?.spec ?? {}), identity: { ...(body?.spec?.identity ?? {}), timeframe: tf } }),
      }))
      return json({ instrument: 'XAUUSD', requested: tfs, count: created.length, created }, 201)
    }
    if (url.includes('/dependencies')) {
      const id = Number(/\/nodes\/(\d+)\/dependencies/.exec(url)?.[1] ?? 0)
      const items = (globalThis as { __hero1NodeItems?: Array<Record<string, unknown>> }).__hero1NodeItems ?? []
      const descendants = items.filter((n) => n.parent_node_id === id).map((n) => Number(n.id))
      return json({ node_id: id, descendants, descendant_count: descendants.length,
                    dependent_records: {}, dependent_total: 0, deletable: true, blocked_reason: null })
    }
    if (/\/nodes\/\datz\/duplicates/.test(url)) return json({}, 500)
    if (/\/nodes\/\d+\/duplicate$/.test(url.split('?')[0])) {
      const srcId = Number(/\/nodes\/(\d+)\/duplicate/.exec(url)?.[1] ?? 0)
      const items = (globalThis as { __hero1NodeItems?: Array<Record<string, unknown>> }).__hero1NodeItems ?? []
      const src = items.find((n) => Number(n.id) === srcId) ?? {}
      return json({ ...baseNode, ...src, id: 999, name: body?.name ?? 'C999',
                    parent_node_id: srcId, spec_hash: 'variant999hash',
                    spec_json: body?.spec ? JSON.stringify(body.spec) : src.spec_json ?? null,
                    timeframe: body?.spec?.identity?.timeframe ?? src.timeframe ?? null }, 201)
    }
    if (url.endsWith('/nodes/delete') && method === 'POST') {
      const ids: number[] = Array.isArray(body?.node_ids) ? body.node_ids : []
      const g = globalThis as { __hero1NodeItems?: Array<Record<string, unknown>> }
      const items = g.__hero1NodeItems ?? []
      const failed: Array<{ node_id: number; code: string; message: string }> = []
      const deleted: number[] = []
      for (const id of ids) {
        const kids = items.filter((n) => n.parent_node_id === id && !deleted.includes(Number(n.id)))
        if (kids.length > 0 && !body?.include_descendants) {
          failed.push({ node_id: id, code: 'NODE_HAS_DESCENDANTS', message: `node #${id} has descendants` })
          continue
        }
        deleted.push(id, ...kids.map((k) => Number(k.id)))
      }
      g.__hero1NodeItems = items.filter((n) => !deleted.includes(Number(n.id)))
      return json({ deleted, failed })
    }
    if (method === 'DELETE' && /\/nodes\/\d+$/.test(url.split('?')[0])) {
      const id = Number(url.split('?')[0].split('/').pop())
      const g = globalThis as { __hero1NodeItems?: Array<Record<string, unknown>> }
      g.__hero1NodeItems = (g.__hero1NodeItems ?? []).filter((n) => Number(n.id) !== id)
      return json({ deleted: [id], failed: [] })
    }
    if (method === 'POST') {
      const hasSpec = typeof body?.spec_json === 'string'
      return json({ ...baseNode, spec_hash: hasSpec ? SPEC_FINGERPRINT : null, spec_json: hasSpec ? body.spec_json : null }, 201)
    }
    if (method === 'PATCH' && /\/nodes\/\d+$/.test(url.split('?')[0])) {
      return json({ ...baseNode, ...(body ?? {}), id: Number(url.split('?')[0].split('/').pop()) })
    }
    const all = (globalThis as { __hero1NodeItems?: Array<Record<string, unknown>> }).__hero1NodeItems ?? []
    const starredTotal = all.filter((n) => n.starred === true).length
    const wantStarred = /[?&]starred=true/.test(url)
    const wantUnstarred = /[?&]starred=false/.test(url)
    const filtered = wantStarred ? all.filter((n) => n.starred === true)
      : wantUnstarred ? all.filter((n) => n.starred !== true)
      : all
    const tfCounts: Record<string, number> = {}
    for (const n of filtered) { const tf = (n as Record<string, unknown>).timeframe as string | null | undefined; if (tf) tfCounts[tf] = (tfCounts[tf] ?? 0) + 1 }
    const wantTf = new URLSearchParams(url.split('?')[1] ?? '').get('timeframe')
    const tfFilter = wantTf ? new Set(wantTf.split(',').map((t) => t.trim().toUpperCase())) : null
    const tfFiltered = tfFilter ? filtered.filter((n) => tfFilter.has(String((n as Record<string, unknown>).timeframe ?? '').toUpperCase())) : filtered
    return json({ instrument: 'XAUUSD', total: all.length, starred_total: starredTotal,
                  filtered_total: tfFiltered.length,
                  timeframe_counts: tfCounts,
                  unassigned_timeframe_total: filtered.filter((n) => !(n as Record<string, unknown>).timeframe).length,
                  items: tfFiltered })
  }
  if (url.includes('/api/v1/instruments')) {
    const override = (globalThis as { __hero1Instruments?: unknown[] }).__hero1Instruments
    return json({ instruments: override ?? [
      { symbol: 'XAUUSD', display_name: 'Gold Spot vs US Dollar', asset_class: 'METAL', enabled: true, default_timeframe: 'M15', digits: 2, contract_size: 100 },
      { symbol: 'EURUSD', display_name: 'Euro vs US Dollar', asset_class: 'FX', enabled: true, default_timeframe: 'M15', digits: 5, contract_size: 100000 },
      { symbol: 'BTCUSD', display_name: 'Bitcoin vs US Dollar', asset_class: 'CRYPTO', enabled: true, default_timeframe: 'H1', digits: 2, contract_size: 1 },
    ] })
  }
  if (url.includes('/api/v1/settings')) return json(SETTINGS)
  if (url.includes('/api/v1/events')) {
    if (method === 'POST') return json({ id: 42 })
    const evts = (globalThis as { __hero1Events?: unknown[] }).__hero1Events ?? []
    return json({ limit: 50, count: evts.length, total: evts.length, events: evts })
  }
  return json({ detail: 'not found' }, 404)
}

beforeEach(() => {
  localStorage.clear()
  delete (globalThis as { __hero1NodeItems?: unknown[] }).__hero1NodeItems
  delete (globalThis as { __hero1Instruments?: unknown[] }).__hero1Instruments
  delete (globalThis as { __hero1Metadata?: unknown }).__hero1Metadata
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
