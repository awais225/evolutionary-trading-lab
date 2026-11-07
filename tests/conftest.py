"""Pytest bootstrap: a FRESH, temporary HERO1_DATA_ROOT per session.
Never touches production DATA; never writes into the repository.
"""
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_TMP_DATA = Path(tempfile.mkdtemp(prefix="hero1_test_data_"))
os.environ["HERO1_DATA_ROOT"] = str(_TMP_DATA)
os.environ["HERO1_REPO_ROOT"] = str(REPO_ROOT)

for pkg in ("storage", "eventbus", "ai_orchestrator", "strategy_spec",
            "exec_semantics", "indicators", "mt5_adapter", "market_data", "research"):
    sys.path.insert(0, str(REPO_ROOT / "packages" / pkg / "src"))
sys.path.insert(0, str(REPO_ROOT / "services" / "api"))

import pytest  # noqa: E402

from hero1_storage.init import initialize_databases  # noqa: E402

_REPORT = initialize_databases(Path(_TMP_DATA), create=True)
assert _REPORT.ok, f"test data-root initialization failed: {_REPORT.to_dict()}"


@pytest.fixture(scope="session")
def data_root() -> Path:
    return Path(_TMP_DATA)


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c
