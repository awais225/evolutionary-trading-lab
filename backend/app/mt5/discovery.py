"""
Flexible MetaTrader 5 Terminal Discovery.
Scans standard Windows directories and registry for installed MT5 terminals.
Accepts any valid terminal build without hardcoding versions or broker brands.
"""
from __future__ import annotations

import glob
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple

log = logging.getLogger("mt5.discovery")

COMMON_EXE_NAMES = ["terminal64.exe", "terminal.exe", "metatrader64.exe", "metatrader.exe"]


def validate_terminal_path(path_str: str) -> Tuple[bool, str]:
    """
    Validates that a path points to an accessible executable file.
    Returns (is_valid, error_reason).
    """
    if not path_str or not path_str.strip():
        return False, "Path is empty"
    p = Path(path_str.strip())
    if not p.exists():
        return False, f"File does not exist: {p}"
    if not p.is_file():
        return False, f"Path is not a regular file: {p}"
    if p.suffix.lower() != ".exe":
        return False, f"File is not an executable (.exe): {p.name}"
    if p.name.lower() not in [n.lower() for n in COMMON_EXE_NAMES]:
        # Log a warning but allow it if it's an executable
        log.info("Non-standard executable name '%s' passed for MT5 terminal", p.name)
    return True, ""


def normalize_terminal_path(path_str: str) -> Tuple[str, str]:
    """Accept either the terminal ``.exe`` **or** the installation folder.

    ``initialize(path=...)`` needs the executable. A saved path can legitimately
    be a folder (the Windows diagnostic showed exactly that:
    ``C:\\Program Files\\MetaTrader 5 IC Markets Global``), which makes
    ``mt5.initialize(path=folder)`` fail with ``-10003 IPC initialize failed,
    Process create failed`` while auto-discovery still works. Returns
    ``(usable_path, note)``; the note is empty when nothing had to be changed.
    """
    raw = str(path_str or "").strip()
    if not raw:
        return "", "empty path"
    p = Path(raw)
    try:
        if p.is_file():
            return str(p), ""
        if p.is_dir():
            for exe in COMMON_EXE_NAMES:
                cand = p / exe
                try:
                    if cand.exists():
                        return str(cand), (f"the configured path was a folder; initialized "
                                           f"{cand.name} inside it")
                except Exception:
                    continue
            return str(p), "the configured path is a folder with no terminal executable inside it"
    except Exception as e:                                  # pragma: no cover - defensive
        return raw, f"path could not be inspected: {e}"
    return raw, "the configured path does not exist"


def discover_terminals() -> List[Dict[str, str]]:
    """
    Scans common Windows directories for all installed MT5 terminals.
    Returns list of dicts: [{"name": str, "path": str, "exists": bool}]
    """
    found: List[Dict[str, str]] = []
    seen_paths = set()

    def add_if_valid(name: str, p: Path):
        norm = str(p.resolve()).lower()
        if norm not in seen_paths and p.exists() and p.is_file():
            seen_paths.add(norm)
            found.append({
                "name": name,
                "path": str(p),
                "filename": p.name,
                "folder": p.parent.name,
            })

    # 1. Check Windows Program Files & AppData
    search_roots = []
    for env_var in ["ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"]:
        val = os.environ.get(env_var)
        if val:
            search_roots.append(Path(val))
            if env_var == "LOCALAPPDATA":
                search_roots.append(Path(val) / "Programs")

    for root in search_roots:
        if not root.exists():
            continue

        # Check standard MetaTrader 5 folder
        mt5_dir = root / "MetaTrader 5"
        if mt5_dir.exists():
            for exe in COMMON_EXE_NAMES:
                candidate = mt5_dir / exe
                if candidate.exists():
                    add_if_valid("MetaTrader 5 (Official)", candidate)

        # Scan all subdirectories 1 level deep for broker-branded installations
        try:
            for child in root.iterdir():
                if child.is_dir() and "metatrader" in child.name.lower() or "mt5" in child.name.lower():
                    for exe in COMMON_EXE_NAMES:
                        candidate = child / exe
                        if candidate.exists():
                            add_if_valid(child.name, candidate)
        except Exception as e:
            log.debug("Directory iteration error on %s: %e", root, e)

    # 2. Check Windows Registry if running on Windows
    if sys.platform == "win32":
        try:
            import winreg
            reg_paths = [
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
                (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            ]
            for hkey, subkey in reg_paths:
                try:
                    with winreg.OpenKey(hkey, subkey) as key:
                        count, _, _ = winreg.QueryInfoKey(key)
                        for i in range(count):
                            try:
                                sub = winreg.EnumKey(key, i)
                                with winreg.OpenKey(key, sub) as item:
                                    name, _ = winreg.QueryValueEx(item, "DisplayName")
                                    loc, _ = winreg.QueryValueEx(item, "InstallLocation")
                                    if "metatrader" in str(name).lower() or "mt5" in str(name).lower():
                                        loc_p = Path(loc)
                                        for exe in COMMON_EXE_NAMES:
                                            candidate = loc_p / exe
                                            if candidate.exists():
                                                add_if_valid(str(name), candidate)
                            except Exception:
                                continue
                except Exception:
                    continue
        except Exception:
            pass

    return found


def discover_installed_terminals() -> List[Dict[str, str]]:
    """Alias for discover_terminals."""
    return discover_terminals()


def is_valid_terminal_exe(path_str: str) -> bool:
    """Convenience boolean helper for validating a terminal path."""
    return validate_terminal_path(path_str)[0]
