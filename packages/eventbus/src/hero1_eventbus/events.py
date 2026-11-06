from __future__ import annotations

import json
from typing import Any, Optional

from hero1_storage.enums import EventCategory, EventSeverity
from hero1_storage.timeutil import utcnow


def build_event(*, category: str, severity: str, message: str,
                instrument: Optional[str] = None, subsystem: str = "system",
                payload: Optional[dict] = None, node_id: Optional[int] = None) -> dict:
    cat = str(category).upper()
    sev = str(severity).upper()
    if cat not in {c.value for c in EventCategory}:
        raise ValueError(f"invalid event category: {category!r}")
    if sev not in {s.value for s in EventSeverity}:
        raise ValueError(f"invalid event severity: {severity!r}")
    return {
        "ts": utcnow(),
        "category": cat,
        "severity": sev,
        "instrument": instrument.upper() if instrument else None,
        "subsystem": subsystem,
        "message": str(message)[:500],
        "payload": json.dumps(payload, separators=(",", ":"), default=str) if payload is not None else None,
        "node_id": node_id,
    }
