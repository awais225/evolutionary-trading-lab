"""Application settings (infrastructure). App-level user preferences live in the
registry DB (see /api/v1/settings) to keep secrets out of both."""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HERO1_", env_file=".env", extra="ignore")

    data_root: Optional[str] = None           # None -> safe default (~/.hero1/data)
    log_level: str = "INFO"
    ollama_host: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5-coder:14b-instruct-q5_K_M"
    ollama_num_ctx: int = 8192
    ollama_timeout_s: float = 60.0
    app_version: str = "1.0.0"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def settings_data_root_display() -> str:
    return os.environ.get("HERO1_DATA_ROOT") or "(unset — using safe default ~/.hero1/data)"
