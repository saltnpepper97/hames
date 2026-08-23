"""Deterministic M0 model context assembly."""

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, ConfigDict

from hames.agent import AgentCapsule
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


class ContextManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    core_contract_hash: str
    agent_capsule_hash: str
    history_event_ids: list[str]
    working_directory: str
    source_order: list[str]
    tool_schema_hash: str
    policy_summary_hash: str


class CompiledContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    system: str
    messages: list[ProviderMessage]
    manifest: ContextManifest


def compile_context(
    session: Session,
    events: list[Event],
    capsule: AgentCapsule,
    tools: list[ToolDefinition],
    policy_summary: str,
) -> CompiledContext:
    messages: list[ProviderMessage] = []
    history_ids: list[str] = []
    reasoning_by_request: dict[str, str] = {}
    assistants_by_request: dict[str, ProviderMessage] = {}
    for event in events:
        if event.type == "assistant.reasoning" and event.causation_id:
            reasoning_by_request[event.causation_id] = str(event.payload.get("content", ""))
            history_ids.append(event.id)
        elif event.type == "user.message":
            messages.append(ProviderMessage(role="user", content=str(event.payload["content"])))
            history_ids.append(event.id)
        elif event.type == "assistant.message":
            message = ProviderMessage(
                role="assistant",
                content=str(event.payload.get("content", "")),
                reasoning_content=reasoning_by_request.get(event.causation_id or "", ""),
            )
            messages.append(message)
            if event.causation_id:
                assistants_by_request[event.causation_id] = message
            history_ids.append(event.id)
        elif event.type == "model.tool_call" and event.causation_id:
            assistant = assistants_by_request.get(event.causation_id)
            if assistant is None:
                assistant = ProviderMessage(role="assistant", content="")
                assistants_by_request[event.causation_id] = assistant
                messages.append(assistant)
            assistant.tool_calls.append(
                ToolCall(
                    id=str(event.payload["tool_call_id"]),
                    name=str(event.payload["name"]),
                    arguments=dict(event.payload.get("arguments", {})),
                )
            )
            history_ids.append(event.id)
        elif event.type in {"tool.completed", "tool.failed", "tool.rejected"}:
            messages.append(
                ProviderMessage(
                    role="tool",
                    content=_tool_result_content(event),
                    tool_call_id=str(event.payload["tool_call_id"]),
                    tool_name=str(event.payload["name"]),
                )
            )
            history_ids.append(event.id)

    encoded_tools = json.dumps(
        [tool.model_dump(mode="json") for tool in tools], separators=(",", ":"), sort_keys=True
    )
    system = (
        f"{CORE_CONTRACT}\nAgent instructions:\n{capsule.instructions}\n"
        f"Current project workspace: {session.working_directory}\n"
        f"Policy summary: {policy_summary}"
    )
    return CompiledContext(
        system=system,
        messages=messages,
        manifest=ContextManifest(
            core_contract_hash=hashlib.sha256(CORE_CONTRACT.encode()).hexdigest(),
            agent_capsule_hash=capsule.content_hash,
            history_event_ids=history_ids,
            working_directory=session.working_directory,
            source_order=[
                "core.contract",
                "agent.instructions",
                "session.history",
                "run.cwd",
                "policy.summary",
                "tool.schemas",
            ],
            tool_schema_hash=hashlib.sha256(encoded_tools.encode()).hexdigest(),
            policy_summary_hash=hashlib.sha256(policy_summary.encode()).hexdigest(),
        ),
    )


def _tool_result_content(event: Event) -> str:
    return json.dumps(
        {
            "status": event.payload.get("status"),
            "summary": event.payload.get("summary"),
            "content": event.payload.get("content"),
            "structured_data": event.payload.get("structured_data", {}),
            "truncated": event.payload.get("truncated", False),
            "blob_references": event.payload.get("blob_references", []),
        },
        separators=(",", ":"),
    )
