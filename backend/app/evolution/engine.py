"""
Evolution Engine — population control and reproduction (spec §7, §8).

Fixed population with configurable composition:
  elite preservation / mutation / crossover / random exploration.

Selection is diversity-aware: elites are capped per species (timeframe +
direction + indicator-family signature) so a single family cannot dominate,
and a lower-profit but structurally different strategy can survive.

Duplicate genome detection: canonical-JSON hash; identical genomes are never
re-tested (counted as duplicates_blocked).

Full ancestry is kept in SQLite (parent_id, generation, mutation_type,
creation_reason, origin, hypothesis_id).
"""
from __future__ import annotations

import logging
import os
import random
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..activity import activity
from ..config import config_digest, get_config
from ..data.engine import get_data_engine
from ..db.database import get_db
from ..genome import ops as gops
from ..genome.schema import (GenomeError, describe, genome_hash, species_key,
                             validate_genome)
from ..research import export
from ..versions import APP_VERSION

log = logging.getLogger("evolution.engine")

# V4.1: the LEGACY_TEST *infrastructure population* - the rows the project's own
# classification marks as legacy (``data_source``) that also belong to the
# historical/test runs (verified: 0 rows disagree on the shipped DATA). These rows
# must never be retired, deleted or re-scored by the research engine. Rows that
# merely carry the LEGACY_TEST label because the engine had no active run bound
# when they were created are research candidates and keep the pre-existing rules.
LEGACY_POPULATION_SQL = (
    "COALESCE(data_source, 'USER_RESEARCH') = 'LEGACY_TEST' "
    "AND (id <= 787 OR run_id IS NULL OR run_id LIKE '%TEST%' OR run_id = 'RUN-HISTORICAL-PRESERVED')"
)

ACTIVE_STATES = (
    "BORN", "GENERATED", "QUEUED",
    "BACKTESTING", "TESTING",
    "SURVIVED", "EVALUATED",
    "VALIDATING",
)
IN_FLIGHT_STATES = ACTIVE_STATES

QUALIFIED_STATES = (
    "QUALIFIED", "PAPER_ELIGIBLE",
    "PAPER", "PAPER_TRADING", "PAPER_PASSED", "FINAL",
)

TERMINAL_STATES = (
    "FAILED", "KILLED", "PAPER_FAILED", "RETIRED",
)

DURABLE_STATES = (
    "GENERATED", "QUEUED", "TESTING", "EVALUATED",
    "QUALIFIED", "PAPER_ELIGIBLE", "PAPER_TRADING",
    "PAPER_PASSED", "PAPER_FAILED", "FINAL",
)

_evo_engine: Optional["EvolutionEngine"] = None


def get_evo_engine(db=None) -> "EvolutionEngine":
    global _evo_engine
    if _evo_engine is None:
        _evo_engine = EvolutionEngine(db or get_db())
    elif db is not None:
        _evo_engine.db = db
    return _evo_engine


class EvolutionEngine:
    def __init__(self, db=None, run_id: Optional[str] = None):
        global _evo_engine
        self.db = db or get_db()
        _evo_engine = self
        self.active_run_id = run_id

        # Persist evolution random seed in meta table (spec §42)
        stored_seed = self.db.get_meta("evolution_seed")
        if stored_seed:
            try:
                base_seed = int(stored_seed)
            except ValueError:
                base_seed = 20250925
        else:
            base_seed = 20250925
            self.db.set_meta("evolution_seed", str(base_seed))
        self._base_seed = base_seed
        self._rng = random.Random(base_seed)
        self.duplicates_blocked = 0
        self._cached_node_state: Optional[Dict[str, Any]] = None
        self._cached_node_state_time: float = 0.0
        # V4.0: separate cache slot for the legacy-excluded display snapshot
        self._cached_node_state_user: Optional[Dict[str, Any]] = None
        self._cached_node_state_user_time: float = 0.0
        self._entropy_counter: int = 0

    # ---------- total node target & bookkeeping (spec §V2.1 D-H) ----------
    def get_total_node_target(self) -> int:
        stored = self.db.get_meta("total_node_target")
        if stored:
            try:
                val = int(stored)
                if val > 0:
                    return val
            except ValueError:
                pass
        cfg = get_config()
        t = getattr(cfg.evolution, "total_node_target", None)
        if t and t > 0:
            return int(t)
        return 1000

    def set_total_node_target(self, target: int) -> int:
        target = max(1, int(target))
        self.db.set_meta("total_node_target", str(target))
        cfg = get_config()
        if hasattr(cfg.evolution, "total_node_target"):
            cfg.evolution.total_node_target = target
        return target

    def total_nodes(self, scope_run: Optional[bool] = None, exclude_legacy: bool = False) -> int:
        if scope_run is False:
            return self.db.total_strategies_count(exclude_legacy=exclude_legacy)
        if self.active_run_id:
            return self.db.total_strategies_count(run_id=self.active_run_id, exclude_legacy=exclude_legacy)
        return self.db.total_strategies_count(exclude_legacy=exclude_legacy)

    def run_nodes(self, run_id: Optional[str] = None, exclude_legacy: bool = False) -> int:
        rid = run_id or self.active_run_id
        if rid:
            return self.db.total_strategies_count(run_id=rid, exclude_legacy=exclude_legacy)
        return self.db.total_strategies_count(exclude_legacy=exclude_legacy)

    def all_persisted_nodes(self, exclude_legacy: bool = False) -> int:
        return self.db.total_strategies_count(exclude_legacy=exclude_legacy)

    def remaining_nodes(self) -> int:
        target = self.get_total_node_target()
        cur = self.run_nodes() if self.active_run_id else self.total_nodes()
        return max(0, target - cur)

    def is_target_reached(self) -> bool:
        target = self.get_total_node_target()
        cur = self.run_nodes() if self.active_run_id else self.total_nodes()
        return cur >= target

    def node_accounting(self, target: Optional[int] = None, exclude_legacy: bool = False) -> Dict[str, int]:
        """Audit all 9 distinct node metrics (User Spec §4, §9).

        exclude_legacy (V4.0): scope the strategy status counts to the user
        research population. Backtest/validation row counters are unchanged.
        """
        target = target or self.get_total_node_target()
        total_created = self.total_nodes(exclude_legacy=exclude_legacy)
        counts = self.db.count_by_status(exclude_legacy=exclude_legacy)
        eval_row = self.db.one("SELECT COUNT(DISTINCT strategy_id) e FROM backtests")
        bt_row = self.db.one("SELECT COUNT(*) b FROM backtests WHERE stage IN ('screen', 'detail')")
        val_row = self.db.one("SELECT COUNT(*) v FROM validations")

        total_eval = int(eval_row["e"]) if eval_row else 0
        total_bt = int(bt_row["b"]) if bt_row else 0
        total_val = int(val_row["v"]) if val_row else 0
        total_qual = sum(counts.get(s, 0) for s in QUALIFIED_STATES)
        total_rej = sum(counts.get(s, 0) for s in ("FAILED", "KILLED", "PAPER_FAILED"))
        total_dead = total_rej + counts.get("RETIRED", 0)
        total_active = sum(counts.get(s, 0) for s in IN_FLIGHT_STATES)
        total_rem = max(0, target - total_created)

        return {
            "TOTAL CREATED": total_created,
            "TOTAL EVALUATED": total_eval,
            "TOTAL BACKTESTED": total_bt,
            "TOTAL VALIDATED": total_val,
            "TOTAL QUALIFIED": total_qual,
            "TOTAL REJECTED": total_rej,
            "TOTAL DEAD": total_dead,
            "TOTAL ACTIVE": total_active,
            "TOTAL REMAINING": total_rem,
        }

    def get_node_generation_state(self, force: bool = False, exclude_legacy: bool = True) -> Dict[str, Any]:
        """Authoritative single source of truth for node generation metrics (User Spec §3, §9).

        The snapshot describes the USER RESEARCH population: the ~787 LEGACY_TEST
        infrastructure records are excluded by default (V4.0 display scope), because
        every consumer of this snapshot is a research/dashboard surface (Overview,
        Live Activity, watchdog, pipeline state, milestones). Pass
        exclude_legacy=False for the raw database-wide view.

        The metric formulas, status classification and the engine's own decision
        gates (is_target_reached() / total_nodes() / remaining_nodes(), which use the
        run-scoped node counts) are unchanged: this only controls the population
        scope of the reported statistics. The two scopes are cached in separate
        slots, so a legacy-excluded display call can never leak into a
        database-wide reader (and vice versa) through the shared snapshot cache.
        """
        now = time.time()
        cached = self._cached_node_state_user if exclude_legacy else self._cached_node_state
        cached_at = self._cached_node_state_user_time if exclude_legacy else self._cached_node_state_time
        if not force and cached is not None and (now - cached_at < 1.0):
            return dict(cached)

        counts = self.db.count_by_status(exclude_legacy=exclude_legacy)
        total_nodes = self.total_nodes(exclude_legacy=exclude_legacy)
        target = self.get_total_node_target()
        remaining = max(0, target - total_nodes)
        gen = self.generation()

        # Alive: In-flight or permanent qualified / paper trading nodes
        alive = sum(counts.get(s, 0) for s in (IN_FLIGHT_STATES + QUALIFIED_STATES))
        dead = sum(counts.get(s, 0) for s in TERMINAL_STATES)
        backtesting = counts.get("BACKTESTING", 0) + counts.get("TESTING", 0)
        validating = counts.get("VALIDATING", 0)
        qualified = sum(counts.get(s, 0) for s in QUALIFIED_STATES)
        pending = sum(counts.get(s, 0) for s in IN_FLIGHT_STATES)

        try:
            from ..orchestrator.stages import get_stage_manager
            sm = get_stage_manager()
            current_stage = str(sm.current_stage)
        except Exception:
            current_stage = "EVOLUTION"

        legacy_sql = " AND COALESCE(data_source, 'USER_RESEARCH') <> 'LEGACY_TEST'" if exclude_legacy else ""
        last_row = self.db.one("SELECT MAX(created_at) t FROM strategies WHERE 1=1" + legacy_sql)
        last_node_created_at = float(last_row["t"]) if (last_row and last_row["t"]) else 0.0

        gen_stat_row = self.db.one("SELECT MAX(ts) t FROM generation_stats")
        last_gen_at = float(gen_stat_row["t"]) if (gen_stat_row and gen_stat_row["t"]) else last_node_created_at

        # Nodes per minute (calculated from recent 5 minute window)
        recent_row = self.db.one(
            "SELECT COUNT(*) c FROM strategies WHERE created_at > ?" + legacy_sql, (now - 300,))
        recent_count = int(recent_row["c"]) if recent_row else 0
        nodes_per_minute = round(recent_count / 5.0, 1)

        is_complete = total_nodes >= target
        generation_status = "COMPLETE" if is_complete else ("ACTIVE" if pending > 0 or not is_complete else "IDLE")
        pct = round((total_nodes / target) * 100, 1) if target > 0 else 100.0

        # Query last success and last failure rows
        last_succ_row = self.db.one(
            "SELECT id, updated_at FROM strategies WHERE status IN ('QUALIFIED', 'SURVIVED', 'PAPER') ORDER BY updated_at DESC LIMIT 1"
        )
        last_fail_row = self.db.one(
            "SELECT id, updated_at FROM strategies WHERE status IN ('FAILED', 'KILLED') ORDER BY updated_at DESC LIMIT 1"
        )
        last_success_str = (
            f"Node #{last_succ_row['id']} at {datetime.fromtimestamp(float(last_succ_row['updated_at']), tz=timezone.utc).strftime('%H:%M:%S UTC')}"
            if (last_succ_row and last_succ_row.get("updated_at")) else "None"
        )
        last_failure_str = (
            f"Node #{last_fail_row['id']} at {datetime.fromtimestamp(float(last_fail_row['updated_at']), tz=timezone.utc).strftime('%H:%M:%S UTC')}"
            if (last_fail_row and last_fail_row.get("updated_at")) else "None"
        )

        # Parent and task statistics
        eligible_parents = len(self.db.q(
            f"SELECT id FROM strategies WHERE status IN {tuple(QUALIFIED_STATES + ('SURVIVED', 'EVALUATED', 'VALIDATING'))}"
        ))
        completed_tasks = counts.get("QUALIFIED", 0) + counts.get("PAPER", 0) + counts.get("RETIRED", 0) + counts.get("KILLED", 0) + counts.get("FAILED", 0)
        rejected_tasks = counts.get("KILLED", 0) + counts.get("RETIRED", 0)
        duplicate_tasks = counts.get("DUPLICATE", 0)
        failed_tasks = counts.get("FAILED", 0)
        children_generated = counts.get("BORN", 0) + backtesting
        children_rejected = counts.get("KILLED", 0)
        stop_cond = "TARGET_REACHED" if is_complete else "NONE"

        from ..orchestrator.pipeline_state import get_pipeline_state_manager
        psm = get_pipeline_state_manager()
        active_workers = getattr(psm, "worker_status", "ACTIVE")
        run_id = getattr(psm, "run_id", "RUN-CLEAN-MT5")

        from ..data.discovery import get_discovery_engine
        de = get_discovery_engine()
        rep = de.get_startup_status_report(self.db, force_refresh=False)
        raw_validated = rep["data"]["valid_datasets"] > 0
        feat_validated = rep["data"]["feature_sets"] > 0
        data_source = rep["data"].get("data_source", "MT5 REAL" if raw_validated else "NONE")

        detailed_diagnostics = {
            "TARGET NODES": target,
            "CURRENT PERSISTED NODES": self.all_persisted_nodes(),
            "CURRENT GENERATED NODES": total_nodes,
            "REMAINING NODES": remaining,
            "CURRENT GENERATION": gen,
            "ACTIVE WORKERS": active_workers,
            "PENDING TASKS": pending,
            "COMPLETED TASKS": completed_tasks,
            "REJECTED TASKS": rejected_tasks,
            "DUPLICATE TASKS": duplicate_tasks,
            "FAILED TASKS": failed_tasks,
            "ELIGIBLE PARENTS": eligible_parents,
            "CHILDREN GENERATED": children_generated,
            "CHILDREN REJECTED": children_rejected,
            "STOP CONDITION": stop_cond,
            "DATA SOURCE": data_source,
            "RUN ID": run_id,
            "RAW DATA VALIDATED": raw_validated,
            "FEATURES VALIDATED": feat_validated,
            "LAST NODE SUCCESS": last_success_str,
            "LAST NODE FAILURE": last_failure_str,
        }

        detailed_diagnostics["CURRENT PERSISTED NODES"] = self.all_persisted_nodes(exclude_legacy=exclude_legacy)

        res = {
            "current_nodes": total_nodes,
            "run_nodes": self.run_nodes(exclude_legacy=exclude_legacy),
            "all_persisted_nodes": self.all_persisted_nodes(exclude_legacy=exclude_legacy),
            "target_nodes": target,
            "remaining_nodes": remaining,
            "generation_number": gen,
            "alive_nodes": alive,
            "dead_nodes": dead,
            "backtesting_nodes": backtesting,
            "validating_nodes": validating,
            "qualified_nodes": qualified,
            "pending_nodes": pending,
            "generation_status": generation_status,
            "current_stage": current_stage,
            "nodes_per_minute": nodes_per_minute,
            "last_node_created_at": last_node_created_at,
            "last_successful_generation_at": last_gen_at,
            "progress_pct": pct,
            "is_target_reached": is_complete,
            "node_accounting": self.node_accounting(target, exclude_legacy=exclude_legacy),
            "diagnostics": detailed_diagnostics,
            # Flattened upper-case keys for direct diagnostic indexing
            **detailed_diagnostics,
        }
        if exclude_legacy:
            self._cached_node_state_user = res
            self._cached_node_state_user_time = now
        else:
            self._cached_node_state = res
            self._cached_node_state_time = now
        return res

    def emit_stop_diagnostic(self, reason: str = "STOPPED", exception: Optional[Exception] = None,
                             recovery_action: str = "", stop_reason: Optional[str] = None) -> List[str]:
        """Explicitly log why node generation stopped before target (User Spec §3)."""
        effective_reason = stop_reason or reason
        target = self.get_total_node_target()
        cur = self.total_nodes()
        rem = max(0, target - cur)
        gen = self.generation()

        last_succ = self.db.one(
            "SELECT id FROM strategies WHERE status IN ('QUALIFIED', 'SURVIVED') ORDER BY id DESC LIMIT 1"
        )
        last_failed = self.db.one(
            "SELECT id, failure_reason FROM strategies WHERE status IN ('FAILED', 'KILLED') ORDER BY id DESC LIMIT 1"
        )
        queue_cnt = self.db.one(
            "SELECT COUNT(*) c FROM strategies WHERE status IN ('BORN', 'BACKTESTING')"
        )["c"]

        from ..resources import get_resource_manager
        rm = get_resource_manager()
        eff_workers = rm.effective_workers()

        succ_str = f"Node_{last_succ['id']}" if last_succ else "None"
        fail_str = f"Node_{last_failed['id']}" if last_failed else "None"
        if last_failed and last_failed.get("failure_reason"):
            fail_str += f" ({last_failed['failure_reason'][:80]})"
        exc_str = str(exception) if exception else "None"
        rec_str = recovery_action or ("Target reached" if cur >= target else "Resume research loop or check worker pool")

        diag_lines = [
            f"[NODE ENGINE] STOP REASON: {effective_reason}",
            f"CURRENT: {cur}",
            f"TARGET: {target}",
            f"REMAINING: {rem}",
            f"GENERATION: {gen}",
            f"QUEUE: {queue_cnt}",
            f"ACTIVE WORKERS: {eff_workers}",
            f"LAST SUCCESSFUL NODE: {succ_str}",
            f"LAST FAILED NODE: {fail_str}",
            f"EXCEPTION: {exc_str}",
            f"RECOVERY ACTION: {rec_str}",
        ]
        diag = "\n" + "\n".join(diag_lines)
        log.warning(diag)
        try:
            from ..activity import activity
            activity.warning("EVOLUTION", f"STOP: {effective_reason} | {cur}/{target} nodes",
                             details={"diagnostic": diag})
        except Exception:
            pass
        return diag_lines

    def _emit_target_reached(self, cur: int, target: int) -> None:
        log.info("[NODE] Target=%d Current=%d Remaining=0", target, cur)
        log.info("[NODE] TARGET REACHED")
        try:
            from ..activity import activity
            activity.success("EVOLUTION", f"[NODE] TARGET REACHED: Total nodes ({cur}) reached target ceiling ({target}).")
        except Exception:
            pass
        try:
            from ..api.ws import bus
            bus.publish("NODE_TARGET_REACHED", {
                "total_nodes": cur,
                "target": target,
                "remaining": 0,
            })
        except Exception:
            pass

    # ---------- population bookkeeping ----------
    def active_count(self, exclude_legacy: bool = False) -> int:
        """Count in-flight (active) nodes. exclude_legacy (V4.0) omits the
        LEGACY_TEST infrastructure records (user-facing displays and the V4.1
        population cap, which must not touch legacy nodes)."""
        where = f"status IN ({','.join('?' * len(ACTIVE_STATES))})"
        if exclude_legacy:
            where += " AND NOT (" + LEGACY_POPULATION_SQL + ")"
        row = self.db.one(f"SELECT COUNT(*) c FROM strategies WHERE {where}", ACTIVE_STATES)
        return row["c"] if row else 0

    def generation(self) -> int:
        row = self.db.one("SELECT MAX(generation) g FROM strategies")
        return int(row["g"] or 0)

    def seed_population(self, n: int, symbol: str) -> int:
        """Create generation-0 random genomes (BORN) with dynamic entropy duplicate protection."""
        cfg = get_config()
        target = self.get_total_node_target()
        cur = self.total_nodes()
        remaining = max(0, target - cur)
        if remaining <= 0:
            self._emit_target_reached(cur, target)
            return 0
        n = min(n, remaining)

        created = 0
        attempts = 0
        max_attempts = max(n * 20, 200)

        avail_tfs = get_data_engine().get_available_timeframes(symbol)
        while created < n and attempts < max_attempts and not self.is_target_reached():
            attempts += 1
            chosen_tf = self._rng.choice(avail_tfs) if avail_tfs else cfg.data.timeframe
            g = gops.random_genome(symbol, self._rng, cfg.evolution.max_indicators, timeframe=chosen_tf)
            sid = self.try_insert(g, parent_id=None, generation=0, status="BORN",
                                  origin="seed", mutation_type="random_exploration",
                                  creation_reason="generation-0 random seed")
            if sid is not None:
                created += 1
            else:
                # Collision detected: inject dynamic entropy/salt to escape deterministic duplicate loop
                self._entropy_counter += 1
                salt = (int(time.time() * 1000) ^ os.getpid() ^ (self._entropy_counter << 16)) & 0x7FFFFFFF
                self._rng = random.Random(salt)
        return created

    def try_insert(self, genome: Dict, parent_id: Optional[int], generation: int,
                   status: str = "BORN", origin: str = "evolution",
                   mutation_type: str = "", creation_reason: str = "",
                   hypothesis_id: Optional[int] = None,
                   mutation_params: Optional[Dict] = None) -> Optional[int]:
        """Validate, dedupe, insert. Returns strategy id or None (duplicate/invalid)."""
        # Global Total Node Target ceiling check (V4.1: the configured target is
        # the authoritative node ceiling. The earlier hard-coded 10,000 boundary
        # made an approved "resume + add nodes" target above 10,000 impossible.)
        target = self.get_total_node_target()
        cur_nodes = self.total_nodes()
        if cur_nodes >= target:
            self._emit_target_reached(cur_nodes, target)
            log.info("[CEILING REACHED] Node ceiling reached (%d / %d). Node creation halted.", cur_nodes, target)
            return None

        cfg = get_config()
        try:
            validate_genome(genome, cfg.evolution.max_indicators_hard,
                            cfg.evolution.max_conditions + 4,
                            cfg.evolution.max_condition_depth + 1)
        except GenomeError as e:
            log.debug("genome rejected: %s", e)
            return None
        h = genome_hash(genome)
        existing = self.db.find_by_hash(h)
        if existing:
            self.duplicates_blocked += 1
            return None
        from ..genome.schema import complexity
        _, _, cscore = complexity(genome)

        # Generate per-birth deterministic seed (spec §42)
        birth_seed = self._rng.randint(1, 2_000_000_000)

        record = {
            "hash": h, "parent_id": parent_id, "generation": generation,
            "symbol": genome["symbol"], "timeframe": genome["timeframe"],
            "direction": genome.get("direction", "both"), "status": status,
            "creation_reason": creation_reason, "mutation_type": mutation_type,
            "species_key": species_key(genome), "genome": genome,
            "complexity": cscore, "origin": origin, "hypothesis_id": hypothesis_id,
            "seed": birth_seed,
            "mutation_params": __import__("json").dumps(mutation_params) if mutation_params else None,
            "config_version": config_digest(),
            "software_version": APP_VERSION,
            "run_id": self.active_run_id or "RUN-20260927-CLEAN-MT5",
            "data_source": "USER_RESEARCH" if (self.active_run_id and "TEST" not in self.active_run_id.upper() and "HISTORICAL" not in self.active_run_id.upper()) else "LEGACY_TEST",
        }
        sid = self.db.insert_strategy(record)

        # Invalidate cached node state so next poll gets fresh state
        self._cached_node_state = None
        self._cached_node_state_time = 0.0
        self._cached_node_state_user = None
        self._cached_node_state_user_time = 0.0

        # Mirror to RESEARCH/strategies/ (spec §10)
        try:
            export.export_strategy(sid, {**record, "id": sid})
        except Exception as e:
            log.warning("failed to export strategy #%s: %s", sid, e)

        # Explicit node-generation state logging (User Spec §2)
        cur_after = cur_nodes + 1
        rem_after = max(0, target - cur_after)
        parent_label = f"Node_{parent_id}" if parent_id else "None"
        log.info("[NODE] Target=%d Current=%d Remaining=%d Generation=%d", target, cur_after, rem_after, generation)
        if parent_id:
            log.info("[NODE] Parent=%s", parent_label)
        log.info("[NODE] Mutation=SUCCESS (%s)", mutation_type or "seed")
        log.info("[NODE] Child=Node_%d", sid)
        log.info("[NODE] Backtest=QUEUED")

        # Check if node target ceiling reached after insertion
        if cur_nodes + 1 >= target:
            self._emit_target_reached(cur_nodes + 1, target)

        return sid

    # ---------- selection ----------
    def elites(self, n: int) -> List[Dict]:
        """Diversity-aware elite selection among tested survivors."""
        rows = self.db.q(
            f"""SELECT * FROM strategies
               WHERE status IN {tuple(QUALIFIED_STATES + ('SURVIVED', 'EVALUATED', 'VALIDATING'))}
                 AND fitness IS NOT NULL
               ORDER BY fitness DESC LIMIT ?""", (n * 8,))
        cfg = get_config()
        cap = max(1, int(n * cfg.evolution.species_cap_pct))
        per_species: Dict[str, int] = {}
        chosen: List[Dict] = []
        for r in rows:
            sp = r["species_key"] or "?"
            if per_species.get(sp, 0) >= cap:
                continue
            per_species[sp] = per_species.get(sp, 0) + 1
            r["genome"] = __import__("json").loads(r["genome"])
            chosen.append(r)
            if len(chosen) >= n:
                break
        return chosen

    def _tournament_parent(self, pool: List[Dict]) -> Dict:
        cfg = get_config()
        k = min(cfg.evolution.tournament_size, len(pool))
        cands = self._rng.sample(pool, k)
        # fitness-primary, small diversity bonus for rarer species
        counts: Dict[str, int] = {}
        for r in pool:
            counts[r["species_key"]] = counts.get(r["species_key"], 0) + 1
        def score(r):
            rarity = 1.0 / (1.0 + counts.get(r["species_key"], 1))
            return (r["fitness"] or 0.0) + 0.05 * rarity
        return max(cands, key=score)

    # ---------- reproduction ----------
    def log_selection_basis(self) -> None:
        cfg = get_config()
        w = cfg.fitness.weights
        msg = (
            "[SELECTION BASIS]\n"
            "  Fitness formula: Multi-objective weighted blend of 8 normalized metrics (profitability, risk-adjusted, drawdown, profit_factor, consistency, oos, robustness, complexity penalty) with expectancy damping (*0.25 if <=0)\n"
            f"  Return weight: {w.get('profitability', 0.2):.2f}\n"
            f"  Drawdown penalty: {w.get('drawdown', 0.15):.2f} (hard limit: {cfg.fitness.max_drawdown_pct*100:.0f}%)\n"
            f"  Trade count requirement: screen >= {cfg.backtest.screen_min_trades}, detail >= {cfg.fitness.min_trades}\n"
            f"  Validation requirement: min_trade_duration >= {getattr(cfg.backtest, 'min_trade_duration_seconds', 120)}s, robustness >= 0.60, walk-forward majority positive, MC > 30%\n"
            f"  Other constraints: max indicators {cfg.evolution.max_indicators_hard}, tournament size {cfg.evolution.tournament_size}, species cap {cfg.evolution.species_cap_pct*100:.0f}%"
        )
        log.info(msg)

    def reproduce(self, target_births: int, symbol: str,
                  pct_override: Optional[Dict[str, float]] = None) -> Dict[str, int]:
        """Fill the population: elite children via mutation/crossover + exploration."""
        self.log_selection_basis()
        target = self.get_total_node_target()
        cur = self.total_nodes()
        remaining = max(0, target - cur)
        if remaining <= 0:
            self._emit_target_reached(cur, target)
            return {"mutation": 0, "crossover": 0, "exploration": 0, "duplicate": 0,
                    "invalid": 0, "target_reached": True}
        target_births = min(target_births, remaining)

        start_node = cur + 1
        end_node = cur + target_births
        log.info("[NODE] Target=%d Current=%d Remaining=%d Generation=%d",
                 target, cur, remaining, self.generation() + 1)
        log.info("[NODE] Generating nodes %d to %d (%d node batch)", start_node, end_node, target_births)
        activity.running("EVOLUTION", f"Generating nodes {start_node} to {end_node} (0 / {target_births})",
                         progress=round((cur / target) * 100, 1),
                         details={"start": start_node, "end": end_node, "current": 0, "total": target_births})

        cfg = get_config().evolution
        mut_pct = (pct_override or {}).get("mutation", cfg.mutation_pct)
        cross_pct = (pct_override or {}).get("crossover", cfg.crossover_pct)
        expl_pct = (pct_override or {}).get("exploration", cfg.exploration_pct)
        counts = {"mutation": 0, "crossover": 0, "exploration": 0, "duplicate": 0,
                  "invalid": 0}
        total_pct = mut_pct + cross_pct + expl_pct
        if total_pct <= 0:
            total_pct = 1.0
        n_mut = int(round(target_births * mut_pct / total_pct))
        n_cross = int(round(target_births * cross_pct / total_pct))
        n_expl = max(0, target_births - n_mut - n_cross)

        pool = self.elites(max(20, int(cfg.population_size * 0.15)))

        def _report_tick(new_sid):
            c_tot = counts["mutation"] + counts["crossover"] + counts["exploration"]
            is_done = (c_tot >= target_births)
            tick_msg = f"Generating nodes {start_node} to {end_node} ({c_tot} / {target_births}{' ✓' if is_done else ''})"
            log.info("[NODE] %s", tick_msg)
            activity.running("EVOLUTION", tick_msg,
                             progress=round(((cur + c_tot) / target) * 100, 1),
                             details={"start": start_node, "end": end_node, "current": c_tot, "total": target_births, "node_id": new_sid})

        if pool:
            for _ in range(n_mut * 5):
                if counts["mutation"] >= n_mut or self.is_target_reached():
                    break
                parent = self._tournament_parent(pool)
                try:
                    child, mt, desc = gops.mutate(parent["genome"], self._rng,
                                                  max_indicators=cfg.max_indicators_hard)
                except GenomeError as ge:
                    log.warning("INVALID CANDIDATE\nREASON: %s", ge)
                    counts["invalid"] += 1
                    continue
                sid = self.try_insert(child, parent["id"], parent["generation"] + 1,
                                      origin="mutation", mutation_type=mt,
                                      creation_reason=f"mutation of #{parent['id']}: {desc}")
                if sid is None:
                    counts["duplicate"] += 1
                else:
                    counts["mutation"] += 1
                    _report_tick(sid)
            for _ in range(n_cross * 5):
                if counts["crossover"] >= n_cross or self.is_target_reached():
                    break
                p1 = self._tournament_parent(pool)
                p2 = self._tournament_parent(pool)
                if p1["id"] == p2["id"]:
                    continue
                try:
                    child, mt, desc = gops.crossover(p1["genome"], p2["genome"],
                                                     self._rng, cfg.max_indicators_hard)
                except GenomeError as ge:
                    log.warning("INVALID CANDIDATE\nREASON: %s", ge)
                    counts["invalid"] += 1
                    continue
                sid = self.try_insert(child, p1["id"], max(p1["generation"], p2["generation"]) + 1,
                                      origin="crossover", mutation_type=mt,
                                      creation_reason=f"crossover of #{p1['id']} x #{p2['id']}: {desc}")
                if sid is None:
                    counts["duplicate"] += 1
                    self._entropy_counter += 1
                    salt = (int(time.time() * 1000) ^ os.getpid() ^ (self._entropy_counter << 16)) & 0x7FFFFFFF
                    self._rng = random.Random(salt)
                else:
                    counts["crossover"] += 1
                    _report_tick(sid)
        else:
            n_expl = target_births   # no viable pool yet -> explore

        # Initial exploration allotment
        avail_tfs = get_data_engine().get_available_timeframes(symbol)
        cur_gen = self.generation()
        for _ in range(n_expl * 5):
            if counts["exploration"] >= n_expl or self.is_target_reached():
                break
            chosen_tf = self._rng.choice(avail_tfs) if avail_tfs else None
            g = gops.random_genome(symbol, self._rng, cfg.max_indicators, timeframe=chosen_tf)
            sid = self.try_insert(g, None, cur_gen + 1, origin="exploration",
                                  mutation_type="random_exploration",
                                  creation_reason=f"Gen {cur_gen + 1} random exploration birth")
            if sid is None:
                counts["duplicate"] += 1
                self._entropy_counter += 1
                salt = (int(time.time() * 1000) ^ os.getpid() ^ (self._entropy_counter << 16)) & 0x7FFFFFFF
                self._rng = random.Random(salt)
            else:
                counts["exploration"] += 1
                _report_tick(sid)

        # Shortfall guarantee: if duplicates prevented filling the quota, fill remainder via exploration
        total_created = counts["mutation"] + counts["crossover"] + counts["exploration"]
        shortfall = max(0, target_births - total_created)
        if shortfall > 0 and not self.is_target_reached():
            log.warning("[WARNING] EVOLUTION: Generation %d candidate pool exhausted", cur_gen)
            log.info("[INFO] EVOLUTION: %d / %d nodes currently persisted", cur, target)
            log.info("[INFO] EVOLUTION: %d nodes still required", remaining)
            log.info("[INFO] EVOLUTION: Creating next candidate generation")

            next_gen = max(1, cur_gen + 1)
            for _ in range(shortfall * 20):
                if counts["mutation"] + counts["crossover"] + counts["exploration"] >= target_births or self.is_target_reached():
                    break
                chosen_tf = self._rng.choice(avail_tfs) if avail_tfs else None
                g = gops.random_genome(symbol, self._rng, cfg.max_indicators, timeframe=chosen_tf)
                sid = self.try_insert(g, None, next_gen, origin="exploration",
                                      mutation_type="random_exploration",
                                      creation_reason=f"Gen {next_gen} candidate exploration (filling shortfall)")
                if sid is None:
                    counts["duplicate"] += 1
                    self._entropy_counter += 1
                    salt = (int(time.time() * 1000) ^ os.getpid() ^ (self._entropy_counter << 16)) & 0x7FFFFFFF
                    self._rng = random.Random(salt)
                else:
                    counts["exploration"] += 1
                    _report_tick(sid)

        total_created = counts["mutation"] + counts["crossover"] + counts["exploration"]
        if total_created > 0:
            cur_after = self.total_nodes()
            log.info("[SUCCESS] Persisted nodes %d to %d (Total nodes: %d / %d)",
                     start_node, start_node + total_created - 1, cur_after, target)
            activity.success(
                "EVOLUTION",
                f"[SUCCESS] Persisted nodes {start_node} to {start_node + total_created - 1} (Total nodes: {cur_after} / {target})",
                progress=round((cur_after / target) * 100, 1),
                details={"batch_size": total_created, "current": cur_after, "target": target}
            )

        return counts

    def spawn_child(self, genome: Dict, parent_id: Optional[int], mutation_type: str,
                    reason: str, hypothesis_id: Optional[int] = None) -> Optional[int]:
        """Directed child (specialization / AI hypothesis).

        Dead node rule (spec §35, §V2.1 J): A dead strategy (FAILED/KILLED/RETIRED) cannot create NEW children.
        """
        # Node target ceiling gate (spec §V2.1 H)
        if self.is_target_reached():
            self._emit_target_reached(self.total_nodes(), self.get_total_node_target())
            return None

        parent = self.db.get_strategy(parent_id) if parent_id else None
        if parent and parent["status"] in ("FAILED", "KILLED", "RETIRED"):
            log.warning("cannot spawn child from dead strategy #%s (status=%s)", parent_id, parent["status"])
            return None

        gen = (parent["generation"] + 1) if parent else self.generation() + 1
        return self.try_insert(genome, parent_id, gen, origin="hypothesis"
                               if hypothesis_id else "specialization",
                               mutation_type=mutation_type, creation_reason=reason,
                               hypothesis_id=hypothesis_id)

    # ---------- population cap ----------
    def enforce_population_cap(self, symbol: str) -> int:
        """Retire lowest-fitness non-elite actives beyond population_size.

        Only unvalidated active survivor candidates ('SURVIVED') in the generation pool
        are capped. BORN candidates, candidates in evaluation ('BACKTESTING', 'VALIDATING'),
        and permanent research achievements ('QUALIFIED', 'PAPER') must NEVER be retired by population cap.

        V4.1: the LEGACY_TEST infrastructure nodes are not research candidates and
        are never retired/status-changed by the cap (same classification as V4.0).
        """
        cfg = get_config().evolution
        active_cnt = self.active_count(exclude_legacy=True)
        excess = active_cnt - cfg.population_size
        if excess <= 0:
            return 0
        survivors = self.db.q(
            """SELECT id, fitness, status, species_key FROM strategies
               WHERE status='SURVIVED' AND fitness IS NOT NULL
                 AND NOT (" + LEGACY_POPULATION_SQL + ")"""
        )
        if not survivors:
            return 0
        survivors.sort(key=lambda a: a["fitness"] or 0.0)
        retired = 0
        for a in survivors[:excess]:
            self.db.update_strategy(a["id"], status="RETIRED")
            retired += 1
        return retired

    def clear_failed(self) -> int:
        """Safely clear failed strategies without destroying ancestry (spec §14, §35).

        Only deletes leaf nodes with no children. Ancestors of surviving or
        historical strategies are permanently preserved.

        V4.1: LEGACY_TEST infrastructure rows are never deleted by this control.
        """
        rows = self.db.q(
            """SELECT id FROM strategies
               WHERE status IN ('FAILED','KILLED')
                 AND NOT (""" + LEGACY_POPULATION_SQL + """)
                 AND id NOT IN (SELECT DISTINCT parent_id FROM strategies WHERE parent_id IS NOT NULL)"""
        )
        for r in rows:
            self.db.x("DELETE FROM strategies WHERE id=?", (r["id"],))
        self.db.x("DELETE FROM backtests WHERE strategy_id NOT IN (SELECT id FROM strategies)")
        log.info("clear_failed pruned %d leaf failed strategies; ancestor lineage preserved", len(rows))
        return len(rows)

    def reset_generation(self, symbol: str) -> Dict[str, int]:
        """Retire everything and reseed generation 0 (research reset).

        V4.1: the retirement sweep applies to the user research population; the
        LEGACY_TEST infrastructure nodes keep their status untouched.
        """
        self.db.x("UPDATE strategies SET status='RETIRED' WHERE status NOT IN ('RETIRED') "
                  "AND NOT (" + LEGACY_POPULATION_SQL + ")")
        n = self.seed_population(min(200, get_config().evolution.population_size), symbol)
        return {"retired_all": True, "new_seeds": n}
