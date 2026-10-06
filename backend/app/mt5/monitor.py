"""
MT5 Connection & Feed Monitor (spec §15, §16, §17, §18, §19).

Maintains three independent status indicators:
  1. INTERNET: CONNECTED | DISCONNECTED (lightweight periodic check, spec §18)
  2. METATRADER: CONNECTED | CONNECTING | DISCONNECTED | ERROR (spec §15)
  3. DATA FEED: LIVE | STALE | DISCONNECTED (spec §17)

Detects MT5 disconnection, pauses data-dependent operations, retries
automatically with exponential backoff, records outages, and incrementally
resynchronizes missing bars on reconnect (spec §19).
"""
from __future__ import annotations

import logging
import socket
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from ..activity import activity
from ..api.ws import bus
from ..config import get_config
from . import get_bridge, reset_bridge

log = logging.getLogger("mt5.monitor")


class ConnectionMonitor:
    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.RLock()

        # States
        self.internet_status: str = "CHECKING"      # CONNECTED | DISCONNECTED | ERROR
        self.mt5_status: str = "CONNECTING"         # CONNECTED | CONNECTING | DISCONNECTED | ERROR
        self.data_feed_status: str = "CONNECTING"   # LIVE | STALE | DISCONNECTED

        self.last_internet_check: float = 0.0
        self.last_reconnect_attempt: float = 0.0
        self.reconnect_delay: float = 5.0
        self.consecutive_failures: int = 0
        self.outages: list[Dict[str, Any]] = []

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run_loop, daemon=True, name="connection-monitor")
            self._thread.start()
            log.info("connection monitor started")

    def stop(self) -> None:
        self._stop.set()

    # ---------- lightweight internet check (spec §18) ----------
    def _check_internet(self) -> bool:
        """Lightweight non-invasive check (socket connect to public DNS). Throttled to ~15s."""
        now = time.time()
        if now - self.last_internet_check < 15.0 and self.internet_status in ("CONNECTED", "DISCONNECTED"):
            return self.internet_status == "CONNECTED"

        self.last_internet_check = now
        for host, port in [("1.1.1.1", 53), ("8.8.8.8", 53)]:
            try:
                s = socket.create_connection((host, port), timeout=1.5)
                s.close()
                self.internet_status = "CONNECTED"
                return True
            except OSError:
                continue

        self.internet_status = "DISCONNECTED"
        return False

    # ---------- main monitor loop ----------
    def _run_loop(self) -> None:
        cfg = get_config()
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                log.warning("error in connection monitor tick: %s", e)
            self._stop.wait(3.0)

    def _tick(self) -> None:
        cfg = get_config()
        now = time.time()
        bridge = get_bridge()

        # 1. Check Internet independently
        prev_net = self.internet_status
        self._check_internet()
        if prev_net != "CHECKING" and prev_net != self.internet_status:
            if self.internet_status == "CONNECTED":
                activity.info("SYSTEM", "Internet connection restored")
            else:
                activity.warning("SYSTEM", "Internet connection lost")

        # 2. Check MT5 connection
        is_conn = bridge.available() and getattr(bridge, "_connected", True)
        prev_mt5 = self.mt5_status

        if is_conn:
            self.mt5_status = "CONNECTED"
            self.reconnect_delay = float(cfg.mt5.reconnect_min_s)
            self.consecutive_failures = 0

            if prev_mt5 in ("DISCONNECTED", "ERROR"):
                activity.success("MT5", f"MT5 terminal reconnected ({bridge.profile_key()})")
                self._on_reconnected()
        else:
            self.mt5_status = "DISCONNECTED"
            if prev_mt5 == "CONNECTED":
                activity.warning("MT5", "MT5 terminal connection lost — pausing live operations")
                self.outages.append({"start_ts": now, "profile": bridge.profile_key()})

            # Reconnection backoff logic (spec §19)
            if now - self.last_reconnect_attempt >= self.reconnect_delay:
                self.last_reconnect_attempt = now
                self.mt5_status = "CONNECTING"
                log.info("attempting MT5 reconnection (delay=%.1fs)...", self.reconnect_delay)
                try:
                    reconnected = bridge.connect()
                    if reconnected:
                        self.mt5_status = "CONNECTED"
                        activity.success("MT5", f"MT5 reconnected successfully: {bridge.profile_key()}")
                        self._on_reconnected()
                    else:
                        self.mt5_status = "DISCONNECTED"
                        self.consecutive_failures += 1
                        self.reconnect_delay = min(float(cfg.mt5.reconnect_max_s), self.reconnect_delay * 1.5)
                except Exception as e:
                    self.mt5_status = "ERROR"
                    self.consecutive_failures += 1
                    log.warning("reconnect failed: %s", e)

        # 3. Check Data Feed freshness (spec §17)
        if self.mt5_status != "CONNECTED":
            self.data_feed_status = "DISCONNECTED"
        else:
            # Check last tick or last successful request
            last_activity = max(getattr(bridge, "last_tick_ts", 0.0), getattr(bridge, "last_request_ts", 0.0))
            if last_activity > 0 and (now - last_activity) > cfg.mt5.feed_stale_s:
                self.data_feed_status = "STALE"
            else:
                self.data_feed_status = "LIVE"

        # Broadcast status via event bus
        bus.publish("connection_status", self.status_details())

    def _on_reconnected(self) -> None:
        """Trigger incremental sync after reconnect (spec §19)."""
        from ..data.engine import get_data_engine
        try:
            de = get_data_engine()
            cfg = get_config()
            activity.info("DATA", f"Resynchronizing missing data for {cfg.data.symbol}...")
            de.sync_master(cfg.data.symbol, cfg.data.timeframes[0])
            activity.success("DATA", "Data feed resynchronized after reconnect")
        except Exception as e:
            log.warning("incremental sync on reconnect failed: %s", e)

    def status_details(self) -> Dict[str, Any]:
        """Detailed status for the top-right header and MT5 modal (spec §15, §16)."""
        bridge = get_bridge()
        p = bridge.profile()
        cfg = get_config()
        now = time.time()

        account_masked = str(p.get("account", ""))
        if len(account_masked) > 3:
            account_masked = account_masked[:2] + "***" + account_masked[-1]

        last_tick = getattr(bridge, "last_tick_ts", 0.0)
        last_req = getattr(bridge, "last_request_ts", 0.0)
        lat_ms = getattr(bridge, "last_request_ms", 0.0)

        return {
            "internet": self.internet_status,
            "mt5": self.mt5_status,
            "data_feed": self.data_feed_status,
            "details": {
                "terminal_detected": bridge.available(),
                "terminal_available": bridge.available(),
                "account_available": bool(p.get("account")),
                "platform_mode": bridge.source,
                "bridge_source": bridge.source,
                "is_simulated": bridge.source == "SIMULATOR",
                "broker": p.get("broker", "Unknown"),
                "server": p.get("server", "Unknown"),
                "account_masked": account_masked,
                "selected_symbol": cfg.data.symbol,
                "last_tick_ts": last_tick if last_tick > 0 else None,
                "last_request_ts": last_req if last_req > 0 else None,
                "last_heartbeat_ts": now,
                "last_heartbeat_str": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                "latency_ms": round(lat_ms, 1),
                "reconnect_delay": self.reconnect_delay,
                "reconnect_failures": self.consecutive_failures,
                "reconnection_attempts": self.consecutive_failures,
            },
        }


_monitor: Optional[ConnectionMonitor] = None


def get_connection_monitor() -> ConnectionMonitor:
    global _monitor
    if _monitor is None:
        _monitor = ConnectionMonitor()
    return _monitor
