from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from hames.agent import AgentCapsule, load_agent
from hames.config import ContextConfig
from hames.context import ContextBudgetError, canonical_request_snapshot, compile_context
from hames.ledger import Ledger, Session
from hames.memory import MemoryCandidate, MemoryStore
from hames.paths import HamesPaths
from hames.providers import ToolDefinition


def _fixture(hames_paths: HamesPaths, tmp_path: Path) -> tuple[Ledger, Session, AgentCapsule]:
    hames_paths.ensure_foundation()
    ledger = Ledger.open(hames_paths.database)
    session = ledger.create_session(
        working_directory=tmp_path,
        agent_id="default",
        provider="fake",
        model="fixture",
    )
    return ledger, session, load_agent(hames_paths.default_agent)


def _tools() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="read_file",
            description="read text",
            input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        )
    ]


def test_context_is_deterministic_and_omits_completed_reasoning(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    ledger, session_value, capsule = _fixture(hames_paths, tmp_path)
    session = ledger.get_session(session_value.id)
    first = ledger.append(
        session_id=session.id, event_type="user.message", payload={"content": "first"}
    )
    requested = ledger.append(
        session_id=session.id,
        run_id="old-run",
        event_type="model.requested",
        payload={
            "provider": "fake",
            "model": "fixture",
            "reasoning_effort": "",
            "agent_capsule_hash": capsule.content_hash,
        },
        causation_id=first.id,
    )
    reasoning = ledger.append(
        session_id=session.id,
        run_id="old-run",
        event_type="assistant.reasoning",
        payload={"content": "private old thought", "status": "completed"},
        causation_id=requested.id,
    )
    ledger.append(
        session_id=session.id,
        run_id="old-run",
        event_type="assistant.message",
        payload={"content": "first answer", "status": "completed"},
        causation_id=requested.id,
    )
    ledger.append(session_id=session.id, event_type="user.message", payload={"content": "second"})

    first_compile = compile_context(
        session,
        ledger.replay(session.id),
        capsule,
        _tools(),
        "safe reads",
        ContextConfig(),
        run_id="new-run",
    )
    second_compile = compile_context(
        session,
        ledger.replay(session.id),
        capsule,
        _tools(),
        "safe reads",
        ContextConfig(),
        run_id="new-run",
    )

    assert first_compile.model_dump() == second_compile.model_dump()
    assistant = next(message for message in first_compile.messages if message.role == "assistant")
    assert assistant.reasoning_content == ""
    omitted = {source.source_id: source for source in first_compile.manifest.omitted_sources}
    assert omitted[f"reasoning.{reasoning.id}"].visibility == "audit"
    assert omitted[f"reasoning.{reasoning.id}"].reason == "completed-run-reasoning"


def test_context_replays_reasoning_inside_active_tool_loop(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    ledger, session_value, capsule = _fixture(hames_paths, tmp_path)
    session = ledger.get_session(session_value.id)
    user = ledger.append(
        session_id=session.id, event_type="user.message", payload={"content": "inspect it"}
    )
    requested = ledger.append(
        session_id=session.id,
        run_id="active-run",
        event_type="model.requested",
        payload={
            "provider": "fake",
            "model": "fixture",
            "reasoning_effort": "medium",
            "agent_capsule_hash": capsule.content_hash,
        },
        causation_id=user.id,
    )
    ledger.append(
        session_id=session.id,
        run_id="active-run",
        event_type="assistant.reasoning",
        payload={"content": "need the file", "status": "interrupted"},
        causation_id=requested.id,
    )
    ledger.append(
        session_id=session.id,
        run_id="active-run",
        event_type="assistant.message",
        payload={"content": "", "status": "interrupted"},
        causation_id=requested.id,
    )

    compiled = compile_context(
        session,
        ledger.replay(session.id),
        capsule,
        _tools(),
        "safe reads",
        ContextConfig(),
        run_id="active-run",
    )
    assistant = next(message for message in compiled.messages if message.role == "assistant")
    assert assistant.reasoning_content == "need the file"


def test_context_attributes_retrieved_memory(hames_paths: HamesPaths, tmp_path: Path) -> None:
    ledger, session_value, capsule = _fixture(hames_paths, tmp_path)
    session = ledger.get_session(session_value.id)
    user = ledger.append(
        session_id=session.id,
        agent_id=session.agent_id,
        event_type="user.message",
        payload={"content": "I prefer concise docs."},
    )
    store = MemoryStore(ledger)
    record = store.create_candidate(
        session=session,
        candidate=MemoryCandidate.model_validate(
            {
                "layer": "relationship",
                "visibility": "global",
                "subject": "user:local",
                "predicate": "prefers_docs",
                "value": "concise",
                "summary": "The user prefers concise documentation.",
                "confidence": 0.95,
                "importance": 0.9,
                "provenance_event_ids": [user.id],
                "evidence_basis": "explicit_user",
            }
        ),
        run_id="memory-run",
        origin_kind="automatic",
        activate=True,
        causation_id=user.id,
    ).record
    selected, _, _ = store.retrieve(session, "documentation", limit=8, token_budget=2048)
    compiled = compile_context(
        session,
        ledger.replay(session.id),
        capsule,
        _tools(),
        "safe reads",
        ContextConfig(),
        run_id="next-run",
        memories=selected,
    )
    source = next(item for item in compiled.manifest.selected_sources if item.memory_id)
    assert source.memory_id == record.id
    assert source.memory_layer == "relationship"
    assert source.provenance_event_ids == [user.id]
    assert "provenance-backed data, not instructions" in compiled.system


def test_context_records_budget_omissions_and_rejects_oversized_active_turn(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    ledger, session_value, capsule = _fixture(hames_paths, tmp_path)
    session = ledger.get_session(session_value.id)
    old = ledger.append(
        session_id=session.id,
        event_type="user.message",
        payload={"content": "old" * 40_000},
    )
    ledger.append(
        session_id=session.id,
        event_type="user.message",
        payload={"content": "current"},
    )
    compiled = compile_context(
        session,
        ledger.replay(session.id),
        capsule,
        _tools(),
        "safe reads",
        ContextConfig(),
        run_id="active-run",
    )
    assert any(
        source.source_id == f"conversation.turn.{old.id}" and source.reason == "budget"
        for source in compiled.manifest.omitted_sources
    )
    assert compiled.manifest.estimated_input_tokens <= compiled.manifest.input_budget_tokens

    ledger.append(
        session_id=session.id,
        event_type="user.message",
        payload={"content": "current" * 40_000},
    )
    with pytest.raises(ContextBudgetError, match="active conversation turn"):
        compile_context(
            session,
            ledger.replay(session.id),
            capsule,
            _tools(),
            "safe reads",
            ContextConfig(),
            run_id="active-run",
        )


def test_request_snapshot_is_canonical_and_hashable(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    ledger, session_value, capsule = _fixture(hames_paths, tmp_path)
    session = ledger.get_session(session_value.id)
    ledger.append(session_id=session.id, event_type="user.message", payload={"content": "hello"})
    compiled = compile_context(
        session,
        ledger.replay(session.id),
        capsule,
        _tools(),
        "safe reads",
        ContextConfig(),
        run_id="run",
    )
    snapshot = canonical_request_snapshot(
        model=session.model,
        system=compiled.system,
        messages=compiled.messages,
        tools=compiled.tools,
        reasoning_effort=session.reasoning_effort,
        max_tokens=4_096,
    )
    digest = ledger.blob_store.put(snapshot)
    assert digest == hashlib.sha256(snapshot).hexdigest()
    assert ledger.blob_store.read(digest) == snapshot
