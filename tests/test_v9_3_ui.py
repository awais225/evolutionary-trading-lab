from pathlib import Path
import subprocess

def test_v93_functional_ui_controls():
    r=Path(__file__).resolve().parents[1]
    p=subprocess.run(['node','tests/v9_3_ui.mjs'],cwd=r/'frontend',capture_output=True,text=True,timeout=180)
    assert p.returncode==0,p.stdout+'\n'+p.stderr[-10000:]
    assert '11/11 behaviours OK' in p.stdout
