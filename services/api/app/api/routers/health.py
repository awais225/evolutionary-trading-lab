from __future__ import annotations

import shutil
import subprocess
import time

from fastapi import APIRouter, Depends

from app.config import get_settings
from app.schemas import HealthOut
from hero1_ai_orchestrator import get_ollama_provider
from hero1_mt5_adapter import probe_mt5_availability
from hero1_storage.engine import get_engine, registry_url
from sqlalchemy import text

router = APIRouter(tags=["health"])

_cache: dict = {"ts": 0.0, "payload": None}
_CACHE_TTL = 5.0
_mt5_cache: dict = {"ts": 0.0, "payload": None}
_MT5_TTL = 30.0
_gpu_cache: dict = {"ts": 0.0, "payload": None}
_GPU_TTL = 30.0


def _registry_check() -> dict:
    try:
        eng = get_engine(registry_url())
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "healthy"}
    except Exception as exc:  # noqa: BLE001
        detail = str(exc)
        if "no such table" in detail or "unable to open" in detail:
            return {"status": "unavailable", "detail": "registry database not initialized (run: python scripts/init_db.py --create)"}
        return {"status": "error", "detail": f"{type(exc).__name__}: {detail[:200]}"}


def _ollama_check() -> dict:
    try:
        return get_ollama_provider().health()
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": f"{type(exc).__name__}: {exc}"}


def _mt5_check() -> dict:
    now = time.time()
    if now - _mt5_cache["ts"] > _MT5_TTL:
        _mt5_cache.update(ts=now, payload=probe_mt5_availability())
    return _mt5_cache["payload"]


def _gpu_check() -> dict:
    now = time.time()
    if now - _gpu_cache["ts"] <= _GPU_TTL:
        return _gpu_cache["payload"]
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        payload = {"status": "not_detectable", "detail": "nvidia-smi not found — no NVIDIA GPU/driver visible from this environment"}
    else:
        try:
            out = subprocess.run(
                [nvidia_smi, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=4, check=False,
            ).stdout.strip()
            payload = {"status": "healthy", "detail": out or "nvidia-smi returned no GPU rows"}
        except Exception as exc:  # noqa: BLE001
            payload = {"status": "error", "detail": f"nvidia-smi failed: {exc}"}
    _gpu_cache.update(ts=now, payload=payload)
    return payload


@router.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    now = time.time()
    if _cache["payload"] is not None and now - _cache["ts"] < _CACHE_TTL:
        return HealthOut(**_cache["payload"])
    registry = _registry_check()
    checks = {
        "registry_db": registry,
        "ollama": _ollama_check(),
        "mt5": _mt5_check(),
        "gpu": _gpu_check(),
    }
    status = "ok" if registry["status"] == "healthy" else "degraded"
    payload = {"status": status, "version": get_settings().app_version, "checks": checks}
    _cache.update(ts=now, payload=payload)
    return HealthOut(**payload)
