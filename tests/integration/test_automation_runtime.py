import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from hames.automations import AutomationDefinition, AutomationScheduler
from hames.gateway import GatewayState, create_app
from hames.paths import HamesPaths
from hames.providers.base import StreamEvent, StreamEventKind
from hames.providers.fake import FakeProvider

# pyright: reportPrivateUsage=false


def setup(tmp_path: Path) -> tuple[GatewayState, FakeProvider, AutomationDefinition]:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    paths.ensure_foundation()
    paths.config_file.write_text(
        "[memory]\nenabled=false\n[skills]\nenabled=false\n[evolution]\nenabled=false\n"
    )
    provider = FakeProvider(
        [
            StreamEvent(kind=StreamEventKind.STARTED),
            StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="No urgent items."),
            StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop"),
        ]
    )
    state = GatewayState.create(paths, providers={"fake": provider})
    state.controls.grant_trust(tmp_path)
    spec = AutomationDefinition(
        title="Morning review",
        instructions="Read and report. Do not send.",
        working_directory=str(tmp_path),
        provider="fake",
        model="fixture",
        timezone="America/Halifax",
    )
    return state, provider, spec


@pytest.mark.asyncio
async def test_api_requires_auth_and_trust_and_roundtrips_schedule(tmp_path: Path) -> None:
    state, _, spec = setup(tmp_path)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(state)), base_url="http://test"
        ) as client:
            assert (await client.get("/v1/automations")).status_code == 401
            headers = {"Authorization": f"Bearer {state.token}"}
            assert (
                await client.post(
                    "/v1/automations",
                    headers=headers,
                    json={**spec.model_dump(), "working_directory": "/"},
                )
            ).status_code == 400
            response = await client.post("/v1/automations", headers=headers, json=spec.model_dump())
            assert response.status_code == 201
            item = response.json()
            assert not item["enabled"] and item["next_run"] is None
            updated = await client.put(
                f"/v1/automations/{item['id']}",
                headers=headers,
                json={**spec.model_dump(), "enabled": True},
            )
            assert updated.status_code == 200 and updated.json()["next_run"]
            assert (
                await client.post(f"/v1/automations/{item['id']}/run", headers=headers)
            ).status_code == 200
            assert (
                await client.post(f"/v1/automations/{item['id']}/run", headers=headers)
            ).status_code == 409
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_scheduled_run_separate_chat_terminal_notification_and_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, provider, spec = setup(tmp_path)
    scheduler = AutomationScheduler(state.runs, "http://localhost:7411")
    scheduler.native_available = True
    notices: list[str] = []

    async def notice(title: str, job: dict[str, Any]) -> None:
        notices.append(title)
        assert job["status"] == "completed"

    monkeypatch.setattr(scheduler, "desktop_notice", notice)
    try:
        item = state.runs.automations.save(spec)
        state.runs.automations.enqueue(item["id"])
        await scheduler.tick()
        job = state.runs.automations.history()[0]
        assert job["session_id"] and job["run_id"]
        await asyncio.wait_for(asyncio.shield(state.runs._tasks[job["run_id"]]), 10)
        await scheduler.tick()
        assert state.runs.automations.history()[0]["status"] == "completed"
        assert provider.requests
        await scheduler.tick()
        await asyncio.sleep(0)
        await scheduler.tick()
        assert notices == ["Morning review"]
        # Startup must preserve a completed result not overwrite it as interrupted.
        with state.ledger.database.connect() as db:
            db.execute("UPDATE automation_runs SET status='running'")
        await scheduler.start()
        assert state.runs.automations.history()[0]["status"] == "completed"
    finally:
        await scheduler.close()
        await state.runs.close()


@pytest.mark.asyncio
async def test_retries_bounded_and_cancellation_never_retried(tmp_path: Path) -> None:
    state, _, spec = setup(tmp_path)
    scheduler = AutomationScheduler(state.runs, "http://localhost:7411")
    try:
        item = state.runs.automations.save(spec.model_copy(update={"enabled": True, "retries": 1}))
        state.runs.automations.enqueue(item["id"])
        job = state.runs.automations.history()[0]
        with state.ledger.database.connect() as db:
            db.execute("UPDATE automation_runs SET status='running'")
        await scheduler.finish(job, "failed", "Temporary outage", retryable=True)
        jobs = state.runs.automations.history()
        assert len(jobs) == 2 and jobs[0]["attempt"] == 1 and jobs[0]["status"] == "pending"
        assert datetime.fromisoformat(jobs[0]["due_at"]) > datetime.now(UTC) + timedelta(seconds=60)
        with state.ledger.database.connect() as db:
            db.execute("UPDATE automation_runs SET status='running' WHERE status='pending'")
        await scheduler.finish(jobs[0], "cancelled", "User cancelled", retryable=True)
        assert len(state.runs.automations.history()) == 2
    finally:
        await scheduler.close()
        await state.runs.close()


@pytest.mark.asyncio
async def test_general_automation_needs_no_project(tmp_path: Path) -> None:
    state, _, spec = setup(tmp_path)
    scheduler = AutomationScheduler(state.runs, "http://localhost:7411")
    spec.working_directory = ""
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(state)), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/automations",
                json=spec.model_dump(),
                headers={"Authorization": f"Bearer {state.token}"},
            )
            assert response.status_code == 201
            item = response.json()
            assert item["working_directory"] == ""
        state.runs.automations.enqueue(item["id"])
        await scheduler.tick()
        job = state.runs.automations.history()[0]
        assert job["session_id"]
        session = state.ledger.get_session(job["session_id"])
        directory = Path(session.working_directory)
        assert directory == tmp_path / "home-automation-files" / item["id"]
        assert await asyncio.to_thread(directory.is_dir)
        assert not directory.is_relative_to(state.paths.root)
        await asyncio.wait_for(asyncio.shield(state.runs._tasks[job["run_id"]]), 10)
        await scheduler.tick()
        assert state.runs.automations.history()[0]["status"] == "completed"
    finally:
        await scheduler.close()
        await state.runs.close()
