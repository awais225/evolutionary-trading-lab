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

@pytest.fixture(scope="module", autouse=True)
def isolated_test_data_root():
    """Give each test module an isolated disposable root; preserve module fixtures."""
    global _TMP_DATA
    from hero1_storage.engine import reset_engine_caches
    from app import task_service
    previous_root = os.environ.get("HERO1_DATA_ROOT")
    reset_engine_caches()
    _TMP_DATA = Path(tempfile.mkdtemp(prefix="hero1_test_data_"))
    os.environ["HERO1_DATA_ROOT"] = str(_TMP_DATA)
    report = initialize_databases(_TMP_DATA, create=True)
    assert report.ok, f"test data-root initialization failed: {report.to_dict()}"
    task_service.set_accepting_jobs(True)
    yield
    task_service.set_accepting_jobs(True)
    reset_engine_caches()
    import shutil
    shutil.rmtree(_TMP_DATA, ignore_errors=True)
    if previous_root is None:
        os.environ.pop("HERO1_DATA_ROOT", None)
    else:
        os.environ["HERO1_DATA_ROOT"] = previous_root


@pytest.fixture(autouse=True)
def reset_process_local_admission(isolated_test_data_root):
    """A prior test's stop/shutdown simulation must not leak into its neighbor."""
    from app import task_service
    task_service.set_accepting_jobs(True)
    yield
    task_service.set_accepting_jobs(True)


@pytest.fixture(scope="module")
def data_root(isolated_test_data_root) -> Path:
    return Path(_TMP_DATA)


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c
