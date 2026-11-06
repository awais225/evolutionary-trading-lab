from __future__ import annotations

import time


def utcnow() -> float:
    """Epoch seconds (UTC). Portable across SQLite/PostgreSQL and JSON payloads."""
    return time.time()
