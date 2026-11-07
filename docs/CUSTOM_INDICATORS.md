# Custom indicators — future architecture (documentation only)

TASK 13 does **not** implement custom indicators. This note records the safe
path so a future task does not invent an unsafe shortcut.

## Intended flow (example: Fair Value Gap / FVG)

1. Start from a written indicator template that states inputs, outputs,
   warmup, timeframe behavior, missing-data behavior, and version.
2. Promote the template to a **named, versioned indicator definition** in the
   indicator registry (for example `fvg@1.0.0`). The definition is data and
   reviewed code owned by the indicator engine; it is not pasted runtime code.
3. Run an explicit **CHECK** against stored historical candles for a chosen
   instrument/timeframe. The check reports candle coverage, warmup, signal
   count, first/last signal timestamps, invalid-input counts, and the exact
   dataset fingerprints used. It never fills gaps or fabricates signals.
4. Only after CHECK passes may strategy conditions reference the named output
   (for example `fvg.bullish.active`) across instruments/timeframes through
   the normal spec validation/compiler path. Per-node instrument/timeframe
   remain part of strategy identity.

## Safety boundary

- No `eval`, no `exec`, no arbitrary Python/file/module loading from a strategy,
  UI field, imported JSON, or network response.
- A future implementation must use a controlled registry of reviewed indicator
  implementations, versioned parameters, deterministic outputs, and tests with
  hand-computed fixtures.
- Indicator availability is separate from broker metadata and historical-data
  availability; the UI must say exactly which one is missing.
- Backtest/paper/live consumers must use the same versioned definition and
  must reject unknown indicator names or versions.
