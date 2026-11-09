import json
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.decision_ledger import load_all_events, persist_event_batch
from hero1_storage.instrument_models import DeepTestDecisionEvent, DeepTestRun


def test_incremental_batches_survive_session_restart_and_retry_idempotently():
    engine = create_engine("sqlite://")
    DeepTestRun.__table__.create(engine)
    DeepTestDecisionEvent.__table__.create(engine)
    with Session(engine) as session:
        session.add(DeepTestRun(id=41, instrument_id="XAUUSD", node_id=1,
                                status="RUNNING", mode="deeptest"))
        session.commit()
        batch = [{"event_id": "stable-1", "sequence": 0, "event": "CANDLE_EVALUATED",
                  "candle_ts": 1700000000, "signal_id": None},
                 {"event_id": "stable-2", "sequence": 1, "event": "CONDITION_EVALUATED",
                  "candle_ts": 1700000000, "signal_id": "sig-1"}]
        assert persist_event_batch(session, batch, run_id=41, node_id=1,
            node_version=3, fingerprint="a" * 64, instrument="XAUUSD", timeframe="M1") == 2
        # Simulate retry after an acknowledgement was lost: primary-key event IDs
        # prevent duplicate rows while preserving the original sequence.
        assert persist_event_batch(session, batch, run_id=41, node_id=1,
            node_version=3, fingerprint="a" * 64, instrument="XAUUSD", timeframe="M1") == 0
    # A fresh ORM session is the restart boundary.
    with Session(engine) as restarted:
        events = load_all_events(restarted, 41)
        assert [e["event_id"] for e in events] == ["stable-1", "stable-2"]
        assert [e["sequence"] for e in events] == [0, 1]
        assert restarted.query(DeepTestDecisionEvent).filter_by(run_id=41).count() == 2
    engine.dispose()
