# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller Specification for Evolutionary Trading Research Lab (V2.3)
Packages the complete backend, pre-compiled frontend, and desktop shell
into a single Windows executable: dist/EvolutionaryTradingResearchLab.exe
"""
import sys
from pathlib import Path

block_cipher = None
ROOT_DIR = Path.cwd()

datas = [
    (str(ROOT_DIR / "frontend" / "dist"), "frontend/dist"),
    (str(ROOT_DIR / "CONFIG"), "CONFIG"),
    (str(ROOT_DIR / "backend" / "app"), "app"),
]

hiddenimports = [
    "uvicorn",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "fastapi",
    "pydantic",
    "psutil",
    "numpy",
    "pandas",
    "pyarrow",
    "duckdb",
    "yaml",
    "httpx",
    "websockets",
    "sqlite3",
    "zoneinfo",
    "webview",
]

a = Analysis(
    ["desktop/app_window.py"],
    pathex=[str(ROOT_DIR), str(ROOT_DIR / "backend")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="EvolutionaryTradingResearchLab",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
