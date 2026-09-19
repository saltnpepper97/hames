"""DeepSeek's authenticated Chat Completions API."""

from collections.abc import Mapping

import httpx

from hames.providers.base import ModelRequest
from hames.providers.chat_completions import ChatCompletionsProvider


class DeepSeekProvider(ChatCompletionsProvider):
    adapter = "deepseek"
    brand = "DeepSeek"

    def __init__(
        self,
        base_url: str = "https://api.deepseek.com",
        *,
        profile_id: str = "deepseek",
        api_key_env: str = "DEEPSEEK_API_KEY",
        api_key_file: str = "",
        timeout_seconds: float = 600,
        default_model: str = "",
        supported_reasoning_efforts: list[str] | None = None,
        environ: Mapping[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            base_url,
            profile_id=profile_id,
            api_key_env=api_key_env,
            timeout_seconds=timeout_seconds,
            default_model=default_model,
            supported_reasoning_efforts=supported_reasoning_efforts or ["low", "high", "max"],
            environ=environ,
            client=client,
        )
        self.api_key_file = api_key_file

    def _is_text_model(self, model_id: str) -> bool:
        return model_id.startswith("deepseek-")

    def _supports_image_input(self, model_id: str) -> bool:
        return model_id in {"deepseek-flash", "deepseek-v4-flash-vision-exp"}

    def _reasoning_supported(self, model_id: str) -> bool:
        return self._is_text_model(model_id)

    def _default_context_length(self, model_id: str) -> int | None:
        if model_id == "deepseek-flash" or model_id.startswith("deepseek-v4"):
            return 1_000_000
        return None

    def request_body(self, request: ModelRequest) -> dict[str, object]:
        body = super().request_body(request)
        body["thinking"] = {"type": "disabled" if request.reasoning_effort == "off" else "enabled"}
        if request.reasoning_effort not in {"", "on", "off"}:
            body["reasoning_effort"] = request.reasoning_effort
        return body
