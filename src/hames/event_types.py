"""Typed durable event payload registry."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from hames.providers.base import JSON_OBJECT, JsonValue


class EventPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyPayload(EventPayload):
    pass


class SessionOpenedPayload(EventPayload):
    working_directory: str
    provider: str
    model: str
    reasoning_effort: str
    context_window_tokens: int = 32_768
    context_window_source: str = "fallback"


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
    context_window_tokens: int = 32_768
    context_window_source: str = "fallback"


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
    tool_schema_hash: str = ""
    policy_summary_hash: str = ""


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
    provider_reported_cost: float | None = None


class ModelToolCallPayload(EventPayload):
    index: int
    tool_call_id: str
    provider_call_id: str | None
    name: str
    arguments: dict[str, JsonValue]
    status: str


class RunStartedPayload(EventPayload):
    max_model_turns: int
    max_tool_calls: int
    max_active_seconds: float


class RunCompletedPayload(EventPayload):
    model_turns: int
    tool_calls: int
    active_seconds: float


class ToolRequestedPayload(EventPayload):
    tool_call_id: str
    provider_call_id: str | None
    name: str
    arguments: dict[str, JsonValue]


class ToolStartedPayload(EventPayload):
    tool_call_id: str
    name: str


class ToolResultPayload(EventPayload):
    tool_call_id: str
    name: str
    status: str
    summary: str
    content: str
    structured_data: dict[str, JsonValue] = Field(default_factory=dict)
    truncated: bool = False
    blob_references: list[str] = Field(default_factory=list)
    duration_seconds: float = 0.0


class PolicyRequestedPayload(EventPayload):
    tool_call_id: str
    name: str
    request_hash: str


class PolicyDecidedPayload(EventPayload):
    tool_call_id: str
    decision: str
    reason: str
    risk: str


class ApprovalRequestedPayload(EventPayload):
    approval_id: str
    tool_call_id: str
    name: str
    arguments: dict[str, JsonValue]
    request_hash: str
    working_directory: str
    reason: str


class ApprovalResolvedPayload(EventPayload):
    approval_id: str
    request_hash: str
    decision: str


class TrustPayload(EventPayload):
    path: str


class ModelCompletedPayload(EventPayload):
    finish_reason: str


class FailurePayload(EventPayload):
    code: str
    message: str
    retryable: bool
    details: dict[str, JsonValue] = Field(default_factory=dict)


class RuntimeNoticePayload(EventPayload):
    code: str = "notice"
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


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
    "model.tool_call": ModelToolCallPayload,
    "model.response.completed": ModelCompletedPayload,
    "model.response.failed": FailurePayload,
    "run.started": RunStartedPayload,
    "run.completed": RunCompletedPayload,
    "run.failed": FailurePayload,
    "run.cancelled": EmptyPayload,
    "tool.requested": ToolRequestedPayload,
    "tool.started": ToolStartedPayload,
    "tool.completed": ToolResultPayload,
    "tool.failed": ToolResultPayload,
    "tool.rejected": ToolResultPayload,
    "policy.requested": PolicyRequestedPayload,
    "policy.decided": PolicyDecidedPayload,
    "approval.requested": ApprovalRequestedPayload,
    "approval.resolved": ApprovalResolvedPayload,
    "trust.granted": TrustPayload,
    "trust.revoked": TrustPayload,
    "runtime.error": FailurePayload,
    "runtime.notice": RuntimeNoticePayload,
}


class UnknownEventType(ValueError):
    pass


def validate_payload(event_type: str, payload: dict[str, Any]) -> dict[str, JsonValue]:
    model = EVENT_PAYLOADS.get(event_type)
    if model is None:
        raise UnknownEventType(f"unknown event type: {event_type}")
    return JSON_OBJECT.validate_python(model.model_validate(payload).model_dump(mode="json"))
