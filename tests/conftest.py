"""V4.1 repo-side test bootstrap.

The original suite lives in the DATA tree (DATA/tests). These repo-side tests
exercise the V4.1 research-run workflow against the configured real DATA root
(selected with EVOLUTIONARY_LAB_DATA_ROOT) and only need `backend` importable.
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_BACKEND = _REPO / "backend"
if _BACKEND.exists() and str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import os  # noqa: E402

# V4.7: in production the expensive startup work (DATA discovery, restoration,
# reconciliation, diagnostics) runs in a bounded background bootstrap so the API
# answers immediately. Tests want the deterministic behaviour instead, so the
# lifecycle runs synchronously here.
os.environ.setdefault("EVOLUTIONARY_LAB_STARTUP_BLOCKING", "1")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="module")
def client():
    from app.main import app

    return TestClient(app)


# V6.5 §12-G — calendar determinism: the product's default schedule is Mon-Fri,
# so schedule-dependent tests fail on a weekend through NO defect in the code.
# The schedule module's own clock is shifted back to the nearest weekday (same
# time of day) so every assertion stays exactly as written and the suite is
# deterministic on any calendar day. Nothing else is patched.
import datetime as _dt  # noqa: E402

@pytest.fixture(autouse=True)
def _weekday_schedule_clock(monkeypatch):
    import app.live_testing.schedule as _sched

    class _WeekdayDateTime(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            n = _dt.datetime.now(tz)
            while n.weekday() >= 5:            # Sat/Sun -> the preceding Friday
                n = n - _dt.timedelta(days=1)
            return n

    class _ShiftedDT:                          # schedule.py imports `datetime as dt`
        datetime = _WeekdayDateTime
        timezone = _dt.timezone
        timedelta = _dt.timedelta

    monkeypatch.setattr(_sched, "dt", _ShiftedDT)
    # the engine's market panel has its own weekend check (market_closed) — pin
    # only ITS narrow clock seam; day-boundary accounting elsewhere keeps the
    # real clock so "today vs yesterday" tests stay honest.
    import app.live_testing.engine as _eng

    def _shifted_clock():
        return _WeekdayDateTime.now(_dt.timezone.utc)

    monkeypatch.setattr(_eng, "_market_clock", _shifted_clock)
    # demo scheduling passes now=time.time() explicitly — shift that clock too
    import time as _real_time_mod

    class _ShiftedTimeModule:
        def time(self):
            return _WeekdayDateTime.now(_dt.timezone.utc).timestamp()

        def __getattr__(self, name):
            return getattr(_real_time_mod, name)

    try:
        import app.mt5.demo_schedule as _ds
        monkeypatch.setattr(_ds, "time", _ShiftedTimeModule())
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _fresh_admission_gate():
    """Process-wide admission state must not leak between tests."""
    from app.live_testing.admission import get_admission
    get_admission().reset()
    yield
    get_admission().reset()
