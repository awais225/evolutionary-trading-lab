"""WebSocket event bus: orchestrator threads publish; dashboard subscribes."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import deque
from typing import Any, Dict

log = logging.getLogger("api.ws")


class EventBus:
    def __init__(self, maxlen: int = 800):
        self.recent: deque = deque(maxlen=maxlen)
        self._subs: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs.discard(q)

    def publish(self, event_type: str, payload: Dict[str, Any]) -> None:
        ev = {"ts": time.time(), "type": event_type, "payload": payload}
        self.recent.append(ev)
        loop = self._loop
        if loop is None:
            return
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                loop.call_soon_threadsafe(_safe_put, q, ev)
            except RuntimeError:
                pass

    def recent_events(self, n: int = 100) -> list[Dict]:
        return list(self.recent)[-n:]


def _safe_put(q: asyncio.Queue, ev: Dict) -> None:
    try:
        q.put_nowait(ev)
    except asyncio.QueueFull:
        try:
            q.get_nowait()
            q.put_nowait(ev)
        except Exception:
            pass


bus = EventBus()
