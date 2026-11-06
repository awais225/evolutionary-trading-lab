/**
 * Frontend mirror of the BACKEND indicator reference contract
 * (packages/strategy_spec/src/hero1_strategy_spec/indicators.py).
 *
 * Authoritative source of truth = backend. This module only drives the UI
 * (labels, defaults, ranges, disabled entries). The backend revalidates every
 * spec on Validate/Compile/Save — nothing here is sufficient on its own.
 * Indicator MATHEMATICS do not exist yet (Phase 3); values can never be faked.
 */

export const CANDLE_FIELDS = ['open', 'high', 'low', 'close', 'volume'] as const
export type CandleField = (typeof CANDLE_FIELDS)[number]

export const PRICE_SOURCES = ['close', 'open', 'high', 'low', 'hl2', 'hlc3', 'ohlc4'] as const

export const OPERATORS: { op: string; label: string }[] = [
  { op: 'gt', label: '>' },
  { op: 'gte', label: '≥' },
  { op: 'lt', label: '<' },
  { op: 'lte', label: '≤' },
  { op: 'eq', label: '==' },
  { op: 'neq', label: '!=' },
  { op: 'cross_above', label: 'crosses above' },
  { op: 'cross_below', label: 'crosses below' },
]
export const OPERATOR_LABELS: Record<string, string> = Object.fromEntries(OPERATORS.map((o) => [o.op, o.label]))

export type ParamKind = 'int' | 'float' | 'source' | 'anchor'
export interface ParamSpec {
  kind: ParamKind
  required?: boolean
  def: number | string
  min?: number
  max?: number
  label: string
}

export interface IndicatorSpec {
  name: string
  category: 'Moving Averages' | 'Momentum' | 'Volatility' | 'Volume & Price' | 'Trend'
  description: string
  params: Record<string, ParamSpec>
  /** Multi-output selector values; null = single output ("value"). */
  fields: string[] | null
  fieldRequired?: boolean
  warmup: (params: Record<string, number | string>) => number
}

export const INDICATORS: IndicatorSpec[] = [
  {
    name: 'SMA', category: 'Moving Averages', description: 'Simple moving average',
    params: { period: { kind: 'int', required: true, def: 20, min: 2, max: 500, label: 'Period' },
              source: { kind: 'source', def: 'close', label: 'Applied price' } },
    fields: null, warmup: (p) => Math.max(0, Number(p.period ?? 20) - 1),
  },
  {
    name: 'EMA', category: 'Moving Averages', description: 'Exponential moving average',
    params: { period: { kind: 'int', required: true, def: 20, min: 2, max: 500, label: 'Period' },
              source: { kind: 'source', def: 'close', label: 'Applied price' } },
    fields: null, warmup: (p) => Math.max(0, Number(p.period ?? 20) - 1),
  },
  {
    name: 'RSI', category: 'Momentum', description: 'Relative strength index',
    params: { period: { kind: 'int', def: 14, min: 1, max: 100, label: 'Period' },
              source: { kind: 'source', def: 'close', label: 'Applied price' } },
    fields: null, warmup: (p) => Number(p.period ?? 14),
  },
  {
    name: 'ROC', category: 'Momentum', description: 'Rate of change',
    params: { period: { kind: 'int', def: 12, min: 1, max: 200, label: 'Period' },
              source: { kind: 'source', def: 'close', label: 'Applied price' } },
    fields: null, warmup: (p) => Number(p.period ?? 12),
  },
  {
    name: 'MACD', category: 'Momentum', description: 'MACD — select a line output',
    params: { fast: { kind: 'int', def: 12, min: 1, max: 200, label: 'Fast EMA' },
              slow: { kind: 'int', def: 26, min: 2, max: 300, label: 'Slow EMA' },
              signal: { kind: 'int', def: 9, min: 1, max: 100, label: 'Signal' },
              source: { kind: 'source', def: 'close', label: 'Applied price' } },
    fields: ['macd', 'signal', 'histogram'], fieldRequired: true,
    warmup: (p) => Number(p.slow ?? 26) + Number(p.signal ?? 9) - 2,
  },
  {
    name: 'STOCH', category: 'Momentum', description: 'Stochastic oscillator',
    params: { k_period: { kind: 'int', def: 14, min: 1, max: 100, label: '%K period' },
              d_period: { kind: 'int', def: 3, min: 1, max: 50, label: '%D period' },
              slowing: { kind: 'int', def: 3, min: 1, max: 50, label: 'Slowing' } },
    fields: ['k', 'd'], fieldRequired: true,
    warmup: (p) => Number(p.k_period ?? 14) + Number(p.slowing ?? 3) - 1,
  },
  {
    name: 'ATR', category: 'Volatility', description: 'Average true range',
    params: { period: { kind: 'int', def: 14, min: 1, max: 100, label: 'Period' } },
    fields: null, warmup: (p) => Number(p.period ?? 14),
  },
  {
    name: 'BBANDS', category: 'Volatility', description: 'Bollinger Bands — select band',
    params: { period: { kind: 'int', def: 20, min: 2, max: 500, label: 'Period' },
              stddev: { kind: 'float', def: 2.0, min: 0.1, max: 10.0, label: 'StdDev multiplier' },
              source: { kind: 'source', def: 'close', label: 'Applied price' } },
    fields: ['upper', 'basis', 'lower'], fieldRequired: true,
    warmup: (p) => Math.max(0, Number(p.period ?? 20) - 1),
  },
  {
    name: 'VWAP', category: 'Volume & Price', description: 'Volume-weighted average price (daily anchor)',
    params: { anchor: { kind: 'anchor', def: 'daily', label: 'Anchor' } },
    fields: null, warmup: () => 0,
  },
  {
    name: 'ADX', category: 'Trend', description: 'Average directional index',
    params: { period: { kind: 'int', def: 14, min: 1, max: 100, label: 'Period' } },
    fields: ['adx'], fieldRequired: true,
    warmup: (p) => 2 * Number(p.period ?? 14) - 1,
  },
]

export const INDICATOR_CATEGORIES = ['Moving Averages', 'Momentum', 'Volatility', 'Volume & Price', 'Trend'] as const

/**
 * Requested in the library UX but NOT part of the backend indicator registry.
 * Shown as disabled — never selectable, never sent to the backend.
 */
export const UNSUPPORTED_INDICATORS: { name: string; reason: string }[] = [
  { name: 'WMA', reason: 'not in the backend indicator registry (Phase 3+)' },
  { name: 'SMMA', reason: 'not in the backend indicator registry (Phase 3+)' },
  { name: 'CCI', reason: 'not in the backend indicator registry (Phase 3+)' },
  { name: 'Standard Deviation', reason: 'not in the backend indicator registry (Phase 3+)' },
  { name: 'OBV', reason: 'not in the backend indicator registry (Phase 3+)' },
  { name: 'Parabolic SAR', reason: 'not in the backend indicator registry (Phase 3+)' },
  { name: 'Volume (indicator)', reason: 'volume exists as a candle field, not as an indicator output' },
]

export function getIndicator(name: string): IndicatorSpec | undefined {
  return INDICATORS.find((i) => i.name === name)
}

export function defaultParams(spec: IndicatorSpec): Record<string, number | string> {
  const out: Record<string, number | string> = {}
  for (const [key, param] of Object.entries(spec.params)) out[key] = param.def
  return out
}

/** Client-side PRE-CHECK only. Mirrors validate_indicator_params; the backend revalidates everything. */
export function validateParamsClient(name: string, params: Record<string, unknown>, field: string | null | undefined): string[] {
  const problems: string[] = []
  const spec = getIndicator(name)
  if (!spec) return [`unknown indicator ${name}`]
  for (const key of Object.keys(params)) {
    if (!spec.params[key]) problems.push(`unknown parameter '${key}' for ${name}`)
  }
  for (const [key, p] of Object.entries(spec.params)) {
    const value = params[key] ?? p.def
    if (value === undefined || value === null || value === '') {
      if (p.required) problems.push(`missing required parameter '${key}' for ${name}`)
      continue
    }
    if (p.kind === 'int') {
      const v = Number(value)
      if (!Number.isFinite(v) || !Number.isInteger(v)) problems.push(`parameter '${key}' must be an integer, got ${JSON.stringify(value)}`)
      else if (p.min !== undefined && (v < p.min || v > (p.max as number))) problems.push(`parameter '${key}'=${v} out of range [${p.min}, ${p.max}] for ${name}`)
    } else if (p.kind === 'float') {
      const v = Number(value)
      if (!Number.isFinite(v)) problems.push(`parameter '${key}' must be a number, got ${JSON.stringify(value)}`)
      else if (p.min !== undefined && (v < p.min || v > (p.max as number))) problems.push(`parameter '${key}'=${v} out of range [${p.min}, ${p.max}] for ${name}`)
    } else if (p.kind === 'source') {
      if (!(PRICE_SOURCES as readonly string[]).includes(String(value))) problems.push(`source '${String(value)}' must be one of ${PRICE_SOURCES.join(', ')}`)
    } else if (p.kind === 'anchor') {
      if (String(value) !== 'daily') problems.push(`anchor must be 'daily'`)
    }
  }
  if (name === 'MACD') {
    const fast = Number(params.fast ?? 12), slow = Number(params.slow ?? 26)
    if (Number.isInteger(fast) && Number.isInteger(slow) && fast >= slow) problems.push(`MACD requires fast < slow (got fast=${fast}, slow=${slow})`)
  }
  if (spec.fields) {
    if (!field) problems.push(`${name} requires a field selector: ${spec.fields.join(' | ')}`)
    else if (!spec.fields.includes(field)) problems.push(`${name} field '${field}' not supported (${spec.fields.join(' | ')})`)
  } else if (field && field !== 'value') {
    problems.push(`${name} has a single output; field must be omitted`)
  }
  return problems
}

/** One-line human summary of an indicator operand, e.g. `EMA(20, close) field=value`. */
export function indicatorSummary(ind: { name: string; params?: Record<string, unknown>; field?: string | null; offset?: number }): string {
  const spec = getIndicator(ind.name)
  const paramParts: string[] = []
  if (spec) {
    for (const [key, p] of Object.entries(spec.params)) {
      const v = ind.params?.[key] ?? p.def
      paramParts.push(`${v}`)
    }
  }
  const field = ind.field && ind.field !== 'value' ? `.${ind.field}` : ''
  const offset = ind.offset && ind.offset > 0 ? `[-${ind.offset}]` : ''
  return `${ind.name}(${paramParts.join(', ')})${field}${offset}`
}
