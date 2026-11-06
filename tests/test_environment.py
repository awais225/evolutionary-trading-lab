import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_doctor_check_passes(data_root, monkeypatch, tmp_path):
    env = dict(os.environ)
    env["HERO1_DATA_ROOT"] = str(data_root)
    env["HERO1_REPO_ROOT"] = str(REPO_ROOT)
    out = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "doctor.py")],
                         capture_output=True, text=True, env=env, timeout=120)
    assert "registry_db" in out.stdout
    assert "XAUUSD" in out.stdout and "EURUSD" in out.stdout and "BTCUSD" in out.stdout
    assert out.returncode == 0, out.stdout + out.stderr


def test_data_root_inside_repo_rejected():
    from hero1_storage.engine import ensure_outside_repository, DataRootError
    import pytest
    with pytest.raises(DataRootError):
        ensure_outside_repository(REPO_ROOT / "DATA")


def test_data_root_outside_repo_accepted(tmp_path):
    from hero1_storage.engine import ensure_outside_repository
    assert ensure_outside_repository(tmp_path) == tmp_path.resolve()


def test_relative_data_root_rejected(monkeypatch):
    import hero1_storage.engine as eng
    monkeypatch.setenv("HERO1_DATA_ROOT", "relative/path")
    try:
        eng.data_root()
        assert False, "expected DataRootError"
    except eng.DataRootError:
        pass
