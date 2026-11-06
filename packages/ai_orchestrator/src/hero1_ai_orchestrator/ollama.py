from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

import httpx
import jsonschema

from .base import AIProvider, AIProviderError, AIValidationError, ModelConfig

log = logging.getLogger("hero1.ai.ollama")


def config_from_env() -> ModelConfig:
    return ModelConfig(
        host=os.environ.get("HERO1_OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/"),
        model=os.environ.get("HERO1_OLLAMA_MODEL", ModelConfig.model),
        num_ctx=int(os.environ.get("HERO1_OLLAMA_NUM_CTX", "8192")),
        temperature=float(os.environ.get("HERO1_OLLAMA_TEMPERATURE", "0.0")),
        timeout_s=float(os.environ.get("HERO1_OLLAMA_TIMEOUT_S", "60")),
    )


class OllamaProvider(AIProvider):
    name = "ollama"

    def __init__(self, config: Optional[ModelConfig] = None):
        self.config = config or config_from_env()

    # ------------------------------------------------------------------
    def health(self) -> dict:
        try:
            r = httpx.get(f"{self.config.host}/api/version", timeout=2.5)
            r.raise_for_status()
            version = r.json().get("version", "unknown")
            # list models without failing the probe when tags endpoint hiccups
            models: list[str] = []
            try:
                tags = httpx.get(f"{self.config.host}/api/tags", timeout=2.5)
                if tags.status_code == 200:
                    models = [m.get("name", "?") for m in tags.json().get("models", [])]
            except Exception:
                pass
            return {"status": "healthy", "provider": self.name, "host": self.config.host,
                    "version": version, "models": models, "configured_model": self.config.model}
        except httpx.ConnectError:
            return {"status": "unavailable", "provider": self.name, "host": self.config.host,
                    "detail": "connection refused — is `ollama serve` running?"}
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "provider": self.name, "host": self.config.host,
                    "detail": f"{type(exc).__name__}: {exc}"}

    # ------------------------------------------------------------------
    def _generate(self, payload: dict) -> dict:
        try:
            r = httpx.post(f"{self.config.host}/api/generate", json=payload,
                           timeout=self.config.timeout_s)
            r.raise_for_status()
            return r.json()
        except httpx.TimeoutException as exc:
            raise AIProviderError(f"ollama timed out after {self.config.timeout_s}s") from exc
        except httpx.HTTPStatusError as exc:
            raise AIProviderError(f"ollama HTTP {exc.response.status_code}: {exc.response.text[:300]}") from exc
        except httpx.HTTPError as exc:
            raise AIProviderError(f"ollama unreachable: {type(exc).__name__}: {exc}") from exc

    def chat(self, messages: list[dict], *, json_schema: Optional[dict] = None) -> dict:
        prompt = "\n".join(f"[{m.get('role', 'user')}] {m.get('content', '')}" for m in messages)
        fmt: Any = "json" if json_schema else None
        payload: dict[str, Any] = {
            "model": self.config.model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_ctx": self.config.num_ctx, "temperature": self.config.temperature},
        }
        if fmt:
            payload["format"] = fmt
        raw = self._generate(payload).get("response", "")
        if not json_schema:
            return {"content": raw}
        return {"content": self._validate(raw, json_schema)}

    def structured(self, prompt: str, schema: dict, *, system: Optional[str] = None) -> dict:
        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        payload: dict[str, Any] = {
            "model": self.config.model,
            "prompt": full_prompt,
            "stream": False,
            "format": schema,  # Ollama accepts a full JSON Schema for constrained decoding
            "options": {"num_ctx": self.config.num_ctx, "temperature": self.config.temperature},
        }
        raw = self._generate(payload).get("response", "")
        return self._validate(raw, schema)

    def _validate(self, raw: str, schema: dict) -> dict:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AIValidationError(f"model returned non-JSON output: {exc}; head={raw[:200]!r}") from exc
        try:
            jsonschema.validate(data, schema)
        except jsonschema.ValidationError as exc:
            raise AIValidationError(f"output failed schema validation at {list(exc.path)}: {exc.message}") from exc
        return data


def get_ollama_provider() -> OllamaProvider:
    return OllamaProvider()
