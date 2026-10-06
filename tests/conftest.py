"""V4.1 repo-side test bootstrap.

The original suite lives in the DATA tree (DATA/tests). These repo-side tests
exercise the V4.1 research-run workflow against the configured real DATA root
(selected with EVOLUTIONARY_LAB_DATA_ROOT) and only need `backend` importable.
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_BACKEND = _REPO / "backend"
if _BACKEND.exists() and str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="module")
def client():
    from app.main import app

    return TestClient(app)
