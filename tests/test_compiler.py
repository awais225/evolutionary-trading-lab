"""Deterministic compiler + fingerprint + safety guarantees."""
from __future__ import annotations

import copy
import hashlib
import json
import random

import pytest

from hero1_strategy_spec import (
    SpecCompilationError, ReportStatus, bullish_engulfing, compile_spec,
    canonical_json, validate_spec, COMPILER_VERSION, SCHEMA_VERSION,
)

XAU_META = {"XAUUSD": {"pip_size": 0.1, "point_size": 0.01, "tick_size": 0.01, "tick_value": 1.0}}


def _spec(tf="M15"):
    return bullish_engulfing("XAUUSD", tf)


def test_identical_specs_identical_fingerprint():
    a = compile_spec(_spec(), expected_instrument="XAUUSD", broker_meta=XAU_META)
    b = compile_spec(_spec(), expected_instrument="XAUUSD", broker_meta=XAU_META)
    assert a.fingerprint == b.fingerprint
    assert a.canonical_json == b.canonical_json
    assert len(a.fingerprint) == 64 and all(c in "0123456789abcdef" for c in a.fingerprint)
    # fingerprint = sha256 of the SEMANTIC canonical JSON (presentation identity excluded)
    from hero1_strategy_spec import semantic_canonical
    from hero1_strategy_spec.spec import StrategySpec
    spec_obj = bullish_engulfing("XAUUSD", "M15")
    spec_obj = StrategySpec.model_validate(spec_obj)
    assert hashlib.sha256(semantic_canonical(spec_obj.model_dump(mode="json")).encode()).hexdigest() == a.fingerprint


def test_dictionary_insertion_order_does_not_matter():
    spec = _spec()
    # rotate key order aggressively
    def rot(o):
        if isinstance(o, dict):
            items = list(o.items())
            random.Random(42).shuffle(items)
            return {k: rot(v) for k, v in items}
        if isinstance(o, list):
            return [rot(v) for v in o]
        return o
    shuffled = rot(copy.deepcopy(spec))
    a = compile_spec(spec, expected_instrument="XAUUSD", broker_meta=XAU_META)
    b = compile_spec(shuffled, expected_instrument="XAUUSD", broker_meta=XAU_META)
    assert a.fingerprint == b.fingerprint


def test_different_meaningful_specs_differ():
    a = compile_spec(_spec("M1"), expected_instrument="XAUUSD", broker_meta=XAU_META)
    b = compile_spec(_spec("M5"), expected_instrument="XAUUSD", broker_meta=XAU_META)
    assert a.fingerprint != b.fingerprint
    c_spec = _spec("M1"); c_spec["exits"]["stop_loss"]["distance"]["value"] = 12
    c = compile_spec(c_spec, expected_instrument="XAUUSD", broker_meta=XAU_META)
    assert c.fingerprint != a.fingerprint


def test_compiler_records_versions_and_warmup():
    r = compile_spec(_spec(), expected_instrument="XAUUSD", broker_meta=XAU_META)
    assert r.schema_version == SCHEMA_VERSION == "1.0.0"
    assert r.compiler_version == COMPILER_VERSION == "1.1.0"
    assert r.ir["ir_version"] == "1.0.0"
    assert r.ir["strategy"]["instrument"] == "XAUUSD"
    assert r.ir["strategy"]["origin"] == "CUSTOM"
    assert isinstance(r.required_warmup_candles, int) and r.required_warmup_candles >= 1


def test_canonical_json_has_sorted_keys_and_no_whitespace():
    canon = canonical_json({"b": 1, "a": {"d": 2, "c": 3}})
    assert canon == '{"a":{"c":3,"d":2},"b":1}'


def test_invalid_spec_refused_by_compiler():
    spec = _spec()
    spec["schema_version"] = "0.0.0"
    with pytest.raises(SpecCompilationError) as ei:
        compile_spec(spec, expected_instrument="XAUUSD", broker_meta=XAU_META)
    assert ei.value.report.status is ReportStatus.INVALID


def test_needs_metadata_refused_by_compiler():
    with pytest.raises(SpecCompilationError) as ei:
        compile_spec(_spec(), expected_instrument="XAUUSD")   # no broker meta
    assert ei.value.report.status is ReportStatus.NEEDS_METADATA


def test_arbitrary_code_cannot_smuggle_through_spec():
    """Injection-shaped payloads are inert data and get rejected as bad input."""
    spec = _spec()
    spec["identity"]["name"] = "x'); DROP TABLE nodes;--"
    spec["metadata_extra"] = "__import__('os').system('id')"   # unknown field
    r = validate_spec(spec, expected_instrument="XAUUSD", broker_meta=XAU_META)
    assert r.status is ReportStatus.INVALID
    # and even a technically-valid weird string stays inert text in IR:
    ok = _spec(); ok["identity"]["name"] = "__import__('os').system('id')"
    res = compile_spec(ok, expected_instrument="XAUUSD", broker_meta=XAU_META)
    assert res.ir["strategy"]["name"] == "__import__('os').system('id')"   # literal string, never executed


def test_no_eval_or_exec_anywhere_in_package_source():
    import pathlib
    pkg = pathlib.Path("/home/user/hero1-trading-platform/packages/strategy_spec/src/hero1_strategy_spec")
    for f in pkg.glob("*.py"):
        src = f.read_text()
        assert "eval(" not in src, f"eval( found in {f.name}"
        assert "exec(" not in src, f"exec( found in {f.name}"
        assert "__import__" not in src, f"__import__ found in {f.name}"
        assert "importlib" not in src, f"importlib found in {f.name}"


def test_unknown_type_combinator_rejected_not_guessed():
    spec = _spec()
    spec["entry"] = {"type": "nand", "children": []}
    r = validate_spec(spec, expected_instrument="XAUUSD", broker_meta=XAU_META)
    assert r.status is ReportStatus.INVALID


def test_compile_result_is_json_serializable():
    r = compile_spec(_spec(), expected_instrument="XAUUSD", broker_meta=XAU_META)
    json.dumps(r.model_dump())   # must not raise
    json.dumps(r.ir)
