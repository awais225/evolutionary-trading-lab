"""V4.1 research-run lifecycle: backup / reset / resume-add (spec §A–§C).

Single place that implements the three START NEW RESEARCH RUN options:

  A. backup_and_reset  - scoped USER_RESEARCH backup -> verify -> reset -> fresh run
  B. resume_add        - keep the current study, raise the target by N nodes
  C. reset_only        - destructive reset without backup (explicit confirmation)

Hard rules enforced here:
  * LEGACY_TEST infrastructure rows are never deleted, reset, backed up as
    USER_RESEARCH, re-statused, re-generated or counted towards a research
    target. Every destructive statement is scoped with NOT <LEGACY_PROTECT>,
    so a row is only ever removed when it is provably not legacy.
  * The existing application machinery is reused, not replaced: run/run-metadata
    come from the existing pipeline-state manager (psm.new_run / psm.resume_run /
    research_runs), the target comes from the existing
    EvolutionEngine.get/set_total_node_target(), and state is rebuilt through the
    existing Laboratory.recheck() / reconstruct_state() path.
  * The engine's own frontier (is_target_reached / remaining_nodes) is run-scoped
    by the existing code, so a fresh run counts only the new run's nodes and a
    resumed study counts only that study's nodes - legacy rows never contribute.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
import zipfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from .. import paths as P
from ..config import get_config
from ..db.database import get_db

log = logging.getLogger("research_run")

# --------------------------------------------------------------------------- #
# Scope predicates - one definition, mirroring the V4.0 LEGACY_TEST classifier.
# A row is LEGACY (protected) if the data_source column says so, or if it matches
# the repository's historical dummy/legacy signature.
# --------------------------------------------------------------------------- #
LEGACY_PROTECT = (
    "(COALESCE(data_source,'')='LEGACY_TEST' "
    "OR id<=787 OR run_id IS NULL "
    "OR run_id LIKE '%TEST%' OR run_id='RUN-HISTORICAL-PRESERVED')"
)
USER_ROWS = "NOT " + LEGACY_PROTECT

# Tables that reference strategies(strategy_id) and therefore belong to the
# research-run state that is backed up / reset together with the nodes.
STRATEGY_DEPENDENT_TABLES: Tuple[str, ...] = (
    "activity", "backtests", "executions", "hypotheses", "live_test_configs",
    "matrices", "mt5_backtests", "mt5_demo_configs", "paper_trades",
    "research_shortlist", "strategy_pipeline_states", "validations",
)

# Exact confirmation phrases required by the destructive options (spec §8).
CONFIRM_BACKUP_AND_RESET = "BACKUP_AND_RESET"
CONFIRM_RESET_ONLY = "RESET_USER_RESEARCH"

MAX_ADDITIONAL_NODES = 1_000_000

# --------------------------------------------------------------------------- #
# Operation state (progress + duplicate-submission protection)
# --------------------------------------------------------------------------- #
_op_gate = threading.Lock()    # single-operation guard (non-reentrant: one operation at a time)
_op_lock = threading.RLock()   # reentrant: protects the shared operation-status dict
_op: Dict[str, Any] = {
    "busy": False,
    "operation": None,
    "stage": "IDLE",
    "stages": [],
    "message": "",
    "started_at": None,
    "finished_at": None,
    "result": None,
    "error": None,
}


def operation_status() -> Dict[str, Any]:
    with _op_lock:
        return json.loads(json.dumps(_op, default=str))


def _set_stage(name: str, message: str = "", status: str = "RUNNING") -> None:
    with _op_lock:
        _op["stage"] = name
        _op["message"] = message
        _op["stages"].append({"stage": name, "message": message, "status": status, "ts": time.time()})
    log.info("[RESEARCH-RUN] %s: %s", name, message)


@contextmanager
def _operation(name: str) -> Iterator[None]:
    """Serialise research-run operations; a second concurrent call is rejected."""
    if not _op_gate.acquire(blocking=False):
        raise RuntimeError("another research-run operation is already in progress")
    try:
        with _op_lock:
            _op.update({"busy": True, "operation": name, "stage": "START", "stages": [],
                        "message": "", "started_at": time.time(), "finished_at": None,
                        "result": None, "error": None})
        yield
    finally:
        with _op_lock:
            _op["busy"] = False
            _op["finished_at"] = time.time()
        _op_gate.release()


# --------------------------------------------------------------------------- #
# Counters / state
# --------------------------------------------------------------------------- #
def counts() -> Dict[str, Any]:
    """Current USER_RESEARCH vs LEGACY_TEST inventory (DB truth)."""
    db = get_db()
    row = db.one(
        "SELECT "
        " (SELECT COUNT(*) FROM strategies WHERE " + USER_ROWS + ") AS user_nodes,"
        " (SELECT COUNT(*) FROM strategies WHERE " + LEGACY_PROTECT + ") AS legacy_nodes,"
        " (SELECT COUNT(*) FROM strategies) AS total_nodes,"
        " (SELECT MAX(id) FROM strategies) AS max_id,"
        " (SELECT MAX(research_node_num) FROM strategies WHERE " + USER_ROWS + ") AS max_research_node_num,"
        " (SELECT MAX(generation) FROM strategies WHERE " + USER_ROWS + ") AS max_user_generation"
    ) or {}
    return {
        "user_research_nodes": int(row.get("user_nodes") or 0),
        "legacy_test_nodes": int(row.get("legacy_nodes") or 0),
        "total_strategies": int(row.get("total_nodes") or 0),
        "max_strategy_id": int(row.get("max_id") or 0),
        "max_user_research_node_num": int(row.get("max_research_node_num") or 0),
        "max_user_generation": int(row.get("max_user_generation") or 0),
    }


def _run_context() -> Dict[str, Any]:
    """Active run + target information coming from the existing machinery."""
    from ..evolution.engine import get_evo_engine
    from ..orchestrator.pipeline_state import get_pipeline_state_manager

    evo = get_evo_engine()
    psm = get_pipeline_state_manager()
    run_id = psm.run_id or evo.active_run_id
    run_nodes = evo.run_nodes(run_id) if run_id else evo.total_nodes()
    return {
        "run_id": run_id,
        "active_run_id": evo.active_run_id,
        "run_nodes": int(run_nodes),
        "target": int(evo.get_total_node_target()),
        "remaining_in_run": int(max(0, evo.get_total_node_target() - int(run_nodes))),
        "engine_active_run_id": evo.active_run_id,
    }


def configured_fresh_target() -> int:
    """Starting target for a fresh run: the configured value, never hard-coded.

    Uses the existing configuration (evolution.total_node_target); if that is not
    configured falls back to the currently effective target from the existing
    engine accessor.
    """
    cfg = get_config()
    configured = getattr(getattr(cfg, "evolution", None), "total_node_target", None)
    try:
        configured = int(configured) if configured else 0
    except (TypeError, ValueError):
        configured = 0
    if configured > 0:
        return configured
    from ..evolution.engine import get_evo_engine
    return int(get_evo_engine().get_total_node_target())


def state() -> Dict[str, Any]:
    """Everything the dashboard needs for the START NEW RESEARCH RUN dialog."""
    c = counts()
    ctx = _run_context()
    return {
        "user_research_nodes": c["user_research_nodes"],
        "legacy_test_nodes": c["legacy_test_nodes"],
        "total_strategies": c["total_strategies"],
        "run_id": ctx["run_id"],
        "run_nodes": ctx["run_nodes"],
        "target": ctx["target"],
        "remaining_in_run": ctx["remaining_in_run"],
        "next_strategy_id": c["max_strategy_id"] + 1,
        "next_research_node_num": (ctx["run_nodes"] + 1),
        "configured_fresh_target": configured_fresh_target(),
        "legacy_notice": (
            "LEGACY_TEST infrastructure nodes (%d) are outside this workflow and "
            "will not be deleted, reset, backed up or counted." % c["legacy_test_nodes"]
        ),
        "backups": list_research_backups(limit=5),
        "operation": operation_status(),
    }


def _population_fingerprint() -> Dict[str, Any]:
    """Identity of the current USER_RESEARCH population (proves non-regeneration)."""
    rows = get_db().q("SELECT id, status, generation, research_node_num, hash FROM strategies "
                      "WHERE " + USER_ROWS + " ORDER BY id")
    h = hashlib.sha256()
    for r in rows:
        h.update(("%s|%s|%s|%s|%s\n" % (r["id"], r["status"], r["generation"],
                                        r["research_node_num"], r["hash"])).encode())
    return {"count": len(rows), "max_id": max((r["id"] for r in rows), default=0),
            "sha256": h.hexdigest()}


# --------------------------------------------------------------------------- #
# Backup (scoped to USER_RESEARCH only)
# --------------------------------------------------------------------------- #
def list_research_backups(limit: int = 50) -> List[Dict[str, Any]]:
    P.ensure_layout()
    out = []
    for f in sorted(P.BACKUPS_DIR.glob("research_backup_*.zip"), reverse=True)[:limit]:
        st = f.stat()
        out.append({
            "filename": f.name, "path": str(f),
            "size_bytes": st.st_size, "size_mb": round(st.st_size / (1024 * 1024), 2),
            "created_at": st.st_mtime,
        })
    return out


def create_research_backup(note: str = "") -> Dict[str, Any]:
    """Create a USER_RESEARCH-only research-run backup archive.

    Same architecture/storage format as the existing backup facility
    (DATA/BACKUPS/*.zip containing DATABASE/lab_state.db + manifest), but the
    database snapshot is scoped: LEGACY_TEST rows, their dependent rows and the
    test-run metadata are removed from the *copy*, and a research-run manifest
    (counts + sha256) is written so the archive can be verified before any
    destructive step.
    """
    P.ensure_layout()
    cfg = get_config()
    db = get_db()
    before = counts()
    ctx = _run_context()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = P.BACKUPS_DIR / f"research_backup_{ts}.zip"
    tmp_db = P.BACKUPS_DIR / f".tmp_research_backup_{ts}.db"

    # 1. consistent snapshot of the live database (sqlite backup API, read-only on source)
    src = sqlite3.connect(cfg.database_path)
    try:
        dst = sqlite3.connect(tmp_db)
        try:
            src.backup(dst)
            # 2. strip everything that is not USER_RESEARCH research state
            for tbl in STRATEGY_DEPENDENT_TABLES:
                dst.execute("DELETE FROM %s WHERE strategy_id IN "
                            "(SELECT id FROM strategies WHERE %s)" % (tbl, LEGACY_PROTECT))
            dst.execute("DELETE FROM research_memory WHERE parent_id IN "
                        "(SELECT id FROM strategies WHERE %s) OR child_id IN "
                        "(SELECT id FROM strategies WHERE %s)" % (LEGACY_PROTECT, LEGACY_PROTECT))
            dst.execute("DELETE FROM strategies WHERE " + LEGACY_PROTECT)
            dst.execute("DELETE FROM research_runs WHERE run_id IS NULL OR run_id LIKE '%TEST%' "
                        "OR run_id='RUN-HISTORICAL-PRESERVED'")
            dst.commit()
            # reading the snapshot must be an ordinary, fully usable database
            user_nodes = dst.execute("SELECT COUNT(*) FROM strategies").fetchone()[0]
            legacy_left = dst.execute("SELECT COUNT(*) FROM strategies WHERE " + LEGACY_PROTECT).fetchone()[0]
            runs_left = [r[0] for r in dst.execute("SELECT run_id FROM research_runs ORDER BY created_at")]
            dst.execute("VACUUM")
            dst.commit()
        finally:
            dst.close()
    finally:
        src.close()

    db_bytes = tmp_db.read_bytes()
    manifest = {
        "kind": "RESEARCH_RUN_BACKUP",
        "created_at": time.time(),
        "created_at_iso": datetime.now().isoformat(timespec="seconds"),
        "note": note,
        "run_id": ctx["run_id"],
        "run_nodes": ctx["run_nodes"],
        "target": ctx["target"],
        "user_research_nodes": int(user_nodes),
        "legacy_test_nodes_excluded": int(before["legacy_test_nodes"]),
        "legacy_rows_remaining_in_backup": int(legacy_left),
        "total_strategies_before_backup": int(before["total_strategies"]),
        "research_runs_kept": runs_left,
        "database_sha256": hashlib.sha256(db_bytes).hexdigest(),
        "database_bytes": len(db_bytes),
        "source_database": str(cfg.database_path),
    }

    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("DATABASE/lab_state.db", db_bytes)
            zf.writestr("research_run_manifest.json", json.dumps(manifest, indent=2, default=str))
    finally:
        if tmp_db.exists():
            tmp_db.unlink()

    size = zip_path.stat().st_size
    log.info("[RESEARCH-RUN] backup created %s (%d user nodes, %d legacy excluded, %d bytes)",
             zip_path.name, user_nodes, before["legacy_test_nodes"], size)
    return {
        "ok": True,
        "filename": zip_path.name,
        "path": str(zip_path),
        "size_bytes": size,
        "size_mb": round(size / (1024 * 1024), 2),
        "user_research_nodes": int(user_nodes),
        "legacy_test_nodes_excluded": int(before["legacy_test_nodes"]),
        "manifest": manifest,
    }


def verify_research_backup(path: str | Path, expected_user_nodes: Optional[int] = None) -> Dict[str, Any]:
    """Read the archive back and prove it is usable before anything is deleted."""
    errors: List[str] = []
    details: Dict[str, Any] = {}
    p = Path(path)
    if not p.exists():
        return {"ok": False, "errors": ["backup file does not exist"], "details": {}}

    try:
        zf = zipfile.ZipFile(p)
    except Exception as e:  # unreadable archive
        return {"ok": False, "errors": ["backup archive is not readable: %s" % e], "details": {}}

    with zf:
        bad = zf.testzip()
        if bad:
            errors.append("corrupt archive member: %s" % bad)
        names = zf.namelist()
        details["members"] = names
        if "DATABASE/lab_state.db" not in names:
            errors.append("database member DATABASE/lab_state.db is missing")
        manifest: Dict[str, Any] = {}
        if "research_run_manifest.json" in names:
            try:
                manifest = json.loads(zf.read("research_run_manifest.json").decode())
            except Exception as e:
                errors.append("manifest is unreadable: %s" % e)
        else:
            errors.append("research_run_manifest.json is missing")
        details["manifest"] = manifest

        if "DATABASE/lab_state.db" in names:
            data = zf.read("DATABASE/lab_state.db")
            sha = hashlib.sha256(data).hexdigest()
            details["database_sha256"] = sha
            if manifest.get("database_sha256") and manifest["database_sha256"] != sha:
                errors.append("database sha256 does not match the manifest")
            tmp = P.BACKUPS_DIR / (".verify_" + p.stem + ".db")
            try:
                tmp.write_bytes(data)
                conn = sqlite3.connect(str(tmp))
                try:
                    integ = conn.execute("PRAGMA integrity_check").fetchone()
                    if not integ or integ[0] != "ok":
                        errors.append("sqlite integrity_check failed")
                    n_user = conn.execute("SELECT COUNT(*) FROM strategies").fetchone()[0]
                    n_legacy = conn.execute("SELECT COUNT(*) FROM strategies WHERE "
                                            + LEGACY_PROTECT).fetchone()[0]
                    run_ids = [r[0] for r in conn.execute("SELECT run_id FROM research_runs")]
                    details.update({"strategies_in_backup": int(n_user),
                                    "legacy_rows_in_backup": int(n_legacy),
                                    "research_runs_in_backup": run_ids})
                    if n_legacy:
                        errors.append("backup contains %d legacy rows" % n_legacy)
                    if expected_user_nodes is not None and int(n_user) != int(expected_user_nodes):
                        errors.append("backup holds %s nodes, expected %s" % (n_user, expected_user_nodes))
                    if manifest.get("run_id") and manifest["run_id"] not in run_ids:
                        errors.append("active run %s missing from backup metadata" % manifest["run_id"])
                finally:
                    conn.close()
            except sqlite3.Error as e:
                errors.append("backup database cannot be opened: %s" % e)
            finally:
                if tmp.exists():
                    tmp.unlink()

    return {"ok": not errors, "errors": errors, "details": details}


# --------------------------------------------------------------------------- #
# Reset (USER_RESEARCH only) + fresh run
# --------------------------------------------------------------------------- #
def _reset_statements() -> List[Tuple[str, tuple]]:
    stmts: List[Tuple[str, tuple]] = []
    for tbl in STRATEGY_DEPENDENT_TABLES:
        stmts.append(("DELETE FROM %s WHERE strategy_id IN "
                      "(SELECT id FROM strategies WHERE %s)" % (tbl, USER_ROWS), ()))
    stmts.append(("DELETE FROM research_memory WHERE parent_id IN "
                  "(SELECT id FROM strategies WHERE %s) OR child_id IN "
                  "(SELECT id FROM strategies WHERE %s)" % (USER_ROWS, USER_ROWS), ()))
    stmts.append(("DELETE FROM strategies WHERE " + USER_ROWS, ()))
    return stmts


def reset_user_research() -> Dict[str, Any]:
    """Atomically delete the USER_RESEARCH research state; LEGACY_TEST untouched."""
    db = get_db()
    before = counts()
    stmts = _reset_statements()
    rowcounts = db.transaction(stmts)
    deleted_dependents = {t: c for (sql, _), c, t in zip(stmts, rowcounts, list(STRATEGY_DEPENDENT_TABLES) + ["research_memory", "strategies"])}
    after = counts()
    result = {
        "ok": True,
        "deleted_strategies": int(deleted_dependents.get("strategies", 0)),
        "deleted_dependents": {k: v for k, v in deleted_dependents.items() if k != "strategies"},
        "user_research_before": before["user_research_nodes"],
        "user_research_after": after["user_research_nodes"],
        "legacy_test_before": before["legacy_test_nodes"],
        "legacy_test_after": after["legacy_test_nodes"],
        "legacy_untouched": before["legacy_test_nodes"] == after["legacy_test_nodes"],
    }
    log.info("[RESEARCH-RUN] reset: %d user nodes deleted; legacy %d -> %d (untouched=%s)",
             result["deleted_strategies"], result["legacy_test_before"], result["legacy_test_after"],
             result["legacy_untouched"])
    return result


def _init_fresh_run(target: int, start: bool) -> Dict[str, Any]:
    """Initialise the new run through the existing run/target/reconstruction machinery."""
    from ..evolution.engine import get_evo_engine
    from ..orchestrator.lab import get_lab
    from ..orchestrator.pipeline_state import get_pipeline_state_manager

    evo = get_evo_engine()
    psm = get_pipeline_state_manager()
    lab = get_lab()

    evo.set_total_node_target(int(target))          # existing target machinery
    new_run_id = psm.new_run(target=int(target))    # existing new-run machinery (RUN-YYYYMMDD-HHMMSS)
    evo.active_run_id = new_run_id                  # engine frontier follows the new run
    state_rebuilt = lab.recheck()                   # existing reconstruction + publish
    evo.get_node_generation_state(force=True)       # refresh the node-state snapshot cache
    started = False
    if start:
        res = lab.start(mode="continuous", run_type="resume")
        started = bool(res.get("ok"))
    return {
        "run_id": new_run_id,
        "target": int(target),
        "started": started,
        "reconstructed_total_nodes": int(state_rebuilt.get("total_nodes", 0)),
        "generation": int(state_rebuilt.get("current_generation", 0)),
    }


def start_fresh_run(mode: str, confirm: str, target: Optional[int] = None,
                    start: bool = False, note: str = "") -> Dict[str, Any]:
    """Option A (backup_and_reset) / Option C (reset_only)."""
    if mode not in ("backup_and_reset", "reset_only"):
        return {"ok": False, "stage": "VALIDATE",
                "error": "mode must be 'backup_and_reset' or 'reset_only'"}
    required = CONFIRM_BACKUP_AND_RESET if mode == "backup_and_reset" else CONFIRM_RESET_ONLY
    if (confirm or "").strip() != required:
        return {"ok": False, "stage": "CONFIRM",
                "error": "explicit confirmation required: send confirm='%s'" % required,
                "confirmation_required": required}

    fresh_target = int(target) if target else configured_fresh_target()
    if fresh_target <= 0:
        return {"ok": False, "stage": "VALIDATE", "error": "target must be a positive integer"}

    with _operation("start_fresh_run:%s" % mode):
        before = counts()
        backup: Optional[Dict[str, Any]] = None
        verification: Optional[Dict[str, Any]] = None
        try:
            if mode == "backup_and_reset":
                _set_stage("BACKUP", "creating USER_RESEARCH-only backup")
                backup = create_research_backup(note=note or "pre-reset research backup")
                if not backup.get("ok"):
                    _set_stage("BACKUP", "backup failed", "FAILED")
                    return {"ok": False, "stage": "BACKUP", "error": "backup failed", "backup": backup}

                _set_stage("VERIFY", "verifying backup before any destructive step")
                verification = verify_research_backup(backup["path"],
                                                      expected_user_nodes=before["user_research_nodes"])
                if not verification.get("ok"):
                    _set_stage("VERIFY", "backup verification failed - nothing was deleted", "FAILED")
                    return {"ok": False, "stage": "BACKUP_VERIFY",
                            "error": "backup verification failed: %s" % "; ".join(verification["errors"]),
                            "backup": backup, "verification": verification,
                            "deleted_anything": False}
                _set_stage("VERIFY", "backup verified (%d user nodes, 0 legacy rows)"
                           % verification["details"].get("strategies_in_backup", 0), "OK")

            _set_stage("RESET", "resetting USER_RESEARCH study (LEGACY_TEST preserved)")
            reset = reset_user_research()
            if not reset.get("legacy_untouched"):
                _set_stage("RESET", "LEGACY_TEST count changed - aborting", "FAILED")
                return {"ok": False, "stage": "RESET", "error": "LEGACY_TEST integrity check failed",
                        "reset": reset, "backup": backup}
            _set_stage("RESET", "removed %d user nodes; legacy %d untouched"
                       % (reset["deleted_strategies"], reset["legacy_test_after"]), "OK")

            _set_stage("INIT", "initialising fresh research run (target %d)" % fresh_target)
            init = _init_fresh_run(fresh_target, start)
            _set_stage("INIT", "fresh run %s initialised" % init["run_id"], "OK")

            after = counts()
            result = {
                "ok": True,
                "mode": mode,
                "stage": "DONE",
                "run_id": init["run_id"],
                "target": fresh_target,
                "started": init["started"],
                "before": before,
                "after": after,
                "reset": reset,
                "backup": backup,
                "verification": ({"ok": verification.get("ok"),
                                  "details": verification.get("details", {}).get("manifest", {})}
                                 if verification else None),
                "legacy_test_nodes": after["legacy_test_nodes"],
                "legacy_untouched": reset["legacy_untouched"] and after["legacy_test_nodes"] == before["legacy_test_nodes"],
                "generation": init["generation"],
            }
            with _op_lock:
                _op["result"] = result
            return result
        except Exception as e:  # never leave a half-reported state
            log.exception("[RESEARCH-RUN] %s failed", mode)
            _set_stage("ERROR", str(e), "FAILED")
            with _op_lock:
                _op["error"] = str(e)
            return {"ok": False, "stage": _op["stage"], "error": str(e),
                    "backup": backup, "verification": verification}


def resume_add_nodes(additional: int, start: bool = False) -> Dict[str, Any]:
    """Option B: keep the current study and raise its target by `additional` nodes."""
    try:
        additional = int(additional)
    except (TypeError, ValueError):
        return {"ok": False, "stage": "VALIDATE", "error": "additional_nodes must be an integer"}
    if additional <= 0:
        return {"ok": False, "stage": "VALIDATE", "error": "additional_nodes must be a positive integer"}
    if additional > MAX_ADDITIONAL_NODES:
        return {"ok": False, "stage": "VALIDATE",
                "error": "additional_nodes must be <= %d" % MAX_ADDITIONAL_NODES}

    with _operation("resume_add_nodes"):
        from ..evolution.engine import get_evo_engine
        from ..orchestrator.lab import get_lab
        from ..orchestrator.pipeline_state import get_pipeline_state_manager

        evo = get_evo_engine()
        psm = get_pipeline_state_manager()
        lab = get_lab()
        try:
            fingerprint_before = _population_fingerprint()
            ctx = _run_context()
            current = int(ctx["run_nodes"]) if ctx["run_nodes"] else counts()["user_research_nodes"]
            if current <= 0:
                return {"ok": False, "stage": "VALIDATE",
                        "error": "no active USER_RESEARCH population to resume (use START FROM ZERO)"}
            new_target = current + additional

            _set_stage("RESUME", "raising target of run %s from %d to %d (+%d)"
                       % (ctx["run_id"], current, new_target, additional))
            evo.set_total_node_target(new_target)                 # existing target machinery
            if psm.run_id:
                psm.resume_run(target=new_target)                 # same study: metadata only
            state_rebuilt = lab.recheck()                         # existing reconstruction
            evo.get_node_generation_state(force=True)

            fingerprint_after = _population_fingerprint()
            preserved = (fingerprint_before["count"] == fingerprint_after["count"]
                         and fingerprint_before["sha256"] == fingerprint_after["sha256"])

            started = False
            if start and preserved:
                res = lab.start(mode="continuous", run_type="resume")
                started = bool(res.get("ok"))
            _set_stage("RESUME", "target set to %d; existing %d nodes preserved (run %s)"
                       % (new_target, fingerprint_after["count"], ctx["run_id"]), "OK")

            result = {
                "ok": True,
                "mode": "resume_add",
                "stage": "DONE",
                "run_id": ctx["run_id"],
                "previous_target": int(ctx["target"]),
                "new_target": int(new_target),
                "existing_nodes": fingerprint_after["count"],
                "additional_nodes_requested": additional,
                "nodes_to_generate": int(max(0, new_target - fingerprint_after["count"])),
                "existing_nodes_preserved": preserved,
                "existing_population_fingerprint": fingerprint_after["sha256"],
                "started": started,
                "legacy_test_nodes": counts()["legacy_test_nodes"],
                "reconstructed_total_nodes": int(state_rebuilt.get("total_nodes", 0)),
                "generation": int(state_rebuilt.get("current_generation", 0)),
            }
            with _op_lock:
                _op["result"] = result
            return result
        except Exception as e:
            log.exception("[RESEARCH-RUN] resume_add failed")
            _set_stage("ERROR", str(e), "FAILED")
            with _op_lock:
                _op["error"] = str(e)
            return {"ok": False, "stage": _op["stage"], "error": str(e)}


# --------------------------------------------------------------------------- #
# Recovery helper (spec §14: "the original research must remain recoverable")
# --------------------------------------------------------------------------- #
def restore_research_backup(path: str | Path, confirm: str) -> Dict[str, Any]:
    """Restore a research backup created by create_research_backup().

    Recovery path for a failed/reset study: replaces the current USER_RESEARCH
    state with the archived one (rows keep their original ids / research numbers
    / statuses / generations). LEGACY_TEST rows are never touched.
    """
    if (confirm or "").strip() != "RESTORE_RESEARCH_BACKUP":
        return {"ok": False, "stage": "CONFIRM",
                "error": "explicit confirmation required: send confirm='RESTORE_RESEARCH_BACKUP'"}

    verification = verify_research_backup(path)
    if not verification.get("ok"):
        return {"ok": False, "stage": "VERIFY", "error": "backup is not restorable",
                "verification": verification}

    db = get_db()
    with _operation("restore_research_backup"):
        try:
            P.ensure_layout()
            tmp = P.BACKUPS_DIR / ".restore_source.db"
            with zipfile.ZipFile(Path(path)) as zf:
                tmp.write_bytes(zf.read("DATABASE/lab_state.db"))
            src = sqlite3.connect(str(tmp))
            try:
                restore_tables = list(STRATEGY_DEPENDENT_TABLES) + ["research_memory", "strategies",
                                                                   "research_runs"]
                before = counts()
                # one atomic transaction: clear the current study, then put the
                # archived study back exactly as it was (ids / numbering / statuses).
                # `OR REPLACE` so archived rows stay authoritative for their keys.
                statements = list(_reset_statements())
                inserted = {}
                for tbl in restore_tables:
                    cols = [r[1] for r in src.execute("PRAGMA table_info(%s)" % tbl)]
                    rows = src.execute("SELECT %s FROM %s" % (", ".join(cols), tbl)).fetchall()
                    if rows:
                        statements.append(("INSERT OR REPLACE INTO %s (%s) VALUES (%s)"
                                           % (tbl, ", ".join(cols), ", ".join("?" * len(cols))), list(rows)))
                    inserted[tbl] = len(rows)
                db.transaction(statements)
            finally:
                src.close()
                tmp.unlink()
            after = counts()
            return {"ok": True, "stage": "DONE", "restored": inserted, "before": before,
                    "after": after, "counts": after,
                    "legacy_untouched": before["legacy_test_nodes"] == after["legacy_test_nodes"]}
        except Exception as e:
            log.exception("[RESEARCH-RUN] restore failed")
            return {"ok": False, "stage": "ERROR", "error": str(e)}
