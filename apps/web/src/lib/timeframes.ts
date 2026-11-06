/**
 * SUPPORTED_TIMEFRAMES — the SINGLE source of truth lives in
 *   packages/strategy_spec/src/hero1_strategy_spec/spec.py
 *   (SUPPORTED_TIMEFRAMES — enforced by the compiler + validation engine;
 *    unsupported values are rejected with 422 TIMEFRAME_UNSUPPORTED).
 *
 * The Hero1 MT5 adapter (packages/mt5_adapter) is a probe-only skeleton with NO
 * timeframe registry and makes NO capability claim — W1 / MN1 are deliberately
 * NOT exposed anywhere in the product. Keep this list in sync with the backend;
 * never add a value the backend will not compile.
 */
export const SUPPORTED_TIMEFRAMES = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'] as const
export type Timeframe = (typeof SUPPORTED_TIMEFRAMES)[number]

/** Ordered sort key for grouping (registry order). */
export const TIMEFRAME_ORDER = new Map(SUPPORTED_TIMEFRAMES.map((tf, i) => [tf, i]))

export const DEFAULT_TIMEFRAME: Timeframe = 'M15'

export function isTimeframe(v: unknown): v is Timeframe {
  return typeof v === 'string' && (SUPPORTED_TIMEFRAMES as readonly string[]).includes(v.toUpperCase())
}

/** Compact human summary: "M1 · M5 · M15" or "7 timeframes · all supported". */
export function summarizeTimeframes(tfs: string[]): string {
  if (tfs.length === 0) return 'none selected'
  if (tfs.length === SUPPORTED_TIMEFRAMES.length) return `all ${SUPPORTED_TIMEFRAMES.length} supported (${SUPPORTED_TIMEFRAMES.join(' · ')})`
  return tfs.join(' · ')
}
