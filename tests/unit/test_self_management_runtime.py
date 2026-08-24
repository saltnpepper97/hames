# pyright: reportPrivateUsage=false

from __future__ import annotations

from pathlib import Path

import pytest

from hames.gateway import GatewayState
from hames.paths import HamesPaths
from hames.providers.fake import FakeProvider
from hames.skills import SkillDraft
from hames.tools import (
    MemoryAddArguments,
    MemoryEditArguments,
    MemoryForgetArguments,
    MemorySearchArguments,
    ScarControlArguments,
    ScarListArguments,
    ScarRecordArguments,
    SkillCatalogArguments,
    SkillControlArguments,
)


def _evidence(state: GatewayState, session_id: str, content: str) -> str:
    session = state.ledger.get_session(session_id)
    return state.ledger.append(
        session_id=session.id,
        agent_id=session.agent_id,
        event_type="tool.started",
        payload={"tool_call_id": content, "name": "fixture"},
    ).id


@pytest.mark.asyncio
async def test_runtime_self_management_memory_and_scar_lifecycles(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    state = GatewayState.create(paths, providers={"fake": FakeProvider([])})
    session = state.ledger.create_session(
        working_directory=tmp_path,
        agent_id="default",
        provider="fake",
        model="fixture",
    )
    try:
        added = await state.runs._handle_self_management_tool(
            "run-add",
            session,
            MemoryAddArguments(
                layer="relationship",
                visibility="global",
                subject="user:local",
                predicate="prefers_response_style",
                value="concise",
                summary="The user prefers concise responses.",
            ),
            "memory_add",
            _evidence(state, session.id, "memory-add"),
        )
        assert added.status == "completed"
        memory = state.runs.memory.list_visible(session)[0]
        assert memory.status == "active"

        searched = await state.runs._handle_self_management_tool(
            "run-search",
            session,
            MemorySearchArguments(query="concise responses"),
            "memory_search",
            _evidence(state, session.id, "memory-search"),
        )
        assert searched.structured_data["count"] == 1

        edited = await state.runs._handle_self_management_tool(
            "run-edit",
            session,
            MemoryEditArguments(
                memory_id=memory.id,
                value="detailed",
                summary="The user prefers detailed responses.",
            ),
            "memory_edit",
            _evidence(state, session.id, "memory-edit"),
        )
        assert edited.status == "completed"
        replacement = state.runs.memory.list_visible(session)[0]
        assert replacement.summary == "The user prefers detailed responses."
        assert state.runs.memory.get(memory.id).status == "superseded"

        forgotten = await state.runs._handle_self_management_tool(
            "run-forget",
            session,
            MemoryForgetArguments(memory_id=replacement.id),
            "memory_forget",
            _evidence(state, session.id, "memory-forget"),
        )
        assert forgotten.status == "completed"
        assert state.runs.memory.list_visible(session) == []

        recorded = await state.runs._handle_self_management_tool(
            "run-scar",
            session,
            ScarRecordArguments(
                title="Do not infer project identity from a greeting",
                failure_signature="assistant:unprompted_project_assumption",
                description="The assistant added project context to a simple greeting.",
                expected_behavior="Answer greetings without inventing a project-specific frame.",
            ),
            "scar_record",
            _evidence(state, session.id, "scar-record"),
        )
        assert recorded.status == "completed"
        scar = state.runs.scar_store.list_scars(session)[0]
        assert scar.status == "open"

        listed = await state.runs._handle_self_management_tool(
            "run-scar-list",
            session,
            ScarListArguments(status="open"),
            "scar_list",
            _evidence(state, session.id, "scar-list"),
        )
        assert listed.structured_data["count"] == 1

        dismissed = await state.runs._handle_self_management_tool(
            "run-scar-dismiss",
            session,
            ScarControlArguments(scar_id=scar.id, action="dismiss", reason="obsolete"),
            "scar_control",
            _evidence(state, session.id, "scar-dismiss"),
        )
        assert dismissed.status == "completed"
        assert state.runs.scar_store.get(scar.id).status == "dismissed"
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_runtime_skill_catalog_and_controls_reuse_registry(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    state = GatewayState.create(paths, providers={"fake": FakeProvider([])})
    session = state.ledger.create_session(
        working_directory=tmp_path,
        agent_id="default",
        provider="fake",
        model="fixture",
    )
    try:
        source_id = _evidence(state, session.id, "skill-source")
        draft = state.runs.skills.create_draft(
            session=session,
            draft=SkillDraft(
                id="inspect-carefully",
                name="Inspect Carefully",
                description="Inspect relevant files before making a change.",
                scope="workspace",
                tools=["read_file"],
                triggers=["inspect"],
                instructions="Read the relevant files, then report the evidence.",
            ),
            evidence_event_ids=[source_id],
            created_by="user",
            run_id=None,
            causation_id=source_id,
        )
        state.runs.skills.activate(
            session=session,
            version_id=draft.version.id,
            causation_id=draft.events[-1].id,
        )

        catalog = await state.runs._handle_self_management_tool(
            "run-skill-list",
            session,
            SkillCatalogArguments(query="inspect"),
            "skill_catalog",
            _evidence(state, session.id, "skill-list"),
        )
        assert catalog.structured_data["count"] == 1

        pinned = await state.runs._handle_self_management_tool(
            "run-skill-pin",
            session,
            SkillControlArguments(
                id="inspect-carefully", action="pin", reason="user selected this version"
            ),
            "skill_control",
            _evidence(state, session.id, "skill-pin"),
        )
        assert pinned.status == "completed"
        assert state.runs.skills.get_visible(session, "inspect-carefully").pinned

        archived = await state.runs._handle_self_management_tool(
            "run-skill-archive",
            session,
            SkillControlArguments(
                id="inspect-carefully", action="archive", reason="not currently needed"
            ),
            "skill_control",
            _evidence(state, session.id, "skill-archive"),
        )
        assert archived.status == "completed"
        assert state.runs.skills.visible(session) == []

        restored = await state.runs._handle_self_management_tool(
            "run-skill-restore",
            session,
            SkillControlArguments(id="inspect-carefully", action="restore", reason="needed again"),
            "skill_control",
            _evidence(state, session.id, "skill-restore"),
        )
        assert restored.status == "completed"
        assert len(state.runs.skills.visible(session)) == 1
    finally:
        await state.runs.close()
