import pytest

from hero1_ai_orchestrator import AIProviderError, OllamaProvider
from hero1_ai_orchestrator.base import ModelConfig


def test_ollama_failure_path_unreachable_host():
    provider = OllamaProvider(ModelConfig(host="http://127.0.0.1:9", model="x", timeout_s=1.0))
    health = provider.health()
    assert health["status"] in {"unavailable", "error"}
    with pytest.raises(AIProviderError):
        provider.structured("hi", {"type": "object"})


def _ollama_up() -> bool:
    try:
        import httpx
        httpx.get("http://127.0.0.1:11434/api/version", timeout=1.5).raise_for_status()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _ollama_up(), reason="local Ollama not running")
def test_ollama_small_structured_integration():
    """One tiny call against the local LMS Ollama (qwen2.5:0.5b).

    This validates the pipeline only. It says NOTHING about model quality or
    speed on the user's RTX 4090 laptop.
    """
    provider = OllamaProvider(ModelConfig(model="qwen2.5:0.5b", num_ctx=512, timeout_s=120, temperature=0.0))
    schema = {
        "type": "object",
        "required": ["ok"],
        "properties": {"ok": {"type": "boolean"}},
        "additionalProperties": False,
    }
    out = provider.structured(
        "Reply with a JSON object containing exactly one key 'ok' set to true. No other text.",
        schema,
    )
    assert out["ok"] is True
