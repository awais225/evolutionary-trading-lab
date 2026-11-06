// Reference example spec — MIRRORS packages/strategy_spec/.../examples.py (bullish_engulfing).
// This is a client-side convenience DRAFT only: the server re-validates and re-hashes
// everything; nothing here is trusted or executed.
import type { InstrumentSymbol } from '../api/types'

export const EXAMPLE_TIMEFRAMES = ['M1', 'M5', 'M15'] as const

const candle = (field: string, offset: number) => ({ kind: 'candle', field, offset })
const compare = (op: string, left: unknown, right: unknown) => ({ type: 'compare', op, left, right })

export function bullishEngulfingSpec(instrument: InstrumentSymbol | string, timeframe: string): Record<string, unknown> {
  return {
    schema_version: '1.0.0',
    identity: {
      name: `Bullish Engulfing · ${instrument} ${timeframe}`,
      description:
        'Reference price-action pattern: bearish candle followed by an engulfing bullish ' +
        'candle closing above the previous high. Entry next bar open after signal close. ' +
        'Stop-loss 10 pips below signal low; take-profit 2R.',
      instrument,
      timeframe,
      direction: 'LONG',
      origin: 'CUSTOM',
      tags: ['pattern', 'price_action', 'reference'],
      metadata: { reference: true, pattern: 'bullish_engulfing' },
    },
    entry: {
      type: 'and',
      children: [
        compare('lt', candle('close', 1), candle('open', 1)), // prev candle bearish
        compare('gt', candle('close', 0), candle('open', 0)),  // current candle bullish
        compare('lte', candle('open', 0), candle('close', 1)), // engulf: open <= prev close
        compare('gte', candle('close', 0), candle('open', 1)), // engulf: close >= prev open
        compare('gt', candle('high', 0), candle('high', 1)),   // current high > prev high
        compare('lt', candle('low', 0), candle('low', 1)),     // current low < prev low
      ],
    },
    exits: {
      stop_loss: {
        anchor: 'signal_low',
        distance: { unit: 'pips', value: 10 }, // pip_size comes from broker metadata — never hardcoded
      },
      take_profit: { ratio: 2.0 },             // 1:2 risk:reward
      max_holding_candles: 100,
    },
    risk: {
      sizing: { mode: 'fixed_risk', risk_pct: 1.0 },
    },
    execution: {
      entry_timing: 'next_bar_open',
      candle: 'completed',
      permit_forming_candle: false,
      order_type: 'market',
      position_handling: 'single',
    },
  }
}
