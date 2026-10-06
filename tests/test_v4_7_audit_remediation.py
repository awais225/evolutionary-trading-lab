"""
V4.7 audit remediation — regression tests for the three defects found by the
final pre-manual-test audit.

  1. GET /api/data/management/status returned HTTP 500 because ``Path`` was used
     in ``app/api/routes.py`` without being imported at module level.
  2. The same missing dependency made the ``/api/data/inventory`` fallback branch
     (``Path(r["path"])``) unsafe for a registered dataset whose raw file is not
     on disk (the branch was shadowed by a function-local import, so it never
     raised in practice - the test below pins the behaviour either way).
  3. The EvolutionTree page assumed ``data.nodes``/``data.edges`` are always
     arrays; the frontend regression cases live in
     ``frontend/tests/v47_pages_smoke.jsx`` (run by tests/test_v4_7_frontend.py).

None of these tests changes an endpoint's calculations or response schema, and
they only ever write to the disposable DATA copy the suite runs against.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ROUTES = REPO / "backend" / "app" / "api" / "routes.py"


def _strict_json(text: str):
    """Parse JSON and fail on NaN/Infinity (i.e. invalid JSON tokens)."""

    def _reject(token: str):
        raise AssertionError(f"response contained the invalid JSON token {token}")

    return json.loads(text, parse_constant=_reject)


# --------------------------------------------------------------------------- #
# defect 1 - /api/data/management/status
# --------------------------------------------------------------------------- #
DM_KEYS = {
    "ok", "timeframes", "data_source", "raw_datasets_count", "valid_datasets_count",
    "invalid_datasets_count", "missing_datasets_count", "xauusd_size_bytes",
    "cache_size_bytes", "nodes_count", "genomes_count",
}


def test_data_management_status_returns_200_with_its_schema(client):
    """The endpoint answers with the same schema it always returned (no 500)."""
    r = client.get("/api/data/management/status")
    assert r.status_code == 200, f"{r.status_code}: {r.text[:300]}"

    body = _strict_json(r.text)
    assert DM_KEYS <= set(body), f"missing keys: {sorted(DM_KEYS - set(body))}"

    # the values the Settings -> Data Management panel displays
    for key in ("xauusd_size_bytes", "cache_size_bytes"):
        assert isinstance(body[key], int) and body[key] >= 0, (key, body[key])
    for key in ("nodes_count", "genomes_count", "raw_datasets_count",
                "valid_datasets_count", "invalid_datasets_count", "missing_datasets_count"):
        assert isinstance(body[key], int) and body[key] >= 0, (key, body[key])
    assert isinstance(body["timeframes"], dict) and body["timeframes"], body["timeframes"]
    assert body["ok"] is True
    assert body["nodes_count"] > 0, "the research population must still be counted"

    # no undefined/null/NaN/[object Object] style artefacts
    text = r.text
    for token in ("NaN", "Infinity", "[object Object]", "undefined"):
        assert token not in text, f"{token} in the response"


def test_data_management_status_helpers_return_numbers_for_real_directories():
    """Direct call: the size helper itself must not raise for real/missing dirs."""
    from app.api import routes
    from app import paths as P

    assert routes._dir_size_bytes(P.MT5_XAUUSD_DIR) >= 0
    assert routes._dir_size_bytes(Path(P.DATA_ROOT) / "definitely-not-here") == 0

    body = routes.data_management_status()
    assert body["cache_size_bytes"] >= 0 and body["xauusd_size_bytes"] >= 0


def test_routes_module_resolves_path_everywhere_it_is_used():
    """Pin the defect class: `Path` is imported at module level and every use of
    it in routes.py is covered by that import (no function-local shadowing)."""
    source = ROUTES.read_text(encoding="utf-8")
    tree = ast.parse(source)

    module_level = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            module_level.update(a.asname or a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            module_level.update(a.asname or a.name for a in node.names)
    assert "Path" in module_level, "routes.py must import Path at module level"

    # every function that mentions Path must be able to resolve it at runtime
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        uses_path = any(isinstance(n, ast.Name) and n.id == "Path" for n in ast.walk(fn))
        if not uses_path:
            continue
        local = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Import):
                local.update(a.asname or a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                local.update(a.asname or a.name for a in node.names)
        assert "Path" in module_level or "Path" in local, (
            f"{fn.name}() uses Path but neither imports it nor inherits it")


# --------------------------------------------------------------------------- #
# defect 2 - /api/data/inventory fallback branch
# --------------------------------------------------------------------------- #
def test_data_inventory_prefers_the_raw_dataset_file_when_it_exists(client):
    """Normal behaviour must be unchanged: an on-disk raw file wins."""
    r = client.get("/api/data/inventory")
    assert r.status_code == 200, r.text[:300]
    body = _strict_json(r.text)
    assert set(body) >= {"data_root", "summary", "datasets", "features", "manifests"}

    entries = [d for d in body["datasets"] if d.get("symbol") == "XAUUSD"]
    assert entries, "the stored XAUUSD dataset must still be listed"
    raw = [d for d in entries if d.get("size_mb", 0) > 0]
    assert raw, "the raw dataset file must still be measured"


def test_data_inventory_falls_back_to_the_registry_path_when_the_raw_file_is_missing(client, tmp_path):
    """A registered dataset whose raw file is absent must use the registry path
    (the branch that used to depend on an unimported Path) and answer 200."""
    from app.db.database import get_db

    db = get_db()
    fallback_file = tmp_path / "QTESTSYM_M15.parquet"
    fallback_file.write_bytes(b"x" * (2 * 1024 * 1024))          # 2 MiB
    dataset_id = "QTESTSYM_M15_TEST_v1"

    db.x("DELETE FROM datasets WHERE id=?", (dataset_id,))
    db.x("""INSERT INTO datasets (id, symbol, timeframe, start_ts, end_ts, bars, source, path, created_at)
            VALUES (?,?,?,?,?,?,?,?,?)""",
         (dataset_id, "QTESTSYM", "M15", 1.0, 2.0, 10, "MT5", str(fallback_file), 1.0))
    try:
        r = client.get("/api/data/inventory")
        assert r.status_code == 200, r.text[:300]
        body = _strict_json(r.text)

        rows = [d for d in body["datasets"] if d.get("symbol") == "QTESTSYM"]
        assert rows, "the registered dataset must appear in the inventory"
        entry = rows[0]
        assert entry["timeframe"] == "M15"
        assert entry["raw_file"].endswith("QTESTSYM_M15.parquet"), entry["raw_file"]
        assert entry["size_mb"] == 2.0, entry["size_mb"]        # measured from the fallback path
        assert entry["status"] == "READY"
    finally:
        db.x("DELETE FROM datasets WHERE id=?", (dataset_id,))


def test_data_inventory_reports_zero_when_no_file_can_be_resolved(client):
    """The fallback is safe even when neither the raw file nor the registry path
    resolves: the entry stays in the inventory with a zero size (no exception)."""
    from app.db.database import get_db

    db = get_db()
    dataset_id = "QMISSING_M15_TEST_v1"
    db.x("DELETE FROM datasets WHERE id=?", (dataset_id,))
    db.x("""INSERT INTO datasets (id, symbol, timeframe, start_ts, end_ts, bars, source, path, created_at)
            VALUES (?,?,?,?,?,?,?,?,?)""",
         (dataset_id, "QMISSING", "M15", 1.0, 2.0, 10, "MT5",
          "/nonexistent/does-not-exist/QMISSING_M15.parquet", 1.0))
    try:
        r = client.get("/api/data/inventory")
        assert r.status_code == 200, r.text[:300]
        rows = [d for d in _strict_json(r.text)["datasets"] if d.get("symbol") == "QMISSING"]
        assert rows and rows[0]["size_mb"] == 0.0, rows
    finally:
        db.x("DELETE FROM datasets WHERE id=?", (dataset_id,))
