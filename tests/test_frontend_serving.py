"""V5 launcher fix — the dashboard the normal launcher serves must be the one
built from ``frontend/src``.

These tests pin the mechanism that makes a stale interface impossible to serve
by accident:

* the build stamp ties ``frontend/dist`` to a source fingerprint;
* every non-fresh state (STALE / MISSING / UNSTAMPED / CORRUPT / BROKEN) is
  reported as "build first" instead of "serve it";
* ``GET /system/build`` publishes what a *running* backend actually serves, so
  the launcher can refuse to reuse an old instance;
* the backend serves ``index.html`` with ``Cache-Control: no-store`` (so a
  rebuilt dashboard is not masked by a browser cache) while hashed assets stay
  cacheable.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from app import frontend_build as fb                                     # noqa: E402


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _make_app(tmp: Path, src: str = "export default function App(){return 1;}\n") -> Path:
    """A minimal but structurally real frontend tree."""
    fe = tmp / "frontend"
    (fe / "src").mkdir(parents=True)
    (fe / "src" / "App.jsx").write_text(src, encoding="utf-8")
    (fe / "index.html").write_text(
        '<div id="root"></div>\n<script type="module" src="/assets/index-abc123.js"></script>\n',
        encoding="utf-8")
    (fe / "package.json").write_text('{"name":"t","version":"0.0.0"}\n', encoding="utf-8")
    return fe


def _make_dist(tmp: Path, fe: Path) -> None:
    dist = fe / "dist"
    (dist / "assets").mkdir(parents=True, exist_ok=True)
    (dist / "assets" / "index-abc123.js").write_text("console.log('bundle')\n", encoding="utf-8")
    shutil.copyfile(fe / "index.html", dist / "index.html")


# --------------------------------------------------------------------------- #
# the guard itself
# --------------------------------------------------------------------------- #
def test_missing_bundle_requires_a_build(tmp_path):
    _make_app(tmp_path)
    info = fb.check_dist(tmp_path)
    assert info["status"] == "MISSING"
    assert info["needs_build"] is True and info["ok"] is False


def test_unstamped_bundle_is_not_assumed_current(tmp_path):
    """A dist of unknown origin must never be presented as the current interface."""
    fe = _make_app(tmp_path)
    _make_dist(tmp_path, fe)
    info = fb.check_dist(tmp_path)
    assert info["status"] == "UNSTAMPED"
    assert info["needs_build"] is True
    assert "cannot be proven" in info["reasons"][0]


def test_stamp_makes_the_bundle_fresh_and_detects_source_changes(tmp_path):
    fe = _make_app(tmp_path)
    _make_dist(tmp_path, fe)
    fb.write_stamp(tmp_path, built_by="pytest")

    assert fb.check_dist(tmp_path)["status"] == "FRESH"
    assert fb.check_dist(tmp_path)["ok"] is True

    # editing the source makes the existing bundle stale (the exact regression
    # that made the launcher serve an old interface)
    (fe / "src" / "App.jsx").write_text("export default function App(){return 2;}\n", encoding="utf-8")
    stale = fb.check_dist(tmp_path)
    assert stale["status"] == "STALE"
    assert stale["needs_build"] is True
    assert "changed after this bundle was built" in stale["reasons"][0]

    # rebuilding + re-stamping restores freshness
    _make_dist(tmp_path, fe)
    fb.write_stamp(tmp_path, built_by="pytest")
    assert fb.check_dist(tmp_path)["status"] == "FRESH"


def test_source_change_outside_the_entry_file_is_detected(tmp_path):
    """Every file under frontend/src counts, not just the first one."""
    fe = _make_app(tmp_path)
    _make_dist(tmp_path, fe)
    fb.write_stamp(tmp_path, built_by="pytest")
    (fe / "src" / "components").mkdir()
    (fe / "src" / "components" / "DeepBacktest.jsx").write_text("export const B = 1;\n", encoding="utf-8")
    assert fb.check_dist(tmp_path)["status"] == "STALE"


def test_edited_bundle_is_reported_corrupt(tmp_path):
    fe = _make_app(tmp_path)
    _make_dist(tmp_path, fe)
    fb.write_stamp(tmp_path, built_by="pytest")
    (fe / "dist" / "index.html").write_text("<html>tampered</html>", encoding="utf-8")
    info = fb.check_dist(tmp_path)
    assert info["status"] in ("CORRUPT", "BROKEN")
    assert info["needs_build"] is True


def test_missing_bundle_asset_is_broken(tmp_path):
    fe = _make_app(tmp_path)
    _make_dist(tmp_path, fe)
    fb.write_stamp(tmp_path, built_by="pytest")
    (fe / "dist" / "assets" / "index-abc123.js").unlink()
    info = fb.check_dist(tmp_path)
    assert info["status"] == "BROKEN"
    assert info["needs_build"] is True
    assert "asset missing" in info["reasons"][0]


def test_stamp_records_the_bundle_identity(tmp_path):
    fe = _make_app(tmp_path)
    _make_dist(tmp_path, fe)
    stamp = fb.write_stamp(tmp_path, built_by="pytest")
    assert stamp["src_hash"] and stamp["index_sha256"]
    assert stamp["entry_assets"] == ["assets/index-abc123.js"]
    on_disk = json.loads((fe / "dist" / fb.STAMP_NAME).read_text(encoding="utf-8"))
    assert on_disk["src_hash"] == stamp["src_hash"]
    assert on_disk["built_by"] == "pytest"


def test_source_fingerprint_changes_only_when_inputs_change(tmp_path):
    _make_app(tmp_path)
    h1, n1 = fb.source_fingerprint(tmp_path)
    h2, n2 = fb.source_fingerprint(tmp_path)
    assert (h1, n1) == (h2, n2)
    # files that are not build inputs must not invalidate a good bundle
    (tmp_path / "frontend" / "tests").mkdir()
    (tmp_path / "frontend" / "tests" / "smoke.jsx").write_text("// not bundled\n", encoding="utf-8")
    h3, n3 = fb.source_fingerprint(tmp_path)
    assert (h3, n3) == (h1, n1)


# --------------------------------------------------------------------------- #
# the guard CLI used by the launcher
# --------------------------------------------------------------------------- #
def _guard():
    sys.path.insert(0, str(REPO / "backend" / "tools"))
    import frontend_build_guard as g
    return g


def test_guard_cli_exit_codes(tmp_path, capsys):
    g = _guard()
    fe = _make_app(tmp_path)

    assert g.main(["--root", str(tmp_path), "--check"]) == 10      # missing -> build
    _make_dist(tmp_path, fe)
    assert g.main(["--root", str(tmp_path), "--check"]) == 10      # unstamped -> build
    assert g.main(["--root", str(tmp_path), "--stamp"]) == 0
    out = capsys.readouterr().out
    assert "GUARD_STAMPED=True" in out
    assert g.main(["--root", str(tmp_path), "--check"]) == 0       # fresh

    (fe / "src" / "App.jsx").write_text("// changed\n", encoding="utf-8")
    assert g.main(["--root", str(tmp_path), "--check"]) == 10      # stale -> build

    (fe / "dist" / "index.html").unlink()
    assert g.main(["--root", str(tmp_path), "--check"]) == 10      # missing again


def test_guard_refuses_to_stamp_without_a_bundle(tmp_path, capsys):
    g = _guard()
    _make_app(tmp_path)
    assert g.main(["--root", str(tmp_path), "--stamp"]) == 1
    assert "refusing to stamp" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# the backend's published identity (what the launcher reads)
# --------------------------------------------------------------------------- #
def _client(monkeypatch, root: Path):
    import importlib
    monkeypatch.setenv("EVOLUTIONARY_LAB_DATA_ROOT", str(root / "DATA"))
    from fastapi.testclient import TestClient
    import app.main as main_mod
    importlib.reload(main_mod)
    return TestClient(main_mod.app), main_mod


def test_system_build_reports_the_served_bundle(tmp_path, monkeypatch):
    """A running instance must be able to prove which root/build it serves."""
    fe = _make_app(tmp_path)
    _make_dist(tmp_path, fe)
    fb.write_stamp(tmp_path, built_by="pytest")

    import importlib
    from fastapi.testclient import TestClient
    import app.main as main_mod
    main_mod = importlib.reload(main_mod)
    main_mod.ROOT = tmp_path                     # point the app at the temp tree
    main_mod.FRONTEND_DIST = tmp_path / "frontend" / "dist"
    client = TestClient(main_mod.app)
    body = client.get("/system/build").json()

    assert body["backend_root"] == str(tmp_path)
    assert body["dist_matches_src"] is True
    assert body["dist_status"] == "FRESH"
    assert body["src_hash"] == fb.source_fingerprint(tmp_path)[0]
    assert body["entry_assets"] == ["assets/index-abc123.js"]
    assert body["serving"] == "static"
    assert body["reasons"] == []


def test_system_build_flags_a_stale_bundle(tmp_path):
    fe = _make_app(tmp_path)
    _make_dist(tmp_path, fe)
    fb.write_stamp(tmp_path, built_by="pytest")
    (fe / "src" / "App.jsx").write_text("// newer source, older bundle\n", encoding="utf-8")

    import importlib
    from fastapi.testclient import TestClient
    import app.main as main_mod
    main_mod = importlib.reload(main_mod)
    main_mod.ROOT = tmp_path
    main_mod.FRONTEND_DIST = tmp_path / "frontend" / "dist"
    body = TestClient(main_mod.app).get("/system/build").json()
    assert body["dist_matches_src"] is False
    assert body["dist_status"] == "STALE"
    assert body["reasons"], "a stale bundle must say why"


def test_verify_served_compares_root_and_source_hash(tmp_path, capsys):
    """The launcher's decision: reuse the running instance, or replace it."""
    g = _guard()
    fe = _make_app(tmp_path)
    _make_dist(tmp_path, fe)
    fb.write_stamp(tmp_path, built_by="pytest")

    served = fb.served_payload(tmp_path)                 # what the instance would answer
    payload = json.dumps(served)

    # nothing listening -> unreachable (the launcher then just starts its own)
    assert g.cmd_verify_served(tmp_path, "http://127.0.0.1:1") == 14

    # simulate the HTTP layer
    class _Resp:
        def __init__(self, payload): self._p = payload
        def getcode(self): return 200
        def read(self): return self._p.encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    import urllib.request
    real = urllib.request.urlopen
    try:
        urllib.request.urlopen = lambda req, timeout=3.0: _Resp(payload)
        assert g.cmd_verify_served(tmp_path, "http://127.0.0.1:8787") == 0

        # same repository, older build -> 12 (replace)
        older = dict(served); older["src_hash"] = "0" * 64; older["dist_matches_src"] = False
        urllib.request.urlopen = lambda req, timeout=3.0: _Resp(json.dumps(older))
        assert g.cmd_verify_served(tmp_path, "http://127.0.0.1:8787") == 12

        # a different repository -> 11 (never silently reused)
        foreign = dict(served); foreign["backend_root"] = str(tmp_path / "elsewhere")
        urllib.request.urlopen = lambda req, timeout=3.0: _Resp(json.dumps(foreign))
        assert g.cmd_verify_served(tmp_path, "http://127.0.0.1:8787") == 11
    finally:
        urllib.request.urlopen = real


def test_lab_identity_check_is_not_fooled_by_a_foreign_service():
    g = _guard()
    import urllib.error
    import urllib.request

    class _NotFound:
        def __init__(self): self.code = 404
        def getcode(self): return self.code
        def read(self): return b'{"detail":"Not Found"}'
        def __enter__(self): return self
        def __exit__(self, *a): return False

    real = urllib.request.urlopen
    try:
        urllib.request.urlopen = lambda req, timeout=2.5: _NotFound()
        assert g.is_this_lab("http://127.0.0.1:9999") is False
    finally:
        urllib.request.urlopen = real


# --------------------------------------------------------------------------- #
# cache headers: a rebuilt dashboard must not be masked by the browser cache
# --------------------------------------------------------------------------- #
def test_served_index_is_no_store_and_assets_are_immutable(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import app.main as main_mod

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>dashboard</body></html>", encoding="utf-8")
    (dist / "assets" / "index-abc123.js").write_text("console.log(1)", encoding="utf-8")

    probe = FastAPI()
    probe.mount("/", main_mod.DashboardStaticFiles(directory=str(dist), html=True), name="fe")
    c = TestClient(probe)

    r = c.get("/")
    assert r.status_code == 200
    assert "no-store" in r.headers.get("cache-control", "")

    r2 = c.get("/assets/index-abc123.js")
    assert r2.status_code == 200
    assert "immutable" in r2.headers.get("cache-control", "")
