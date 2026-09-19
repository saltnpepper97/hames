"""xAI Grok provider using the OpenAI-compatible Responses API."""

from __future__ import annotations

from collections.abc import Mapping

import httpx

from hames.providers.openai import OpenAIProvider

_NON_TEXT_MARKERS = (
    "audio",
    "embedding",
    "image",
    "imagine",
    "realtime",
    "stt",
    "tts",
    "video",
    "voice",
)

GROK_DEFAULT_CONTEXT_TOKENS = 500_000
_GROK_CONTEXT_WINDOWS: tuple[tuple[str, int], ...] = (
    ("grok-4.20", 2_000_000),
    ("grok-4-fast", 2_000_000),
    ("grok-4.6", 500_000),
    ("grok-4.5", 500_000),
    ("grok-3", 131_072),
    ("grok-2", 131_072),
    ("grok-4", 256_000),
    ("grok-", GROK_DEFAULT_CONTEXT_TOKENS),
)


def grok_context_length(model_id: str) -> int:
    lowered = model_id.lower()
    for prefix, tokens in _GROK_CONTEXT_WINDOWS:
        if lowered.startswith(prefix):
            return tokens
    return GROK_DEFAULT_CONTEXT_TOKENS


class XaiProvider(OpenAIProvider):
    adapter = "xai"
    brand = "xAI"

    def __init__(
        self,
        base_url: str = "https://api.x.ai/v1",
        *,
        profile_id: str = "xai",
        api_key_env: str = "XAI_API_KEY",
        api_key_file: str = "",
        timeout_seconds: float = 600.0,
        default_model: str = "",
        supported_reasoning_efforts: list[str] | None = None,
        environ: Mapping[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            base_url,
            profile_id=profile_id,
            api_key_env=api_key_env,
            api_key_file=api_key_file,
            timeout_seconds=timeout_seconds,
            default_model=default_model,
            supported_reasoning_efforts=supported_reasoning_efforts,
            environ=environ,
            client=client,
        )

    def _is_text_model(self, model_id: str) -> bool:
        lowered = model_id.lower()
        return lowered.startswith("grok-") and not any(
            marker in lowered for marker in _NON_TEXT_MARKERS
        )

    def _supports_image_input(self, model_id: str) -> bool:
        return self._is_text_model(model_id)

    def _reasoning_supported(self, model_id: str) -> bool:
        return self._is_text_model(model_id) and "non-reasoning" not in model_id.lower()

    def _default_context_length(self, model_id: str) -> int | None:
        return grok_context_length(model_id)
