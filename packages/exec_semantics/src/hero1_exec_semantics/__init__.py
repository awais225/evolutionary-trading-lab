"""Shared execution semantics — Phase 3. ONE implementation shared by
backtesting, DeepTesting and MT5 live execution. Phase 1: skeleton only.

Hard rules for the eventual implementation:
  * Fills modeled at next-bar-open (backtest) and broker-exact (live) from one
    cost model interface.
  * Position sizing uses broker tick value/size; refuses to guess when unknown.
  * SL/TP respect broker stops_level; every rejection is typed and explicit.
"""
__phase__ = 3
