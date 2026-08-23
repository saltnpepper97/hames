"""llama.cpp OpenAI-compatible streaming adapter."""

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


class LlamaCppProvider:
    adapter = "llama_cpp"

    def __init__(
        self,
        base_url: str,
        *,
        profile_id: str = "llama_cpp",
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
            response = await self.client.get(f"{self.base_url}/v1/models")
            response.raise_for_status()
            body = JSON_OBJECT.validate_python(cast(object, response.json()))
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError("provider_unavailable", str(exc), retryable=True) from exc

        models: list[ProviderModel] = []
        raw_models = body.get("data", [])
        if not isinstance(raw_models, list):
            raise ProviderError(
                "malformed_provider_response", "llama.cpp models data is not a list"
            )
        for raw_object in raw_models:
            if not isinstance(raw_object, dict):
                continue
            raw = raw_object
            model_id = str(raw.get("id", ""))
            if not model_id:
                continue
            status_value = raw.get("status", "unknown")
            if isinstance(status_value, dict):
                status_value = status_value.get("value", "unknown")
            status = str(status_value)
            props = await self._model_props(model_id) if status in {"loaded", "sleeping"} else {}
            caps = props.get("chat_template_caps", {})
            if not isinstance(caps, dict):
                caps = {}
            reasoning = bool(caps.get("supports_reasoning_effort", False)) if props else None
            if reasoning is None and self.supported_reasoning_efforts:
                reasoning = True
            efforts = _reasoning_efforts(model_id, reasoning, self.supported_reasoning_efforts)
            if reasoning is None and efforts:
                reasoning = True
            meta = raw.get("meta", {})
            if not isinstance(meta, dict):
                meta = {}
            architecture = raw.get("architecture", {})
            if not isinstance(architecture, dict):
                architecture = {}
            models.append(
                ProviderModel(
                    id=model_id,
                    provider=self.profile_id,
                    status=status,
                    context_length=_optional_int(meta.get("n_ctx"))
                    or _nested_optional_int(props, "default_generation_settings", "n_ctx"),
                    parameter_size=_parameter_size(meta.get("n_params")),
                    quantization=_optional_str(meta.get("ftype")),
                    input_modalities=_string_list(architecture.get("input_modalities")),
                    output_modalities=_string_list(architecture.get("output_modalities")),
                    reasoning_supported=reasoning,
                    reasoning_efforts=efforts,
                )
            )
        return models

    async def _model_props(self, model_id: str) -> dict[str, JsonValue]:
        try:
            response = await self.client.get(f"{self.base_url}/props", params={"model": model_id})
            if response.status_code >= 400:
                return {}
            return JSON_OBJECT.validate_python(cast(object, response.json()))
        except (httpx.HTTPError, ValueError):
            return {}

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        messages = [_openai_message(message) for message in request.messages]
        if request.system:
            messages.insert(0, {"role": "system", "content": request.system})
        body: dict[str, object] = {
            "model": request.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_tokens": request.max_tokens,
            "reasoning_format": "deepseek",
        }
        if request.temperature is not None:
            body["temperature"] = request.temperature
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
            body["parallel_tool_calls"] = False
        if request.reasoning_effort == "off":
            body["chat_template_kwargs"] = {"enable_thinking": False}
        elif request.reasoning_effort:
            template_options: dict[str, object] = {"enable_thinking": True}
            if request.reasoning_effort != "on":
                template_options["reasoning_effort"] = request.reasoning_effort
            body["chat_template_kwargs"] = template_options

        try:
            async with self.client.stream(
                "POST", f"{self.base_url}/v1/chat/completions", json=body
            ) as response:
                response.raise_for_status()
                started = False
                finish_reason: str | None = None
                provider_request_id: str | None = None
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = JSON_OBJECT.validate_json(data)
                    except ValueError as exc:
                        raise ProviderError(
                            "malformed_provider_event", "llama.cpp emitted invalid SSE JSON"
                        ) from exc
                    request_id = _optional_str(chunk.get("id"))
                    provider_request_id = request_id or provider_request_id
                    if not started:
                        started = True
                        yield StreamEvent(
                            kind=StreamEventKind.STARTED, provider_request_id=request_id
                        )
                    usage = _usage_from_openai(chunk.get("usage"))
                    if usage is not None:
                        yield StreamEvent(kind=StreamEventKind.USAGE, usage=usage)
                    choices = chunk.get("choices", [])
                    if not isinstance(choices, list):
                        continue
                    for choice_object in choices:
                        if not isinstance(choice_object, dict):
                            continue
                        choice = choice_object
                        delta = choice.get("delta", {})
                        if isinstance(delta, dict):
                            reasoning = delta.get("reasoning_content", "")
                            content = delta.get("content", "")
                            if reasoning:
                                yield StreamEvent(
                                    kind=StreamEventKind.REASONING_DELTA,
                                    text=str(reasoning),
                                )
                            if content:
                                yield StreamEvent(
                                    kind=StreamEventKind.TEXT_DELTA, text=str(content)
                                )
                            for tool_call in _tool_call_deltas(delta.get("tool_calls")):
                                yield StreamEvent(
                                    kind=StreamEventKind.TOOL_CALL_DELTA,
                                    tool_call=tool_call,
                                )
                        finish = choice.get("finish_reason")
                        if finish:
                            value = str(finish)
                            if finish_reason is not None and finish_reason != value:
                                raise ProviderError(
                                    "malformed_provider_event",
                                    "llama.cpp emitted conflicting finish reasons",
                                )
                            finish_reason = value
                if not started:
                    raise ProviderError("empty_provider_response", "llama.cpp emitted no events")
                if finish_reason is None:
                    raise ProviderError(
                        "incomplete_provider_response",
                        "llama.cpp stream ended without a finish reason",
                    )
                yield StreamEvent(
                    kind=StreamEventKind.COMPLETED,
                    finish_reason=finish_reason,
                    provider_request_id=provider_request_id,
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


def _usage_from_openai(value: JsonValue) -> Usage | None:
    if not isinstance(value, dict):
        return None
    details = value.get("prompt_tokens_details", {})
    cached = details.get("cached_tokens") if isinstance(details, dict) else None
    completion_details = value.get("completion_tokens_details", {})
    reasoning = (
        completion_details.get("reasoning_tokens") if isinstance(completion_details, dict) else None
    )
    return Usage(
        input_tokens=_int_default(value.get("prompt_tokens")),
        output_tokens=_int_default(value.get("completion_tokens")),
        cached_input_tokens=_optional_int(cached),
        reasoning_tokens=_optional_int(reasoning),
        provider_reported_cost=_optional_float(value.get("cost")),
    )


def _openai_message(message: object) -> dict[str, object]:
    from hames.providers.base import ProviderMessage

    value = ProviderMessage.model_validate(message)
    result: dict[str, object] = {"role": value.role, "content": value.content}
    if value.reasoning_content:
        result["reasoning_content"] = value.reasoning_content
    if value.tool_calls:
        result["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, separators=(",", ":")),
                },
            }
            for call in value.tool_calls
        ]
    if value.tool_call_id is not None:
        result["tool_call_id"] = value.tool_call_id
    if value.tool_name is not None:
        result["name"] = value.tool_name
    return result


def _tool_call_deltas(value: JsonValue) -> list[ToolCallDelta]:
    if not isinstance(value, list):
        return []
    result: list[ToolCallDelta] = []
    for fallback_index, raw in enumerate(value):
        if not isinstance(raw, dict):
            continue
        function = raw.get("function", {})
        if not isinstance(function, dict):
            function = {}
        raw_index = raw.get("index", fallback_index)
        index = int(raw_index) if isinstance(raw_index, int | float) else fallback_index
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


def _reasoning_efforts(model_id: str, supported: bool | None, configured: list[str]) -> list[str]:
    if supported is False:
        return []
    if configured:
        return configured
    if "qwen3.8" in model_id.lower():
        return ["low", "medium", "xhigh"]
    return ["on"] if supported is True else []


def _nested_optional_int(value: dict[str, JsonValue], outer: str, inner: str) -> int | None:
    nested = value.get(outer)
    return _optional_int(nested.get(inner)) if isinstance(nested, dict) else None


def _optional_int(value: object) -> int | None:
    return int(value) if isinstance(value, int | float) else None


def _optional_float(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _int_default(value: object) -> int:
    return int(value) if isinstance(value, int | float) else 0


def _optional_str(value: object) -> str | None:
    return str(value) if value is not None else None


def _string_list(value: JsonValue) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _parameter_size(value: object) -> str | None:
    if not isinstance(value, int | float):
        return None
    return f"{float(value) / 1_000_000_000:.1f}B"
