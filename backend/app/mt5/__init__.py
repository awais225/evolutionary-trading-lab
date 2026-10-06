"""
Market data bridges.

MarketBridge is the abstract contract used by the whole lab (data engine,
paper trading). Two implementations exist:

* MT5RealBridge  -> real MetaTrader 5 terminal via the `MetaTrader5` package
                    (Windows only). Never faked: if the package or terminal
                    is unavailable it reports `available=False`.
* SimulatorBridge-> clearly-marked SIMULATOR producing synthetic-but-plausible
                    XAUUSD-style data for development / non-Windows machines.

The rest of the system must not know which one it is talking to, but every
record is tagged with its `source` ("MT5" | "SIMULATOR") so nothing synthetic
is ever presented as real.
"""
from .bridge import MarketBridge, Bar, Tick, SymbolInfo, AccountInfo
from .factory import get_bridge, bridge_status, reset_bridge

__all__ = ["MarketBridge", "Bar", "Tick", "SymbolInfo", "AccountInfo",
           "get_bridge", "bridge_status", "reset_bridge"]
