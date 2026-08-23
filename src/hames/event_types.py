"""Typed durable event payload registry."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class EventPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyPayload(EventPayload):
    pass


class SessionOpenedPayload(EventPayload):
    working_directory: str
    provider: str
    model: str
    reasoning_effort: str


class SessionClosedPayload(EventPayload):
    status: str


class SessionForkedPayload(EventPayload):
    parent_session_id: str
    fork_event_id: str
    fork_sequence: int


class SessionSettingsPayload(EventPayload):
    provider: str
    model: str
    reasoning_effort: str


class MessagePayload(EventPayload):
    content: str


class AssistantOutputPayload(EventPayload):
    content: str
    status: str


class ContextCompiledPayload(EventPayload):
    core_contract_hash: str
    agent_capsule_hash: str
    history_event_ids: list[str]
    working_directory: str
    source_order: list[str]


class ModelRequestedPayload(EventPayload):
    provider: str
    model: str
    reasoning_effort: str
    agent_capsule_hash: str


class ModelStartedPayload(EventPayload):
    provider_request_id: str | None


class ModelUsagePayload(EventPayload):
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None


class ModelCompletedPayload(EventPayload):
    finish_reason: str


class FailurePayload(EventPayload):
    code: str
    message: str
    retryable: bool


class RuntimeNoticePayload(EventPayload):
    code: str = "notice"
    message: str


EVENT_PAYLOADS: dict[str, type[EventPayload]] = {
    "session.opened": SessionOpenedPayload,
    "session.closed": SessionClosedPayload,
    "session.forked": SessionForkedPayload,
    "session.settings.changed": SessionSettingsPayload,
    "user.message": MessagePayload,
    "assistant.message": AssistantOutputPayload,
    "assistant.reasoning": AssistantOutputPayload,
    "context.compiled": ContextCompiledPayload,
    "model.requested": ModelRequestedPayload,
    "model.response.started": ModelStartedPayload,
    "model.usage": ModelUsagePayload,
    "model.response.completed": ModelCompletedPayload,
    "model.response.failed": FailurePayload,
    "run.cancelled": EmptyPayload,
    "runtime.error": FailurePayload,
    "runtime.notice": RuntimeNoticePayload,
}


class UnknownEventType(ValueError):
    pass


def validate_payload(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    model = EVENT_PAYLOADS.get(event_type)
    if model is None:
        raise UnknownEventType(f"unknown event type: {event_type}")
    return model.model_validate(payload).model_dump(mode="json")
