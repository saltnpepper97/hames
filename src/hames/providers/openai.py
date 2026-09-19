"""OpenAI Responses API provider with Hames-owned stateless context."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import cast

import httpx

from hames.providers.base import (
    JSON_OBJECT,
    JsonValue,
    ModelRequest,
    ProviderError,
    ProviderMessage,
    ProviderModel,
    StreamEvent,
    StreamEventKind,
    ToolCallDelta,
    Usage,
)

_NON_TEXT_MARKERS = (
    "audio",
    "embedding",
    "image",
    "moderation",
    "realtime",
    "search-preview",
    "sora",
    "transcribe",
    "tts",
)
_REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")


class OpenAIProvider:
    adapter = "openai"
    brand = "OpenAI"

    def __init__(
        self,
        base_url: str = "https://api.openai.com/v1",
        *,
        profile_id: str = "openai",
        api_key_env: str = "OPENAI_API_KEY",
        api_key_file: str = "",
        timeout_seconds: float = 120.0,
        default_model: str = "",
        supported_reasoning_efforts: list[str] | None = None,
        environ: Mapping[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.profile_id = profile_id
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.api_key_file = api_key_file
        self.default_model = default_model
        self.supported_reasoning_efforts = supported_reasoning_efforts or []
        self._environ = os.environ if environ is None else environ
        self._owned_client = client is None
        self.client = client or httpx.AsyncClient(timeout=timeout_seconds)

    async def aclose(self) -> None:
        if self._owned_client:
            await self.client.aclose()

    def _is_text_model(self, model_id: str) -> bool:
        lowered = model_id.lower()
        return lowered.startswith(("gpt-", "o1", "o3", "o4", "chatgpt-")) and not any(
            marker in lowered for marker in _NON_TEXT_MARKERS
        )

    def _supports_image_input(self, model_id: str) -> bool:
        value = model_id.lower()
        return value.startswith(("gpt-4o", "gpt-4.1", "gpt-5", "chatgpt-", "o1", "o3", "o4"))

    def _reasoning_supported(self, model_id: str) -> bool:
        return model_id.lower().startswith(_REASONING_PREFIXES)

    def _default_context_length(self, model_id: str) -> int | None:
        return None

    def _headers(self) -> dict[str, str]:
        api_key = self._environ.get(self.api_key_env, "").strip()
        if not api_key and self.api_key_file:
            try:
                api_key = Path(self.api_key_file).expanduser().read_text().strip()
            except FileNotFoundError:
                pass
            except (OSError, UnicodeError) as exc:
                raise ProviderError(
                    "provider_not_configured", f"Could not read the {self.brand} credential file"
                ) from exc
        if not api_key:
            raise ProviderError(
                "provider_not_configured",
                f"{self.api_key_env} is not set for {self.brand}",
                details={"environment_variable": self.api_key_env},
            )
        return {"Authorization": f"Bearer {api_key}"}

    async def list_models(self) -> list[ProviderModel]:
        try:
            response = await self.client.get(f"{self.base_url}/models", headers=self._headers())
            response.raise_for_status()
            body = JSON_OBJECT.validate_python(cast(object, response.json()))
        except ProviderError:
            raise
        except httpx.HTTPStatusError as exc:
            raise _http_error(exc) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError("provider_unavailable", str(exc), retryable=True) from exc

        raw_models = body.get("data", [])
        if not isinstance(raw_models, list):
            raise ProviderError(
                "malformed_provider_response", f"{self.brand} models data is not a list"
            )
        identifiers: dict[str, dict[str, JsonValue]] = {}
        for raw_value in raw_models:
            if not isinstance(raw_value, dict):
                continue
            raw = cast(dict[str, JsonValue], raw_value)
            identifier = raw.get("id")
            if isinstance(identifier, str) and identifier:
                identifiers[identifier] = raw
        if self.default_model:
            identifiers.setdefault(self.default_model, {})
        models: list[ProviderModel] = []
        for model_id in sorted(
            identifier for identifier in identifiers if self._is_text_model(identifier)
        ):
            reasoning = self._reasoning_supported(model_id)
            efforts = self.supported_reasoning_efforts if reasoning else []
            models.append(
                ProviderModel(
                    id=model_id,
                    provider=self.profile_id,
                    status="available",
                    input_modalities=(
                        ["text", "image"] if self._supports_image_input(model_id) else ["text"]
                    ),
                    output_modalities=["text"],
                    reasoning_supported=reasoning,
                    reasoning_efforts=efforts,
                    context_length=_context_length_from_raw(identifiers[model_id])
                    or self._default_context_length(model_id),
                )
            )
        return models

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        body: dict[str, object] = {
            "model": request.model,
            "input": _response_input(request.messages),
            "instructions": request.system,
            "stream": True,
            "store": False,
            "include": ["reasoning.encrypted_content"],
            "max_output_tokens": request.max_tokens,
            "parallel_tool_calls": True,
        }
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.reasoning_effort and request.reasoning_effort not in {"off", "on"}:
            body["reasoning"] = {
                "effort": request.reasoning_effort,
                "summary": "auto",
            }
        elif request.reasoning_effort == "on":
            body["reasoning"] = {"summary": "auto"}
        if request.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                    "strict": False,
                }
                for tool in request.tools
            ]

        started = False
        completed = False
        response_id: str | None = None
        tool_calls: dict[int, tuple[str | None, str | None]] = {}
        provider_items: list[dict[str, JsonValue]] = []
        try:
            async with self.client.stream(
                "POST",
                f"{self.base_url}/responses",
                headers={**self._headers(), "Accept": "text/event-stream"},
                json=body,
            ) as response:
                await _raise_for_status(response)
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
                            "malformed_provider_event",
                            f"{self.brand} emitted invalid SSE JSON",
                        ) from exc
                    event_type = str(event.get("type", ""))
                    if event_type == "response.created":
                        if started:
                            raise ProviderError(
                                "provider_protocol_error",
                                f"{self.brand} started a response twice",
                            )
                        response_object = event.get("response", {})
                        if isinstance(response_object, dict):
                            response_id = _optional_str(response_object.get("id"))
                        started = True
                        yield StreamEvent(
                            kind=StreamEventKind.STARTED,
                            provider_request_id=response_id,
                        )
                    elif event_type == "response.output_item.added":
                        item = event.get("item", {})
                        if not isinstance(item, dict) or item.get("type") != "function_call":
                            continue
                        index = _int_default(event.get("output_index"))
                        call_id = _optional_str(item.get("call_id")) or _optional_str(
                            item.get("id")
                        )
                        name = _optional_str(item.get("name"))
                        tool_calls[index] = (call_id, name)
                        yield StreamEvent(
                            kind=StreamEventKind.TOOL_CALL_DELTA,
                            tool_call=ToolCallDelta(
                                index=index,
                                provider_call_id=call_id,
                                name=name,
                                arguments_delta=str(item.get("arguments", "")),
                            ),
                        )
                    elif event_type == "response.function_call_arguments.delta":
                        index = _int_default(event.get("output_index"))
                        call_id, name = tool_calls.get(index, (None, None))
                        yield StreamEvent(
                            kind=StreamEventKind.TOOL_CALL_DELTA,
                            tool_call=ToolCallDelta(
                                index=index,
                                provider_call_id=call_id,
                                name=None if name is not None else _optional_str(event.get("name")),
                                arguments_delta=str(event.get("delta", "")),
                            ),
                        )
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
                    elif event_type in {"response.completed", "response.incomplete"}:
                        response_object = event.get("response", {})
                        if not isinstance(response_object, dict):
                            raise ProviderError(
                                "malformed_provider_event",
                                f"{self.brand} completion omitted response",
                            )
                        usage = _usage(response_object.get("usage"))
                        if usage is not None:
                            yield StreamEvent(kind=StreamEventKind.USAGE, usage=usage)
                        output = response_object.get("output", [])
                        if isinstance(output, list):
                            provider_items = [
                                cast(dict[str, JsonValue], item)
                                for item in output
                                if isinstance(item, dict) and item.get("type") == "reasoning"
                            ]
                        completed = True
                        finish_reason = "tool_calls" if tool_calls else "stop"
                        if event_type == "response.incomplete":
                            finish_reason = "length"
                        yield StreamEvent(
                            kind=StreamEventKind.COMPLETED,
                            finish_reason=finish_reason,
                            provider_request_id=response_id,
                            provider_items=provider_items,
                        )
                    elif event_type == "response.failed":
                        response_object = event.get("response", {})
                        error_value = (
                            response_object.get("error")
                            if isinstance(response_object, dict)
                            else None
                        )
                        error = (
                            cast(dict[str, JsonValue], error_value)
                            if isinstance(error_value, dict)
                            else {}
                        )
                        message = str(error.get("message", f"{self.brand} response failed"))
                        raise ProviderError("provider_response_failed", message)
                    elif event_type == "error":
                        raise ProviderError(
                            str(event.get("code", "provider_response_failed")),
                            str(event.get("message", f"{self.brand} stream failed")),
                        )
            if not started:
                raise ProviderError("empty_provider_response", f"{self.brand} emitted no response")
            if not completed:
                raise ProviderError(
                    "provider_protocol_error",
                    f"{self.brand} stream ended before completion",
                )
        except ProviderError:
            raise
        except httpx.TimeoutException as exc:
            raise ProviderError("provider_timeout", str(exc), retryable=True) from exc
        except httpx.HTTPStatusError as exc:
            raise _http_error(exc) from exc
        except httpx.HTTPError as exc:
            raise ProviderError("provider_transport_error", str(exc), retryable=True) from exc


def _response_input(messages: list[ProviderMessage]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for message in messages:
        result.extend(cast(list[dict[str, object]], message.provider_items))
        if message.role == "tool":
            if message.tool_call_id:
                result.append(
                    {
                        "type": "function_call_output",
                        "call_id": message.tool_call_id,
                        "output": message.content,
                    }
                )
            continue
        if message.content or message.attachments:
            if message.role == "user" and message.attachments:
                content: list[dict[str, object]] = []
                if message.content:
                    content.append({"type": "input_text", "text": message.content})
                content.extend(
                    {
                        "type": "input_image",
                        "image_url": (
                            f"data:{attachment.media_type};base64,{attachment.data_base64}"
                        ),
                    }
                    for attachment in message.attachments
                )
                result.append({"role": message.role, "content": content})
            else:
                result.append({"role": message.role, "content": message.content})
        for call in message.tool_calls:
            result.append(
                {
                    "type": "function_call",
                    "call_id": call.id,
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, separators=(",", ":")),
                }
            )
    return result


def _usage(value: JsonValue) -> Usage | None:
    if not isinstance(value, dict):
        return None
    input_details = value.get("input_tokens_details", {})
    output_details = value.get("output_tokens_details", {})
    return Usage(
        input_tokens=_int_default(value.get("input_tokens")),
        output_tokens=_int_default(value.get("output_tokens")),
        cached_input_tokens=(
            _optional_int(input_details.get("cached_tokens"))
            if isinstance(input_details, dict)
            else None
        ),
        reasoning_tokens=(
            _optional_int(output_details.get("reasoning_tokens"))
            if isinstance(output_details, dict)
            else None
        ),
    )


async def _raise_for_status(response: httpx.Response) -> None:
    if response.is_error:
        await response.aread()
    response.raise_for_status()


def _http_error(exc: httpx.HTTPStatusError) -> ProviderError:
    status = exc.response.status_code
    code = "provider_authentication_failed" if status in {401, 403} else "provider_http_error"
    message = str(exc)
    try:
        body = JSON_OBJECT.validate_python(cast(object, exc.response.json()))
        error_value = body.get("error")
        if isinstance(error_value, dict):
            error = cast(dict[str, JsonValue], error_value)
            message = str(error.get("message", message))
    except (ValueError, httpx.ResponseNotRead, httpx.StreamClosed, httpx.StreamConsumed):
        pass
    return ProviderError(code, message, retryable=status == 429 or status >= 500)


def _optional_str(value: JsonValue) -> str | None:
    return value if isinstance(value, str) and value else None


def _int_default(value: JsonValue) -> int:
    return int(value) if isinstance(value, int | float) else 0


def _optional_int(value: JsonValue) -> int | None:
    return int(value) if isinstance(value, int | float) else None


def _context_length_from_raw(raw: Mapping[str, JsonValue]) -> int | None:
    for key in ("context_window", "context_length", "max_context_length", "max_prompt_tokens"):
        value = raw.get(key)
        if isinstance(value, int | float) and int(value) >= 8_192:
            return int(value)
    return None
