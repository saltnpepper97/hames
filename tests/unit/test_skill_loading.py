# pyright: reportPrivateUsage=false
from pathlib import Path
from typing import Any

import pytest

from hames.gateway import GatewayState
from hames.paths import HamesPaths
from hames.runtime import ActiveClock
from hames.tools import SkillLoadArguments, ToolContext


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_only", "explicit", "scope", "permitted"),
    [
        (False, False, {}, True),
        (True, False, {}, False),
        (True, True, {}, True),
        (False, False, {"skill_denied": ["review"]}, False),
        (False, False, {"skill_allowlists": [["other"]]}, False),
        (False, False, {"skill_allowlists": [["review"]]}, True),
    ],
)
async def test_catalog_id_loading_preserves_invocation_and_inherited_authority(
    tmp_path: Path,
    user_only: bool,
    explicit: bool,
    scope: dict[str, Any],
    permitted: bool,
) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    package = paths.portable_global_skills / "review"
    package.mkdir(parents=True)
    package.joinpath("SKILL.md").write_text(
        "---\nname: review\ndescription: Review a topic.\n"
        f"disable-model-invocation: {str(user_only).lower()}\n---\nReview $ARGUMENTS.\n"
    )
    state = GatewayState.create(paths)
    try:
        parent = state.ledger.create_session(
            working_directory=tmp_path, agent_id="default", provider="fake", model="fixture"
        )
        session = parent
        if scope:
            event = state.ledger.append(
                session_id=parent.id,
                event_type="delegation.requested",
                payload={
                    "tool_call_id": "call",
                    "target_agent_id": "default",
                    "task": "Review",
                    "delegation_depth": 1,
                },
            )
            session = state.ledger.create_delegated_session(
                parent.id, parent_event_id=event.id, agent_id="default"
            )
            state.ledger.append(
                session_id=session.id,
                event_type="delegation.task_card",
                payload={
                    "parent_session_id": parent.id,
                    "parent_run_id": "parent-run",
                    "parent_event_id": event.id,
                    "target_agent_id": "default",
                    "task": "Review",
                    "delegation_depth": 1,
                    "allowed_tools": ["skill_load"],
                    **scope,
                },
            )
        skill = state.runs.skills.get_visible(session, "review")
        if explicit:
            state.runs._loaded_skills["run"] = {
                "review": skill.model_copy(update={"instructions": "Review requested topic."})
            }
        context = ToolContext(
            project_root=tmp_path,
            scratch_root=tmp_path / "scratch",
            blobs=state.ledger.blob_store,
            config=state.config.tools,
        )
        result = await state.runs._handle_skill_tool(
            "run",
            session,
            SkillLoadArguments(id=skill.skill_id),
            "skill_load",
            context,
            "event",
            ActiveClock(30),
        )
        assert result.status == ("completed" if permitted else "rejected")
        if explicit and permitted:
            assert result.content == "Review requested topic."
        assert package.joinpath("SKILL.md").read_text().endswith("Review $ARGUMENTS.\n")
    finally:
        await state.runs.close()
