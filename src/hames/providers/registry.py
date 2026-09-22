"""Provider construction from strict configuration."""

from __future__ import annotations

from hames.config import HamesConfig
from hames.providers.base import Provider
from hames.providers.codex import CodexProvider
from hames.providers.deepseek import DeepSeekProvider
from hames.providers.grok import GrokProvider
from hames.providers.llama_cpp import LlamaCppProvider
from hames.providers.mimo import MimoProvider, MimoTokenPlanProvider
from hames.providers.ollama import OllamaProvider
from hames.providers.openai import OpenAIProvider
from hames.providers.xai import XaiProvider
from hames.providers.zai import ZaiCodingProvider, ZaiProvider


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
        elif profile.adapter == "openai":
            providers[profile_id] = OpenAIProvider(
                profile.base_url,
                profile_id=profile_id,
                api_key_env=profile.api_key_env,
                api_key_file=profile.api_key_file,
                timeout_seconds=profile.timeout_seconds,
                default_model=profile.model,
                supported_reasoning_efforts=profile.supported_reasoning_efforts,
            )
        elif profile.adapter == "xai":
            providers[profile_id] = XaiProvider(
                profile.base_url,
                profile_id=profile_id,
                api_key_env=profile.api_key_env,
                api_key_file=profile.api_key_file,
                timeout_seconds=profile.timeout_seconds,
                default_model=profile.model,
                supported_reasoning_efforts=profile.supported_reasoning_efforts,
            )
        elif profile.adapter == "grok":
            providers[profile_id] = GrokProvider(
                profile.base_url,
                profile_id=profile_id,
                timeout_seconds=profile.timeout_seconds,
                default_model=profile.model,
                supported_reasoning_efforts=profile.supported_reasoning_efforts,
            )
        elif profile.adapter in {"deepseek", "zai", "zai_coding", "mimo", "mimo_token_plan"}:
            provider_class = {
                "deepseek": DeepSeekProvider,
                "mimo": MimoProvider,
                "mimo_token_plan": MimoTokenPlanProvider,
                "zai": ZaiProvider,
                "zai_coding": ZaiCodingProvider,
            }[profile.adapter]
            providers[profile_id] = provider_class(
                profile.base_url,
                profile_id=profile_id,
                api_key_env=profile.api_key_env,
                api_key_file=profile.api_key_file,
                timeout_seconds=profile.timeout_seconds,
                default_model=profile.model,
                supported_reasoning_efforts=profile.supported_reasoning_efforts,
            )
        elif profile.adapter == "codex":
            providers[profile_id] = CodexProvider(
                profile_id=profile_id,
                timeout_seconds=profile.timeout_seconds,
                default_model=profile.model,
            )
    return providers
