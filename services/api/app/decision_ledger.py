"""Append-only DeepTesting decision-event persistence and retrieval."""
from __future__ import annotations

import json
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from hero1_storage.instrument_models import DeepTestDecisionEvent


def persist_event_batch(session: Session, events: list[dict], *, run_id: int,
                        node_id: int, node_version: int | None, fingerprint: str,
                        instrument: str, timeframe: str) -> int:
    """Insert a bounded batch idempotently. The stable event ID is the key.

    Commit here intentionally: if later execution fails, already acknowledged
    batches remain visible with the failed run record.
    """
    rows = [{
        "event_id": e["event_id"], "run_id": run_id,
        "sequence": int(e["sequence"]), "node_id": node_id,
        "node_version": node_version, "fingerprint": fingerprint,
        "instrument_id": instrument.upper(), "timeframe": timeframe,
        "signal_id": e.get("signal_id"), "related_event_id": e.get("related_event_id"),
        "candle_ts": int(e["candle_ts"]), "event_type": e["event"],
        "payload_json": json.dumps(e, separators=(",", ":")),
    } for e in events]
    if not rows:
        return 0
    # Use DBAPI executemany instead of constructing a multi-VALUES statement.
    # Evidence batches contain thousands of JSON rows; .values(rows) makes
    # SQLAlchemy rebuild/compile a statement with every row's bind tree on
    # every flush. Executemany keeps the same atomic/idempotent batch contract
    # and schema while compiling one stable statement per batch.
    stmt = sqlite_insert(DeepTestDecisionEvent.__table__).on_conflict_do_nothing(
        index_elements=["event_id"])
    result = session.connection().execute(stmt, rows)
    session.commit()
    return int(result.rowcount or 0)


def load_event_batch(session: Session, run_id: int, *, after_sequence: int = -1,
                     limit: int = 4096) -> list[dict]:
    rows = (session.query(DeepTestDecisionEvent)
            .filter(DeepTestDecisionEvent.run_id == run_id,
                    DeepTestDecisionEvent.sequence > after_sequence)
            .order_by(DeepTestDecisionEvent.sequence)
            .limit(max(1, min(int(limit), 10000))).all())
    return [json.loads(row.payload_json) for row in rows]


def load_all_events(session: Session, run_id: int, *, batch_size: int = 4096) -> list[dict]:
    events, cursor = [], -1
    while True:
        batch = load_event_batch(session, run_id, after_sequence=cursor, limit=batch_size)
        if not batch:
            return events
        events.extend(batch)
        cursor = int(batch[-1]["sequence"])
