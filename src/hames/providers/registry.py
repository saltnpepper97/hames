"""Provider construction from strict configuration."""

from __future__ import annotations

from hames.config import HamesConfig
from hames.providers.base import Provider
from hames.providers.llama_cpp import LlamaCppProvider
from hames.providers.ollama import OllamaProvider


def configured_providers(config: HamesConfig) -> dict[str, Provider]:
    providers: dict[str, Provider] = {}
    for profile_id, profile in config.providers.items():
        if profile.adapter == "llama_cpp":
            providers[profile_id] = LlamaCppProvider(
                profile.base_url,
                profile_id=profile_id,
                timeout_seconds=profile.timeout_seconds,
                default_model=profile.model,
                supported_reasoning_efforts=profile.supported_reasoning_efforts,
            )
        elif profile.adapter == "ollama":
            providers[profile_id] = OllamaProvider(
                profile.base_url,
                profile_id=profile_id,
                timeout_seconds=profile.timeout_seconds,
                supported_reasoning_efforts=profile.supported_reasoning_efforts,
            )
    return providers
