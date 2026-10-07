"""V5.2.2 — ONE live-tradeability predicate (shared by the engine and the counters).

The live engine (which may enrol a node and let it trade DEMO orders) and the
population counters (which tell the operator how many nodes are live-testing
*eligible*) have to answer "may this node trade live?" with the same rule. If they
do not, the dashboard number and the engine behaviour drift apart and the operator
cannot tell whether ``0`` means *nothing qualifies* or *nothing has been started*.

That confusion is exactly what this module removes:

    eligibility  = CAPABILITY — the START path could enrol this node right now.
    active       = ENROLMENT — the node is enrolled/started at this moment.

They are now two separate numbers (``LIVE_TESTING_ELIGIBLE`` and
``LIVE_TESTING_ACTIVE``).  Nothing is lost and nothing is faked: a node that is
enrolled is not automatically called "eligible", and a qualified node is not
silently reported as "wired".

The rule itself is unchanged, and the wording of every reason is byte-for-byte
the wording the engine already showed the operator:

  1. never a LEGACY_TEST record (infrastructure, not a trading candidate);
  2. the stored status must not be one of :data:`EXCLUDED_STATUSES`;
  3. the genome must carry a symbol;
  4. the genome must carry at least one entry rule (long or short).

The predicate is pure and read-only: it takes a row (as read from ``strategies``,
with ``genome`` either a dict or the stored JSON text) and returns a verdict.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

#: stored statuses that disqualify a node from live trading (engine §16, unchanged)
EXCLUDED_STATUSES = ("DEAD", "KILLED", "RETIRED", "INVALID", "FAILED", "ARCHIVED")

#: reason texts — kept identical to the strings the engine's eligibility notes use
REASON_LEGACY = "LEGACY_TEST node - never a live trading candidate"
REASON_NO_CONFIG = "node has no trading configuration (genome/symbol missing)"
REASON_NO_ENTRY = "node has no entry conditions"

#: stable machine-readable codes for the same verdicts
CODE_OK = "OK"
CODE_LEGACY = "LEGACY_TEST"
CODE_STATUS = "STATUS_NOT_ELIGIBLE"
CODE_NO_CONFIG = "NO_TRADING_CONFIGURATION"
CODE_NO_ENTRY = "NO_ENTRY_CONDITIONS"


def genome_of(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The row's genome as a dict (``{}`` when it is missing or unreadable)."""
    g = (row or {}).get("genome")
    if isinstance(g, str):
        try:
            g = json.loads(g)
        except Exception:
            return {}
    return g if isinstance(g, dict) else {}


def tradeability(row: Optional[Dict[str, Any]],
                 genome: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """May this stored node trade live?  Verdict + reason, never an exception.

    ``genome`` may be supplied when the caller already parsed it (the engine reads
    the genome from the joined strategy row).
    """
    row = row or {}
    source = str(row.get("data_source") or "").strip().upper()
    status = str(row.get("status") or "").strip().upper()
    g = genome if genome is not None else genome_of(row)

    if source == "LEGACY_TEST":
        return {"ok": False, "code": CODE_LEGACY, "reason": REASON_LEGACY}
    if status in EXCLUDED_STATUSES:
        return {"ok": False, "code": CODE_STATUS,
                "reason": f"node status {status} is not eligible for live testing"}
    if not g or not g.get("symbol"):
        return {"ok": False, "code": CODE_NO_CONFIG, "reason": REASON_NO_CONFIG}
    if not (g.get("entry_long") or g.get("entry_short")):
        return {"ok": False, "code": CODE_NO_ENTRY, "reason": REASON_NO_ENTRY}
    return {"ok": True, "code": CODE_OK, "reason": None}
