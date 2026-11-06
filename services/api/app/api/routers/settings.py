from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import get_settings, settings_data_root_display
from app.deps import get_registry_session
from app.schemas import SettingsOut, SettingsPut, _DEFAULT_SETTINGS
from hero1_eventbus import get_event_publisher
from hero1_storage.repositories import SettingsRepository

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


def _merged(repo: SettingsRepository) -> dict:
    stored = repo.get_all()
    merged = dict(_DEFAULT_SETTINGS)
    merged.update(stored)
    return merged


@router.get("", response_model=SettingsOut)
def read_settings(registry: Session = Depends(get_registry_session)) -> SettingsOut:
    settings = _merged(SettingsRepository(registry))
    cfg = get_settings()
    environment = {
        "data_root": settings_data_root_display(),
        "ollama_host": cfg.ollama_host,
        "ollama_model": cfg.ollama_model,
        "config_source": "environment / .env (never committed) + registry DB for app preferences",
    }
    return SettingsOut(settings=settings, environment=environment)


@router.put("", response_model=SettingsOut)
def update_settings(body: SettingsPut, registry: Session = Depends(get_registry_session)) -> SettingsOut:
    repo = SettingsRepository(registry)
    repo.set_many(body.settings, updated_by="user")
    merged = _merged(repo)
    get_event_publisher().publish(
        category="SYSTEM", severity="INFO", subsystem="settings",
        message=f"settings updated: {', '.join(sorted(body.settings))}",
        payload={"updated_keys": sorted(body.settings)},
    )
    cfg = get_settings()
    environment = {
        "data_root": settings_data_root_display(),
        "ollama_host": cfg.ollama_host,
        "ollama_model": cfg.ollama_model,
        "config_source": "environment / .env (never committed) + registry DB for app preferences",
    }
    return SettingsOut(settings=merged, environment=environment)
