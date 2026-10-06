"""V4.3 - controlled Live Testing layer (demo only).

Sits strictly ON TOP of the V4.2 MT5 demo execution path: it decides *whether*
a signal may be traded (risk, limits, safety locks), sizes it from the broker's
real symbol contract specification, and then hands the order to
:mod:`app.mt5.execution`. It never bypasses the V4.2 account guard, validation,
duplicate protection or audit trail.

The engine is INACTIVE by default and has no persisted "enabled" flag: nothing
here can resume trading after a restart, reconnect or reload by itself.
"""

from .engine import LiveTestingEngine, get_live_testing_engine  # noqa: F401

__all__ = ["LiveTestingEngine", "get_live_testing_engine"]
