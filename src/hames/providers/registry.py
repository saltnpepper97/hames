"""Provider construction from strict configuration."""

from __future__ import annotations

from hames.config import HamesConfig
from hames.providers.base import Provider
from hames.providers.llama_cpp import LlamaCppProvider
from hames.providers.ollama import OllamaProvider


def configured_providers(config: HamesConfig) -> dict[str, Provider]:
    llama = config.providers.llama_cpp
    ollama = config.providers.ollama
    return {
        "llama_cpp": LlamaCppProvider(
            llama.base_url,
            timeout_seconds=llama.timeout_seconds,
        ),
        "ollama": OllamaProvider(
            ollama.base_url,
            timeout_seconds=ollama.timeout_seconds,
        ),
    }
