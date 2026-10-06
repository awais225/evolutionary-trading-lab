"""
AI Researcher (spec §14).

Analyzes STRUCTURED experiment results (metrics, matrices, validation) and
emits structured research hypotheses. Hypotheses are whitelisted genome
directives (see genome.ops.directed) — the AI layer can NEVER:
  * generate executable Python trading code
  * bypass risk controls, validation, or the execution layer
  * place orders directly

Two sources:
  1. rule_analyzer — deterministic, always available (default)
  2. llm (optional) — receives a structured experiment summary and must
     answer with JSON matching the same hypothesis schema; output is
     validated against the whitelist before anything touches the genome.
"""
from __future__ import annotations

import json
import logging

from ..jsonutil import jd
import time
from typing import Any, Dict, List, Optional

from ..config import get_config
from ..db.database import get_db
from ..genome import ops as gops
from ..genome.schema import GenomeError
from ..specialization.matrix import propose_specializations

log = logging.getLogger("ai.researcher")

ALLOWED_ACTIONS = {"add_regime_filter", "restrict_sessions", "exclude_day",
                   "restrict_days", "change_timeframe", "add_adx_filter",
                   "add_feature_condition", "restrict_direction",
                   "tighten_sl", "widen_tp"}


class Hypothesis:
    @staticmethod
    def make(strategy_id: Optional[int], source: str, observation: str,
             hypothesis: str, action: str, params: Dict) -> Dict:
        if action not in ALLOWED_ACTIONS:
            raise ValueError(f"action '{action}' not in whitelist — AI proposals "
                             f"are restricted to structured genome directives")
        return {"strategy_id": strategy_id, "source": source,
                "observation": observation, "hypothesis": hypothesis,
                "proposal": {"action": action, "params": params}}


def rule_analyze(strategy: Dict, matrices: Optional[Dict],
                 validation: Optional[Dict]) -> List[Dict]:
    """Deterministic analysis -> hypotheses (same schema as LLM output)."""
    out: List[Dict] = []
    genome = strategy["genome"]
    sid = strategy["id"]

    # 1) matrix-driven specializations (the bulk of the research signal)
    if matrices:
        for p in propose_specializations(genome, matrices, max_children=4):
            out.append(Hypothesis.make(
                sid, "rule_analyzer", p["reason"], p["expected"],
                p["action"], p["params"]))

    # 2) validation-driven hypotheses
    if validation:
        oos = validation.get("oos") or {}
        if oos.get("ok") and oos.get("degradation") is not None and oos["degradation"] > 0.4:
            out.append(Hypothesis.make(
                sid, "rule_analyzer",
                f"fitness degrades {oos['degradation']:.0%} out-of-sample — "
                f"possible overfitting of thresholds",
                "tighter stops and fewer degrees of freedom may generalize better",
                "tighten_sl", {"factor": 0.85}))
        mc = validation.get("montecarlo") or {}
        if mc.get("ok") and mc.get("return_positive_frac", 1) < 0.6:
            out.append(Hypothesis.make(
                sid, "rule_analyzer",
                f"Monte Carlo: only {mc['return_positive_frac']:.0%} of randomized "
                f"trade sequences stay profitable",
                "the edge may depend on specific trades; a volatility regime "
                "filter could stabilize expectancy",
                "add_regime_filter", {"regime": "trending"}))
        pert = validation.get("perturbation") or {}
        if pert.get("ok") and pert.get("instability", 0) > 0.35:
            out.append(Hypothesis.make(
                sid, "rule_analyzer",
                f"parameter perturbation instability {pert['instability']:.0%}",
                "thresholds sit on a fragile edge; widening TP relative to SL "
                "may reduce sensitivity",
                "widen_tp", {"factor": 1.2}))
    return out


def apply_hypothesis(hyp_id: int, db=None) -> Optional[int]:
    """Convert an approved hypothesis into a real child strategy."""
    db = db or get_db()
    row = db.one("SELECT * FROM hypotheses WHERE id=?", (hyp_id,))
    if not row:
        return None
    proposal = json.loads(row["proposal"])
    parent = db.get_strategy(row["strategy_id"]) if row["strategy_id"] else None
    if not parent:
        db.x("UPDATE hypotheses SET status='REJECTED' WHERE id=?", (hyp_id,))
        return None
    try:
        child, mt, desc = gops.directed(parent["genome"], proposal["action"],
                                        proposal["params"])
    except (GenomeError, KeyError, ValueError) as e:
        db.x("UPDATE hypotheses SET status='REJECTED' WHERE id=?", (hyp_id,))
        log.info("hypothesis %s rejected: %s", hyp_id, e)
        return None
    from ..evolution.engine import EvolutionEngine
    evo = EvolutionEngine(db)
    sid = evo.spawn_child(child, parent["id"], mt,
                          f"AI hypothesis #{hyp_id}: {row['hypothesis']} (observation: "
                          f"{row['observation'][:120]})", hypothesis_id=hyp_id)
    if sid:
        db.x("UPDATE hypotheses SET status='APPLIED', child_strategy_id=? WHERE id=?",
             (sid, hyp_id))
    else:
        db.x("UPDATE hypotheses SET status='REJECTED' WHERE id=?", (hyp_id,))
    return sid


def run_research_cycle(evolution_engine) -> List[Dict]:
    """Analyze the most promising strategies and store new hypotheses."""
    cfg = get_config()
    if not cfg.ai.researcher_enabled:
        return []
    db = get_db()
    rows = db.q("""SELECT * FROM strategies
                   WHERE status IN ('SURVIVED','VALIDATING','QUALIFIED','PAPER')
                     AND fitness IS NOT NULL
                   ORDER BY fitness DESC LIMIT 12""")
    created: List[Dict] = []
    for r in rows:
        if len(created) >= cfg.ai.max_hypotheses_per_cycle:
            break
        genome = json.loads(r["genome"])
        strat = {**r, "genome": genome}
        mat_row = db.one("SELECT * FROM matrices WHERE strategy_id=?", (r["id"],))
        matrices = None
        if mat_row:
            matrices = {k: json.loads(mat_row[k]) if mat_row[k] else None
                        for k in ("timeframe", "session", "day", "regime", "direction")}
        val_row = db.one("SELECT * FROM validations WHERE strategy_id=?", (r["id"],))
        validation = None
        if val_row:
            keymap = {"oos": "oos", "walkforward": "walkforward",
                      "perturbation": "perturbation",
                      "spread_slippage_stress": "spread_stress",
                      "montecarlo": "montecarlo", "regime_holdout": "regime_holdout"}
            validation = {k: json.loads(val_row[col]) if val_row.get(col) else None
                          for k, col in keymap.items()}
        # avoid re-hypothesizing the same action for the same strategy
        existing = db.q("SELECT proposal FROM hypotheses WHERE strategy_id=?", (r["id"],))
        seen = set()
        for e in existing:
            try:
                seen.add(json.loads(e["proposal"])["action"])
            except Exception:
                pass
        for h in rule_analyze(strat, matrices, validation):
            action = h["proposal"]["action"]
            if action in seen:
                continue
            # Redundancy avoidance from accumulated research memory (spec §24, §26)
            prev_failed = db.one("""SELECT failure_reason FROM research_memory
                                    WHERE symbol=? AND timeframe=? AND action=? AND outcome='FAILED'
                                    LIMIT 1""",
                                 (r["symbol"], r["timeframe"], action))
            if prev_failed and prev_failed["failure_reason"]:
                log.info("Skipping redundant hypothesis '%s' on %s %s — previously failed with: %s",
                         action, r["symbol"], r["timeframe"], prev_failed["failure_reason"])
                continue

            seen.add(action)
            hid = db.x("""INSERT INTO hypotheses (strategy_id,source,observation,
                          hypothesis,proposal,status,created_at) VALUES (?,?,?,?,?,?,?)""",
                       (h["strategy_id"], h["source"], h["observation"],
                        h["hypothesis"], jd(h["proposal"]), "PROPOSED",
                        time.time()))
            h["id"] = hid
            created.append(h)
            if len(created) >= cfg.ai.max_hypotheses_per_cycle:
                break
    # LLM hypotheses (optional, same whitelist)
    try:
        from .llm_adapter import llm_hypotheses
        for h in llm_hypotheses(rows):
            if len(created) >= cfg.ai.max_hypotheses_per_cycle:
                break
            hid = db.x("""INSERT INTO hypotheses (strategy_id,source,observation,
                          hypothesis,proposal,status,created_at) VALUES (?,?,?,?,?,?,?)""",
                       (h["strategy_id"], h["source"], h["observation"],
                        h["hypothesis"], jd(h["proposal"]), "PROPOSED",
                        time.time()))
            h["id"] = hid
            created.append(h)
    except Exception as e:
        log.debug("llm adapter inactive: %s", e)
    return created


def record_experiment_memory(child_id: int, db=None) -> None:
    """Record experimental knowledge into research_memory table (spec §20-28)."""
    db = db or get_db()
    child = db.get_strategy(child_id)
    if not child or not child.get("parent_id"):
        return
    parent = db.get_strategy(child["parent_id"])
    if not parent:
        return

    hyp_id = child.get("hypothesis_id")
    hyp = db.one("SELECT * FROM hypotheses WHERE id=?", (hyp_id,)) if hyp_id else None

    parent_fit = parent.get("fitness") or 0.0
    child_fit = child.get("fitness") or 0.0
    delta = round(child_fit - parent_fit, 4)

    status = child.get("status")
    if status in ("FAILED", "KILLED"):
        outcome = "FAILED"
    elif delta > 0.05:
        outcome = "IMPROVED"
    elif delta < -0.05:
        outcome = "DEGRADED"
    else:
        outcome = "NEUTRAL"

    p_genome = parent.get("genome", {})
    c_genome = child.get("genome", {})
    changed = []
    if p_genome.get("indicators") != c_genome.get("indicators"):
        changed.append("indicators")
    if p_genome.get("conditions") != c_genome.get("conditions"):
        changed.append("conditions")
    if p_genome.get("regime_filters") != c_genome.get("regime_filters"):
        changed.append("regime_filters")
    if p_genome.get("sessions") != c_genome.get("sessions"):
        changed.append("sessions")
    if p_genome.get("days") != c_genome.get("days"):
        changed.append("days")
    if p_genome.get("exit") != c_genome.get("exit"):
        changed.append("exit_rules")

    fail_reason = child.get("failure_reason") or ""
    surv_reason = child.get("survival_reason") or ""

    if outcome == "IMPROVED":
        learned = f"Modification ({child.get('mutation_type', 'mutation')}) improved fitness from {parent_fit:.3f} to {child_fit:.3f} ({delta:+.3f})"
    elif outcome == "FAILED":
        learned = f"Modification failed: {fail_reason or 'failed validation floor'}"
    else:
        learned = f"Modification degraded fitness by {delta:+.3f}"

    proposal = json.loads(hyp["proposal"]) if hyp and hyp.get("proposal") else {}
    action = proposal.get("action") or child.get("mutation_type") or "mutation"
    params = json.dumps(proposal.get("params") or {})

    db.x("""INSERT INTO research_memory
            (parent_id, child_id, hypothesis_id, hypothesis, action, params,
             changed_variables, unchanged_variables, symbol, timeframe, regime,
             parent_fitness, child_fitness, fitness_delta, outcome, failure_reason,
             survival_reason, learned_rule, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
         (parent["id"], child["id"], hyp_id,
          hyp["hypothesis"] if hyp else child.get("creation_reason", ""),
          action, params, ", ".join(changed), "",
          child["symbol"], child["timeframe"], str(c_genome.get("regime_filters", "")),
          parent_fit, child_fit, delta, outcome, fail_reason,
          surv_reason, learned, time.time()))
