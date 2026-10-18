from pathlib import Path
import subprocess

def test_v95_ui_expansion_and_batch_confirmation():
    p=subprocess.run(['node','tests/v9_5_ui.mjs'],cwd=Path(__file__).resolve().parents[1]/'frontend',capture_output=True,text=True,timeout=180)
    assert p.returncode==0,p.stdout+'\n'+p.stderr[-8000:]
    assert '5/5 behaviours OK' in p.stdout
