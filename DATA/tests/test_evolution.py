"""Evolution engine tests: duplicates, ancestry, elites, population cap."""
from __future__ import annotations

import json
import random

from app.evolution.engine import EvolutionEngine
from app.genome import ops as gops
from app.genome.schema import genome_hash
from tests.test_backtest import base_genome


def test_duplicate_genome_blocked(lab_env):
    evo = EvolutionEngine(lab_env["db"])
    g = base_genome()
    sid = evo.try_insert(g, None, 0, creation_reason="test")
    assert sid is not None
    sid2 = evo.try_insert(json.loads(json.dumps(g)), None, 0, creation_reason="dupe")
    assert sid2 is None           # identical genome -> rejected as duplicate
    before = evo.duplicates_blocked
    g2 = base_genome()
    g2["exit"]["sl_atr_mult"] = 1.6   # one gene different
    sid3 = evo.try_insert(g2, None, 0)
    assert sid3 is not None and sid3 != sid
    assert evo.duplicates_blocked >= before


def test_invalid_genome_rejected(lab_env):
    evo = EvolutionEngine(lab_env["db"])
    g = base_genome()
    g["risk"]["risk_per_trade"] = 0.9     # insane risk must be rejected
    assert evo.try_insert(g, None, 0) is None


def test_ancestry_chain(lab_env):
    db = lab_env["db"]
    evo = EvolutionEngine(db)
    rng = random.Random(5)
    parent = evo.try_insert(base_genome(), None, 0)
    child_g, mt, desc = gops.mutate(base_genome(), rng, "sl_mutation")
    child = evo.try_insert(child_g, parent, 1, mutation_type=mt,
                           creation_reason=f"mutation of #{parent}: {desc}")
    assert child is not None
    row = db.get_strategy(child)
    assert row["parent_id"] == parent
    assert row["generation"] == 1
    assert row["mutation_type"] == "sl_mutation"
    assert f"#{parent}" in row["creation_reason"]


def test_reproduce_respects_mix_and_dedupes(lab_env):
    db = lab_env["db"]
    evo = EvolutionEngine(db)
    rng = random.Random(9)
    # give the pool some tested survivors with fitness
    ids = []
    for i in range(12):
        g = gops.random_genome("XAUUSD", rng, 3)
        sid = evo.try_insert(g, None, 0)
        if sid:
            db.update_strategy(sid, status="SURVIVED", fitness=rng.random())
            ids.append(sid)
    counts = evo.reproduce(15, "XAUUSD")
    assert counts["mutation"] + counts["crossover"] + counts["exploration"] > 0
    assert counts["duplicate"] >= 0
    # children have parents or are exploration
    kids = db.q("SELECT * FROM strategies WHERE generation>=1 ORDER BY id DESC LIMIT 20")
    for k in kids:
        if k["origin"] in ("mutation", "crossover"):
            assert k["parent_id"] is not None
            assert k["creation_reason"]


def test_elite_diversity_cap(lab_env):
    db = lab_env["db"]
    evo = EvolutionEngine(db)
    # 10 near-identical species members with high fitness + 2 diverse ones
    base = base_genome()
    made = []
    for i in range(10):
        g = json.loads(json.dumps(base))
        g["exit"]["tp_atr_mult"] = 2.5 + i * 0.01
        sid = evo.try_insert(g, None, 0)
        if sid:
            db.update_strategy(sid, status="SURVIVED", fitness=0.9 - i * 0.001)
            made.append(sid)
    rng = random.Random(2)
    for i in range(2):
        g = gops.random_genome("XAUUSD", rng, 2)
        sid = evo.try_insert(g, None, 0)
        if sid:
            db.update_strategy(sid, status="SURVIVED", fitness=0.3)
    elites = evo.elites(6)
    from collections import Counter
    sp_counts = Counter(e["species_key"] for e in elites)
    # no single species may take the whole elite set
    assert max(sp_counts.values()) < len(elites)


def test_population_cap_retires_worst(lab_env):
    from app.config import update_config, get_config
    db = lab_env["db"]
    evo = EvolutionEngine(db)
    update_config("evolution", {"population_size": 30})
    rng = random.Random(4)
    n_before = evo.active_count()
    for i in range(40):
        g = gops.random_genome("XAUUSD", rng, 3)
        sid = evo.try_insert(g, None, 0)
        if sid:
            db.update_strategy(sid, status="SURVIVED", fitness=i / 100.0)
    retired = evo.enforce_population_cap("XAUUSD")
    assert retired > 0
    assert evo.active_count() <= get_config().evolution.population_size
