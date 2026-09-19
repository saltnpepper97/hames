"""Streaming Chat Completions transport for DeepSeek and Z.ai."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

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
from hames.providers.openai import (
    OpenAIProvider,
    _context_length_from_raw,  # pyright: ignore[reportPrivateUsage]
    _http_error,  # pyright: ignore[reportPrivateUsage]
    _raise_for_status,  # pyright: ignore[reportPrivateUsage]
)


class ChatCompletionsProvider(OpenAIProvider):
    """Reuse authenticated discovery, while keeping this wire protocol separate."""

    api_key_file: str = ""
    catalog: tuple[str, ...] = ()

    def _headers(self) -> dict[str, str]:
        if self._environ.get(self.api_key_env, "").strip():
            return super()._headers()
        if self.api_key_file:
            try:
                key = Path(self.api_key_file).expanduser().read_text().strip()
            except FileNotFoundError:
                key = ""
            except (OSError, UnicodeError) as exc:
                raise ProviderError(
                    "provider_not_configured", f"Could not read the {self.brand} credential file"
                ) from exc
            if key:
                return {"Authorization": f"Bearer {key}"}
        raise ProviderError(
            "provider_not_configured",
            f"{self.brand} is not connected; run hames setup {self.adapter.replace('_', '-')} "
            f"or set {self.api_key_env}",
        )

    async def list_models(self) -> list[ProviderModel]:
        try:
            response = await self.client.get(f"{self.base_url}/models", headers=self._headers())
            fallback = response.status_code in {404, 405} and bool(self.catalog)
            if fallback:
                identifiers: dict[str, dict[str, JsonValue]] = {name: {} for name in self.catalog}
                if self.default_model:
                    identifiers.setdefault(self.default_model, {})
            else:
                response.raise_for_status()
                body = JSON_OBJECT.validate_json(response.content)
                raw_models = body.get("data")
                if not isinstance(raw_models, list):
                    raise ProviderError(
                        "malformed_provider_response", "models data is not an array"
                    )
                identifiers = {}
                for raw in raw_models:
                    if isinstance(raw, dict) and isinstance(raw.get("id"), str):
                        identifier = raw["id"]
                        if isinstance(identifier, str):
                            identifiers[identifier] = raw
        except ProviderError:
            raise
        except httpx.HTTPStatusError as exc:
            raise _http_error(exc) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError("provider_unavailable", str(exc), retryable=True) from exc
        return [
            ProviderModel(
                id=name,
                provider=self.profile_id,
                status="configured" if fallback else "available",
                context_length=_context_length_from_raw(raw) or self._default_context_length(name),
                input_modalities=["text", "image"]
                if self._supports_image_input(name)
                else ["text"],
                output_modalities=["text"],
                reasoning_supported=self._reasoning_supported(name),
                reasoning_efforts=self._model_efforts(name),
            )
            for name, raw in sorted(identifiers.items())
            if self._is_text_model(name)
        ]

    def _model_efforts(self, model_id: str) -> list[str]:
        return self.supported_reasoning_efforts if self._reasoning_supported(model_id) else []

    def request_body(self, request: ModelRequest) -> dict[str, object]:
        messages: list[dict[str, object]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.extend(_message(message) for message in request.messages)
        body: dict[str, object] = {
            "model": request.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_tokens": request.max_tokens,
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
        if request.temperature is not None:
            body["temperature"] = request.temperature
        return body

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        try:
            async with self.client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=self.request_body(request),
            ) as response:
                await _raise_for_status(response)
                started = False
                finish_reason: str | None = None
                request_id: str | None = None
                last_usage: Usage | None = None
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data:
                        continue
                    if data == "[DONE]":
                        break
                    try:
                        chunk = JSON_OBJECT.validate_json(data)
                    except ValueError as exc:
                        raise ProviderError(
                            "malformed_provider_event", f"{self.brand} emitted invalid SSE JSON"
                        ) from exc
                    if chunk.get("error"):
                        raise ProviderError(
                            "provider_response_failed", f"{self.brand} stream failed"
                        )
                    if not started:
                        request_id = str(chunk["id"]) if chunk.get("id") else None
                        started = True
                        yield StreamEvent(
                            kind=StreamEventKind.STARTED, provider_request_id=request_id
                        )
                    usage = chunk.get("usage")
                    if isinstance(usage, dict):
                        last_usage = _usage(usage)
                    choices = chunk.get("choices", [])
                    if not isinstance(choices, list):
                        raise ProviderError("malformed_provider_event", "choices must be an array")
                    for choice in choices:
                        if not isinstance(choice, dict) or choice.get("index", 0) != 0:
                            continue
                        delta = choice.get("delta") or {}
                        if not isinstance(delta, dict):
                            raise ProviderError(
                                "malformed_provider_event", "delta must be an object"
                            )
                        if finish_reason is not None and delta:
                            raise ProviderError(
                                "provider_protocol_error", "output after finish_reason"
                            )
                        for key, kind in (
                            ("reasoning_content", StreamEventKind.REASONING_DELTA),
                            ("content", StreamEventKind.TEXT_DELTA),
                        ):
                            value = delta.get(key)
                            if isinstance(value, str) and value:
                                yield StreamEvent(kind=kind, text=value)
                        calls = delta.get("tool_calls", [])
                        if not isinstance(calls, list):
                            raise ProviderError(
                                "malformed_provider_event", "tool_calls must be an array"
                            )
                        for call in calls:
                            if not isinstance(call, dict):
                                raise ProviderError("malformed_provider_event", "invalid tool call")
                            function = call.get("function") or {}
                            if not isinstance(function, dict):
                                raise ProviderError(
                                    "malformed_provider_event", "invalid function call"
                                )
                            index = call.get("index")
                            if not isinstance(index, int) or isinstance(index, bool) or index < 0:
                                raise ProviderError(
                                    "malformed_provider_event", "invalid tool call index"
                                )
                            yield StreamEvent(
                                kind=StreamEventKind.TOOL_CALL_DELTA,
                                tool_call=ToolCallDelta(
                                    index=index,
                                    provider_call_id=_string(call.get("id")),
                                    name=_string(function.get("name")),
                                    arguments_delta=_string(function.get("arguments")) or "",
                                ),
                            )
                        reason = choice.get("finish_reason")
                        if isinstance(reason, str) and reason:
                            finish_reason = reason
                if not started or finish_reason is None:
                    raise ProviderError(
                        "provider_protocol_error", f"{self.brand} stream ended before completion"
                    )
                if last_usage is not None:
                    yield StreamEvent(kind=StreamEventKind.USAGE, usage=last_usage)
                # Usage may arrive in a final chunk after finish_reason. Complete last.
                yield StreamEvent(
                    kind=StreamEventKind.COMPLETED,
                    finish_reason=finish_reason,
                    provider_request_id=request_id,
                )
        except ProviderError:
            raise
        except httpx.HTTPStatusError as exc:
            raise _http_error(exc) from exc
        except httpx.TimeoutException as exc:
            raise ProviderError("provider_timeout", str(exc), retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError("provider_transport_error", str(exc), retryable=True) from exc


def _message(message: ProviderMessage) -> dict[str, object]:
    result: dict[str, object] = {"role": message.role, "content": message.content}
    if message.attachments:
        result["content"] = [
            {"type": "text", "text": message.content},
            *[
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{item.media_type};base64,{item.data_base64}"},
                }
                for item in message.attachments
            ],
        ]
    if message.role == "assistant":
        result["reasoning_content"] = message.reasoning_content
    if message.tool_calls:
        result["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, separators=(",", ":")),
                },
            }
            for call in message.tool_calls
        ]
    if message.tool_call_id:
        result["tool_call_id"] = message.tool_call_id
    return result


def _string(value: JsonValue) -> str | None:
    return value if isinstance(value, str) and value else None


def _usage(raw: dict[str, JsonValue]) -> Usage:
    def number(value: JsonValue) -> int:
        return max(0, int(value)) if isinstance(value, int | float) else 0

    prompt_details = raw.get("prompt_tokens_details")
    output_details = raw.get("completion_tokens_details")
    cached = raw.get("prompt_cache_hit_tokens")
    if cached is None and isinstance(prompt_details, dict):
        cached = prompt_details.get("cached_tokens")
    reasoning = output_details.get("reasoning_tokens") if isinstance(output_details, dict) else None
    return Usage(
        input_tokens=number(raw.get("prompt_tokens")),
        output_tokens=number(raw.get("completion_tokens")),
        cached_input_tokens=number(cached) if cached is not None else None,
        reasoning_tokens=number(reasoning) if reasoning is not None else None,
    )
