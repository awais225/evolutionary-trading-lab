from pathlib import Path
import subprocess


def test_v10_explicit_policy_progress_refresh_dedup_and_nonoverlapping_poll():
    p=subprocess.run(['node','tests/v10_ui.mjs'],cwd=Path(__file__).resolve().parents[1]/'frontend',capture_output=True,text=True,timeout=45)
    assert p.returncode==0,p.stdout+'\n'+p.stderr[-8000:]
    assert '6/6 behaviours OK' in p.stdout
