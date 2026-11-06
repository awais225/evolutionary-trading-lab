"""Provider-independent AI interface.

Hard guarantees:
  * No model text is ever executed as code. Outputs are validated JSON only.
  * Every structured call is validated against a JSON Schema; failures raise
    AIProviderError (or AIValidationError) — callers decide, nothing silent.
  * Budgets (context tokens, timeout, retries) are explicit configuration.
"""
from .base import AIProvider, AIProviderError, AIValidationError, ModelConfig
from .ollama import OllamaProvider, get_ollama_provider

__all__ = [
    "AIProvider", "AIProviderError", "AIValidationError", "ModelConfig",
    "OllamaProvider", "get_ollama_provider",
]
