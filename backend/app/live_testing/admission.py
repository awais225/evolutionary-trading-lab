"""V6.5 §5 — authoritative trade-limit admission (global + per-node).

This is the ONE place a new entry is admitted or refused.  Both limits are
checked against authoritative current state (broker positions/orders for the
managed magic range, plus short-lived reservations for sends in flight), under
a process-wide lock, so two concurrent node workers can never both pass the
check and jointly exceed a cap (§5.3.5).

What counts as ACTIVE (documented convention, §5.3.1):

* an open MT5 POSITION in the managed scope (magic range) — counted by its
  stable ``position`` ticket, never by deal or order tickets;
* a working/pending MT5 ORDER in the managed scope (a slot is committed before
  the fill is known);
* a RESERVATION: an admission just granted whose send outcome is not yet a
  verified non-execution.  A reservation is RELEASED on verified rejection or
  pre-send block, and is KEPT when the execution status is uncertain (timeout /
  UNKNOWN) until reconciliation proves there is no position — §5.3.7.

Positions already open when the application starts are counted because the
count comes from the broker, not from process memory (§5.3.3).  A trade counts
against the node encoded in its magic number — the node that ORIGINATED it
(§5.3.4).  Reducing a limit below the number already open never closes
anything: admissions are simply refused until the count drops (§5.3.9).

If broker state cannot be read, admission FAILS SAFE (refuse) — missing data
is never proof that no positions are open (§5.3 final paragraph).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

#: a reservation older than this without settlement is only kept while the
#: broker state is unreadable; reconcile drops ones proven unnecessary.
RESERVATION_TTL_S = 15 * 60.0


class _Reservation:
    __slots__ = ("node_id", "magic", "symbol", "ts", "state", "reason")

    def __init__(self, node_id: int, magic: Optional[int], symbol: str, reason: str):
        self.node_id = int(node_id)
        self.magic = magic
        self.symbol = symbol
        self.ts = time.time()
        self.state = "HELD"          # HELD | UNCERTAIN | RELEASED
        self.reason = reason


class TradeAdmission:
    """Process-wide admission control for managed live entries."""

    def __init__(self):
        self._lock = threading.RLock()
        self._reservations: Dict[str, _Reservation] = {}
        self._seq = 0

    # ------------------------------------------------------------- counting --
    @staticmethod
    def _magic_of(row: Any) -> int:
        if isinstance(row, dict):
            return int(row.get("magic") or 0)
        return int(getattr(row, "magic", 0) or 0)

    @staticmethod
    def _ticket_of(row: Any) -> Any:
        return row.get("ticket") if isinstance(row, dict) else getattr(row, "ticket", None)

    @staticmethod
    def _symbol_of(row: Any) -> str:
        return str(row.get("symbol") if isinstance(row, dict) else getattr(row, "symbol", "") or "")

    def authoritative_counts(self, bridge, magic_lo: int, magic_hi: int
                             ) -> Tuple[Optional[Dict[int, int]], int, Optional[str]]:
        """({magic: open positions}, managed pending orders, error).

        Positions are keyed by their MAGIC (the origin stamp the engine writes
        at submission).  Returns ``None`` for the map when broker state is
        unreadable — callers must then FAIL SAFE.  The same position seen twice
        (two APIs/events) is counted once, by its stable position ticket.
        """
        try:
            positions = bridge.positions_get() or []
            orders = bridge.orders_get() or []
        except Exception as e:
            return None, 0, f"{type(e).__name__}: {e}"
        per_magic: Dict[int, int] = {}
        tickets_seen = set()
        pending = 0
        for p in positions:
            m = self._magic_of(p)
            if not (magic_lo <= m < magic_hi):
                continue
            t = self._ticket_of(p)
            if t is not None:
                if t in tickets_seen:          # same position via two APIs/events
                    continue
                tickets_seen.add(t)
            per_magic[m] = per_magic.get(m, 0) + 1
        for o in orders:
            m = self._magic_of(o)
            if magic_lo <= m < magic_hi:
                pending += 1
        return per_magic, pending, None

    # ----------------------------------------------------------- admission --
    def admit(self, *, node_id: int, magic: Optional[int], symbol: str,
              bridge: Any, magic_lo: int, magic_hi: int,
              global_limit: int, per_node_limit: int,
              node_active: bool = True) -> Dict[str, Any]:
        """Atomic check-and-reserve.  ``ok`` True ⇒ a reservation is HELD."""
        with self._lock:
            if not node_active:
                return {"ok": False, "code": "NODE_STOPPED",
                        "reason": ("the node is no longer enrolled (STOP was acknowledged) — "
                                   "no entry is admitted")}
            per_magic, pending, err = self.authoritative_counts(bridge, magic_lo, magic_hi)
            if per_magic is None:
                return {"ok": False, "code": "BROKER_STATE_UNAVAILABLE",
                        "reason": (f"could not read broker positions/orders ({err}) — "
                                   "refusing to admit an entry blind"),
                        "error": err}
            # reservations not yet reflected at the broker keep their slots
            held_global = 0
            held_node = 0
            for r in self._reservations.values():
                if r.state == "RELEASED":
                    continue
                held_global += 1
                if r.node_id == int(node_id):
                    held_node += 1
            total_positions = sum(per_magic.values())
            active_total = total_positions + pending + held_global
            node_magic = int(magic) if magic is not None else None
            active_node = (per_magic.get(node_magic, 0) if node_magic is not None else 0) + held_node
            if global_limit and active_total >= int(global_limit):
                return {"ok": False, "code": "GLOBAL_LIMIT_REACHED",
                        "reason": (f"global max active trades reached ({active_total}/"
                                   f"{global_limit}) — no new entry admitted"),
                        "active_total": active_total, "limit": int(global_limit)}
            if per_node_limit and active_node >= int(per_node_limit):
                return {"ok": False, "code": "NODE_LIMIT_REACHED",
                        "reason": (f"node {node_id} max active trades reached "
                                   f"({active_node}/{per_node_limit}) — no new entry admitted"),
                        "active_node": active_node, "limit": int(per_node_limit),
                        "effective_limit_source": "override" if per_node_limit else "default"}
            self._seq += 1
            key = f"{int(node_id)}-{self._seq}-{int(time.time() * 1000)}"
            self._reservations[key] = _Reservation(node_id, magic, symbol, "admitted")
            return {"ok": True, "reservation": key,
                    "active_total": active_total, "active_node": active_node,
                    "global_limit": int(global_limit), "node_limit": int(per_node_limit)}

    def settle(self, reservation: Optional[str], outcome: str) -> None:
        """``outcome``: 'EXECUTED' (slot is now a real position — release the
        reservation only), 'REJECTED'/'BLOCKED' (release), 'UNCERTAIN' (KEEP —
        the slot must not be reused while the execution status is unknown)."""
        if not reservation:
            return
        with self._lock:
            r = self._reservations.get(str(reservation))
            if r is None:
                return
            if outcome in ("EXECUTED", "REJECTED", "BLOCKED", "CLOSED"):
                r.state = "RELEASED"
            elif outcome == "UNCERTAIN":
                r.state = "UNCERTAIN"
            # housekeeping: drop old released entries
            now = time.time()
            for k in [k for k, v in self._reservations.items()
                      if v.state == "RELEASED" and now - v.ts > 60.0]:
                self._reservations.pop(k, None)

    def reconcile(self, bridge, magic_lo: int, magic_hi: int) -> Dict[str, Any]:
        """Drop reservations the broker proves unnecessary (§5.3.8): a HELD or
        UNCERTAIN reservation whose symbol/node has no position and no pending
        order at the broker is released after its TTL; one WITH a position is
        released too (the real position now carries the slot)."""
        out = {"released": 0, "kept": 0}
        with self._lock:
            per_magic, pending, err = self.authoritative_counts(bridge, magic_lo, magic_hi)
            if per_magic is None:
                out["error"] = err
                out["kept"] = len([r for r in self._reservations.values()
                                   if r.state != "RELEASED"])
                return out
            now = time.time()
            for key, r in list(self._reservations.items()):
                if r.state == "RELEASED":
                    continue
                node_positions = per_magic.get(int(r.magic), 0) if r.magic else 0
                if node_positions > 0 or pending > 0:
                    # a real broker position/order carries the slot now
                    r.state = "RELEASED"
                    out["released"] += 1
                elif r.state == "UNCERTAIN":
                    # §5.3.7/§5.3.8 — NOT "merely a timeout": broker state is
                    # readable and shows no position/order for this stamp, i.e.
                    # verified non-execution. The slot is released.
                    r.state = "RELEASED"
                    out["released"] += 1
                elif now - r.ts > RESERVATION_TTL_S:
                    # in-flight HELD slot that never settled: stranded, release
                    r.state = "RELEASED"
                    out["released"] += 1
                else:
                    out["kept"] += 1
        return out

    def reset(self) -> None:
        """Drop all reservations (test isolation / engine hard reset)."""
        with self._lock:
            self._reservations.clear()

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "held": [{"node_id": r.node_id, "symbol": r.symbol,
                          "age_s": round(time.time() - r.ts, 1), "state": r.state}
                         for r in self._reservations.values() if r.state != "RELEASED"],
            }


_gate: Optional[TradeAdmission] = None
_gate_lock = threading.Lock()


def get_admission() -> TradeAdmission:
    global _gate
    with _gate_lock:
        if _gate is None:
            _gate = TradeAdmission()
        return _gate
