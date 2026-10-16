"""Functional React controls in jsdom; network fakes cannot send MT5 orders."""
from pathlib import Path
import subprocess

def test_v92_functional_ui_controls():
    root=Path(__file__).resolve().parents[1]
    p=subprocess.run(['node','tests/v9_2_ui.mjs'],cwd=root/'frontend',text=True,capture_output=True,timeout=120)
    assert p.returncode==0,p.stdout+'\n'+p.stderr[-8000:]
    assert '6/6 behaviours OK' in p.stdout
