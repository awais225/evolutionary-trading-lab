"""V6.6 §8/§12 — DeepTest run orchestration and persistent scenario snapshots.

A DeepTest run executes the node's ACTUAL strategy through the lab's historical
Backtester (the same engine that produced the generation metrics) on validated,
persisted candles — never mock or generic logic, never any broker order, never
touching the live execution worker. Every run is persisted as an immutable
scenario snapshot (requested vs actual dates, bars, dataset fingerprint,
coverage status + gaps, settings, assumptions, metrics, trades, timestamps,
engine versions). Later edits never alter a stored scenario.

Statuses: QUEUED | FETCHING | VALIDATING | RUNNING | COMPLETED | PARTIAL |
CANCELLED | FAILED. PARTIAL = COMPLETED results on data the operator accepted
under §9 with incomplete coverage (always marked; never labelled complete).

Statistics precedence (§12): explicit preferred DeepTest -> latest valid
DeepTest -> generation stats -> N/A. A failed/cancelled/invalid run never
displaces a valid one; an explicit preference is never silently replaced.
"""
from __future__ import annotations

from ..speed import profiled
import hashlib
import json
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from . import analytics as A
from . import store as S
from .coverage import check_period, pair_coverage, tf_seconds
from .periods import explicit_period, resolve_period

VALID_STATUSES = ("QUEUED", "FETCHING", "VALIDATING", "RUNNING",
                  "COMPLETED", "PARTIAL", "CANCELLED", "FAILED")
TERMINAL = ("COMPLETED", "PARTIAL", "CANCELLED", "FAILED")
VALID_FOR_PREFERENCE = ("COMPLETED", "PARTIAL")

from .parallel import ScenarioExecutor
_executor = ScenarioExecutor()
_CANCELS: set = set()
_LOCK = threading.Lock()
_REQUEST_LOCK=threading.RLock()
_FUTURES={}


def settings_fingerprint(settings: Dict[str, Any]) -> str:
    # Numeric representations normalize equally; names/timestamps are not test inputs.
    def clean(value):
        if isinstance(value,dict):return {k:clean(v) for k,v in sorted(value.items()) if k not in ('name','assigned_at','created_at','updated_at','provenance','idempotency_key','notes','updated_by')}
        if isinstance(value,list):return [clean(v) for v in value]
        if isinstance(value,(float,int)) and not isinstance(value,bool):return float(value)
        return value
    return hashlib.sha256(json.dumps(clean(settings),sort_keys=True,default=str).encode()).hexdigest()[:16]


NODE_SETTING_FIELDS = ("risk_mode", "risk_pct", "risk_amount", "max_trades",
                       "sl_offset_pips", "tp_offset_pips")


def _validate_node_settings(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate the per-node DeepTest settings (never touching live config)."""
    errors = []
    values: Dict[str, Any] = {}
    rm = str((payload or {}).get("risk_mode") or "").upper()
    if rm:
        if rm not in ("PERCENT", "AMOUNT"):
            errors.append({"field": "risk_mode", "error": "risk_mode must be PERCENT or AMOUNT"})
        else:
            values["risk_mode"] = rm
    for key, lo, hi in (("risk_pct", 0.01, 100.0), ("risk_amount", 0.01, 10_000_000.0),
                        ("sl_offset_pips", -10_000.0, 10_000.0),
                        ("tp_offset_pips", -10_000.0, 10_000.0)):
        if key in (payload or {}) and payload.get(key) is not None:
            try:
                v = float(payload.get(key))
            except (TypeError, ValueError):
                errors.append({"field": key, "error": f"{key} must be a number"})
                continue
            if key in ("risk_pct", "risk_amount") and not (lo <= v <= hi):
                errors.append({"field": key, "error": f"{key} must be between {lo:g} and {hi:g}"})
                continue
            values[key] = v
    if "max_trades" in (payload or {}) and payload.get("max_trades") is not None:
        try:
            mt = int(payload.get("max_trades"))
            if mt < 0:
                raise ValueError
            values["max_trades"] = mt
        except (TypeError, ValueError):
            errors.append({"field": "max_trades", "error": "max_trades must be a non-negative "
                                                            "whole number (0 = unlimited)"})
    return {"ok": not errors, "values": values, "errors": errors}


# --------------------------------------------------------------------------- #
# §9 preflight
# --------------------------------------------------------------------------- #
def preflight(db: Any, *, node_id: int, duration_key: str = "",
              requested_start: Optional[float] = None,
              requested_end: Optional[float] = None,
              settings: Optional[Dict[str, Any]] = None,
              node_settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Resolve the period, inspect coverage, and decide whether the §9
    incomplete-history confirmation is required. Never runs anything.

    The confirmation fingerprint binds EXACTLY these settings (global + the
    per-node experimental ones) — so both ``preflight`` and ``start_run`` must
    see the same dict for a remembered YES to carry over.
    """
    settings = {**S.get_settings(db), **(settings or {})}
    node = _load_node(db, node_id)
    node_cfg = S.get_node_config(db, node_id)
    effective_ns={**(node_cfg.get('node_settings') or {}),**settings.get('node_settings',{}),**(node_settings or {})}
    for key in ('sl_offset_pips','tp_offset_pips'):
        if effective_ns.get(key) is None:effective_ns[key]=(node.get('config') or {}).get(key) or 0.0
    vns = _validate_node_settings(effective_ns)
    if not vns['ok']:return {'ok':False,'error':'invalid node settings','details':vns['errors']}
    vns['values']['max_trades'] = None
    settings['node_settings']=vns['values']
    settings['execution_policy'] = 'data_bounded_v102'
    if node_cfg:
        node_cfg = {**node_cfg, 'node_settings': {**(node_cfg.get('node_settings') or {}), 'max_trades':None}}
    if node_cfg:settings['node_config']=node_cfg
    from .filter_scenarios import assignment
    assigned=assignment(db,node_id,'deep')
    if assigned:settings['filter_scenario_snapshot']={'rules':assigned['rules']}
    else:settings.pop('filter_scenario_snapshot',None)
    symbol = node["symbol"]
    timeframe = node["timeframe"]
    if duration_key:
        period = resolve_period(duration_key, end_ts=requested_end)
    else:
        if requested_start is None or requested_end is None:
            return {"ok": False, "error": "duration_key or explicit start/end is required"}
        period = explicit_period(requested_start, requested_end)

    chk = check_period(symbol, timeframe,
                       period["start_ts"], period["end_ts"],
                       node_id=node_id, duration_key=period["duration_key"],
                       settings_fingerprint=settings_fingerprint(settings))
    from ..data import history as H
    if H.canonical_profile(symbol,timeframe):
        from .coverage import _mstore
        from ..data.history_range import coverage as observed
        persisted=observed(_mstore(symbol,timeframe),period['start_ts'],__import__('math').nextafter(period['end_ts'],float('-inf')),tf_seconds(timeframe))
        chk['effective_population']={k:persisted[k] for k in ('persisted_in_range','persisted_range_first','persisted_range_last')}
        chk['actual']={**chk.get('actual',{}),'bars':persisted['persisted_in_range'],'first_ts':persisted['persisted_range_first'],'last_ts':persisted['persisted_range_last']}
        if not persisted['persisted_in_range']:chk['coverage_status']='NONE'
        elif persisted['status']=='PARTIAL':chk['coverage_status']='PARTIAL'
    needs_confirmation = chk["coverage_status"] != "COMPLETE"
    if chk["coverage_status"] == "NONE":
        return {"ok": False, "error": ("no historical candles exist for "
                                       f"{symbol}/{timeframe} — use GET MT5 DATA to fetch "
                                       "history or MANUALLY LOAD DATA to import a CSV"),
                "coverage": chk, "period": period}

    saved = S.get_confirmation(db, chk["confirmation_fingerprint"])
    decided_yes = bool(saved and saved.get("decision") == "YES_PROCEED")
    return {
        "ok": True,
        "period": period,
        "coverage": chk,
        "settings": settings,
        "settings_fingerprint": settings_fingerprint(settings),
        "needs_confirmation": needs_confirmation,
        "confirmation_required":needs_confirmation and not decided_yes,
        "coverage_classification":"PARTIAL" if needs_confirmation else "FULL",
        "confirmation_valid_for_this_fingerprint": decided_yes,
        "may_proceed": (not needs_confirmation) or decided_yes,
    }


def record_decision(db: Any, *, confirmation_fingerprint: str, decision: str,
                    node_id: Any = None) -> Dict[str, Any]:
    """§9's three buttons. ``decision`` ∈ YES_PROCEED | NO_CANCEL | LOAD_CSV."""
    if decision not in ("YES_PROCEED", "NO_CANCEL", "LOAD_CSV"):
        return {"ok": False, "error": "decision must be YES_PROCEED, NO_CANCEL or LOAD_CSV"}
    rec = S.save_confirmation(db, confirmation_fingerprint, decision)
    return {"ok": True, "decision": rec.get("decision"), "fingerprint": confirmation_fingerprint}


# --------------------------------------------------------------------------- #
# run lifecycle
# --------------------------------------------------------------------------- #
def effective_signature(*, node_id: int, genome_hash: str, config_hash: str,
                        symbol: str, timeframe: str, duration_key: str,
                        requested_start, requested_end,
                        settings: Dict[str, Any], node_settings: Dict[str, Any],
                        dataset_fingerprint, engine_version: str,
                        node_config_hash: str = "") -> str:
    """V6.7 §7.1 — the STABLE identity of a DeepTest scenario.

    Hashes exactly what makes two runs "the same test": the node + its strategy
    identity (genome/config fingerprints), symbol + timeframe, the requested
    range + timerange choice, the scenario/scheduling config (settings +
    node-level risk/offset settings), the dataset fingerprint actually behind
    the run and the engine version. Unchanged reruns reuse the existing valid
    scenario; any material change produces a different signature and therefore
    a new scenario identity. It never contains a timestamp or a scenario id, so
    recomputing it over identical inputs is deterministic.
    """
    payload = {
        "node_id": int(node_id),
        "genome_hash": genome_hash,
        "config_hash": config_hash,
        "symbol": str(symbol or "").upper(),
        "timeframe": str(timeframe or "").upper(),
        "duration_key": str(duration_key or ""),
        "requested_start": requested_start,
        "requested_end": requested_end,
        "settings_fingerprint": settings_fingerprint(settings or {}),
        "node_settings": {k: (node_settings or {}).get(k)
                          for k in sorted(NODE_SETTING_FIELDS)},
        "dataset_fingerprint": dataset_fingerprint,
        "engine_version": engine_version,
        "node_config_hash": node_config_hash,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:24]


def _reuse_scenario(db: Any, node_id: int, signature: str, *, require_valid: bool = False) -> Optional[Dict[str, Any]]:
    """An earlier valid scenario with this exact effective signature, if any."""
    rows = db.q("""SELECT run_id, status FROM deeptest_scenarios
                   WHERE node_id=? AND signature=?
                   ORDER BY rowid DESC""", (int(node_id), str(signature))) or []
    for r in rows:
        if (r.get("status") or "") in ("COMPLETED", "PARTIAL", "RUNNING", "QUEUED",
                                       "FETCHING", "VALIDATING"):
            scenario = S.get_scenario(db, r["run_id"])
            if require_valid and r['status'] in ('COMPLETED','PARTIAL') and not (scenario.get('analytics') or {}).get('summary'):
                continue
            return scenario
    return None


def _scenario_label(db: Any, run_id: str) -> str:
    """Human label 'Scenario N' = the run's position in its node's history."""
    sc = S.get_scenario(db, run_id)
    if not sc:
        return "Scenario"
    rows = db.q("""SELECT run_id FROM deeptest_scenarios WHERE node_id=?
                   ORDER BY rowid""", (sc.get("node_id"),)) or []
    for i, r in enumerate(rows, 1):
        if r["run_id"] == run_id:
            return f"Scenario {i}"
    return "Scenario"


def start_run(db: Any,payload: Dict[str,Any])->Dict[str,Any]:
    # Cross-process keyed lock + UNIQUE claim; old duplicate history is retained.
    from contextlib import nullcontext
    from pathlib import Path
    from ..data.master import _store_lock
    path=getattr(db,'path',None)
    with _REQUEST_LOCK, (_store_lock(Path(path).resolve().parent/'.deep-requests') if path is not None and str(path) != ':memory:' else nullcontext()):
        S.ensure_schema(db)
        db.x('CREATE TABLE IF NOT EXISTS deeptest_claims (signature TEXT PRIMARY KEY,run_id TEXT NOT NULL UNIQUE,owner_pid INTEGER NOT NULL)')
        return _start_run_locked(db,payload)


def _start_run_locked(db: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate + queue a DeepTest run. Returns the QUEUED scenario row.

    Idempotency: material signatures, not caller request keys, select reuse.
    A changed input is never replayed merely because a request key was reused. V6.7 §7.1: an identical rerun
    (same effective signature) REUSES the existing valid scenario — "Already
    DeepTested — see Scenario N" + an INSPECT action — and never reruns the
    engine or overwrites the scenario's historical metrics/trades.
    """
    try:
        node_id = int(payload.get("node_id"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "node_id is required"}

    if payload.get('force_rerun'):
        return {'ok':False,'error':'force_rerun is not supported; explicitly retry only failed/interrupted scenarios'}
    idem = payload.get("idempotency_key")
    node = _load_node(db, node_id)
    for field in ('symbol','timeframe'):
        if payload.get(field) and str(payload[field]).upper() != str(node[field]).upper():
            return {'ok':False,'error':f"Requested {field} does not match selected stored node; select a node with the requested {field}"}
    settings = payload.get("settings") or S.get_settings(db)
    v = S.validate_settings(settings)
    if payload.get("settings") and not v["ok"]:
        return {"ok": False, "error": "invalid settings", "details": v["errors"]}
    settings = {**S.get_settings(db), **(payload.get("settings") or {})}
    # per-node experimental settings (Deep Testing only — the LIVE TESTING
    # config is never mutated). Missing fields inherit the node's own semantics
    # as read-only defaults inside the engine call.
    node_cfg = S.get_node_config(db, node_id)
    node_settings = {**(node_cfg.get('node_settings') or {}), **(payload.get("node_settings") or {})}
    vns = _validate_node_settings(node_settings)
    if not vns["ok"]:
        return {"ok": False, "error": "invalid node settings", "details": vns["errors"]}
    settings["node_settings"] = vns["values"]

    pf = preflight(db, node_id=node_id,
                   duration_key=payload.get("duration_key") or "",
                   requested_start=payload.get("requested_start"),
                   requested_end=payload.get("requested_end"),
                   settings=settings,
                   node_settings=None)     # already merged above — same dict
    if not pf.get("ok"):
        return pf
    if pf["needs_confirmation"] and not pf.get("may_proceed"):
        return {"ok": False, "needs_confirmation": True,
                "error": "the requested history is incomplete — confirm the coverage "
                         "warning before this run may start",
                "confirmation": pf["coverage"]}
    # a remembered YES binds to EXACTLY this fingerprint
    if pf["coverage"]["coverage_status"] != "COMPLETE":
        saved = S.get_confirmation(db, pf["coverage"]["confirmation_fingerprint"])
        if not (saved and saved.get("decision") == "YES_PROCEED"):
            return {"ok": False, "needs_confirmation": True,
                    "error": "the stored confirmation does not match this request "
                             "(data/period/settings changed) — confirm again",
                    "confirmation": pf["coverage"]}

    settings=pf.get("settings") or settings
    period = pf["period"]
    ghash = _genome_hash(node.get("genome"))
    cfg_hash = _execution_config_hash()
    # V6.7 §7.2/§7.3 — the SCENARIO popup's saved config (DeepTesting-only
    # scheduling + scenario settings) is part of the scenario identity and is
    # never written to the live execution settings.
    node_cfg = settings.get('node_config') or {}
    cov = pf.get('coverage') or {}
    effective_period=cov.get('effective_population') or {}
    signature = effective_signature(
        node_id=node_id, genome_hash=ghash, config_hash=cfg_hash,
        symbol=node.get("symbol") or "", timeframe=node.get("timeframe") or "",
        duration_key=period.get("duration_key") or "",
        requested_start=(effective_period.get('persisted_range_first') if effective_period and payload.get('duration_key') and payload.get('requested_end') is None else period.get('start_ts')),
        requested_end=(effective_period.get('persisted_range_last') if effective_period and payload.get('duration_key') and payload.get('requested_end') is None else period.get('end_ts')),
        settings=settings, node_settings=settings.get("node_settings") or {},
        dataset_fingerprint=(cov.get("dataset_fingerprint")
                             or cov.get("fingerprint")
                             or cov.get("confirmation_fingerprint")),
        engine_version=_material_engine_version(),
        node_config_hash=settings_fingerprint(node_cfg or {}),
    )
    # V6.7 §7.1 — identical effective configuration + range ⇒ reuse. The
    # engine is NOT rerun and the existing scenario's historical metrics and
    # trades are never overwritten.
    existing = _reuse_scenario(db, node_id, signature, require_valid=True)
    prior=db.one('SELECT run_id,status FROM deeptest_scenarios WHERE node_id=? AND signature=? ORDER BY rowid DESC LIMIT 1',(node_id,signature))
    if existing and existing['status'] not in ('COMPLETED','PARTIAL'):
        import os
        claim=db.one('SELECT * FROM deeptest_claims WHERE signature=?',(signature,))
        owner_alive=False
        if claim:
            from .runtime import alive
            owner_alive=alive(claim['owner_pid'])
        if not owner_alive:
            if not payload.get('retry_incomplete'):
                return {'ok':False,'retry_required':True,'existing_run_id':existing['run_id'],'error':'prior in-flight owner is unavailable; explicitly retry after reviewing the retained run'}
            existing=None
    if prior and existing is None and not (payload.get('retry_incomplete') or payload.get('force_rerun')):
        return {'ok':False,'retry_required':True,'existing_run_id':prior['run_id'],'error':'matching previous run is incomplete/failed; explicit retry is required; history is preserved'}
    if existing is not None:
        label = _scenario_label(db, existing.get("run_id"))
        return {
            "ok": True,
            "already_deep_tested": True,
            "idempotent_replay": bool(idem),
            "idempotency_basis": "material_signature",
            "duplicate": True,
            "existing_run_id": existing.get("run_id"),
            "scenario": existing,
            "label": label,
            "message": f"Already DeepTested — see {label}",
            "inspect": {"run_id": existing.get("run_id")},
            "engine_rerun": False,
        }
    run_id = S.new_run_id()
    S.insert_scenario(db, {
        "run_id": run_id,
        "node_id": node_id,
        "status": "QUEUED",
        "signature": signature,
        "idempotency_key": None,
        "duration_key": period["duration_key"],
        "requested_start": period["start_ts"],
        "requested_end": period["end_ts"],
        "symbol": node["symbol"],
        "timeframe": node["timeframe"],
        "coverage_status": "PENDING",
        "dataset_fingerprint": cov.get("dataset_fingerprint") or cov.get("fingerprint"),
        "settings": settings,
        "genome_hash": ghash,
        "config_hash": cfg_hash,
        "confirmed_fingerprint": pf["coverage"]["confirmation_fingerprint"]
        if pf["coverage"]["coverage_status"] != "COMPLETE" else None,
        "notes": ("fetched by engine" if False else None),
        "provenance": {"requested_period": period,
                       "requested_by": "deeptest-api",
                       "run_policy": "historical simulation only — no broker orders"},
    })
    import os
    db.x('INSERT INTO deeptest_claims VALUES (?,?,?) ON CONFLICT(signature) DO UPDATE SET run_id=excluded.run_id,owner_pid=excluded.owner_pid',(signature,run_id,os.getpid()))
    from .runtime import CALLBACKS
    if payload.get('_progress_callback'):CALLBACKS[run_id]=payload['_progress_callback']
    try:
        future = _executor.submit(_execute, run_id)
        with _LOCK:_FUTURES[run_id]=future
        if hasattr(future,'add_done_callback'):
            def queue_finished(future):
                with _LOCK:_FUTURES.pop(run_id,None)
                if future.cancelled():
                    CALLBACKS.pop(run_id,None)
                    S.update_scenario(db,run_id,status='CANCELLED',finished_at=time.time(),
                                      error='Not started: backend historical executor shutdown')
            future.add_done_callback(queue_finished)
    except Exception:
        CALLBACKS.pop(run_id,None)
        S.update_scenario(db,run_id,status='FAILED',error='could not queue historical worker')
        raise
    return {"ok": True, "run_id": run_id, "scenario": S.get_scenario(db, run_id)}


@profiled('deeptest.cancel_request', hot=False)
def cancel_run(db: Any, run_id: str) -> Dict[str, Any]:
    sc = S.get_scenario(db, run_id)
    if not sc:
        return {"ok": False, "error": f"unknown run {run_id}"}
    if sc["status"] in TERMINAL:
        return {"ok": False, "error": f"run already {sc['status']}"}
    with _LOCK:
        sc = S.get_scenario(db, run_id)
        if sc['status'] in TERMINAL:return {'ok':False,'error':f"run already {sc['status']}"}
        _CANCELS.add(run_id)  # wake persistence before waiting for its writer lock
        if not S.cancel_if_active(db,run_id):
            _CANCELS.discard(run_id)
            return {'ok':False,'error':'run reached a terminal state before cancellation'}
    return {"ok": True, "run_id": run_id, "status": "CANCELLED"}


@profiled('deeptest.run', hot=False, run_arg=0)
def _execute(run_id: str) -> None:
    from .runtime import Session, CURRENT, CALLBACKS, exception
    from ..db.database import get_db
    token = None
    try:
        with Session(run_id, CALLBACKS.pop(run_id, None)) as session:
            token = CURRENT.set({'session': session})
            from ..performance import capture
            with capture('deeptest'):_execute_body(run_id)
            outcome = S.get_scenario(get_db(),run_id)
            if outcome and outcome['status'] in ('FAILED','CANCELLED'):
                session.update(outcome['status'],outcome.get('error') or 'Historical scenario cancelled',node_id=outcome.get('node_id'),error=outcome.get('error'))
    except Exception as exc:
        exception('job=%s phase=EXECUTION worker exception', run_id)
        S.update_scenario(get_db(), run_id, status='FAILED', finished_at=time.time(),
                          error=f'{type(exc).__name__}: {exc}'[:500], notes=traceback.format_exc()[-2000:])
    finally:
        CALLBACKS.pop(run_id, None)
        if token is not None:CURRENT.reset(token)
        with _LOCK:_CANCELS.discard(run_id)


def _execute_body(run_id: str) -> None:
    from ..db.database import get_db
    db = get_db()
    t0 = time.time()
    from .runtime import CURRENT, exception
    execution = CURRENT.get()
    session = execution['session']
    last_check = [0.0,False]
    def was_cancelled(force=False):
        if run_id in _CANCELS:return True
        now=time.monotonic()
        if force or now-last_check[0]>=.2:
            row=db.one('SELECT status FROM deeptest_scenarios WHERE run_id=?',(run_id,))
            last_check[:]=[now,bool(row and row['status']=='CANCELLED')]
        return last_check[1]
    execution['cancelled']=was_cancelled
    try:
        session.update('LOADING_DATA', 'Loading authoritative node/genome and data availability')
        sc = S.get_scenario(db, run_id)
        if not sc or sc["status"] in TERMINAL:
            return
        if was_cancelled():
            S.update_scenario(db, run_id, status="CANCELLED", finished_at=time.time())
            return
        node = _load_node(db, int(sc["node_id"]))
        settings = sc.get("settings") or S.get_settings(db)
        # Immutable historical schedule: never reread a subsequently edited live
        # or Deep configuration while executing a queued scenario.
        saved_schedule=(settings.get('node_config') or {}).get('schedule')
        if saved_schedule is not None:
            node={**node,'schedule':saved_schedule}
        symbol, timeframe = sc["symbol"], sc["timeframe"]

        # ---- VALIDATING: materialise the dataset view over the persisted store
        S.update_scenario(db, run_id, status="VALIDATING", started_at=t0)
        from ..data.master import get_master
        from ..mt5.factory import get_bridge
        mstore = get_master(symbol, timeframe, get_bridge().profile())
        if not mstore.rows:
            S.update_scenario(db, run_id, status="FAILED", finished_at=time.time(),
                              error=f"no validated candles are persisted for {symbol}/{timeframe} — "
                                    "use GET MT5 DATA or IMPORT CSV first (nothing was fabricated)")
            return
        from contextlib import nullcontext
        from ..data.master import _store_lock
        with (_store_lock(mstore.raw_dir) if getattr(mstore,'raw_dir',None) else nullcontext()):
            # Read authoritative bars in place. Do not materialize canonical
            # dataset/feature caches merely to execute a transient DeepTest.
            from ..data.engine import make_master_dataset_id
            from ..data import sessions
            from ..performance import measure
            from .parallel import reserve_memory
            with reserve_memory(source_rows=mstore.rows), measure('deeptest.materialization'):
                frame = mstore.read_range()
            if len(frame)!=mstore.rows:
                raise RuntimeError(f'Incomplete persisted history snapshot: expected {mstore.rows} bars, read {len(frame)}; refusing partial-file success')
            dataset_id = make_master_dataset_id(symbol, timeframe, mstore.profile_key, mstore.version)
            frame['session'] = sessions.session_labels(frame['ts'].to_numpy())
            from .parallel import worker_limit
            if worker_limit() > 1:
                import pyarrow.feather as feather
                view = session.path / 'market.arrow'
                feather.write_feather(frame, view, compression='uncompressed')
                view_note=f'system TEMP Arrow IPC ({view.stat().st_size} bytes)'
            else:
                view_note='memory; redundant serial TEMP encoding omitted'
            execution['frame'] = frame
            session.update('DATA_READY',f'Consistent single-timeframe view prepared in {view_note}',node_id=node['node_id'])
            cov = pair_coverage(symbol,timeframe)
            session.update('VALIDATING_COVERAGE', f'{symbol}/{timeframe}: {len(frame)} source bars, stored coverage inspected', node_id=node['node_id'])
            if sc.get('dataset_fingerprint') and sc['dataset_fingerprint']!=cov.get('fingerprint'):
                S.update_scenario(db,run_id,status='FAILED',finished_at=time.time(),error='Persisted history changed after queueing; review the new data and explicitly retry. No old-signature result was produced.')
                return
        req_start, req_end = float(sc["requested_start"]), float(sc["requested_end"])
        ts = frame["ts"].to_numpy()
        lo = int(__import__("numpy").searchsorted(ts,req_start,side="left")) if len(ts) else 0
        hi = int(__import__("numpy").searchsorted(ts,req_end,side="left")) if len(ts) else 0
        if hi - lo < 2:
            S.update_scenario(db, run_id, status="FAILED", finished_at=time.time(),
                              error="fewer than 2 validated bars in the requested range — "
                                    "refusing to backtest a near-empty window")
            return
        if was_cancelled():
            S.update_scenario(db, run_id, status="CANCELLED", finished_at=time.time())
            return

        if sc.get('config_hash') and sc['config_hash']!=_execution_config_hash():
            S.update_scenario(db,run_id,status='FAILED',finished_at=time.time(),error='Material historical engine configuration changed after queueing; review and explicitly retry. No mismatched-signature result was produced.')
            return
        # ---- RUNNING: the node's ACTUAL strategy through the historical engine
        S.update_scenario(db, run_id, status="RUNNING")
        result = _run_strategy(db, node, dataset_id, (lo, hi), settings, symbol, timeframe)
        if was_cancelled():
            S.update_scenario(db, run_id, status="CANCELLED", finished_at=time.time())
            return
        if not result.get("ok"):
            S.update_scenario(db, run_id, status="FAILED", finished_at=time.time(),
                              error=str(result.get("error"))[:500])
            return

        from ..speed import event
        event('deeptest','window_accounting',run_id=run_id,fields={'source_bars':len(frame),'eligible_bars':hi-lo,'processed_bars':result['metrics'].get('bars_processed',hi-lo),'excluded_before_window':lo,'excluded_after_window':len(frame)-hi,'requested_start_ts':req_start,'requested_end_ts':req_end,'actual_start_ts':float(ts[lo]),'actual_end_ts':float(ts[hi-1])})
        metrics = result["metrics"]
        trades = result["trades"]
        summary = A.summarize(trades, initial_capital=float(settings["total_capital"]))
        reconciliation = A.reconcile(trades, summary["summary"])

        # cov describes the immutable dataset snapshot captured before execution.
        gaps = (cov.get("missing_periods") or [])[:25]
        coverage_status = sc.get("provenance", {}) or {}
        coverage_state = "COMPLETE"
        if sc.get("confirmed_fingerprint"):
            coverage_state = "PARTIAL"

        ts_lo = float(ts[lo]); ts_hi = float(ts[hi - 1])
        assumptions = result["assumptions"]
        ns = (settings or {}).get("node_settings") or {}
        assumptions["capital_and_risk"] = {
            "total_capital": settings["total_capital"],
            "sl_risk_per_trade_pct": ns.get("risk_pct", settings["risk_pct"]),
            "risk_mode": ns.get("risk_mode", "PERCENT"),
            "risk_amount": ns.get("risk_amount") if ns.get("risk_mode") == "AMOUNT" else None,
            "risk_basis": ("percent of the scenario's simulated equity at trade entry "
                           "(equity starts at Total Capital; each trade is sized when it "
                           "opens, using the equity realised at that moment)"
                           if ns.get("risk_mode", "PERCENT") != "AMOUNT" else
                           "constant monetary risk per trade (money at risk if the SL is hit)"),
            "max_active_trades_per_node": settings["max_active_trades_per_node"],
            "max_active_trades_semantics": ("0 = unlimited concurrent simulated trades; "
                                            "N = at most N open at once (entries gated only — "
                                            "an exit is never forced early); 1 = one open "
                                            "simulated trade at a time"),
        }
        session.update("PERSISTING_RESULTS", "Persisting metrics and trades", node_id=node["node_id"])
        S.update_scenario(db, run_id,
                          status="RUNNING",
                          finished_at=time.time(),
                          actual_start=ts_lo, actual_end=ts_hi,
                          bars_used=int(hi - lo),
                          dataset_id=dataset_id,
                          dataset_fingerprint=cov.get("fingerprint"),
                          coverage_status=coverage_state,
                          gaps=gaps,
                          metrics=metrics,
                          analytics={**summary, "reconciliation": reconciliation},
                          provenance={
                              "requested_period": {"start_ts": req_start, "end_ts": req_end},
                              "actual_tested_range": {"start_ts": ts_lo, "end_ts": ts_hi,
                                                      "bars": int(hi - lo)},
                              "dataset": {"id": dataset_id,
                                          "fingerprint": cov.get("fingerprint"),
                                          "source": cov.get("source"),
                                          "broker": cov.get("broker"),
                                          "server": cov.get("server")},
                              "assumptions": assumptions,
                              "provisional": coverage_state == "PARTIAL",
                              "run_policy": "historical simulation only — no broker orders",
                          },
                          engine_versions=result["engine_versions"])
        S.insert_trades(db, run_id, trades, cancelled=was_cancelled)
        with _LOCK:
            if was_cancelled():
                S.update_scenario(db, run_id, status='CANCELLED', finished_at=time.time())
                return
            if not S.finish_if_running(db,run_id,status='PARTIAL' if coverage_state == 'PARTIAL' else 'COMPLETED'):
                return
        if coverage_state=='PARTIAL':event('deeptest','partial_coverage','WARN',run_id=run_id,fields={'operation':'confirmed_incomplete_coverage','eligible_bars':hi-lo,'trades':len(trades)})
        session.update('COMPLETED', f'Real metrics and {len(trades)} trades durably persisted ({coverage_state})', node_id=node['node_id'])
    except Exception as e:  # noqa: BLE001 — a failed run must persist its failure
        if was_cancelled():
            S.update_scenario(db, run_id, status='CANCELLED', finished_at=time.time())
            return
        exception('job=%s phase=%s execution failed', run_id, session.phase)
        session.update('FAILED', f'{type(e).__name__}: {e}'[:500])
        try:
            S.update_scenario(db, run_id, status="FAILED", finished_at=time.time(),
                              error=f"{type(e).__name__}: {e}"[:500],
                              notes=traceback.format_exc()[-2000:])
        except Exception:
            pass


def _run_strategy(db: Any, node: Dict[str, Any], dataset_id: str,
                  window: Tuple[int, int], settings: Dict[str, Any],
                  symbol: str, timeframe: str) -> Dict[str, Any]:
    """Build the BacktestRequest from the node's own genome + DeepTest settings."""
    from ..backtest.engine import BacktestRequest, run_backtest
    from ..versions import manifest as engine_manifest

    g = node.get("genome") or {}
    ns = (settings or {}).get("node_settings") or {}
    max_trades = None  # V10.2: eligible data, never an inherited per-node trade ceiling
    # offsets default to the node's own config values (read-only); an explicit
    # DeepTest setting overrides them FOR THIS SCENARIO ONLY
    gen_settings = node.get("config") or {}
    sl_off = float(ns.get("sl_offset_pips")
                   if ns.get("sl_offset_pips") is not None
                   else gen_settings.get("sl_offset_pips") or 0.0)
    tp_off = float(ns.get("tp_offset_pips")
                   if ns.get("tp_offset_pips") is not None
                   else gen_settings.get("tp_offset_pips") or 0.0)
    risk_mode = str(ns.get("risk_mode") or "PERCENT").upper()
    risk_amount = float(ns.get("risk_amount") or 0.0)
    risk_pct = float(ns.get("risk_pct") if ns.get("risk_pct") is not None
                     else settings["risk_pct"])

    req = BacktestRequest(
        genome=g,
        dataset_id=dataset_id,
        stage="oos",
        window=window,
        # the historical research defaults exactly: the engine applies its own
        # base spread/slippage/commission; mults stay 1.0 (no cost fudging)
        spread_mult=1.0,
        slippage_mult=1.0,
        commission_mult=1.0,
        max_trades=max_trades,
        schedule={**(node.get("schedule") or {}), **({'filter_rules':settings['filter_scenario_snapshot']['rules']} if settings.get('filter_scenario_snapshot') else {})} or None,
        conditions=node.get("conditions"),
        initial_balance=float(settings["total_capital"]),
        risk_per_trade_pct=(risk_pct if risk_mode == "PERCENT" else None),
        risk_mode=risk_mode,
        risk_amount=(risk_amount if risk_mode == "AMOUNT" else None),
        max_active_trades=int(settings["max_active_trades_per_node"]),
        sl_offset_pips=sl_off,
        tp_offset_pips=tp_off,
    )
    from .runtime import CURRENT, scratch_backtest
    execution = CURRENT.get()
    if execution and execution.get('frame') is not None:
        session = execution['session']
        from .parallel import worker_limit, execute
        from ..performance import measure
        cancelled = execution.get('cancelled') or (lambda: session.ident in _CANCELS)
        phase = lambda phase,msg: session.update(phase,msg,node_id=node['node_id'])
        progress = lambda processed,eligible,trades: session.update('BACKTESTING',f'Processing history: {processed}/{eligible} bars; {trades} closed trades',node_id=node['node_id'],bars_processed=processed,eligible_bars=eligible,completed_trades=trades)
        with measure('deeptest.compute_wall'):
            if worker_limit() > 1:
                res = execute(req, execution['frame'], session, cancelled, phase)
            else:
                from .parallel import reserve_memory
                with reserve_memory(execution['frame']):
                    res = scratch_backtest(req, execution['frame'], session.path, phase,
                                           cancelled=cancelled, progress=progress)
    else:
        res = run_backtest(req)
    if not res.ok:
        return {"ok": False, "error": getattr(res, "error", "backtest failed")}

    assumptions = {
        "execution_model": (res.metrics or {}).get("execution_model"),
        "spread_mult": req.spread_mult,
        "slippage_mult": req.slippage_mult,
        "commission_mult": req.commission_mult,
        "commission": ((g.get("costs") or {}).get("commission_per_lot"),
                       "per-lot commission from the node's generation cost model"),
        "swap": "not modelled (positions are intraday-window only in this engine)",
        "sl_tp_same_bar_policy": ("SL-first: if a bar touches both stop and target, the stop "
                                  "is assumed hit (conservative, never profit-favouring); "
                                  "signals are evaluated on bar close"),
        "timing": "signals on bar close; fills at the next bar open",
        "session_timezone": "stored bar timestamps (UTC) — session labels from the lab's "
                            "DST-aware session bands",
        "gaps": "gaps are reported, never filled; no bars are interpolated or extrapolated",
        "sizing": "volume floored to the broker lot step; a trade whose size falls below the "
                  "broker minimum is skipped rather than enlarged",
        "look_ahead": "none — features and signals are computed on closed bars only; the "
                      "window bounds the dataset slice",
        "sl_offsets_pips": sl_off,
        "tp_offsets_pips": tp_off,
    }
    engine_versions = {
        **engine_manifest(),
        "mt5_historical_adapter": "MT5->pandas historical adapter v1 (no broker orders)",
        "deeptest": "V10.2-data-bounded",
        "product": _product_version(),
    }

    trades = _normalize_trades(res.trades, settings)
    for trade in trades:trade["symbol"] = symbol
    return {"ok": True, "metrics": res.metrics, "trades": trades,
            "assumptions": assumptions, "engine_versions": engine_versions}


@profiled('deeptest.normalize_trades', hot=False)
def _normalize_trades(raw_trades: List[Dict[str, Any]], settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Map engine trades onto the §11 trade-level schema (costs broken out)."""
    out = []
    for t in raw_trades or []:
        hold = None
        try:
            hold = float(t.get("exit_ts")) - float(t.get("entry_ts"))
        except (TypeError, ValueError):
            hold = None
        spread_cost = float(t.get("spread_paid") or 0.0)
        slippage_cost = float(t.get("slippage_points") or 0.0)
        pnl = float(t.get("pnl") or 0.0)
        # engine pnl already subtracts commission+swap; costs recorded as modelled
        out.append({
            "side": t.get("side"), "symbol": None,
            "entry_ts": t.get("entry_ts"), "entry_price": t.get("entry_price"),
            "exit_ts": t.get("exit_ts"), "exit_price": t.get("exit_price"),
            "volume": t.get("lots"), "sl": t.get("sl"), "tp": t.get("tp"),
            "exit_reason": t.get("exit_reason"),
            "hold_seconds": hold,
            "gross_pnl": None,
            "commission": None,
            "spread_cost": spread_cost,
            "slippage_cost": slippage_cost,
            "swap_cost": None,
            "net_pnl": pnl,
            "session": t.get("session"), "dow": t.get("dow"),
        })
    return out


# --------------------------------------------------------------------------- #
# §12 preference + statistics precedence
# --------------------------------------------------------------------------- #
def preferred_for(db: Any, node_id: int) -> Optional[Dict[str, Any]]:
    """The node's preferred scenario row (explicit choice first, else latest valid)."""
    pref = S.get_pref(db, node_id)
    if pref and pref.get("preferred_run_id") and int(pref.get("explicitly_selected") or 0):
        sc = S.get_scenario(db, pref["preferred_run_id"])
        if sc and sc["status"] in VALID_FOR_PREFERENCE and (sc.get("analytics") or {}).get("summary"):
            sc["preference_source"] = "explicit"
            from .metric_service import enrich
            return enrich(db,sc)
        if sc:
            # a stored explicit preference pointing at an invalid run is kept
            # as a record but never used silently
            pass
    valid = [r for r in S.list_scenarios(db, int(node_id)) if r["status"] in VALID_FOR_PREFERENCE and (r.get("analytics") or {}).get("summary")]
    if valid:
        valid[0]["preference_source"] = "latest_valid"
        from .metric_service import enrich
        return enrich(db,valid[0])
    return None


def set_preferred(db: Any, node_id: int, run_id: str, explicit: bool = True) -> Dict[str, Any]:
    sc = S.get_scenario(db, run_id)
    if not sc:
        return {"ok": False, "error": f"unknown run {run_id}"}
    if int(sc["node_id"]) != int(node_id):
        return {"ok": False, "error": "that scenario belongs to a different node"}
    if sc["status"] not in VALID_FOR_PREFERENCE:
        return {"ok": False,
                "error": f"a {sc['status']} run cannot be preferred — only COMPLETED or "
                         "PARTIAL scenarios hold valid results"}
    S.set_pref(db, node_id, run_id, explicit=explicit)
    from .metric_service import enrich
    return {"ok": True, "preferred_run_id": run_id, "explicit": bool(explicit),
            "scenario": enrich(db,S.get_scenario(db, run_id))}


def node_deeptest_summary(db: Any, node_id: int) -> Dict[str, Any]:
    """Everything the DEEPTESTED-NODES / stats projection needs for one node."""
    from .metric_service import enrich_many, enrich
    rows = enrich_many(db,S.list_scenarios(db, int(node_id)))
    valid = [r for r in rows if r["status"] in VALID_FOR_PREFERENCE]
    pref = enrich(db,preferred_for(db, node_id))
    return {
        "node_id": int(node_id),
        "scenario_count": len(rows),
        "valid_scenario_count": len(valid),
        "statuses": {s: sum(1 for r in rows if r["status"] == s) for s in VALID_STATUSES},
        "preferred": ({"run_id": pref["run_id"], "status": pref["status"],
                       "coverage_status": pref.get("coverage_status"),
                       "metrics": pref.get("metrics"), "analytics": pref.get("analytics"),
                       "metric_provenance":pref.get("metric_provenance"),
                       "duration_key": pref.get("duration_key"),
                       "finished_at": pref.get("finished_at"),
                       "preference_source": pref.get("preference_source")}
                      if pref else None),
        "last_run": ({"run_id": rows[0]["run_id"], "status": rows[0]["status"],
                      "finished_at": rows[0].get("finished_at")}
                     if rows else None),
        # V6.8 §5.1 — every saved scenario, individually addressable (Inspect
        # S1/S2/… and Compare); each keeps its OWN metrics, never blended.
        "scenarios": [{
            "run_id": r["run_id"],
            "status": r.get("status"),
            "coverage_status": r.get("coverage_status"),
            "duration_key": r.get("duration_key"),
            "requested_start": r.get("requested_start"),
            "requested_end": r.get("requested_end"),
            "actual_start": r.get("actual_start"),
            "actual_end": r.get("actual_end"),
            "bars_used": r.get("bars_used"),
            "signature": r.get("signature"),
            "metric_provenance":r.get("metric_provenance"),
            "headline": {
                "net_profit": ((r.get("analytics") or {}).get("summary") or {}).get("net_profit"),
                "net_return_pct": ((r.get("analytics") or {}).get("summary") or {}).get("net_return_pct"),
                "profit_factor": ((r.get("analytics") or {}).get("summary") or {}).get("profit_factor"),
                "max_drawdown_pct": ((r.get("analytics") or {}).get("summary") or {}).get("max_drawdown_pct"),
                "win_rate": ((r.get("analytics") or {}).get("summary") or {}).get("win_rate"),
                "trades": ((r.get("analytics") or {}).get("summary") or {}).get("trades"),
            },
            "is_preferred": bool(pref and pref.get("run_id") == r["run_id"]),
            "finished_at": r.get("finished_at"),
        } for r in rows],
    }


def compare(db: Any, run_ids: List[str]) -> Dict[str, Any]:
    """§13 — side-by-side comparison that EXPLICITLY shows config/coverage diffs."""
    if len(run_ids or []) < 2:
        return {"ok": False, "error": "at least two scenario run_ids are required"}
    rows = []
    for rid in run_ids:
        sc = S.get_scenario(db, rid)
        if not sc:
            return {"ok": False, "error": f"unknown run {rid}"}
        rows.append(sc)
    from .metric_service import enrich_many
    rows=enrich_many(db,rows)
    node_ids = {int(r["node_id"]) for r in rows}
    if len(node_ids) > 1:
        return {"ok": False, "error": "scenarios of different nodes cannot be compared"}
    a, b = rows[0], rows[1]

    def _set(r):
        s = r.get("settings") or {}
        prov = r.get("provenance") or {}
        return {
            "node_id": r["node_id"], "run_id": r["run_id"], "status": r["status"],
            "duration_key": r.get("duration_key"),
            "requested": (prov.get("requested_period") or
                          {"start_ts": r.get("requested_start"), "end_ts": r.get("requested_end")}),
            "actual_tested_range": prov.get("actual_tested_range"),
            "bars_used": r.get("bars_used"),
            "dataset_fingerprint": r.get("dataset_fingerprint"),
            "coverage_status": r.get("coverage_status"),
            "gaps_count": len(r.get("gaps") or []),
            "total_capital": s.get("total_capital"),
            "risk_pct": s.get("risk_pct"),
            "max_active_trades": s.get("max_active_trades_per_node"),
            "sl_offset_pips": s.get("sl_offset_pips"),
            "tp_offset_pips": s.get("tp_offset_pips"),
            "genome_hash": r.get("genome_hash"),
            "config_hash": r.get("config_hash"),
            "engine_versions": r.get("engine_versions"),
        }

    left, right = _set(a), _set(b)
    differences = []
    for k in left:
        if k in ("run_id",):
            continue
        if left.get(k) != right.get(k):
            differences.append({"field": k, "left": left.get(k), "right": right.get(k)})
    metrics_rows = []
    keys = ["net_profit", "net_return_pct", "max_drawdown_pct", "sharpe", "profit_factor",
            "trades", "win_rate", "expectancy_per_trade", "avg_trade", "sortino",
            "winning_trades", "losing_trades"]
    sa, sb = (a.get("analytics") or {}).get("summary") or {}, (b.get("analytics") or {}).get("summary") or {}
    ma, mb = a.get("metrics") or {}, b.get("metrics") or {}
    for k in keys:
        lv = sa.get(k) if sa.get(k) is not None else ma.get(k)
        rv = sb.get(k) if sb.get(k) is not None else mb.get(k)
        delta = None
        if isinstance(lv, (int, float)) and isinstance(rv, (int, float)) \
                and not isinstance(lv, bool) and not isinstance(rv, bool):
            delta = float(lv) - float(rv)
        metrics_rows.append({"metric": k, "left": lv, "right": rv, "delta": delta,
                             "unavailable_reason": (None if (lv is not None or rv is not None)
                                                    else "not recomputable from the stored scenario summary")})

    # V6.8 §5.2 — monthly PnL + exit-reason counts/PnL per scenario, each from
    # ITS OWN saved trades (never combined), plus explicit non-equivalence notes.
    def _blocks(sc_row):
        rid_ = sc_row["run_id"]
        trades = [dict(r) for r in (db.q(
            "SELECT * FROM deeptest_trades WHERE run_id=? ORDER BY trade_no", (rid_,)) or [])]
        months: Dict[str, float] = {}
        exits: Dict[str, Dict[str, Any]] = {}
        for t in trades:
            pnl = float(t.get("net_pnl") if t.get("net_pnl") is not None else 0.0)
            try:
                m = datetime.fromtimestamp(float(t.get("entry_ts")), tz=timezone.utc).strftime("%Y-%m")
            except Exception:
                m = "unknown"
            months[m] = months.get(m, 0.0) + pnl
            er = str(t.get("exit_reason") or "UNKNOWN")
            e = exits.setdefault(er, {"count": 0, "net_pnl": 0.0})
            e["count"] += 1
            e["net_pnl"] = round(e["net_pnl"] + pnl, 2)
        return {"trade_count": len(trades),
                "monthly_pnl": {k: round(v, 2) for k, v in sorted(months.items())},
                "exit_reasons": exits}

    warnings = []
    if differences:
        warnings.append("these scenarios differ in the fields listed above — do not treat "
                        "their metrics as directly equivalent")
    if left.get("requested") != right.get("requested"):
        warnings.append("different requested date ranges — results are NOT directly comparable")
    if left.get("coverage_status") != right.get("coverage_status"):
        warnings.append("different data completeness (coverage) — the shorter run's metrics "
                        "cover a different slice of history")
    if left.get("dataset_fingerprint") != right.get("dataset_fingerprint"):
        warnings.append("different data fingerprints — the scenarios ran on different candle stores")
    if left.get("genome_hash") != right.get("genome_hash") or left.get("config_hash") != right.get("config_hash"):
        warnings.append("different strategy/engine identity — the strategies are not the same build")
    if (left.get("total_capital"), left.get("risk_pct")) != (right.get("total_capital"), right.get("risk_pct")):
        warnings.append("different capital or risk assumptions — money metrics are not equivalent")
    return {
        "ok": True,
        "scenarios": [left, right],
        "config_and_coverage_differences": differences,
        "metrics": metrics_rows,
        "monthly_pnl": {"left": _blocks(a), "right": _blocks(b)},
        "warnings": warnings,
        "warning": warnings[0] if warnings else
                   "same configuration and coverage binding for the compared fields",
    }


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _load_node(db: Any, node_id: int) -> Dict[str, Any]:
    """The node's own generation data — the authoritative strategies row
    (same universe as the nodes index; never a second qualification rule)."""
    row = db.one("SELECT * FROM strategies WHERE id=?", (int(node_id),))
    if not row:
        raise ValueError(f"node {node_id} is not in the authoritative inventory")
    r = dict(row)
    if str(r.get('data_source') or '').upper()=='LEGACY_TEST' or str(r.get('status') or '').upper()=='EXCLUDED':
        raise ValueError('Excluded/legacy node is not part of the active historical population')
    g = r.get("genome") or {}
    if isinstance(g, str):
        try:
            g = json.loads(g)
        except Exception:
            g = {}
    # READ-ONLY view of the node's live config (for default display semantics);
    # Deep Testing never writes it.
    cfg = {}
    try:
        cfg_row = db.one("SELECT * FROM live_test_configs WHERE strategy_id=?", (int(node_id),))
        cfg = dict(cfg_row) if cfg_row else {}
    except Exception:
        cfg = {}
    symbol = str(g.get("symbol") or r.get("symbol") or "").upper()
    timeframe = str(g.get("timeframe") or r.get("timeframe") or "").upper()
    if not symbol or not timeframe:
        raise ValueError(f"node {node_id} has no symbol/timeframe in its generation data")
    return {"node_id": int(node_id), "symbol": symbol, "timeframe": timeframe,
            "genome": g, "config": cfg,
            "research_node_num": r.get("research_node_num")}


def _genome_hash(g: Any) -> str:
    try:
        return hashlib.sha256(json.dumps(g, sort_keys=True, default=str).encode()).hexdigest()[:16]
    except Exception:
        return ""


def _execution_config_hash():
    from ..config import get_config
    cfg=get_config()
    # Effective offsets are already frozen in node_settings by preflight;
    # unrelated Live metadata/default edits must not create duplicate tests.
    return _genome_hash({'max_hold_bars':cfg.backtest.max_hold_bars,
                         'max_position_size_lots':cfg.risk.max_position_size_lots})


def _material_engine_version():
    from ..versions import manifest
    # Git/product names are provenance, NOT execution inputs. Explicit semantic
    # versions keep cosmetic releases from invalidating identical scenarios.
    m=manifest()
    return _genome_hash({k:m[k] for k in ('backtester','cost_model','slippage_model','sessions','risk','data_schema')})+':daily-v1:realised-v1:data-bounded-v102'


def _product_version() -> str:
    try:
        from ..versions import PRODUCT_RELEASE
        return PRODUCT_RELEASE
    except Exception:
        return "unknown"
