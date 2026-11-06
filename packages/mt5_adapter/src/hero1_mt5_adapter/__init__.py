"""Hero1 MT5 adapter. Phase 1 ships ONLY availability probing — no connection,
no orders, no login. Full adapter lands in Phase 5 and is verified on Windows.
"""
from .probe import probe_mt5_availability

__all__ = ["probe_mt5_availability"]
__phase__ = 5
