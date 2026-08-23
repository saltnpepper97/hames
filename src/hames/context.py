"""Deterministic M0 model context assembly."""

from __future__ import annotations

import hashlib

from pydantic import BaseModel, ConfigDict

from hames.agent import AgentCapsule
from hames.ledger import Event, Session
from hames.providers import ProviderMessage

CORE_CONTRACT = """You are an agent running inside Hames, a trusted local harness.
Be direct and truthful. Never claim to have used tools or inspected files unless
the harness supplied the corresponding result. The harness owns permissions,
persistence, durable memory, Flows, and side effects. In M0 you have no tools.
"""


class ContextManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    core_contract_hash: str
    agent_capsule_hash: str
    history_event_ids: list[str]
    working_directory: str
    source_order: list[str]


class CompiledContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    system: str
    messages: list[ProviderMessage]
    manifest: ContextManifest


def compile_context(
    session: Session,
    events: list[Event],
    capsule: AgentCapsule,
) -> CompiledContext:
    messages: list[ProviderMessage] = []
    history_ids: list[str] = []
    reasoning_by_run: dict[str, str] = {}
    for event in events:
        if event.type == "assistant.reasoning" and event.run_id:
            reasoning_by_run[event.run_id] = str(event.payload.get("content", ""))
            history_ids.append(event.id)
        elif event.type == "user.message":
            messages.append(ProviderMessage(role="user", content=str(event.payload["content"])))
            history_ids.append(event.id)
        elif event.type == "assistant.message":
            messages.append(
                ProviderMessage(
                    role="assistant",
                    content=str(event.payload.get("content", "")),
                    reasoning_content=reasoning_by_run.get(event.run_id or "", ""),
                )
            )
            history_ids.append(event.id)

    system = (
        f"{CORE_CONTRACT}\nAgent instructions:\n{capsule.instructions}\n"
        f"Current working directory: {session.working_directory}"
    )
    return CompiledContext(
        system=system,
        messages=messages,
        manifest=ContextManifest(
            core_contract_hash=hashlib.sha256(CORE_CONTRACT.encode()).hexdigest(),
            agent_capsule_hash=capsule.content_hash,
            history_event_ids=history_ids,
            working_directory=session.working_directory,
            source_order=["core.contract", "agent.instructions", "session.history", "run.cwd"],
        ),
    )
