"""llama.cpp Responses API streaming adapter."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
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
        default_model: str = "",
        supported_reasoning_efforts: list[str] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.profile_id = profile_id
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model
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
            status_object = raw.get("status")
            status_args: list[str] = []
            if isinstance(status_object, dict):
                status_args = _string_list(status_object.get("args"))
                status_value = status_object.get("value", "unknown")
            else:
                status_value = status_object or "unknown"
            status = str(status_value)
            props = await self._model_props(model_id) if status in {"loaded", "sleeping"} else {}
            caps = props.get("chat_template_caps", {})
            if not isinstance(caps, dict):
                caps = {}
            supports_effort = bool(caps.get("supports_reasoning_effort", False))
            reasoning = True if supports_effort or _has_reasoning_option(status_args) else None
            if (
                reasoning is None
                and model_id == self.default_model
                and self.supported_reasoning_efforts
            ):
                reasoning = True
            efforts = _reasoning_efforts(
                model_id,
                reasoning,
                self.supported_reasoning_efforts if model_id == self.default_model else [],
            )
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
        body: dict[str, object] = {
            "model": request.model,
            "input": _response_input(request.messages),
            "instructions": request.system,
            "stream": True,
            "store": False,
            "max_output_tokens": request.max_tokens,
            # llama.cpp attaches its exact slot counters to streamed Responses
            # events when this is enabled. The final response also carries the
            # normalized usage object used below.
            "timings_per_token": True,
            "parallel_tool_calls": False,
        }
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                }
                for tool in request.tools
            ]
        if request.reasoning_effort == "off":
            body["reasoning_budget_tokens"] = 0
        elif request.reasoning_effort and request.reasoning_effort != "on":
            body["reasoning"] = {"effort": request.reasoning_effort}

        try:
            async with self.client.stream(
                "POST", f"{self.base_url}/v1/responses", json=body
            ) as response:
                response.raise_for_status()
                started = False
                completed = False
                provider_request_id: str | None = None
                next_tool_index = 0
                tool_indices: dict[str, int] = {}
                finalized_tool_items: dict[str, dict[str, JsonValue]] = {}
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        event = JSON_OBJECT.validate_json(data)
                    except ValueError as exc:
                        raise ProviderError(
                            "malformed_provider_event", "llama.cpp emitted invalid SSE JSON"
                        ) from exc
                    event_type = str(event.get("type", ""))
                    if event_type == "response.created":
                        if started:
                            raise ProviderError(
                                "provider_protocol_error", "llama.cpp started a response twice"
                            )
                        response_object = event.get("response", {})
                        if isinstance(response_object, dict):
                            provider_request_id = _optional_str(response_object.get("id"))
                        started = True
                        yield StreamEvent(
                            kind=StreamEventKind.STARTED,
                            provider_request_id=provider_request_id,
                        )
                    elif event_type == "response.output_item.added":
                        item = event.get("item", {})
                        if not isinstance(item, dict) or item.get("type") != "function_call":
                            continue
                        item_id = _optional_str(item.get("id"))
                        if item_id is not None and item_id not in tool_indices:
                            tool_indices[item_id] = next_tool_index
                            next_tool_index += 1
                    elif event_type in {
                        "response.reasoning_summary_text.delta",
                        "response.reasoning_text.delta",
                    }:
                        yield StreamEvent(
                            kind=StreamEventKind.REASONING_DELTA,
                            text=str(event.get("delta", "")),
                        )
                    elif event_type == "response.output_text.delta":
                        yield StreamEvent(
                            kind=StreamEventKind.TEXT_DELTA,
                            text=str(event.get("delta", "")),
                        )
                    elif event_type == "response.output_item.done":
                        item = event.get("item", {})
                        if not isinstance(item, dict) or item.get("type") != "function_call":
                            continue
                        item_id = _optional_str(item.get("id")) or f"tool-{next_tool_index}"
                        index = tool_indices.get(item_id)
                        if index is None:
                            index = next_tool_index
                            next_tool_index += 1
                            tool_indices[item_id] = index
                        finalized_tool_items[item_id] = item
                    elif event_type in {"response.completed", "response.incomplete"}:
                        response_object = event.get("response", {})
                        if not isinstance(response_object, dict):
                            raise ProviderError(
                                "malformed_provider_event",
                                "llama.cpp completion omitted response",
                            )
                        # A completed output item is authoritative. If a server
                        # omitted output_item.done, recover it from the final
                        # response. Never expose an incomplete function call:
                        # its arguments may be truncated at the token limit.
                        if event_type == "response.completed":
                            output = response_object.get("output", [])
                            if isinstance(output, list):
                                for value in output:
                                    if (
                                        not isinstance(value, dict)
                                        or value.get("type") != "function_call"
                                    ):
                                        continue
                                    item_id = (
                                        _optional_str(value.get("id"))
                                        or f"tool-{next_tool_index}"
                                    )
                                    index = tool_indices.get(item_id)
                                    if index is None:
                                        index = next_tool_index
                                        next_tool_index += 1
                                        tool_indices[item_id] = index
                                    finalized_tool_items[item_id] = value
                            for item_id, value in finalized_tool_items.items():
                                yield _final_tool_call_event(value, tool_indices[item_id])
                        usage = _usage_from_responses(response_object.get("usage"))
                        if usage is not None:
                            yield StreamEvent(kind=StreamEventKind.USAGE, usage=usage)
                        hit_output_limit = (
                            usage is not None and usage.output_tokens >= request.max_tokens
                        )
                        completed = True
                        yield StreamEvent(
                            kind=StreamEventKind.COMPLETED,
                            finish_reason=(
                                "length"
                                if event_type == "response.incomplete"
                                or (hit_output_limit and not finalized_tool_items)
                                else "tool_calls" if finalized_tool_items else "stop"
                            ),
                            provider_request_id=provider_request_id,
                        )
                    elif event_type == "response.failed":
                        response_object = event.get("response", {})
                        error = (
                            response_object.get("error")
                            if isinstance(response_object, dict)
                            else None
                        )
                        message = (
                            str(error.get("message", "llama.cpp response failed"))
                            if isinstance(error, dict)
                            else "llama.cpp response failed"
                        )
                        raise ProviderError("provider_response_failed", message)
                    elif event_type == "error":
                        raise ProviderError(
                            str(event.get("code", "provider_response_failed")),
                            str(event.get("message", "llama.cpp stream failed")),
                        )
                if not started:
                    raise ProviderError("empty_provider_response", "llama.cpp emitted no events")
                if not completed:
                    raise ProviderError(
                        "incomplete_provider_response",
                        "llama.cpp stream ended before completion",
                    )
        except ProviderError:
            raise
        except httpx.TimeoutException as exc:
            raise ProviderError("provider_timeout", str(exc), retryable=True) from exc
        except httpx.HTTPStatusError as exc:
            raise _http_error(exc) from exc
        except httpx.HTTPError as exc:
            raise ProviderError("provider_transport_error", str(exc), retryable=True) from exc


def _usage_from_responses(value: JsonValue) -> Usage | None:
    if not isinstance(value, dict):
        return None
    details = value.get("input_tokens_details", {})
    cached = details.get("cached_tokens") if isinstance(details, dict) else None
    completion_details = value.get("output_tokens_details", {})
    reasoning = (
        completion_details.get("reasoning_tokens") if isinstance(completion_details, dict) else None
    )
    return Usage(
        input_tokens=_int_default(value.get("input_tokens")),
        output_tokens=_int_default(value.get("output_tokens")),
        cached_input_tokens=_optional_int(cached),
        reasoning_tokens=_optional_int(reasoning),
        provider_reported_cost=_optional_float(value.get("cost")),
    )


def _response_input(messages: Sequence[object]) -> list[dict[str, object]]:
    from hames.providers.base import ProviderMessage

    result: list[dict[str, object]] = []
    for message in messages:
        value = ProviderMessage.model_validate(message)
        if value.role == "tool":
            if value.tool_call_id:
                result.append(
                    {
                        "type": "function_call_output",
                        "call_id": value.tool_call_id,
                        "output": value.content,
                    }
                )
            continue
        if value.content:
            # llama.cpp cannot infer an assistant message item's type from the
            # Responses shorthand accepted for user input. Emit the canonical
            # message/content shape so continued turns remain valid.
            result.append(
                {
                    "type": "message",
                    "role": value.role,
                    "content": [
                        {
                            "type": (
                                "output_text" if value.role == "assistant" else "input_text"
                            ),
                            "text": value.content,
                        }
                    ],
                }
            )
        for call in value.tool_calls:
            result.append(
                {
                    "type": "function_call",
                    "call_id": call.id,
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, separators=(",", ":")),
                }
            )
    return result


def _http_error(exc: httpx.HTTPStatusError) -> ProviderError:
    status = exc.response.status_code
    message = str(exc)
    try:
        body = JSON_OBJECT.validate_python(cast(object, exc.response.json()))
        error_value = body.get("error")
        if isinstance(error_value, dict):
            message = str(error_value.get("message", message))
    except ValueError:
        pass
    return ProviderError(
        "provider_http_error",
        message,
        retryable=status == 429 or status >= 500,
    )


def _final_tool_call_event(item: dict[str, JsonValue], index: int) -> StreamEvent:
    arguments = item.get("arguments", "")
    return StreamEvent(
        kind=StreamEventKind.TOOL_CALL_DELTA,
        tool_call=ToolCallDelta(
            index=index,
            provider_call_id=_optional_str(item.get("call_id")) or _optional_str(item.get("id")),
            name=_optional_str(item.get("name")),
            arguments_delta=(
                arguments
                if isinstance(arguments, str)
                else json.dumps(arguments, separators=(",", ":"))
            ),
        ),
    )


def _reasoning_efforts(model_id: str, supported: bool | None, configured: list[str]) -> list[str]:
    if supported is False:
        return []
    if configured:
        return configured
    if "qwen3.8" in model_id.lower():
        return ["low", "medium", "xhigh"]
    return ["on"] if supported is True else []


def _has_reasoning_option(arguments: list[str]) -> bool:
    return any(argument in {"--reasoning", "--reasoning-budget"} for argument in arguments)


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
