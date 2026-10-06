"""
Persistent configuration for MetaTrader 5 Terminal paths and preferences.
Stored in CONFIG/mt5_config.json. Never stores account passwords.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger("mt5.config")

ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent
CONFIG_DIR = ROOT_DIR / "CONFIG"
MT5_CONFIG_FILE = CONFIG_DIR / "mt5_config.json"
CONFIG_FILE = MT5_CONFIG_FILE

DEFAULT_CONFIG: Dict[str, Any] = {
    "terminal_path": "",
    "auto_discover": True,
    "last_connected_terminal": None,
    "last_connected_company": None,
    "last_connected_build": None,
}


SENSITIVE_KEYS = {"password", "pass", "pwd", "secret", "token", "credentials"}


def load_mt5_config() -> Dict[str, Any]:
    """Load MT5 configuration from disk, creating default if missing."""
    if not MT5_CONFIG_FILE.exists():
        return dict(DEFAULT_CONFIG)
    try:
        data = json.loads(MT5_CONFIG_FILE.read_text(encoding="utf-8"))
        merged = dict(DEFAULT_CONFIG)
        merged.update({k: v for k, v in data.items() if k.lower() not in SENSITIVE_KEYS})
        return merged
    except Exception as e:
        log.warning("Failed to parse %s: %s", MT5_CONFIG_FILE, e)
        return dict(DEFAULT_CONFIG)


def save_mt5_config(cfg: Dict[str, Any]) -> None:
    """Save MT5 configuration to disk. Strips any sensitive credentials/passwords."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = dict(DEFAULT_CONFIG)
        cleaned = {k: v for k, v in cfg.items() if k.lower() not in SENSITIVE_KEYS}
        data.update(cleaned)
        MT5_CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        log.info("Saved MT5 config to %s", MT5_CONFIG_FILE)
    except Exception as e:
        log.error("Failed to write %s: %s", MT5_CONFIG_FILE, e)


def get_saved_terminal_path() -> Optional[str]:
    """Returns saved terminal path if non-empty, else None."""
    cfg = load_mt5_config()
    p = cfg.get("terminal_path", "")
    if p and isinstance(p, str):
        p = p.strip()
    return p if p else None


def get_configured_terminal_path() -> Optional[str]:
    """Alias for get_saved_terminal_path."""
    return get_saved_terminal_path()


def set_saved_terminal_path(path: str, auto_discover: bool = False) -> None:
    """Updates saved terminal path."""
    cfg = load_mt5_config()
    cfg["terminal_path"] = path.strip()
    cfg["auto_discover"] = auto_discover
    save_mt5_config(cfg)
