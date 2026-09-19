from pathlib import Path

import httpx
import pytest

from hames.gateway import GatewayState, create_app
from hames.paths import HamesPaths
from hames.providers.fake import FakeProvider


@pytest.mark.asyncio
async def test_custom_plan_command_uses_configured_agent_and_current_definition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = GatewayState.create(
        HamesPaths.resolve(root=tmp_path / "home"), providers={"fake": FakeProvider([])}
    )
    coordinator = state.agents.create("Coordinator")
    renamed = state.agents.update(coordinator.metadata.id, name="Project Coordinator")
    workspace = tmp_path / "repo"
    workspace.mkdir()
    session = state.ledger.create_session(
        working_directory=workspace, provider="fake", model="fixture", agent_id="default"
    )
    command = state.paths.root / "commands" / "review.toml"
    command.parent.mkdir()
    command.write_text('description="Review plan"\nagent="project-coordinator"\n')
    calls: list[tuple[str, str]] = []
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(state)),
            base_url="http://test",
            headers={"Authorization": f"Bearer {state.token}"},
        ) as client:
            listed = await client.get(f"/v1/sessions/{session.id}/commands")
            assert listed.status_code == 200
            assert listed.json()[0]["name"] == "review"
            rejected = await client.post(f"/v1/sessions/{session.id}/commands/review", json={})
            assert rejected.status_code == 400  # No approved/ready plan to execute.
            assert not calls

            async def execute(session_id: str, *, strategy: str, note: str, agent_id: str):
                assert session_id == session.id
                assert strategy == "keep"
                calls.append((agent_id, note))
                return (
                    await state.runs.current_plan(session.id),
                    await state.runs.current_tasks(session.id),
                    "run-fixture",
                )

            monkeypatch.setattr(state.runs, "execute_plan", execute)
            accepted = await client.post(
                f"/v1/sessions/{session.id}/commands/review", json={"note": "Do not push"}
            )
            assert accepted.status_code == 202, accepted.text
            assert calls == [(renamed.metadata.id, "Do not push")]
            command.unlink()
            missing = await client.post(f"/v1/sessions/{session.id}/commands/review", json={})
            assert missing.status_code == 404
            assert len(calls) == 1
    finally:
        await state.runs.close()
