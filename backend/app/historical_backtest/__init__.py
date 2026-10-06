"""
V4.6 — MT5 HISTORICAL BACKTEST EXECUTION (research, never order execution).

What this package is
--------------------
A user-triggered historical backtest of one existing USER_RESEARCH strategy over
a historical period of *stored* MT5 market data, executed by the existing
`app.backtest.engine` (the same engine the research pipeline uses) with the
existing execution/cost model (`app.backtest.execution`). Results are persisted
as their own immutable run (row in `mt5_historical_runs` + parquet artifacts
under RESEARCH/mt5_historical/runs/<run_id>/) and exposed through
`/api/mt5-historical/*`.

Safety contract (enforced by tests/test_v4_6_mt5_historical.py)
---------------------------------------------------------------
A historical backtest NEVER places an order. This package imports no order path:

  HISTORICAL BACKTEST  !=  MT5 DEMO ORDER  !=  LIVE TESTING  !=  LIVE TRADING

It never calls `app.mt5.execution.place_demo_order` / `close_demo_position`,
never calls `MarketBridge.send_market_order` / `close_position`, never touches
the V4.3 live-testing order path, and it writes to none of the order/audit
tables (`executions`, `paper_trades`, `mt5_demo_trades`, `live_test_trades`).
The only tables it writes are `mt5_historical_runs` (its own results) and the
paper/audit layers are left exactly as they were.

Honesty rules
-------------
* Only values the backtest engine actually produced are reported; every missing
  metric is `None` plus an `unavailable` reason, never a made-up number and
  never a silent zero.
* The run records its provenance (dataset id, file, sha256, broker/server
  identity, bar range, engine versions, genome hash, fingerprint) so a result
  can always be traced back to exactly what was tested.
* Real MT5 data is used only when a stored MT5 dataset for the requested
  symbol/timeframe exists. If it does not, the request is refused (or, when the
  caller explicitly asks for it, executed on SIMULATOR data and labelled
  SIMULATOR everywhere) — a simulator run is never presented as an MT5 run.
"""
from . import runs

__all__ = ["runs"]
