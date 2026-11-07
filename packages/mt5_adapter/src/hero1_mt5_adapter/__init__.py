"""Hero1 MT5 adapter. Phase 5: connection, status, account & symbol metadata,
broker-aware sizing utilities. The MetaTrader5 package is Windows-only;
connection entry points report explicit UNAVAILABLE results elsewhere. NO order
execution lives in this package.
"""
from .probe import probe_mt5_availability
from . import connection, metadata, sizing
from .connection import (
    MT5_PACKAGE_AVAILABLE, MT5_IMPORT_ERROR,
    package_available, discover_terminals, normalize_terminal_path,
    connect, status, account_snapshot, disconnect,
)
from .metadata import build_snapshot, broker_meta_from_snapshot, normalize_symbol_info, normalize_tick
from .sizing import pip_size_from_digits, points_per_pip_from_digits, round_price_to_tick, normalize_volume

__all__ = [
    "probe_mt5_availability", "connection", "metadata", "server_time", "sizing",
    "MT5_PACKAGE_AVAILABLE", "MT5_IMPORT_ERROR",
    "package_available", "discover_terminals", "normalize_terminal_path",
    "terminal_running", "launch_terminal",
    "connect", "status", "account_snapshot", "disconnect",
    "build_snapshot", "broker_meta_from_snapshot", "normalize_symbol_info", "normalize_tick",
    "pip_size_from_digits", "points_per_pip_from_digits", "round_price_to_tick", "normalize_volume",
]
__phase__ = 5
