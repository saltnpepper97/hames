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


class SessionAgentChangedPayload(EventPayload):
    agent_id: str


class MessagePayload(EventPayload):
    content: str


class AssistantOutputPayload(EventPayload):
    content: str
    status: str


def _empty_anchor_dicts() -> list[dict[str, str]]:
    return []


class ContextSourcePayload(EventPayload):
    source_id: str
    source_type: str
    content_hash: str
    priority: int
    estimated_tokens: int
    selected_tokens: int = 0
    visibility: str
    truncation: str
    reason: str
    event_ids: list[str] = Field(default_factory=list)
    origin: str = ""
    source_path: str = ""
    memory_id: str = ""
    memory_layer: str = ""
    memory_visibility: str = ""
    memory_anchors: list[dict[str, str]] = Field(default_factory=_empty_anchor_dicts)
    retrieval_score: float = 0.0
    provenance_event_ids: list[str] = Field(default_factory=list)


class ContextCompiledPayload(EventPayload):
    compiler_version: int
    estimator_version: str
    provider: str
    model: str
    reasoning_effort: str
    context_window_tokens: int
    context_window_source: str
    input_budget_tokens: int
    output_reserve_tokens: int
    estimated_input_tokens: int
    selected_sources: list[ContextSourcePayload]
    omitted_sources: list[ContextSourcePayload]
    source_order: list[str]
    contributing_event_ids: list[str]
    request_hash: str
    request_snapshot_blob_hash: str
    agent_id: str
    agent_capsule_hash: str
    agent_capsule_path: str
    agent_origin: str = "global"


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


class DelegationEvidencePayload(EventPayload):
    event_id: str
    event_type: str
    payload_hash: str
    content: str


def _empty_delegation_evidence() -> list[DelegationEvidencePayload]:
    return []


class DelegationRequestedPayload(EventPayload):
    tool_call_id: str
    target_agent_id: str
    task: str
    evidence: list[DelegationEvidencePayload] = Field(default_factory=_empty_delegation_evidence)
    delegation_depth: int


class DelegationTaskCardPayload(EventPayload):
    parent_session_id: str
    parent_run_id: str
    parent_event_id: str
    target_agent_id: str
    task: str
    evidence: list[DelegationEvidencePayload] = Field(default_factory=_empty_delegation_evidence)
    delegation_depth: int


class DelegationTerminalPayload(EventPayload):
    child_session_id: str
    child_run_id: str
    target_agent_id: str
    status: str
    summary: str
    duration_seconds: float = 0.0


class MemoryAnchorPayload(EventPayload):
    kind: str
    value: str


def _empty_memory_anchors() -> list[MemoryAnchorPayload]:
    return []


class MemoryRecordPayload(EventPayload):
    memory_id: str
    layer: str
    status: str
    visibility: str
    summary: str
    confidence: float
    importance: float
    anchors: list[MemoryAnchorPayload] = Field(default_factory=_empty_memory_anchors)
    provenance_event_ids: list[str] = Field(default_factory=list)
    supersedes_id: str | None = None


class MemoryTransitionPayload(EventPayload):
    memory_id: str
    previous_status: str
    status: str
    reason: str
    replacement_id: str | None = None


class MemoryJobPayload(EventPayload):
    job_id: str
    kind: str
    status: str
    attempts: int
    error_code: str | None = None
    error_message: str | None = None


class MemoryRetrievedItemPayload(EventPayload):
    memory_id: str
    layer: str
    score: float
    estimated_tokens: int
    provenance_event_ids: list[str] = Field(default_factory=list)


def _empty_retrieved_memories() -> list[MemoryRetrievedItemPayload]:
    return []


class MemoryRetrievedPayload(EventPayload):
    query_hash: str
    selected: list[MemoryRetrievedItemPayload] = Field(default_factory=_empty_retrieved_memories)
    omitted: list[MemoryRetrievedItemPayload] = Field(default_factory=_empty_retrieved_memories)
    eligible_count: int


class MemoryEpisodePayload(EventPayload):
    memory_id: str
    source_run_id: str
    reason: str


EVENT_PAYLOADS: dict[str, type[EventPayload]] = {
    "session.opened": SessionOpenedPayload,
    "session.closed": SessionClosedPayload,
    "session.forked": SessionForkedPayload,
    "session.settings.changed": SessionSettingsPayload,
    "session.agent.changed": SessionAgentChangedPayload,
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
    "delegation.requested": DelegationRequestedPayload,
    "delegation.task_card": DelegationTaskCardPayload,
    "delegation.completed": DelegationTerminalPayload,
    "delegation.failed": DelegationTerminalPayload,
    "memory.proposed": MemoryRecordPayload,
    "memory.accepted": MemoryTransitionPayload,
    "memory.rejected": MemoryTransitionPayload,
    "memory.superseded": MemoryTransitionPayload,
    "memory.retracted": MemoryTransitionPayload,
    "memory.promoted": MemoryRecordPayload,
    "memory.job.queued": MemoryJobPayload,
    "memory.job.started": MemoryJobPayload,
    "memory.job.completed": MemoryJobPayload,
    "memory.job.failed": MemoryJobPayload,
    "memory.retrieved": MemoryRetrievedPayload,
    "memory.episode.projected": MemoryEpisodePayload,
}


class UnknownEventType(ValueError):
    pass


def validate_payload(event_type: str, payload: dict[str, Any]) -> dict[str, JsonValue]:
    model = EVENT_PAYLOADS.get(event_type)
    if model is None:
        raise UnknownEventType(f"unknown event type: {event_type}")
    return JSON_OBJECT.validate_python(model.model_validate(payload).model_dump(mode="json"))
