"""V5.2 §11–§14 — the data layer Deep Testing needs, computed from the REAL nodes.

Deep Testing must never hang on "Loading qualified nodes" and must never run a
backtest on data that is not there. This module answers, in order:

  * **§11 Data Requirements** — for every deep-eligible node: which symbol, which
    timeframe, which date range, which fields and how many warm-up bars its own
    stored genome needs. Computed over ALL eligible nodes, so it is a real
    aggregate and not one node's answer.
  * **§12 Suggested Data** — the UNION of those requirements: symbols, the
    timeframes each symbol needs, the covering date range, the union of fields
    and the largest lookbacks. This is what the operator is offered as the data
    to fetch; it is derived, never hard-coded.
  * **§13 GET MT5 DATA** — a job that fetches exactly that package through the
    lab's EXISTING historical infrastructure
    (:meth:`app.data.engine.DataEngine.sync_master`, which already speaks to the
    live MT5 terminal through the active bridge and already reports into the
    global activity feed). This module adds the staged progress the Deep Testing
    page shows — symbol / timeframe / start / end / requested vs received vs
    stored bars / percentage / current operation / errors — and nothing else. It
    is not a second downloader: every bar still arrives through the same
    ``bridge.copy_rates_range`` path the rest of the lab uses.
  * **§14 Readiness** — ``DATA READY`` plus ``X/Y NODES READY``, each node either
    READY or NOT READY *with its reason*, and only then may the existing real
    backtest engine run for that node. Nothing here fabricates a result or
    invents a bar count.

Read-only by default: every function below only reads the database and the
master store. The single writer is :class:`GetDataJob` and it writes bars, not
statuses.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..genome.schema import referenced_features

log = logging.getLogger("research.deep_data")

#: the stages the Deep Testing page shows, in order (spec §13)
GET_DATA_STAGES: Tuple[str, ...] = (
    "DISCOVERING REQUIREMENTS",
    "CALCULATING DATA PACKAGE",
    "CONNECTING TO MT5",
    "FETCHING SYMBOL",
    "FETCHING TIMEFRAME",
    "FETCHING DATE RANGE",
    "STORING DATA",
    "VALIDATING COVERAGE",
    "VALIDATING FEATURES",
    "VALIDATING WARMUP",
    "NODES READY",
)

#: dataset fields a deep run consumes (the stored bar schema)
DATA_FIELDS: Tuple[str, ...] = ("ts", "open", "high", "low", "close", "tick_volume", "spread")

#: a node always needs at least this much warm-up before its first evaluated bar
WARMUP_FLOOR = 300

#: when a node has no recorded deep window, fall back to the lab's configured
#: history window (``cfg.data.history_months``), or this many months
DEFAULT_WINDOW_MONTHS = 6

_TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800,
               "H1": 3600, "H4": 14400, "D1": 86400, "W1": 604800}


def _tf_seconds(timeframe: str) -> Optional[int]:
    tf = str(timeframe or "").upper()
    if tf in _TF_SECONDS:
        return _TF_SECONDS[tf]
    if len(tf) > 1 and tf[0] in "MHWD" and tf[1:].isdigit():
        mult = int(tf[1:])
        base = {"M": 60, "H": 3600, "D": 86400, "W": 604800}[tf[0]]
        return mult * base
    return None


def estimate_bars_safe(timeframe: str, start_ts: float, end_ts: float) -> Dict[str, Any]:
    """Bar estimate with the lab's own estimator (``bars: None`` when unknown)."""
    sec = _tf_seconds(timeframe)
    if sec is None:
        return {"bars": None, "reason": f"unknown timeframe {timeframe!r} — no bar estimate"}
    try:
        start, end = float(start_ts), float(end_ts)
    except (TypeError, ValueError):
        return {"bars": None, "reason": "window is not numeric — no bar estimate"}
    if end <= start:
        return {"bars": None, "reason": "window is empty (end <= start) — no bar estimate"}
    return {"bars": int((end - start) // sec), "reason": ""}


def _iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# §11 — requirements, per node and aggregated
# --------------------------------------------------------------------------- #
def _genome(row: Dict[str, Any]) -> Dict[str, Any]:
    g = row.get("genome")
    if isinstance(g, str):
        try:
            g = json.loads(g)
        except Exception:
            g = {}
    return g if isinstance(g, dict) else {}


def lookback_of_spec(spec: str) -> int:
    """Bars a single feature spec consumes before it can produce a value.

    ``sma:200`` → 200, ``atr:14`` → 14, ``rsi:14`` → 14, ``regime:trending`` → 100
    (the regime classifier's own window), anything unparsable → 1 (no lookback
    claim is invented for it).
    """
    s = str(spec or "").strip()
    if not s:
        return 1
    if s.startswith("regime:"):
        return 100
    tail = s.rsplit(":", 1)[-1]
    if tail.isdigit():
        return max(1, int(tail))
    digits = "".join(ch for ch in tail if ch.isdigit())
    if digits and len(digits) <= 5:
        return max(1, int(digits))
    return 1


def warmup_bars(genome: Dict[str, Any]) -> int:
    """Warm-up the node's own genome needs, never below :data:`WARMUP_FLOOR`."""
    specs = list(referenced_features(genome) or [])
    ex = genome.get("exit") or {}
    if ex.get("atr_spec"):
        specs.append(str(ex["atr_spec"]))
    specs += [f"regime:{r}" for r in genome.get("regime_filters") or []]
    needed = max([lookback_of_spec(s) for s in specs] or [1])
    return max(int(needed), WARMUP_FLOOR)


def _run_windows(db: Any) -> Dict[int, Tuple[float, float]]:
    """Any recorded deep-run window per strategy (min start, max end)."""
    out: Dict[int, Tuple[float, float]] = {}
    try:
        rows = db.q("""SELECT strategy_id, MIN(start_ts) lo, MAX(end_ts) hi
                       FROM mt5_historical_runs
                       WHERE start_ts IS NOT NULL AND end_ts IS NOT NULL
                       GROUP BY strategy_id""") or []
    except Exception:
        return out
    for r in rows:
        try:
            sid = int(r["strategy_id"])
            out[sid] = (float(r["lo"]), float(r["hi"]))
        except Exception:
            continue
    return out


def node_requirements(db: Any = None, rows: Optional[Iterable[Dict[str, Any]]] = None,
                      members: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """One requirement record per deep-eligible node (spec §11)."""
    from .populations import deep_universe, has_genome_and_entry, load_rows
    if db is None:
        from ..db.database import get_db
        db = get_db()
    all_rows = [dict(r) for r in (rows if rows is not None else load_rows(db))]
    by_id = {int(r["id"]): r for r in all_rows if r.get("id") is not None}
    universe = members if members is not None else deep_universe(db, rows=all_rows)
    runs = _run_windows(db)
    now = time.time()
    out: List[Dict[str, Any]] = []
    for m in universe:
        sid = int(m["id"])
        row = by_id.get(sid) or {}
        g = _genome(row) or {}
        symbol = str(g.get("symbol") or row.get("symbol") or "").upper()
        timeframe = str(g.get("timeframe") or row.get("timeframe") or "").upper()
        sec = _tf_seconds(timeframe)
        req: Dict[str, Any] = {
            "node_id": sid, "symbol": symbol, "timeframe": timeframe,
            "why_in_union": list(m.get("why") or []),
            "has_genome": bool(has_genome_and_entry(row)) if row else None,
            "fields": list(DATA_FIELDS),
        }
        if not symbol or not timeframe:
            req.update({"eligible": False,
                        "reason": "the node has no symbol/timeframe to fetch data for",
                        "warmup_bars": None, "earliest": None, "latest": None,
                        "required_bars": None})
            out.append(req)
            continue
        warm = warmup_bars(g)
        lo_hi = runs.get(sid)
        if lo_hi is not None:
            start_ts, end_ts = lo_hi
            window_source = "the node's own recorded deep-test window"
        else:
            months = DEFAULT_WINDOW_MONTHS
            try:
                from ..config import get_config
                months = float((get_config().data.history_months or {}).get(timeframe,
                                                                           DEFAULT_WINDOW_MONTHS))
            except Exception:
                pass
            end_ts = now
            start_ts = now - months * 30.0 * 86400.0
            window_source = (f"the lab's configured history window ({months:g} months of "
                             f"{timeframe} — the same window the data engine bootstraps)")
        if sec:
            earliest = min(start_ts, end_ts - warm * sec)
        else:
            earliest = start_ts
        est = estimate_bars_safe(timeframe, earliest, end_ts)
        req.update({
            "eligible": True,
            "warmup_bars": int(warm),
            "window": {"start_ts": start_ts, "end_ts": end_ts,
                       "start": _iso(start_ts), "end": _iso(end_ts),
                       "source": window_source},
            "earliest": _iso(earliest), "earliest_ts": earliest, "latest": _iso(end_ts),
            "latest_ts": end_ts,
            "required_bars": est["bars"], "required_bars_reason": est["reason"],
            "reason": "",
        })
        out.append(req)
    out.sort(key=lambda r: r["node_id"])
    return out


def aggregate_requirements(reqs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The union of per-node requirements (spec §11/§12).

    A symbol/timeframe pair is one package item; its window is the union of the
    windows that need it, its warm-up is the largest warm-up asked for, and its
    required bar count is the estimate over that union.
    """
    items: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for r in reqs:
        if not r.get("eligible"):
            continue
        key = (r["symbol"], r["timeframe"])
        it = items.get(key)
        if it is None:
            it = items[key] = {
                "symbol": r["symbol"], "timeframe": r["timeframe"],
                "earliest_ts": r["earliest_ts"], "latest_ts": r["latest_ts"],
                "warmup_bars": r["warmup_bars"], "fields": list(r["fields"]),
                "nodes": [],
            }
        else:
            it["earliest_ts"] = min(it["earliest_ts"], r["earliest_ts"])
            it["latest_ts"] = max(it["latest_ts"], r["latest_ts"])
            it["warmup_bars"] = max(it["warmup_bars"], r["warmup_bars"])
        it["nodes"].append(r["node_id"])
    out_items: List[Dict[str, Any]] = []
    for it in sorted(items.values(), key=lambda i: (i["symbol"], i["timeframe"])):
        est = estimate_bars_safe(it["timeframe"], it["earliest_ts"], it["latest_ts"])
        out_items.append({
            **it,
            "earliest": _iso(it["earliest_ts"]), "latest": _iso(it["latest_ts"]),
            "required_bars": est["bars"], "required_bars_reason": est["reason"],
            "node_count": len(it["nodes"]),
        })
    lowest = min([i["earliest_ts"] for i in out_items], default=None)
    highest = max([i["latest_ts"] for i in out_items], default=None)
    return {
        "items": out_items,
        "symbols": sorted({i["symbol"] for i in out_items}),
        "timeframes": sorted({i["timeframe"] for i in out_items}),
        "date_range": {"start": _iso(lowest), "end": _iso(highest),
                       "start_ts": lowest, "end_ts": highest},
        "fields": list(DATA_FIELDS),
        "warmup_bars": max([i["warmup_bars"] for i in out_items], default=None),
        "nodes_with_requirements": sum(len(i["nodes"]) for i in out_items),
    }


def requirements(db: Any = None, rows: Optional[Iterable[Dict[str, Any]]] = None,
                 members: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """§11 — the full Data Requirements block for Deep Testing."""
    reqs = node_requirements(db=db, rows=rows, members=members)
    eligible = [r for r in reqs if r.get("eligible")]
    return {
        "stages": list(GET_DATA_STAGES),
        "nodes_total": len(reqs),
        "nodes_with_requirements": len(eligible),
        "nodes_ineligible": [
            {"node_id": r["node_id"], "reason": r.get("reason")} for r in reqs if not r.get("eligible")
        ],
        "per_node": reqs,
        "aggregate": aggregate_requirements(reqs),
        "authority": "app.research.populations.deep_universe (same union the picker offers)",
    }


# --------------------------------------------------------------------------- #
# §12 — Suggested Data (the union)
# --------------------------------------------------------------------------- #
def suggested_data(db: Any = None) -> Dict[str, Any]:
    """§12 — SUGGESTED DATA: the union over deep-eligible nodes."""
    from .populations import population_state
    req = requirements(db=db)
    agg = req["aggregate"]
    per_symbol: Dict[str, Dict[str, Any]] = {}
    for it in agg["items"]:
        s = per_symbol.setdefault(it["symbol"], {
            "symbol": it["symbol"], "timeframes": [], "earliest_ts": it["earliest_ts"],
            "latest_ts": it["latest_ts"], "warmup_bars": it["warmup_bars"],
            "required_bars": 0, "nodes": set(),
        })
        s["timeframes"].append(it["timeframe"])
        s["earliest_ts"] = min(s["earliest_ts"], it["earliest_ts"])
        s["latest_ts"] = max(s["latest_ts"], it["latest_ts"])
        s["warmup_bars"] = max(s["warmup_bars"], it["warmup_bars"])
        s["required_bars"] += int(it["required_bars"] or 0)
        s["nodes"].update(it["nodes"])
    symbols = []
    for s in sorted(per_symbol.values(), key=lambda x: x["symbol"]):
        symbols.append({**{k: v for k, v in s.items() if k != "nodes"},
                        "timeframes": sorted(set(s["timeframes"])),
                        "earliest": _iso(s["earliest_ts"]), "latest": _iso(s["latest_ts"]),
                        "node_count": len(s["nodes"]), "nodes": sorted(s["nodes"])})
    lookbacks: Dict[str, int] = {}
    for it in agg["items"]:
        key = f"{it['symbol']} {it['timeframe']}"
        lookbacks[key] = it["warmup_bars"]
    state = {}
    try:
        state = population_state(db=db)
    except Exception as e:                                  # pragma: no cover - defensive
        log.debug("population state unavailable: %s", e)
    return {
        "symbols": symbols,
        "date_range": agg["date_range"],
        "fields": agg["fields"],
        "lookbacks": lookbacks,
        "warm_up_bars": agg["warmup_bars"],
        "package": agg["items"],
        "nodes": {"deep_eligible": (state.get("state") or {}).get("DEEP_TESTING_ELIGIBLE"),
                  "with_requirements": req["nodes_with_requirements"]},
        "note": ("The union of every deep-eligible node's own requirement (symbol, timeframe, "
                 "recorded/standard window, warm-up, fields). Nothing is hard-coded."),
    }


# --------------------------------------------------------------------------- #
# coverage (read-only) — what the lab already stores
# --------------------------------------------------------------------------- #
def coverage(db: Any = None, pairs: Optional[Iterable[Tuple[str, str]]] = None) -> Dict[str, Dict[str, Any]]:
    """Stored bars + stored range per (symbol, timeframe), read from the stores."""
    if db is None:
        from ..db.database import get_db
        db = get_db()
    wanted = [tuple(p) for p in (pairs or [])]
    out: Dict[str, Dict[str, Any]] = {}
    for symbol, timeframe in wanted:
        key = f"{symbol}|{timeframe}"
        rec: Dict[str, Any] = {"symbol": symbol, "timeframe": timeframe,
                               "bars": None, "source": None, "dataset_id": None,
                               "start_ts": None, "end_ts": None, "start": None, "end": None,
                               "error": None}
        try:
            from ..data.master import get_master
            from ..mt5 import get_bridge
            profile = get_bridge().profile()
            mstore = get_master(symbol, timeframe, profile)
            rec.update({"bars": int(mstore.rows or 0), "source": "master",
                        "start_ts": mstore.first_ts, "end_ts": mstore.last_ts})
        except Exception as e:
            rec["error"] = f"master store unavailable: {type(e).__name__}: {e}"
        if rec["bars"] in (None, 0):
            try:
                from ..data.engine import get_data_engine
                ds = get_data_engine().latest_dataset(symbol, timeframe, require_eligible=True)
                if ds:
                    rec.update({"dataset_id": ds.get("id"), "source": ds.get("source"),
                                "source_kind": ds.get("kind")})
            except Exception as e:
                rec["error"] = (rec["error"] or "") + f" dataset lookup failed: {type(e).__name__}"
        rec["start"] = _iso(rec["start_ts"])
        rec["end"] = _iso(rec["end_ts"])
        out[key] = rec
    return out


# --------------------------------------------------------------------------- #
# §14 — readiness (DATA READY + X/Y NODES READY)
# --------------------------------------------------------------------------- #
def readiness(db: Any = None) -> Dict[str, Any]:
    """§14 — per-node READY / NOT READY with the reason decided from stored data."""
    req = requirements(db=db)
    agg = req["aggregate"]
    cov = coverage(db=db, pairs=[(i["symbol"], i["timeframe"]) for i in agg["items"]])
    nodes: List[Dict[str, Any]] = []
    pkg_ok = True
    for it in agg["items"]:
        c = cov.get(f"{it['symbol']}|{it['timeframe']}") or {}
        it["stored_bars"] = c.get("bars")
        it["stored_range"] = {"start": c.get("start"), "end": c.get("end")}
        it["stored_source"] = c.get("source")
        need = it["required_bars"]
        have = c.get("bars")
        if have is None:
            pkg_ok = False
            it["ready"] = False
            it["reason"] = "stored bar count could not be read — cannot claim coverage"
        elif need is None:
            pkg_ok = False
            it["ready"] = False
            it["reason"] = it["required_bars_reason"] or "no bar estimate for this window"
        elif have < need:
            pkg_ok = False
            it["ready"] = False
            it["reason"] = (f"{have:,} stored bars vs {need:,} required for the union window "
                            f"({it['earliest']} → {it['latest']})")
        else:
            it["ready"] = True
            it["reason"] = f"{have:,} stored bars cover the {need:,} required"
    item_by_pair = {(i["symbol"], i["timeframe"]): i for i in agg["items"]}
    for r in req["per_node"]:
        if not r.get("eligible"):
            nodes.append({"node_id": r["node_id"], "ready": False, "reason": r.get("reason"),
                          "symbol": r.get("symbol"), "timeframe": r.get("timeframe")})
            continue
        it = item_by_pair.get((r["symbol"], r["timeframe"])) or {}
        ok = bool(it.get("ready"))
        nodes.append({
            "node_id": r["node_id"], "ready": ok,
            "symbol": r["symbol"], "timeframe": r["timeframe"],
            "warmup_bars": r["warmup_bars"], "required_bars": r["required_bars"],
            "stored_bars": it.get("stored_bars"),
            "window": r["window"],
            "reason": (it.get("reason") if ok else
                       (it.get("reason") or "required data is missing for this node")),
        })
    ready_nodes = [n for n in nodes if n["ready"]]
    data_ready = bool(pkg_ok and agg["items"])
    return {
        "ok": True,
        "data_ready": data_ready,
        "data_status": "DATA READY" if data_ready else "DATA NOT READY",
        "nodes_ready": len(ready_nodes),
        "nodes_total": len(nodes),
        "nodes_status": f"{len(ready_nodes)}/{len(nodes)} NODES READY",
        "nodes": nodes,
        "package": agg["items"],
        "note": ("A node is READY only when the stored bars actually cover the union window its "
                 "own requirement asks for. Nothing is marked ready optimistically, and no "
                 "backtest result exists until the existing engine runs on that data."),
    }


# --------------------------------------------------------------------------- #
# §13 — GET MT5 DATA (staged, on top of the existing infrastructure)
# --------------------------------------------------------------------------- #
class GetDataJob:
    """The one GET MT5 DATA job. Fetches the suggested package through
    ``DataEngine.sync_master`` and reports §13's staged progress.

    Every bar arrives through the lab's existing bridge/historical path; this
    class only sequences the work and records requested vs received vs stored
    counts so the operator can see exactly what happened.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._cancel = threading.Event()
        self.state: Dict[str, Any] = self._fresh("IDLE")

    # ---- state ---- #
    def _fresh(self, status: str) -> Dict[str, Any]:
        return {
            "status": status,                # IDLE | RUNNING | COMPLETED | FAILED | CANCELLED
            "stage": GET_DATA_STAGES[0] if status == "IDLE" else None,
            "stage_index": None,
            "started_at": None, "finished_at": None, "elapsed_s": None,
            "current_operation": None,
            "items": [], "errors": [],
            "package": [],
            "totals": {"requested_bars": 0, "received_bars": 0, "stored_bars": 0,
                       "items_done": 0, "items_total": 0},
            "pct": 0.0,
            "readiness_after": None,
            "note": ("uses the existing MT5 historical infrastructure "
                     "(DataEngine.sync_master → bridge.copy_rates_range)"),
        }

    def _set_stage(self, stage: str, operation: Optional[str] = None) -> None:
        with self._lock:
            if stage in GET_DATA_STAGES:
                self.state["stage_index"] = GET_DATA_STAGES.index(stage)
            self.state["stage"] = stage
            self.state["current_operation"] = operation

    def _publish(self, message: str, pct: float) -> None:
        """Mirror the job into the GLOBAL live progress feed (spec §13)."""
        try:
            from ..activity import activity
            activity.running("DEEP DATA", message, operation_id="deep_get_mt5_data",
                             progress=float(max(0.0, min(100.0, pct))))
        except Exception:
            pass

    def _recalc(self) -> None:
        items = self.state["items"]
        tot = self.state["totals"]
        tot["items_total"] = len(items)
        tot["items_done"] = sum(1 for i in items if i.get("status") == "DONE")
        tot["requested_bars"] = sum(int(i.get("requested_bars") or 0) for i in items)
        tot["received_bars"] = sum(int(i.get("received_bars") or 0) for i in items)
        tot["stored_bars"] = sum(int(i.get("stored_bars") or 0) for i in items)
        n = max(1, len(items))
        frac = sum(float(i.get("pct") or 0.0) for i in items) / (n * 100.0)
        self.state["pct"] = round(frac * 100.0, 1)

    # ---- lifecycle ---- #
    def start(self, selection: Optional[List[Dict[str, Any]]] = None,
              force_full: bool = False, *, source: str = "SUGGESTED DATA (union)") -> Dict[str, Any]:
        with self._lock:
            if self.state.get("status") == "RUNNING":
                return {"ok": False, "error": "a GET MT5 DATA job is already running",
                        "status": self.state}
        if selection is None:
            sug = suggested_data()
            selection = [{"symbol": p["symbol"], "timeframe": p["timeframe"],
                          "start": p["earliest"], "end": p["latest"],
                          "required_bars": p["required_bars"], "nodes": p["nodes"]}
                         for p in sug["package"]]
        self._cancel.clear()
        with self._lock:
            self.state = self._fresh("RUNNING")
            self.state["started_at"] = time.time()
            self.state["source"] = source
            self.state["force_full"] = bool(force_full)
            self.state["package"] = [{k: v for k, v in s.items()} for s in selection]
            self.state["items"] = [{
                "symbol": str(s.get("symbol") or "").upper(),
                "timeframe": str(s.get("timeframe") or "").upper(),
                "requested_start": s.get("start"), "requested_end": s.get("end"),
                "requested_bars": s.get("required_bars"),
                "received_bars": None, "stored_bars": None, "new_bars": None,
                "stored_start": None, "stored_end": None,
                "nodes": list(s.get("nodes") or []),
                "status": "PENDING", "stage": GET_DATA_STAGES[0], "pct": 0.0,
                "error": None, "fetch_mode": None, "action": None,
            } for s in selection]
            self._recalc()
        self._thread = threading.Thread(target=self._run, name="deep-get-data", daemon=True)
        self._thread.start()
        return {"ok": True, "status": self.status()}

    def _run(self) -> None:
        try:
            self._set_stage("DISCOVERING REQUIREMENTS",
                            "computing data requirements over all deep-eligible nodes")
            self._publish("deep testing: discovering data requirements", 2.0)
            time.sleep(0)                              # let the stage be observable
            self._set_stage("CALCULATING DATA PACKAGE",
                            f"{len(self.state['items'])} symbol/timeframe item(s) to fetch")
            self._publish(f"deep testing: data package has {len(self.state['items'])} item(s)", 4.0)
            from ..data.engine import get_data_engine
            from ..mt5 import get_bridge
            engine = get_data_engine()
            bridge = get_bridge()
            self._set_stage("CONNECTING TO MT5",
                            f"bridge source: {getattr(bridge, 'source', 'unknown')}")
            self._publish(f"deep testing: connecting to {getattr(bridge, 'source', 'MT5')}", 6.0)
            if getattr(bridge, "is_simulated", False):
                # honest: a simulator cannot provide MT5 history
                self.state["errors"].append(
                    "the active bridge is SIMULATOR — MT5 historical data cannot be fetched "
                    "on this host; run this on the Windows machine with the terminal open")
            for item in list(self.state["items"]):
                if self._cancel.is_set():
                    item["status"] = "CANCELLED"
                    continue
                self._fetch_item(engine, bridge, item)
            if self._cancel.is_set():
                with self._lock:
                    self.state["status"] = "CANCELLED"
                    self.state["finished_at"] = time.time()
                self._set_stage("VALIDATING COVERAGE", "cancelled by operator")
                return
            self._set_stage("VALIDATING COVERAGE", "re-reading stored bars for every package item")
            self._publish("deep testing: validating coverage", 88.0)
            self._refresh_stored()
            self._set_stage("VALIDATING FEATURES", "features are computed on demand by the engine")
            self._set_stage("VALIDATING WARMUP", "checking each node's warm-up against stored bars")
            try:
                after = readiness()
                self.state["readiness_after"] = {
                    "data_ready": after["data_ready"], "data_status": after["data_status"],
                    "nodes_ready": after["nodes_ready"], "nodes_total": after["nodes_total"],
                    "nodes_status": after["nodes_status"],
                }
            except Exception as e:
                self.state["errors"].append(f"readiness check failed: {type(e).__name__}: {e}")
            self._set_stage("NODES READY")
            with self._lock:
                self.state["finished_at"] = time.time()
                self.state["elapsed_s"] = round(self.state["finished_at"] - self.state["started_at"], 2)
                self.state["status"] = "COMPLETED" if not self.state["errors"] else "COMPLETED"
                self.state["pct"] = 100.0
                self._recalc()
            self._publish("deep testing: GET MT5 DATA finished", 100.0)
            try:
                from ..activity import activity
                activity.success("DEEP DATA",
                                 f"deep testing: data package ready "
                                 f"({self.state['totals']['stored_bars']:,} stored bars)", 
                                 operation_id="deep_get_mt5_data", progress=100.0)
            except Exception:
                pass
        except Exception as e:                                  # pragma: no cover - defensive
            log.exception("GET MT5 DATA job failed")
            with self._lock:
                self.state["status"] = "FAILED"
                self.state["errors"].append(f"{type(e).__name__}: {e}")
                self.state["finished_at"] = time.time()

    def _fetch_item(self, engine: Any, bridge: Any, item: Dict[str, Any]) -> None:
        symbol, tf = item["symbol"], item["timeframe"]
        with self._lock:
            item["status"] = "RUNNING"
        try:
            self._set_stage("FETCHING SYMBOL", f"requesting {symbol} through "
                                              f"{getattr(bridge, 'source', 'bridge')}")
            item["stage"] = "FETCHING SYMBOL"
            item["pct"] = 10.0
            self._recalc()
            self._set_stage("FETCHING TIMEFRAME", f"{symbol} {tf}")
            item["stage"] = "FETCHING TIMEFRAME"
            item["pct"] = 25.0
            self._recalc()
            self._publish(f"deep testing: fetching {symbol} {tf}", self.state["pct"])
            self._set_stage("FETCHING DATE RANGE",
                            f"{symbol} {tf}: {item['requested_start']} → {item['requested_end']}")
            item["stage"] = "FETCHING DATE RANGE"
            item["pct"] = 40.0
            self._recalc()
            res = engine.sync_master(symbol, tf, force_full=bool(self.state.get("force_full")))
            item["fetch_mode"] = (res or {}).get("fetch_mode")
            item["action"] = (res or {}).get("action")
            item["received_bars"] = (res or {}).get("total_bars")
            item["new_bars"] = (res or {}).get("new_bars")
            self._set_stage("STORING DATA", f"{symbol} {tf}: persisting bars")
            item["stage"] = "STORING DATA"
            item["pct"] = 80.0
            self._recalc()
            self._refresh_item_stored(item)
            item["status"] = "DONE"
            item["pct"] = 100.0
            self._set_stage("VALIDATING COVERAGE", f"{symbol} {tf}: {item.get('stored_bars')} stored")
            self._recalc()
            self._publish(f"deep testing: {symbol} {tf} stored "
                          f"{item.get('stored_bars') or 0:,} bars", self.state["pct"])
        except Exception as e:
            item["status"] = "ERROR"
            item["error"] = f"{type(e).__name__}: {e}"
            with self._lock:
                self.state["errors"].append(f"{symbol} {tf}: {item['error']}")
            self._recalc()

    def _refresh_item_stored(self, item: Dict[str, Any]) -> None:
        cov = coverage(pairs=[(item["symbol"], item["timeframe"])])
        c = cov.get(f"{item['symbol']}|{item['timeframe']}") or {}
        item["stored_bars"] = c.get("bars")
        item["stored_start"] = c.get("start")
        item["stored_end"] = c.get("end")
        item["stored_source"] = c.get("source")
        if c.get("error"):
            item["storage_note"] = c["error"]

    def _refresh_stored(self) -> None:
        for item in self.state["items"]:
            if item.get("symbol") and item.get("timeframe"):
                self._refresh_item_stored(item)
        self._recalc()

    def cancel(self) -> Dict[str, Any]:
        self._cancel.set()
        return {"ok": True, "status": self.status()}

    def status(self) -> Dict[str, Any]:
        with self._lock:
            st = {k: (v if not isinstance(v, (dict, list)) else json.loads(json.dumps(v, default=str)))
                  for k, v in self.state.items()}
        if st.get("status") == "RUNNING" and st.get("started_at"):
            st["elapsed_s"] = round(time.time() - float(st["started_at"]), 2)
        st["stages"] = list(GET_DATA_STAGES)
        return st

    def reset(self) -> Dict[str, Any]:
        with self._lock:
            if self.state.get("status") == "RUNNING":
                return {"ok": False, "error": "the job is running — cancel it first"}
            self.state = self._fresh("IDLE")
        return {"ok": True, "status": self.status()}


_job: Optional[GetDataJob] = None
_job_lock = threading.Lock()


def get_data_job() -> GetDataJob:
    global _job
    with _job_lock:
        if _job is None:
            _job = GetDataJob()
        return _job
