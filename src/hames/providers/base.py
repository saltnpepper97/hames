"""Provider-independent model and streaming boundaries."""

from __future__ import annotations

from collections.abc import AsyncIterator
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None
JSON_OBJECT = TypeAdapter(dict[str, JsonValue])


class ProviderBoundary(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProviderMessage(ProviderBoundary):
    role: str
    content: str
    reasoning_content: str = ""


class ModelRequest(ProviderBoundary):
    model: str
    messages: list[ProviderMessage]
    system: str
    reasoning_effort: str = ""
    max_tokens: int = Field(default=4096, gt=0)


class ProviderModel(ProviderBoundary):
    id: str
    provider: str
    status: str = "unknown"
    context_length: int | None = None
    parameter_size: str | None = None
    quantization: str | None = None
    input_modalities: list[str] = []
    output_modalities: list[str] = []
    reasoning_supported: bool | None = None
    reasoning_efforts: list[str] = []


class Usage(ProviderBoundary):
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None


class StreamEventKind(StrEnum):
    STARTED = "response.started"
    REASONING_DELTA = "response.reasoning_delta"
    TEXT_DELTA = "response.text_delta"
    USAGE = "response.usage"
    COMPLETED = "response.completed"
    FAILED = "response.failed"


class StreamEvent(ProviderBoundary):
    kind: StreamEventKind
    text: str = ""
    usage: Usage | None = None
    finish_reason: str | None = None
    provider_request_id: str | None = None
    error_code: str | None = None


class ProviderError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class Provider(Protocol):
    profile_id: str
    adapter: str
    base_url: str

    async def list_models(self) -> list[ProviderModel]: ...

    def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]: ...

    async def aclose(self) -> None: ...
