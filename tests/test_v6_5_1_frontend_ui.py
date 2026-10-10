"""V6.5.1 §11 — the frontend acceptance harness runs as part of the suite.

Delegates to the jsdom driver ``frontend/tests/v6_5_1_nodes_ui.mjs`` (same
pattern as V4.7/V4.8): the REAL LiveNodeTable / Mt5AccountPanel components run
against a stateful fetch stub, and the driver exits non-zero if any acceptance
behaviour fails.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
FRONTEND = REPO / "frontend"


def test_v6_5_1_nodes_ui_acceptance_harness():
    proc = subprocess.run(["node", "tests/v6_5_1_nodes_ui.mjs"], cwd=str(FRONTEND),
                          capture_output=True, text=True, timeout=540)
    out = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, f"v6.5.1 acceptance harness failed:\n{out}"
    assert "behaviours OK" in out
    assert "FAIL" not in out, out
