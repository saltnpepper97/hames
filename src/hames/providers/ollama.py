"""Ollama native streaming adapter."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import cast

import httpx

from hames.providers.base import (
    JSON_OBJECT,
    ModelRequest,
    ProviderError,
    ProviderModel,
    StreamEvent,
    StreamEventKind,
    Usage,
)


class OllamaProvider:
    name = "ollama"

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 120.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._owned_client = client is None
        self.client = client or httpx.AsyncClient(timeout=timeout_seconds)

    async def aclose(self) -> None:
        if self._owned_client:
            await self.client.aclose()

    async def list_models(self) -> list[ProviderModel]:
        try:
            response = await self.client.get(f"{self.base_url}/api/tags")
            response.raise_for_status()
            body = JSON_OBJECT.validate_python(cast(object, response.json()))
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError("provider_unavailable", str(exc), retryable=True) from exc
        raw_models = body.get("models", [])
        if not isinstance(raw_models, list):
            raise ProviderError("malformed_provider_response", "Ollama models is not a list")
        models: list[ProviderModel] = []
        for raw_object in raw_models:
            if not isinstance(raw_object, dict):
                continue
            raw = raw_object
            model_id = str(raw.get("model") or raw.get("name") or "")
            if not model_id:
                continue
            details = raw.get("details", {})
            if not isinstance(details, dict):
                details = {}
            capabilities = await self._capabilities(model_id)
            models.append(
                ProviderModel(
                    id=model_id,
                    provider=self.name,
                    status="available",
                    parameter_size=_optional_str(details.get("parameter_size")),
                    quantization=_optional_str(details.get("quantization_level")),
                    input_modalities=["text"],
                    output_modalities=["text"],
                    reasoning_supported="thinking" in capabilities,
                    reasoning_efforts=["low", "medium", "high"]
                    if "thinking" in capabilities
                    else [],
                )
            )
        return models

    async def _capabilities(self, model_id: str) -> set[str]:
        try:
            response = await self.client.post(f"{self.base_url}/api/show", json={"model": model_id})
            if response.status_code >= 400:
                return set()
            body = JSON_OBJECT.validate_python(cast(object, response.json()))
            capabilities = body.get("capabilities", [])
            return (
                {str(value) for value in capabilities} if isinstance(capabilities, list) else set()
            )
        except (httpx.HTTPError, ValueError):
            return set()

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        messages = [
            message.model_dump(exclude={"reasoning_content"}) for message in request.messages
        ]
        if request.system:
            messages.insert(0, {"role": "system", "content": request.system})
        body: dict[str, object] = {
            "model": request.model,
            "messages": messages,
            "stream": True,
            "options": {"num_predict": request.max_tokens},
        }
        if request.reasoning_effort == "off":
            body["think"] = False
        elif request.reasoning_effort:
            body["think"] = request.reasoning_effort

        started = False
        try:
            async with self.client.stream(
                "POST", f"{self.base_url}/api/chat", json=body
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = JSON_OBJECT.validate_json(line)
                    except ValueError as exc:
                        raise ProviderError(
                            "malformed_provider_event", "Ollama emitted invalid NDJSON"
                        ) from exc
                    if not started:
                        started = True
                        yield StreamEvent(kind=StreamEventKind.STARTED)
                    message = chunk.get("message", {})
                    if isinstance(message, dict):
                        thinking = message.get("thinking", "")
                        content = message.get("content", "")
                        if thinking:
                            yield StreamEvent(
                                kind=StreamEventKind.REASONING_DELTA, text=str(thinking)
                            )
                        if content:
                            yield StreamEvent(kind=StreamEventKind.TEXT_DELTA, text=str(content))
                    if bool(chunk.get("done", False)):
                        usage = Usage(
                            input_tokens=_int_default(chunk.get("prompt_eval_count")),
                            output_tokens=_int_default(chunk.get("eval_count")),
                        )
                        yield StreamEvent(kind=StreamEventKind.USAGE, usage=usage)
                        yield StreamEvent(
                            kind=StreamEventKind.COMPLETED,
                            finish_reason=str(chunk.get("done_reason", "stop")),
                        )
                if not started:
                    raise ProviderError("empty_provider_response", "Ollama emitted no events")
        except ProviderError:
            raise
        except httpx.TimeoutException as exc:
            raise ProviderError("provider_timeout", str(exc), retryable=True) from exc
        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                "provider_http_error", str(exc), retryable=exc.response.status_code >= 500
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError("provider_transport_error", str(exc), retryable=True) from exc


def _optional_str(value: object) -> str | None:
    return str(value) if value is not None else None


def _int_default(value: object) -> int:
    return int(value) if isinstance(value, int | float) else 0
