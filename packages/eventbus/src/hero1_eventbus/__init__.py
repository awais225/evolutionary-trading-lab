"""Hero1 event architecture.

Design: events are (1) persisted to the registry ``global_events`` table and
(2) fanned out in-process to async subscribers (the WebSocket layer). Clients
replay missed events with ``since_id`` and then receive live pushes — there is
no database polling loop anywhere in this path.
"""
from .bus import EventBus, get_event_bus
from .publisher import EventPublisher, get_event_publisher
from .events import build_event

__all__ = ["EventBus", "get_event_bus", "EventPublisher", "get_event_publisher", "build_event"]
