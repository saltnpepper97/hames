"""Typed durable event payload registry."""

from __future__ import annotations

from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator

from hames.environment import RuntimeEnvironmentSnapshot
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


class SessionModeChangedPayload(EventPayload):
    mode: str


class SessionTitleChangedPayload(EventPayload):
    title: str = Field(min_length=1, max_length=80)


class PasteSpanPayload(EventPayload):
    start_byte: int = Field(ge=0)
    end_byte: int = Field(gt=0)
    line_count: int = Field(ge=1)
    byte_count: int = Field(ge=1)


def _empty_paste_spans() -> list[PasteSpanPayload]:
    return []


class MessagePayload(EventPayload):
    content: str
    remember: bool = False
    purpose: Literal["turn", "plan_note", "plan_execution", "heal"] = "turn"
    paste_spans: list[PasteSpanPayload] = Field(default_factory=_empty_paste_spans, max_length=64)
    submission_id: str | None = None


class ModelProviderStatePayload(EventPayload):
    provider: str
    items: list[dict[str, JsonValue]] = Field(default_factory=lambda: list[dict[str, JsonValue]]())


class QueuedMessagePayload(MessagePayload):
    queue_id: str
    position: int = Field(ge=1, le=2)


class QueueRemovedPayload(EventPayload):
    queue_id: str
    reason: str


class QueuePrioritizedPayload(EventPayload):
    queue_id: str
    position: Literal[1] = 1
    reason: str


class QueueStatePayload(EventPayload):
    paused: bool


class PlanProposedPayload(EventPayload):
    plan_id: str
    revision: int = Field(ge=1)
    title: str
    markdown: str
    tasks: list[str] = Field(default_factory=list)
    source_run_id: str
    supersedes_plan_id: str | None = None


class PlanTransitionPayload(EventPayload):
    plan_id: str
    strategy: Literal["keep", "compact"] | None = None
    execution_run_id: str | None = None
    execution_note: str = ""
    message: str = ""


class PlanNotePayload(EventPayload):
    plan_id: str | None = None
    queue_ids: list[str] = Field(default_factory=list)
    contents: list[str] = Field(default_factory=list)


class TaskPayload(EventPayload):
    id: str
    text: str
    status: Literal["pending", "in_progress", "completed", "blocked"]
    position: int = Field(ge=0)
    created_by: str


def _empty_tasks() -> list[TaskPayload]:
    return []


class TasksReplacedPayload(EventPayload):
    title: str
    revision: int = Field(ge=1)
    items: list[TaskPayload] = Field(default_factory=_empty_tasks)


class TaskAddedPayload(EventPayload):
    task: TaskPayload


class TaskUpdatedPayload(EventPayload):
    task_id: str
    text: str | None = None
    status: Literal["pending", "in_progress", "completed", "blocked"] | None = None
    position: int | None = Field(default=None, ge=0)


class TaskRemovedPayload(EventPayload):
    task_id: str


class CorrectionPayload(EventPayload):
    content: str
    target_event_id: str | None = None


class AssistantOutputPayload(EventPayload):
    content: str
    status: str


class AssistantReasoningPayload(AssistantOutputPayload):
    duration_seconds: float = Field(default=0.0, ge=0)


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
    skill_id: str = ""
    skill_version_id: str = ""
    skill_slug: str = ""
    skill_version: int = 0
    skill_scope: str = ""


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
    environment: RuntimeEnvironmentSnapshot | None = None


class ContextCompactionStartedPayload(EventPayload):
    compaction_id: str
    trigger: Literal["automatic", "manual", "plan"]
    preserve_recent_turns: int = 4


class ContextCompactionCompletedPayload(EventPayload):
    compaction_id: str
    trigger: Literal["automatic", "manual", "plan"]
    preserve_recent_turns: int = 4
    summary: str
    cutoff_event_id: str
    cutoff_sequence: int
    source_event_ids: list[str]
    provider: str
    model: str
    reasoning_effort: str
    turns_compacted: int
    before_tokens: int
    after_tokens: int
    passes: int
    partial: bool = False


class ContextCompactionTerminalPayload(EventPayload):
    compaction_id: str
    trigger: Literal["automatic", "manual", "plan"]
    message: str = ""


class GoalEventPayload(EventPayload):
    goal_id: str
    objective: str = ""
    status: str = ""
    step: int = 0
    run_id: str = ""
    summary: str = ""
    evidence: list[str] = Field(default_factory=list)
    reason: str = ""
    signature: str = ""
    repeated_no_progress: int = 0


class ModelRequestedPayload(EventPayload):
    provider: str
    model: str
    reasoning_effort: str
    agent_capsule_hash: str
    purpose: str = "agent_turn"


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


class RunContinuationPayload(EventPayload):
    reason: Literal["output_limit", "unfinished_execution", "malformed_tool_call"]
    attempt: int = Field(ge=1)
    task_revision: int = Field(ge=0)
    unfinished_task_count: int = Field(ge=0)


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
    allow_session: bool = False


class ApprovalResolvedPayload(EventPayload):
    approval_id: str
    request_hash: str
    decision: str
    approval_scope: str = "once"


class QuestionOptionPayload(EventPayload):
    label: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)


def _empty_question_options() -> list[QuestionOptionPayload]:
    return []


class QuestionRequestedPayload(EventPayload):
    question_id: str
    tool_call_id: str
    question: str
    options: list[QuestionOptionPayload] = Field(
        default_factory=_empty_question_options, max_length=3
    )

    @field_validator("options", mode="before")
    @classmethod
    def accept_legacy_string_options(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        options = cast(list[object], value)
        return [
            {"label": option, "description": ""} if isinstance(option, str) else option
            for option in options
        ]


class QuestionAnsweredPayload(EventPayload):
    question_id: str
    answer: str = Field(min_length=1, max_length=4200)
    selected_option: str | None = Field(default=None, max_length=160)
    selected_description: str = Field(default="", max_length=2000)
    note: str = Field(default="", max_length=4000)
    custom: bool = False


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


class TerminalStartedPayload(EventPayload):
    terminal_id: str
    command: str
    workspace: Literal["project", "home"]
    pid: int = Field(gt=0)
    timeout_seconds: float | None = Field(default=None, gt=0)


class TerminalFinishedPayload(EventPayload):
    terminal_id: str
    command: str
    workspace: Literal["project", "home"]
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    truncated: bool = False
    duration_seconds: float = Field(default=0.0, ge=0)
    reason: Literal[
        "exit", "timeout", "user_stop", "agent_stop", "session_closed", "gateway_shutdown"
    ]


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


class DreamPayload(EventPayload):
    dream_id: str
    status: Literal["running", "paused", "completed", "failed"]
    memories_reconciled: int = 0
    skills_reconciled: int = 0
    scars_repaired: int = 0
    message: str = ""


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


class MemoryCapturePayload(EventPayload):
    content: str
    explicit: bool = True


class MemoryPromotionPayload(EventPayload):
    memory_id: str
    visibility: str


class SkillVersionPayload(EventPayload):
    skill_id: str
    version_id: str
    slug: str
    version: int
    content_hash: str
    scope: str
    status: str
    evidence_event_ids: list[str] = Field(default_factory=list)
    reason: str = ""


class SkillTransitionPayload(EventPayload):
    skill_id: str
    version_id: str
    reason: str
    replacement_version_id: str | None = None


class SkillRollbackPayload(EventPayload):
    skill_id: str
    from_version_id: str
    to_version_id: str
    reason: str


class SkillJobPayload(EventPayload):
    job_id: str
    kind: str
    status: str
    attempts: int
    target_skill_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class SkillAuthoringPayload(EventPayload):
    goal: str
    scope: str
    target_skill_id: str | None = None
    evidence_event_ids: list[str] = Field(default_factory=list)


class SkillWorkflowPayload(EventPayload):
    run_id: str
    fingerprint: str
    tool_sequence: list[str]
    outcome: str
    similar_run_ids: list[str] = Field(default_factory=list)


class SkillCatalogueItemPayload(EventPayload):
    skill_id: str
    version_id: str
    slug: str
    version: int
    content_hash: str
    scope: str
    score: float


def _empty_skill_catalogue() -> list[SkillCatalogueItemPayload]:
    return []


class SkillCataloguedPayload(EventPayload):
    query_hash: str
    skills: list[SkillCatalogueItemPayload] = Field(default_factory=_empty_skill_catalogue)


class SkillLoadedPayload(EventPayload):
    skill_id: str
    version_id: str
    slug: str
    version: int
    content_hash: str
    reason: str
    score: float = 0.0


class SkillExecutedPayload(EventPayload):
    skill_id: str
    version_id: str
    slug: str
    script: str | None = None
    tool_name: str | None = None


class SkillOutcomePayload(EventPayload):
    skill_id: str
    version_id: str
    run_id: str
    outcome: str
    tool_calls: int
    correction: bool = False


class SkillEvaluationPayload(EventPayload):
    skill_id: str
    version_id: str
    kind: str
    status: str
    score: float
    report: dict[str, JsonValue] = Field(default_factory=dict)


class SkillControlPayload(EventPayload):
    skill_id: str
    version_id: str
    action: str
    reason: str


def _empty_trigger_conditions() -> list[str]:
    return []


class ScarRecordPayload(EventPayload):
    scar_id: str
    title: str
    scope: str
    status: str
    severity: str
    failure_signature: str
    description: str
    trigger: dict[str, JsonValue] = Field(default_factory=dict)
    expected_behavior: str
    evidence_event_ids: list[str] = Field(default_factory=list)
    detection: str = "explicit_correction"


class ScarTransitionPayload(EventPayload):
    scar_id: str
    previous_status: str
    status: str
    reason: str
    repair_id: str | None = None


class ScarEditChangePayload(EventPayload):
    previous: JsonValue
    value: JsonValue


class ScarEditedPayload(EventPayload):
    scar_id: str
    status: str
    changes: dict[str, ScarEditChangePayload]


class ScarTriggerPayload(EventPayload):
    scar_id: str
    run_id: str
    matched_on: list[str] = Field(default_factory=_empty_trigger_conditions)
    regression: bool = False


class ScarGuardPayload(EventPayload):
    scar_id: str
    run_id: str
    successful_guard_count: int
    held: bool


class ScarRepairPayload(EventPayload):
    scar_id: str
    repair_id: str
    version: int
    repair_layer: str
    risk: str
    required_authority: str
    rationale: str
    proposal: dict[str, JsonValue] = Field(default_factory=dict)
    evidence_event_ids: list[str] = Field(default_factory=list)


class ScarRepairDecisionPayload(EventPayload):
    scar_id: str
    repair_id: str
    decision: str
    reason: str
    checks: dict[str, JsonValue] = Field(default_factory=dict)


class ScarEvaluationPayload(EventPayload):
    scar_id: str
    repair_id: str
    kind: str
    status: str
    score: float = Field(ge=0, le=1)
    report: dict[str, JsonValue] = Field(default_factory=dict)


class CorrectionVerdictPayload(EventPayload):
    content: str
    is_correction: bool


def _empty_require_source_types() -> list[str]:
    return []


class ContextRuleEventPayload(EventPayload):
    rule_id: str
    version: int
    status: str
    condition: dict[str, JsonValue] = Field(default_factory=dict)
    require_source_types: list[str] = Field(default_factory=_empty_require_source_types)
    reason: str = ""


class PolicyRuleEventPayload(EventPayload):
    rule_id: str
    action: str = ""
    pattern: str = ""
    status: str
    reason: str = ""


def _empty_permissions() -> list[str]:
    return []


class PluginLifecyclePayload(EventPayload):
    plugin_id: str
    version_id: str = ""
    version: str = ""
    fingerprint: str = ""
    permissions: list[str] = Field(default_factory=_empty_permissions)
    enabled: bool = False


class PluginWorkerPayload(EventPayload):
    plugin_id: str
    status: str = ""
    message: str = ""


class PluginBrokerPayload(EventPayload):
    plugin_id: str
    method: str
    status: str = ""
    reason: str = ""


class PluginProposalPayload(EventPayload):
    proposal_id: str
    plugin_id: str = ""
    scar_id: str = ""
    permissions: list[str] = Field(default_factory=_empty_permissions)


EVENT_PAYLOADS: dict[str, type[EventPayload]] = {
    "session.opened": SessionOpenedPayload,
    "session.closed": SessionClosedPayload,
    "session.forked": SessionForkedPayload,
    "session.settings.changed": SessionSettingsPayload,
    "session.agent.changed": SessionAgentChangedPayload,
    "session.mode.changed": SessionModeChangedPayload,
    "session.title.changed": SessionTitleChangedPayload,
    "user.message": MessagePayload,
    "queue.enqueued": QueuedMessagePayload,
    "queue.removed": QueueRemovedPayload,
    "queue.promoted": QueueRemovedPayload,
    "queue.prioritized": QueuePrioritizedPayload,
    "queue.paused": QueueStatePayload,
    "queue.resumed": QueueStatePayload,
    "plan.proposed": PlanProposedPayload,
    "plan.note.queued": PlanNotePayload,
    "plan.note.applied": PlanNotePayload,
    "plan.execution.requested": PlanTransitionPayload,
    "plan.approved": PlanTransitionPayload,
    "plan.execution.started": PlanTransitionPayload,
    "plan.execution.completed": PlanTransitionPayload,
    "plan.execution.failed": PlanTransitionPayload,
    "tasks.replaced": TasksReplacedPayload,
    "task.added": TaskAddedPayload,
    "task.updated": TaskUpdatedPayload,
    "task.removed": TaskRemovedPayload,
    "user.correction": CorrectionPayload,
    "assistant.message": AssistantOutputPayload,
    "assistant.reasoning": AssistantReasoningPayload,
    "context.compiled": ContextCompiledPayload,
    "context.compaction.started": ContextCompactionStartedPayload,
    "context.compaction.completed": ContextCompactionCompletedPayload,
    "context.compaction.failed": ContextCompactionTerminalPayload,
    "context.compaction.cancelled": ContextCompactionTerminalPayload,
    "goal.created": GoalEventPayload,
    "goal.step.started": GoalEventPayload,
    "goal.progressed": GoalEventPayload,
    "goal.yielded": GoalEventPayload,
    "goal.resumed": GoalEventPayload,
    "goal.paused": GoalEventPayload,
    "goal.achieved": GoalEventPayload,
    "goal.blocked": GoalEventPayload,
    "goal.cancelled": GoalEventPayload,
    "model.requested": ModelRequestedPayload,
    "model.provider_state": ModelProviderStatePayload,
    "model.response.started": ModelStartedPayload,
    "model.usage": ModelUsagePayload,
    "model.tool_call": ModelToolCallPayload,
    "model.response.completed": ModelCompletedPayload,
    "model.response.failed": FailurePayload,
    "model.response.preempted": FailurePayload,
    "run.started": RunStartedPayload,
    "run.continuation.requested": RunContinuationPayload,
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
    "question.requested": QuestionRequestedPayload,
    "question.answered": QuestionAnsweredPayload,
    "trust.granted": TrustPayload,
    "trust.revoked": TrustPayload,
    "runtime.error": FailurePayload,
    "runtime.notice": RuntimeNoticePayload,
    "terminal.started": TerminalStartedPayload,
    "terminal.completed": TerminalFinishedPayload,
    "terminal.failed": TerminalFinishedPayload,
    "terminal.stopped": TerminalFinishedPayload,
    "delegation.requested": DelegationRequestedPayload,
    "delegation.task_card": DelegationTaskCardPayload,
    "delegation.completed": DelegationTerminalPayload,
    "delegation.failed": DelegationTerminalPayload,
    "memory.proposed": MemoryRecordPayload,
    "memory.accepted": MemoryTransitionPayload,
    "memory.rejected": MemoryTransitionPayload,
    "memory.superseded": MemoryTransitionPayload,
    "memory.retracted": MemoryTransitionPayload,
    "memory.deleted": MemoryTransitionPayload,
    "memory.promoted": MemoryRecordPayload,
    "memory.job.queued": MemoryJobPayload,
    "memory.job.started": MemoryJobPayload,
    "memory.job.completed": MemoryJobPayload,
    "memory.job.failed": MemoryJobPayload,
    "memory.job.paused": MemoryJobPayload,
    "dream.started": DreamPayload,
    "dream.paused": DreamPayload,
    "dream.completed": DreamPayload,
    "dream.failed": DreamPayload,
    "memory.retrieved": MemoryRetrievedPayload,
    "memory.episode.projected": MemoryEpisodePayload,
    "memory.capture.requested": MemoryCapturePayload,
    "memory.promotion.requested": MemoryPromotionPayload,
    "skill.authoring.requested": SkillAuthoringPayload,
    "skill.workflow.observed": SkillWorkflowPayload,
    "skill.evolution.triggered": SkillAuthoringPayload,
    "skill.job.queued": SkillJobPayload,
    "skill.job.started": SkillJobPayload,
    "skill.job.completed": SkillJobPayload,
    "skill.job.failed": SkillJobPayload,
    "skill.job.paused": SkillJobPayload,
    "skill.drafted": SkillVersionPayload,
    "skill.validated": SkillEvaluationPayload,
    "skill.evaluated": SkillEvaluationPayload,
    "skill.rejected": SkillTransitionPayload,
    "skill.activated": SkillVersionPayload,
    "skill.superseded": SkillTransitionPayload,
    "skill.quarantined": SkillTransitionPayload,
    "skill.rolled_back": SkillRollbackPayload,
    "skill.catalogued": SkillCataloguedPayload,
    "skill.loaded": SkillLoadedPayload,
    "skill.executed": SkillExecutedPayload,
    "skill.outcome.recorded": SkillOutcomePayload,
    "skill.control.requested": SkillControlPayload,
    "skill.staled": SkillControlPayload,
    "skill.archived": SkillControlPayload,
    "skill.restored": SkillControlPayload,
    "skill.pinned": SkillControlPayload,
    "skill.unpinned": SkillControlPayload,
    "scar.recorded": ScarRecordPayload,
    "scar.opened": ScarTransitionPayload,
    "scar.dismissed": ScarTransitionPayload,
    "scar.deleted": ScarTransitionPayload,
    "scar.edited": ScarEditedPayload,
    "scar.repair_proposed": ScarTransitionPayload,
    "scar.guarded": ScarTransitionPayload,
    "scar.triggered": ScarTriggerPayload,
    "scar.guard.succeeded": ScarGuardPayload,
    "scar.healed": ScarTransitionPayload,
    "scar.regressed": ScarTransitionPayload,
    "scar.repair.proposed": ScarRepairPayload,
    "scar.repair.promoted": ScarRepairDecisionPayload,
    "scar.repair.rejected": ScarRepairDecisionPayload,
    "scar.repair.evaluated": ScarEvaluationPayload,
    "correction.verdict": CorrectionVerdictPayload,
    "context.rule.proposed": ContextRuleEventPayload,
    "context.rule.activated": ContextRuleEventPayload,
    "context.rule.retired": ContextRuleEventPayload,
    "policy.rule.proposed": PolicyRuleEventPayload,
    "policy.rule.activated": PolicyRuleEventPayload,
    "policy.rule.retired": PolicyRuleEventPayload,
    "plugin.installed": PluginLifecyclePayload,
    "plugin.enabled": PluginLifecyclePayload,
    "plugin.disabled": PluginLifecyclePayload,
    "plugin.removed": PluginLifecyclePayload,
    "plugin.worker.started": PluginWorkerPayload,
    "plugin.worker.stopped": PluginWorkerPayload,
    "plugin.worker.failed": PluginWorkerPayload,
    "plugin.capability.registered": PluginLifecyclePayload,
    "plugin.broker.requested": PluginBrokerPayload,
    "plugin.broker.completed": PluginBrokerPayload,
    "plugin.proposal.created": PluginProposalPayload,
}


class UnknownEventType(ValueError):
    pass


def validate_payload(event_type: str, payload: dict[str, Any]) -> dict[str, JsonValue]:
    model = EVENT_PAYLOADS.get(event_type)
    if model is None:
        raise UnknownEventType(f"unknown event type: {event_type}")
    return JSON_OBJECT.validate_python(model.model_validate(payload).model_dump(mode="json"))
