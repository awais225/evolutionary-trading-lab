"""Indicator library — Phase 3 mathematics (causal series: SMA/EMA/RSI/ATR/
MACD/BBANDS/ROC/STOCH/ADX/VWAP). Warmup positions are None, never filled.
Formulas documented in compute.py."""
from .compute import compute_indicator

__version__ = "0.3.0"
__phase__ = 3

__all__ = ["compute_indicator", "__version__"]
