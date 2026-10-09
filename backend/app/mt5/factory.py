"""Bridge selection: real MT5 when possible, otherwise labelled SIMULATOR."""
from __future__ import annotations

import logging
import sys
import threading
from typing import Dict, Optional

from .bridge import MarketBridge
from .simulator import SimulatorBridge
from .mt5_real import MT5RealBridge, MT5_PACKAGE_AVAILABLE
from .config import get_saved_terminal_path, set_saved_terminal_path, load_mt5_config

log = logging.getLogger("mt5.factory")
_bridge: Optional[MarketBridge] = None
_lock = threading.Lock()


def build_bridge(cfg=None, explicit_path: Optional[str] = None) -> MarketBridge:
    from ..config import get_config
    cfg = cfg or get_config().mt5
    mode = (cfg.mode or "auto").lower()

    target_path = explicit_path or cfg.path or get_saved_terminal_path()

    if mode in ("real", "auto"):
        real = MT5RealBridge(login=cfg.login, password=cfg.password,
                             server=cfg.server, path=target_path or "",
                             timeout_ms=cfg.timeout_ms)
        # connect() itself records the failure reason (including "package not
        # importable") into status()/last_error — an explicit REAL request must
        # expose a clear unavailable state, never silently swap the simulator in.
        if real.connect():
            log.info("Using REAL MT5 bridge (Path: %s)", target_path or "auto-discovered")
            return real
        if mode == "real":
            log.warning("Real MT5 requested but unavailable: %s", real.status())
            return real
        # V5.1a §8 - the fallback is a reported, explained mode, never a silent one:
        # name the actual blocking fact instead of logging an empty reason.
        st = real.status() or {}
        reason = st.get("last_error") or ""
        if not reason and not MT5_PACKAGE_AVAILABLE:
            from .mt5_real import MT5_IMPORT_ERROR
            reason = (f"the MetaTrader5 package is not importable in this interpreter "
                      f"({sys.executable}): {MT5_IMPORT_ERROR or 'not installed'}")
        if not reason:
            reason = "the MetaTrader5 package is present but no terminal could be connected"
        log.warning("MT5 unavailable (%s) -> SIMULATOR mode (explicitly reported, not used for "
                    "real execution)", reason)
    return SimulatorBridge()


def get_bridge() -> MarketBridge:
    global _bridge
    with _lock:
        if _bridge is None:
            _bridge = build_bridge()
            _bridge.connect()
        return _bridge


def reset_bridge(explicit_path: Optional[str] = None) -> None:
    """Rebuild bridge (e.g. after user changes MT5 settings or terminal path)."""
    global _bridge
    with _lock:
        if _bridge is not None:
            try:
                _bridge.disconnect()
            except Exception:
                pass
        _bridge = build_bridge(explicit_path=explicit_path)
        _bridge.connect()


def connect_mt5_terminal(path: Optional[str] = None) -> Dict:
    """Attempts to connect to MT5 with optional terminal path, updating saved config."""
    if path:
        set_saved_terminal_path(path)
    reset_bridge(explicit_path=path)
    return bridge_status()


def disconnect_mt5_terminal() -> Dict:
    """Explicitly disconnects MT5 and forces SIMULATOR mode."""
    global _bridge
    with _lock:
        if _bridge is not None:
            try:
                _bridge.disconnect()
            except Exception:
                pass
        _bridge = SimulatorBridge()
        _bridge.connect()
    return bridge_status()


def bridge_status() -> Dict:
    b = get_bridge()
    st = b.status()
    cfg = load_mt5_config()
    return {
        "active_bridge": b.name,
        "source": b.source,
        "is_simulated": b.source == "SIMULATOR",
        "mt5_package_installed": MT5_PACKAGE_AVAILABLE,
        "saved_terminal_path": cfg.get("terminal_path"),
        "last_connected_company": cfg.get("last_connected_company"),
        "last_connected_build": cfg.get("last_connected_build"),
        "terminal_build": getattr(b, "_terminal_build", None),
        "terminal_company": getattr(b, "_terminal_company", None),
        "terminal_path": getattr(b, "_terminal_path", None),
        **st,
    }
