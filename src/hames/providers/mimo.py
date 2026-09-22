"""Xiaomi MiMo API and separate Token Plan Chat Completions endpoints."""

from collections.abc import Mapping

import httpx

from hames.providers.base import ModelRequest
from hames.providers.chat_completions import ChatCompletionsProvider

MIMO_API_URL = "https://api.xiaomimimo.com/v1"
MIMO_TOKEN_PLAN_URL = "https://token-plan-cn.xiaomimimo.com/v1"


class MimoProvider(ChatCompletionsProvider):
    adapter = "mimo"
    brand = "Xiaomi MiMo"
    default_base_url = MIMO_API_URL
    default_api_key_env = "MIMO_API_KEY"

    def __init__(
        self,
        base_url: str | None = None,
        *,
        profile_id: str = "",
        api_key_env: str = "",
        api_key_file: str = "",
        timeout_seconds: float = 600,
        default_model: str = "",
        supported_reasoning_efforts: list[str] | None = None,
        environ: Mapping[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            base_url or self.default_base_url,
            profile_id=profile_id or self.adapter,
            api_key_env=api_key_env or self.default_api_key_env,
            api_key_file=api_key_file,
            timeout_seconds=timeout_seconds,
            default_model=default_model,
            supported_reasoning_efforts=["on"],
            environ=environ,
            client=client,
        )

    def _is_text_model(self, model_id: str) -> bool:
        return model_id.lower().startswith("mimo-") and not any(
            marker in model_id.lower().split("-")
            for marker in ("tts", "asr", "voiceclone", "voicedesign")
        )

    def _supports_image_input(self, model_id: str) -> bool:
        return model_id.lower() in {"mimo-v2.5", "mimo-v2-omni"} or model_id.lower().startswith(
            "mimo-v2.6-"
        )

    def _reasoning_supported(self, model_id: str) -> bool:
        return self._is_text_model(model_id)

    def _default_context_length(self, model_id: str) -> int | None:
        if model_id.lower() in {"mimo-v2.5", "mimo-v2.5-pro", "mimo-v2.5-pro-ultraspeed"}:
            return 1_048_576
        if model_id.lower() in {"mimo-v2.6-pro", "mimo-v2.6-flash", "mimo-v2.6-pro-ultraspeed"}:
            return 1_000_000
        return None

    def request_body(self, request: ModelRequest) -> dict[str, object]:
        body = super().request_body(request)
        body["max_completion_tokens"] = body.pop("max_tokens")
        # MiMo streams usage without OpenAI's optional stream_options field.
        body.pop("stream_options", None)
        thinking = request.reasoning_effort != "off"
        body["thinking"] = {"type": "enabled" if thinking else "disabled"}
        if thinking:
            body.pop("temperature", None)
        return body


class MimoTokenPlanProvider(MimoProvider):
    adapter = "mimo_token_plan"
    brand = "Xiaomi MiMo Token Plan"
    default_base_url = MIMO_TOKEN_PLAN_URL
    default_api_key_env = "MIMO_TOKEN_PLAN_API_KEY"
