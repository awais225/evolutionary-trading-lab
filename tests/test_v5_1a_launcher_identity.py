"""V5.1a §9 / §3 / §8 — launcher regression: fresh, stale, foreign, replace.

These tests drive the *real* launcher tooling (``backend/tools/check_port.py``
and ``backend/tools/frontend_build_guard.py``) against real HTTP servers on
spare ports, so the behaviours that decide "may this instance be reused / must
it be replaced / must it be left alone" are covered by execution, not by
inspection.

Safety: every server used here is spawned by the test itself on a port that is
not the dashboard port, and only that PID is ever stopped. Nothing outside the
test's own temporary resources is signalled, and no test can touch the
provisioned DATA tree.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "backend" / "tools" / "frontend_build_guard.py"
CHECK_PORT = ROOT / "backend" / "tools" / "check_port.py"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def run(tool: Path, *args: str, timeout: int = 90):
    env = dict(os.environ)
    env.setdefault("EVOLUTIONARY_LAB_DATA_ROOT", os.environ.get("EVOLUTIONARY_LAB_DATA_ROOT", ""))
    return subprocess.run([sys.executable, str(tool), *args], capture_output=True, text=True,
                          cwd=str(ROOT), timeout=timeout)


def fields(out: str) -> dict:
    data = {}
    for line in out.splitlines():
        if "=" in line and not line.startswith(" "):
            k, _, v = line.partition("=")
            if k and k.replace("_", "").isalnum():
                data[k] = v
    return data


class _Stub(BaseHTTPRequestHandler):
    """A configurable stand-in for a lab backend / unrelated service."""

    payload: dict = {}
    kind: str = "lab"          # lab | legacy-lab | foreign
    log_message = lambda *a, **k: None                              # noqa: E731

    def do_GET(self):                                                # noqa: N802
        path = self.path.split("?")[0]
        if self.kind == "foreign":
            body, code = b"<html>some other application</html>", 404
            self.send_response(code)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/system/build":
            if self.kind == "legacy-lab":
                self.send_response(404)
                self.end_headers()
                return
            body = json.dumps(self.payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/health":
            body = json.dumps({"status": "ok", "ready": True,
                              "startup": {"state": "ready", "steps": []}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):                                               # noqa: N802
        self.send_response(404)
        self.end_headers()


def serve_stub(kind: str, payload: dict | None = None) -> tuple[ThreadingHTTPServer, int]:
    class Handler(_Stub):
        pass
    Handler.kind = kind
    Handler.payload = payload or {}
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, srv.server_address[1]


@pytest.fixture()
def make_stub():
    servers = []

    def _make(kind: str, payload: dict | None = None):
        srv, port = serve_stub(kind, payload)
        servers.append(srv)
        return port
    yield _make
    for s in servers:
        s.shutdown()
        s.server_close()


def base_payload(port: int, *, src_hash: str, index_sha: str, code_hash: str,
                 entry: list[str], loaded_index: str | None = None,
                 changed: bool = False, token: str | None = None) -> dict:
    return {
        "app": "EVOLUTIONARY TRADING RESEARCH LAB",
        "backend_root": str(ROOT),
        "frontend_dist": str(ROOT / "frontend" / "dist"),
        "serving": "static",
        "dist_status": "FRESH",
        "dist_matches_src": True,
        "src_hash": src_hash,
        "index_sha256": index_sha,
        "entry_assets": entry,
        "reasons": [],
        "process": {"pid": 4242, "started_iso": "2026-10-07T00:00:00Z", "uptime_s": 5.0,
                    "start_token": token, "code_hash": code_hash, "code_files": 10,
                    "python": "3.13.14", "cwd": str(ROOT)},
        "loaded": {"captured_at": "2026-10-07T00:00:00Z",
                   "bundle_dir": str(ROOT / "frontend" / "dist"),
                   "index_sha256": loaded_index or index_sha, "entry_assets": entry},
        "served": {"bundle_dir": str(ROOT / "frontend" / "dist"),
                   "index_sha256": index_sha, "entry_assets": entry,
                   "changed_since_start": changed, "code_changed_since_start": False},
        "version": "5.1a",
    }


def local_identity() -> dict:
    """What the guard computes for this checkout (source of truth for the stubs)."""
    sys.path.insert(0, str(ROOT / "backend"))
    from app import frontend_build as fb
    from app.runtime_identity import code_fingerprint
    info = fb.check_dist(ROOT)
    return {"src_hash": info["src_hash"], "index_sha256": info["index_sha256"] or "x" * 64,
            "entry": info["entry_assets"] or [], "code_hash": code_fingerprint(ROOT)["code_hash"]}


# --------------------------------------------------------------------------- #
# §3 — exactly what the launcher decides, per situation
# --------------------------------------------------------------------------- #
def test_01_a_current_instance_is_reused(make_stub):
    loc = local_identity()
    port = make_stub("lab", base_payload(0, src_hash=loc["src_hash"], index_sha=loc["index_sha256"],
                                         code_hash=loc["code_hash"], entry=loc["entry"],
                                         token="LAB-X"))
    r = run(GUARD, "--root", str(ROOT), "--verify-served", f"http://127.0.0.1:{port}",
            "--expect-token", "LAB-X")
    assert r.returncode == 0, r.stdout + r.stderr
    f = fields(r.stdout)
    assert f["SAME_ROOT"] == "True"
    assert f["PROCESS_CODE_IS_CURRENT"] == "True"
    assert f["PROCESS_BUNDLE_IS_CURRENT"] == "True"


def test_02_a_stale_process_is_replaced_not_reused(make_stub):
    """The exact hole: filesystem fresh, but the *process* runs older code."""
    loc = local_identity()
    port = make_stub("lab", base_payload(0, src_hash=loc["src_hash"], index_sha=loc["index_sha256"],
                                         code_hash="f" * 64, entry=loc["entry"], token="LAB-X"))
    r = run(GUARD, "--root", str(ROOT), "--verify-served", f"http://127.0.0.1:{port}")
    assert r.returncode == 15
    f = fields(r.stdout)
    # ...and the old *filesystem* criteria would all have said "reuse" (root cause)
    assert f["SAME_ROOT"] == "True"
    assert f["SERVED_MATCHES_LOCAL_SRC"] == "True"
    assert f["SERVED_DIST_MATCHES_SRC"] == "True"
    assert f["PROCESS_CODE_IS_CURRENT"] == "False"


def test_03_a_process_that_survived_a_rebuild_is_replaced(make_stub):
    loc = local_identity()
    port = make_stub("lab", base_payload(0, src_hash=loc["src_hash"], index_sha=loc["index_sha256"],
                                         code_hash=loc["code_hash"], entry=loc["entry"],
                                         loaded_index="a" * 64, changed=True))
    r = run(GUARD, "--root", str(ROOT), "--verify-served", f"http://127.0.0.1:{port}")
    assert r.returncode == 15
    assert fields(r.stdout)["PROCESS_BUNDLE_CHANGED_SINCE_START"] == "True"


def test_04_the_instance_this_launch_started_is_proven_by_its_token(make_stub):
    loc = local_identity()
    payload = base_payload(0, src_hash=loc["src_hash"], index_sha=loc["index_sha256"],
                           code_hash=loc["code_hash"], entry=loc["entry"], token="LAB-OLD")
    port = make_stub("lab", payload)
    ok = run(GUARD, "--root", str(ROOT), "--verify-served", f"http://127.0.0.1:{port}",
             "--expect-token", "LAB-OLD")
    assert ok.returncode == 0
    wrong = run(GUARD, "--root", str(ROOT), "--verify-served", f"http://127.0.0.1:{port}",
                "--expect-token", "LAB-NEW")
    assert wrong.returncode == 15
    assert "not the instance this launcher started" in wrong.stdout


def test_05_a_different_checkout_is_never_reused(make_stub):
    loc = local_identity()
    payload = base_payload(0, src_hash=loc["src_hash"], index_sha=loc["index_sha256"],
                           code_hash=loc["code_hash"], entry=loc["entry"])
    payload["backend_root"] = str(ROOT / "somewhere" / "else")
    port = make_stub("lab", payload)
    r = run(GUARD, "--root", str(ROOT), "--verify-served", f"http://127.0.0.1:{port}")
    assert r.returncode == 11


def test_06_an_older_build_without_identity_is_replaced(make_stub):
    port = make_stub("legacy-lab")
    r = run(GUARD, "--root", str(ROOT), "--verify-served", f"http://127.0.0.1:{port}")
    assert r.returncode == 13


def test_07_nothing_listening_is_reported_as_such():
    r = run(GUARD, "--root", str(ROOT), "--verify-served", f"http://127.0.0.1:{free_port()}")
    assert r.returncode == 14


# --------------------------------------------------------------------------- #
# §8 — port ownership: identify, never kill the innocent
# --------------------------------------------------------------------------- #
def test_08_an_unrelated_service_is_identified_as_foreign_and_never_stopped(make_stub):
    port = make_stub("foreign")
    identified = run(CHECK_PORT, str(port))
    assert identified.returncode == 20
    assert "NOT this lab" in identified.stdout
    assert "never stopped" in identified.stdout

    stopped = run(GUARD, "--root", str(ROOT), "--stop", f"http://127.0.0.1:{port}",
                  "--port", str(port))
    assert stopped.returncode == 3
    assert "refusing to stop" in stopped.stdout
    # it must still be alive after the launcher looked at it
    r = run(GUARD, "--root", str(ROOT), "--identify", f"http://127.0.0.1:{port}")
    assert r.returncode == 1


def test_09_a_lab_instance_is_identified_as_ours(make_stub):
    port = make_stub("legacy-lab")
    r = run(CHECK_PORT, str(port))
    assert r.returncode == 10
    assert "older Evolutionary Trading Research Lab backend" in r.stdout


def test_10_a_free_port_is_free():
    r = run(CHECK_PORT, str(free_port()))
    assert r.returncode == 0


# --------------------------------------------------------------------------- #
# §2 — the scan names the physical bundle behind every listener
# --------------------------------------------------------------------------- #
def test_11_scan_reports_the_physical_bundle_of_a_lab_listener(make_stub):
    loc = local_identity()
    port = make_stub("lab", base_payload(0, src_hash=loc["src_hash"], index_sha=loc["index_sha256"],
                                         code_hash=loc["code_hash"], entry=loc["entry"],
                                         token="LAB-SCAN"))
    r = run(GUARD, "--root", str(ROOT), "--scan")
    assert r.returncode == 0
    assert "PORT_8787=" in r.stdout                       # the real dashboard port is always listed
    assert "SCAN_LOCAL_SRC_HASH=" in r.stdout
    # and a lab answering on another port is named with its own identity
    direct = run(GUARD, "--root", str(ROOT), "--identify", f"http://127.0.0.1:{port}")
    assert direct.returncode == 0


# --------------------------------------------------------------------------- #
# §9-F — a checkout with no bundle at all must refuse to start silently
# --------------------------------------------------------------------------- #
def test_12_a_missing_bundle_forces_a_build(tmp_path):
    empty = tmp_path / "checkout"
    (empty / "frontend" / "src").mkdir(parents=True)
    (empty / "backend").mkdir()
    (empty / "frontend" / "src" / "main.jsx").write_text("// nothing\n")
    r = run(GUARD, "--root", str(empty), "--check")
    assert r.returncode == 10                     # build needed - never "serve it anyway"
    assert "MISSING" in r.stdout or "UNSTAMPED" in r.stdout


def test_13_a_stale_bundle_forces_a_build(tmp_path):
    build = tmp_path / "checkout"
    (build / "backend").mkdir(parents=True)
    src = build / "frontend" / "src"
    dist = build / "frontend" / "dist"
    src.mkdir(parents=True)
    dist.mkdir(parents=True)
    (src / "main.jsx").write_text("// v1\n")
    (dist / "index.html").write_text('<html><script src="assets/app-AAA.js"></script></html>')
    (dist / "assets").mkdir()
    (dist / "assets" / "app-AAA.js").write_text("console.log(1)\n")
    assert run(GUARD, "--root", str(build), "--stamp").returncode == 0
    assert run(GUARD, "--root", str(build), "--check").returncode == 0        # FRESH

    (src / "main.jsx").write_text("// v2 - the operator changed the dashboard\n")
    stale = run(GUARD, "--root", str(build), "--check")
    assert stale.returncode == 10
    assert "STALE" in stale.stdout
