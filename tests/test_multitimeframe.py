"""Multi-timeframe node generation, naming, fingerprinting, filtering, migration.

Dedicated pristine test instruments (GAPX / ALLX / ISOLX) are registered in the
SESSION-TEMP registry fixture; they are never touched by other test files.
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from hero1_storage import instrument_models as im
from hero1_storage.engine import _configure_sqlite, instrument_db_path, instrument_url, registry_url
from hero1_strategy_spec import bullish_engulfing

GAPX, ALLX, ISOLX = "GAPX", "ALLX", "ISOLX"
META = {"pip_size": 0.1, "point_size": 0.01, "tick_size": 0.01, "tick_value": 1.0}
ALL_TFS = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]

import itertools
_COUNTER = itertools.count(1)


def _unique_spec(symbol: str, timeframe: str) -> dict:
    """Spec with a per-call unique SEMANTIC tag (fingerprints must not collide
    between independent test scenarios in the shared session DB)."""
    from hero1_strategy_spec import SUPPORTED_TIMEFRAMES  # clarity only
    spec = bullish_engulfing(symbol, timeframe if timeframe in ALL_TFS else "M15")
    spec["identity"]["tags"] = [*spec["identity"].get("tags", []), f"scenario-{next(_COUNTER)}"]
    return spec


def _broker_meta(symbol: str) -> dict:
    return {symbol: META}


def _mk_instrument(data_root: Path, symbol: str) -> None:
    from hero1_storage.init import initialize_databases
    initialize_databases(data_root, create=True)   # idempotent; registry + known DBs
    from sqlalchemy import text
    reg = create_engine(registry_url(data_root))
    with reg.begin() as conn:
        exists = conn.execute(text("SELECT 1 FROM instruments WHERE symbol=:s"), {"s": symbol}).first()
        if not exists:
            conn.execute(text(
                "INSERT INTO instruments (symbol, display_name, asset_class, enabled,"
                " default_timeframe, created_at, updated_at)"
                " VALUES (:s, :d, 'test', 1, 'M15', 0, 0)"), {"s": symbol, "d": f"Test {symbol}"})
    reg.dispose()
    path = instrument_db_path(symbol, data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    eng = create_engine(instrument_url(symbol, data_root))
    _configure_sqlite(eng)
    im.Base.metadata.create_all(eng)
    eng.dispose()


@pytest.fixture(scope="module", autouse=True)
def _instruments(data_root):
    for sym in (GAPX, ALLX, ISOLX):
        _mk_instrument(data_root, sym)


def _generate(client, symbol, timeframes, name=None, meta=None):
    reference_tf = next((t.upper() for t in timeframes if t.upper() in ALL_TFS), "M15")
    body = {"spec": _unique_spec(symbol, reference_tf),
            "timeframes": timeframes,
            "broker_meta": meta if meta is not None else _broker_meta(symbol)}
    if name:
        body["name"] = name
    return client.post(f"/api/v1/instruments/{symbol}/nodes/generate", json=body)


# ------------------------------------------------------------------ generation
def test_single_timeframe_generation(client):
    r = _generate(client, GAPX, ["M15"])
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["count"] == 1
    node = body["created"][0]
    assert node["name"] == "C1"                      # pristine instrument: first sequential name
    assert node["timeframe"] == "M15"
    assert node["spec_hash"] and len(node["spec_hash"]) == 64
    assert json.loads(node["spec_json"])["identity"]["timeframe"] == "M15"
    assert node["parent_node_id"] is None


def test_three_timeframes_generate_three_independent_nodes(client):
    r = _generate(client, GAPX, ["M1", "M30", "H1"])
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["count"] == 3
    assert [n["timeframe"] for n in body["created"]] == ["M1", "M30", "H1"]
    assert [n["name"] for n in body["created"]] == ["C2", "C3", "C4"]
    ids = {n["id"] for n in body["created"]}
    hashes = {n["spec_hash"] for n in body["created"]}
    assert len(ids) == 3 and len(hashes) == 3       # no duplicate ids/hashes
    for n in body["created"]:
        assert json.loads(n["spec_json"])["identity"]["timeframe"] == n["timeframe"]
        assert n["status"] == "DRAFT"               # created, NOT backtested/validated


def test_all_supported_timeframes_generate_seven_nodes(client):
    r = _generate(client, ALLX, ALL_TFS)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["count"] == len(ALL_TFS) == 7
    assert [n["name"] for n in body["created"]] == [f"C{i}" for i in range(1, 8)]
    assert [n["timeframe"] for n in body["created"]] == ALL_TFS
    assert len({n["spec_hash"] for n in body["created"]}) == 7


def test_missing_sequence_numbers_are_reused(client):
    # GAPX currently holds C1..C4 (tests above). Create C6 explicitly; the next
    # generation must take C5, then C7 — never C6, never out-of-order guessing.
    r = client.post(f"/api/v1/instruments/{GAPX}/nodes",
                    json={"name": "C6", "origin": "CUSTOM",
                          "description": "naming gap fixture"})
    assert r.status_code == 201, r.text
    r = _generate(client, GAPX, ["M5", "H4"])
    assert r.status_code == 201, r.text
    assert [n["name"] for n in r.json()["created"]] == ["C5", "C7"]


def test_explicit_name_for_single_timeframe_and_duplicate_rejected(client):
    assert _generate(client, ISOLX, ["M15"], name="My Alpha").status_code == 201
    r_dup = _generate(client, ISOLX, ["H1"], name="My Alpha")   # same name again
    assert r_dup.status_code == 409
    assert r_dup.json()["detail"]["code"] == "NAME_DUPLICATE"
    r_conflict = client.post(f"/api/v1/instruments/{ISOLX}/nodes",
                             json={"name": "My Alpha", "origin": "CUSTOM"})
    assert r_conflict.status_code == 409
    assert r_conflict.json()["detail"]["code"] == "NAME_DUPLICATE"


def test_generate_is_all_or_nothing_and_rejects_bad_timeframes(client):
    r = _generate(client, ISOLX, ["W1"])            # W1 deliberately unsupported
    assert r.status_code == 422
    assert "supported" in r.text.lower()
    r = _generate(client, ISOLX, ["M15", "m15"])    # duplicate after normalisation
    assert r.status_code == 422
    r = _generate(client, ISOLX, [])
    assert r.status_code == 422


def test_instrument_isolation_for_generated_nodes(client):
    mine = _generate(client, ISOLX, ["M30"]).json()["created"][0]
    items = client.get(f"/api/v1/instruments/{ISOLX}/nodes").json()["items"]
    assert mine["id"] in {n["id"] for n in items}
    assert all(n["instrument_id"] == ISOLX for n in items)
    gap = client.get(f"/api/v1/instruments/{GAPX}/nodes").json()["items"]
    assert all(n["instrument_id"] == GAPX for n in gap)
    # ids repeat across instrument DBs by design (ISSUE-004) — isolation is proven
    # by the endpoint never returning another instrument's record:
    assert all(n["instrument_id"] != ISOLX for n in gap)


# ------------------------------------------------------- fingerprint & identity
def test_timeframe_is_part_of_semantic_identity(client):
    r_a = client.post("/api/v1/instruments/GAPX/nodes/compile",
                      json={"spec": bullish_engulfing(GAPX, "M15"), "broker_meta": _broker_meta(GAPX)})
    r_b = client.post("/api/v1/instruments/GAPX/nodes/compile",
                      json={"spec": bullish_engulfing(GAPX, "H1"), "broker_meta": _broker_meta(GAPX)})
    assert r_a.status_code == r_b.status_code == 200
    assert r_a.json()["fingerprint"] != r_b.json()["fingerprint"]


def test_display_name_is_not_part_of_semantic_identity(client):
    created = _generate(client, ISOLX, ["M15"], name="Renamable").json()["created"][0]
    original_hash = created["spec_hash"]
    stored = json.loads(created["spec_json"])

    variant = json.loads(json.dumps(stored))
    variant["identity"]["tags"] = [*variant["identity"].get("tags", []), "new-semantic-tag"]
    r = client.post("/api/v1/instruments/ISOLX/nodes/compile",
                    json={"spec": variant, "broker_meta": _broker_meta(ISOLX)})
    # tags ARE semantic (fingerprinted) → this must differ from the stored node…
    assert r.json()["fingerprint"] != original_hash

    presentation_only = json.loads(json.dumps(stored))
    presentation_only["identity"]["name"] = "Yet Another Name"
    presentation_only["identity"]["description"] = "a wholly different description"
    r = client.post("/api/v1/instruments/ISOLX/nodes/compile",
                    json={"spec": presentation_only, "broker_meta": _broker_meta(ISOLX)})
    # name+description are NOT semantic → this must match the stored node's hash
    assert r.json()["fingerprint"] == original_hash

    # same after an actual PATCH rename — stored hash never changes
    r2 = client.patch(f"/api/v1/instruments/ISOLX/nodes/{created['id']}", json={"name": "Renamed Display"})
    assert r2.status_code == 200
    assert r2.json()["spec_hash"] == original_hash


# --------------------------------------------------------------- filtering
def test_timeframe_filter_and_counts(client):
    allx = client.get(f"/api/v1/instruments/{ALLX}/nodes").json()
    assert allx["total"] == 7
    assert allx["timeframe_counts"] == {tf: 1 for tf in ALL_TFS}
    assert allx["unassigned_timeframe_total"] == 0
    only_m15 = client.get(f"/api/v1/instruments/{ALLX}/nodes?timeframe=M15").json()
    assert [n["timeframe"] for n in only_m15["items"]] == ["M15"]
    assert only_m15["filtered_total"] == 1 and only_m15["total"] == 7
    two = client.get(f"/api/v1/instruments/{ALLX}/nodes?timeframe=M1,D1").json()
    assert sorted(n["timeframe"] for n in two["items"]) == ["D1", "M1"]
    bad = client.get(f"/api/v1/instruments/{ALLX}/nodes?timeframe=M2")
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "TIMEFRAME_UNSUPPORTED"
    gap = client.get(f"/api/v1/instruments/{GAPX}/nodes").json()
    assert gap["unassigned_timeframe_total"] >= 1     # the explicit-name draft
    starred_scope = client.get(f"/api/v1/instruments/{ALLX}/nodes?starred=true").json()
    assert starred_scope["timeframe_counts"] == {} and starred_scope["total"] == 7


# ---------------------------------------------------------------- migration
def _legacy_hash(spec: dict) -> str:
    import hashlib
    from hero1_strategy_spec import canonical_json
    from hero1_strategy_spec.spec import StrategySpec
    payload = StrategySpec.model_validate(spec).model_dump(mode="json")
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def test_fingerprint_migration_rewrites_hashes_and_is_idempotent(tmp_path):
    root = tmp_path / "data"
    sym = "MIGX"
    _mk_instrument(root, sym)
    db = instrument_db_path(sym, root)
    spec = bullish_engulfing("XAUUSD", "M15")
    spec["identity"]["name"] = "Legacy Node"
    legacy = _legacy_hash(spec)
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO nodes (instrument_id, origin, name, description, status,"
        " approval_state, version, spec_json, spec_hash, starred, created_by,"
        " created_at, updated_at, timeframe)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (sym, "CUSTOM", "Legacy Node", "desc kept", "DRAFT", "NONE", 3,
         json.dumps(spec), legacy, 1, "user", 111.0, 222.0, "M15"))
    con.execute(
        "INSERT INTO nodes (instrument_id, origin, name, status, approval_state,"
        " version, spec_json, spec_hash, starred, created_by, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (sym, "CUSTOM", "Draft no spec", "DRAFT", "NONE", 1, None, None, 0, "user", 1.0, 1.0))
    con.commit(); con.close()

    from hero1_storage.fingerprint_migrate import apply_plans, collect_plans
    plans = collect_plans(root)
    assert plans["collisions"] == []
    plan = next(db for db in plans["dbs"] if db["symbol"] == sym)
    assert plan["updated"] == [1] and plan["already"] == 0 and plan["scanned"] == 2
    result = apply_plans(plans, backup_dir=tmp_path / "bak")
    plan_out = next(db for db in result["dbs"] if db["symbol"] == sym)
    assert plan_out["written"] == 1
    assert (tmp_path / "bak" / f"{sym}-hero1.db.pre-fingerprint-migration").exists()

    con = sqlite3.connect(db)
    row = con.execute("SELECT name, description, version, starred, spec_json, spec_hash, timeframe FROM nodes WHERE id=1").fetchone()
    draft = con.execute("SELECT spec_hash FROM nodes WHERE id=2").fetchone()
    con.close()
    assert row[0] == "Legacy Node" and row[1] == "desc kept" and row[2] == 3 and row[3] == 1
    assert row[6] == "M15"                                        # timeframe untouched
    assert row[5] != legacy                                        # hash rewritten
    from hero1_strategy_spec import semantic_canonical
    from hero1_strategy_spec.spec import StrategySpec
    import hashlib
    assert row[5] == hashlib.sha256(
        semantic_canonical(StrategySpec.model_validate(json.loads(row[4])).model_dump(mode="json")).encode()).hexdigest()
    assert draft[0] is None                                        # drafts untouched

    plans2 = collect_plans(root)                                   # idempotent
    plan2 = next(db for db in plans2["dbs"] if db["symbol"] == sym)
    assert plan2["updated"] == [] and plan2["already"] == 1
    out2 = apply_plans(plans2, backup_dir=None)
    assert next(db for db in out2["dbs"] if db["symbol"] == sym)["written"] == 0


def test_fingerprint_migration_refuses_collisions(tmp_path):
    root = tmp_path / "data"
    sym = "MIGY"
    _mk_instrument(root, sym)
    db = instrument_db_path(sym, root)
    # two DISPLAY-different but SEMANTICALLY IDENTICAL specs at version 1 — both
    # legal pre-1.1.0 (names were hashed) — re-hashing collides them post-1.1.0
    con = sqlite3.connect(db)
    legacy_hashes = []
    for name in ("Row A", "Row B"):
        spec = bullish_engulfing("XAUUSD", "M15")
        spec["identity"]["name"] = name
        legacy = _legacy_hash(spec)
        legacy_hashes.append(legacy)
        con.execute(
            "INSERT INTO nodes (instrument_id, origin, name, status, approval_state,"
            " version, spec_json, spec_hash, starred, created_by, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (sym, "CUSTOM", name, "DRAFT", "NONE", 1, json.dumps(spec), legacy, 0, "user", 1.0, 1.0))
    con.commit(); con.close()

    from hero1_storage.fingerprint_migrate import apply_plans, collect_plans
    plans = collect_plans(root)
    assert len(plans["collisions"]) == 1
    with pytest.raises(RuntimeError):
        apply_plans(plans, backup_dir=None)
    con = sqlite3.connect(db)
    hashes = [r[0] for r in con.execute("SELECT spec_hash FROM nodes ORDER BY id").fetchall()]
    con.close()
    assert hashes == legacy_hashes                                 # nothing written
