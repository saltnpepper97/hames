"""Ollama native streaming adapter."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import cast

import httpx

from hames.providers.base import (
    JSON_OBJECT,
    JsonValue,
    ModelRequest,
    ProviderError,
    ProviderModel,
    StreamEvent,
    StreamEventKind,
    ToolCallDelta,
    Usage,
)


class OllamaProvider:
    adapter = "ollama"

    def __init__(
        self,
        base_url: str,
        *,
        profile_id: str = "ollama",
        timeout_seconds: float = 120.0,
        supported_reasoning_efforts: list[str] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.profile_id = profile_id
        self.base_url = base_url.rstrip("/")
        self.supported_reasoning_efforts = supported_reasoning_efforts or []
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
            capabilities, context_length = await self._model_details(model_id)
            reasoning = "thinking" in capabilities
            efforts = _reasoning_efforts(model_id, reasoning, self.supported_reasoning_efforts)
            models.append(
                ProviderModel(
                    id=model_id,
                    provider=self.profile_id,
                    status="available",
                    context_length=context_length,
                    parameter_size=_optional_str(details.get("parameter_size")),
                    quantization=_optional_str(details.get("quantization_level")),
                    input_modalities=["text"],
                    output_modalities=["text"],
                    reasoning_supported=reasoning,
                    reasoning_efforts=efforts,
                )
            )
        return models

    async def _model_details(self, model_id: str) -> tuple[set[str], int | None]:
        try:
            response = await self.client.post(f"{self.base_url}/api/show", json={"model": model_id})
            if response.status_code >= 400:
                return set(), None
            body = JSON_OBJECT.validate_python(cast(object, response.json()))
            capabilities = body.get("capabilities", [])
            supported: set[str] = (
                {str(value) for value in capabilities}
                if isinstance(capabilities, list)
                else set[str]()
            )
            model_info = body.get("model_info", {})
            context_length = None
            if isinstance(model_info, dict):
                for key, value in model_info.items():
                    if str(key).endswith(".context_length") and isinstance(value, int | float):
                        context_length = int(value)
                        break
            return supported, context_length
        except (httpx.HTTPError, ValueError):
            return set(), None

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        messages = [_ollama_message(message) for message in request.messages]
        if request.system:
            messages.insert(0, {"role": "system", "content": request.system})
        options: dict[str, object] = {"num_predict": request.max_tokens}
        if request.temperature is not None:
            options["temperature"] = request.temperature
        body: dict[str, object] = {
            "model": request.model,
            "messages": messages,
            "stream": True,
            "options": options,
        }
        if request.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in request.tools
            ]
        if request.reasoning_effort == "off":
            body["think"] = False
        elif request.reasoning_effort:
            body["think"] = True if request.reasoning_effort == "on" else request.reasoning_effort

        started = False
        finish_reason: str | None = None
        pending_tool_calls: list[ToolCallDelta] = []
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
                    if finish_reason is not None:
                        raise ProviderError(
                            "malformed_provider_event",
                            "Ollama emitted data after its completed chunk",
                        )
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
                        if message.get("tool_calls"):
                            pending_tool_calls = _tool_call_deltas(message.get("tool_calls"))
                    if bool(chunk.get("done", False)):
                        for tool_call in pending_tool_calls:
                            yield StreamEvent(
                                kind=StreamEventKind.TOOL_CALL_DELTA,
                                tool_call=tool_call,
                            )
                        if finish_reason is not None:
                            raise ProviderError(
                                "malformed_provider_event",
                                "Ollama emitted more than one completed chunk",
                            )
                        usage = Usage(
                            input_tokens=_int_default(chunk.get("prompt_eval_count")),
                            output_tokens=_int_default(chunk.get("eval_count")),
                        )
                        yield StreamEvent(kind=StreamEventKind.USAGE, usage=usage)
                        finish_reason = str(chunk.get("done_reason", "stop"))
                if not started:
                    raise ProviderError("empty_provider_response", "Ollama emitted no events")
                if finish_reason is None:
                    raise ProviderError(
                        "incomplete_provider_response",
                        "Ollama stream ended without a completed chunk",
                    )
                yield StreamEvent(
                    kind=StreamEventKind.COMPLETED,
                    finish_reason=finish_reason,
                )
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


def _ollama_message(message: object) -> dict[str, object]:
    from hames.providers.base import ProviderMessage

    value = ProviderMessage.model_validate(message)
    result: dict[str, object] = {"role": value.role, "content": value.content}
    if value.reasoning_content:
        result["thinking"] = value.reasoning_content
    if value.tool_calls:
        result["tool_calls"] = [
            {
                "type": "function",
                "function": {
                    "index": index,
                    "name": call.name,
                    "arguments": call.arguments,
                },
            }
            for index, call in enumerate(value.tool_calls)
        ]
    if value.tool_name is not None:
        result["tool_name"] = value.tool_name
    return result


def _tool_call_deltas(value: JsonValue) -> list[ToolCallDelta]:
    if not isinstance(value, list):
        return []
    result: list[ToolCallDelta] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            continue
        function = raw.get("function", {})
        if not isinstance(function, dict):
            function = {}
        arguments = function.get("arguments", "")
        result.append(
            ToolCallDelta(
                index=index,
                provider_call_id=_optional_str(raw.get("id")),
                name=_optional_str(function.get("name")),
                arguments_delta=(
                    arguments
                    if isinstance(arguments, str)
                    else json.dumps(arguments, separators=(",", ":"))
                ),
            )
        )
    return result


def _reasoning_efforts(model_id: str, supported: bool, configured: list[str]) -> list[str]:
    if not supported:
        return []
    if configured:
        return configured
    if "gpt-oss" in model_id.lower():
        return ["low", "medium", "high"]
    return ["on"]


def _int_default(value: object) -> int:
    return int(value) if isinstance(value, int | float) else 0
