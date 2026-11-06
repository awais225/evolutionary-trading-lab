from __future__ import annotations

import asyncio
import threading
from typing import Optional, Set


class EventBus:
    """In-process fanout that is SAFE ACROSS THREADS.

    The FastAPI app's event loop is attached at startup (attach_loop). Producers
    on worker threads (sync endpoints) deliver via loop.call_soon_threadsafe;
    consumers (WebSocket handlers) are awaited on that same loop. Slow
    subscribers drop the oldest queued event rather than blocking producers —
    the durable store remains the source of truth; clients replay via since_id.
    """

    def __init__(self, max_queue: int = 200) -> None:
        self._subs: Set[asyncio.Queue] = set()
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self.max_queue = max_queue

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self.max_queue)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs.discard(q)

    @staticmethod
    def _offer(q: asyncio.Queue, event: dict) -> None:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
                q.put_nowait(event)
            except Exception:
                pass

    def publish_nowait(self, event: dict) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return  # durable store has the event; nothing to fan out to
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                loop.call_soon_threadsafe(self._offer, q, event)
            except RuntimeError:
                pass

    async def publish(self, event: dict) -> None:
        self.publish_nowait(event)

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)


_bus: Optional[EventBus] = None
_bus_lock = threading.Lock()


def get_event_bus() -> EventBus:
    global _bus
    with _bus_lock:
        if _bus is None:
            _bus = EventBus()
        return _bus
