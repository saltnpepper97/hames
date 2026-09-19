"""Z.ai API and explicitly separate GLM Coding Plan endpoint."""

from collections.abc import Mapping

import httpx

from hames.providers.base import ModelRequest
from hames.providers.chat_completions import ChatCompletionsProvider

ZAI_API_URL = "https://api.z.ai/api/paas/v4"
ZAI_CODING_URL = "https://api.z.ai/api/coding/paas/v4"


class ZaiProvider(ChatCompletionsProvider):
    adapter = "zai"
    brand = "Z.ai"
    default_base_url = ZAI_API_URL
    # Used only if this endpoint explicitly does not implement model discovery.
    catalog = ("glm-5.3", "glm-5.3-flash")

    def __init__(
        self,
        base_url: str | None = None,
        *,
        profile_id: str = "",
        api_key_env: str = "ZAI_API_KEY",
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
            api_key_env=api_key_env,
            timeout_seconds=timeout_seconds,
            default_model=default_model,
            supported_reasoning_efforts=supported_reasoning_efforts or ["low", "high", "max"],
            environ=environ,
            client=client,
        )
        self.api_key_file = api_key_file

    def _is_text_model(self, model_id: str) -> bool:
        return model_id.lower().startswith("glm-") and not any(
            marker in model_id.lower() for marker in ("image", "asr", "tts", "voice")
        )

    def _supports_image_input(self, model_id: str) -> bool:
        return model_id.lower() == "glm-5.3-flash" or "v" in model_id.lower().split("-")[-1]

    def _reasoning_supported(self, model_id: str) -> bool:
        return model_id.lower().startswith(("glm-4.5", "glm-4.6", "glm-4.7", "glm-5"))

    def _model_efforts(self, model_id: str) -> list[str]:
        if model_id.lower().startswith("glm-5.3"):
            return self.supported_reasoning_efforts
        return ["on"] if self._reasoning_supported(model_id) else []

    def _default_context_length(self, model_id: str) -> int | None:
        return 1_000_000 if model_id.lower() == "glm-5.3" else None

    def request_body(self, request: ModelRequest) -> dict[str, object]:
        body = super().request_body(request)
        forced = request.model.lower().startswith("glm-5.3")
        body["thinking"] = {
            "type": "enabled" if forced or request.reasoning_effort != "off" else "disabled",
            "clear_thinking": False,
        }
        if forced and request.reasoning_effort == "off":
            # Internal compaction requests ask for off. Forced-thinking models
            # require the documented lowest effort instead of an invalid toggle.
            body["reasoning_effort"] = "low"
        elif forced and request.reasoning_effort not in {"", "on", "off"}:
            body["reasoning_effort"] = request.reasoning_effort
        return body


class ZaiCodingProvider(ZaiProvider):
    adapter = "zai_coding"
    brand = "Z.ai Coding Plan"

    default_base_url = ZAI_CODING_URL
