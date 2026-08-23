"""Deterministic, attributed model-context compilation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from hames.agent import AgentCapsule
from hames.config import ContextConfig
from hames.ledger import Event, Session
from hames.providers import ProviderMessage, ToolCall, ToolDefinition

CORE_CONTRACT = """You are the reasoning model inside Hames, a trusted local coding-agent
harness. Hames owns context assembly, provider calls, permissions, persistence,
tool execution, and every side effect. Use only the supplied tools for filesystem
or command work. Tool results are evidence of what happened; a path in context is
not evidence that you inspected it. Work in the project workspace for requested
deliverables and use scratch for disposable experiments. Hames applies policy and
may reject or require human approval for an action; respect structured rejections
and choose a safer approach when possible. Conversation and tool history may be
supplied, so do not describe yourself as stateless per turn. Do not claim hidden
memory, Skills, or capabilities that the supplied context does not define.
"""

COMPILER_VERSION = 1
ESTIMATOR_VERSION = "utf8-bytes-div-4-v1"


class ContextBudgetError(RuntimeError):
    def __init__(self, message: str, *, details: dict[str, object]) -> None:
        super().__init__(message)
        self.details = details


class SourceDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    source_type: str
    content_hash: str
    priority: int
    estimated_tokens: int
    selected_tokens: int = 0
    visibility: Literal["model", "audit"] = "model"
    truncation: str = "none"
    reason: str = "selected"
    event_ids: list[str] = Field(default_factory=list)


class ContextManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    compiler_version: int = COMPILER_VERSION
    estimator_version: str = ESTIMATOR_VERSION
    provider: str
    model: str
    reasoning_effort: str
    context_window_tokens: int
    context_window_source: str
    input_budget_tokens: int
    output_reserve_tokens: int
    estimated_input_tokens: int
    selected_sources: list[SourceDecision]
    omitted_sources: list[SourceDecision]
    source_order: list[str]
    contributing_event_ids: list[str]
    request_hash: str = ""
    request_snapshot_blob_hash: str = ""


class CompiledContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    system: str
    messages: list[ProviderMessage]
    tools: list[ToolDefinition]
    manifest: ContextManifest


@dataclass(slots=True)
class _Turn:
    source_id: str
    messages: list[ProviderMessage] = field(default_factory=lambda: list[ProviderMessage]())
    event_ids: list[str] = field(default_factory=lambda: list[str]())

    @property
    def content_hash(self) -> str:
        return _hash_json([message.model_dump(mode="json") for message in self.messages])

    @property
    def estimated_tokens(self) -> int:
        return _estimate_messages(self.messages)


def compile_context(
    session: Session,
    events: list[Event],
    capsule: AgentCapsule,
    tools: list[ToolDefinition],
    policy_summary: str,
    config: ContextConfig,
    *,
    run_id: str,
) -> CompiledContext:
    input_budget = session.context_window_tokens - config.output_reserve_tokens
    if input_budget <= 0:
        raise ContextBudgetError(
            "model context window leaves no input capacity",
            details={
                "context_window_tokens": session.context_window_tokens,
                "output_reserve_tokens": config.output_reserve_tokens,
            },
        )

    stable_parts = [
        ("core.contract", CORE_CONTRACT),
        ("run.workspace", f"Current project workspace: {session.working_directory}"),
        ("policy.summary", f"Policy summary: {policy_summary}"),
    ]
    agent_part = ("agent.identity", f"Agent instructions:\n{capsule.instructions}")
    encoded_tools = _canonical_json([tool.model_dump(mode="json") for tool in tools])
    stable_tokens = sum(_estimate_text(content) for _, content in stable_parts)
    agent_tokens = _estimate_text(agent_part[1])
    tool_tokens = _estimate_text(encoded_tools)
    _require_category("stable instructions", stable_tokens, config.stable_instruction_limit_tokens)
    _require_category("agent identity", agent_tokens, config.agent_identity_limit_tokens)
    _require_category("tool schemas", tool_tokens, config.tool_schema_limit_tokens)

    selected: list[SourceDecision] = []
    omitted: list[SourceDecision] = []
    for priority, (source_id, content) in enumerate(stable_parts, start=100):
        selected.append(_source(source_id, "instruction", content, priority))
    selected.append(_source(agent_part[0], "agent", agent_part[1], 200))
    selected.append(_source("tool.schemas", "tools", encoded_tools, 150))

    fixed_tokens = stable_tokens + agent_tokens + tool_tokens
    remaining = input_budget - fixed_tokens
    if remaining <= 0:
        raise ContextBudgetError(
            "required instructions and tools exceed the model input budget",
            details={"input_budget_tokens": input_budget, "required_tokens": fixed_tokens},
        )

    turns, audit_reasoning = _conversation_turns(events, run_id)
    omitted.extend(audit_reasoning)

    selected_turns: list[_Turn] = []
    if turns:
        current = turns[-1]
        if current.estimated_tokens > remaining:
            compacted = _compact_turn(current)
            if compacted.estimated_tokens > remaining:
                raise ContextBudgetError(
                    "the active conversation turn exceeds the remaining model input budget",
                    details={
                        "source_id": current.source_id,
                        "estimated_tokens": compacted.estimated_tokens,
                        "remaining_tokens": remaining,
                    },
                )
            omitted.append(
                _turn_source(
                    current,
                    selected=False,
                    reason="compacted",
                    truncation="tool-results-to-summary",
                )
            )
            current = compacted
        selected_turns.append(current)
        remaining -= current.estimated_tokens

        for turn in reversed(turns[:-1]):
            candidate = turn
            truncation = "none"
            if candidate.estimated_tokens > remaining:
                candidate = _compact_turn(turn)
                truncation = "tool-results-to-summary"
            if candidate.estimated_tokens <= remaining:
                selected_turns.append(candidate)
                remaining -= candidate.estimated_tokens
                if truncation != "none":
                    omitted.append(
                        _turn_source(
                            turn,
                            selected=False,
                            reason="compacted",
                            truncation=truncation,
                        )
                    )
            else:
                omitted.append(_turn_source(turn, selected=False, reason="budget"))

    selected_turns.reverse()
    for turn in selected_turns:
        selected.append(_turn_source(turn, selected=True))
    messages = [message for turn in selected_turns for message in turn.messages]
    system = "\n".join([content for _, content in stable_parts] + [agent_part[1]])
    estimated_input = fixed_tokens + _estimate_messages(messages)
    contributing = [event_id for item in selected for event_id in item.event_ids]
    return CompiledContext(
        system=system,
        messages=messages,
        tools=tools,
        manifest=ContextManifest(
            provider=session.provider,
            model=session.model,
            reasoning_effort=session.reasoning_effort,
            context_window_tokens=session.context_window_tokens,
            context_window_source=session.context_window_source,
            input_budget_tokens=input_budget,
            output_reserve_tokens=config.output_reserve_tokens,
            estimated_input_tokens=estimated_input,
            selected_sources=selected,
            omitted_sources=omitted,
            source_order=[item.source_id for item in selected],
            contributing_event_ids=contributing,
        ),
    )


def canonical_request_snapshot(
    *,
    model: str,
    system: str,
    messages: list[ProviderMessage],
    tools: list[ToolDefinition],
    reasoning_effort: str,
    max_tokens: int,
) -> bytes:
    return _canonical_json(
        {
            "model": model,
            "system": system,
            "messages": [message.model_dump(mode="json") for message in messages],
            "tools": [tool.model_dump(mode="json") for tool in tools],
            "reasoning_effort": reasoning_effort,
            "max_tokens": max_tokens,
        }
    ).encode()


def _conversation_turns(
    events: list[Event], run_id: str
) -> tuple[list[_Turn], list[SourceDecision]]:
    turns: list[_Turn] = []
    current: _Turn | None = None
    reasoning_by_request: dict[str, tuple[str, Event]] = {}
    assistants_by_request: dict[str, ProviderMessage] = {}
    audit_reasoning: list[SourceDecision] = []

    for event in events:
        if event.type == "user.message":
            current = _Turn(source_id=f"conversation.turn.{event.id}")
            current.messages.append(
                ProviderMessage(role="user", content=str(event.payload["content"]))
            )
            current.event_ids.append(event.id)
            turns.append(current)
        elif event.type == "assistant.reasoning" and event.causation_id:
            content = str(event.payload.get("content", ""))
            reasoning_by_request[event.causation_id] = (content, event)
            if event.run_id != run_id:
                audit_reasoning.append(
                    SourceDecision(
                        source_id=f"reasoning.{event.id}",
                        source_type="reasoning",
                        content_hash=_hash_text(content),
                        priority=10,
                        estimated_tokens=_estimate_text(content),
                        visibility="audit",
                        reason="completed-run-reasoning",
                        event_ids=[event.id],
                    )
                )
        elif event.type == "assistant.message" and current is not None:
            reasoning = reasoning_by_request.get(event.causation_id or "")
            reasoning_content = reasoning[0] if reasoning and reasoning[1].run_id == run_id else ""
            message = ProviderMessage(
                role="assistant",
                content=str(event.payload.get("content", "")),
                reasoning_content=reasoning_content,
            )
            current.messages.append(message)
            current.event_ids.append(event.id)
            if reasoning_content and reasoning is not None:
                current.event_ids.append(reasoning[1].id)
            if event.causation_id:
                assistants_by_request[event.causation_id] = message
        elif event.type == "model.tool_call" and event.causation_id and current is not None:
            assistant = assistants_by_request.get(event.causation_id)
            if assistant is None:
                assistant = ProviderMessage(role="assistant", content="")
                assistants_by_request[event.causation_id] = assistant
                current.messages.append(assistant)
            assistant.tool_calls.append(
                ToolCall(
                    id=str(event.payload["tool_call_id"]),
                    name=str(event.payload["name"]),
                    arguments=dict(event.payload.get("arguments", {})),
                )
            )
            current.event_ids.append(event.id)
        elif (
            event.type in {"tool.completed", "tool.failed", "tool.rejected"} and current is not None
        ):
            current.messages.append(
                ProviderMessage(
                    role="tool",
                    content=_tool_result_content(event),
                    tool_call_id=str(event.payload["tool_call_id"]),
                    tool_name=str(event.payload["name"]),
                )
            )
            current.event_ids.append(event.id)
    return turns, audit_reasoning


def _compact_turn(turn: _Turn) -> _Turn:
    compacted = _Turn(source_id=turn.source_id, event_ids=list(turn.event_ids))
    for message in turn.messages:
        if message.role != "tool":
            compacted.messages.append(message.model_copy(deep=True))
            continue
        try:
            payload = json.loads(message.content)
        except json.JSONDecodeError:
            payload = {"summary": "tool result omitted during context compaction"}
        compacted.messages.append(
            message.model_copy(
                update={
                    "content": _canonical_json(
                        {
                            "status": payload.get("status"),
                            "summary": payload.get("summary"),
                            "truncated": True,
                            "blob_references": payload.get("blob_references", []),
                            "context_compaction": "content omitted; consult a tool again if needed",
                        }
                    )
                },
                deep=True,
            )
        )
    return compacted


def _tool_result_content(event: Event) -> str:
    return _canonical_json(
        {
            "status": event.payload.get("status"),
            "summary": event.payload.get("summary"),
            "content": event.payload.get("content"),
            "structured_data": event.payload.get("structured_data", {}),
            "truncated": event.payload.get("truncated", False),
            "blob_references": event.payload.get("blob_references", []),
        }
    )


def _source(source_id: str, source_type: str, content: str, priority: int) -> SourceDecision:
    tokens = _estimate_text(content)
    return SourceDecision(
        source_id=source_id,
        source_type=source_type,
        content_hash=_hash_text(content),
        priority=priority,
        estimated_tokens=tokens,
        selected_tokens=tokens,
    )


def _turn_source(
    turn: _Turn,
    *,
    selected: bool,
    reason: str = "selected",
    truncation: str = "none",
) -> SourceDecision:
    return SourceDecision(
        source_id=turn.source_id,
        source_type="conversation",
        content_hash=turn.content_hash,
        priority=50,
        estimated_tokens=turn.estimated_tokens,
        selected_tokens=turn.estimated_tokens if selected else 0,
        reason=reason,
        truncation=truncation,
        event_ids=turn.event_ids,
    )


def _require_category(name: str, actual: int, limit: int) -> None:
    if actual > limit:
        raise ContextBudgetError(
            f"{name} exceed their configured context limit",
            details={"category": name, "estimated_tokens": actual, "limit_tokens": limit},
        )


def _estimate_messages(messages: list[ProviderMessage]) -> int:
    return sum(
        _estimate_text(_canonical_json(message.model_dump(mode="json"))) + 4 for message in messages
    )


def _estimate_text(content: str) -> int:
    return max(1, (len(content.encode()) + 3) // 4)


def _canonical_json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, ensure_ascii=False)


def _hash_text(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def _hash_json(value: object) -> str:
    return _hash_text(_canonical_json(value))
