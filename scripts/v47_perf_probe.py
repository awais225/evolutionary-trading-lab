#!/usr/bin/env python3
"""
V4.7 performance / reliability probe (before-and-after measurement tool).

Starts the real backend against a disposable DATA root and measures:

  * startup: time from process launch to the first successful HTTP answer
    (first 200 from /health, i.e. "the API is answering at all"), time to the
    first 200 from /health with a fully initialised backend, and the time the
    application itself reports for its startup hook;
  * endpoint latency for the dashboard paths that matter (median / max over N
    samples, single-threaded, sequential);
  * optional repeated-poll behaviour, to prove polling does not hammer the DB.

Usage:
    EVOLUTIONARY_LAB_DATA_ROOT=/path/to/disposable/DATA \
        python scripts/v47_perf_probe.py --port 8799 --out /tmp/probe.json [--label before]

Read-only against the DATA root it is given apart from ordinary app behaviour
(feature cache / manifests), so point it at a disposable copy, never at the
authoritative tree.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

ENDPOINTS = [
    ("health", "/health"),
    ("api_health", "/api/health"),
    ("readiness", "/api/ready"),
    ("lab_status", "/api/lab/status"),
    ("stats_overview", "/api/stats/overview"),
    ("stats_nodes", "/api/stats/nodes?limit=25"),
    ("strategy_lab_initial", "/api/research/strategies?limit=50"),
    ("strategy_lab_facets", "/api/research/facets"),
    ("backtest_matrix_initial", "/api/research/matrix?ids=1195,1908,240"),
    ("node_detail", "/api/strategies/1195"),
    ("mt5_hist_capabilities", "/api/mt5-historical/capabilities"),
    ("mt5_hist_runs", "/api/mt5-historical/runs?strategy_id=1195"),
]


def _get(url: str, timeout: float = 30.0):
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read()
            return r.status, (time.perf_counter() - t0) * 1000.0, body
    except urllib.error.HTTPError as e:
        return e.code, (time.perf_counter() - t0) * 1000.0, e.read()
    except Exception:
        return None, (time.perf_counter() - t0) * 1000.0, b""


def wait_for(url: str, deadline: float, accept=(200,)):
    """Poll `url` until it answers with an accepted status. Returns seconds waited or None."""
    t0 = time.perf_counter()
    while time.perf_counter() < deadline:
        status, _ms, _body = _get(url, timeout=5.0)
        if status in accept:
            return time.perf_counter() - t0
        time.sleep(0.05)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--samples", type=int, default=5)
    ap.add_argument("--out", default="/tmp/v47_probe.json")
    ap.add_argument("--label", default="run")
    ap.add_argument("--startup-timeout", type=float, default=180.0)
    args = ap.parse_args()

    data_root = os.environ.get("EVOLUTIONARY_LAB_DATA_ROOT")
    if not data_root:
        print("EVOLUTIONARY_LAB_DATA_ROOT must point at a disposable DATA copy", file=sys.stderr)
        return 2

    env = dict(os.environ)
    env["EVOLUTIONARY_LAB_DATA_ROOT"] = data_root
    cmd = [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(args.port)]
    t_launch = time.perf_counter()
    proc = subprocess.Popen(cmd, cwd=str(BACKEND), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    log_lines: list[str] = []

    def drain():
        assert proc.stdout is not None
        for line in proc.stdout:
            log_lines.append(line.rstrip())

    th = threading.Thread(target=drain, daemon=True)
    th.start()

    base = f"http://127.0.0.1:{args.port}"
    deadline = time.perf_counter() + args.startup_timeout

    # 1. earliest possible answer from the API (any 200 on /health)
    t_first_answer = wait_for(base + "/health", deadline)
    # 2. backend fully initialised (readiness endpoint says ready)
    t_ready = wait_for(base + "/api/ready", deadline) if t_first_answer is not None else None
    if t_ready is None:
        t_ready = wait_for(base + "/api/health", deadline)
    t_launch_to_ready = (time.perf_counter() - t_launch) if t_ready is not None else None

    result: dict = {
        "label": args.label,
        "data_root": data_root,
        "port": args.port,
        "startup": {
            "first_answer_s": round(t_first_answer, 3) if t_first_answer is not None else None,
            "ready_s": round(t_launch_to_ready, 3) if t_launch_to_ready is not None else None,
            "startup_hook_reported_s": None,
        },
        "endpoints": {},
    }

    # the application's own startup log line, when present
    for line in log_lines:
        if "Application startup complete" in line:
            result["startup"]["startup_hook_reported_line"] = line
            break

    # 3. endpoint latency, sequential, N samples, after a warm-up call
    for name, path in ENDPOINTS:
        _get(base + path, timeout=60.0)
        samples = []
        statuses = []
        for _ in range(args.samples):
            status, ms, _body = _get(base + path, timeout=60.0)
            statuses.append(status)
            samples.append(ms)
            time.sleep(0.02)
        ok = [s for s in samples if s is not None]
        result["endpoints"][name] = {
            "path": path,
            "status": statuses[-1],
            "median_ms": round(statistics.median(ok), 2) if ok else None,
            "max_ms": round(max(ok), 2) if ok else None,
            "samples_ms": [round(s, 2) for s in samples],
        }

    # 4. repeated-poll cost: 30 sequential polls of the readiness/lab status pair
    t0 = time.perf_counter()
    for _ in range(30):
        _get(base + "/health", timeout=15.0)
        _get(base + "/api/lab/status", timeout=15.0)
    result["polling"] = {"pairs": 30, "total_s": round(time.perf_counter() - t0, 3)}

    proc.terminate()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()

    result["log_tail"] = log_lines[-25:]
    Path(args.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
