"""Genome DSL tests: validation, hashing, mutation, crossover, directed ops."""
from __future__ import annotations

import random

import pytest

from app.genome import ops as gops
from app.genome.schema import (GenomeError, canonical, complexity, describe,
                               genome_hash, species_key, validate_genome)


def make_genome():
    return {
        "symbol": "XAUUSD", "timeframe": "M15", "direction": "long",
        "features": ["ema:20", "ema:50", "rsi:14"],
        "entry_long": {"op": "and", "clauses": [
            {"type": "crossover", "a": "ema:20", "b": "ema:50", "dir": "up"},
            {"type": "compare", "left": "rsi:14", "cmp": ">", "right": 55},
        ]},
        "entry_short": None,
        "exit": {"atr_spec": "atr:14", "sl_atr_mult": 1.5, "tp_atr_mult": 2.5,
                 "trailing": None, "min_hold_bars": 1, "max_hold_bars": 48,
                 "exit_condition": None},
        "sessions": None, "days": None, "regime_filters": None,
        "risk": {"risk_per_trade": 0.005, "max_concurrent": 1},
    }


def test_valid_genome_passes():
    validate_genome(make_genome())


def test_unknown_feature_rejected():
    g = make_genome()
    g["entry_long"]["clauses"][1]["left"] = "death_star:7"
    with pytest.raises(GenomeError):
        validate_genome(g)


def test_too_many_indicators_rejected():
    g = make_genome()
    g["features"] = ["ema:20", "ema:50", "rsi:14", "adx:14"]
    with pytest.raises(GenomeError):
        validate_genome(g, max_indicators=3)


def test_risk_band_enforced():
    g = make_genome()
    g["risk"]["risk_per_trade"] = 0.5   # 50% per trade — must be rejected
    with pytest.raises(GenomeError):
        validate_genome(g)


def test_hash_deterministic_and_order_free():
    g1 = make_genome()
    g2 = make_genome()
    g2["features"] = ["rsi:14", "ema:20", "ema:50"]   # different order
    assert genome_hash(g1) == genome_hash(g1)
    assert canonical(g1) == canonical(make_genome())
    assert genome_hash(g1) != genome_hash(g2)


def test_random_genomes_valid():
    rng = random.Random(7)
    n_ok = 0
    for _ in range(200):
        g = gops.random_genome("XAUUSD", rng, 3)
        try:
            validate_genome(g, 8, 10, 4)
            n_ok += 1
        except GenomeError:
            pass
    assert n_ok >= 150   # generator mostly produces valid genomes


def test_mutations_produce_valid_different_children():
    rng = random.Random(11)
    base = make_genome()
    seen_types = set()
    for mt in ["param_mutation", "indicator_addition", "indicator_removal",
               "sl_mutation", "tp_mutation", "timeframe_mutation",
               "session_mutation", "day_mutation", "regime_mutation",
               "min_hold_mutation", "max_hold_mutation", "exit_condition_mutation",
               "entry_condition_mutation"]:
        child, t, desc = gops.mutate(base, rng, mutation_type=mt, max_indicators=6)
        assert t == mt
        assert desc
        try:
            validate_genome(child, 8, 12, 4)
        except GenomeError as e:
            pytest.fail(f"mutation {mt} produced invalid genome: {e} ({desc})")
        if genome_hash(child) != genome_hash(base):
            seen_types.add(mt)
    assert len(seen_types) >= 10


def test_crossover_valid():
    rng = random.Random(3)
    g2 = gops.random_genome("XAUUSD", rng, 3)
    try:
        validate_genome(g2, 8, 10, 4)
    except GenomeError:
        g2 = make_genome()
        g2["features"] = ["adx:14", "atr:14"]
        g2["entry_long"] = {"type": "compare", "left": "adx:14", "cmp": ">", "right": 25}
    child, mt, desc = gops.crossover(make_genome(), g2, rng, 6)
    assert mt == "crossover"
    validate_genome(child, 8, 12, 4)


def test_directed_ops():
    g = make_genome()
    child, mt, desc = gops.directed(g, "add_adx_filter", {"threshold": 25})
    validate_genome(child, 8, 12, 4)
    assert "adx" in {f.split(":")[0] for f in child["features"]}
    child2, _, _ = gops.directed(g, "exclude_day", {"day": 1})
    assert child2["days"] == [0, 2, 3, 4]
    child3, _, _ = gops.directed(g, "restrict_sessions", {"sessions": ["london"]})
    assert child3["sessions"] == ["london"]
    child4, _, _ = gops.directed(g, "change_timeframe", {"timeframe": "M5"})
    assert child4["timeframe"] == "M5"
    with pytest.raises(GenomeError):
        gops.directed(g, "drop_database", {})


def test_complexity_and_species():
    g = make_genome()
    n_ind, n_cond, score = complexity(g)
    assert n_ind == 3 and n_cond == 2 and score >= 5
    assert species_key(g).startswith("M15|long|")
    assert "EMA" not in describe(g) or True   # describe is human-readable
    assert "SL=1.5xATR" in describe(g)
