"""Runtime dashboard diagnostic — the verdict must be *earned*, not printed.

``backend/tools/dashboard_diagnostic.py`` is the tool the operator runs against
a live Windows dashboard; its whole purpose is to say, with evidence, whether the
dashboard the browser receives is the current GitHub build. A tool that only ever
prints PASS is worthless, so these tests drive it against real HTTP servers and
require it to

* PASS a dashboard that is current (same repo, fresh bundle, current process,
  V5 UI signature present, no service worker), and
* FAIL every realistic way a dashboard can be old or foreign:

  1. the local bundle is stale (frontend/src changed after the build),
  2. the running process executes older backend code,
  3. the HTTP-served index.html is not the bundle on disk (an old dashboard),
  4. the answering backend belongs to a different checkout,
  5. nothing is listening,
  6. the served JavaScript is not the V5 UI (feature signature missing),
  7. a service worker is registered by the served JavaScript.

It also checks the two promises made to the operator: the diagnostic modifies
nothing (the repository tree is byte-identical before/after) and kills nothing
(the server it inspected is still serving afterwards).

Every server here is spawned by the test on a spare port; only that process is
stopped, in fixture teardown.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "backend" / "tools" / "dashboard_diagnostic.py"

sys.path.insert(0, str(ROOT / "backend"))

GOOD_JS = (
    "/* the served V5 dashboard bundle */\n"
    'const t={"Deep Backtest":"Deep Backtest","Live Testing":"Live Testing",'
    '"Live Test Results":"Live Test Results","MT5 Demo Trading":"MT5 Demo Trading",'
    '"Trading Info":"Trading Info","Prop-firm monitor":"Prop-firm",'
    '"Schedule":"Schedule","FROM SCRATCH":"FROM SCRATCH","Build:":"Build:",'
    '"SHUTDOWN DASHBOARD":"SHUTDOWN DASHBOARD","Amount / risk (money)":"Amount / risk (money)"};\n'
    "export default t;\n"
)
OLD_JS = "/* a dashboard from before V5.1a: no live-testing UI, no schedule */\nexport default {};\n"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _diag_module():
    spec = importlib.util.spec_from_file_location("dashboard_diagnostic", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_repo(root: Path, *, js: str = GOOD_JS, asset_name: str = "assets/index-test123.js") -> Path:
    """A miniature repository: frontend/src + a built frontend/dist + launchers +
    a Git checkout whose origin/main points at HEAD (as on the operator's PC)."""
    _git_init(root)
    src = root / "frontend" / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "App.jsx").write_text("export default function App(){ return null; }\n", encoding="utf-8")
    (src / "main.jsx").write_text("import App from './App.jsx';\n", encoding="utf-8")
    (root / "frontend" / "index.html").write_text("<!doctype html><html><body><div id=root></div></body></html>\n",
                                                  encoding="utf-8")
    dist = root / "frontend" / "dist"
    (dist / asset_name).parent.mkdir(parents=True, exist_ok=True)
    (dist / asset_name).write_text(js, encoding="utf-8")
    (dist / "index.html").write_text(
        "<!doctype html><html><head>\n"
        f'<script type="module" crossorigin src="/{asset_name}"></script>\n'
        "</head><body><div id=\"root\"></div></body></html>\n", encoding="utf-8")
    (root / "start.bat").write_text("@echo off\r\nrem launcher\r\nexit /b 0\r\n", encoding="utf-8")
    (root / "scripts").mkdir(exist_ok=True)
    (root / "scripts" / "build_and_serve.bat").write_text("@echo off\r\nexit /b 0\r\n", encoding="utf-8")

    from app import frontend_build as fb
    fb.write_stamp(root, built_by="pytest")
    # commit the built state so the fixture looks like a real checkout with a clean
    # tree whose origin/main == HEAD
    import subprocess
    env = {**os.environ, "GIT_AUTHOR_NAME": "pytest", "GIT_AUTHOR_EMAIL": "pytest@local",
           "GIT_COMMITTER_NAME": "pytest", "GIT_COMMITTER_EMAIL": "pytest@local"}
    for args in (("add", "-A"), ("commit", "-q", "-m", "fixture: dashboard state"),
                 ("update-ref", "refs/remotes/origin/main", "HEAD")):
        subprocess.run(["git", *args], cwd=str(root), env=env, capture_output=True, text=True, timeout=60)
    return root


def _git_init(root: Path) -> None:
    import subprocess
    root.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0",
           "GIT_AUTHOR_NAME": "pytest", "GIT_AUTHOR_EMAIL": "pytest@local",
           "GIT_COMMITTER_NAME": "pytest", "GIT_COMMITTER_EMAIL": "pytest@local"}
    run = lambda *a: subprocess.run(["git", *a], cwd=str(root), env=env, capture_output=True,
                                    text=True, timeout=60)
    run("init", "-q", "-b", "main")
    run("add", "-A")
    run("commit", "-q", "-m", "fixture: minimal lab checkout")
    run("update-ref", "refs/remotes/origin/main", "HEAD")


def served_payload_for(root: Path, *, code_hash: str | None = None, token: str = "LAB-TEST",
                       changed_since_start: bool = False, different_repo: bool = False) -> dict:
    """What a *running* backend would answer on /system/build."""
    from app import frontend_build as fb
    payload = dict(fb.served_payload(root))
    if different_repo:
        payload["backend_root"] = str(root.parent / "some_other_checkout")
    proc_code = code_hash
    if proc_code is None:
        mod = _diag_module()
        proc_code = mod.code_hash_of(root)
    payload["process"] = {"pid": 4242, "started_iso": "2026-01-01T00:00:00Z", "uptime_s": 12.0,
                          "start_token": token, "code_hash": proc_code, "cwd": str(root / "backend"),
                          "python": "pytest"}
    payload["served"] = {"root": payload.get("backend_root"), "src_hash": payload.get("src_hash"),
                         "status": payload.get("dist_status"), "index_sha256": payload.get("index_sha256"),
                         "entry_assets": payload.get("entry_assets") or [],
                         "dist_matches_src": payload.get("dist_matches_src"),
                         "changed_since_start": changed_since_start, "code_changed_since_start": False}
    return payload


class StubDashboard(BaseHTTPRequestHandler):
    """A stand-in dashboard: serves whatever index.html + assets it is told to."""

    root: Path | None = None          # directory to serve files from
    payload: dict = {}                # /system/build body
    health: dict = {"status": "ok"}
    log_message = lambda *a, **k: None                                   # noqa: E731

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store" if ctype.startswith("text/html")
                         else "public, max-age=31536000, immutable")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):                                                    # noqa: N802
        path = self.path.split("?")[0]
        if path == "/health":
            self._send(200, json.dumps(self.health).encode(), "application/json")
            return
        if path == "/system/build":
            self._send(200, json.dumps(self.payload).encode(), "application/json")
            return
        root = self.root
        if root is None:
            self._send(404, b"no root", "text/plain")
            return
        if path == "/":
            target = root / "index.html"
        else:
            target = root / path.lstrip("/")
        if not target.is_file():
            self._send(404, b"not found", "text/plain")
            return
        ctype = ("text/html" if target.suffix == ".html" else
                 "text/javascript" if target.suffix == ".js" else
                 "text/css" if target.suffix == ".css" else "application/octet-stream")
        self._send(200, target.read_bytes(), ctype)


@pytest.fixture
def dashboards():
    """Spawn stub dashboards; only these are ever stopped."""
    servers = []

    def start(directory: Path, payload: dict) -> str:
        port = free_port()
        handler = type("_H", (StubDashboard,), {"root": Path(directory), "payload": payload})
        server = ThreadingHTTPServer(("127.0.0.1", port), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread, port))
        return f"http://127.0.0.1:{port}"

    yield start
    for server, thread, _port in servers:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def run_diag(root: Path, url: str, *extra: str, timeout: int = 180):
    import subprocess
    cmd = [sys.executable, str(TOOL), "--root", str(root), "--url", url, "--json", *extra]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT), timeout=timeout)


def report_of(proc) -> dict:
    assert proc.returncode in (0, 3), f"diagnostic crashed: {proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
    return json.loads(proc.stdout)


def tree_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


# --------------------------------------------------------------------------- #
# 1. the positive case
# --------------------------------------------------------------------------- #
def test_01_a_current_dashboard_passes(tmp_path, dashboards):
    repo = make_repo(tmp_path / "repo")
    dist = repo / "frontend" / "dist"
    url = dashboards(dist, served_payload_for(repo))

    proc = run_diag(repo, url)
    report = report_of(proc)

    assert report["verdict"]["running_dashboard_matches_github"] is True, report["verdict"]
    assert report["checks"]["backend_reachable"] is True
    assert report["checks"]["served_from_this_repo"] is True
    assert report["checks"]["served_index_matches_local_dist"] is True
    assert report["checks"]["served_src_matches_local_src"] is True
    assert report["checks"]["served_entry_assets_match_local_dist"] is True
    assert report["checks"]["v5_feature_signature"] is True
    assert report["frontend"]["dist_status"] == "FRESH"


def test_02_a_stale_local_bundle_fails(tmp_path, dashboards):
    """frontend/src changed after the build → dist no longer matches the source."""
    repo = make_repo(tmp_path / "repo")
    dist = repo / "frontend" / "dist"
    url = dashboards(dist, served_payload_for(repo))

    (repo / "frontend" / "src" / "App.jsx").write_text("export default function App(){ return 1; }\n",
                                                       encoding="utf-8")
    report = report_of(run_diag(repo, url))

    assert report["frontend"]["dist_status"] == "STALE"
    assert report["frontend"]["dist_matches_src"] is False
    assert report["verdict"]["running_dashboard_matches_github"] is False
    assert "bundle_fresh" in report["verdict"]["failing_checks"]
    assert "served_src_matches_local_src" in report["verdict"]["failing_checks"]


def test_03_a_stale_running_process_fails(tmp_path, dashboards):
    """The files on disk are current but the process executes older backend code."""
    repo = make_repo(tmp_path / "repo")
    dist = repo / "frontend" / "dist"
    url = dashboards(dist, served_payload_for(repo, code_hash="0" * 64, changed_since_start=False))

    report = report_of(run_diag(repo, url))
    assert report["checks"]["process_code_current"] is False
    assert report["verdict"]["running_dashboard_matches_github"] is False


def test_04_an_old_served_index_fails(tmp_path, dashboards):
    """The URL answers, the build is fresh, but the HTTP index.html is not the one
    on disk — exactly the 'old dashboard is still being served' situation."""
    repo = make_repo(tmp_path / "repo")
    other_dist = tmp_path / "old_dist"
    other_dist.mkdir()
    (other_dist / "assets").mkdir()
    (other_dist / "assets" / "index-OLD999.js").write_text(OLD_JS, encoding="utf-8")
    (other_dist / "index.html").write_text(
        '<!doctype html><html><head><script type="module" src="/assets/index-OLD999.js"></script>'
        "</head><body></body></html>\n", encoding="utf-8")

    url = dashboards(other_dist, served_payload_for(repo))     # serves the OLD shell

    report = report_of(run_diag(repo, url))
    assert report["checks"]["served_index_matches_local_dist"] is False
    assert report["checks"]["served_entry_assets_match_local_dist"] is False
    assert report["checks"]["v5_feature_signature"] is False
    assert report["verdict"]["running_dashboard_matches_github"] is False
    assert "served_index_matches_local_dist" in report["verdict"]["failing_checks"]
    assert "v5_feature_signature" in report["verdict"]["failing_checks"]
    # the diagnostic must still report what it *did* receive
    assert report["served_index"]["script_references"] == ["/assets/index-OLD999.js"]


def test_05_a_different_checkout_fails(tmp_path, dashboards):
    repo = make_repo(tmp_path / "repo")
    dist = repo / "frontend" / "dist"
    url = dashboards(dist, served_payload_for(repo, different_repo=True))

    report = report_of(run_diag(repo, url))
    assert report["checks"]["served_from_this_repo"] is False
    assert report["verdict"]["running_dashboard_matches_github"] is False
    assert "served_from_this_repo" in report["verdict"]["failing_checks"]


def test_06_nothing_listening_fails(tmp_path):
    repo = make_repo(tmp_path / "repo")
    url = f"http://127.0.0.1:{free_port()}"
    report = report_of(run_diag(repo, url))
    assert report["checks"]["backend_reachable"] is False
    assert report["verdict"]["running_dashboard_matches_github"] is False
    assert report["system_build"]["http_status"] in (None, 0) or report["system_build"].get("error")


def test_07_served_js_without_the_v5_ui_fails(tmp_path, dashboards):
    """A bundle that is present and hash-consistent but is not the V5 dashboard."""
    repo = make_repo(tmp_path / "repo", js=OLD_JS)
    dist = repo / "frontend" / "dist"
    url = dashboards(dist, served_payload_for(repo))

    report = report_of(run_diag(repo, url))
    assert report["checks"]["served_index_matches_local_dist"] is True     # it *is* the local bundle
    assert report["features"]["all_present"] is False
    assert "v5_feature_signature" in report["verdict"]["failing_checks"]
    assert report["verdict"]["running_dashboard_matches_github"] is False


def test_08_a_service_worker_in_the_served_js_fails(tmp_path, dashboards):
    repo = make_repo(tmp_path / "repo",
                     js=GOOD_JS + "\nnavigator.serviceWorker.register('/sw.js');\n")
    (repo / "frontend" / "dist" / "sw.js").write_text("self.addEventListener('install', () => {});\n",
                                                      encoding="utf-8")
    dist = repo / "frontend" / "dist"
    url = dashboards(dist, served_payload_for(repo))

    report = report_of(run_diag(repo, url))
    assert report["service_worker"]["pwa_registration_in_served_js"] is True
    assert report["service_worker"]["sw_files"], report["service_worker"]
    assert report["checks"]["no_service_worker"] is False
    assert report["verdict"]["running_dashboard_matches_github"] is False


# --------------------------------------------------------------------------- #
# 2. the operator's two promises: nothing changed, nothing killed
# --------------------------------------------------------------------------- #
def test_09_the_diagnostic_is_read_only(tmp_path, dashboards):
    repo = make_repo(tmp_path / "repo")
    dist = repo / "frontend" / "dist"
    url = dashboards(dist, served_payload_for(repo))

    before = tree_fingerprint(repo)
    report = report_of(run_diag(repo, url))
    after = tree_fingerprint(repo)

    assert before == after, "the diagnostic modified the repository"
    assert report["verdict"]["running_dashboard_matches_github"] is True
    # the served shell is saved for inspection outside the repository, then removed
    assert report["served_index"]["temp_path"] is not None
    assert not Path(report["served_index"]["temp_path"]).exists()


def test_10_the_diagnostic_kills_nothing(tmp_path, dashboards):
    import urllib.request
    repo = make_repo(tmp_path / "repo")
    dist = repo / "frontend" / "dist"
    url = dashboards(dist, served_payload_for(repo))

    report = report_of(run_diag(repo, url))
    assert report["verdict"]["running_dashboard_matches_github"] is True
    with urllib.request.urlopen(url + "/health", timeout=5) as res:
        assert res.getcode() == 200, "the inspected server was disturbed"


def test_11_strict_mode_exits_three_on_failure(tmp_path, dashboards):
    repo = make_repo(tmp_path / "repo")
    dist = repo / "frontend" / "dist"
    url = dashboards(dist, served_payload_for(repo, different_repo=True))
    proc = run_diag(repo, url, "--strict")
    assert proc.returncode == 3, proc.stdout[-800:]
    ok_proc = run_diag(repo, url)
    assert ok_proc.returncode in (0, 3)


def test_12_the_human_summary_contains_every_required_line(tmp_path, dashboards):
    """The final block is what the operator pastes back; it must carry all of it."""
    repo = make_repo(tmp_path / "repo")
    dist = repo / "frontend" / "dist"
    url = dashboards(dist, served_payload_for(repo))
    import subprocess
    proc = subprocess.run([sys.executable, str(TOOL), "--root", str(repo), "--url", url,
                           "--no-remote"], capture_output=True, text=True, cwd=str(ROOT), timeout=180)
    out = proc.stdout
    for label in ("GITHUB REMOTE", "LOCAL HEAD", "GIT MATCH", "RUNNING BACKEND PID",
                  "RUNNING BACKEND REPOSITORY", "RUNNING BACKEND COMMIT", "LOCAL SOURCE HASH",
                  "LOCAL DIST HASH", "RUNNING FRONTEND HASH", "HTTP SERVED BUILD",
                  "DIST MATCHES SOURCE", "V5 UI SIGNATURE", "LIVE TESTING", "DEEP BACKTEST",
                  "MT5 DEMO TRADING", "SCHEDULE", "SERVICE WORKER",
                  "RUNNING_DASHBOARD_MATCHES_GITHUB", "END DIAGNOSTIC"):
        assert label in out, f"the final summary is missing {label!r}"
    # the sections the brief requires
    for title in ("GIT IDENTITY", "FRONTEND SOURCE IDENTITY", "RUNNING BACKEND PROCESS",
                  "RUNNING HTTP IDENTITY", "SERVED FRONTEND FINGERPRINT",
                  "FRONTEND UI IDENTITY", "BROWSER / CACHE / SERVICE WORKER",
                  "ALL FRONTEND COPIES", "NORMAL LAUNCHER IDENTITY"):
        assert title in out, f"missing section {title!r}"
    assert "GIT_IDENTITY_MATCH: PASS" in out
    assert "FRONTEND_BUILD_MATCH: PASS" in out
    assert "SERVED_V5_FEATURE_SIGNATURE: PASS" in out
    assert "RUNNING_DASHBOARD_MATCHES_GITHUB: PASS" in out


def test_13_all_frontend_copies_are_discovered(tmp_path, dashboards):
    """A second physical dashboard (an old dist kept next to the real one) must be
    listed, so 'multiple dashboards exist' is visible in the report."""
    repo = make_repo(tmp_path / "repo")
    stale = repo / "frontend" / "dist_old"
    stale.mkdir(parents=True)
    (stale / "index.html").write_text(
        '<!doctype html><html><head><script src="/assets/index-OLD999.js"></script>'
        "</head><body></body></html>\n", encoding="utf-8")

    dist = repo / "frontend" / "dist"
    url = dashboards(dist, served_payload_for(repo))
    report = report_of(run_diag(repo, url))

    dirs = {row["dir"] for row in report["frontend_copies"]}
    assert "frontend/dist" in dirs and "frontend/dist_old" in dirs, dirs
    kinds = {row["dir"]: row["kind"] if "kind" in row else ("built shell" if row["is_built_shell"] else "other")
             for row in report["frontend_copies"]}
    assert kinds.get("frontend/dist_old") == "built shell"


def test_14_launcher_hashes_are_reported(tmp_path, dashboards):
    repo = make_repo(tmp_path / "repo")
    dist = repo / "frontend" / "dist"
    url = dashboards(dist, served_payload_for(repo))
    report = report_of(run_diag(repo, url))
    files = {row["file"]: row for row in report["launchers"]}
    assert "start.bat" in files and "scripts/build_and_serve.bat" in files
    assert files["start.bat"]["sha256_prefix"]
    assert files["start.bat"]["first_lines"][0].lower().startswith("@echo off")
