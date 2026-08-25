from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from hames.agent import AgentCapsule, load_agent
from hames.config import ContextConfig
from hames.context import (
    ContextBudgetError,
    PluginContextItem,
    canonical_request_snapshot,
    compile_context,
)
from hames.ledger import Ledger, Session
from hames.memory import MemoryCandidate, MemoryStore, canonical_memory_context
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


def test_completed_compaction_replaces_only_the_prefix_in_model_context(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    ledger, session_value, capsule = _fixture(hames_paths, tmp_path)
    session = ledger.get_session(session_value.id)
    old = ledger.append(
        session_id=session.id,
        event_type="user.message",
        payload={"content": "old requirement"},
    )
    recent = ledger.append(
        session_id=session.id,
        event_type="user.message",
        payload={"content": "recent requirement"},
    )
    compacted = ledger.append(
        session_id=session.id,
        event_type="context.compaction.completed",
        payload={
            "compaction_id": "compact-1",
            "trigger": "manual",
            "summary": "The earlier user required the old behavior.",
            "cutoff_event_id": old.id,
            "cutoff_sequence": old.sequence,
            "source_event_ids": [old.id],
            "provider": "fake",
            "model": "fixture",
            "reasoning_effort": "",
            "turns_compacted": 1,
            "before_tokens": 12,
            "after_tokens": 9,
            "passes": 1,
            "partial": False,
        },
    )

    compiled = compile_context(
        session,
        ledger.replay(session.id),
        capsule,
        _tools(),
        "safe reads",
        ContextConfig(),
        run_id="new-run",
    )

    assert [message.content for message in compiled.messages] == ["recent requirement"]
    assert "The earlier user required the old behavior." in compiled.system
    source = next(
        item for item in compiled.manifest.selected_sources if item.source_type == "compaction"
    )
    assert source.event_ids == [compacted.id]
    assert old.id not in compiled.manifest.contributing_event_ids
    assert recent.id in compiled.manifest.contributing_event_ids


def test_active_goal_is_attributed_and_supplies_a_distinct_step_prompt(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    ledger, session_value, capsule = _fixture(hames_paths, tmp_path)
    session = ledger.get_session(session_value.id)
    created = ledger.append(
        session_id=session.id,
        event_type="goal.created",
        payload={"goal_id": "goal-1", "objective": "Finish the release", "status": "running"},
    )
    step = ledger.append(
        session_id=session.id,
        run_id="goal-run",
        event_type="goal.step.started",
        payload={
            "goal_id": "goal-1",
            "objective": "Finish the release",
            "status": "running",
            "step": 1,
            "run_id": "goal-run",
        },
    )

    compiled = compile_context(
        session,
        ledger.replay(session.id),
        capsule,
        _tools(),
        "safe reads",
        ContextConfig(),
        run_id="goal-run",
    )

    assert "Objective: Finish the release" in compiled.system
    assert compiled.messages[-1].content.startswith("Continue the active autonomous goal")
    source = next(item for item in compiled.manifest.selected_sources if item.source_type == "goal")
    assert created.id in source.event_ids
    assert step.id in source.event_ids


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


def test_memory_retrieval_budget_accounts_for_canonical_provenance_shape(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    ledger, session_value, capsule = _fixture(hames_paths, tmp_path)
    session = ledger.get_session(session_value.id)
    user = ledger.append(
        session_id=session.id,
        agent_id=session.agent_id,
        event_type="user.message",
        payload={"content": "Record several detailed project facts."},
    )
    store = MemoryStore(ledger)
    for index in range(8):
        store.create_candidate(
            session=session,
            candidate=MemoryCandidate(
                layer="semantic",
                visibility="workspace",
                subject="project:hames",
                predicate=f"detailed_fact_{index}",
                value="x" * 450,
                summary=f"Detailed fact {index}: " + "x" * 450,
                confidence=0.95,
                importance=0.9,
                provenance_event_ids=[user.id],
                evidence_basis="explicit_user",
            ),
            run_id=f"memory-{index}",
            origin_kind="automatic",
            activate=True,
            causation_id=user.id,
        )
    selected, _, _ = store.retrieve(session, "detailed fact", limit=8, token_budget=2048)
    encoded = canonical_memory_context(selected)
    assert max(1, len(encoded.encode()) // 4) <= 2048
    compiled = compile_context(
        session,
        ledger.replay(session.id),
        capsule,
        _tools(),
        "safe reads",
        ContextConfig(),
        run_id="new-run",
        memories=selected,
    )
    assert any(source.source_type == "memory" for source in compiled.manifest.selected_sources)
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
        reasoning_budget_tokens=512,
        max_tokens=4_096,
    )
    assert b'"reasoning_budget_tokens":512' in snapshot
    digest = ledger.blob_store.put(snapshot)
    assert digest == hashlib.sha256(snapshot).hexdigest()
    assert ledger.blob_store.read(digest) == snapshot


def test_context_rule_enforcement_requires_matching_source(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    from hames.context import ContextRuleViolation
    from hames.memory import MemoryAnchor
    from hames.rules import ContextRule, ContextRuleCondition

    ledger, session_value, capsule = _fixture(hames_paths, tmp_path)
    session = ledger.get_session(session_value.id)
    ledger.append(session_id=session.id, event_type="user.message", payload={"content": "hi"})
    rule = ContextRule(
        id="rule-1",
        version=1,
        description="Milestone context must be present.",
        condition=ContextRuleCondition(workspace_paths=[session.working_directory]),
        require_source_types=["memory"],
        status="active",
        scar_id=None,
        source_session_id=session.id,
        created_by="user",
        created_at="2026-01-01",
        updated_at="2026-01-01",
    )

    with pytest.raises(ContextRuleViolation):
        compile_context(
            session,
            ledger.replay(session.id),
            capsule,
            _tools(),
            "safe reads",
            ContextConfig(),
            run_id="run-1",
            context_rules=[rule],
        )

    store = MemoryStore(ledger)
    user_event_id = ledger.list_events(session.id)[0].id
    mutation = store.create_candidate(
        session=session,
        candidate=MemoryCandidate(
            layer="semantic",
            visibility="workspace",
            subject="project",
            predicate="current_milestone",
            value={"text": "m8"},
            summary="Current milestone is m8.",
            confidence=1.0,
            importance=0.8,
            anchors=[MemoryAnchor(kind="workspace", value=session.working_directory)],
            provenance_event_ids=[user_event_id],
            evidence_basis="explicit_user",
        ),
        run_id=None,
        origin_kind="explicit",
        activate=True,
        causation_id=user_event_id,
    )
    retrieved, _, _ = store.retrieve(session, "milestone", limit=4, token_budget=512)
    assert any(item.record.id == mutation.record.id for item in retrieved)
    compiled = compile_context(
        session,
        ledger.replay(session.id),
        capsule,
        _tools(),
        "safe reads",
        ContextConfig(),
        run_id="run-2",
        memories=retrieved,
        context_rules=[rule],
    )
    memory_sources = [
        source for source in compiled.manifest.selected_sources if source.source_type == "memory"
    ]
    assert memory_sources


def test_non_matching_context_rules_do_not_apply(hames_paths: HamesPaths, tmp_path: Path) -> None:
    from hames.rules import ContextRule, ContextRuleCondition

    ledger, session_value, capsule = _fixture(hames_paths, tmp_path)
    session = ledger.get_session(session_value.id)
    ledger.append(session_id=session.id, event_type="user.message", payload={"content": "hi"})
    other_root = tmp_path / "elsewhere"
    other_root.mkdir()
    rule = ContextRule(
        id="rule-2",
        version=1,
        description="Other project only.",
        condition=ContextRuleCondition(workspace_paths=[str(other_root)]),
        require_source_types=["memory"],
        status="active",
        scar_id=None,
        source_session_id=session.id,
        created_by="user",
        created_at="2026-01-01",
        updated_at="2026-01-01",
    )
    compiled = compile_context(
        session,
        ledger.replay(session.id),
        capsule,
        _tools(),
        "safe reads",
        ContextConfig(),
        run_id="run-1",
        context_rules=[rule],
    )
    assert compiled.manifest.selected_sources


def test_plugin_context_is_attributed_and_hashed(hames_paths: HamesPaths, tmp_path: Path) -> None:
    ledger, session_value, capsule = _fixture(hames_paths, tmp_path)
    session = ledger.get_session(session_value.id)
    ledger.append(session_id=session.id, event_type="user.message", payload={"content": "stats"})
    text = "file count 3"
    compiled = compile_context(
        session,
        ledger.replay(session.id),
        capsule,
        _tools(),
        "safe reads",
        ContextConfig(),
        run_id="run-1",
        plugin_sources=[PluginContextItem("project-stats", "files", text)],
    )
    source = next(
        item
        for item in compiled.manifest.selected_sources
        if item.source_id == "plugin.project-stats.files"
    )
    assert source.source_type == "plugin"
    assert source.origin == "plugin"
    assert source.content_hash == hashlib.sha256(text.encode()).hexdigest()
    assert "Plugin project-stats (files)" in compiled.system
    assert "file count 3" in compiled.system
