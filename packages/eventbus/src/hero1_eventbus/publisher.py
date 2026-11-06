from __future__ import annotations

import logging
import threading
from typing import Optional

from hero1_storage.repositories import EventRepository, events_to_dicts
from hero1_storage.engine import registry_session
from .bus import get_event_bus
from .events import build_event

log = logging.getLogger("hero1.eventbus.publisher")

_publisher: Optional["EventPublisher"] = None
_lock = threading.Lock()


class EventPublisher:
    """Persist to the durable store, then fan out in-process."""

    def __init__(self) -> None:
        self.bus = get_event_bus()

    def publish(self, *, category: str, severity: str, message: str,
                instrument: Optional[str] = None, subsystem: str = "system",
                payload: Optional[dict] = None, node_id: Optional[int] = None) -> dict:
        event = build_event(category=category, severity=severity, message=message,
                            instrument=instrument, subsystem=subsystem,
                            payload=payload, node_id=node_id)
        with registry_session() as s:
            stored = EventRepository(s).append(
                ts=event["ts"], category=event["category"], severity=event["severity"],
                instrument_id=event["instrument"], subsystem=event["subsystem"],
                message=event["message"], payload=event["payload"], node_id=event["node_id"],
            )
            event["id"] = stored.id
        out = events_to_dicts([stored])[0]
        self.bus.publish_nowait(out)
        return out


def get_event_publisher() -> EventPublisher:
    global _publisher
    with _lock:
        if _publisher is None:
            _publisher = EventPublisher()
        return _publisher
